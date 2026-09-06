"""Round-trip execution cost, produced by the Phase 1 cost model and nothing else.

The T5 card is explicit about where a cost figure may come from: every one of
them is computed by :class:`fxlab.costs.IBCostModel` at the pre-registered
ladder, never by a pip figure somebody typed into a report. That rule is what
this module exists to make structural. Nothing here contains a spread, a
commission rate or a minimum; it takes quotes measured from the store, hands
them to the model, and hands the model's answer back.

Three things are worth stating before the code.

**A round trip is two orders, and both are priced.** Entry crosses to the ask,
exit crosses back to the bid, and each pays its own commission. The Phase 1
accounting convention (``CLAUDE.md`` §Accounting) measures gross P&L mid to
mid precisely so the two crossings show up as an explicit cost line, and that
is what makes the comparison in this card possible at all: a "cost" that had
been buried in a fill price could not be put beside a return distribution.

**The comparison is in basis points of notional, and that is currency-free.**
``spread_cost`` and ``commission`` are both in the quote currency, and so is
``units * price``. Their ratio is dimensionless and therefore correct for all
twelve pairs -- with one exception, which is the whole of SPEC2 prerequisite
P0-A: the ``commission_min`` floor is a **USD** 2.00 figure that the Phase 1
model applies to a quote-currency notional. Where the floor binds, a non-USD
pair's commission is wrong. So :func:`floor_notional` measures where it binds,
every caller checks that its reference size is above that, and any table that
cannot make that claim says so.

**The cost multiplier scales the finished cost exactly.** ``IBCostModel``
applies ``cost_multiplier`` to both lines, floor included, so the cost at 1.5x
is exactly 1.5 times the cost at 1.0x. That is a property of the model rather
than an assumption about it, which is why :func:`multiplier_check` asks the
model itself, on a grid that includes floor-binding sizes, and why the answer
travels inside the hashed result.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final, Mapping, Sequence

import numpy as np

from fxlab.costs import BUY, SELL, IBCostModel, Quote

#: Basis points per unit.
BP: Final[float] = 1e4

#: The timestamp handed to every :class:`~fxlab.costs.model.Quote` built here.
#: ``IBCostModel`` reads a quote's bid, ask and mid and never its time -- only
#: ``RecordedSpreadCostModel`` looks at the clock, and this card does not use
#: it. Building twelve million real timestamps to be ignored would cost more
#: than the cost model does.
EPOCH: Final[dt.datetime] = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def model_for(costs: Mapping[str, Any], multiplier: float) -> IBCostModel:
    """The declared cost model at one rung of the ladder.

    Args:
        costs: The experiment's ``[experiment.costs]`` table. Read rather than
            defaulted: SPEC2's cost rule is one model with one set of
            parameters for every candidate, declared in the config and carried
            in the result, and a default here would be a second set nobody
            declared.
        multiplier: The ladder rung, applied as ``cost_multiplier``.

    Raises:
        KeyError: If the config does not declare the model's parameters. An
            absent parameter is not a zero.
    """
    return IBCostModel(commission_rate=float(costs["commission_rate"]),
                       commission_min=float(costs["commission_min"]),
                       cost_multiplier=float(multiplier))


def ladder_models(costs: Mapping[str, Any],
                  ladder: Sequence[str]) -> dict[str, IBCostModel]:
    """One model per rung, keyed by the rung's string name."""
    return {rung: model_for(costs, float(rung)) for rung in ladder}


def quote(pair: str, mid: float, spread: float) -> Quote:
    """A two-sided quote around ``mid`` with ``spread`` in price units."""
    half = spread / 2.0
    return Quote(pair=pair, ts=EPOCH, bid=mid - half, ask=mid + half)


def round_trip(model: IBCostModel, pair: str, entry_mid: float,
               entry_spread: float, exit_mid: float,
               exit_spread: float, units: float) -> dict[str, float]:
    """Price one round trip -- buy then sell -- through the cost model.

    Returns:
        The two cost lines, their total, and the total as basis points of the
        entry notional, which is the break-even move: a trade whose gross
        mid-to-mid return is smaller than this loses money by arithmetic,
        before any question of whether the move was forecastable.
    """
    entry = model.execute(pair, BUY, units, quote(pair, entry_mid, entry_spread))
    exit_ = model.execute(pair, SELL, units, quote(pair, exit_mid, exit_spread))
    spread_cost = entry.spread_cost + exit_.spread_cost
    commission = entry.commission + exit_.commission
    total = spread_cost + commission
    notional = abs(units) * entry_mid
    return {
        "spread_cost": spread_cost,
        "commission": commission,
        "total": total,
        "notional": notional,
        "break_even_bp": total / notional * BP if notional else float("nan"),
    }


