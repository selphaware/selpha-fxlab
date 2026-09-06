"""The T6 experiment: cross-pair structure, and what it would cost to trade.

T4 measured what the twelve pairs are, one at a time. T5 measured what they
cost. This card measures what they are **together** -- how they move with each
other, whether combinations of them are stationary when none of them is,
whether one of them moves before another, and how many independent things the
universe is actually doing -- and puts every one of those against the same
round trip T5 priced, at the reference notional SPEC2 decision D9 now fixes at
100,000 units.

Six decisions shape every number here, and each is stated rather than buried.

**The universe has twelve series and seven degrees of freedom.** Eight
currencies, and a pair's log return is its base currency's strength less its
quote's. Five of the twelve pairs are therefore *exact triangular functions* of
the others -- ``log EURGBP = log EURUSD - log GBPUSD`` is a definition, not a
finding -- and this module derives which ones from the currency design matrix
rather than listing them. Every relationship it reports carries an
``identity`` flag, because a cointegration scan that does not separate the
arithmetic from the economics will rank the arithmetic first and call it an
opportunity.

**Nothing spans a hole, and nothing is interpolated.** Series are aligned onto
a common timestamp index by intersection, and every lagged estimator works
inside the contiguous runs of that index (:mod:`research.crossstats`).

**Discovery is 2015-2019 and confirmation is untouched.** The scan and its
false-discovery correction run in the discovery window. 2020-2025 and
2009-2012 are confirmations of a set that was fixed before they were looked
at. The full 2015-2025 window is reported for context and is not a third
independent test, because it contains both halves.

**The early window runs on eleven pairs.** Ruling R1 excludes ``AUDUSD`` before
2011-01-01, and R1 says in terms that a cross-pair analysis spanning that
window runs on eleven pairs and says so. Relationships involving ``AUDUSD``
therefore have no early confirmation available and are recorded as such rather
than as having failed one.

**The p-values for the cointegration statistics are simulated, not tabulated**,
from independent random walks through the same code path, with the
experiment's own seed. The simulation is checked against MacKinnon's published
asymptotic values in ``tests2/test_crossstats.py``.

**Every cost comes out of the Phase 1 cost model**, through
:mod:`research.costs` and :func:`research.cost_geometry.price_series`, at
decision D9's reference notional. A two-leg relationship pays two round trips:
one for each leg, the second scaled by the hedge ratio, both in basis points of
the first leg's notional so the comparison stays currency-free everywhere the
per-order floor does not bind -- and where it does bind, the pair is named.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any, Final, Sequence

import numpy as np

from research import costs as cost_lib
from research import crossstats as cs
from research import stats
from research.character import (MAX_DAILY_GAP_DAYS, STABILITY_LABELS,
                                STEP_NS, Register, Series, load_series)
from research.cost_geometry import floored_mask, price_series
from research.experiment import LADDER, SURVIVAL_BAR
from research.exclusions import exclusion_for
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.cross_pair")

#: Basis points per unit.
BP: Final[float] = 1e4

#: Volatility regimes, in the order a table reads best.
TERCILES: Final[tuple[str, ...]] = ("low", "mid", "high")

#: Windows every scan is run in. ``primary`` contains both ``discovery`` and
#: ``confirmation`` and is therefore context rather than a third test.
W_PRIMARY: Final[str] = "primary"
W_DISCOVERY: Final[str] = "discovery"
W_CONFIRM: Final[str] = "confirmation"
W_EARLY: Final[str] = "early"

#: Out-of-window verdicts. ``NO_WINDOW`` is not a failure: it is ruling R1
#: costing a relationship the early era, and it is reported as its own state so
#: that a table cannot quietly count it as a rejection.
CONFIRMED: Final[str] = "CONFIRMED"
NOT_CONFIRMED: Final[str] = "NOT_CONFIRMED"
NO_WINDOW: Final[str] = "NO_WINDOW"

#: Q-values of the variance ratio the currency factors are tested at, matching
#: T4's fingerprint so the factor series and the pairs are compared on the same
#: instrument.
VR_HORIZONS: Final[tuple[int, ...]] = (2, 4, 8, 16, 32)

#: The rung the variance-ratio fingerprint's headline is taken at, and the one
#: T4's false-discovery correction was computed on.
VR_HEADLINE: Final[int] = 4


def _r(value: Any, places: int) -> Any:
    """Round for the hash, passing ``None`` and non-finite values through.

    The result document is hashed and must reproduce exactly, so a float
    carrying its last two bits from the order a sum happened in is a float
    that will eventually fail the gate for no reason a message can name.
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


def _alias_step(timeframe: str) -> tuple[int, int | None]:
    """One bar in nanoseconds, and the largest gap still counted as adjacent."""
    from fxlab.ingestion.bars import offset_alias

    alias = offset_alias(timeframe)
    step = STEP_NS[alias]
    max_gap = MAX_DAILY_GAP_DAYS * STEP_NS["1D"] if alias == "1D" else None
    return step, max_gap


def _ns(date: dt.date) -> int:
    """A UTC date as epoch nanoseconds at midnight."""
    return int(np.datetime64(date.isoformat(), "ns").astype("int64"))


def _day(stamp: Any) -> str:
    """One epoch-nanosecond stamp as a ``YYYY-MM-DD`` string.

    ``np.datetime64`` refuses an ``int64`` scalar with a unit, so the cast to
    a plain ``int`` is load-bearing rather than tidying.
    """
    return str(np.datetime64(int(stamp), "ns").astype("datetime64[D]"))


# --------------------------------------------------------------------------- #
# The triangular structure of the universe
# --------------------------------------------------------------------------- #

def currencies_of(pairs: Sequence[str]) -> list[str]:
    """Every currency appearing in the universe, in first-seen order."""
    out: list[str] = []
    for pair in pairs:
        for code in (pair[:3], pair[3:]):
            if code not in out:
                out.append(code)
    return out


def identity_of(pairs: Sequence[str],
                currencies: Sequence[str]) -> dict[str, Any]:
    """Whether a set of pairs is linearly dependent by construction.

    Derived from the currency design matrix rather than from a list somebody
    typed: a set of pairs whose rows are linearly dependent has a combination
    whose log price is identically constant, which is a cross-rate definition
    and not a relationship anyone discovered.

    The combination is the **left** null space of the design -- a weighting of
    the pairs, not of the currencies -- so ``[1, -1, -1]`` on
    ``EURGBP, EURUSD, GBPUSD`` reads as the identity it is. It is reported so a
    reader can check the claim by eye rather than take it.
    """
    design = cs.currency_design(pairs, currencies)
    rank = int(np.linalg.matrix_rank(design, tol=1e-10))
    if rank >= len(pairs):
        return {"identity": False, "rank": rank, "size": len(pairs),
                "combination": None}
    left, _s, _vt = np.linalg.svd(design, full_matrices=True)
    vector = left[:, rank]
    scale = max(abs(float(v)) for v in vector) or 1.0
    if float(vector[0]) < 0.0:
        scale = -scale
    combination = [_r(float(v) / scale, 4) for v in vector]
    return {"identity": True, "rank": rank, "size": len(pairs),
            "combination": combination}


def identity_summary(universe: Sequence[str],
                     currencies: Sequence[str]) -> dict[str, Any]:
    """How much of the universe is arithmetic, stated once at the top.

    Twelve pairs across eight currencies span at most seven dimensions, so at
    least five of them are exact functions of the rest. Which five is not
    unique -- any spanning set of seven will do -- so this reports the rank,
    the shortfall, and one explicit spanning set rather than pretending there
    is a canonical answer.
    """
    design = cs.currency_design(universe, currencies)
    rank = int(np.linalg.matrix_rank(design, tol=1e-10))
    spanning: list[str] = []
    chosen: list[int] = []
    for index, pair in enumerate(universe):
        trial = chosen + [index]
        if np.linalg.matrix_rank(design[trial], tol=1e-10) == len(trial):
            chosen = trial
            spanning.append(pair)
    dependent = [p for p in universe if p not in spanning]
    return {
        "pairs": len(universe),
        "currencies": len(currencies),
        "rank": rank,
        "degrees_of_freedom": rank,
        "dependent_pairs": len(universe) - rank,
        "one_spanning_set": spanning,
        "pairs_it_determines": dependent,
        "note": ("a pair's log return is its base currency's strength less "
                 "its quote's, so the design matrix has rank one less than "
                 "the number of currencies. Every pair beyond that rank is an "
                 "exact triangular function of the others -- a definition "
                 "rather than a relationship."),
    }


# --------------------------------------------------------------------------- #
# Loading, and the cost of each pair at each horizon
# --------------------------------------------------------------------------- #

