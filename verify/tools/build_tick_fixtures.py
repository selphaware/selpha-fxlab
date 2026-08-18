"""Harness-time fixture-freeze: decode raw bi5 hours and emit ground truth.

Part of the HARNESS, not the deliverable. It reads the frozen raw bytes already
sitting in ``verify/fixtures/raw/`` and writes:

* ``verify/fixtures/expected.json`` -- per-hour tick counts and exact first/last
  tick values, captured at freeze time. This is what makes failure modes 1 and 2
  (silent timestamp corruption, silent data loss) detectable at all.
* ``verify/fixtures/poison/``       -- deliberately corrupted bi5 files used to
  prove the deliverable's validator actually rejects bad input.

The decoding here is intentionally written from ``struct``/``lzma`` directly and
shares no code with ``fxlab``. If the gate reused the deliverable's decoder to
compute its own expectations, a decoder bug would cancel itself out and the gate
would happily bless it.

This script does no network I/O. Fetching is a separate, explicit step
(``verify/tools/fetch_raw.py``) so that re-freezing is reproducible offline.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import lzma
import pathlib
import struct
from typing import Any, Final

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "verify" / "fixtures"
RAW = FIX / "raw"
POISON = FIX / "poison"

#: Bytes per tick record in a Dukascopy bi5 file.
RECORD_SIZE: Final[int] = 20

#: Record layout, confirmed against the live feed in Phase 0:
#: big-endian, ms-offset-into-hour, ASK first, then BID, then the two volumes.
RECORD_FMT: Final[str] = ">IIIff"

#: Price scale = 10 ** -display_precision. Confirmed from live data for EURUSD
#: (1e-5) and USDJPY (1e-3) by cross-checking decoded prices against OANDA H1
#: bid/ask OHLC for the same hour. JPY-quoted pairs are the 1e-3 class.
JPY_QUOTED_SCALE: Final[float] = 1e-3
DEFAULT_SCALE: Final[float] = 1e-5


def price_scale(pair: str) -> float:
    """Return the integer->price scale factor for ``pair``."""
    return JPY_QUOTED_SCALE if pair.upper().endswith("JPY") else DEFAULT_SCALE


def decode_hour(blob: bytes, hour_start: dt.datetime, scale: float) -> list[dict[str, Any]]:
    """Decode one hour of bi5 bytes into tick dicts.

    Args:
        blob: The raw (LZMA-compressed) file contents. May be empty, which
            Dukascopy uses to mean "market closed", not "error".
        hour_start: The UTC hour the file covers.
        scale: Integer-to-price scale factor for the pair.

    Returns:
        A list of tick dicts in file order.
    """
    if not blob:
        return []
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob)
    if len(raw) % RECORD_SIZE:
        raise ValueError(
            f"decompressed length {len(raw)} is not a multiple of {RECORD_SIZE}"
        )
    ticks = []
    for ms, ask_i, bid_i, ask_vol, bid_vol in struct.iter_unpack(RECORD_FMT, raw):
        ticks.append(
            {
                "ts": (hour_start + dt.timedelta(milliseconds=ms)).isoformat(),
                "bid": round(bid_i * scale, 10),
                "ask": round(ask_i * scale, 10),
                # Volumes are float32 in the file. Store the exact float64
                # widening rather than a rounded value: the gate compares these
                # to what the deliverable decodes from the same bits, so any
                # rounding here would make an exact match impossible.
                "bid_volume": float(bid_vol),
                "ask_volume": float(ask_vol),
            }
        )
    return ticks


def encode_hour(ticks: list[tuple[int, int, int, float, float]]) -> bytes:
    """Re-encode raw tick tuples into a bi5 blob (used to build poison files)."""
    body = b"".join(struct.pack(RECORD_FMT, *t) for t in ticks)
    return lzma.compress(
        body,
        format=lzma.FORMAT_ALONE,
        filters=[{"id": lzma.FILTER_LZMA1, "preset": 6}],
    )


def raw_records(blob: bytes) -> list[tuple[int, int, int, float, float]]:
    """Unpack a bi5 blob into its raw integer/float record tuples."""
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob)
    return list(struct.iter_unpack(RECORD_FMT, raw))


def build_poison(base_blob: bytes) -> dict[str, dict[str, Any]]:
    """Construct the corrupted fixtures the gate uses to test the validator.

    Each entry names the failure reason the deliverable is required to report.
    """
    recs = raw_records(base_blob)
    target = len(recs) // 2
    out: dict[str, dict[str, Any]] = {}

    # 1. Crossed quote: make bid strictly greater than ask on one tick.
    crossed = list(recs)
    ms, ask_i, bid_i, av, bv = crossed[target]
    crossed[target] = (ms, ask_i, ask_i + 50, av, bv)
    out["crossed_quote"] = {
        "blob": encode_hour(crossed),
        "expect_reason": "CROSSED_QUOTE",
        "expect_exit_nonzero": True,
        "note": f"tick #{target} has bid = ask + 50 points",
        "corrupt_index": target,
    }

    # 2. Non-positive price: zero the bid on one tick.
    zeroed = list(recs)
    ms, ask_i, bid_i, av, bv = zeroed[target]
    zeroed[target] = (ms, ask_i, 0, av, bv)
    out["non_positive_price"] = {
        "blob": encode_hour(zeroed),
        "expect_reason": "NON_POSITIVE_PRICE",
        "expect_exit_nonzero": True,
        "note": f"tick #{target} has bid = 0",
        "corrupt_index": target,
    }

    # 3. Closed-market ticks: a Saturday hour that nonetheless contains ticks.
    out["closed_market"] = {
        "blob": encode_hour(recs),
        "expect_reason": "CLOSED_MARKET_TICK",
        "expect_exit_nonzero": True,
        "note": "real weekday ticks filed under a Saturday hour",
        "corrupt_index": None,
    }

    # 4. Duplicated block: NOT a hard failure -- the contract says duplicates are
    #    dropped and *counted*. This proves the count is reported, not swallowed.
    dup_from, dup_n = 1000, 100
    duped = recs[:dup_from + dup_n] + recs[dup_from:dup_from + dup_n] + recs[dup_from + dup_n:]
    out["duplicate_block"] = {
        "blob": encode_hour(duped),
        "expect_reason": None,
        "expect_exit_nonzero": False,
        "expect_duplicates_dropped": dup_n,
        "expect_written_ticks": len(recs),
        "note": f"records [{dup_from},{dup_from + dup_n}) repeated once",
        "corrupt_index": None,
    }
    return out


def main() -> None:
    """Freeze ground truth and poison fixtures from the raw bi5 files."""
    meta_path = RAW / "_fetch_meta.json"
    if not meta_path.exists():
        raise SystemExit(f"missing {meta_path}; run verify/tools/fetch_raw.py first")
    fetch_meta = json.loads(meta_path.read_text(encoding="utf8"))

    hours: list[dict[str, Any]] = []
    for entry in fetch_meta:
        if entry["status"] != 200:
            continue
        pair, date_s, hour = entry["pair"], entry["date"], entry["hour"]
        path = RAW / entry["file"]
        if not path.exists():
            raise SystemExit(f"missing raw fixture {path}")
        blob = path.read_bytes()
        day = dt.date.fromisoformat(date_s)
        hour_start = dt.datetime(day.year, day.month, day.day, hour,
                                 tzinfo=dt.timezone.utc)
        scale = price_scale(pair)
        ticks = decode_hour(blob, hour_start, scale)

        rec = {
            "pair": pair,
            "date": date_s,
            "hour": hour,
            "weekday": day.strftime("%a"),
            "file": entry["file"],
            "url": entry["url"],
            "last_modified": entry["last_modified"],
            "sha256": hashlib.sha256(blob).hexdigest(),
            "compressed_bytes": len(blob),
            "price_scale": scale,
            "tick_count": len(ticks),
            "status": "ok" if ticks else "empty",
        }
        if ticks:
            rec["first_tick"] = ticks[0]
            rec["last_tick"] = ticks[-1]
            rec["decoded_bytes"] = len(ticks) * RECORD_SIZE
            bids = [t["bid"] for t in ticks]
            asks = [t["ask"] for t in ticks]
            rec["bid_min"], rec["bid_max"] = min(bids), max(bids)
            rec["ask_min"], rec["ask_max"] = min(asks), max(asks)
        hours.append(rec)

    # Poison fixtures are derived from the busiest real hour we have.
    base = max((h for h in hours if h["status"] == "ok"),
               key=lambda h: h["tick_count"])
    base_blob = (RAW / base["file"]).read_bytes()
    POISON.mkdir(parents=True, exist_ok=True)
    poison_meta: dict[str, Any] = {}
    for name, spec in build_poison(base_blob).items():
        fname = f"{name}.bi5"
        (POISON / fname).write_bytes(spec["blob"])
        entry = {k: v for k, v in spec.items() if k != "blob"}
        entry["file"] = fname
        entry["sha256"] = hashlib.sha256(spec["blob"]).hexdigest()
        entry["derived_from"] = base["file"]
        # closed_market is filed under a Saturday so the weekend rule can fire.
        entry["pair"] = base["pair"]
        if name == "closed_market":
            entry["date"], entry["hour"] = "2026-07-11", base["hour"]
        else:
            entry["date"], entry["hour"] = base["date"], base["hour"]
        poison_meta[name] = entry

    expected = {
        "schema_version": 1,
        "frozen_from": "live Dukascopy datafeed (see per-hour url/last_modified)",
        "source": {
            "url_pattern": ("https://datafeed.dukascopy.com/datafeed/{PAIR}/"
                            "{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5"),
            "month_indexing": "ZERO-BASED (January=00 ... December=11)",
            "record_size_bytes": RECORD_SIZE,
            "record_format": RECORD_FMT,
            "record_fields": ["ms_offset_in_hour", "ask_int", "bid_int",
                              "ask_volume_f32", "bid_volume_f32"],
            "compression": "LZMA1 alone-format (FORMAT_ALONE)",
            "empty_body_means": "market closed for that hour (HTTP 200, 0 bytes)",
            "price_scale_rule": "10 ** -display_precision; JPY-quoted = 1e-3, others = 1e-5",
        },
        "tick_schema": {
            "pair": "large_string",
            "ts": "timestamp[us, tz=UTC]",
            "bid": "double",
            "ask": "double",
            "bid_volume": "double",
            "ask_volume": "double",
            "source": "large_string",
        },
        "hours": hours,
        "poison": poison_meta,
    }

    FIX.mkdir(parents=True, exist_ok=True)
    (FIX / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True), encoding="utf8")

    print(f"froze {len(hours)} hours -> {FIX / 'expected.json'}")
    for h in hours:
        head = f"  {h['pair']} {h['date']} {h['hour']:02d}h ({h['weekday']})"
        if h["status"] == "empty":
            print(f"{head:44s} EMPTY (market closed)")
        else:
            print(f"{head:44s} {h['tick_count']:>6,} ticks  "
                  f"first={h['first_tick']['ts']}  last={h['last_tick']['ts']}")
    print(f"\nwrote {len(poison_meta)} poison fixtures -> {POISON}")
    for name, e in poison_meta.items():
        print(f"  {name:20s} -> {e['expect_reason'] or 'must SUCCEED'}  ({e['note']})")


if __name__ == "__main__":
    main()
