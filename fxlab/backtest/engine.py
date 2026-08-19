"""Event-ordered, multi-pair backtester.

Three rules, enforced by construction rather than by convention:

1. **A signal computed on bar t may not fill before bar t+1 open.** Orders are
   enqueued after a bar is processed and executed at the *next* bar. Nothing
   can fill on the bar that produced it, so bar 0 can never trade.
2. **Fills cross the spread.** Every fill goes through a
   :class:`~fxlab.costs.model.CostModel`; the engine has no way to construct a
   price of its own, so the cost model cannot be bypassed.
3. **Gross P&L is measured mid to mid.** The spread paid is booked as its own
   cost line, so ``net = gross - (spread + commission)`` holds exactly and the
   spread is auditable instead of buried in the fill price.

Positions, P&L and equity are per-pair and portfolio-level from the start.

Two conventions worth stating out loud, because both are choices:

* A position that reverses direction is executed as **two orders** (flatten,
  then open). A single netting order would attract one commission rather than
  two, so this errs towards charging too much rather than too little.
* A position still open on the final bar is **closed at that bar close**, so
  that every cost paid belongs to a trade and the summary reconciles exactly.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Final

from fxlab.costs.model import BUY, SELL, CostModel, Execution, Quote

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: Columns a bar frame must carry.
REQUIRED_BAR_COLUMNS: Final[tuple[str, ...]] = (
    "ts", "bid_open", "bid_close", "ask_open", "ask_close",
)


@dataclass(frozen=True, slots=True)
class Bar:
    """One bar. The timestamp is the bar OPEN; the bar covers [ts, ts + delta)."""

    pair: str
    ts: dt.datetime
    bid_open: float
    bid_high: float
    bid_low: float
    bid_close: float
    ask_open: float
    ask_high: float
    ask_low: float
    ask_close: float
    mid_open: float
    mid_high: float
    mid_low: float
    mid_close: float
    tick_count: int = 0
    spread_mean: float = 0.0
    spread_max: float = 0.0

    @property
    def open_quote(self) -> Quote:
        """The quote an order enqueued on the previous bar fills against."""
        return Quote(pair=self.pair, ts=self.ts, bid=self.bid_open, ask=self.ask_open)

    @property
    def close_quote(self) -> Quote:
        """The quote used to mark the book and to force-close at the end."""
        return Quote(pair=self.pair, ts=self.ts, bid=self.bid_close, ask=self.ask_close)


def bars_from_frame(frame: Any, pair: str | None = None) -> list[Bar]:
    """Build :class:`Bar` objects from a bar DataFrame.

    Args:
        frame: A DataFrame carrying at least :data:`REQUIRED_BAR_COLUMNS`.
        pair: Pair label; taken from a ``pair`` column when omitted.

    Returns:
        Bars in ascending timestamp order.

    Raises:
        ValueError: If a required column is missing or the frame is unsorted.
    """
    missing = [c for c in REQUIRED_BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"bar frame is missing column(s) {missing}")

    ordered = frame.sort_values("ts", kind="stable")
    bars: list[Bar] = []
    for row in ordered.itertuples(index=False):
        row_pair = pair or getattr(row, "pair", None)
        if not row_pair:
            raise ValueError("bar rows carry no pair and none was supplied")
        bid_open, bid_close = float(row.bid_open), float(row.bid_close)
        ask_open, ask_close = float(row.ask_open), float(row.ask_close)
        bid_high = float(getattr(row, "bid_high", max(bid_open, bid_close)))
        bid_low = float(getattr(row, "bid_low", min(bid_open, bid_close)))
        ask_high = float(getattr(row, "ask_high", max(ask_open, ask_close)))
        ask_low = float(getattr(row, "ask_low", min(ask_open, ask_close)))
        bars.append(Bar(
            pair=str(row_pair),
            ts=row.ts.to_pydatetime() if hasattr(row.ts, "to_pydatetime") else row.ts,
            bid_open=bid_open, bid_high=bid_high, bid_low=bid_low, bid_close=bid_close,
            ask_open=ask_open, ask_high=ask_high, ask_low=ask_low, ask_close=ask_close,
            mid_open=float(getattr(row, "mid_open", (bid_open + ask_open) / 2.0)),
            mid_high=float(getattr(row, "mid_high", (bid_high + ask_high) / 2.0)),
            mid_low=float(getattr(row, "mid_low", (bid_low + ask_low) / 2.0)),
            mid_close=float(getattr(row, "mid_close", (bid_close + ask_close) / 2.0)),
            tick_count=int(getattr(row, "tick_count", 0) or 0),
            spread_mean=float(getattr(row, "spread_mean", 0.0) or 0.0),
            spread_max=float(getattr(row, "spread_max", 0.0) or 0.0),
        ))
    return bars


@dataclass(slots=True)
class Trade:
    """One completed round trip, with its own cost attribution."""

    pair: str
    units: float
    entry_ts: dt.datetime
    entry_fill: float
    entry_mid: float
    exit_ts: dt.datetime
    exit_fill: float
    exit_mid: float
    gross_pnl: float
    spread_cost: float
    commission: float
    forced_close: bool = False

    @property
    def direction(self) -> int:
        """+1 for a long round trip, -1 for a short one."""
        return 1 if self.units > 0 else -1

    @property
    def total_cost(self) -> float:
        """Spread plus commission across both legs."""
        return self.spread_cost + self.commission

    @property
    def net_pnl(self) -> float:
        """Gross less this trade costs."""
        return self.gross_pnl - self.total_cost

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form for the results file."""
        return {
            "pair": self.pair,
            "units": self.units,
            "direction": self.direction,
            "entry_ts": self.entry_ts.isoformat(),
            "entry_fill": self.entry_fill,
            "entry_mid": self.entry_mid,
            "exit_ts": self.exit_ts.isoformat(),
            "exit_fill": self.exit_fill,
            "exit_mid": self.exit_mid,
            "gross_pnl": self.gross_pnl,
            "spread_cost": self.spread_cost,
            "commission": self.commission,
            "total_cost": self.total_cost,
            "net_pnl": self.net_pnl,
            "forced_close": self.forced_close,
        }


