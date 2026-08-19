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


def test_tick_count_outlier_is_flagged_against_the_trailing_median() -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2026-07-13", 0, STATUS_OK, written_ticks=100_000),
        HourRecord("EURUSD", "2026-07-14", 0, STATUS_OK, written_ticks=100_000),
        HourRecord("EURUSD", "2026-07-15", 0, STATUS_OK, written_ticks=200),
    ])
    payload = manifest.to_dict()
    reasons = [w["reason"] for w in payload["validation"]["warnings"]]
    assert reasons == ["TICK_COUNT_OUTLIER"]


def test_rendering_twice_does_not_duplicate_warnings() -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2026-07-13", 0, STATUS_OK, written_ticks=100_000),
        HourRecord("EURUSD", "2026-07-14", 0, STATUS_OK, written_ticks=200),
    ])
    manifest.to_dict()
    payload = manifest.to_dict()
    assert len(payload["validation"]["warnings"]) == 1


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
