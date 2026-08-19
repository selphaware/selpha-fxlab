"""The coverage and spread report."""

from __future__ import annotations

import datetime as dt
import json

from fxlab import report
from fxlab.config import HourRequest, IngestConfig
from fxlab.ingestion.manifest import STATUS_CLOSED, STATUS_GAP, STATUS_OK, HourRecord, Manifest
from fxlab.ingestion.pipeline import ingest
from tests.conftest import RAW_DIR


def ingest_a_few(tmp_path):
    config = IngestConfig(
        mode="fixture", raw_dir=RAW_DIR, out_dir=tmp_path / "data",
        hours=(HourRequest("EURUSD", dt.date(2026, 7, 14), 13),
               HourRequest("EURUSD", dt.date(2026, 7, 11), 13),
               HourRequest("EURUSD", dt.date(2026, 7, 19), 21)))
    assert ingest(config).ok
    return config


def test_coverage_counts_every_status() -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2026-07-14", 12, STATUS_OK, written_ticks=10,
                   duplicates_dropped=2),
        HourRecord("EURUSD", "2026-07-11", 13, STATUS_CLOSED),
        HourRecord("EURUSD", "2026-07-14", 14, STATUS_GAP,
                   issues=[{"reason": "FETCH_ERROR", "detail": "", "count": 0}]),
    ])
    summary = report.coverage_summary(manifest)
    assert summary["hours_requested"] == 3
    assert summary["by_status"] == {"ok": 1, "closed": 1, "gap": 1}
    assert summary["ticks"] == 10
    assert summary["duplicates_dropped"] == 2
    assert summary["gaps"][0]["reasons"] == ["FETCH_ERROR"]


def test_spread_distribution_is_split_by_session(tmp_path) -> None:
    config = ingest_a_few(tmp_path)
    built = report.build_report(config.out_dir)
    spread = built["pairs"]["EURUSD"]["spread_pips"]
    assert spread["all"]["ticks"] == 9_915 + 222
    # 13:00Z on a Tuesday is the London/New York overlap; 21:00Z on a Sunday
    # is the weekly reopen, which is a different market entirely.
    assert "london_ny_overlap" in spread
    assert "sydney" in spread
    assert spread["sydney"]["p50"] > spread["london_ny_overlap"]["p50"]


def test_report_records_the_hourly_profile(tmp_path) -> None:
    config = ingest_a_few(tmp_path)
    profile = report.build_report(config.out_dir)["pairs"]["EURUSD"][
        "ticks_by_utc_hour"]
    assert profile["13"] == 9_915
    assert profile["21"] == 222
    assert profile["03"] == 0


def test_cli_writes_a_report(tmp_path) -> None:
    ingest_a_few(tmp_path)
    config_path = tmp_path / "ingest.toml"
    config_path.write_text(
        "[ingest]\n"
        'mode = "fixture"\n'
        f'raw_dir = "{RAW_DIR.as_posix()}"\n'
        f'out_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[[ingest.hours]]\n"
        'pair = "EURUSD"\n'
        'date = "2026-07-14"\n'
        "hour = 13\n", encoding="utf8")
    assert report.main(["--config", str(config_path)]) == 0
    payload = json.loads(
        (tmp_path / "data" / report.REPORT_NAME).read_text(encoding="utf8"))
    assert payload["coverage"]["hours_requested"] == 3
    assert payload["pairs"]["EURUSD"]["hours_stored"] == 2


def test_an_empty_store_reports_nothing_rather_than_crashing(tmp_path) -> None:
    built = report.build_report(tmp_path)
    assert built["coverage"]["hours_requested"] == 0
    assert built["pairs"] == {}