@dataclass(slots=True)
class OpenPosition:
    """A position currently held, with the entry costs not yet attributed."""

    pair: str
    units: float
    entry_ts: dt.datetime
    entry_fill: float
    entry_mid: float
    spread_cost: float
    commission: float

    def merge(self, execution: Execution) -> None:
        """Fold a same-direction addition into the weighted-average entry."""
        added = execution.signed_units
        total = self.units + added
        weight_old = abs(self.units) / abs(total)
        weight_new = abs(added) / abs(total)
        self.entry_mid = self.entry_mid * weight_old + execution.mid_price * weight_new
        self.entry_fill = self.entry_fill * weight_old + execution.fill_price * weight_new
        self.units = total
        self.spread_cost += execution.spread_cost
        self.commission += execution.commission


@dataclass(slots=True)
class EquityPoint:
    """Portfolio equity at one timestamp."""

    ts: dt.datetime
    equity: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {"ts": self.ts.isoformat(), "equity": self.equity}


@dataclass(slots=True)
class BacktestResult:
    """Everything one backtest produced."""

    trades: list[Trade] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    initial_equity: float = 0.0
    bar_count: int = 0
    pairs: tuple[str, ...] = ()
    strategy: str = ""

    @property
    def gross_pnl(self) -> float:
        """Sum of mid-to-mid P&L across closed trades."""
        return sum(t.gross_pnl for t in self.trades)

    @property
    def spread_cost(self) -> float:
        """Total spread crossed, in money."""
        return sum(t.spread_cost for t in self.trades)

    @property
    def commission(self) -> float:
        """Total commission charged."""
        return sum(t.commission for t in self.trades)

    @property
    def total_costs(self) -> float:
        """Spread plus commission."""
        return self.spread_cost + self.commission

    @property
    def net_pnl(self) -> float:
        """Gross less total costs. Asserted exactly by the gate."""
        return self.gross_pnl - self.total_costs

    @property
    def max_drawdown(self) -> float:
        """Largest peak-to-trough fall of the equity curve, as a positive number."""
        peak = float("-inf")
        worst = 0.0
        for point in self.equity:
            peak = max(peak, point.equity)
            worst = max(worst, peak - point.equity)
        return worst