def load_all(loader: Any, pairs: Sequence[str], timeframe: str,
             start: dt.date, end: dt.date) -> dict[str, Series]:
    """Every readable pair at one timeframe, through the loader."""
    out: dict[str, Series] = {}
    for pair in pairs:
        series = load_series(loader, pair, timeframe, start, end)
        if series is None or len(series) < stats.MIN_SAMPLE:
            _LOG.warning("%s %s: unreadable over %s..%s", pair, timeframe,
                         start, end)
            continue
        out[pair] = series
    return out


def cost_table(series_by_pair: dict[str, Series], costs: dict[str, Any],
               units: float, roll: tuple[int, int], vol_window: int,
               windows: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """The median round trip of each pair, per window, at every rung.

    Priced by :func:`research.cost_geometry.price_series`, which is the same
    function T5's tables come out of, so a cost quoted here and a cost quoted
    there are the same measurement. Each series is priced once and then sliced
    by window rather than re-priced per window: the cost of a move does not
    depend on which window a reader puts it in.

    The floor-binding share travels with every row because at decision D9's
    notional the per-order minimum is inside the arithmetic for part of the
    universe, and a cost quoted at this size without it is a cost whose
    currency nobody stated.
    """
    model = cost_lib.model_for(costs, 1.0)
    floor = cost_lib.floor_notional(model)
    out: dict[str, Any] = {name: {} for name in windows}
    for pair, series in series_by_pair.items():
        priced = price_series(series, model, units, roll=roll,
                              vol_window=vol_window, floor_notional=floor)
        floored = floored_mask(priced, model, units)
        stamps = series.ts[series.ret_pos]
        for name, (low, high) in windows.items():
            keep = (stamps >= low) & (stamps <= high)
            if int(keep.sum()) < stats.MIN_SAMPLE:
                continue
            cost = priced.cost_bp[keep]
            out[name][pair] = {
                "n": int(keep.sum()),
                "cost_bp": {rung: _r(float(np.median(cost * float(rung))), 5)
                            for rung in LADDER},
                "median_move_bp": _r(float(np.median(priced.abs_bp[keep])), 4),
                "median_spread_pips": _r(float(np.median(
                    series.spread_pips[series.ret_pos[keep]])), 4),
                "floor_binding_share": _r(float(floored[keep].mean()), 6),
            }
        del priced, floored
    return out


# --------------------------------------------------------------------------- #
# Section 1 -- correlation structure
# --------------------------------------------------------------------------- #

def universe_terciles(block: cs.Aligned, vol_window: int) -> np.ndarray:
    """A universe-level volatility regime per row, conditioned not fitted.

    The cross-sectional mean of each pair's trailing volatility, bucketed into
    terciles. Trailing rather than contemporaneous for the same reason T4 and
    T5 use trailing: a row bucketed by a volatility estimate containing it
    would be in the high bucket by construction.
    """
    trailing = np.column_stack([
        stats.trailing_volatility(block.returns[:, column], vol_window)
        for column in range(block.returns.shape[1])])
    # The first ``vol_window`` rows have no trailing estimate for any pair, so
    # the row mean is a mean of nothing. Counting the finite entries first
    # says so explicitly rather than leaving numpy to warn about it.
    finite_count = np.isfinite(trailing).sum(axis=1)
    totals = np.where(np.isfinite(trailing), trailing, 0.0).sum(axis=1)
    average = np.divide(totals, finite_count,
                        out=np.full(totals.shape, np.nan),
                        where=finite_count > 0)
    labels = np.full(average.size, "", dtype=object)
    finite = np.isfinite(average)
    edges = stats.tercile_edges(average[finite])
    if edges is None:
        return labels
    low, high = edges
    labels[finite & (average <= low)] = "low"
    labels[finite & (average > low) & (average <= high)] = "mid"
    labels[finite & (average > high)] = "high"
    return labels


def correlation_section(block: cs.Aligned, register: Register, horizon: str,
                        *, vol_window: int, cluster_threshold: float,
                        split_ns: int) -> dict[str, Any]:
    """Pairwise correlation, its stability, its regimes and its network."""
    pairs = block.pairs
    matrix = cs.correlation_matrix(block.returns)
    tests: list[dict[str, Any]] = []
    for i, first in enumerate(pairs):
        for j in range(i + 1, len(pairs)):
            second = pairs[j]
            key = f"{first}|{second}|{horizon}"
            result = cs.correlation_test(block.returns[:, i],
                                         block.returns[:, j])
            register.add(f"correlation@{horizon}", key, result)
            tests.append({"a": first, "b": second, "rho": _r(result["rho"], 5),
                          "n": result["n"],
                          "p_value": _r(result["p_value"], 12)})

    early = block.stamps < split_ns
    late = ~early
    halves: list[dict[str, Any]] = []
    if int(early.sum()) >= stats.MIN_SAMPLE and int(late.sum()) >= stats.MIN_SAMPLE:
        first_matrix = cs.correlation_matrix(block.returns[early])
        second_matrix = cs.correlation_matrix(block.returns[late])
        for i, first in enumerate(pairs):
            for j in range(i + 1, len(pairs)):
                halves.append({
                    "a": first, "b": pairs[j],
                    "first_half": _r(float(first_matrix[i, j]), 5),
                    "second_half": _r(float(second_matrix[i, j]), 5),
                    "sign_held": bool(
                        np.sign(first_matrix[i, j])
                        == np.sign(second_matrix[i, j])),
                    "shift": _r(float(second_matrix[i, j]
                                      - first_matrix[i, j]), 5)})

    labels = universe_terciles(block, vol_window)
    regimes: dict[str, Any] = {}
    for name in TERCILES:
        mask = labels == name
        if int(mask.sum()) < stats.MIN_SAMPLE:
            continue
        regime_matrix = cs.correlation_matrix(block.returns[mask])
        regimes[name] = {
            "n": int(mask.sum()),
            "mean_abs_rho": _r(_offdiag_mean(np.abs(regime_matrix)), 5),
            "mean_rho": _r(_offdiag_mean(regime_matrix), 5),
            "max_abs_rho": _r(float(np.nanmax(np.abs(
                regime_matrix - np.eye(len(pairs))))), 5),
            "geometry": _round_geometry(cs.effective_bets(regime_matrix)),
            # The card asks how the network changes in a high-volatility
            # regime, which is a question about the clusters and not only
            # about how many independent directions they leave.
            "clusters": cs.average_linkage(
                cs.correlation_distance(regime_matrix), pairs,
                cluster_threshold),
        }

    distance = cs.correlation_distance(matrix)
    clusters = cs.average_linkage(distance, pairs, cluster_threshold)
    return {
        "pairs": list(pairs),
        "n": len(block),
        "adjacent_share": _r(block.adjacent_share(), 6),
        "matrix": [[_r(float(value), 5) for value in row] for row in matrix],
        "tests": tests,
        "mean_abs_rho": _r(_offdiag_mean(np.abs(matrix)), 5),
        "mean_rho": _r(_offdiag_mean(matrix), 5),
        "split_half": halves,
        "by_regime": regimes,
        "clusters": clusters,
        "cluster_threshold": cluster_threshold,
        "geometry": _round_geometry(cs.effective_bets(matrix)),
    }


def _offdiag_mean(matrix: np.ndarray) -> float | None:
    """Mean of the off-diagonal entries, or ``None`` for a 1x1 matrix."""
    n = matrix.shape[0]
    if n < 2:
        return None
    mask = ~np.eye(n, dtype=bool)
    values = matrix[mask]
    values = values[np.isfinite(values)]
    return float(values.mean()) if values.size else None


def _round_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    """Round a geometry block for the hash, keeping the leading eigenvalues."""
    return {
        "n": geometry["n"],
        "participation_ratio": _r(geometry["participation_ratio"], 5),
        "entropy_bets": _r(geometry["entropy_bets"], 5),
        "components_for_90pct": geometry["components_for_90pct"],
        "eigenvalues": [_r(v, 6) for v in geometry["eigenvalues"]],
        "variance_explained": [_r(v, 6)
                               for v in geometry["variance_explained"]],
    }


def rolling_windows(block: cs.Aligned, *, window_years: int,
                    step_months: int) -> list[tuple[dt.date, dt.date]]:
    """Rolling window edges, two years stepped six months, as T4 used.

    Consecutive windows share three quarters of their data, so a property has
    to hold for two years at a time everywhere in the decade rather than on
    average across it.
    """
    if not len(block):
        return []
    start = dt.date.fromisoformat(_day(block.stamps[0]))
    end = dt.date.fromisoformat(_day(block.stamps[-1]))
    out: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while True:
        stop = _add_months(cursor, window_years * 12)
        if stop > end:
            break
        out.append((cursor, stop))
        cursor = _add_months(cursor, step_months)
    return out


def rolling_geometry(block: cs.Aligned, *, window_years: int,
                     step_months: int) -> list[dict[str, Any]]:
    """Effective bets and mean correlation on rolling windows.

    Two years stepped six months, as T4: consecutive windows share three
    quarters of their data, so a property has to hold for two years at a time
    everywhere in the decade rather than on average across it.
    """
    rows: list[dict[str, Any]] = []
    for first, last in rolling_windows(block, window_years=window_years,
                                       step_months=step_months):
        low, high = _ns(first), _ns(last)
        mask = (block.stamps >= low) & (block.stamps < high)
        if int(mask.sum()) < stats.MIN_SAMPLE:
            continue
        matrix = cs.correlation_matrix(block.returns[mask])
        geometry = cs.effective_bets(matrix)
        rows.append({
            "from": first.isoformat(), "to": last.isoformat(),
            "n": int(mask.sum()),
            "mean_abs_rho": _r(_offdiag_mean(np.abs(matrix)), 5),
            "participation_ratio": _r(geometry["participation_ratio"], 5),
            "entropy_bets": _r(geometry["entropy_bets"], 5),
            "components_for_90pct": geometry["components_for_90pct"]})
    return rows


def _add_months(date: dt.date, months: int) -> dt.date:
    """Calendar-month arithmetic, clamped to the end of a short month."""
    month = date.month - 1 + months
    year = date.year + month // 12
    month = month % 12 + 1
    day = min(date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0
                                                      or year % 400 == 0)
                         else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30,
                         31][month - 1])
    return dt.date(year, month, day)


