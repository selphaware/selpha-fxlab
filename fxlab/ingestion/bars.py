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

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, Final

import pyarrow as pa

from fxlab.ingestion.store import write_table_atomic

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: Timeframe spellings accepted in config, mapped to pandas offset aliases.
TIMEFRAME_ALIASES: Final[dict[str, str]] = {
    "1m": "1min", "1min": "1min", "m1": "1min",
    "5m": "5min", "5min": "5min", "m5": "5min",
    "15m": "15min", "15min": "15min", "m15": "15min",
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
    import pandas as pd

    rule = offset_alias(timeframe)
    if pair is None:
        pair = str(ticks["pair"].iloc[0]) if len(ticks) else ""

    if not len(ticks):
        return BarResult(frame=_empty_bar_frame(), timeframe=rule, pair=pair)

    frame = ticks.loc[:, ["ts", "bid", "ask"]].copy()
    frame["mid"] = (frame["bid"] + frame["ask"]) / 2.0
    frame["spread"] = frame["ask"] - frame["bid"]
    frame = frame.set_index("ts").sort_index(kind="stable")

    grouped = frame.resample(rule, label="left", closed="left", origin="epoch")
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
