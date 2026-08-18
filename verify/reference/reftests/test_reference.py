"""Minimal unit tests for the reference implementation (harness machinery)."""

from __future__ import annotations

import datetime as dt

import pytest

from fxlab._core import ValidationError, commission, dedupe, price_scale, validate

UTC = dt.timezone.utc


def test_price_scale_jpy_vs_rest() -> None:
    assert price_scale("USDJPY") == pytest.approx(1e-3)
    assert price_scale("GBPJPY") == pytest.approx(1e-3)
    assert price_scale("EURUSD") == pytest.approx(1e-5)
    assert price_scale("EURGBP") == pytest.approx(1e-5)


def test_dedupe_counts_what_it_drops() -> None:
    row = (dt.datetime(2026, 7, 14, 13, tzinfo=UTC), 1.1, 1.2, 1.0, 1.0)
    kept, dropped = dedupe([row, row, row])
    assert len(kept) == 1
    assert dropped == 2


def test_validate_rejects_crossed_quote() -> None:
    ts = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)
    with pytest.raises(ValidationError) as exc:
        validate([(ts, 1.2, 1.1, 1.0, 1.0)], "EURUSD", ts)
    assert exc.value.reason == "CROSSED_QUOTE"


def test_validate_rejects_non_positive_price() -> None:
    ts = dt.datetime(2026, 7, 14, 13, tzinfo=UTC)
    with pytest.raises(ValidationError) as exc:
        validate([(ts, 0.0, 1.1, 1.0, 1.0)], "EURUSD", ts)
    assert exc.value.reason == "NON_POSITIVE_PRICE"


def test_validate_rejects_saturday_ticks() -> None:
    sat = dt.datetime(2026, 7, 11, 13, tzinfo=UTC)
    assert sat.weekday() == 5
    with pytest.raises(ValidationError) as exc:
        validate([(sat, 1.1, 1.2, 1.0, 1.0)], "EURUSD", sat)
    assert exc.value.reason == "CLOSED_MARKET_TICK"


def test_commission_minimum_binds_on_small_orders() -> None:
    rate, minimum = 0.20 / 10_000, 2.00
    assert commission(5_000, 1.1121, rate, minimum) == pytest.approx(2.00)
    assert commission(1_000_000, 1.1121, rate, minimum) == pytest.approx(22.242)
