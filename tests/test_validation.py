"""Validation: named reasons, poisoned fixtures, and duplicate accounting."""

from __future__ import annotations

import lzma
import struct

import pytest

from fxlab.ingestion.bi5 import RECORD_FORMAT, decode_bi5
from fxlab.ingestion.validation import (
    CLOSED_MARKET_TICK,
    CROSSED_QUOTE,
    HARD_REASONS,
    NON_POSITIVE_PRICE,
    SPREAD_OUTLIER,
    TICK_OUTSIDE_HOUR,
    deduplicate,
    spread_stats,
    validate,
)
from tests.conftest import POISON_DIR, RAW_DIR, hour_start, read_fixture


def _batch(records, pair="USDJPY", date="2026-07-14", hour=13):
    """Decode and de-duplicate a synthetic record list."""
    packed = b"".join(struct.pack(RECORD_FORMAT, *r) for r in records)
    payload = lzma.compress(packed, format=lzma.FORMAT_ALONE)
    return deduplicate(decode_bi5(payload, pair, hour_start(date, hour)))


def _fixture_batch(directory, name, pair, date, hour):
    payload = read_fixture(directory, name)
    return deduplicate(decode_bi5(payload, pair, hour_start(date, hour)))


def test_clean_fixture_hours_raise_no_hard_issue(clean_hours: list[dict]) -> None:
    for spec in clean_hours:
        batch = _fixture_batch(RAW_DIR, spec["file"], spec["pair"],
                               spec["date"], spec["hour"])
        hard = [i.reason for i in validate(batch) if i.is_hard]
        assert hard == [], (spec["file"], hard)


def test_crossed_quote_fixture_is_rejected_by_name() -> None:
    batch = _fixture_batch(POISON_DIR, "crossed_quote.bi5", "USDJPY",
                           "2026-07-14", 13)
    reasons = [i.reason for i in validate(batch) if i.is_hard]
    assert reasons == [CROSSED_QUOTE]


def test_non_positive_price_fixture_is_rejected_by_name() -> None:
    batch = _fixture_batch(POISON_DIR, "non_positive_price.bi5", "USDJPY",
                           "2026-07-14", 13)
    reasons = [i.reason for i in validate(batch) if i.is_hard]
    assert reasons == [NON_POSITIVE_PRICE]


def test_closed_market_fixture_is_rejected_by_name() -> None:
    # Real weekday ticks filed under a Saturday hour.
    batch = _fixture_batch(POISON_DIR, "closed_market.bi5", "USDJPY",
                           "2026-07-11", 13)
    reasons = [i.reason for i in validate(batch) if i.is_hard]
    assert reasons == [CLOSED_MARKET_TICK]


def test_duplicate_block_is_dropped_and_counted_not_rejected() -> None:
    batch = _fixture_batch(POISON_DIR, "duplicate_block.bi5", "USDJPY",
                           "2026-07-14", 13)
    assert batch.decoded_ticks == 11_881
    assert batch.duplicates_dropped == 100
    assert len(batch) == 11_781
    assert [i.reason for i in validate(batch) if i.is_hard] == []


def test_deduplication_keeps_time_order_after_dropping() -> None:
    batch = _fixture_batch(POISON_DIR, "duplicate_block.bi5", "USDJPY",
                           "2026-07-14", 13)
    assert all(batch.ts_us[i] <= batch.ts_us[i + 1] for i in range(len(batch) - 1))


def test_only_whole_record_repeats_count_as_duplicates() -> None:
    # Same millisecond, different price: two real ticks, not a duplicate.
    batch = _batch([(1000, 161911, 161908, 1.2, 6.3),
                    (1000, 161912, 161908, 1.2, 6.3)])
    assert batch.duplicates_dropped == 0
    assert len(batch) == 2


def test_exact_repeat_is_a_duplicate() -> None:
    record = (1000, 161911, 161908, 1.2, 6.3)
    batch = _batch([record, record, record])
    assert batch.duplicates_dropped == 2
    assert len(batch) == 1


def test_tick_that_escapes_its_own_hour_is_named() -> None:
    batch = _batch([(3_600_001, 161911, 161908, 1.2, 6.3)])
    reasons = [i.reason for i in validate(batch) if i.is_hard]
    assert reasons == [TICK_OUTSIDE_HOUR]


def test_every_hard_reason_token_is_stable() -> None:
    # The gate greps for these exact strings; renaming one silently breaks it.
    assert {"CROSSED_QUOTE", "NON_POSITIVE_PRICE", "CLOSED_MARKET_TICK"} <= HARD_REASONS
    assert SPREAD_OUTLIER not in HARD_REASONS


def test_empty_batch_validates_clean() -> None:
    assert validate(_batch([])) == []


def test_spread_stats_are_reported_in_pips() -> None:
    batch = _fixture_batch(RAW_DIR, "EURUSD_2026-07-14_13h.bi5", "EURUSD",
                           "2026-07-14", 13)
    stats = spread_stats(batch)
    assert 0.0 < stats["median_pips"] < 1.0
    assert stats["max_pips"] >= stats["p99_9_pips"] >= stats["median_pips"]


def test_wide_spread_warns_without_rejecting() -> None:
    batch = _batch([(1000, 200_000, 100_000, 1.0, 1.0)], pair="USDJPY")
    issues = validate(batch)
    assert [i.reason for i in issues] == [SPREAD_OUTLIER]
    assert not any(i.is_hard for i in issues)
