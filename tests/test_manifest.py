"""The manifest: every hour accounted for, and coverage that flags outliers."""

from __future__ import annotations

import json

from fxlab.ingestion.manifest import (
    EMPTY_SHA256,
    STATUS_CLOSED,
    STATUS_GAP,
    STATUS_OK,
    HourRecord,
    Manifest,
    load_manifest,
    manifest_path,
    write_manifest,
)
from fxlab.ingestion.validation import CROSSED_QUOTE, SPREAD_OUTLIER, ValidationIssue


def test_record_round_trips_through_json() -> None:
    record = HourRecord("EURUSD", "2026-07-14", 13, STATUS_OK,
                        decoded_ticks=9_915, written_ticks=9_915,
                        duplicates_dropped=0, sha256="abc", compressed_bytes=43_161)
    restored = HourRecord.from_dict(json.loads(json.dumps(record.to_dict())))
    assert restored == record


def test_upsert_replaces_rather_than_duplicates() -> None:
    manifest = Manifest()
    manifest.upsert(HourRecord("EURUSD", "2026-07-14", 13, STATUS_GAP))
    manifest.upsert(HourRecord("EURUSD", "2026-07-14", 13, STATUS_OK,
                               written_ticks=10))
    assert len(manifest.hours) == 1
    assert manifest.hours[0].status == STATUS_OK


def test_hard_issues_make_the_manifest_not_ok_warnings_do_not() -> None:
    manifest = Manifest()
    manifest.add_issue("EURUSD", "2026-07-14", 13,
                       ValidationIssue(SPREAD_OUTLIER, "wide", 0))
    assert manifest.ok is True
    manifest.add_issue("EURUSD", "2026-07-14", 13,
                       ValidationIssue(CROSSED_QUOTE, "crossed", 1))
    assert manifest.ok is False


def test_empty_hour_hashes_to_the_empty_digest() -> None:
    record = HourRecord("EURUSD", "2026-07-11", 13, STATUS_CLOSED)
    assert record.sha256 == EMPTY_SHA256
    assert record.compressed_bytes == 0


def test_coverage_counts_hours_by_outcome() -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2026-07-14", 12, STATUS_OK, written_ticks=10),
        HourRecord("EURUSD", "2026-07-14", 13, STATUS_CLOSED),
        HourRecord("EURUSD", "2026-07-14", 14, STATUS_GAP),
    ])
    day = manifest.coverage()["by_day"][0]
    assert (day["hours_ok"], day["hours_empty"], day["hours_gap"]) == (1, 1, 1)
    assert day["ticks"] == 10


def whole_day(date: str, ticks: int, hours: int = 24) -> list[HourRecord]:
    """Build one day of stored hours carrying ``ticks`` between them."""
    per_hour, remainder = divmod(ticks, hours)
    return [HourRecord("EURUSD", date, h, STATUS_OK,
                       written_ticks=per_hour + (remainder if h == 0 else 0))
            for h in range(hours)]


def test_tick_count_outlier_is_flagged_against_the_trailing_median() -> None:
    hours: list[HourRecord] = []
    for date in ("2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"):
        hours += whole_day(date, 100_000)
    hours += whole_day("2026-07-17", 200)
    payload = Manifest(hours=hours).to_dict()
    warnings = payload["validation"]["warnings"]
    assert [w["reason"] for w in warnings] == ["TICK_COUNT_OUTLIER"]
    assert warnings[0]["date"] == "2026-07-17"


def test_a_short_history_does_not_flag_anything() -> None:
    hours = whole_day("2026-07-13", 100_000) + whole_day("2026-07-14", 200)
    assert Manifest(hours=hours).to_dict()["validation"]["warnings"] == []


def test_partial_days_neither_flag_nor_skew_the_median() -> None:
    # A real FX week opens on Sunday evening and closes on Friday evening, so
    # its first and last days are partial. Comparing those against a full day
    # would flag every single week.
    hours: list[HourRecord] = whole_day("2026-08-09", 1_951, hours=3)
    for date in ("2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"):
        hours += whole_day(date, 40_000)
    hours += whole_day("2026-08-14", 42_000, hours=21)
    payload = Manifest(hours=hours).to_dict()
    assert payload["validation"]["warnings"] == []
    by_day = {d["date"]: d for d in payload["coverage"]["by_day"]}
    assert by_day["2026-08-09"]["whole_trading_day"] is False
    assert by_day["2026-08-14"]["whole_trading_day"] is True


def test_rendering_twice_does_not_duplicate_warnings() -> None:
    hours: list[HourRecord] = []
    for date in ("2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"):
        hours += whole_day(date, 100_000)
    hours += whole_day("2026-07-17", 200)
    manifest = Manifest(hours=hours)
    manifest.to_dict()
    payload = manifest.to_dict()
    assert len(payload["validation"]["warnings"]) == 1


def test_a_reloaded_manifest_does_not_accumulate_derived_warnings(tmp_path) -> None:
    hours: list[HourRecord] = []
    for date in ("2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"):
        hours += whole_day(date, 100_000)
    hours += whole_day("2026-07-17", 200)
    write_manifest(tmp_path, Manifest(hours=hours))
    reloaded = load_manifest(tmp_path)
    warnings = reloaded.to_dict()["validation"]["warnings"]
    assert [w["reason"] for w in warnings] == ["TICK_COUNT_OUTLIER"]


def test_write_and_reload(tmp_path) -> None:
    manifest = Manifest(hours=[HourRecord("EURUSD", "2026-07-14", 13, STATUS_OK,
                                          written_ticks=9_915)])
    path = write_manifest(tmp_path, manifest)
    assert path == manifest_path(tmp_path)
    assert load_manifest(tmp_path).hours[0].written_ticks == 9_915
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_corrupt_manifest_is_treated_as_absent(tmp_path) -> None:
    manifest_path(tmp_path).write_text("{not json", encoding="utf8")
    assert load_manifest(tmp_path).hours == []


def test_gaps_are_listed_separately() -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2026-07-14", 12, STATUS_OK),
        HourRecord("EURUSD", "2026-07-14", 13, STATUS_GAP),
    ])
    assert [r.hour for r in manifest.gaps()] == [13]