# --------------------------------------------------------------------------- #
# Section 2 -- currency strength
# --------------------------------------------------------------------------- #

def memory_of(values: np.ndarray, spans: Sequence[tuple[int, int]],
              register: Register, family_prefix: str, key: str
              ) -> dict[str, Any]:
    """T4's memory tests on one series: lag-1 autocorrelation and the VR ladder.

    The same estimators T4 used, registered in their own families so the
    Benjamini-Hochberg correction runs over the factor series rather than over
    a family it was not part of.
    """
    rho = stats.autocorr_at(values, spans, 1)
    register.add(f"{family_prefix}_autocorr", key, rho)
    profile: dict[str, Any] = {}
    for q in VR_HORIZONS:
        result = stats.variance_ratio_segments(values, spans, q)
        register.add(f"{family_prefix}_variance_ratio", f"{key}|q={q}", result)
        profile[str(q)] = {"vr": _r(result["vr"], 6), "z": _r(result["z"], 4),
                           "p_value": _r(result["p_value"], 12),
                           "n": result["n"]}
    return {
        "n": int(values.size),
        "sd_bp": _r(float(values.std(ddof=1)) * BP
                    if values.size > 1 else None, 4),
        "rho1": _r(rho["rho"], 6),
        "rho1_z": _r(rho["z"], 4),
        "rho1_p": _r(rho["p_value"], 12),
        "variance_ratio": profile,
        "vr_headline": profile.get(str(VR_HEADLINE), {}).get("vr"),
    }


def currency_appearances(pairs: Sequence[str],
                         currencies: Sequence[str]) -> dict[str, int]:
    """How many pairs each currency appears in.

    Load-bearing for reading the decomposition: a currency appearing in only
    one pair adds one unknown and one equation, so its strength is exactly
    determined and that pair's residual is zero by construction. Its "factor"
    is that pair's return relabelled, not a factor the universe agreed on.
    """
    counts = {code: 0 for code in currencies}
    for pair in pairs:
        counts[pair[:3]] += 1
        counts[pair[3:]] += 1
    return counts


def currency_section(block: cs.Aligned, currencies: Sequence[str],
                     register: Register, horizon: str, *,
                     split_ns: int) -> dict[str, Any]:
    """Factor the universe into currency strengths and test what they carry."""
    design = cs.currency_design(block.pairs, currencies)
    appearances = currency_appearances(block.pairs, currencies)
    fit = cs.currency_strength(block.returns, design)
    strengths = fit["strengths"]
    residual = fit["residual"]

    per_pair: list[dict[str, Any]] = []
    for column, pair in enumerate(block.pairs):
        own = block.returns[:, column]
        per_pair.append({
            "pair": pair,
            "r_squared": _r(fit["r_squared"][column], 6),
            "sd_bp": _r(float(own.std(ddof=1)) * BP, 4),
            "residual_sd_bp": _r(float(residual[:, column].std(ddof=1)) * BP,
                                 4),
            "residual_share_of_sd": _r(
                float(residual[:, column].std(ddof=1) / own.std(ddof=1))
                if own.std(ddof=1) > 0 else None, 6)})

    factors: list[dict[str, Any]] = []
    for index, code in enumerate(currencies):
        series = strengths[:, index]
        row = memory_of(series, block.spans, register, "currency_factor",
                        f"{code}|{horizon}")
        row["currency"] = code
        row["pairs_it_appears_in"] = appearances[code]
        row["exactly_determined"] = bool(appearances[code] == 1)
        row["share_of_universe_variance"] = _r(
            float(series.var(ddof=1)
                  / strengths.var(ddof=1, axis=0).sum()), 6)
        factors.append(row)

    reference: list[dict[str, Any]] = []
    for column, pair in enumerate(block.pairs):
        row = memory_of(block.returns[:, column], block.spans, register,
                        "pair_reference", f"{pair}|{horizon}")
        row["pair"] = pair
        reference.append(row)

    early = block.stamps < split_ns
    late = ~early
    stability: list[dict[str, Any]] = []
    if int(early.sum()) >= stats.MIN_SAMPLE and int(late.sum()) >= stats.MIN_SAMPLE:
        for index, code in enumerate(currencies):
            first = _split_memory(strengths[early, index])
            second = _split_memory(strengths[late, index])
            stability.append({
                "currency": code,
                "first_half_rho1": _r(first, 6),
                "second_half_rho1": _r(second, 6),
                "sign_held": bool(first is not None and second is not None
                                  and np.sign(first) == np.sign(second))})

    return {
        "currencies": list(currencies),
        "design_rank": int(np.linalg.matrix_rank(design, tol=1e-10)),
        "appearances": appearances,
        "single_pair_currencies": sorted(code for code, count
                                         in appearances.items() if count == 1),
        "by_pair": per_pair,
        "factors": factors,
        "pair_reference": reference,
        "stability": stability,
        "mean_r_squared": _r(float(np.mean([r["r_squared"] for r in per_pair
                                            if r["r_squared"] is not None])),
                             6),
    }


def _split_memory(values: np.ndarray) -> float | None:
    """Lag-1 autocorrelation of a contiguous half, or ``None`` if too short."""
    if values.size < stats.MIN_SAMPLE:
        return None
    return stats.autocorr_at(values, [(0, values.size)], 1)["rho"]


# --------------------------------------------------------------------------- #
# Section 3 -- cointegration
# --------------------------------------------------------------------------- #

def relationship_key(members: Sequence[str], horizon: str) -> str:
    """A stable name for one relationship at one horizon."""
    return f"{'+'.join(members)}@{horizon}"


def cointegration_of(block: cs.Aligned, members: Sequence[str], *,
                     adf_lags: int, vecm_lags: int, null: dict[str, Any],
                     with_johansen: bool = True) -> dict[str, Any] | None:
    """Engle-Granger and Johansen on one set of pairs in one window.

    The Engle-Granger regression puts the first member on the left. That is a
    choice and it matters -- the test is not symmetric -- so the pairs-of-pairs
    scan runs both orderings and reports both, and a triple runs the ordering
    its declaration gives it.

    Johansen **is** symmetric in its members, so it is computed once per
    unordered set and ``with_johansen`` is how the reversed ordering says it
    already has the answer. Running it twice would double the most expensive
    regression in the card to produce the same number.
    """
    columns = [block.column(p) for p in members]
    levels = block.log_price[:, columns]
    if levels.shape[0] < stats.MIN_SAMPLE:
        return None
    fit = cs.engle_granger(levels[:, 0], levels[:, 1:], block.spans, adf_lags)
    width = len(members)
    eg_p = cs.empirical_p(fit["tau"],
                          (null.get("engle_granger") or {}).get(str(width)),
                          "left")
    result = (cs.johansen(levels, block.spans, vecm_lags) if with_johansen
              else {})
    trace = (result.get("trace") or [None])[0]
    max_eigen = (result.get("max_eigen") or [None])[0]
    trace_p = cs.empirical_p(trace, (null.get("trace") or {}).get(str(width)),
                             "right")
    max_p = cs.empirical_p(max_eigen,
                           (null.get("max_eigen") or {}).get(str(width)),
                           "right")
    return {
        "members": list(members),
        "n": int(len(block)),
        "adf_n": fit["adf_n"],
        "beta": [_r(b, 6) for b in fit["beta"]],
        "intercept": _r(fit["intercept"], 6),
        "r_squared": _r(fit["r_squared"], 6),
        "tau": _r(fit["tau"], 5),
        "eg_p_value": _r(eg_p["p_value"], 12),
        "eg_p_on_floor": eg_p[cs.P_FLOOR_KEY],
        "residual_sd_bp": _r(fit["residual_sd_bp"], 4),
        "residual_step_bp": _r(fit["residual_step_bp"], 4),
        "half_life_bars": _r(fit["half_life_bars"], 3),
        "half_life_t_stat": _r(fit["half_life_t_stat"], 4),
        "trace": _r(trace, 5),
        "trace_p_value": _r(trace_p["p_value"], 12),
        "trace_p_on_floor": trace_p[cs.P_FLOOR_KEY],
        "max_eigen": _r(max_eigen, 5),
        "max_eigen_p_value": _r(max_p["p_value"], 12),
        "trace_next_rank": _r((result.get("trace") or [None, None])[1]
                              if result.get("trace")
                              and len(result["trace"]) > 1 else None, 5),
        "levels_rank": result.get("levels_rank"),
        "levels_condition": _r(result.get("levels_condition"), 2),
        "johansen_vector": [_r(v, 6) for v in (result.get("vector") or [])],
    }


