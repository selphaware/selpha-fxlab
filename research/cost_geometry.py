"""The T5 experiment: where edge can survive once the round trip is paid for.

T4 measured what the twelve pairs *are*. This card measures what they *cost*,
and puts the two side by side. Its output is an arithmetic bound and a map, not
a strategy and not a scorecard: a horizon whose median move is smaller than its
own round trip cannot host a rule that trades every bar, and no amount of
signal changes that. Which is the point -- a cell this card closes is closed
before anybody spends a walk-forward on it.

Five decisions shape every number here, and each is stated rather than buried.

**Every cost comes out of the Phase 1 cost model.** Not one pip figure is typed
into this module or into the report it feeds. :mod:`research.costs` builds two
quotes from stored bars, hands them to :class:`fxlab.costs.IBCostModel` and
hands the answer back; the ladder rungs are the model's own
``cost_multiplier``. The card is explicit about this and it is the difference
between measuring a cost and asserting one.

**The comparison is in basis points of notional.** Gross P&L is
``(mid_exit - mid_entry) * units``, so a trade breaks even when its mid-to-mid
return equals the round-trip cost divided by the notional. Both are in the
quote currency, so the ratio is currency-free -- which is why this card can
report a cost geometry for all twelve pairs while SPEC2 prerequisite P0-A is
still open. The single exception is the ``commission_min`` floor, which is a
USD figure applied to a quote-currency notional; the reference size here is
checked against :func:`research.costs.floor_notional` on every bar, and the
share of bars where the floor binds travels in the result so the claim can be
audited rather than believed.

**A round trip is priced at the bars it would actually cross.** For a move from
bar ``t-1``'s close to bar ``t``'s close, entry pays bar ``t-1``'s spread and
exit pays bar ``t``'s. Using one bar's spread twice would understate the cost
of exactly the moments the spread is moving, which is most of the ones that
matter.

**Regimes are conditioned, never fitted** -- the same rule as T4, and for the
same reason. The volatility tercile of a return comes from the standard
deviation of the returns strictly before it.

**The D2 test set was fixed before any cost was computed.** SPEC2's M4
decisions name the eleven T4 reversion cells; this module reads them out of the
config and verdicts every one. It cannot add a cell, and a cell cannot be
dropped after its arithmetic is seen.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import pathlib
from dataclasses import dataclass
from typing import Any, Final, Sequence

import numpy as np

import fxlab.costs as fxcosts
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import SESSIONS, session_labels
from research import costs as cost_lib
from research import crosscheck_class as cc
from research import stats
from research.character import (DENSITY_BANDS, R3_REFERENCE_BAND, Series,
                                density_band, eras_from_classes,
                                in_roll_window, load_series)
from research.experiment import LADDER, PARK_BAR, SURVIVAL_BAR
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.cost_geometry")

#: Basis points per unit.
BP: Final[float] = 1e4

#: Volatility regimes, in the order a table reads best.
TERCILES: Final[tuple[str, ...]] = ("low", "mid", "high")

#: Horizons at which a session label means something. A 4-hour bar spans two
#: sessions and a daily bar spans all of them, so a session statistic there
#: would be a statistic about the label rather than about the market.
SESSION_MAX_MINUTES: Final[int] = 60

#: Bar length in minutes, by the card's horizon label.
HORIZON_MINUTES: Final[dict[str, int]] = {
    "5m": 5, "30m": 30, "1h": 60, "4h": 240, "1d": 1440,
}

#: The era partition the card names for the pre-2013 question. Calendar years,
#: fixed by the card rather than derived, so the split cannot be chosen after
#: seeing which one makes the early data look better. R7's agreement-derived
#: era tags are reported beside it rather than instead of it -- the card asks
#: for both, and they answer different questions: one is "when", the other is
#: "how well could the cross-check see it".
CALENDAR_ERAS: Final[tuple[tuple[str, int, int], ...]] = (
    ("2005-2008", 2005, 2008), ("2009-2012", 2009, 2012), ("2013+", 2013, 9999),
)

#: Verdicts a D2 test-set cell can receive. The first two are pre-reg #1's,
#: applied to an analytical bound rather than to a walk-forward net P&L;
#: ``CLOSED`` is this card's word for what pre-reg #1 calls dead, because a
#: cell that fails a bound was never a candidate to kill.
SURVIVES: Final[str] = "SURVIVES"
PARKED: Final[str] = "PARKED"
CLOSED: Final[str] = "CLOSED"

#: The grid :func:`research.costs.multiplier_check` prices, chosen to include
#: sizes where the per-order floor binds and sizes where it does not, at a
#: USD-quoted and a JPY-quoted price level. A check that never floors has not
#: checked the case the floor exists for.
MULTIPLIER_GRID: Final[tuple[tuple[float, float, float], ...]] = (
    (1.10, 0.00010, 1_000_000.0), (1.10, 0.00010, 10_000.0),
    (1.10, 0.00100, 1_000.0), (150.0, 0.010, 1_000_000.0),
    (150.0, 0.010, 500.0), (150.0, 0.100, 100.0),
)


def _r(value: Any, places: int) -> Any:
    """Round for the hash, passing ``None`` and non-finite values through.

    The result document is hashed and must reproduce exactly, so a float whose
    last two bits came from the order a sum happened in is a float that will
    eventually fail the gate for no reason a message can name.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(number):
        return None
    return round(number, places)


def _q(values: np.ndarray, probability: float) -> float | None:
    """One quantile, or ``None`` when the sample is too small to have one."""
    if values.size < stats.MIN_SAMPLE:
        return None
    return float(np.quantile(values, probability))


