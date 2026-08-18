"""Reference implementation core -- HARNESS MACHINERY, NOT THE DELIVERABLE.

This is a deliberately minimal but *correct* implementation of the contract the
gate enforces. Its only purpose is to give ``--selftest`` something known-good to
pass, and something to mutate into known-broken variants. It is not a model
answer for ``fxlab/``: it has no OANDA client, no recorder, no resampling, no
manifest richness, and handles exactly the cases the fixtures exercise.

Do not copy this into ``fxlab/``. The build agent cannot edit ``verify/`` and
should not read this as a spec -- ``CLAUDE.md`` and ``spec.md`` are the spec.

The ``# MUTATION-ANCHOR:`` comments mark lines that ``verify/reference/mutate.py``
rewrites to build the broken variants. Each anchor must stay unique in the file;
the mutator asserts that it matched exactly once.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import lzma
import pathlib
import struct
from typing import Any, Final

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RECORD_SIZE: Final[int] = 20
RECORD_FMT: Final[str] = ">IIIff"


class ValidationError(Exception):
    """Raised with a machine-readable reason token when input is unusable."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def price_scale(pair: str) -> float:
    """Integer-to-price scale: 1e-3 for JPY-quoted pairs, 1e-5 otherwise."""
    return 1e-3 if pair.upper().endswith("JPY") else 1e-5


def decode_hour(blob: bytes, hour_start: dt.datetime, scale: float) -> list[tuple]:
    """Decode one hour of bi5 bytes into ``(ts, bid, ask, bid_vol, ask_vol)`` rows.

    An empty ``blob`` means Dukascopy served the hour as closed, not that
    anything went wrong.
    """
    if not blob:
        return []
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob)
    if len(raw) % RECORD_SIZE:
        raise ValidationError(
            "CORRUPT_RECORD_LENGTH",
            f"decompressed {len(raw)} bytes, not a multiple of {RECORD_SIZE}")
    rows = []
    for ms, ask_i, bid_i, ask_vol, bid_vol in struct.iter_unpack(RECORD_FMT, raw):
        ts = hour_start + dt.timedelta(milliseconds=ms)  # MUTATION-ANCHOR: timestamp
        rows.append((ts, bid_i * scale, ask_i * scale, float(bid_vol), float(ask_vol)))
    return rows


def validate(rows: list[tuple], pair: str, hour_start: dt.datetime) -> None:
    """Reject impossible quotes and closed-market ticks by named reason."""
    if hour_start.weekday() == 5 and rows:  # MUTATION-ANCHOR: closed-market
        raise ValidationError(
            "CLOSED_MARKET_TICK",
            f"{pair} {hour_start.isoformat()} is a Saturday but carries {len(rows)} ticks")
    for ts, bid, ask, _bv, _av in rows:
        if bid <= 0 or ask <= 0:  # MUTATION-ANCHOR: nonpositive
            raise ValidationError(
                "NON_POSITIVE_PRICE",
                f"{pair} {ts.isoformat()} bid={bid!r} ask={ask!r}")
        if ask < bid:  # MUTATION-ANCHOR: crossed
            raise ValidationError(
                "CROSSED_QUOTE",
                f"{pair} {ts.isoformat()} bid={bid!r} > ask={ask!r}")


def dedupe(rows: list[tuple]) -> tuple[list[tuple], int]:
    """Drop exact duplicate rows, preserving order.

    Returns:
        ``(kept_rows, dropped_count)`` -- the count is reported in the manifest
        rather than discarded, so data loss is never silent.
    """
    seen: set[tuple] = set()
    kept: list[tuple] = []
    dropped = 0
    for row in rows:
        if row in seen:
            dropped += 1
            continue
        seen.add(row)
        kept.append(row)
    return kept, dropped


