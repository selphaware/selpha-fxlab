"""The cost model protocol and the value types it works with.

A cost model turns a decision (pair, side, size, the quote at the moment of
decision) into an :class:`Execution`: a fill price plus the two cost lines that
made it, spread and commission. Keeping the two apart is deliberate. If gross
P&L is measured fill to fill, the spread paid disappears into the price and
becomes invisible; measuring gross mid to mid and charging the spread as its
own line makes it auditable.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

#: Order sides.
BUY: Final[str] = "buy"
SELL: Final[str] = "sell"

_SIDES: Final[frozenset[str]] = frozenset({BUY, SELL})


def opposite(side: str) -> str:
    """Return the side that closes ``side``."""
    if side not in _SIDES:
        raise ValueError(f"unknown side {side!r}")
    return SELL if side == BUY else BUY


def side_for_units(units: float) -> str:
    """Return the side implied by a signed unit change."""
    if units == 0:
        raise ValueError("a zero-size order has no side")
    return BUY if units > 0 else SELL


@dataclass(frozen=True, slots=True)
class Quote:
    """A two-sided quote at an instant."""

    pair: str
    ts: dt.datetime
    bid: float
    ask: float

    def __post_init__(self) -> None:
        if self.ask < self.bid:
            raise ValueError(
                f"{self.pair} {self.ts}: crossed quote bid={self.bid} ask={self.ask}")

    @property
    def mid(self) -> float:
        """The mid price, which is what P&L is measured against."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """The quoted spread in price units."""
        return self.ask - self.bid

    @property
    def half_spread(self) -> float:
        """Distance from mid to either side, the cost of one crossing."""
        return self.spread / 2.0


@dataclass(frozen=True, slots=True)
class Execution:
    """One filled order and the costs it incurred."""

    pair: str
    ts: dt.datetime
    side: str
    units: float
    fill_price: float
    mid_price: float
    spread_cost: float
    commission: float

    @property
    def total_cost(self) -> float:
        """Spread plus commission for this order."""
        return self.spread_cost + self.commission

    @property
    def notional(self) -> float:
        """Trade value in the quote currency, at the fill price."""
        return abs(self.units) * self.fill_price

    @property
    def signed_units(self) -> float:
        """Units as a signed position delta: positive for a buy."""
        return abs(self.units) if self.side == BUY else -abs(self.units)


@runtime_checkable
class CostModel(Protocol):
    """Anything that can price an order into an :class:`Execution`.

    A future ``RecordedSpreadCostModel`` fed by the IB tick recorder implements
    exactly this and drops into the backtester without touching it. That is the
    whole point of the protocol: the engine depends on this shape, never on
    :class:`~fxlab.costs.ib.IBCostModel` specifically.
    """

    def execute(self, pair: str, side: str, units: float, quote: Quote) -> Execution:
        """Price ``units`` of ``pair`` on ``side`` against ``quote``."""
        ...
