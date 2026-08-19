"""The holdout seal: one cutoff, one refusal, no interpretation."""

from __future__ import annotations

import datetime as dt

import pytest

from research import seal


def test_cutoff_and_window_end_are_the_pre_registered_dates() -> None:
    """The two dates are pre-registered and must not drift."""
    assert seal.HOLDOUT_CUTOFF == dt.date(2025, 3, 1)
    assert seal.RESEARCH_WINDOW_END == dt.date(2025, 2, 28)


@pytest.mark.parametrize("value,sealed", [
    ("2025-02-28", False),
    ("2025-03-01", True),      # the boundary is sealed, not the day before it
    ("2024-12-31", False),
    ("2026-08-14", True),      # the Phase 1 live week is inside the seal
])
def test_is_sealed_boundary(value: str, sealed: bool) -> None:
    """The cutoff day itself is sealed."""
    assert seal.is_sealed(value) is sealed


def test_assert_not_sealed_raises_with_the_named_reason() -> None:
    """The reason token is what the gate greps for, so it must be verbatim."""
    with pytest.raises(seal.SealBreach) as caught:
        seal.assert_not_sealed("2025-03-01", "unit test")
    assert caught.value.reason == "HOLDOUT_SEALED"
    assert "HOLDOUT_SEALED" in str(caught.value)


def test_assert_not_sealed_allows_the_last_research_day() -> None:
    """2025-02-28 is the last day research may use."""
    seal.assert_not_sealed(seal.RESEARCH_WINDOW_END)


def test_sealed_dates_in_text_finds_dates_in_comments() -> None:
    """A date in a comment counts: a config is not the place to note the seal."""
    text = "# do not read 2025-06-01\nstart = \"2024-01-01\"\n"
    assert seal.sealed_dates_in_text(text) == ["2025-06-01"]


def test_sealed_dates_in_text_ignores_unsealed_and_nonsense() -> None:
    """Unsealed dates and impossible ones are not reported."""
    assert seal.sealed_dates_in_text("2024-02-29 2025-13-01 2025-02-28") == []


def test_as_date_accepts_datetimes() -> None:
    """Timestamps are reduced to their UTC date."""
    stamp = dt.datetime(2025, 3, 1, 23, 59, tzinfo=dt.timezone.utc)
    assert seal.as_date(stamp) == dt.date(2025, 3, 1)


def test_mechanical_allowlist_is_exactly_the_live_week() -> None:
    """Ruling A: one entry. A second one is a policy change, not a tweak."""
    assert seal.MECHANICAL_ALLOWLIST == ("data/live_week",)
