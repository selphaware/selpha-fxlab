"""Execution cost modelling.

Costs are venue-specific and mids are not, so the cost model is an explicit,
parametric object rather than something inherited from whichever data feed
happened to supply the prices. The backtester cannot produce a fill without
going through one, which is the only reliable way to stop a cost model from
being quietly bypassed.
"""

from __future__ import annotations

from fxlab.costs.model import (
    BUY,
    SELL,
    CostModel,
    Execution,
    Quote,
    opposite,
    side_for_units,
)
from fxlab.costs.ib import IBCostModel, RecordedSpreadCostModel

__all__ = [
    "BUY",
    "SELL",
    "CostModel",
    "Execution",
    "IBCostModel",
    "Quote",
    "RecordedSpreadCostModel",
    "opposite",
    "side_for_units",
]
