"""Shared test fixtures.

Tests read the same frozen bi5 files the gate uses. They are real bytes off the
live feed; synthetic tick fixtures cannot catch a decoder that mis-reads real
data, which is the whole class of bug worth testing for here. When the frozen
files are not present the affected tests skip rather than fail, so the suite
still means something in a checkout without them.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "verify" / "fixtures"
RAW_DIR = FIXTURE_DIR / "raw"
POISON_DIR = FIXTURE_DIR / "poison"
BACKTEST_DIR = FIXTURE_DIR / "backtest"


def utc(text: str) -> dt.datetime:
    """Parse an ISO timestamp into an aware UTC datetime."""
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def hour_start(date_str: str, hour: int) -> dt.datetime:
    """Build the UTC instant one bi5 hour opens."""
    return utc(f"{date_str}T{hour:02d}:00:00+00:00")


@pytest.fixture(scope="session")
def expected() -> dict:
    """The frozen ground truth describing every fixture hour."""
    path = FIXTURE_DIR / "expected.json"
    if not path.is_file():
        pytest.skip(f"frozen fixture metadata not available at {path}")
    return json.loads(path.read_text(encoding="utf8"))


@pytest.fixture(scope="session")
def clean_hours(expected: dict) -> list[dict]:
    """Only the fixture hours that carry ticks."""
    return [h for h in expected["hours"] if h["status"] == "ok"]


def read_fixture(directory: pathlib.Path, name: str) -> bytes:
    """Read one frozen payload, skipping the test when it is absent."""
    path = directory / name
    if not path.is_file():
        pytest.skip(f"fixture not available: {path}")
    return path.read_bytes()


@pytest.fixture
def store_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """An empty Parquet store root."""
    target = tmp_path / "data"
    target.mkdir()
    return target
