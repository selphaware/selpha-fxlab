"""The FX week boundary, which moves with US daylight saving.

These are the cases a hardcoded 21:00 UTC rule gets wrong for half the year.
Every expectation here is anchored to a measurement in SPEC.md.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fxlab.ingestion.sessions import (
    is_market_open,
    market_open_mask,
    session_labels,
    session_of,
    week_bounds,
)
from tests.conftest import utc


def test_summer_week_closes_at_21_utc_on_friday() -> None:
    # EURUSD Fri 2026-07-17 20:00Z carries 1,163 ticks; 21:00Z is empty.
    assert is_market_open(utc("2026-07-17T20:59:59.803+00:00")) is True
    assert is_market_open(utc("2026-07-17T21:00:00+00:00")) is False


def test_summer_week_opens_at_21_utc_on_sunday() -> None:
    # Sun 2026-07-19 20:00Z is empty; 21:00Z carries 222 ticks, the first at
    # 21:00:03.426Z -- three seconds after the open.
    assert is_market_open(utc("2026-07-19T20:59:59+00:00")) is False
    assert is_market_open(utc("2026-07-19T21:00:03.426+00:00")) is True


def test_winter_week_closes_an_hour_later_in_utc() -> None:
    # Fri 2026-01-09 21:00Z still carries 868 ticks; 22:00Z is empty.
    assert is_market_open(utc("2026-01-09T21:00:00+00:00")) is True
    assert is_market_open(utc("2026-01-09T22:00:00+00:00")) is False


def test_a_fixed_utc_hour_cannot_satisfy_both_seasons() -> None:
    # The two assertions below are the proof that the boundary must be derived:
    # 21:00Z is closed in July and open in January.
    assert is_market_open(utc("2026-07-17T21:00:00+00:00")) is False
    assert is_market_open(utc("2026-01-09T21:00:00+00:00")) is True


def test_saturday_is_always_closed() -> None:
    for hour in range(24):
        assert is_market_open(utc(f"2026-07-11T{hour:02d}:00:00+00:00")) is False


def test_midweek_is_always_open() -> None:
    for hour in range(24):
        assert is_market_open(utc(f"2026-07-15T{hour:02d}:00:00+00:00")) is True


def test_naive_timestamps_are_refused() -> None:
    with pytest.raises(ValueError):
        is_market_open(dt.datetime(2026, 7, 14, 13))


def test_week_bounds_track_the_season() -> None:
    summer = week_bounds(utc("2026-07-14T13:00:00+00:00"))
    assert summer[0] == utc("2026-07-12T21:00:00+00:00")
    assert summer[1] == utc("2026-07-17T21:00:00+00:00")
    winter = week_bounds(utc("2026-01-07T13:00:00+00:00"))
    assert winter[0] == utc("2026-01-04T22:00:00+00:00")
    assert winter[1] == utc("2026-01-09T22:00:00+00:00")


def test_vectorised_mask_matches_the_scalar_rule() -> None:
    stamps = [
        "2026-07-17T20:59:59+00:00", "2026-07-17T21:00:00+00:00",
        "2026-07-19T20:00:00+00:00", "2026-07-19T21:00:03+00:00",
        "2026-01-09T21:00:00+00:00", "2026-07-11T13:00:00+00:00",
    ]
    index = pd.DatetimeIndex([utc(s) for s in stamps])
    mask = market_open_mask(index)
    assert list(mask) == [is_market_open(utc(s)) for s in stamps]


def test_session_map_is_derived_from_local_clocks() -> None:
    assert session_of(utc("2026-07-14T13:00:00+00:00")) == "london_ny_overlap"
    assert session_of(utc("2026-07-14T02:00:00+00:00")) == "tokyo"
    assert session_of(utc("2026-07-14T18:00:00+00:00")) == "new_york"
    assert session_of(utc("2026-07-14T23:00:00+00:00")) == "sydney"


def test_vectorised_session_labels_match_the_scalar_rule() -> None:
    stamps = ["2026-07-14T02:00:00+00:00", "2026-07-14T08:00:00+00:00",
              "2026-07-14T13:00:00+00:00", "2026-07-14T18:00:00+00:00",
              "2026-07-14T23:00:00+00:00"]
    index = pd.DatetimeIndex([utc(s) for s in stamps])
    assert list(session_labels(index)) == [session_of(utc(s)) for s in stamps]
