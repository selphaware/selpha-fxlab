"""Tick validation: named reasons, hard failures and warnings.

Every rejection carries a machine-readable reason token. A validator that
fails for an unnamed reason cannot be trusted to have failed for the *right*
reason -- so the tokens below are part of the contract, are written to stderr
verbatim, and are recorded in the manifest.

Hard failures (the hour is rejected and the run exits non-zero):

============================  =====================================
``CROSSED_QUOTE``             any tick with ``bid > ask``
``NON_POSITIVE_PRICE``        any tick with ``bid <= 0`` or ``ask <= 0``
``CLOSED_MARKET_TICK``        any tick outside the FX trading week
``TICK_OUTSIDE_HOUR``         a tick whose offset escapes its own hour file
``DECODE_ERROR``              the payload is not decodable bi5
``FETCH_ERROR``               the hour could not be retrieved at all
============================  =====================================

Duplicates are deliberately **not** a hard failure: exact duplicate records are
dropped and the count is reported. Swallowing them silently would make tick
counts drift between runs with nothing to show for it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from fxlab.ingestion.bi5 import DecodedHour
from fxlab.ingestion.pairs import PairSpec, pair_spec
from fxlab.ingestion.sessions import market_open_mask_micros

CROSSED_QUOTE: Final[str] = "CROSSED_QUOTE"
NON_POSITIVE_PRICE: Final[str] = "NON_POSITIVE_PRICE"
CLOSED_MARKET_TICK: Final[str] = "CLOSED_MARKET_TICK"
TICK_OUTSIDE_HOUR: Final[str] = "TICK_OUTSIDE_HOUR"
DECODE_ERROR: Final[str] = "DECODE_ERROR"
FETCH_ERROR: Final[str] = "FETCH_ERROR"

SPREAD_OUTLIER: Final[str] = "SPREAD_OUTLIER"
TICK_COUNT_OUTLIER: Final[str] = "TICK_COUNT_OUTLIER"

#: Reasons that reject an hour outright.
HARD_REASONS: Final[frozenset[str]] = frozenset({
    CROSSED_QUOTE, NON_POSITIVE_PRICE, CLOSED_MARKET_TICK,
    TICK_OUTSIDE_HOUR, DECODE_ERROR, FETCH_ERROR,
})

#: Reasons that are recorded and logged but do not reject the hour.
WARN_REASONS: Final[frozenset[str]] = frozenset({SPREAD_OUTLIER, TICK_COUNT_OUTLIER})

#: Sanity ceiling for the 99.9th-percentile spread, in pips, by quote currency
#: class. Deliberately far above any normal ECN spread: this exists to catch a
#: structural problem (a wrong price scale, a mangled field order), not to
#: comment on a genuinely wide market. The weekly Sunday open on EURUSD reaches
#: a p99.9 of about 8.5 pips against a weekday median near 0.25, so a tighter
#: ceiling would cry wolf every single week and be ignored by the second one.
_SPREAD_CEILING_PIPS: Final[dict[str, float]] = {"major": 20.0, "cross": 40.0}
_MAJORS: Final[frozenset[str]] = frozenset({
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
})


def spread_ceiling_pips(pair: str) -> float:
    """Return the p99.9 spread ceiling, in pips, above which we warn."""
    key = "major" if pair.upper() in _MAJORS else "cross"
    return _SPREAD_CEILING_PIPS[key]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One named validation finding."""

    reason: str
    detail: str
    count: int = 0

    @property
    def is_hard(self) -> bool:
        """True when this issue rejects the hour."""
        return self.reason in HARD_REASONS

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, as embedded in the manifest."""
        return {"reason": self.reason, "detail": self.detail, "count": self.count}


@dataclass(frozen=True, slots=True)
class TickBatch:
    """De-duplicated, time-sorted ticks for one hour, ready to validate/store."""

    pair: str
    hour_start: dt.datetime
    ts_us: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    bid_volume: np.ndarray
    ask_volume: np.ndarray
    decoded_ticks: int
    duplicates_dropped: int
    compressed_bytes: int
    decoded_bytes: int

    def __len__(self) -> int:
        """Number of ticks that survived de-duplication."""
        return int(self.ts_us.size)

    @property
    def hour_end(self) -> dt.datetime:
        """Exclusive end of the covered hour."""
        return self.hour_start + dt.timedelta(hours=1)

    @property
    def spread(self) -> np.ndarray:
        """Per-tick quoted spread in price units."""
        return self.ask - self.bid


def deduplicate(hour: DecodedHour) -> TickBatch:
    """Drop exact duplicate records, sort by time and count what was dropped.

    Args:
        hour: A freshly decoded hour.

    Returns:
        A :class:`TickBatch` sorted by timestamp, with ``duplicates_dropped``
        set. De-duplication is on the **whole** record: two ticks that share a
        millisecond but differ in price or volume are both real.
    """
    n = len(hour)
    if n == 0:
        empty_i = np.empty(0, dtype=np.int64)
        empty_f = np.empty(0, dtype=np.float64)
        return TickBatch(
            pair=hour.pair, hour_start=hour.hour_start, ts_us=empty_i,
            bid=empty_f, ask=empty_f, bid_volume=empty_f, ask_volume=empty_f,
            decoded_ticks=0, duplicates_dropped=0,
            compressed_bytes=hour.compressed_bytes, decoded_bytes=hour.decoded_bytes)

    key = np.empty(n, dtype=[("ts", "<i8"), ("bid", "<f8"), ("ask", "<f8"),
                             ("bid_volume", "<f8"), ("ask_volume", "<f8")])
    key["ts"] = hour.ts_us
    key["bid"] = hour.bid
    key["ask"] = hour.ask
    key["bid_volume"] = hour.bid_volume
    key["ask_volume"] = hour.ask_volume

    _unique, first_index = np.unique(key, return_index=True)
    keep = np.sort(first_index)
    order = keep[np.argsort(hour.ts_us[keep], kind="stable")]

    return TickBatch(
        pair=hour.pair,
        hour_start=hour.hour_start,
        ts_us=hour.ts_us[order],
        bid=hour.bid[order],
        ask=hour.ask[order],
        bid_volume=hour.bid_volume[order],
        ask_volume=hour.ask_volume[order],
        decoded_ticks=n,
        duplicates_dropped=n - int(order.size),
        compressed_bytes=hour.compressed_bytes,
        decoded_bytes=hour.decoded_bytes,
    )


def _first_offender(mask: np.ndarray, batch: TickBatch) -> str:
    """Describe the first tick flagged by ``mask``, for the issue detail."""
    idx = int(np.argmax(mask))
    ts = batch.ts_us[idx].astype("datetime64[us]")
    return (f"first at index {idx} ts={ts}Z "
            f"bid={batch.bid[idx]!r} ask={batch.ask[idx]!r}")


def validate(batch: TickBatch, *, spec: PairSpec | None = None) -> list[ValidationIssue]:
    """Check one hour of ticks against every rule.

    Args:
        batch: De-duplicated, sorted ticks.
        spec: Pre-resolved pair metadata.

    Returns:
        Every issue found, hard failures and warnings alike, in a stable order.
        All rules are evaluated: knowing an hour is both crossed *and* outside
        the trading week is more useful than knowing only the first thing that
        went wrong.
    """
    spec = spec or pair_spec(batch.pair)
    issues: list[ValidationIssue] = []
    if len(batch) == 0:
        return issues

    label = f"{batch.pair} {batch.hour_start:%Y-%m-%dT%H:00}Z"

    non_positive = (batch.bid <= 0.0) | (batch.ask <= 0.0)
    if non_positive.any():
        issues.append(ValidationIssue(
            NON_POSITIVE_PRICE,
            f"{label}: {int(non_positive.sum())} tick(s) with a non-positive price; "
            + _first_offender(non_positive, batch),
            int(non_positive.sum())))

    crossed = batch.bid > batch.ask
    if crossed.any():
        issues.append(ValidationIssue(
            CROSSED_QUOTE,
            f"{label}: {int(crossed.sum())} crossed quote(s) (bid > ask); "
            + _first_offender(crossed, batch),
            int(crossed.sum())))

    open_mask = market_open_mask_micros(batch.ts_us)
    closed = ~open_mask
    if closed.any():
        issues.append(ValidationIssue(
            CLOSED_MARKET_TICK,
            f"{label}: {int(closed.sum())} tick(s) outside the FX trading week "
            f"(Sun 17:00 to Fri 17:00 America/New_York); "
            + _first_offender(closed, batch),
            int(closed.sum())))

    from fxlab.ingestion.bi5 import epoch_micros

    start_us = epoch_micros(batch.hour_start)
    end_us = start_us + 3_600_000_000
    outside = (batch.ts_us < start_us) | (batch.ts_us >= end_us)
    if outside.any():
        issues.append(ValidationIssue(
            TICK_OUTSIDE_HOUR,
            f"{label}: {int(outside.sum())} tick(s) fall outside the hour this "
            f"file covers; " + _first_offender(outside, batch),
            int(outside.sum())))

    stats = spread_stats(batch, spec=spec)
    ceiling = spread_ceiling_pips(batch.pair)
    if stats["p99_9_pips"] > ceiling:
        issues.append(ValidationIssue(
            SPREAD_OUTLIER,
            f"{label}: p99.9 spread {stats['p99_9_pips']:.2f} pips exceeds the "
            f"{ceiling:.1f} pip sanity ceiling (max {stats['max_pips']:.2f})",
            0))

    return issues


def spread_stats(batch: TickBatch, *, spec: PairSpec | None = None) -> dict[str, float]:
    """Summarise the quoted spread of an hour, in pips.

    Args:
        batch: De-duplicated ticks.
        spec: Pre-resolved pair metadata.

    Returns:
        Median, p99, p99.9, mean and max spread in pips. Reported in pips
        rather than price units so JPY and non-JPY pairs are comparable.
    """
    spec = spec or pair_spec(batch.pair)
    if len(batch) == 0:
        return {"median_pips": 0.0, "mean_pips": 0.0, "p99_pips": 0.0,
                "p99_9_pips": 0.0, "max_pips": 0.0}
    pips = batch.spread / spec.pip_size
    p99, p99_9 = np.percentile(pips, [99.0, 99.9])
    return {
        "median_pips": float(np.median(pips)),
        "mean_pips": float(np.mean(pips)),
        "p99_pips": float(p99),
        "p99_9_pips": float(p99_9),
        "max_pips": float(np.max(pips)),
    }


def hard_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Filter ``issues`` down to the ones that reject the hour."""
    return [i for i in issues if i.is_hard]
