"""The backtester known answers, and the two bugs they exist to catch.

Lookahead and mid-fill are both invisible in an equity curve, so they are
tested against hand-computed answers that differ from the correct one.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from fxlab.backtest import (
    BacktestEngine,
    MovingAverageCrossStrategy,
    bars_from_frame,
    result_to_dict,
    run_backtest,
)
from fxlab.costs import IBCostModel
from tests.conftest import BACKTEST_DIR

BASE = dt.datetime(2026, 7, 14, tzinfo=dt.timezone.utc)


class ScriptedStrategy:
    """Returns a pre-written target position per bar, for engine-level tests."""

    def __init__(self, targets: list[float]) -> None:
        self.targets = list(targets)
        self.seen = 0

    def reset(self) -> None:
        self.seen = 0

    def on_bar(self, bar) -> float:
        target = self.targets[self.seen] if self.seen < len(self.targets) else 0.0
        self.seen += 1
        return target


def make_bars(mids: list[float], spread: float = 0.0002, pair: str = "EURUSD"):
    """Build flat bars whose open and close both sit at the given mid."""
    half = spread / 2.0
    frame = pd.DataFrame({
        "pair": [pair] * len(mids),
        "ts": [BASE + dt.timedelta(hours=i) for i in range(len(mids))],
        "bid_open": [m - half for m in mids], "bid_high": [m - half for m in mids],
        "bid_low": [m - half for m in mids], "bid_close": [m - half for m in mids],
        "ask_open": [m + half for m in mids], "ask_high": [m + half for m in mids],
        "ask_low": [m + half for m in mids], "ask_close": [m + half for m in mids],
        "mid_open": mids, "mid_high": mids, "mid_low": mids, "mid_close": mids,
        "tick_count": [1] * len(mids),
        "spread_mean": [spread] * len(mids), "spread_max": [spread] * len(mids),
    })
    return bars_from_frame(frame, pair)


@pytest.fixture(scope="module")
def fixture_bars():
    path = BACKTEST_DIR / "bars_EURUSD_1h.parquet"
    if not path.is_file():
        pytest.skip(f"backtest bars fixture not available at {path}")
    import pyarrow.parquet as pq

    return bars_from_frame(pq.read_table(path).to_pandas(), "EURUSD")


@pytest.fixture(scope="module")
def known_answers():
    path = BACKTEST_DIR / "expected_backtest.json"
    if not path.is_file():
        pytest.skip(f"backtest known answers not available at {path}")
    return json.loads(path.read_text(encoding="utf8"))


def _run(bars, units, fast=2, slow=4, model=None):
    return run_backtest({"EURUSD": bars}, model or IBCostModel(),
                        MovingAverageCrossStrategy(fast, slow, units))


@pytest.mark.parametrize("scenario", ["large", "small"])
def test_known_answer_net_pnl(fixture_bars, known_answers, scenario) -> None:
    spec = known_answers["scenarios"][scenario]
    result = _run(fixture_bars, spec["units"],
                  known_answers["fast"], known_answers["slow"])
    want = spec["correct"]
    assert len(result.trades) == want["trade_count"]
    assert result.gross_pnl == pytest.approx(want["gross_pnl"], abs=1e-6)
    assert result.spread_cost == pytest.approx(want["spread_cost"], abs=1e-6)
    assert result.commission == pytest.approx(want["commission"], abs=1e-6)
    assert result.net_pnl == pytest.approx(want["net_pnl"], abs=1e-6)


@pytest.mark.parametrize("scenario", ["large", "small"])
@pytest.mark.parametrize("counterfactual", [
    "counterfactual_lookahead_net_pnl",
    "counterfactual_mid_fill_net_pnl",
    "counterfactual_zero_cost_net_pnl",
    "counterfactual_zero_commission_net_pnl",
    "counterfactual_zero_spread_net_pnl",
])
def test_does_not_produce_any_known_wrong_answer(
        fixture_bars, known_answers, scenario, counterfactual) -> None:
    spec = known_answers["scenarios"][scenario]
    result = _run(fixture_bars, spec["units"],
                  known_answers["fast"], known_answers["slow"])
    assert abs(result.net_pnl - spec[counterfactual]) > 1e-6, counterfactual


def test_fill_uses_the_next_bar_open_not_the_signal_bar() -> None:
    # A target raised on bar 1 must fill at bar 2, whose price is far away.
    bars = make_bars([1.1000, 1.1000, 1.5000, 1.5000])
    engine = BacktestEngine(IBCostModel(), ScriptedStrategy([0, 1000, 1000, 0]))
    result = engine.run({"EURUSD": bars})
    trade = result.trades[0]
    assert trade.entry_ts == bars[2].ts
    assert trade.entry_fill == pytest.approx(bars[2].ask_open)
    assert trade.entry_fill != pytest.approx(bars[1].ask_open)


def test_nothing_can_trade_on_the_first_bar() -> None:
    bars = make_bars([1.1000, 1.1000, 1.1000])
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, 1000, 0])).run({"EURUSD": bars})
    assert result.trades[0].entry_ts == bars[1].ts
    assert result.equity[0].equity == 0.0


def test_entry_crosses_the_spread_and_exit_crosses_it_back() -> None:
    bars = make_bars([1.1000, 1.1000, 1.1000, 1.1000], spread=0.0002)
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, 1000, 0, 0])).run({"EURUSD": bars})
    trade = result.trades[0]
    assert trade.entry_fill == pytest.approx(1.1001)
    assert trade.exit_fill == pytest.approx(1.0999)
    assert trade.gross_pnl == pytest.approx(0.0)
    assert trade.spread_cost == pytest.approx(0.2)


def test_gross_is_measured_mid_to_mid_so_spread_stays_visible() -> None:
    bars = make_bars([1.1000, 1.1000, 1.2000, 1.2000])
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, 1000, 0, 0])).run({"EURUSD": bars})
    trade = result.trades[0]
    assert trade.entry_mid == pytest.approx(1.1000)
    assert trade.exit_mid == pytest.approx(1.2000)
    assert trade.gross_pnl == pytest.approx(100.0)
    assert trade.spread_cost > 0
    assert result.gross_pnl != pytest.approx(result.net_pnl)


def test_costs_reconcile_trade_by_trade(fixture_bars, known_answers) -> None:
    result = _run(fixture_bars, 1_000_000,
                  known_answers["fast"], known_answers["slow"])
    per_trade = sum(t.spread_cost + t.commission for t in result.trades)
    assert per_trade == pytest.approx(result.total_costs, abs=1e-9)
    assert result.gross_pnl - result.total_costs == pytest.approx(
        result.net_pnl, abs=1e-9)


def test_equity_curve_agrees_with_the_trades(fixture_bars, known_answers) -> None:
    result = _run(fixture_bars, 1_000_000,
                  known_answers["fast"], known_answers["slow"])
    moved = result.equity[-1].equity - result.equity[0].equity
    assert moved == pytest.approx(result.net_pnl, abs=1e-9)
    assert len(result.equity) == len(fixture_bars)


def test_max_drawdown_is_a_positive_peak_to_trough() -> None:
    bars = make_bars([1.10, 1.10, 1.20, 1.05, 1.05])
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, 1000, 1000, 0, 0])).run(
                                {"EURUSD": bars})
    assert result.max_drawdown > 0


def test_a_reversal_is_executed_as_two_orders() -> None:
    bars = make_bars([1.10, 1.10, 1.10, 1.10, 1.10])
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, -1000, -1000, 0, 0])).run(
                                {"EURUSD": bars})
    assert [t.direction for t in result.trades] == [1, -1]
    # Two round trips, four legs, four commissions at the floor.
    assert result.commission == pytest.approx(8.0)


def test_open_position_is_closed_at_the_final_bar_so_costs_reconcile() -> None:
    bars = make_bars([1.10, 1.10, 1.10])
    result = BacktestEngine(IBCostModel(),
                            ScriptedStrategy([1000, 1000, 1000])).run({"EURUSD": bars})
    assert len(result.trades) == 1
    assert result.trades[0].forced_close is True
    assert sum(t.spread_cost + t.commission
               for t in result.trades) == pytest.approx(result.total_costs)


def test_multi_pair_positions_and_equity_are_kept_apart() -> None:
    engine = BacktestEngine(IBCostModel(), ScriptedStrategy([1000, 1000, 0, 0]))
    result = engine.run({
        "EURUSD": make_bars([1.10, 1.10, 1.20, 1.20]),
        "USDJPY": make_bars([160.0, 160.0, 150.0, 150.0], spread=0.02,
                            pair="USDJPY"),
    })
    assert result.pairs == ("EURUSD", "USDJPY")
    assert {t.pair for t in result.trades} == {"EURUSD", "USDJPY"}
    assert result.equity[-1].equity == pytest.approx(result.net_pnl)


def test_results_document_has_the_contracted_shape(fixture_bars, known_answers) -> None:
    payload = result_to_dict(_run(fixture_bars, 1_000_000,
                                  known_answers["fast"], known_answers["slow"]))
    assert set(payload) >= {"summary", "trades", "equity"}
    for field in ("trade_count", "gross_pnl", "spread_cost", "commission",
                  "total_costs", "net_pnl", "max_drawdown"):
        assert field in payload["summary"], field
    for trade in payload["trades"]:
        assert "spread_cost" in trade and "commission" in trade
    assert set(payload["equity"][0]) == {"ts", "equity"}
