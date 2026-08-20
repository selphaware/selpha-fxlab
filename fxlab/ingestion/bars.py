"""Bar resampling, built from stored ticks and never re-downloaded.

**Convention, stated once and enforced everywhere: a bar timestamp is the bar
OPEN time, and the bar covers the half-open interval [open, open + delta).**
Off-by-one-bar conventions are a classic lookahead source, so this is written
down in the code, in the docs and in the tests rather than left to be inferred
from a resample call.

Empty bins are dropped from the bar table and returned separately as gaps. The
alternative -- letting pandas emit a row of NaN -- produces bars that look real
and price nothing.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Final, Sequence

import pyarrow as pa

from fxlab.ingestion.store import write_table_atomic

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: Timeframe spellings accepted in config, mapped to pandas offset aliases.
TIMEFRAME_ALIASES: Final[dict[str, str]] = {
    "1m": "1min", "1min": "1min", "m1": "1min",
    "5m": "5min", "5min": "5min", "m5": "5min",
    "15m": "15min", "15min": "15min", "m15": "15min",
    "30m": "30min", "30min": "30min", "m30": "30min",
    "1h": "1h", "h1": "1h", "60min": "1h",
    "4h": "4h", "h4": "4h",
    "1d": "1D", "d1": "1D", "1day": "1D",
}

#: The bar schema, pinned for the same reason the tick schema is.
BAR_SCHEMA: Final[pa.Schema] = pa.schema([
    pa.field("pair", pa.large_string(), nullable=False),
    pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("bid_open", pa.float64(), nullable=False),
    pa.field("bid_high", pa.float64(), nullable=False),
    pa.field("bid_low", pa.float64(), nullable=False),
    pa.field("bid_close", pa.float64(), nullable=False),
    pa.field("ask_open", pa.float64(), nullable=False),
    pa.field("ask_high", pa.float64(), nullable=False),
    pa.field("ask_low", pa.float64(), nullable=False),
    pa.field("ask_close", pa.float64(), nullable=False),
    pa.field("mid_open", pa.float64(), nullable=False),
    pa.field("mid_high", pa.float64(), nullable=False),
    pa.field("mid_low", pa.float64(), nullable=False),
    pa.field("mid_close", pa.float64(), nullable=False),
    pa.field("tick_count", pa.int64(), nullable=False),
    pa.field("spread_mean", pa.float64(), nullable=False),
    pa.field("spread_max", pa.float64(), nullable=False),
])

#: Column order of a bar table.
BAR_COLUMNS: Final[tuple[str, ...]] = tuple(BAR_SCHEMA.names)


class TimeframeError(ValueError):
    """Raised for a timeframe spelling the resampler does not understand."""


def offset_alias(timeframe: str) -> str:
    """Translate a configured timeframe to a pandas offset alias.

    Args:
        timeframe: A spelling such as ``1m``, ``5min`` or ``1h``.

    Returns:
        The pandas alias.

    Raises:
        TimeframeError: If the spelling is not recognised.
    """
    key = str(timeframe).strip().lower()
    if key in TIMEFRAME_ALIASES:
        return TIMEFRAME_ALIASES[key]
    raise TimeframeError(
        f"unknown timeframe {timeframe!r}; known: {sorted(TIMEFRAME_ALIASES)}")


def _origin_kwargs(rule: str) -> dict[str, Any]:
    """``origin="epoch"`` where pandas honours it, and nothing where it does not.

    Epoch alignment is what makes every intraday timeframe tile UTC days
    exactly, which is the property incremental bar building rests on. pandas
    only applies ``origin`` to tick-like frequencies and warns for the rest;
    ``1D`` is already anchored to UTC midnight, so asking for it there buys a
    warning and changes nothing.
    """
    import pandas as pd

    offset = pd.tseries.frequencies.to_offset(rule)
    if isinstance(offset, pd.tseries.offsets.Tick):
        return {"origin": "epoch"}
    return {}


@dataclass(slots=True)
class BarResult:
    """Resampled bars plus the bins that had no ticks at all."""

    frame: Any
    timeframe: str
    pair: str
    empty_bins: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        """Number of non-empty bars."""
        return int(len(self.frame))


def resample_ticks(ticks: Any, timeframe: str, pair: str | None = None) -> BarResult:
    """Resample a tick frame into bars.

    Args:
        ticks: A DataFrame with the stored tick columns.
        timeframe: Bar size, e.g. ``1min``, ``5min``, ``1h``.
        pair: Pair label for the output; taken from the data when omitted.

    Returns:
        A :class:`BarResult`. Bar timestamps are bar OPEN times and each bar
        covers [open, open + delta); bins with no ticks are dropped from the
        frame and listed in ``empty_bins``.
    """
    rule = offset_alias(timeframe)
    if pair is None:
        pair = str(ticks["pair"].iloc[0]) if len(ticks) else ""

    if not len(ticks):
        return BarResult(frame=_empty_bar_frame(), timeframe=rule, pair=pair)

    frame = ticks.loc[:, ["ts", "bid", "ask"]].copy()
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
    frame["spread"] = frame["ask"] - frame["bid"]
    frame = frame.set_index("ts").sort_index(kind="stable")

    grouped = frame.resample(rule, label="left", closed="left",
                             **_origin_kwargs(rule))
    bars = grouped.agg(
        bid_open=("bid", "first"), bid_high=("bid", "max"),
        bid_low=("bid", "min"), bid_close=("bid", "last"),
        ask_open=("ask", "first"), ask_high=("ask", "max"),
        ask_low=("ask", "min"), ask_close=("ask", "last"),
        mid_open=("mid", "first"), mid_high=("mid", "max"),
        mid_low=("mid", "min"), mid_close=("mid", "last"),
        tick_count=("mid", "size"),
        spread_mean=("spread", "mean"), spread_max=("spread", "max"),
    )

    empty = bars.index[bars["tick_count"] == 0]
    empty_bins = [ts.isoformat() for ts in empty]
    bars = bars[bars["tick_count"] > 0].copy()

    bars.insert(0, "pair", pair)
    bars = bars.reset_index().rename(columns={"index": "ts"})
    bars["tick_count"] = bars["tick_count"].astype("int64")
    bars = bars.loc[:, list(BAR_COLUMNS)]
    return BarResult(frame=bars, timeframe=rule, pair=pair, empty_bins=empty_bins)


def _empty_bar_frame() -> Any:
    """Return a correctly typed, zero-row bar frame."""
    return BAR_SCHEMA.empty_table().to_pandas()


def bar_table(bars: Any) -> pa.Table:
    """Convert a bar frame to Arrow against the pinned schema."""
    return pa.Table.from_pandas(bars.loc[:, list(BAR_COLUMNS)],
                                schema=BAR_SCHEMA, preserve_index=False)


def bars_path(out_dir: pathlib.Path, pair: str, timeframe: str) -> pathlib.Path:
    """Return the canonical output path for one pair and timeframe."""
    alias = offset_alias(timeframe)
    return (pathlib.Path(out_dir) / "bars" / f"timeframe={alias}"
            / f"pair={pair}" / f"{pair}_{alias}.parquet")


def write_bars(out_dir: pathlib.Path, result: BarResult) -> pathlib.Path:
    """Write a bar table to the store and return its path."""
    path = bars_path(out_dir, result.pair, result.timeframe)
    written = write_table_atomic(bar_table(result.frame), path)
    _LOG.info("wrote %d %s bars for %s to %s",
              written.rows, result.timeframe, result.pair, path)
    return path


# --------------------------------------------------------------------------- #
# Incremental bar building (SPEC2 prerequisite P0-B)
# --------------------------------------------------------------------------- #
#
# Rebuilding every bar from the whole stored history costs nothing on a week of
# one pair and is impossible on a decade of twelve: T2a stores about 764,000
# hours, and re-resampling all of them after every ingested month would dominate
# the run several times over. So bar building is incremental, and it rests on
# one structural fact:
#
#     **Every timeframe in the research set tiles UTC days exactly.**
#
# 1m, 5m, 30m, 1h and 4h all divide 24h and are aligned to the epoch, and 1d
# *is* the UTC day. No bin of any of them can straddle midnight, so a day's bars
# are a function of that day's ticks alone. A day can therefore be rebuilt on
# its own and spliced into the stored table without touching any other day --
# which is what makes "only build what changed" correct rather than merely fast.
#
# What changed is decided from the store itself, not from a run's bookkeeping: a
# day's signature is the number of tick files it holds and their total size. A
# day whose signature matches what was folded in last time is skipped; a day
# that gained an hour since -- a gap that was later refetched -- does not match,
# and is rebuilt. Nothing has to remember to invalidate anything.
#
# Coarser bars are rolled up from the 1m bars rather than resampled from ticks
# again. That is exact, not an approximation: open/high/low/close nest because
# the bins nest, tick_count and spread_max are a sum and a max, and the
# tick-weighted mean of the per-minute spread means is the tick-level mean.
# :func:`aggregate_bars` is tested against :func:`resample_ticks` for exactly
# this reason.

#: Where the per-pair bar-build state lives inside a store.
BAR_STATE_DIRNAME: Final[str] = "_state"

#: The timeframe every other one is rolled up from.
BASE_TIMEFRAME: Final[str] = "1min"

#: Days resampled in one pass. Bounds peak memory on a full rebuild without
#: making the per-batch overhead matter: a month of one pair is one batch.
DEFAULT_BATCH_DAYS: Final[int] = 31


@dataclass(frozen=True, slots=True)
class BarUpdate:
    """What one (pair, timeframe) bar table gained from one incremental build."""

    pair: str
    timeframe: str
    path: pathlib.Path
    dates_built: int
    rows_written: int
    rows_total: int
    seconds: float


def alias_seconds(alias: str) -> int:
    """Length of one bar of ``alias``, in seconds.

    Args:
        alias: A pandas offset alias produced by :func:`offset_alias`.

    Returns:
        The bar length in seconds.

    Raises:
        TimeframeError: If the alias cannot be sized.
    """
    import pandas as pd

    try:
        nanos = pd.tseries.frequencies.to_offset(alias).nanos
    except (ValueError, AttributeError) as exc:  # pragma: no cover - defensive
        raise TimeframeError(f"cannot size timeframe {alias!r}: {exc}") from exc
    return int(nanos // 1_000_000_000)


def aggregate_bars(bars: Any, timeframe: str, pair: str | None = None) -> BarResult:
    """Roll finer bars up into coarser ones, exactly.

    Args:
        bars: A bar frame with :data:`BAR_COLUMNS`, at a timeframe that divides
            ``timeframe`` and shares its epoch alignment.
        timeframe: The coarser timeframe.
        pair: Pair label; taken from the data when omitted.

    Returns:
        A :class:`BarResult` identical to resampling the underlying ticks
        directly. ``spread_mean`` is re-weighted by ``tick_count`` rather than
        averaged, because an average of averages is not the average.
    """
    rule = offset_alias(timeframe)
    if pair is None:
        pair = str(bars["pair"].iloc[0]) if len(bars) else ""
    if not len(bars):
        return BarResult(frame=_empty_bar_frame(), timeframe=rule, pair=pair)

    frame = bars.loc[:, [c for c in BAR_COLUMNS if c != "pair"]].copy()
    frame["spread_sum"] = frame["spread_mean"] * frame["tick_count"]
    frame = frame.set_index("ts").sort_index(kind="stable")

    grouped = frame.resample(rule, label="left", closed="left",
                             **_origin_kwargs(rule))
    out = grouped.agg(
        bid_open=("bid_open", "first"), bid_high=("bid_high", "max"),
        bid_low=("bid_low", "min"), bid_close=("bid_close", "last"),
        ask_open=("ask_open", "first"), ask_high=("ask_high", "max"),
        ask_low=("ask_low", "min"), ask_close=("ask_close", "last"),
        mid_open=("mid_open", "first"), mid_high=("mid_high", "max"),
        mid_low=("mid_low", "min"), mid_close=("mid_close", "last"),
        tick_count=("tick_count", "sum"),
        spread_sum=("spread_sum", "sum"), spread_max=("spread_max", "max"),
    )

    empty = out.index[out["tick_count"] == 0]
    empty_bins = [ts.isoformat() for ts in empty]
    out = out[out["tick_count"] > 0].copy()
    out["spread_mean"] = out["spread_sum"] / out["tick_count"]
    out = out.drop(columns=["spread_sum"])
    out.insert(0, "pair", pair)
    out = out.reset_index().rename(columns={"index": "ts"})
    out["tick_count"] = out["tick_count"].astype("int64")
    out = out.loc[:, list(BAR_COLUMNS)]
    return BarResult(frame=out, timeframe=rule, pair=pair, empty_bins=empty_bins)


def bar_state_path(out_dir: pathlib.Path, pair: str) -> pathlib.Path:
    """Where the per-pair record of already-built days lives."""
    return (pathlib.Path(out_dir) / "bars" / BAR_STATE_DIRNAME
            / f"{pair}_bars.json")


def load_bar_state(out_dir: pathlib.Path, pair: str) -> dict[str, dict[str, str]]:
    """Read the built-day signatures for one pair, or an empty record.

    An unreadable state file is treated as absent. The cost of being wrong that
    way is rebuilding bars that were already correct; the cost of the other way
    is bars that silently never get rebuilt.
    """
    path = bar_state_path(out_dir, pair)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError, ValueError):
        _LOG.warning("bar state for %s is unreadable; rebuilding from ticks", pair)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(tf): {str(d): str(s) for d, s in (days or {}).items()}
            for tf, days in data.items() if isinstance(days, dict)}


def save_bar_state(out_dir: pathlib.Path, pair: str,
                   state: dict[str, dict[str, str]]) -> pathlib.Path:
    """Write the built-day signatures atomically."""
    path = bar_state_path(out_dir, pair)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")),
                   encoding="utf8")
    os.replace(tmp, path)
    return path


def tick_day_signatures(out_dir: pathlib.Path, pair: str,
                        dates: Sequence[str] | None = None) -> dict[str, str]:
    """Signature every stored day of ``pair``, cheaply.

    Args:
        out_dir: Root of the store.
        pair: Pair to inspect.
        dates: Restrict to these ISO dates; every stored date when omitted.

    Returns:
        ``{date: signature}``. The signature is the hour-file count and their
        total size, which changes whenever a day gains, loses or replaces an
        hour -- and is a directory listing rather than a read.
    """
    root = pathlib.Path(out_dir) / "ticks" / f"pair={pair}"
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    if dates is None:
        candidates = [(p.name.removeprefix("date="), p)
                      for p in sorted(root.glob("date=*"))]
    else:
        candidates = [(d, root / f"date={d}") for d in sorted(set(dates))]
    for date_str, directory in candidates:
        count = 0
        total = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not entry.name.endswith(".parquet"):
                        continue
                    count += 1
                    total += entry.stat().st_size
        except OSError:
            continue
        if count:
            found[date_str] = f"{count}:{total}"
    return found


def _row_dates(table: pa.Table) -> Any:
    """The UTC date of every row of ``table``, as ``YYYY-MM-DD`` strings."""
    import pyarrow.compute as pc

    return pc.strftime(table.column("ts"), format="%Y-%m-%d")


def splice_bars(path: pathlib.Path, fresh: pa.Table,
                dates: Sequence[str]) -> tuple[int, int]:
    """Replace ``dates`` in the stored bar table with ``fresh``, atomically.

    Args:
        path: The bar table to update; created when absent.
        fresh: Newly built bars, already matching :data:`BAR_SCHEMA`.
        dates: The ISO dates ``fresh`` is authoritative for. Rows in the stored
            table carrying one of these dates are dropped before the splice, so
            rebuilding a day replaces it rather than duplicating it.

    Returns:
        ``(rows_written, rows_total)``.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    path = pathlib.Path(path)
    keep: pa.Table | None = None
    if path.is_file():
        existing = pq.read_table(path, schema=BAR_SCHEMA)
        if existing.num_rows:
            wanted = pa.array(sorted(set(dates)), type=pa.string())
            stale = pc.is_in(_row_dates(existing), value_set=wanted)
            keep = existing.filter(pc.invert(pc.fill_null(stale, False)))

    parts = [t for t in (keep, fresh) if t is not None and t.num_rows]
    if parts:
        merged = pa.concat_tables(parts).sort_by([("ts", "ascending")])
    else:
        merged = BAR_SCHEMA.empty_table()
    written = write_table_atomic(merged.combine_chunks(), path)
    return fresh.num_rows, written.rows


