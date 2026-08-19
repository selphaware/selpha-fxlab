"""Event-ordered backtester skeleton.

No research lives here. The package exists to prove that an order raised on
bar t fills at bar t+1, crossing the spread, through a cost model that cannot
be bypassed, with costs that reconcile trade by trade.
"""

from __future__ import annotations

from fxlab.backtest.engine import (
    Bar,
    BacktestEngine,
    BacktestResult,
    EquityPoint,
    Trade,
    bars_from_frame,
    run_backtest,
)
from fxlab.backtest.results import result_to_dict, summarise, write_results
from fxlab.backtest.strategy import MovingAverageCrossStrategy, Strategy

__all__ = [
    "Bar",
    "BacktestEngine",
    "BacktestResult",
    "EquityPoint",
    "MovingAverageCrossStrategy",
    "Strategy",
    "Trade",
    "bars_from_frame",
    "result_to_dict",
    "run_backtest",
    "summarise",
    "write_results",
]
