"""Serialisation of backtest output.

The results file is the artefact everything downstream reads, so its shape is
part of the contract::

    {"summary": {...}, "trades": [...], "equity": [{"ts": ..., "equity": ...}]}

``summary`` carries ``trade_count``, ``gross_pnl``, ``spread_cost``,
``commission``, ``total_costs``, ``net_pnl`` and ``max_drawdown``, and every
trade carries its own ``spread_cost`` and ``commission`` so that costs are
attributable trade by trade rather than only in aggregate. A summary that does
not reconcile with the trades it claims to summarise is not a summary.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from fxlab.backtest.engine import BacktestResult


def summarise(result: BacktestResult) -> dict[str, Any]:
    """Build the summary block for a finished backtest."""
    equity = result.equity
    wins = [t for t in result.trades if t.net_pnl > 0]
    return {
        "trade_count": len(result.trades),
        "gross_pnl": result.gross_pnl,
        "spread_cost": result.spread_cost,
        "commission": result.commission,
        "total_costs": result.total_costs,
        "net_pnl": result.net_pnl,
        "max_drawdown": result.max_drawdown,
        "win_count": len(wins),
        "win_rate": (len(wins) / len(result.trades)) if result.trades else 0.0,
        "bar_count": result.bar_count,
        "pairs": list(result.pairs),
        "strategy": result.strategy,
        "initial_equity": result.initial_equity,
        "final_equity": equity[-1].equity if equity else result.initial_equity,
        "start_ts": equity[0].ts.isoformat() if equity else None,
        "end_ts": equity[-1].ts.isoformat() if equity else None,
    }


def result_to_dict(result: BacktestResult,
                   extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render a :class:`BacktestResult` as the documented results document."""
    payload: dict[str, Any] = {
        "summary": summarise(result),
        "trades": [t.to_dict() for t in result.trades],
        "equity": [p.to_dict() for p in result.equity],
    }
    if extra:
        payload["config"] = extra
    return payload


def write_results(path: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
    """Write the results document atomically and return its path."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf8")
    os.replace(tmp, path)
    return path