def build_bars_incremental(
        out_dir: pathlib.Path, pair: str, timeframes: Sequence[str],
        *, dates: Sequence[str] | None = None,
        batch_days: int = DEFAULT_BATCH_DAYS) -> list[BarUpdate]:
    """Bring one pair's bar tables up to date with its stored ticks.

    Only days whose stored ticks differ from what was last folded in are
    resampled; everything else is left alone. Coarser timeframes are rolled up
    from :data:`BASE_TIMEFRAME` when it divides them, and resampled from ticks
    directly when it does not.

    Args:
        out_dir: Root of the store.
        pair: Pair to build.
        timeframes: Timeframes to maintain, in any accepted spelling.
        dates: Restrict the scan to these ISO dates -- what an ingest run just
            touched. Every stored date is considered when omitted, which is how
            a first build, or a store whose state file was lost, catches up.
        batch_days: Days resampled per pass.

    Returns:
        One :class:`BarUpdate` per timeframe that changed.
    """
    from fxlab.ingestion.store import read_ticks

    out_dir = pathlib.Path(out_dir)
    aliases: list[str] = []
    for timeframe in timeframes:
        alias = offset_alias(timeframe)
        if alias not in aliases:
            aliases.append(alias)
    if not aliases:
        return []

    signatures = tick_day_signatures(out_dir, pair, dates)
    state = load_bar_state(out_dir, pair)

    stale: dict[str, set[str]] = {}
    for alias in aliases:
        built = state.get(alias, {})
        if bars_path(out_dir, pair, alias).is_file():
            stale[alias] = {d for d, sig in signatures.items()
                            if built.get(d) != sig}
        else:
            # No stored table means nothing has been folded in, whatever the
            # state file claims. Rebuild everything in scope rather than trust
            # a record of a file that is not there.
            stale[alias] = set(signatures)

    union = sorted({d for days in stale.values() for d in days})
    if not union:
        _LOG.debug("%s: bars already current for %s", pair, aliases)
        return []

    base_alias = offset_alias(BASE_TIMEFRAME)
    base_seconds = alias_seconds(base_alias)
    spent: dict[str, float] = {alias: 0.0 for alias in aliases}
    collected: dict[str, list[Any]] = {alias: [] for alias in aliases}
    step = max(1, batch_days)

    for start in range(0, len(union), step):
        batch = union[start:start + step]
        ticks = read_ticks(out_dir, pair=pair, dates=batch)
        if not len(ticks):
            continue
        base = resample_ticks(ticks, base_alias, pair=pair)
        for alias in aliases:
            if not (stale[alias] & set(batch)):
                continue
            clock = time.perf_counter()
            if alias == base_alias:
                result = base
            elif alias_seconds(alias) % base_seconds == 0:
                result = aggregate_bars(base.frame, alias, pair=pair)
            else:  # pragma: no cover - no configured timeframe needs this
                result = resample_ticks(ticks, alias, pair=pair)
            if len(result):
                collected[alias].append(result.frame)
            spent[alias] += time.perf_counter() - clock

    updates: list[BarUpdate] = []
    for alias in aliases:
        if not stale[alias]:
            continue
        clock = time.perf_counter()
        frames = collected[alias]
        if frames:
            import pandas as pd

            fresh = bar_table(pd.concat(frames, ignore_index=True))
        else:
            fresh = BAR_SCHEMA.empty_table()
        path = bars_path(out_dir, pair, alias)
        written, total = splice_bars(path, fresh, sorted(stale[alias]))
        elapsed = spent[alias] + (time.perf_counter() - clock)
        state.setdefault(alias, {}).update(
            {d: signatures[d] for d in sorted(stale[alias]) if d in signatures})
        updates.append(BarUpdate(pair=pair, timeframe=alias, path=path,
                                 dates_built=len(stale[alias]),
                                 rows_written=written, rows_total=total,
                                 seconds=elapsed))
        _LOG.info("%s %s: %d day(s) built, %d row(s) spliced, %d total (%.2fs)",
                  pair, alias, len(stale[alias]), written, total, elapsed)

    save_bar_state(out_dir, pair, state)
    return updates