# --------------------------------------------------------------------------- #
# One pair at one horizon, with its costs
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Priced:
    """A return series with the round-trip cost of each of its moves.

    Attributes:
        series: The T4 return series, gap-aware, loaded through the loader.
        abs_bp: ``|log return|`` in basis points, one per return.
        spread_bp, commission_bp: The two cost lines at the **1.0x** rung, as
            basis points of the entry notional, one per return.
        cost_bp: Their sum -- the break-even move at 1.0x.
        session: Session label per return, or ``None`` above the session grain.
        roll: Whether the return's bar opens inside the roll window.
        tercile: ``low``/``mid``/``high``/``""`` per return.
        floor_bars: How many returns were priced at a notional small enough for
            the USD 2.00 floor to bind. The P0-A caveat is only load-bearing
            where this is non-zero.
    """

    series: Series
    abs_bp: np.ndarray
    spread_bp: np.ndarray
    commission_bp: np.ndarray
    cost_bp: np.ndarray
    session: np.ndarray | None
    roll: np.ndarray | None
    tercile: np.ndarray
    floor_bars: int

    @property
    def pair(self) -> str:
        return self.series.pair

    @property
    def label(self) -> str:
        return self.series.label


def price_series(series: Series, model: Any, units: float, *,
                 roll: tuple[int, int], vol_window: int,
                 floor_notional: float) -> Priced:
    """Attach a round-trip cost to every move in a return series.

    The cost is computed once, at the 1.0x rung; every other rung is that cost
    times the rung, which is a property of ``IBCostModel`` rather than an
    assumption about it and is measured by
    :func:`research.costs.multiplier_check` in the same result.
    """
    pip = pair_spec(series.pair).pip_size
    spread_price = series.spread_pips * pip
    pos = series.ret_pos
    entry_mid = series.mid_close[pos - 1]
    exit_mid = series.mid_close[pos]
    spread_bp, commission_bp = cost_lib.round_trip_bp(
        model, series.pair, entry_mid, spread_price[pos - 1], exit_mid,
        spread_price[pos], units)
    floor_bars = int((units * entry_mid <= floor_notional).sum())

    stamps = series.stamps()
    minutes = HORIZON_MINUTES.get(series.label, 10 ** 6)
    session = None
    roll_mask = None
    if minutes <= SESSION_MAX_MINUTES:
        session = np.asarray(session_labels(stamps), dtype=object)[pos]
        roll_mask = in_roll_window(stamps, roll[0], roll[1])[pos]

    trailing = stats.trailing_volatility(series.returns, vol_window)
    edges = stats.tercile_edges(trailing[np.isfinite(trailing)])
    tercile = np.full(series.returns.size, "", dtype=object)
    if edges is not None:
        low, high = edges
        finite = np.isfinite(trailing)
        tercile[finite & (trailing <= low)] = "low"
        tercile[finite & (trailing > low) & (trailing <= high)] = "mid"
        tercile[finite & (trailing > high)] = "high"

    return Priced(
        series=series,
        abs_bp=np.abs(series.returns) * BP,
        spread_bp=spread_bp, commission_bp=commission_bp,
        cost_bp=spread_bp + commission_bp,
        session=session, roll=roll_mask, tercile=tercile,
        floor_bars=floor_bars)


def slices_of(priced: Priced) -> dict[str, np.ndarray]:
    """Every conditioning this card reports, as boolean masks over returns.

    One dictionary rather than five loops, so that a slice added for one
    section automatically appears in the others and no table quietly reports a
    different set of conditionings from its neighbour.
    """
    n = priced.abs_bp.size
    out: dict[str, np.ndarray] = {"all": np.ones(n, dtype=bool)}
    if priced.session is not None:
        for name in SESSIONS:
            out[f"session:{name}"] = priced.session == name
    if priced.roll is not None:
        out["roll"] = priced.roll
        out["off_roll"] = ~priced.roll
    for name in TERCILES:
        out[f"tercile:{name}"] = priced.tercile == name
    return out


