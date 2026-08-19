"""End-to-end ingest in fixture mode, including rejection and resume."""

from __future__ import annotations

import json
import shutil

import pytest

from fxlab.config import HourRequest, IngestConfig
from fxlab.ingest import main as ingest_main
from fxlab.ingestion.manifest import STATUS_CLOSED, STATUS_GAP, STATUS_OK
from fxlab.ingestion.pipeline import ingest
from fxlab.ingestion.sources import FixtureSource
from tests.conftest import POISON_DIR, RAW_DIR
import datetime as dt


def config_for(tmp_path, hours, raw_dir=RAW_DIR, **kwargs) -> IngestConfig:
    return IngestConfig(
        mode="fixture", raw_dir=raw_dir, out_dir=tmp_path / "data",
        hours=tuple(HourRequest(p, dt.date.fromisoformat(d), h) for p, d, h in hours),
        **kwargs)


def test_clean_hours_are_ingested_with_exact_counts(tmp_path) -> None:
    report = ingest(config_for(tmp_path, [
        ("EURUSD", "2026-07-14", 12),
        ("EURUSD", "2026-07-14", 13),
        ("USDJPY", "2026-07-14", 13),
    ]))
    assert report.ok
    counts = {r.key: r.written_ticks for r in report.manifest.hours}
    assert counts[("EURUSD", "2026-07-14", 12)] == 11_297
    assert counts[("EURUSD", "2026-07-14", 13)] == 9_915
    assert counts[("USDJPY", "2026-07-14", 13)] == 11_781
    assert report.ticks_written == 11_297 + 9_915 + 11_781


def test_every_requested_hour_appears_including_the_empty_ones(tmp_path) -> None:
    hours = [("EURUSD", "2026-07-11", 13), ("EURUSD", "2026-07-17", 21),
             ("EURUSD", "2026-07-19", 21)]
    report = ingest(config_for(tmp_path, hours))
    recorded = {r.key for r in report.manifest.hours}
    assert recorded == {(p, d, h) for p, d, h in hours}
    by_key = report.manifest.index()
    assert by_key[("EURUSD", "2026-07-11", 13)].status == STATUS_CLOSED
    assert by_key[("EURUSD", "2026-07-17", 21)].status == STATUS_CLOSED
    assert by_key[("EURUSD", "2026-07-19", 21)].status == STATUS_OK


def test_sunday_open_hour_is_kept_and_friday_close_hour_is_kept(tmp_path) -> None:
    # Both sit within seconds of the weekly boundary. A hardcoded UTC hour
    # would reject one of them as a closed-market tick.
    report = ingest(config_for(tmp_path, [("EURUSD", "2026-07-17", 20),
                                          ("EURUSD", "2026-07-19", 21)]))
    assert report.ok
    counts = {r.key: r.written_ticks for r in report.manifest.hours}
    assert counts[("EURUSD", "2026-07-17", 20)] == 1_163
    assert counts[("EURUSD", "2026-07-19", 21)] == 222


def test_manifest_is_written_and_reloadable(tmp_path) -> None:
    report = ingest(config_for(tmp_path, [("EURUSD", "2026-07-14", 13)]))
    payload = json.loads(report.manifest_file.read_text(encoding="utf8"))
    assert payload["validation"]["ok"] is True
    entry = payload["hours"][0]
    assert entry["sha256"] == (
        "d67fbfd92aaba8253cf4987e4a753b44666cfe96c15c3758be7101393563db5d")
    assert entry["compressed_bytes"] == 43_161
    assert entry["decoded_ticks"] == 9_915
    assert entry["duplicates_dropped"] == 0


@pytest.mark.parametrize("name,pair,date,hour,reason", [
    ("crossed_quote.bi5", "USDJPY", "2026-07-14", 13, "CROSSED_QUOTE"),
    ("non_positive_price.bi5", "USDJPY", "2026-07-14", 13, "NON_POSITIVE_PRICE"),
    ("closed_market.bi5", "USDJPY", "2026-07-11", 13, "CLOSED_MARKET_TICK"),
])
def test_poison_is_rejected_by_name_and_recorded_as_a_gap(
        tmp_path, name, pair, date, hour, reason) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copyfile(POISON_DIR / name, raw_dir / f"{pair}_{date}_{hour:02d}h.bi5")
    report = ingest(config_for(tmp_path, [(pair, date, hour)], raw_dir=raw_dir))
    assert not report.ok
    record = report.manifest.index()[(pair, date, hour)]
    assert record.status == STATUS_GAP
    assert [i["reason"] for i in record.issues] == [reason]
    assert reason in json.dumps(report.manifest.to_dict())