def two_leg_cost(members: Sequence[str], beta: Sequence[float],
                 costs_by_pair: dict[str, Any]) -> dict[str, Any] | None:
    """What one round trip of a whole relationship costs, at every rung.

    A relationship holds one unit of notional in its first member against
    ``beta`` units in each of the others, because the residual whose reversion
    it trades is ``r_0 - sum(beta_i r_i)``. So its round trip is the first
    leg's plus each other leg's scaled by that leg's weight, all in basis
    points of the **first leg's** notional -- which keeps the sum dimensionless
    and therefore currency-free, with the single exception of the per-order
    floor, whose binding share is reported per leg.

    The convention has one edge the report states: each leg's cost is measured
    at 100,000 units of *its own* base currency rather than at the value the
    hedge ratio implies. Above the floor a cost in basis points does not depend
    on size at all, so the two agree; where the floor binds, they differ by the
    floor term, and those legs are named.
    """
    first = costs_by_pair.get(members[0])
    if first is None:
        return None
    weights = [1.0] + [abs(float(b)) for b in beta]
    total = {rung: 0.0 for rung in LADDER}
    legs: list[dict[str, Any]] = []
    for member, weight in zip(members, weights):
        row = costs_by_pair.get(member)
        if row is None:
            return None
        for rung in LADDER:
            total[rung] += weight * float(row["cost_bp"][rung])
        legs.append({"pair": member, "weight": _r(weight, 6),
                     "cost_bp_at_survival_bar": row["cost_bp"][SURVIVAL_BAR],
                     "floor_binding_share": row["floor_binding_share"]})
    return {
        "legs": legs,
        "cost_bp": {rung: _r(total[rung], 5) for rung in LADDER},
        "legs_with_a_binding_floor": [leg["pair"] for leg in legs
                                      if (leg["floor_binding_share"] or 0.0)
                                      > 0.0],
    }


def cost_verdict(residual_sd_bp: float | None, step_bp: float | None,
                 cost: dict[str, Any] | None) -> dict[str, Any]:
    """Can the relationship's own move pay for both legs' round trips?

    Two ratios, because a cointegration relationship can be traded two ways
    and they have completely different arithmetic:

    * **amplitude** -- entering one standard deviation from the mean and
      exiting at the mean earns about ``residual_sd_bp`` once, for one round
      trip of every leg. This is what a pairs trade is, and it is the ratio
      the ranked table uses;
    * **per bar** -- the median absolute one-bar move of the spread, which is
      what a rule trading every bar would have to work with.

    ``break_even_entry_sd`` is the entry threshold, in standard deviations, at
    which a full reversion to the mean exactly pays the round trip. Below one
    the relationship pays at a one-sigma entry; far above it, no entry
    threshold a real spread ever reaches would.
    """
    if cost is None or residual_sd_bp is None:
        return {"pays_at_survival_bar": None}
    bar_cost = cost["cost_bp"][SURVIVAL_BAR]
    if not bar_cost:
        return {"pays_at_survival_bar": None}
    return {
        "cost_bp": cost["cost_bp"],
        "amplitude_over_cost": {
            rung: _r(residual_sd_bp / cost["cost_bp"][rung]
                     if cost["cost_bp"][rung] else None, 4)
            for rung in LADDER},
        "per_bar_over_cost": _r(step_bp / bar_cost
                                if step_bp is not None else None, 4),
        "break_even_entry_sd": _r(bar_cost / residual_sd_bp, 4),
        "pays_at_survival_bar": bool(residual_sd_bp > bar_cost),
        "dearest_rung_it_pays": _dearest_rung(residual_sd_bp, cost["cost_bp"]),
    }


def _dearest_rung(edge: float, cost: dict[str, Any]) -> str | None:
    """The dearest ladder rung the edge still clears, or ``None``."""
    best = None
    for rung in LADDER:
        value = cost.get(rung)
        if value is not None and edge > value:
            best = rung
    return best


def confirm(discovery: dict[str, Any], other: dict[str, Any] | None, *,
            alpha: float, beta_tolerance: float) -> dict[str, Any]:
    """Whether a relationship found in discovery holds in another window.

    The rule, stated here so a checkpoint can disagree with the rule rather
    than with a paragraph: a relationship confirms when its Engle-Granger
    residual rejects the unit root at ``alpha`` in that window **and** its
    hedge ratio keeps its sign and stays within a factor of ``beta_tolerance``
    of the discovery value. It thresholds nothing SPEC2 thresholds -- pre-reg
    #1 pins exactly one bar, the 1.5x survival bar, and this adds no second
    one; it is a reading rule for a stability table.
    """
    if other is None:
        return {"verdict": NO_WINDOW, "reason": "window unavailable"}
    p = other.get("eg_p_value")
    if p is None:
        return {"verdict": NO_WINDOW, "reason": "statistic not computable"}
    rejects = bool(p < alpha)
    kept = True
    ratios: list[float | None] = []
    for before, after in zip(discovery.get("beta") or [],
                             other.get("beta") or []):
        if before is None or after is None or before == 0.0:
            kept = False
            ratios.append(None)
            continue
        ratio = float(after) / float(before)
        ratios.append(_r(ratio, 4))
        if ratio <= 0.0 or ratio > beta_tolerance or ratio < 1.0 / beta_tolerance:
            kept = False
    verdict = CONFIRMED if (rejects and kept) else NOT_CONFIRMED
    return {
        "verdict": verdict,
        "p_value": _r(p, 12),
        "rejects": rejects,
        "beta_ratio": ratios,
        "beta_held": kept,
        "half_life_bars": other.get("half_life_bars"),
        "residual_sd_bp": other.get("residual_sd_bp"),
        "n": other.get("n"),
    }


# --------------------------------------------------------------------------- #
# Section 4 -- lead-lag
# --------------------------------------------------------------------------- #

def leadlag_section(block: cs.Aligned, register: Register, horizon: str, *,
                    max_lag: int, family: str,
                    costs_by_pair: dict[str, Any]) -> dict[str, Any]:
    """Every ordered pair at every lag, with the edge it implies in bp.

    The effect size is T5's lag-1 measure applied across pairs: a rule
    forecasting the lagging pair from the leading one has a forecast whose
    standard deviation is ``|rho| x sd`` of the lagging pair, and trading its
    sign earns that per trade before costs. It is compared against the
    **lagging** pair's own round trip, because that is the pair the rule would
    actually trade.
    """
    pairs = block.pairs
    rows: list[dict[str, Any]] = []
    sd_bp = {pair: float(block.returns[:, i].std(ddof=1)) * BP
             for i, pair in enumerate(pairs)}
    for i, lead in enumerate(pairs):
        for j, lagging in enumerate(pairs):
            if i == j:
                continue
            for lag in range(1, max_lag + 1):
                result = cs.lead_lag(block.returns[:, i], block.returns[:, j],
                                     block.spans, lag)
                key = f"{lead}->{lagging}|lag={lag}|{horizon}"
                register.add(family, key, result)
                rho = result["rho"]
                if rho is None:
                    continue
                edge = abs(rho) * sd_bp[lagging]
                cost = (costs_by_pair.get(lagging) or {}).get("cost_bp")
                rows.append({
                    "lead": lead, "lagging": lagging, "lag": lag,
                    "horizon": horizon,
                    "rho": _r(rho, 6), "n": result["n"],
                    "p_value": _r(result["p_value"], 12),
                    "sd_bp": _r(sd_bp[lagging], 4),
                    "edge_bp": _r(edge, 5),
                    "cost_bp_at_survival_bar": (cost or {}).get(SURVIVAL_BAR),
                    "edge_over_cost": _r(
                        edge / cost[SURVIVAL_BAR]
                        if cost and cost.get(SURVIVAL_BAR) else None, 5),
                    "pays_at_survival_bar": bool(
                        cost and cost.get(SURVIVAL_BAR)
                        and edge > cost[SURVIVAL_BAR]),
                })
    return {"horizon": horizon, "family": family, "tests": len(rows),
            "rows": rows,
            "sd_bp": {pair: _r(value, 4) for pair, value in sd_bp.items()}}


