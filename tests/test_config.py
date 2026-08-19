"""Config parsing: required keys, both date spellings, and range expansion."""

from __future__ import annotations

import datetime as dt

import pytest

from fxlab.config import ConfigError, load_backtest_config, load_ingest_config

INGEST = """
[ingest]
mode = "fixture"
raw_dir = "raw"
out_dir = "data"

[[ingest.hours]]
pair = "EURUSD"
date = "2026-07-14"
hour = 13
"""

BACKTEST = """
[backtest]
bars_path = "bars.parquet"
pair = "EURUSD"
units = 1000000
fast = 2
slow = 4
out_path = "results.json"

[backtest.costs]
commission_rate = 2e-05
commission_min = 2.0
cost_multiplier = 1.0
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf8")
    return path


def test_ingest_config_round_trip(tmp_path) -> None:
    config = load_ingest_config(write(tmp_path, "i.toml", INGEST))
    assert config.mode == "fixture"
    assert config.out_dir.name == "data"
    assert len(config.hours) == 1
    request = config.hours[0]
    assert request.key == ("EURUSD", "2026-07-14", 13)
    assert request.start == dt.datetime(2026, 7, 14, 13, tzinfo=dt.timezone.utc)
    assert request.fixture_name == "EURUSD_2026-07-14_13h.bi5"


def test_bare_toml_dates_are_accepted_too(tmp_path) -> None:
    text = INGEST.replace('date = "2026-07-14"', "date = 2026-07-14")
    assert load_ingest_config(write(tmp_path, "i.toml", text)).hours[0].day == \
        dt.date(2026, 7, 14)


def test_range_expands_across_pairs_and_days(tmp_path) -> None:
    text = """
[ingest]
mode = "live"
out_dir = "data"

[[ingest.range]]
pairs = ["EURUSD", "USDJPY"]
start = "2026-07-13"
end = "2026-07-14"
hours = [12, 13]
"""
    config = load_ingest_config(write(tmp_path, "i.toml", text))
    assert len(config.hours) == 2 * 2 * 2
    assert config.hours[0].key == ("EURUSD", "2026-07-13", 12)
    assert config.hours[-1].key == ("USDJPY", "2026-07-14", 13)


def test_repeated_hours_collapse(tmp_path) -> None:
    text = INGEST + INGEST.split("[ingest]")[1].split("\n\n", 1)[1]
    config = load_ingest_config(write(tmp_path, "i.toml", text))
    assert len(config.hours) == 1


def test_fixture_mode_without_raw_dir_is_refused(tmp_path) -> None:
    text = INGEST.replace('raw_dir = "raw"\n', "")
    with pytest.raises(ConfigError, match="raw_dir"):
        load_ingest_config(write(tmp_path, "i.toml", text))


def test_unknown_mode_is_refused(tmp_path) -> None:
    with pytest.raises(ConfigError, match="mode"):
        load_ingest_config(write(tmp_path, "i.toml",
                                 INGEST.replace("fixture", "guesswork")))


def test_no_hours_is_refused(tmp_path) -> None:
    with pytest.raises(ConfigError, match="no hours"):
        load_ingest_config(write(tmp_path, "i.toml", INGEST.split("[[ingest")[0]))


def test_missing_file_and_missing_section_are_distinct(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_ingest_config(tmp_path / "absent.toml")
    with pytest.raises(ConfigError, match="ingest"):
        load_ingest_config(write(tmp_path, "empty.toml", "[other]\nx = 1\n"))


def test_backtest_config_round_trip(tmp_path) -> None:
    config = load_backtest_config(write(tmp_path, "b.toml", BACKTEST))
    assert (config.pair, config.units, config.fast, config.slow) == \
        ("EURUSD", 1_000_000, 2, 4)
    assert config.costs.commission_rate == 2e-05
    assert config.costs.commission_min == 2.0
    assert config.costs.cost_multiplier == 1.0


def test_backtest_windows_must_be_ordered(tmp_path) -> None:
    with pytest.raises(ConfigError, match="shorter"):
        load_backtest_config(write(tmp_path, "b.toml",
                                   BACKTEST.replace("fast = 2", "fast = 8")))


def test_missing_backtest_key_names_itself(tmp_path) -> None:
    with pytest.raises(ConfigError, match="units"):
        load_backtest_config(write(tmp_path, "b.toml",
                                   BACKTEST.replace("units = 1000000\n", "")))