def test_rejected_hour_writes_no_parquet(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copyfile(POISON_DIR / "crossed_quote.bi5",
                    raw_dir / "USDJPY_2026-07-14_13h.bi5")
    ingest(config_for(tmp_path, [("USDJPY", "2026-07-14", 13)], raw_dir=raw_dir))
    assert list((tmp_path / "data").rglob("*.parquet")) == []


def test_duplicates_are_dropped_counted_and_still_accepted(tmp_path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copyfile(POISON_DIR / "duplicate_block.bi5",
                    raw_dir / "USDJPY_2026-07-14_13h.bi5")
    report = ingest(config_for(tmp_path, [("USDJPY", "2026-07-14", 13)],
                               raw_dir=raw_dir))
    assert report.ok
    record = report.manifest.index()[("USDJPY", "2026-07-14", 13)]
    assert (record.decoded_ticks, record.duplicates_dropped,
            record.written_ticks) == (11_881, 100, 11_781)


def test_a_missing_hour_is_a_gap_not_a_silent_skip(tmp_path) -> None:
    report = ingest(config_for(tmp_path, [("EURUSD", "2099-01-01", 5)]))
    assert not report.ok
    record = report.manifest.index()[("EURUSD", "2099-01-01", 5)]
    assert record.status == STATUS_GAP
    assert record.issues[0]["reason"] == "FETCH_ERROR"


def test_gaps_can_be_tolerated_when_configured(tmp_path) -> None:
    report = ingest(config_for(tmp_path, [("EURUSD", "2099-01-01", 5)],
                               fail_on_gap=False))
    assert report.ok
    assert report.hours_gap == 1


def test_resume_does_not_refetch_a_stored_hour(tmp_path) -> None:
    config = config_for(tmp_path, [("EURUSD", "2026-07-14", 13)])
    ingest(config)

    class CountingSource(FixtureSource):
        fetches = 0

        def fetch(self, request):
            type(self).fetches += 1
            return super().fetch(request)

    second = ingest(config, source=CountingSource(RAW_DIR))
    assert CountingSource.fetches == 0
    assert second.hours_skipped == 1
    assert second.ticks_written == 9_915


def test_resume_refetches_when_the_parquet_has_gone(tmp_path) -> None:
    config = config_for(tmp_path, [("EURUSD", "2026-07-14", 13)])
    ingest(config)
    for path in (tmp_path / "data").rglob("*.parquet"):
        path.unlink()
    report = ingest(config)
    assert report.hours_skipped == 0
    assert report.hours_ok == 1


def test_resume_can_be_switched_off(tmp_path) -> None:
    config = config_for(tmp_path, [("EURUSD", "2026-07-14", 13)], resume=False)
    ingest(config)
    assert ingest(config).hours_skipped == 0


def test_bars_are_built_from_stored_ticks_when_asked(tmp_path) -> None:
    report = ingest(config_for(tmp_path, [("EURUSD", "2026-07-14", 13)],
                               bar_timeframes=("5min", "1h")))
    assert len(report.bar_files) == 2
    assert (tmp_path / "data" / "bars").exists()


def test_fixture_mode_never_constructs_a_network_client(tmp_path, monkeypatch) -> None:
    import fxlab.ingestion.sources as sources

    def explode(*_a, **_k):
        raise AssertionError("fixture mode must not open a network client")

    monkeypatch.setattr(sources.urllib.request, "urlopen", explode)
    assert ingest(config_for(tmp_path, [("EURUSD", "2026-07-14", 13)])).ok


def test_cli_exit_codes(tmp_path, capsys) -> None:
    good = tmp_path / "good.toml"
    good.write_text(
        "[ingest]\n"
        'mode = "fixture"\n'
        f'raw_dir = "{RAW_DIR.as_posix()}"\n'
        f'out_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[[ingest.hours]]\n"
        'pair = "EURUSD"\n'
        'date = "2026-07-14"\n'
        "hour = 13\n", encoding="utf8")
    assert ingest_main(["--config", str(good)]) == 0
    assert ingest_main(["--config", str(tmp_path / "absent.toml")]) == 2


def test_cli_reports_a_rejected_hour_on_stderr(tmp_path, capsys) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copyfile(POISON_DIR / "crossed_quote.bi5",
                    raw_dir / "USDJPY_2026-07-14_13h.bi5")
    config = tmp_path / "bad.toml"
    config.write_text(
        "[ingest]\n"
        'mode = "fixture"\n'
        f'raw_dir = "{raw_dir.as_posix()}"\n'
        f'out_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[[ingest.hours]]\n"
        'pair = "USDJPY"\n'
        'date = "2026-07-14"\n'
        "hour = 13\n", encoding="utf8")
    assert ingest_main(["--config", str(config)]) == 1
    assert "CROSSED_QUOTE" in capsys.readouterr().err


def test_manifest_is_checkpointed_during_a_long_run(tmp_path, monkeypatch) -> None:
    # An interrupted live pull must not lose the ledger; if it does, the next
    # run re-fetches everything and earns the same throttling that stopped it.
    import fxlab.ingestion.pipeline as pipeline_module

    checkpoints: list[int] = []
    real = pipeline_module.write_manifest

    def watching(out_dir, manifest):
        checkpoints.append(len(manifest.hours))
        return real(out_dir, manifest)

    monkeypatch.setattr(pipeline_module, "write_manifest", watching)
    ingest(config_for(tmp_path, [("EURUSD", "2026-07-14", 12),
                                 ("EURUSD", "2026-07-14", 13),
                                 ("EURUSD", "2026-07-14", 14)],
                      checkpoint_every=1))
    # One checkpoint per settled hour, plus the final write.
    assert checkpoints == [1, 2, 3, 3]


def test_a_run_interrupted_after_a_checkpoint_resumes_from_it(
        tmp_path, monkeypatch) -> None:
    import fxlab.ingestion.pipeline as pipeline_module

    config = config_for(tmp_path, [("EURUSD", "2026-07-14", 12),
                                   ("EURUSD", "2026-07-14", 13),
                                   ("EURUSD", "2026-07-14", 14)],
                        checkpoint_every=1)
    real = pipeline_module.write_manifest
    calls = {"n": 0}

    def die_after_two(out_dir, manifest):
        calls["n"] += 1
        result = real(out_dir, manifest)
        if calls["n"] == 2:
            raise KeyboardInterrupt("simulated interruption")
        return result

    monkeypatch.setattr(pipeline_module, "write_manifest", die_after_two)
    with pytest.raises(KeyboardInterrupt):
        ingest(config)

    monkeypatch.setattr(pipeline_module, "write_manifest", real)
    resumed = ingest(config)
    assert resumed.hours_skipped == 2
    assert resumed.hours_ok == 3