def leadlag_stability(block: cs.Aligned, rows: Sequence[dict[str, Any]], *,
                      halves: Sequence[cs.Aligned], window_years: int,
                      step_months: int, step_ns: int,
                      max_gap_ns: int | None,
                      limit: int) -> list[dict[str, Any]]:
    """Split-half and rolling sign stability for the leads worth chasing.

    Only the strongest are followed: a rolling test on every one of several
    thousand lead-lag cells would be several hundred thousand correlations to
    answer a question about the handful that survived correction.

    Each half and each rolling window is a re-spanned block rather than a
    masked one, so a lag pair inside a window never straddles the hole the
    window's own edge just created.
    """
    ordered = sorted(rows, key=lambda r: -(abs(r["rho"] or 0.0)))[:limit]
    out: list[dict[str, Any]] = []
    for row in ordered:
        i = block.column(row["lead"])
        j = block.column(row["lagging"])
        split: list[float | None] = []
        for part in halves:
            if len(part) < stats.MIN_SAMPLE:
                split.append(None)
                continue
            split.append(cs.lead_lag(part.returns[:, i], part.returns[:, j],
                                     part.spans, row["lag"])["rho"])
        rolling = _rolling_leadlag(block, i, j, row["lag"],
                                   window_years=window_years,
                                   step_months=step_months, step_ns=step_ns,
                                   max_gap_ns=max_gap_ns)
        signs = [np.sign(value) for value in rolling if value is not None]
        agreement = (float(np.mean([s == np.sign(row["rho"]) for s in signs]))
                     if signs else None)
        out.append({
            "lead": row["lead"], "lagging": row["lagging"], "lag": row["lag"],
            "horizon": row["horizon"], "rho": row["rho"],
            "first_half": _r(split[0], 6), "second_half": _r(split[1], 6),
            "sign_held": bool(split[0] is not None and split[1] is not None
                              and np.sign(split[0]) == np.sign(split[1])),
            "rolling_windows": len(rolling),
            "rolling_sign_agreement": _r(agreement, 4),
            "rolling_min": _r(min((v for v in rolling if v is not None),
                                  default=None), 6),
            "rolling_max": _r(max((v for v in rolling if v is not None),
                                  default=None), 6)})
    return out


def _rolling_leadlag(block: cs.Aligned, i: int, j: int, lag: int, *,
                     window_years: int, step_months: int, step_ns: int,
                     max_gap_ns: int | None) -> list[float | None]:
    """One lead-lag correlation per rolling window, each re-spanned."""
    out: list[float | None] = []
    for first, last in rolling_windows(block, window_years=window_years,
                                       step_months=step_months):
        part = block.window(_ns(first), _ns(last) - 1, step_ns, max_gap_ns)
        if len(part) < stats.MIN_SAMPLE:
            continue
        out.append(cs.lead_lag(part.returns[:, i], part.returns[:, j],
                               part.spans, lag)["rho"])
    return out


# --------------------------------------------------------------------------- #
# The shock window
# --------------------------------------------------------------------------- #
#
# T4 reported it in terms: `EURCHF` and `USDCHF` carry the 2015 SNB de-peg
# inside the primary window -- a 15% five-minute move, 403 standard deviations
# -- and "every statistic for those two pairs in the first half of the split is
# that afternoon". A cross-pair card whose strongest lead-lag cells are
# `EURCHF` and `USDCHF` therefore owes the reader the same statistic with that
# afternoon removed, and owes it as a measurement rather than as a caveat.
#
# The dates are declared in the config, before any result exists, with the
# event that motivates them. Removing them is not a correction to the data: the
# de-peg happened and the prices are real. It is a robustness check, and both
# figures are reported side by side so a reader can see which claims survive it.

def shock_mask(block: cs.Aligned, shock_days: Sequence[str]) -> np.ndarray:
    """Rows outside the declared shock days."""
    if not shock_days:
        return np.ones(len(block), dtype=bool)
    days = np.array([_day(stamp) for stamp in block.stamps], dtype=object)
    keep = np.ones(len(block), dtype=bool)
    for day in shock_days:
        keep &= days != day
    return keep


def leadlag_without_shock(rows: Sequence[dict[str, Any]],
                          block: cs.Aligned, excluded: cs.Aligned,
                          register: Register, horizon: str,
                          family: str) -> list[dict[str, Any]]:
    """Every lead-lag test again, with the shock days out of the sample.

    A full re-scan rather than a re-check of the rows that happened to be
    carried: its own family, its own Benjamini-Hochberg correction, and a
    survivor count a reader can put beside the first one. Re-testing only the
    winners would answer a different question -- whether the winners hold --
    and could not say whether a different set won.
    """
    pairs = block.pairs
    sd_bp = {pair: float(excluded.returns[:, i].std(ddof=1)) * BP
             for i, pair in enumerate(pairs)}
    out: list[dict[str, Any]] = []
    for i, lead in enumerate(pairs):
        for j, lagging in enumerate(pairs):
            if i == j:
                continue
            for lag in sorted({row["lag"] for row in rows}):
                result = cs.lead_lag(excluded.returns[:, i],
                                     excluded.returns[:, j], excluded.spans,
                                     lag)
                register.add(family,
                             f"{lead}->{lagging}|lag={lag}|{horizon}", result)
                out.append({"lead": lead, "lagging": lagging, "lag": lag,
                            "horizon": horizon, "rho": _r(result["rho"], 6),
                            "n": result["n"],
                            "p_value": _r(result["p_value"], 12),
                            "sd_bp": _r(sd_bp[lagging], 4)})
    return out


def stability_label(agreement: float | None) -> str | None:
    """T4's rolling-stability label, reused rather than re-invented.

    ``STABLE`` / ``MOSTLY-STABLE`` / ``MIXED`` / ``UNSTABLE`` on the share of
    rolling windows agreeing with the full-window sign. The thresholds are T4's
    committed ones: a card that invented its own scale would be describing the
    same evidence on a different ruler.
    """
    if agreement is None:
        return None
    for threshold, label in STABILITY_LABELS:
        if agreement >= threshold:
            return label
    return STABILITY_LABELS[-1][1]


def leadlag_headline(section: dict[str, Any], stability: Sequence[dict[str, Any]],
                     ) -> list[dict[str, Any]]:
    """The lead-lag cells that pass every test this card can put to them.

    Three conditions, the analogues of the ranked table's, with one difference
    stated in the row rather than in a footnote: a lead-lag rule trades the
    lagging pair and therefore pays **one** round trip, where a cointegration
    relationship pays one per leg.

    * **correction** -- survives Benjamini-Hochberg inside its own horizon's
      family, and still survives it when the declared shock days are removed
      and the whole family is re-scanned without them;
    * **stability** -- the sign holds across the split-half, and the rolling
      two-year sign agreement earns at least T4's ``MOSTLY-STABLE`` label. The
      lead-lag scan runs on the full primary window rather than on a
      discovery/confirmation split, so this is T4's stability discipline and
      not the ranked table's out-of-window confirmation. The difference is
      real and the column says which test was applied;
    * **cost** -- the implied edge clears the lagging pair's own round trip at
      the survival bar and the reference notional.
    """
    by_cell = {(row["lead"], row["lagging"], row["lag"]): row
               for row in stability}
    out: list[dict[str, Any]] = []
    for row in section["rows"]:
        cell = by_cell.get((row["lead"], row["lagging"], row["lag"]))
        agreement = (cell or {}).get("rolling_sign_agreement")
        label = stability_label(agreement)
        stable = bool(cell and cell.get("sign_held")
                      and label in ("STABLE", "MOSTLY-STABLE"))
        survives = bool(row["survives_correction"]
                        and row.get("survives_without_shock"))
        merged = dict(row)
        merged.update({
            "first_half": (cell or {}).get("first_half"),
            "second_half": (cell or {}).get("second_half"),
            "sign_held": (cell or {}).get("sign_held"),
            "rolling_sign_agreement": agreement,
            "rolling_windows": (cell or {}).get("rolling_windows"),
            "stability_label": label,
            "stability_tested": bool(cell is not None),
            "survives_correction_both_ways": survives,
            "stable": stable,
            "qualifies": bool(survives and stable
                              and row["pays_at_survival_bar"]),
            "legs": 1,
            "kind": "lead-lag",
        })
        out.append(merged)
    return sorted(out, key=lambda r: (not r["qualifies"],
                                      -(abs(r["rho"] or 0.0))))