def describe(priced: Priced, mask: np.ndarray) -> dict[str, Any]:
    """Moves, costs and the share of moves that pay for themselves.

    The last of those is the card's central quantity, and it is computed move
    by move rather than by comparing two medians: spread and volatility move
    together, so a median move measured against a median cost is not the same
    question as how often a move beat the cost that was actually quoted around
    it.
    """
    n = int(mask.sum())
    if n < stats.MIN_SAMPLE:
        return {"n": n, "usable": False}
    moves = priced.abs_bp[mask]
    cost = priced.cost_bp[mask]
    spread = priced.spread_bp[mask]
    commission = priced.commission_bp[mask]
    bar_spread = priced.series.spread_pips[priced.series.ret_pos[mask]]
    bar_ticks = priced.series.tick_count[priced.series.ret_pos[mask]]
    rho = stats.autocorr_at(priced.series.returns, priced.series.spans, 1,
                            select=mask)
    out: dict[str, Any] = {
        "n": n,
        "usable": True,
        "share_of_returns": _r(n / priced.abs_bp.size, 6),
        "move_bp": {name: _r(_q(moves, p), 4) for name, p in
                    (("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
                     ("p75", 0.75), ("p90", 0.90), ("p99", 0.99))},
        "move_mean_bp": _r(float(moves.mean()), 4),
        "cost_bp": {"p50": _r(_q(cost, 0.50), 5),
                    "p90": _r(_q(cost, 0.90), 5)},
        "spread_cost_bp_p50": _r(_q(spread, 0.50), 5),
        "commission_bp_p50": _r(_q(commission, 0.50), 5),
        "median_spread_pips": _r(float(np.median(bar_spread)), 4),
        "p90_spread_pips": _r(float(np.quantile(bar_spread, 0.9)), 4),
        "median_ticks": _r(float(np.median(bar_ticks)), 1),
        "rho1": _r(rho["rho"], 6),
        "rho1_n": int(rho["n"]),
        "sd_bp": _r(float(priced.series.returns[mask].std(ddof=1)) * BP, 4)
        if n > 1 else None,
    }
    ladder: dict[str, Any] = {}
    for rung in LADDER:
        multiple = float(rung)
        threshold = cost * multiple
        median_cost = _q(threshold, 0.50)
        ladder[rung] = {
            "cost_bp_p50": _r(median_cost, 5),
            "cost_bp_p90": _r(_q(threshold, 0.90), 5),
            "share_of_moves_above_cost": _r(float((moves > threshold).mean()),
                                            6),
            "median_move_over_cost": _r(
                (out["move_bp"]["p50"] / median_cost)
                if median_cost else None, 4),
        }
    out["ladder"] = ladder
    return out


# --------------------------------------------------------------------------- #
# Section 1 -- the cost floor
# --------------------------------------------------------------------------- #

def cost_floor(priced: Priced) -> dict[str, Any]:
    """The round-trip cost floor by session and by volatility tercile.

    Both cuts of the same series, at the session grain, with each cell's own
    median tick count beside it. R3 forbids comparing a spread statistic across
    eras without holding density still; the density column is what lets a
    reader see whether a session comparison is really a density comparison, and
    :func:`spread_by_band` is the controlled version.
    """
    masks = slices_of(priced)
    by_session: dict[str, Any] = {}
    if priced.session is not None:
        for name in SESSIONS:
            by_session[name] = describe(priced, masks[f"session:{name}"])
    by_tercile = {name: describe(priced, masks[f"tercile:{name}"])
                  for name in TERCILES}
    cross: dict[str, Any] = {}
    if priced.session is not None:
        for name in SESSIONS:
            for tercile in TERCILES:
                mask = masks[f"session:{name}"] & masks[f"tercile:{tercile}"]
                row = describe(priced, mask)
                if row.get("usable"):
                    cross[f"{name}|{tercile}"] = row
    return {"all": describe(priced, masks["all"]),
            "by_session": by_session,
            "by_tercile": by_tercile,
            "by_session_and_tercile": cross}


def spread_by_band(priced: Priced) -> dict[str, Any]:
    """Cost inside tick-count bands -- ruling R3's control, as a cost table.

    T4 reported the spread this way. The same bands are used here so the two
    cards' tables can be read against each other, and the cost is added because
    that is the quantity this card compares things to.
    """
    pos = priced.series.ret_pos
    bands = density_band(priced.series.tick_count[pos])
    out: dict[str, Any] = {}
    for name, _low, _high in DENSITY_BANDS:
        mask = bands == name
        row = describe(priced, mask)
        if row.get("usable"):
            out[name] = row
    return out


def cheapest_band(floor: dict[str, Any]) -> dict[str, Any]:
    """The pair's own cheapest executable session -- decision D3, quantified.

    D3 carries session restriction into T7 as an **execution constraint**: trade
    where the spread is in its own cheapest band, never as a claim that the
    session predicts anything. So the answer here is a cost ranking and nothing
    else, and the roll window is excluded because pre-reg #4 already excludes
    it from execution -- ranking a window nothing may trade in would produce a
    "cheapest band" no strategy could use.
    """
    usable = {name: row for name, row in floor["by_session"].items()
              if row.get("usable")}
    if not usable:
        return {"session": None}
    ranked = sorted(usable.items(),
                    key=lambda kv: kv[1]["ladder"][SURVIVAL_BAR]["cost_bp_p50"])
    best_name, best = ranked[0]
    worst_name, worst = ranked[-1]
    reference = floor["all"]
    return {
        "session": best_name,
        "cost_bp_at_survival_bar": best["ladder"][SURVIVAL_BAR]["cost_bp_p50"],
        "median_spread_pips": best["median_spread_pips"],
        "median_ticks": best["median_ticks"],
        "share_of_returns": best["share_of_returns"],
        "dearest_session": worst_name,
        "dearest_cost_bp_at_survival_bar":
            worst["ladder"][SURVIVAL_BAR]["cost_bp_p50"],
        "saving_versus_all_hours_bp": _r(
            reference["ladder"][SURVIVAL_BAR]["cost_bp_p50"]
            - best["ladder"][SURVIVAL_BAR]["cost_bp_p50"], 5),
        "ratio_dearest_to_cheapest": _r(
            worst["ladder"][SURVIVAL_BAR]["cost_bp_p50"]
            / best["ladder"][SURVIVAL_BAR]["cost_bp_p50"]
            if best["ladder"][SURVIVAL_BAR]["cost_bp_p50"] else None, 3),
        "ranking": [name for name, _row in ranked],
    }


def minimum_viable_notional(costs: dict[str, Any], pairs: Sequence[str],
                            prices: dict[str, float]) -> dict[str, Any]:
    """Where the USD 2.00 per-order floor stops binding, and what P0-A changes.

    The floor is the one place in the cost model where the quote currency
    matters, so this is the concrete size of SPEC2 prerequisite P0-A. The
    notional is measured off the model by bisection; the per-pair unit figure
    divides it by the pair's own median mid over the window.

    Nothing in this dictionary is used as a cost anywhere. It is the caveat,
    quantified, and the conversion rates are the medians of a conversion pair's
    own mid over the research window -- an illustration of the size of the gap,
    never a conversion applied to a result. P0-A requires a fill-time,
    lookahead-safe rate, and implementing it is a different card.
    """
    per_rung: dict[str, Any] = {}
    for rung in LADDER:
        model = cost_lib.model_for(costs, float(rung))
        per_rung[rung] = _r(cost_lib.floor_notional(model), 4)
    base = cost_lib.model_for(costs, 1.0)
    threshold = cost_lib.floor_notional(base)
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        quote_ccy = pair[3:]
        price = prices.get(pair)
        conversion = None if quote_ccy == "USD" else _conversion_pair(quote_ccy)
        usd_per_quote = None
        if conversion and conversion in prices:
            rate = prices[conversion]
            usd_per_quote = (1.0 / rate if conversion.startswith("USD")
                             else rate)
        rows.append({
            "pair": pair,
            "quote_currency": quote_ccy,
            "usd_quoted": quote_ccy == "USD",
            "median_mid": _r(price, 6),
            "floor_binds_below_quote_notional": _r(threshold, 2),
            "floor_binds_below_units": _r(threshold / price if price else None,
                                          1),
            "conversion_pair_p0a_would_use": conversion,
            "illustrative_usd_per_quote_unit": _r(usd_per_quote, 8),
            "illustrative_floor_in_usd": _r(
                base.commission_min * usd_per_quote
                if usd_per_quote is not None else base.commission_min, 4),
        })
    return {
        "quote_notional_where_the_rate_overtakes_the_floor": per_rung,
        "commission_min": base.commission_min,
        "commission_rate": base.commission_rate,
        "by_pair": rows,
        "note": ("the floor is a USD figure applied to a quote-currency "
                 "notional; the illustrative columns size the gap and are "
                 "used in no cost figure anywhere in this card"),
    }


def _conversion_pair(currency: str) -> str:
    """The universe pair P0-A would convert ``currency`` to USD through."""
    return f"USD{currency}" if currency in ("JPY", "CHF", "CAD") \
        else f"{currency}USD"


# --------------------------------------------------------------------------- #
# Section 3 -- the minimum viable holding period
# --------------------------------------------------------------------------- #

def minimum_holding_period(by_horizon: dict[str, dict[str, Any]],
                           horizons: Sequence[str], rung: str) -> dict[str, Any]:
    """The shortest horizon whose median move clears its own cost at ``rung``.

    Read down the horizon ladder in order and stop at the first one that pays
    for itself. ``None`` means no horizon on the ladder does, which is a
    finding rather than a missing value and is reported as one.
    """
    out: dict[str, Any] = {}
    slice_names = set()
    for horizon in horizons:
        slice_names.update(by_horizon.get(horizon, {}))
    for name in sorted(slice_names):
        first = None
        detail: list[dict[str, Any]] = []
        for horizon in horizons:
            row = by_horizon.get(horizon, {}).get(name)
            if not row or not row.get("usable"):
                continue
            median = row["move_bp"]["p50"]
            cost = row["ladder"][rung]["cost_bp_p50"]
            clears = median is not None and cost is not None and median > cost
            detail.append({"horizon": horizon, "move_bp_p50": median,
                           "cost_bp_p50": cost, "clears": clears,
                           "ratio": _r(median / cost if cost else None, 4)})
            if clears and first is None:
                first = horizon
        out[name] = {"shortest_horizon_that_clears": first, "ladder": detail}
    return out


# --------------------------------------------------------------------------- #
# Section 4 -- the D2 test set
# --------------------------------------------------------------------------- #

def sub_spans(series: Series, mask: np.ndarray) -> tuple[np.ndarray,
                                                         list[tuple[int, int]]]:
    """The selected returns, and the contiguous runs inside the selection.

    A variance ratio needs a contiguous window, so it can only be conditioned
    on something that comes in contiguous blocks. A session does: it is a run
    of consecutive bars each day. A volatility tercile does not, which is why
    the tercile rows below carry a lag-1 autocorrelation and no variance ratio,
    and say so rather than leaving a blank column.
    """
    selected = np.asarray(mask, dtype=bool)
    values = series.returns[selected]
    positions = series.ret_pos[selected]
    return values, stats.segments_of(positions)


def implied_edge(series: Series, mask: np.ndarray, row: dict[str, Any],
                 vr_q: int, *, with_variance_ratio: bool) -> dict[str, Any]:
    """The gross edge per trade a reversion rule could imply, two ways.

    **Lag-1.** ``|rho(1)| x sd``, exactly as the card specifies. A rule
    forecasting ``r_t = rho * r_{t-1}`` has a forecast whose standard deviation
    is ``|rho| sd``; trading its sign earns ``E|forecast|``, which for a
    Gaussian is ``sqrt(2/pi) = 0.798`` times that. The card's figure is the
    larger of the two, so using it errs towards keeping a cell open rather than
    closing one on an unstated refinement.

    **Variance ratio.** Over ``q`` bars a random walk would have variance
    ``q sd^2`` and the series has ``VR(q) q sd^2``. The difference is variance
    that the reverting component removed, so the standard deviation of anything
    forecastable from the past is at most ``sqrt((1 - VR(q)) q) sd`` -- and one
    round trip buys the whole ``q``-bar hold rather than one bar of it, which
    is the only reason a longer hold can beat a shorter one on the same signal.
    It is an upper bound and is labelled as one: it credits the rule with every
    basis point of removed variance, which no rule gets.

    Both are gross. The costs are subtracted by the caller.
    """
    sd_bp = row.get("sd_bp")
    rho = row.get("rho1")
    out: dict[str, Any] = {
        "sd_bp": sd_bp,
        "rho1": rho,
        "rho1_n": row.get("rho1_n"),
        "lag1_edge_bp": _r(abs(rho) * sd_bp
                           if rho is not None and sd_bp is not None else None,
                           5),
        "lag1_edge_bp_expected_absolute": _r(
            math.sqrt(2.0 / math.pi) * abs(rho) * sd_bp
            if rho is not None and sd_bp is not None else None, 5),
        "variance_ratio": None,
        "variance_ratio_z": None,
        "vr_edge_bp": None,
        "vr_hold_bars": vr_q,
    }
    if with_variance_ratio:
        values, spans = sub_spans(series, mask)
        vr = stats.variance_ratio_segments(values, spans, vr_q)
        out["variance_ratio"] = _r(vr["vr"], 6)
        out["variance_ratio_z"] = _r(vr["z"], 4)
        out["variance_ratio_n"] = int(vr["n"])
        if vr["vr"] is not None and sd_bp is not None and vr["vr"] < 1.0:
            out["vr_edge_bp"] = _r(
                math.sqrt((1.0 - float(vr["vr"])) * vr_q) * sd_bp, 5)
    return out


def verdict_for_edge(edge_bp: float | None, cost_bp: dict[str, Any]
                     ) -> dict[str, Any]:
    """Compare one edge against the whole ladder and name the verdict.

    Pre-reg #1's rule, applied to a bound: above the cost at 1.5x survives,
    above it at 1.2x but not 1.5x is parked, anything else is closed. Nothing
    else is thresholded, and no threshold is added.
    """
    if edge_bp is None:
        return {"verdict": None, "net_bp": {}}
    net = {rung: _r(edge_bp - cost_bp[rung], 5) for rung in LADDER}
    if net[SURVIVAL_BAR] is not None and net[SURVIVAL_BAR] > 0.0:
        verdict = SURVIVES
    elif net[PARK_BAR] is not None and net[PARK_BAR] > 0.0:
        verdict = PARKED
    else:
        verdict = CLOSED
    return {"verdict": verdict, "net_bp": net,
            "edge_over_cost_at_survival_bar": _r(
                edge_bp / cost_bp[SURVIVAL_BAR]
                if cost_bp[SURVIVAL_BAR] else None, 4)}


#: Verdicts ranked worst to best, so "the best variant" is a well-defined pick.
VERDICT_RANK: Final[dict[str, int]] = {CLOSED: 0, PARKED: 1, SURVIVES: 2}


def test_set_cell(priced: Priced, cheapest: str | None,
                  vr_q: int) -> dict[str, Any]:
    """Verdict one D2 cell, unconditionally and under every conditioning.

    The cell's verdict is the **best** of its variants. That is deliberate and
    it is the conservative direction for a bound: a cell whose most favourable
    conditioning still cannot pay for its round trip cannot be rescued by a
    backtest, and closing it costs nothing. The cost of the asymmetry is that a
    SURVIVES verdict here is a licence to test, never a result -- picking the
    best of ten variants is a selection, and a T7 card that acts on one owes
    the trial count.
    """
    masks = slices_of(priced)
    variants: dict[str, Any] = {}

    def add(name: str, mask: np.ndarray, *, vr: bool) -> None:
        row = describe(priced, mask)
        if not row.get("usable"):
            return
        cost = {rung: row["ladder"][rung]["cost_bp_p50"] for rung in LADDER}
        edge = implied_edge(priced.series, mask, row, vr_q,
                            with_variance_ratio=vr)
        variants[name] = {
            "n": row["n"],
            "median_spread_pips": row["median_spread_pips"],
            "median_ticks": row["median_ticks"],
            "cost_bp": cost,
            "move_bp_p50": row["move_bp"]["p50"],
            "share_of_moves_above_cost":
                row["ladder"][SURVIVAL_BAR]["share_of_moves_above_cost"],
            "edge": edge,
            "lag1": verdict_for_edge(edge["lag1_edge_bp"], cost),
            "variance_ratio_bound": verdict_for_edge(edge["vr_edge_bp"], cost),
        }

    add("all hours", masks["all"], vr=True)
    if cheapest and f"session:{cheapest}" in masks:
        add(f"session {cheapest}", masks[f"session:{cheapest}"], vr=True)
    if "off_roll" in masks:
        add("outside the roll window", masks["off_roll"], vr=True)
    for name in TERCILES:
        add(f"{name} volatility", masks[f"tercile:{name}"], vr=False)

    # Ranked on the verdict first and on the net at the survival bar second.
    # The tie-break matters most where every variant is CLOSED: without it the
    # reported row would be whichever measure happened to be checked first,
    # and the honest thing to show for a closed cell is the variant that came
    # closest to not being closed.
    best_name = None
    best_route = None
    best_key = (-1, float("-inf"))
    for name, row in variants.items():
        for route in ("lag1", "variance_ratio_bound"):
            verdict = row[route]["verdict"]
            if verdict is None:
                continue
            net = row[route]["net_bp"].get(SURVIVAL_BAR)
            key = (VERDICT_RANK[verdict],
                   float(net) if net is not None else float("-inf"))
            if key > best_key:
                best_key, best_name, best_route = key, name, route
    verdict = (CLOSED if best_name is None
               else variants[best_name][best_route]["verdict"])
    computed = sum(1 for row in variants.values()
                   for route in ("lag1", "variance_ratio_bound")
                   if row[route]["verdict"] is not None)
    return {
        "pair": priced.pair,
        "horizon": priced.label,
        "variants": variants,
        "variants_tested": len(variants) * 2,
        "verdicts_computed": computed,
        "verdict": verdict,
        "verdict_from_variant": best_name,
        "verdict_from_route": best_route,
        "cheapest_session": cheapest,
    }


# --------------------------------------------------------------------------- #
# Section 5 -- the roll window
# --------------------------------------------------------------------------- #

def roll_window(priced: Priced) -> dict[str, Any]:
    """Cost against move inside the derived roll window (pre-reg #4 evidence).

    T4 established that the roll hour is dearer and quieter at once. This asks
    the only question that matters for execution: what does a move have to be
    worth in there, relative to everywhere else, before it pays for itself.
    """
    masks = slices_of(priced)
    if "roll" not in masks:
        return {}
    inside = describe(priced, masks["roll"])
    outside = describe(priced, masks["off_roll"])
    if not (inside.get("usable") and outside.get("usable")):
        return {}
    bar = SURVIVAL_BAR
    return {
        "inside": inside,
        "outside": outside,
        "cost_ratio": _r(inside["ladder"][bar]["cost_bp_p50"]
                         / outside["ladder"][bar]["cost_bp_p50"]
                         if outside["ladder"][bar]["cost_bp_p50"] else None, 3),
        "move_ratio": _r(inside["move_bp"]["p50"] / outside["move_bp"]["p50"]
                         if outside["move_bp"]["p50"] else None, 3),
        "move_over_cost_inside":
            inside["ladder"][bar]["median_move_over_cost"],
        "move_over_cost_outside":
            outside["ladder"][bar]["median_move_over_cost"],
        "share_above_cost_inside":
            inside["ladder"][bar]["share_of_moves_above_cost"],
        "share_above_cost_outside":
            outside["ladder"][bar]["share_of_moves_above_cost"],
    }


# --------------------------------------------------------------------------- #
# Section 6 -- the era question
# --------------------------------------------------------------------------- #

def era_rows(priced: Priced) -> dict[str, Any]:
    """Cost and move by calendar era, with R3's density control beside them.

    The band column is the control: a spread median taken over thousand-tick
    hours and one taken over six-thousand-tick hours are not the same
    instrument, so the reference-band figure is the one an era comparison may
    actually use, and the uncontrolled one is reported next to it so a reader
    can see how much of the era difference is density.
    """
    stamps = priced.series.stamps()
    years = stamps.year.to_numpy()[priced.series.ret_pos].astype("int64")
    bands = density_band(priced.series.tick_count[priced.series.ret_pos])
    out: dict[str, Any] = {}
    for name, low, high in CALENDAR_ERAS:
        mask = (years >= low) & (years <= high)
        row = describe(priced, mask)
        if not row.get("usable"):
            continue
        controlled = describe(priced, mask & (bands == R3_REFERENCE_BAND))
        composition: dict[str, Any] = {}
        for band, _lo, _hi in DENSITY_BANDS:
            share = float((bands[mask] == band).mean()) if mask.any() else 0.0
            composition[band] = _r(share, 4)
        out[name] = {
            "years": [low, min(high, int(years.max()) if years.size else high)],
            "uncontrolled": row,
            "reference_band": controlled,
            "band_composition": composition,
        }
    return out


def era_evidence(eras_by_pair: dict[str, Any], agreement: dict[str, Any],
                 horizon: str) -> list[dict[str, Any]]:
    """The pre-2013 evidence table, one row per era. Recommend, never decide.

    The T5 card asks for evidence for a checkpoint decision -- training data,
    stress test only, or excluded -- and explicitly says to recommend rather
    than decide. So this assembles the four things the decision turns on and
    stops: how expensive the era was, how much of it the cross-check could see,
    how well what it could see agreed, and whether a move in it cleared its own
    cost as often as it does now.
    """
    by_year = agreement.get("by_year", {})
    rows: list[dict[str, Any]] = []
    for name, low, high in CALENDAR_ERAS:
        costs_bp: list[float] = []
        shares: list[float] = []
        moves: list[float] = []
        spreads: list[float] = []
        for _pair, horizons in sorted(eras_by_pair.items()):
            era = (horizons.get(horizon) or {}).get(name)
            if not era:
                continue
            row = era["reference_band"]
            if not row.get("usable"):
                row = era["uncontrolled"]
            costs_bp.append(row["ladder"][SURVIVAL_BAR]["cost_bp_p50"])
            shares.append(row["ladder"][SURVIVAL_BAR]
                          ["share_of_moves_above_cost"])
            moves.append(row["move_bp"]["p50"])
            spreads.append(row["median_spread_pips"])
        years = [y for y in by_year if low <= int(y) <= high]
        sampled = sum(by_year[y]["sampled"] for y in years)
        unverifiable = sum(by_year[y]["unverifiable"] for y in years)
        verifiable = sum(by_year[y]["pass"] + by_year[y]["blocked"]
                         for y in years)
        passed = sum(by_year[y]["pass"] for y in years)
        rows.append({
            "era": name,
            "pairs_measured": len(costs_bp),
            "median_cost_bp_at_survival_bar": _r(
                float(np.median(costs_bp)) if costs_bp else None, 5),
            "median_spread_pips": _r(
                float(np.median(spreads)) if spreads else None, 4),
            "median_move_bp": _r(float(np.median(moves)) if moves else None, 4),
            "median_share_of_moves_above_cost": _r(
                float(np.median(shares)) if shares else None, 4),
            "crosscheck_hours_sampled": sampled,
            "crosscheck_unverifiable_share": _r(
                unverifiable / sampled if sampled else None, 4),
            "crosscheck_agreement_rate": _r(
                passed / verifiable if verifiable else None, 4),
        })
    return rows


# --------------------------------------------------------------------------- #
# Section 7 -- cost sensitivity
# --------------------------------------------------------------------------- #

def executable_universe(by_horizon: dict[str, dict[str, Any]],
                        horizons: Sequence[str]) -> dict[str, Any]:
    """How many horizon-by-session cells pay for themselves at each rung.

    "Executable" here means only that the median move in the cell exceeds the
    median round trip -- the arithmetic precondition for a rule that trades
    every bar, and nothing more. It is not a claim that a rule exists, and the
    report says so wherever this number appears.
    """
    cells: list[tuple[str, str]] = []
    for horizon in horizons:
        minutes = HORIZON_MINUTES.get(horizon, 10 ** 6)
        names = ([f"session:{s}" for s in SESSIONS]
                 if minutes <= SESSION_MAX_MINUTES else ["all"])
        cells += [(horizon, name) for name in names]
    per_rung: dict[str, Any] = {}
    survivors: dict[str, list[str]] = {}
    for rung in LADDER:
        kept: list[str] = []
        for horizon, name in cells:
            row = by_horizon.get(horizon, {}).get(name)
            if not row or not row.get("usable"):
                continue
            median = row["move_bp"]["p50"]
            cost = row["ladder"][rung]["cost_bp_p50"]
            if median is not None and cost is not None and median > cost:
                kept.append(f"{horizon}|{name}")
        per_rung[rung] = len(kept)
        survivors[rung] = sorted(kept)
    measured = sum(1 for horizon, name in cells
                   if (by_horizon.get(horizon, {}).get(name) or {}).get("usable"))
    return {
        "cells_measured": measured,
        "executable_by_rung": per_rung,
        "executable_cells_by_rung": survivors,
        "lost_from_1_to_2": sorted(set(survivors[LADDER[0]])
                                   - set(survivors[LADDER[-1]])),
        "share_surviving_2x": _r(
            per_rung[LADDER[-1]] / measured if measured else None, 4),
    }


# --------------------------------------------------------------------------- #
# The map
# --------------------------------------------------------------------------- #

def edge_map(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every measured cell, ranked by the dearest rung its moves still clear.

    The card's "where edge can survive" map. The name is a claim about
    *possibility*: a cell high on this list is one where a rule with a signal
    could pay for itself, and the whole of T4 says the signals are small. The
    ranking is on the median move over the median cost, which is the same
    ordering at every rung, so the list does not change shape as the ladder is
    climbed -- only its cut-off does.
    """
    ranked: list[dict[str, Any]] = []
    for row in rows:
        if row["slice"] == "roll":
            # Pre-reg #4 excludes the roll window from execution, so a cell
            # inside it is not a place edge could survive whatever its
            # arithmetic says. It gets its own section rather than a rank.
            continue
        stats_row = row["stats"]
        median = stats_row["move_bp"]["p50"]
        base = stats_row["ladder"][LADDER[0]]["cost_bp_p50"]
        if median is None or not base:
            continue
        highest = None
        for rung in LADDER:
            cost = stats_row["ladder"][rung]["cost_bp_p50"]
            if cost is not None and median > cost:
                highest = rung
        ranked.append({
            "pair": row["pair"],
            "horizon": row["horizon"],
            "slice": row["slice"],
            "n": stats_row["n"],
            "move_bp_p50": median,
            "cost_bp_p50_at_1x": base,
            "move_over_cost": _r(median / base, 4),
            "survives_to_rung": highest,
            "share_of_moves_above_cost_at_survival_bar":
                stats_row["ladder"][SURVIVAL_BAR]["share_of_moves_above_cost"],
            "median_spread_pips": stats_row["median_spread_pips"],
        })
    ranked.sort(key=lambda r: (-(r["move_over_cost"] or 0.0), r["pair"],
                               r["horizon"], r["slice"]))
    return ranked


# --------------------------------------------------------------------------- #
# The experiment
# --------------------------------------------------------------------------- #

def run(*, params: dict[str, Any], seed: int, loader: Any,
        costs: dict[str, Any] | None = None) -> dict[str, Any]:
    """The T5 experiment. Cost geometry: evidence and bounds, no scorecard.

    Args:
        params: The ``[experiment.params]`` table.
        seed: Recorded for the hash. Nothing here is random -- every figure is
            a deterministic pass over stored bars in table order and a
            deterministic call into the cost model -- and the seed is carried
            so a later card that does randomise cannot do it without one.
        loader: A :class:`~research.loader.ResearchLoader` in scoring mode.
    """
    base = pathlib.Path(loader.root).parent.parent
    pairs = [str(p) for p in params["pairs"]]
    horizons = [str(t) for t in params["timeframes"]]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    history_start = as_date(str(params["history_start"]))
    history_horizons = [str(t) for t in params["history_timeframes"]]
    roll = (int(params["roll_start_hour_ny"]), int(params["roll_end_hour_ny"]))
    units = float(params["reference_units"])
    vol_window = int(params["vol_window"])
    vr_q = int(params["vr_horizon"])
    test_set = [(str(cell["pair"]), str(cell["horizon"]))
                for cell in params["test_set"]]
    costs = dict(costs or {})
    if not {"commission_rate", "commission_min"} <= set(costs):
        raise ValueError(
            "the cost model parameters must be declared in [experiment.costs]; "
            "an undeclared cost model is not one cost model, and a defaulted "
            "one is a second set of parameters nobody wrote down")

    base_model = cost_lib.model_for(costs, 1.0)
    floor = cost_lib.floor_notional(base_model)
    classes = cc.load_classes(
        base / str(params.get("crosscheck_classes_path", cc.CLASSES_RELPATH)))
    agreement = eras_from_classes(classes)

    priced_cells: dict[str, Priced] = {}
    coverage: list[dict[str, Any]] = []
    floors: dict[str, Any] = {}
    bands: dict[str, Any] = {}
    cheapest: dict[str, Any] = {}
    moves: dict[str, dict[str, dict[str, Any]]] = {}
    rolls: dict[str, Any] = {}
    eras: dict[str, Any] = {}
    prices: dict[str, float] = {}
    floor_bars_total = 0
    map_rows: list[dict[str, Any]] = []

    session_horizon = str(params["session_timeframe"])

    for pair in pairs:
        moves[pair] = {}
        for horizon in horizons:
            series = load_series(loader, pair, horizon, start, end)
            if series is None or len(series) < stats.MIN_SAMPLE:
                coverage.append({"pair": pair, "horizon": horizon,
                                 "readable": False})
                continue
            priced = price_series(series, base_model, units, roll=roll,
                                  vol_window=vol_window,
                                  floor_notional=floor)
            priced_cells[f"{pair}|{horizon}"] = priced
            floor_bars_total += priced.floor_bars
            masks = slices_of(priced)
            described = {name: describe(priced, mask)
                         for name, mask in masks.items()}
            # Slice names keep their ``session:`` and ``tercile:`` prefixes.
            # Stripping them here would put a session and a volatility regime
            # in one namespace, and every lookup downstream would then depend
            # on no pair ever sharing a name with a tercile.
            moves[pair][horizon] = {name: row for name, row
                                    in described.items() if row.get("usable")}
            for name, row in moves[pair][horizon].items():
                map_rows.append({"pair": pair, "horizon": horizon,
                                 "slice": name, "stats": row})
            coverage.append({
                "pair": pair, "horizon": horizon, "readable": True,
                "bars": int(series.ts.size), "returns": len(series),
                "floor_binding_returns": priced.floor_bars,
                "median_cost_bp": described["all"]["ladder"]["1.0"][
                    "cost_bp_p50"],
            })
            if horizon == session_horizon:
                prices[pair] = float(np.median(series.mid_close))
                floors[pair] = cost_floor(priced)
                bands[pair] = spread_by_band(priced)
                cheapest[pair] = cheapest_band(floors[pair])
                rolls[pair] = roll_window(priced)
            _LOG.info("%s %s: %d move(s) priced", pair, horizon, len(series))

        for horizon in history_horizons:
            series = load_series(loader, pair, horizon, history_start, end)
            if series is None or len(series) < stats.MIN_SAMPLE:
                continue
            priced = price_series(series, base_model, units, roll=roll,
                                  vol_window=vol_window, floor_notional=floor)
            floor_bars_total += priced.floor_bars
            eras.setdefault(pair, {})[horizon] = era_rows(priced)

    cheapest_session = {pair: (row or {}).get("session")
                        for pair, row in cheapest.items()}
    cells = [test_set_cell(priced_cells[f"{pair}|{horizon}"],
                           cheapest_session.get(pair), vr_q)
             for pair, horizon in test_set
             if f"{pair}|{horizon}" in priced_cells]
    missing = [f"{pair}|{horizon}" for pair, horizon in test_set
               if f"{pair}|{horizon}" not in priced_cells]

    holding = {pair: minimum_holding_period(moves[pair], horizons,
                                            SURVIVAL_BAR)
               for pair in pairs if moves.get(pair)}
    sensitivity = {pair: executable_universe(moves[pair], horizons)
                   for pair in pairs if moves.get(pair)}

    return {
        "note": ("EDA battery II: cost geometry. Analytical bounds and a map "
                 "of where a rule could pay for its round trip -- never a "
                 "backtest, never a scorecard, and never a claim that an edge "
                 "exists. Pre-reg #3 puts the decisions this evidence informs "
                 "in chat, between cards."),
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "pairs": len(pairs), "horizons": horizons,
                   "history_start": history_start.isoformat(),
                   "history_horizons": history_horizons,
                   "session_timeframe": session_horizon},
        "method": {
            "reference_units": units,
            "ladder": list(LADDER),
            "survival_bar": SURVIVAL_BAR,
            "park_bar": PARK_BAR,
            "cost_model": "fxlab.costs.IBCostModel",
            "cost_parameters": costs,
            "roll_window_ny": [roll[0], roll[1]],
            "vol_window": vol_window,
            "variance_ratio_horizon": vr_q,
            "session_horizons": [h for h in horizons
                                 if HORIZON_MINUTES.get(h, 10 ** 6)
                                 <= SESSION_MAX_MINUTES],
            "r3_reference_band": R3_REFERENCE_BAND,
            "calendar_eras": [{"era": name, "from": low, "to": high}
                              for name, low, high in CALENDAR_ERAS],
            "multiplier_check": {
                key: (_r(value, 12) if isinstance(value, float) else value)
                for key, value in cost_lib.multiplier_check(
                    costs, LADDER, MULTIPLIER_GRID).items()},
            "floor_notional_quote_currency": _r(floor, 4),
            "returns_priced_below_the_floor": floor_bars_total,
        },
        "coverage": coverage,
        "cost_floor": floors,
        "cost_by_density_band": bands,
        "cheapest_band": cheapest,
        "minimum_viable_notional": minimum_viable_notional(costs, pairs,
                                                           prices),
        "moves": moves,
        "minimum_holding_period": holding,
        "test_set": {
            "declared": [{"pair": p, "horizon": h} for p, h in test_set],
            "missing": missing,
            "cells": cells,
            "counts": {verdict: sum(1 for c in cells
                                    if c["verdict"] == verdict)
                       for verdict in (SURVIVES, PARKED, CLOSED)},
        },
        "roll": rolls,
        "eras": eras,
        "era_agreement": agreement,
        "era_evidence": {horizon: era_evidence(eras, agreement, horizon)
                         for horizon in history_horizons},
        "sensitivity": sensitivity,
        "edge_map": edge_map(map_rows),
        "p0a": {
            "landed": bool(getattr(fxcosts, "USD_ACCOUNTING", False)),
            "non_usd_quoted_pairs": [p for p in pairs if p[3:] != "USD"],
            "statement": (
                "commission is floored against a quote-currency notional and "
                "cross-pair P&L is summed without conversion. Every cost in "
                "this card is a ratio of two quote-currency quantities and is "
                "therefore currency-free; the floor is the exception, and no "
                "figure here is priced at a size where it binds."),
        },
        "loader": {"mode": loader.mode,
                   "sealed_dates_served": loader.access.sealed_dates(),
                   "excluded_dates_withheld": len(loader.access.excluded),
                   "excluded_pairs": loader.access.excluded_pairs()},
        "seed": int(seed),
    }