def round_trip_bp(model: IBCostModel, pair: str, entry_mid: np.ndarray,
                  entry_spread: np.ndarray, exit_mid: np.ndarray,
                  exit_spread: np.ndarray,
                  units: float) -> tuple[np.ndarray, np.ndarray]:
    """Break-even move in basis points for many round trips, by cost line.

    One :meth:`IBCostModel.execute` call per leg, per trip: a vectorised
    re-derivation would be faster and would also be a second cost model, which
    is the thing the card's rule exists to prevent. Twelve million trips cost
    about a minute, and the alternative costs the guarantee.

    Returns:
        ``(spread_bp, commission_bp)``, both as basis points of the entry
        notional. Kept apart because they behave completely differently: the
        spread is a measured property of the market at that moment and the
        commission is a constant 0.20 bp per order until the floor binds, so a
        report that added them up would hide which one a horizon dies of.
    """
    n = int(entry_mid.size)
    spread_bp = np.empty(n, dtype="float64")
    commission_bp = np.empty(n, dtype="float64")
    execute = model.execute
    for i in range(n):
        mid_in = float(entry_mid[i])
        half_in = float(entry_spread[i]) / 2.0
        mid_out = float(exit_mid[i])
        half_out = float(exit_spread[i]) / 2.0
        entry = execute(pair, BUY, units,
                        Quote(pair, EPOCH, mid_in - half_in, mid_in + half_in))
        exit_ = execute(pair, SELL, units,
                        Quote(pair, EPOCH, mid_out - half_out,
                              mid_out + half_out))
        scale = BP / (units * mid_in)
        spread_bp[i] = (entry.spread_cost + exit_.spread_cost) * scale
        commission_bp[i] = (entry.commission + exit_.commission) * scale
    return spread_bp, commission_bp


def floor_notional(model: IBCostModel, high: float = 1e12) -> float:
    """The notional at which the commission rate overtakes the per-order floor.

    Found by bisection on the model rather than by dividing its parameters,
    because the question is where *this object* stops flooring, and a division
    would be a second statement of the model's arithmetic sitting next to the
    first. Below the answer the order pays the floor; above it, the rate.

    The number is in the **quote** currency, which is the whole of P0-A: the
    floor is a USD 2.00 figure and the notional it is compared against is not
    USD for eight of the twelve pairs.
    """
    target = model.commission_min * model.cost_multiplier
    low = 0.0
    if model.commission_for(high, 1.0) <= target:
        return float("inf")
    for _ in range(200):
        mid = (low + high) / 2.0
        if model.commission_for(mid, 1.0) > target:
            high = mid
        else:
            low = mid
    return high


def multiplier_check(costs: Mapping[str, Any], ladder: Sequence[str],
                     grid: Sequence[tuple[float, float, float]]
                     ) -> dict[str, Any]:
    """Ask the model whether a rung really is a multiple of the base cost.

    Every per-bar cost in this card is priced once at 1.0x and multiplied,
    which is only legitimate if ``cost_multiplier`` scales the finished cost
    including the floor. It does -- but "it does" is a claim about code, so
    this measures it, on a grid that deliberately includes sizes small enough
    for the floor to bind, and the measurement travels inside the hashed
    result where a reader can see it.

    Args:
        costs: The declared cost parameters.
        ladder: Rung names.
        grid: ``(mid, spread, units)`` triples to price.

    Returns:
        The largest absolute and relative disagreement found, and how many
        grid points had the floor binding at all -- a check that found no
        floored point would not have checked the interesting case.
    """
    base = model_for(costs, 1.0)
    worst_abs = 0.0
    worst_rel = 0.0
    floored = 0
    for mid, spread, units in grid:
        priced = round_trip(base, "EURUSD", mid, spread, mid, spread, units)
        if priced["commission"] > 2.0 * base.commission_rate * units * mid:
            floored += 1
        for rung in ladder:
            model = model_for(costs, float(rung))
            actual = round_trip(model, "EURUSD", mid, spread, mid, spread,
                                units)["total"]
            expected = priced["total"] * float(rung)
            gap = abs(actual - expected)
            worst_abs = max(worst_abs, gap)
            worst_rel = max(worst_rel, gap / expected if expected else 0.0)
    return {
        "grid_points": len(grid),
        "points_with_the_floor_binding": floored,
        "worst_absolute_disagreement": worst_abs,
        "worst_relative_disagreement": worst_rel,
        # Not "exact": the model multiplies in floating point, so 22.0 * 1.2
        # and max(22.0, 2.0) * 1.2 can differ in the last bit. A tolerance of
        # 1e-12 relative is four orders of magnitude tighter than anything a
        # basis-point figure could notice and still admits the arithmetic
        # being genuinely wrong.
        "within_tolerance": worst_rel <= 1e-12,
    }


def floor_binding_legs(model: IBCostModel, entry_mid: np.ndarray,
                       entry_spread: np.ndarray, exit_mid: np.ndarray,
                       exit_spread: np.ndarray, units: float,
                       ) -> tuple[np.ndarray, np.ndarray]:
    """Which legs of each round trip pay the per-order minimum, not the rate.

    T5's reference size of 1,000,000 units was chosen so that this was empty.
    Decision D9 moves the reference to 100,000, where it is not, so the card's
    P0-A caveat stops being a formality and starts being a column: the floor is
    the one term in the model whose currency is wrong for the eight pairs that
    are not USD-quoted, and it is now inside the arithmetic rather than beside
    it.

    The threshold is :func:`floor_notional` -- bisected off the model, not
    divided out of its parameters -- and each leg is compared at the price it
    would actually fill at: the entry crosses to the ask, the exit back to the
    bid. Both are in the quote currency, which is exactly the defect.

    Returns:
        ``(entry_floored, exit_floored)``, boolean arrays. Kept apart because a
        round trip can floor on one leg and not the other: the two legs price
        at different notionals whenever the market has moved between them.
    """
    threshold = floor_notional(model)
    size = abs(float(units))
    entry_notional = size * (np.asarray(entry_mid, dtype="float64")
                             + np.asarray(entry_spread, dtype="float64") / 2.0)
    exit_notional = size * (np.asarray(exit_mid, dtype="float64")
                            - np.asarray(exit_spread, dtype="float64") / 2.0)
    return entry_notional <= threshold, exit_notional <= threshold
