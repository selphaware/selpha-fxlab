"""The IB cost model: crossing the spread, the rate, and the per-order floor."""

from __future__ import annotations

import datetime as dt

import pytest

from fxlab.config import CostConfig
from fxlab.costs import (
    BUY,
    SELL,
    CostModel,
    Execution,
    IBCostModel,
    Quote,
    RecordedSpreadCostModel,
    opposite,
    side_for_units,
)

TS = dt.datetime(2026, 7, 14, 5, tzinfo=dt.timezone.utc)
ENTRY = Quote("EURUSD", TS, bid=1.1119, ask=1.1121)
EXIT = Quote("EURUSD", TS.replace(hour=9), bid=1.0979, ask=1.0981)


def test_buy_fills_at_the_ask_and_sell_at_the_bid() -> None:
    model = IBCostModel()
    assert model.execute("EURUSD", BUY, 1000, ENTRY).fill_price == ENTRY.ask
    assert model.execute("EURUSD", SELL, 1000, ENTRY).fill_price == ENTRY.bid


def test_a_fill_never_happens_at_the_mid() -> None:
    model = IBCostModel()
    for side in (BUY, SELL):
        execution = model.execute("EURUSD", side, 1000, ENTRY)
        assert execution.fill_price != ENTRY.mid
        assert execution.spread_cost > 0


def test_spread_cost_is_the_distance_from_mid_to_fill() -> None:
    execution = IBCostModel().execute("EURUSD", BUY, 1_000_000, ENTRY)
    assert execution.spread_cost == pytest.approx(100.0)
    assert execution.mid_price == pytest.approx(1.1120)


def test_large_order_is_priced_by_the_rate() -> None:
    model = IBCostModel(commission_rate=2e-05, commission_min=2.0)
    entry = model.execute("EURUSD", BUY, 1_000_000, ENTRY)
    exit_ = model.execute("EURUSD", SELL, 1_000_000, EXIT)
    assert entry.commission == pytest.approx(22.242)
    assert exit_.commission == pytest.approx(21.958)
    assert entry.commission > model.commission_min


def test_small_order_hits_the_two_dollar_minimum_on_both_legs() -> None:
    model = IBCostModel()
    entry = model.execute("EURUSD", BUY, 5_000, ENTRY)
    exit_ = model.execute("EURUSD", SELL, 5_000, EXIT)
    assert entry.commission == pytest.approx(2.0)
    assert exit_.commission == pytest.approx(2.0)
    assert model.commission_rate * entry.notional < model.commission_min


def test_the_minimum_binds_exactly_at_the_crossover() -> None:
    model = IBCostModel(commission_rate=2e-05, commission_min=2.0)
    # notional of 100_000 gives exactly 2.00 at 0.20 bp.
    at_floor = model.commission_for(100_000 / ENTRY.ask, ENTRY.ask)
    assert at_floor == pytest.approx(2.0)
    assert model.commission_for(1.0, ENTRY.ask) == pytest.approx(2.0)


def test_cost_multiplier_scales_both_lines() -> None:
    plain = IBCostModel().execute("EURUSD", BUY, 1_000_000, ENTRY)
    stressed = IBCostModel(cost_multiplier=2.0).execute(
        "EURUSD", BUY, 1_000_000, ENTRY)
    assert stressed.spread_cost == pytest.approx(plain.spread_cost * 2)
    assert stressed.commission == pytest.approx(plain.commission * 2)


def test_model_is_built_from_config_not_constants() -> None:
    model = IBCostModel.from_config(
        CostConfig(commission_rate=5e-05, commission_min=3.0, cost_multiplier=1.5))
    assert (model.commission_rate, model.commission_min) == (5e-05, 3.0)
    assert model.cost_multiplier == 1.5


def test_crossed_quote_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="crossed"):
        Quote("EURUSD", TS, bid=1.2, ask=1.1)


def test_side_helpers() -> None:
    assert opposite(BUY) == SELL and opposite(SELL) == BUY
    assert side_for_units(10) == BUY and side_for_units(-10) == SELL
    with pytest.raises(ValueError):
        side_for_units(0)


def test_execution_reports_notional_and_signed_units() -> None:
    execution = IBCostModel().execute("EURUSD", SELL, 1_000, ENTRY)
    assert execution.notional == pytest.approx(1_000 * ENTRY.bid)
    assert execution.signed_units == -1_000
    assert execution.total_cost == execution.spread_cost + execution.commission


def test_a_second_implementation_satisfies_the_same_protocol() -> None:
    # The future RecordedSpreadCostModel must drop in without the backtester
    # knowing. If this ever fails, the engine has grown a dependency on IB.
    recorded = RecordedSpreadCostModel(
        spread_by_session={"london_ny_overlap": 0.00008}, default_spread=0.0002)
    assert isinstance(recorded, CostModel)

    overlap = Quote("EURUSD", TS.replace(hour=13), bid=1.1119, ask=1.1121)
    execution = recorded.execute("EURUSD", BUY, 1_000_000, overlap)
    assert isinstance(execution, Execution)
    assert execution.fill_price == pytest.approx(overlap.mid + 0.00004)
    assert execution.spread_cost == pytest.approx(40.0)

    # Outside a recorded session the fallback spread applies instead.
    tokyo = recorded.execute("EURUSD", BUY, 1_000_000, ENTRY)
    assert tokyo.fill_price == pytest.approx(ENTRY.mid + 0.0001)