def tick_table(rows: list[tuple], pair: str, source: str) -> pa.Table:
    """Build the pinned-schema Arrow table for a set of tick rows."""
    df = pd.DataFrame(rows, columns=["ts", "bid", "ask", "bid_volume", "ask_volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).astype("datetime64[us, UTC]")
    df["pair"] = pair
    df["source"] = source
    df = df[["pair", "ts", "bid", "ask", "bid_volume", "ask_volume", "source"]]
    schema = pa.schema([
        ("pair", pa.large_string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("bid_volume", pa.float64()),
        ("ask_volume", pa.float64()),
        ("source", pa.large_string()),
    ])
    return pa.Table.from_pandas(df, schema=schema, preserve_index=False)


def ingest(config: dict[str, Any]) -> dict[str, Any]:
    """Decode, validate and store every hour named in the config.

    Returns:
        The manifest dict (also written to ``<out_dir>/manifest.json``).
    """
    cfg = config["ingest"]
    raw_dir = pathlib.Path(cfg["raw_dir"])
    out_dir = pathlib.Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"hours": [], "validation": {"ok": True, "errors": []}}
    per_partition: dict[tuple[str, str], list[tuple]] = {}

    try:
        for spec in cfg["hours"]:
            pair, date_s, hour = spec["pair"], spec["date"], int(spec["hour"])
            day = dt.date.fromisoformat(date_s)
            hour_start = dt.datetime(day.year, day.month, day.day, hour,
                                     tzinfo=dt.timezone.utc)
            path = raw_dir / f"{pair}_{date_s}_{hour:02d}h.bi5"
            if not path.exists():
                manifest["hours"].append({
                    "pair": pair, "date": date_s, "hour": hour, "status": "gap",
                    "decoded_ticks": 0, "written_ticks": 0, "duplicates_dropped": 0,
                    "detail": f"missing {path.name}"})
                continue

            blob = path.read_bytes()
            rows = decode_hour(blob, hour_start, price_scale(pair))
            decoded = len(rows)
            validate(rows, pair, hour_start)
            rows, dropped = dedupe(rows)

            manifest["hours"].append({
                "pair": pair, "date": date_s, "hour": hour,
                "status": "ok" if rows else "empty",
                "decoded_ticks": decoded, "written_ticks": len(rows),
                "duplicates_dropped": dropped,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "compressed_bytes": len(blob)})

            if rows:
                per_partition.setdefault((pair, date_s), []).extend(rows)
    except ValidationError as exc:
        manifest["validation"] = {"ok": False,
                                  "errors": [{"reason": exc.reason, "detail": exc.detail}]}
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf8")
        raise

    for (pair, date_s), rows in sorted(per_partition.items()):
        rows.sort(key=lambda r: r[0])
        part = out_dir / "ticks" / f"pair={pair}" / f"date={date_s}"
        part.mkdir(parents=True, exist_ok=True)
        pq.write_table(tick_table(rows, pair, "dukascopy"),
                       part / "ticks.parquet", compression="snappy")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return manifest


# --------------------------------------------------------------------------- #
# Costs and backtest
# --------------------------------------------------------------------------- #

def commission(units: int, price: float, rate: float, minimum: float,
               multiplier: float = 1.0) -> float:
    """IB-style per-order commission: ``rate`` of notional, floored at ``minimum``."""
    return max(rate * units * price, minimum) * multiplier  # MUTATION-ANCHOR: commission


def backtest(config: dict[str, Any]) -> dict[str, Any]:
    """Run the reference SMA-cross strategy with next-bar, spread-crossing fills."""
    cfg = config["backtest"]
    costs = cfg.get("costs", {})
    rate = float(costs.get("commission_rate", 0.20 / 10_000))
    minimum = float(costs.get("commission_min", 2.00))
    mult = float(costs.get("cost_multiplier", 1.0))
    units = int(cfg["units"])
    fast, slow = int(cfg["fast"]), int(cfg["slow"])

    bars = pq.read_table(cfg["bars_path"]).to_pandas()
    bars = bars[bars["pair"] == cfg["pair"]].sort_values("ts").reset_index(drop=True)
    closes = bars["mid_close"].tolist()

    def sma(window: int, end: int) -> float | None:
        if end + 1 < window:
            return None
        return sum(closes[end + 1 - window: end + 1]) / window

    target = []
    for t in range(len(closes)):
        f, s = sma(fast, t), sma(slow, t)
        target.append(1 if (f is not None and s is not None and f > s) else 0)

    trades: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    pos, entry, running = 0, None, 0.0
    equity.append({"ts": bars.iloc[0]["ts"].isoformat(), "equity": 0.0})

    for t in range(len(bars) - 1):
        if target[t] != pos:
            fill_bar = bars.iloc[t + 1]  # MUTATION-ANCHOR: fill-bar
            buy_px = float(fill_bar["ask_open"])   # MUTATION-ANCHOR: buy-price
            sell_px = float(fill_bar["bid_open"])  # MUTATION-ANCHOR: sell-price
            mid_px = float(fill_bar["mid_open"])
            if target[t] == 1:
                entry = {"ts": fill_bar["ts"], "fill": buy_px, "mid": mid_px}
                pos = 1
            else:
                assert entry is not None
                gross = (mid_px - entry["mid"]) * units
                spread_cost = ((entry["fill"] - entry["mid"]) * units
                               + (mid_px - sell_px) * units) * mult
                comm = (commission(units, entry["fill"], rate, minimum, mult)
                        + commission(units, sell_px, rate, minimum, mult))
                running += gross - spread_cost - comm
                trades.append({
                    "entry_ts": entry["ts"].isoformat(), "exit_ts": fill_bar["ts"].isoformat(),
                    "side": "long", "units": units,
                    "entry_fill": entry["fill"], "exit_fill": sell_px,
                    "entry_mid": entry["mid"], "exit_mid": mid_px,
                    "gross_pnl": gross, "spread_cost": spread_cost, "commission": comm,
                    "net_pnl": gross - spread_cost - comm})
                pos, entry = 0, None
        equity.append({"ts": bars.iloc[t + 1]["ts"].isoformat(), "equity": running})

    gross = sum(t["gross_pnl"] for t in trades)
    spread_cost = sum(t["spread_cost"] for t in trades)
    comm = sum(t["commission"] for t in trades)
    peak, max_dd = 0.0, 0.0
    for pt in equity:
        peak = max(peak, pt["equity"])
        max_dd = max(max_dd, peak - pt["equity"])

    return {
        "summary": {
            "trade_count": len(trades), "gross_pnl": gross,
            "spread_cost": spread_cost, "commission": comm,
            "total_costs": spread_cost + comm,
            "net_pnl": gross - spread_cost - comm, "max_drawdown": max_dd},
        "trades": trades,
        "equity": equity,
    }