class BacktestEngine:
    """Runs a strategy over bars, filling only through the cost model.

    Args:
        cost_model: Prices every order. There is no other way to get a fill.
        strategy: Produces a target position from each bar.
        initial_equity: Starting equity, purely a display offset.
    """

    def __init__(self, cost_model: CostModel, strategy: Any,
                 initial_equity: float = 0.0) -> None:
        self.cost_model = cost_model
        self.strategy = strategy
        self.initial_equity = float(initial_equity)
        self._positions: dict[str, OpenPosition] = {}
        self._trades: list[Trade] = []
        self._realized_gross = 0.0
        self._costs_paid = 0.0

    def _reset(self) -> None:
        """Clear per-run state so an engine can be reused."""
        self._positions.clear()
        self._trades = []
        self._realized_gross = 0.0
        self._costs_paid = 0.0
        if hasattr(self.strategy, "reset"):
            self.strategy.reset()

    def _equity(self, last_mid: dict[str, float]) -> float:
        """Mark the book: realised P&L, costs paid, and open positions at mid."""
        unrealized = 0.0
        for pair, position in self._positions.items():
            mid = last_mid.get(pair)
            if mid is not None:
                unrealized += (mid - position.entry_mid) * position.units
        return (self.initial_equity + self._realized_gross
                - self._costs_paid + unrealized)

    def _execute(self, pair: str, delta: float, quote: Quote,
                 forced: bool = False) -> None:
        """Fill one order of ``delta`` signed units against ``quote``."""
        if delta == 0:
            return
        side = BUY if delta > 0 else SELL
        execution = self.cost_model.execute(pair, side, abs(delta), quote)
        self._costs_paid += execution.total_cost

        position = self._positions.get(pair)
        if position is None:
            self._positions[pair] = OpenPosition(
                pair=pair, units=execution.signed_units, entry_ts=quote.ts,
                entry_fill=execution.fill_price, entry_mid=execution.mid_price,
                spread_cost=execution.spread_cost, commission=execution.commission)
            return

        if (position.units > 0) == (execution.signed_units > 0):
            position.merge(execution)
            return

        closing = min(abs(position.units), abs(execution.signed_units))
        share = closing / abs(position.units)
        entry_spread = position.spread_cost * share
        entry_commission = position.commission * share
        direction = 1 if position.units > 0 else -1
        gross = (execution.mid_price - position.entry_mid) * closing * direction
        self._realized_gross += gross
        self._trades.append(Trade(
            pair=pair, units=closing * direction,
            entry_ts=position.entry_ts, entry_fill=position.entry_fill,
            entry_mid=position.entry_mid, exit_ts=quote.ts,
            exit_fill=execution.fill_price, exit_mid=execution.mid_price,
            gross_pnl=gross,
            spread_cost=entry_spread + execution.spread_cost,
            commission=entry_commission + execution.commission,
            forced_close=forced))

        remaining = position.units + execution.signed_units
        if remaining == 0:
            del self._positions[pair]
        else:
            position.units = remaining
            position.spread_cost -= entry_spread
            position.commission -= entry_commission

    def _rebalance(self, pair: str, target: float, quote: Quote,
                   forced: bool = False) -> None:
        """Move ``pair`` to ``target`` units, splitting a reversal into two orders."""
        position = self._positions.get(pair)
        current = position.units if position else 0.0
        if target == current:
            return
        if current != 0.0 and target != 0.0 and (current > 0) != (target > 0):
            self._execute(pair, -current, quote, forced=forced)
            self._execute(pair, target, quote, forced=forced)
            return
        self._execute(pair, target - current, quote, forced=forced)

    def run(self, bars_by_pair: dict[str, list[Bar]]) -> BacktestResult:
        """Run the strategy over ``bars_by_pair`` and return the result.

        Args:
            bars_by_pair: Bars per pair, each list in ascending time order.

        Returns:
            A :class:`BacktestResult` whose costs reconcile trade by trade.

        The loop is event ordered across pairs: at every timestamp, orders
        enqueued on the previous bar fill at this bar OPEN, then the book is
        marked, then new orders are enqueued for the next bar. Nothing that
        happens at a timestamp can influence a fill at that same timestamp.
        """
        self._reset()

        events: dict[dt.datetime, list[Bar]] = {}
        final_ts: dict[str, dt.datetime] = {}
        bar_count = 0
        for pair, bars in bars_by_pair.items():
            for bar in bars:
                events.setdefault(bar.ts, []).append(bar)
                final_ts[pair] = bar.ts
                bar_count += 1

        timestamps = sorted(events)
        pending: dict[str, float] = {}
        last_mid: dict[str, float] = {}
        equity: list[EquityPoint] = []

        for ts in timestamps:
            at_ts = sorted(events[ts], key=lambda b: b.pair)

            for bar in at_ts:
                target = pending.pop(bar.pair, None)
                if target is not None:
                    self._rebalance(bar.pair, target, bar.open_quote)

            for bar in at_ts:
                last_mid[bar.pair] = bar.mid_close
                target = float(self.strategy.on_bar(bar))
                position = self._positions.get(bar.pair)
                current = position.units if position else 0.0
                if target != current:
                    pending[bar.pair] = target
                else:
                    pending.pop(bar.pair, None)

            for bar in at_ts:
                if final_ts.get(bar.pair) == ts and bar.pair in self._positions:
                    _LOG.info("%s: closing the open position at the final bar close",
                              bar.pair)
                    self._rebalance(bar.pair, 0.0, bar.close_quote, forced=True)
                    pending.pop(bar.pair, None)

            equity.append(EquityPoint(ts=ts, equity=self._equity(last_mid)))

        result = BacktestResult(
            trades=list(self._trades), equity=equity,
            initial_equity=self.initial_equity, bar_count=bar_count,
            pairs=tuple(sorted(bars_by_pair)), strategy=repr(self.strategy))
        _LOG.info("backtest: %d bar(s), %d trade(s), gross=%.2f costs=%.2f net=%.2f",
                  bar_count, len(result.trades), result.gross_pnl,
                  result.total_costs, result.net_pnl)
        return result


def run_backtest(bars_by_pair: dict[str, list[Bar]], cost_model: CostModel,
                 strategy: Any, initial_equity: float = 0.0) -> BacktestResult:
    """Convenience wrapper around :class:`BacktestEngine`."""
    return BacktestEngine(cost_model, strategy, initial_equity).run(bars_by_pair)