# --------------------------------------------------------------------------- #
# Section 5 -- portfolio geometry
# --------------------------------------------------------------------------- #

def portfolio_section(correlations: dict[str, Any], identity: dict[str, Any]
                      ) -> dict[str, Any]:
    """How many independent bets the universe offers, by horizon and regime.

    Derived entirely from the correlation section rather than recomputed, so
    the geometry a reader sees is the geometry of the matrix printed above it.
    The rank of the currency design is carried alongside because it is the
    ceiling: twelve pairs across eight currencies cannot offer more than seven
    independent directions however they happen to be correlated, and any
    effective-bet count above that is a count of noise.
    """
    rows: list[dict[str, Any]] = []
    for horizon, block in correlations.items():
        geometry = block["geometry"]
        row = {
            "horizon": horizon,
            "pairs": len(block["pairs"]),
            "n": block["n"],
            "mean_abs_rho": block["mean_abs_rho"],
            "participation_ratio": geometry["participation_ratio"],
            "entropy_bets": geometry["entropy_bets"],
            "components_for_90pct": geometry["components_for_90pct"],
            "pc1_share": (geometry["variance_explained"] or [None])[0],
            "by_regime": {name: {
                "mean_abs_rho": regime["mean_abs_rho"],
                "participation_ratio":
                    regime["geometry"]["participation_ratio"],
                "entropy_bets": regime["geometry"]["entropy_bets"],
                "components_for_90pct":
                    regime["geometry"]["components_for_90pct"]}
                for name, regime in block["by_regime"].items()},
        }
        low = (row["by_regime"].get("low") or {}).get("participation_ratio")
        high = (row["by_regime"].get("high") or {}).get("participation_ratio")
        row["high_over_low_bets"] = _r(high / low if low and high else None, 4)
        rows.append(row)
    return {
        "rows": rows,
        "structural_ceiling": identity["rank"],
        "note": ("the currency design's rank is the ceiling on independent "
                 "directions: five of the twelve pairs are exact functions of "
                 "the other seven, so an effective-bet count is measuring how "
                 "much of seven the universe actually delivers, not how much "
                 "of twelve."),
    }


# --------------------------------------------------------------------------- #
# The ranked table
# --------------------------------------------------------------------------- #

