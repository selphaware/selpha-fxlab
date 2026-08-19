"""Interactive Brokers cost model.

Two components, both explicit:

* **Spread.** Fills cross the quoted spread: buy at the ask, sell at the bid,
  never at the mid. The cost booked is the distance from the mid to the fill,
  times size, so that gross P&L can be measured mid to mid and the spread stays
  a visible line rather than a haircut hidden in the price.
* **Commission.** IB tier 1 FX pricing: 0.20 bp of trade value with a USD 2.00
  minimum per order. Both numbers are configuration, not constants, and
  ``cost_multiplier`` scales the finished cost for stress runs at 1.5x or 2x.

Known limitation, deliberately not papered over: trade value is computed in the
**quote** currency (units times fill price). For a USD-quoted pair that is USD
and the minimum applies directly; for a JPY-quoted pair it is JPY, and a
faithful model would convert to USD before applying the USD 2.00 floor. Phase 1
prices USD-quoted pairs exactly and overstates the floor elsewhere. Doing it
properly needs a cross rate at fill time, which is a Phase 2 concern.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Final, Mapping

from fxlab.costs.model import BUY, SELL, Execution, Quote

#: IB tier 1 defaults, restated here so the code reads without the config file.
DEFAULT_COMMISSION_RATE: Final[float] = 2e-05
DEFAULT_COMMISSION_MIN: Final[float] = 2.0


@dataclass(frozen=True, slots=True)
class IBCostModel:
    """Cross the quoted spread and charge IB tiered commission.

    Attributes:
        commission_rate: Fraction of notional, 2e-05 being 0.20 bp.
        commission_min: Per-order floor in the quote currency.
        cost_multiplier: Applied to both cost lines for stress runs.
    """

    commission_rate: float = DEFAULT_COMMISSION_RATE
    commission_min: float = DEFAULT_COMMISSION_MIN
    cost_multiplier: float = 1.0

    @classmethod
    def from_config(cls, config: Any) -> IBCostModel:
        """Build from a :class:`~fxlab.config.CostConfig`."""
        return cls(commission_rate=float(config.commission_rate),
                   commission_min=float(config.commission_min),
                   cost_multiplier=float(config.cost_multiplier))

    def fill_price(self, side: str, quote: Quote) -> float:
        """Return the crossing price: the ask for a buy, the bid for a sell."""
        if side == BUY:
            return quote.ask
        if side == SELL:
            return quote.bid
        raise ValueError(f"unknown side {side!r}")

    def commission_for(self, units: float, fill_price: float) -> float:
        """Charge the greater of the rate and the per-order minimum.

        Args:
            units: Order size, sign ignored.
            fill_price: The price the order filled at.

        Returns:
            Commission for this one order, after ``cost_multiplier``.
        """
        notional = abs(units) * fill_price
        return max(self.commission_rate * notional,
                   self.commission_min) * self.cost_multiplier

    def spread_cost_for(self, units: float, fill_price: float, mid: float) -> float:
        """Book the distance from mid to fill as an explicit cost."""
        return abs(fill_price - mid) * abs(units) * self.cost_multiplier

    def execute(self, pair: str, side: str, units: float, quote: Quote) -> Execution:
        """Price one order against ``quote``.

        Args:
            pair: The pair being traded.
            side: ``buy`` or ``sell``.
            units: Order size, sign ignored; ``side`` carries the direction.
            quote: The two-sided quote the order fills against.

        Returns:
            The resulting :class:`~fxlab.costs.model.Execution`.
        """
        fill = self.fill_price(side, quote)
        mid = quote.mid
        return Execution(
            pair=pair, ts=quote.ts, side=side, units=abs(units),
            fill_price=fill, mid_price=mid,
            spread_cost=self.spread_cost_for(units, fill, mid),
            commission=self.commission_for(units, fill),
        )


@dataclass(frozen=True, slots=True)
class RecordedSpreadCostModel:
    """Price fills from recorded venue spreads instead of the quoted spread.

    This is the shape the IB tick recorder will feed once venue-true spread
    distributions exist: the mid comes from the research feed, the spread comes
    from what IB was actually showing at that time of week. It exists now, and
    is unit-tested against the same protocol, so that swapping it in later is a
    configuration change rather than a backtester change.

    ``spread_by_session`` maps a session label (see
    :mod:`fxlab.ingestion.sessions`) to a spread in **price units**. Sessions
    with no recorded spread fall back to ``default_spread``.
    """

    spread_by_session: Mapping[str, float]
    default_spread: float
    commission_rate: float = DEFAULT_COMMISSION_RATE
    commission_min: float = DEFAULT_COMMISSION_MIN
    cost_multiplier: float = 1.0

    def spread_at(self, ts: dt.datetime) -> float:
        """Return the recorded spread applying at ``ts``."""
        from fxlab.ingestion.sessions import session_of

        return float(self.spread_by_session.get(session_of(ts), self.default_spread))

    def execute(self, pair: str, side: str, units: float, quote: Quote) -> Execution:
        """Price one order using the recorded spread around the quoted mid."""
        mid = quote.mid
        half = self.spread_at(quote.ts) / 2.0
        if side == BUY:
            fill = mid + half
        elif side == SELL:
            fill = mid - half
        else:
            raise ValueError(f"unknown side {side!r}")
        notional = abs(units) * fill
        return Execution(
            pair=pair, ts=quote.ts, side=side, units=abs(units),
            fill_price=fill, mid_price=mid,
            spread_cost=half * abs(units) * self.cost_multiplier,
            commission=max(self.commission_rate * notional,
                           self.commission_min) * self.cost_multiplier,
        )
