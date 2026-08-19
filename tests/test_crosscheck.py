"""The OANDA cross-check job, driven entirely offline."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from fxlab import crosscheck
from fxlab.config import HourRequest, IngestConfig
from fxlab.ingestion.oanda import Candle
from fxlab.ingestion.pipeline import ingest
from tests.conftest import RAW_DIR


def ingest_one_hour(tmp_path):
    config = IngestConfig(
        mode="fixture", raw_dir=RAW_DIR, out_dir=tmp_path / "data",
        hours=(HourRequest("EURUSD", dt.date(2026, 7, 14), 13),))
    assert ingest(config).ok
    return config


def candle_at(hour: int, mid: float, spread: float = 0.00016) -> Candle:
    half = spread / 2.0
    return Candle.from_payload({
        "time": f"2026-07-14T{hour:02d}:00:00.000000000Z",
        "complete": True, "volume": 1000,
        "bid": {"o": f"{mid - half:.5f}", "h": f"{mid:.5f}",
                "l": f"{mid:.5f}", "c": f"{mid:.5f}"},
        "ask": {"o": f"{mid + half:.5f}", "h": f"{mid:.5f}",
                "l": f"{mid:.5f}", "c": f"{mid:.5f}"},
        "mid": {"o": f"{mid:.5f}", "h": f"{mid:.5f}",
                "l": f"{mid:.5f}", "c": f"{mid:.5f}"},
    })


class StubClient:
    """Stands in for OandaClient so no test needs a token or the network."""

    host = "https://api-fxpractice.oanda.com"
    has_token = True

    def __init__(self, candles):
        self._candles = candles
        self.calls = []

    def candles(self, pair, granularity="H1", start=None, end=None, **kwargs):
        self.calls.append((pair, granularity, start, end))
        return self._candles


def run_cli(tmp_path, monkeypatch, client, extra=()):
    config_path = tmp_path / "ingest.toml"
    config_path.write_text(
        "[ingest]\n"
        'mode = "fixture"\n'
        f'raw_dir = "{RAW_DIR.as_posix()}"\n'
        f'out_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[ingest.oanda]\n"
        "enabled = true\n"
        "max_mid_diff_pips = 1.0\n\n"
        "[[ingest.hours]]\n"
        'pair = "EURUSD"\n'
        'date = "2026-07-14"\n'
        "hour = 13\n", encoding="utf8")
    monkeypatch.setattr(crosscheck, "OandaClient", lambda **kw: client)
    return crosscheck.main(["--config", str(config_path), *extra])


def test_agreeing_feeds_pass(tmp_path, monkeypatch) -> None:
    ingest_one_hour(tmp_path)
    client = StubClient([candle_at(13, 1.144615)])
    assert run_cli(tmp_path, monkeypatch, client) == 0
    report = json.loads((tmp_path / "data" / crosscheck.REPORT_NAME).read_text())
    entry = report["pairs"][0]
    assert entry["compared"] == 1
    assert entry["ok"] is True
    assert abs(entry["stats"]["mid_diff_mean_pips"]) < 0.2


def test_a_real_disagreement_fails_the_job(tmp_path, monkeypatch) -> None:
    ingest_one_hour(tmp_path)
    client = StubClient([candle_at(13, 1.15000)])
    assert run_cli(tmp_path, monkeypatch, client) == 1


def test_the_request_span_covers_the_stored_bars(tmp_path, monkeypatch) -> None:
    ingest_one_hour(tmp_path)
    client = StubClient([candle_at(13, 1.144615)])
    run_cli(tmp_path, monkeypatch, client)
    _pair, granularity, start, end = client.calls[0]
    assert granularity == "H1"
    assert start == dt.datetime(2026, 7, 14, 13, tzinfo=dt.timezone.utc)
    assert end == dt.datetime(2026, 7, 14, 14, tzinfo=dt.timezone.utc)


def test_a_missing_token_is_unavailable_not_a_disagreement(
        tmp_path, monkeypatch) -> None:
    ingest_one_hour(tmp_path)

    class NoToken(StubClient):
        has_token = False

    assert run_cli(tmp_path, monkeypatch, NoToken([])) == 3


def test_a_bad_config_is_reported_as_a_config_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(crosscheck, "OandaClient", lambda **kw: StubClient([]))
    assert crosscheck.main(["--config", str(tmp_path / "absent.toml")]) == 2