def rank_relationships(scan: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort the scan so the qualifying relationships come first.

    A relationship qualifies when all three of the card's conditions hold: it
    survives the false-discovery correction across the scan, it confirms in
    **both** untouched windows, and its own amplitude pays both legs' round
    trips at the survival bar and the reference notional. The sort key is the
    three conditions, then the amplitude-over-cost ratio, so a table truncated
    at any length still shows the strongest first.
    """
    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        ratio = ((row.get("cost") or {}).get("amplitude_over_cost")
                 or {}).get(SURVIVAL_BAR)
        return (
            not row.get("qualifies", False),
            not row.get("stable_out_of_window", False),
            not row.get("survives_correction", False),
            -(float(ratio) if ratio is not None else -1.0),
        )
    return sorted(scan, key=key)


def qualification(row: dict[str, Any]) -> dict[str, Any]:
    """The three conditions, evaluated and named.

    Separately rather than as one boolean, because a relationship that fails
    on cost and one that fails on stability are different findings and a
    single flag would hide which.
    """
    survives = bool(row.get("survives_correction"))
    confirmations = row.get("confirmation") or {}
    verdicts = {name: (block or {}).get("verdict")
                for name, block in confirmations.items()}
    stable = all(verdicts.get(name) == CONFIRMED
                 for name in (W_CONFIRM, W_EARLY))
    pays = bool((row.get("cost") or {}).get("pays_at_survival_bar"))
    return {
        "survives_correction": survives,
        "stable_out_of_window": stable,
        "confirmation_verdicts": verdicts,
        "pays_two_legs_at_survival_bar": pays,
        "qualifies": bool(survives and stable and pays),
        "fails_on": [name for name, ok in (("correction", survives),
                                           ("out-of-window", stable),
                                           ("cost", pays)) if not ok],
    }


# --------------------------------------------------------------------------- #
# The experiment
# --------------------------------------------------------------------------- #

def run(*, params: dict[str, Any], seed: int, loader: Any,
        costs: dict[str, Any] | None = None) -> dict[str, Any]:
    """The T6 experiment. Cross-pair structure: evidence, never a strategy.

    Args:
        params: The ``[experiment.params]`` table.
        seed: Load-bearing here for the first time in this phase. The
            cointegration statistics have no standard distribution, so their
            p-values come from a simulated null, and the simulation is drawn
            from this seed. Everything else is a deterministic pass over
            stored bars.
        loader: A :class:`~research.loader.ResearchLoader` in scoring mode.
        costs: The declared cost model, handed over by the runner.
    """
    pairs = [str(p) for p in params["pairs"]]
    horizons = [str(h) for h in params["timeframes"]]
    characterisation = [str(h) for h in params["characterisation_timeframes"]]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    discovery_end = as_date(str(params["discovery_end"]))
    confirm_start = as_date(str(params["confirm_start"]))
    early_start = as_date(str(params["early_start"]))
    early_end = as_date(str(params["early_end"]))
    units = float(params["reference_units"])
    vol_window = int(params["vol_window"])
    roll = (int(params["roll_start_hour_ny"]), int(params["roll_end_hour_ny"]))
    adf_lags = int(params["adf_lags"])
    vecm_lags = int(params["vecm_lags"])
    max_lag = int(params["leadlag_max_lag"])
    alpha = float(params["fdr_alpha"])
    confirm_alpha = float(params["confirm_alpha"])
    beta_tolerance = float(params["confirm_beta_tolerance"])
    cluster_threshold = float(params["cluster_threshold"])
    window_years = int(params["rolling_window_years"])
    step_months = int(params["rolling_step_months"])
    detail_limit = int(params["detail_limit"])
    null_replications = int(params["null_replications"])
    null_length = int(params["null_length"])
    triples = [{"members": [str(m) for m in entry["members"]],
                "motivation": str(entry.get("motivation", ""))}
               for entry in params.get("triples", [])]
    shock_days = [str(day) for day in params.get("shock_days", [])]
    shock_reason = str(params.get("shock_reason", ""))

    costs = dict(costs or {})
    if not {"commission_rate", "commission_min"} <= set(costs):
        raise ValueError(
            "the cost model parameters must be declared in [experiment.costs]; "
            "an undeclared cost model is not one cost model, and a defaulted "
            "one is a second set of parameters nobody wrote down")

    currencies = currencies_of(pairs)
    identity = identity_summary(pairs, currencies)

    # Ruling R1: AUDUSD before 2011-01-01 is excluded, and R1 says a
    # cross-pair analysis spanning that window runs on eleven pairs and says
    # so. Derived from the exclusion table rather than named here, so a change
    # to the table changes this and a change here changes nothing.
    early_pairs = [p for p in pairs
                   if not (exclusion_for(p) is not None
                           and exclusion_for(p).covers(early_start))]
    withheld_early = [p for p in pairs if p not in early_pairs]

    register = Register()
    # Widths 2 and 3 only: a relationship in this scan has two or three legs,
    # so those are the system sizes whose null it needs. Simulating a width
    # nothing asks about would be a third of the null's cost spent on a table
    # no p-value reads.
    null = cs.simulate_null(seed, replications=null_replications,
                            length=null_length, lags=adf_lags,
                            widths=(2, 3))
    _LOG.info("null simulated: %d replications of %d observations",
              null_replications, null_length)

    windows = {
        W_PRIMARY: (_ns(start), _ns(end)),
        W_DISCOVERY: (_ns(start), _ns(discovery_end)),
        W_CONFIRM: (_ns(confirm_start), _ns(end)),
        W_EARLY: (_ns(early_start), _ns(early_end)),
    }

    correlations: dict[str, Any] = {}
    rolling: dict[str, Any] = {}
    currency: dict[str, Any] = {}
    leadlag: dict[str, Any] = {}
    leadlag_stab: dict[str, Any] = {}
    leadlag_noshock: dict[str, Any] = {}
    correlation_noshock: dict[str, Any] = {}
    cost_by_horizon: dict[str, Any] = {}
    coverage: list[dict[str, Any]] = []
    scan: list[dict[str, Any]] = []

    for horizon in horizons:
        step_ns, max_gap_ns = _alias_step(horizon)
        series = load_all(loader, pairs, horizon, early_start, end)
        readable = [p for p in pairs if p in series]
        cost_by_horizon[horizon] = cost_table(series, costs, units, roll,
                                              vol_window, windows)

        blocks: dict[str, cs.Aligned | None] = {}
        primary_all = cs.align(series, readable, step_ns, max_gap_ns)
        if primary_all is None:
            _LOG.warning("%s: no common index across the universe", horizon)
            continue
        blocks[W_PRIMARY] = primary_all.window(*windows[W_PRIMARY], step_ns,
                                               max_gap_ns)
        blocks[W_DISCOVERY] = primary_all.window(*windows[W_DISCOVERY],
                                                 step_ns, max_gap_ns)
        blocks[W_CONFIRM] = primary_all.window(*windows[W_CONFIRM], step_ns,
                                               max_gap_ns)
        early_block = cs.align({p: series[p] for p in early_pairs
                                if p in series},
                               [p for p in early_pairs if p in series],
                               step_ns, max_gap_ns)
        blocks[W_EARLY] = (early_block.window(*windows[W_EARLY], step_ns,
                                              max_gap_ns)
                           if early_block is not None else None)

        for name, block in blocks.items():
            coverage.append({
                "horizon": horizon, "window": name,
                "pairs": len(block.pairs) if block is not None else 0,
                "n": len(block) if block is not None else 0,
                "adjacent_share": _r(block.adjacent_share(), 6)
                if block is not None else None,
                "from": (_day(block.stamps[0])
                         if block is not None and len(block) else None),
                "to": (_day(block.stamps[-1])
                       if block is not None and len(block) else None)})

        primary = blocks[W_PRIMARY]
        correlations[horizon] = correlation_section(
            primary, register, horizon, vol_window=vol_window,
            cluster_threshold=cluster_threshold,
            split_ns=_ns(confirm_start))
        rolling[horizon] = rolling_geometry(primary,
                                            window_years=window_years,
                                            step_months=step_months)
        currency[horizon] = currency_section(primary, currencies, register,
                                             horizon,
                                             split_ns=_ns(confirm_start))

        section = leadlag_section(
            primary, register, horizon, max_lag=max_lag,
            family=f"leadlag@{horizon}",
            costs_by_pair=cost_by_horizon[horizon][W_PRIMARY])
        leadlag[horizon] = section
        leadlag_stab[horizon] = leadlag_stability(
            primary, section["rows"],
            halves=[blocks[W_DISCOVERY], blocks[W_CONFIRM]],
            window_years=window_years, step_months=step_months,
            step_ns=step_ns, max_gap_ns=max_gap_ns, limit=detail_limit)

        excluded = primary.drop(shock_mask(primary, shock_days), step_ns,
                                max_gap_ns)
        without = cs.correlation_matrix(excluded.returns)
        correlation_noshock[horizon] = {
            "n": len(excluded),
            "rows_dropped": len(primary) - len(excluded),
            "mean_abs_rho": _r(_offdiag_mean(np.abs(without)), 5),
            "geometry": _round_geometry(cs.effective_bets(without)),
        }
        leadlag_noshock[horizon] = {
            "rows": leadlag_without_shock(section["rows"], primary, excluded,
                                          register, horizon,
                                          f"leadlag_no_shock@{horizon}"),
            "n": len(excluded),
            "rows_dropped": len(primary) - len(excluded),
        }
        _LOG.info("%s: %d lead-lag test(s), %d correlation(s)",
                  horizon, section["tests"],
                  len(correlations[horizon]["tests"]))

        candidates: list[list[str]] = []
        for i, first in enumerate(readable):
            for j, second in enumerate(readable):
                if i != j:
                    candidates.append([first, second])
        for entry in triples:
            if all(member in readable for member in entry["members"]):
                candidates.append(list(entry["members"]))
        motivation = {"+".join(e["members"]): e["motivation"] for e in triples}

        discovery_block = blocks[W_DISCOVERY]
        for members in candidates:
            # Johansen is symmetric in its members, so it is computed for the
            # canonical ordering only and the reversed one carries no trace
            # statistic rather than a duplicate of it.
            canonical = list(members) == sorted(members)
            found = cointegration_of(discovery_block, members,
                                     adf_lags=adf_lags, vecm_lags=vecm_lags,
                                     null=null, with_johansen=canonical)
            if found is None:
                continue
            flags = identity_of(members, currencies)
            key = relationship_key(members, horizon)
            register.add("cointegration_engle_granger", key,
                         {"z": found["tau"], "p_value": found["eg_p_value"]})
            if canonical and found["trace_p_value"] is not None:
                register.add("cointegration_johansen", key,
                             {"z": found["trace"],
                              "p_value": found["trace_p_value"]})
            row: dict[str, Any] = {
                "members": members, "horizon": horizon,
                "size": len(members),
                "identity": flags["identity"],
                "johansen_canonical": canonical,
                "identity_combination": flags["combination"],
                "motivation": motivation.get("+".join(members), ""),
                "key": key,
                "discovery": found,
            }
            others: dict[str, Any] = {}
            for name in (W_CONFIRM, W_EARLY, W_PRIMARY):
                block = blocks[name]
                if block is None or any(m not in block.pairs
                                        for m in members):
                    others[name] = None
                    continue
                others[name] = cointegration_of(block, members,
                                                adf_lags=adf_lags,
                                                vecm_lags=vecm_lags, null=null,
                                                with_johansen=canonical)
            row["windows"] = others
            row["confirmation"] = {
                name: confirm(found, others.get(name), alpha=confirm_alpha,
                              beta_tolerance=beta_tolerance)
                for name in (W_CONFIRM, W_EARLY)}
            leg_cost = two_leg_cost(
                members, found["beta"],
                cost_by_horizon[horizon].get(W_CONFIRM, {}))
            reference = others.get(W_CONFIRM) or found
            row["cost"] = cost_verdict(reference["residual_sd_bp"],
                                       reference["residual_step_bp"], leg_cost)
            row["cost_legs"] = leg_cost
            row["cost_window"] = W_CONFIRM
            scan.append(row)
        _LOG.info("%s: %d cointegration candidate(s) scanned", horizon,
                  len(candidates))
        del series, primary_all, blocks, early_block

    summary = register.summarise(alpha)
    q_lookup = register.q_lookup()
    for row in scan:
        q = q_lookup.get(f"cointegration_engle_granger|{row['key']}")
        row["eg_q_value"] = _r(q, 12)
        row["survives_correction"] = bool(q is not None and q <= alpha)
        trace_q = q_lookup.get(f"cointegration_johansen|{row['key']}")
        row["johansen_q_value"] = _r(trace_q, 12)
        row["johansen_survives_correction"] = bool(
            trace_q is not None and trace_q <= alpha)
        row.update(qualification(row))
    ranked = rank_relationships(scan)

    for horizon, section in leadlag.items():
        for row in section["rows"]:
            key = (f"leadlag@{horizon}|{row['lead']}->{row['lagging']}"
                   f"|lag={row['lag']}|{horizon}")
            row["q_value"] = _r(q_lookup.get(key), 12)
            row["survives_correction"] = bool(
                row["q_value"] is not None and row["q_value"] <= alpha)
        # Every one of the several thousand tests is in the register, which is
        # what makes the family countable and a test undroppable. The payload
        # carries the rows a reader would look at: the ones that survived the
        # correction, the ones that pay their cost, and the strongest by
        # effect size whether they did either or not.
        kept = {id(row): row for row in section["rows"]
                if row["survives_correction"] or row["pays_at_survival_bar"]}
        for row in sorted(section["rows"],
                          key=lambda r: -(abs(r["rho"] or 0.0)))[:detail_limit]:
            kept.setdefault(id(row), row)
        section["rows_carried"] = len(kept)
        section["rows"] = sorted(
            kept.values(), key=lambda r: -(abs(r["rho"] or 0.0)))
        section["survivors"] = sum(1 for row in section["rows"]
                                   if row["survives_correction"])
        section["paying"] = sum(1 for row in section["rows"]
                                if row["pays_at_survival_bar"])

        # The same cells with the declared shock days removed, annotated onto
        # the rows the payload carries so the two figures sit together.
        block = leadlag_noshock.get(horizon) or {}
        without = {(r["lead"], r["lagging"], r["lag"]): r
                   for r in block.get("rows", [])}
        survivors_without = 0
        for r in block.get("rows", []):
            key = (f"leadlag_no_shock@{horizon}|{r['lead']}->{r['lagging']}"
                   f"|lag={r['lag']}|{horizon}")
            r["q_value"] = _r(q_lookup.get(key), 12)
            r["survives_correction"] = bool(
                r["q_value"] is not None and r["q_value"] <= alpha)
            survivors_without += int(r["survives_correction"])
            cost = (cost_by_horizon[horizon][W_PRIMARY].get(r["lagging"])
                    or {}).get("cost_bp")
            edge = abs(r["rho"] or 0.0) * (r["sd_bp"] or 0.0)
            r["edge_bp"] = _r(edge, 5)
            r["pays_at_survival_bar"] = bool(
                cost and cost.get(SURVIVAL_BAR)
                and edge > cost[SURVIVAL_BAR])
        block["survivors"] = survivors_without
        block["paying"] = sum(1 for r in block.get("rows", [])
                              if r["pays_at_survival_bar"])
        block["both"] = sum(1 for r in block.get("rows", [])
                            if r["survives_correction"]
                            and r["pays_at_survival_bar"])
        for row in section["rows"]:
            match = without.get((row["lead"], row["lagging"], row["lag"]))
            row["rho_without_shock"] = (match or {}).get("rho")
            row["q_without_shock"] = (match or {}).get("q_value")
            row["survives_without_shock"] = (match or {}).get(
                "survives_correction")
            row["pays_without_shock"] = (match or {}).get(
                "pays_at_survival_bar")
            row["shock_share_of_rho"] = _r(
                1.0 - abs(match["rho"]) / abs(row["rho"])
                if match and match.get("rho") and row["rho"] else None, 4)
        # The carried rows are trimmed, so the no-shock block is trimmed to
        # the same cells plus its own survivors -- otherwise the payload would
        # carry a second copy of the whole family.
        keep_keys = {(r["lead"], r["lagging"], r["lag"])
                     for r in section["rows"]}
        block["rows"] = [r for r in block.get("rows", [])
                         if (r["lead"], r["lagging"], r["lag"]) in keep_keys
                         or r["survives_correction"]]

    for horizon, section in currency.items():
        for row in section["factors"]:
            key = f"{row['currency']}|{horizon}"
            row["rho1_q"] = _r(
                q_lookup.get(f"currency_factor_autocorr|{key}"), 12)
            row["vr_q_value"] = _r(q_lookup.get(
                f"currency_factor_variance_ratio|{key}|q={VR_HEADLINE}"), 12)
            row["vr_survives_correction"] = bool(
                row["vr_q_value"] is not None and row["vr_q_value"] <= alpha)
        for row in section["pair_reference"]:
            key = f"{row['pair']}|{horizon}"
            row["rho1_q"] = _r(q_lookup.get(f"pair_reference_autocorr|{key}"),
                               12)
            row["vr_q_value"] = _r(q_lookup.get(
                f"pair_reference_variance_ratio|{key}|q={VR_HEADLINE}"), 12)
            row["vr_survives_correction"] = bool(
                row["vr_q_value"] is not None and row["vr_q_value"] <= alpha)

    short: dict[str, Any] = {}
    for horizon in characterisation:
        step_ns, max_gap_ns = _alias_step(horizon)
        series = load_all(loader, pairs, horizon, start, end)
        readable = [p for p in pairs if p in series]
        block = cs.align(series, readable, step_ns, max_gap_ns)
        if block is None:
            continue
        table = cost_table(series, costs, units, roll, vol_window,
                           {W_PRIMARY: windows[W_PRIMARY]})[W_PRIMARY]
        matrix = cs.correlation_matrix(block.returns)
        section = leadlag_section(block, register, horizon, max_lag=max_lag,
                                  family="leadlag@characterisation",
                                  costs_by_pair=table)
        strongest = sorted(section["rows"],
                           key=lambda r: -(abs(r["rho"] or 0.0)))[:detail_limit]
        short[horizon] = {
            "pairs": list(block.pairs),
            "n": len(block),
            "adjacent_share": _r(block.adjacent_share(), 6),
            "mean_abs_rho": _r(_offdiag_mean(np.abs(matrix)), 5),
            "matrix": [[_r(float(v), 5) for v in row] for row in matrix],
            "geometry": _round_geometry(cs.effective_bets(matrix)),
            "clusters": cs.average_linkage(cs.correlation_distance(matrix),
                                           block.pairs, cluster_threshold),
            "leadlag_tests": section["tests"],
            "leadlag_strongest": strongest,
            "costs": table,
        }
        _LOG.info("characterisation %s: %d rows, %d lead-lag test(s)",
                  horizon, len(block), section["tests"])
        del series, block

    summary = register.summarise(alpha)
    q_lookup = register.q_lookup()
    for horizon, section in short.items():
        for row in section["leadlag_strongest"]:
            key = ("leadlag@characterisation|"
                   f"{row['lead']}->{row['lagging']}|lag={row['lag']}"
                   f"|{horizon}")
            row["q_value"] = _r(q_lookup.get(key), 12)
            row["survives_correction"] = bool(
                row["q_value"] is not None and row["q_value"] <= alpha)

    headline: dict[str, Any] = {}
    for horizon, section in leadlag.items():
        rows = leadlag_headline(section, leadlag_stab.get(horizon, []))
        headline[horizon] = {
            "rows": rows,
            "tested": section["tests"],
            "survives_correction": sum(1 for r in rows
                                       if r["survives_correction"]),
            "survives_both_ways": sum(1 for r in rows
                                      if r["survives_correction_both_ways"]),
            "stable": sum(1 for r in rows if r["stable"]),
            "pays": sum(1 for r in rows if r["pays_at_survival_bar"]),
            "qualifying": sum(1 for r in rows if r["qualifies"]),
        }

    qualifying = [row for row in ranked if row["qualifies"]]
    floored = sum(1 for row in scan
                  if (row["discovery"] or {}).get("eg_p_on_floor"))
    return {
        "note": ("EDA battery III: cross-pair structure. Evidence and "
                 "questions for a checkpoint -- never a strategy, never a "
                 "scorecard, never a pair promoted or dropped. Pre-registered "
                 "decision #3 puts the decisions this evidence informs in "
                 "chat, between cards."),
        "window": {
            "primary": {"start": start.isoformat(), "end": end.isoformat()},
            "discovery": {"start": start.isoformat(),
                          "end": discovery_end.isoformat()},
            "confirmation": {"start": confirm_start.isoformat(),
                             "end": end.isoformat()},
            "early": {"start": early_start.isoformat(),
                      "end": early_end.isoformat()},
            "pairs": pairs,
            "early_pairs": early_pairs,
            "pairs_withheld_from_the_early_window": withheld_early,
            "horizons": horizons,
            "characterisation_horizons": characterisation,
        },
        "method": {
            "reference_units": units,
            "ladder": list(LADDER),
            "survival_bar": SURVIVAL_BAR,
            "cost_model": "fxlab.costs.IBCostModel",
            "cost_parameters": costs,
            "cost_window_for_the_verdict": W_CONFIRM,
            "adf_lags": adf_lags,
            "vecm_lags": vecm_lags,
            "leadlag_max_lag": max_lag,
            "variance_ratio_horizons": list(VR_HORIZONS),
            "variance_ratio_headline": VR_HEADLINE,
            "fdr_alpha": alpha,
            "confirm_alpha": confirm_alpha,
            "confirm_beta_tolerance": beta_tolerance,
            "cluster_threshold": cluster_threshold,
            "rolling_window_years": window_years,
            "rolling_step_months": step_months,
            "detail_limit": detail_limit,
            "vol_window": vol_window,
            "roll_window_ny": [roll[0], roll[1]],
            "shock_days": shock_days,
            "shock_reason": shock_reason,
            "null": {
                "replications": null["replications"],
                "length": null["length"],
                "lags": null["lags"],
                "seed": null["seed"],
                "smallest_p_value": _r(1.0 / (null["replications"] + 1.0), 8),
                "quantiles": {name: {width: {label: _r(value, 5)
                                             for label, value in block.items()}
                                     for width, block in widths.items()}
                              for name, widths in null["quantiles"].items()},
                "engle_granger_published": {
                    str(width): cs.engle_granger_reference(width)
                    for width in (2, 3)},
            },
        },
        "identity": identity,
        "coverage": coverage,
        "costs": cost_by_horizon,
        "correlation": correlations,
        "rolling_geometry": rolling,
        "currency": currency,
        "cointegration": {
            "scanned": len(scan),
            "ranked": ranked,
            "qualifying": len(qualifying),
            "identity_relationships": sum(1 for row in scan
                                          if row["identity"]),
            "p_values_on_the_simulation_floor": floored,
            "triples_declared": triples,
        },
        "leadlag": leadlag,
        "leadlag_headline": headline,
        "leadlag_stability": leadlag_stab,
        "leadlag_without_shock": leadlag_noshock,
        "correlation_without_shock": correlation_noshock,
        "portfolio": portfolio_section(correlations, identity),
        "characterisation": short,
        "test_register": register.rows(),
        "families": summary,
        "loader": {"mode": loader.mode,
                   "sealed_dates_served": loader.access.sealed_dates(),
                   "excluded_dates_withheld": len(loader.access.excluded),
                   "excluded_pairs": loader.access.excluded_pairs()},
        "seed": int(seed),
    }
