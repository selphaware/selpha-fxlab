"""Cross-series estimators for the T6 battery, in numpy alone.

T4's :mod:`research.stats` answers questions about one series. This module
answers questions about several at once: how they move together, whether a
combination of them is stationary when none of them is, whether one of them
moves before another, and how many independent things the universe is really
doing. Same discipline as T4: no scipy, no statsmodels, every estimator
checked in ``tests2/test_crossstats.py`` against either a hand-computed
example, a printed table, or a system whose answer is known by construction.

Four things are worth stating before the code.

**Nothing spans a hole, on either side.** A cross-correlation at lag 1 between
two pairs is only a lag-1 pair if both series really are adjacent in time
there, so every series is first aligned onto a **common** timestamp index and
the contiguous runs of that index are what every estimator works inside. The
alignment is an intersection and never an interpolation: a bar one pair has and
another does not is dropped from both rather than filled in, because a filled
value is a return nobody quoted.

**The null distributions are simulated, not tabulated.** The Engle-Granger and
Johansen statistics do not have standard distributions, and the published
critical-value tables stop at the handful of significance levels somebody
printed. A scan of several hundred relationships needs a false-discovery
correction, and a correction needs p-values. So the null is simulated here from
independent random walks, with the experiment's own seed, using **the same
code path** the data goes through -- and :func:`engle_granger_reference` holds
MacKinnon's published asymptotic values so a test can check the simulation
against them rather than the other way round.

**A p-value has a floor and the floor is stated.** With ``R`` replications the
smallest attainable p-value is ``1/(R+1)``. Every result carries the flag
:data:`P_FLOOR_KEY` when it sits on that floor, the report says how many did,
and the Benjamini-Hochberg step-up is applied to the floored values -- which
costs power only when very few tests are significant, and is visible when it
does.

**Johansen is run with an unrestricted constant.** The levels of a decade of
log FX prices carry a drift; the variant that allows one is the standard
choice, and it is the numerically robust one because ``S11`` stays a plain
covariance of the levels rather than a covariance augmented with a column of
ones. The choice is stated rather than defaulted, and it is the same choice in
the simulated null.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final, Sequence

import numpy as np

from research import stats

_LOG: Final[logging.Logger] = logging.getLogger("research.crossstats")

#: Basis points per unit.
BP: Final[float] = 1e4

#: Key marking a p-value that sits on the simulation's resolution floor.
P_FLOOR_KEY: Final[str] = "p_on_floor"

#: MacKinnon's asymptotic critical values for the Engle-Granger residual test
#: with a constant in the cointegrating regression, by the number of variables
#: in that regression. Printed values, carried here so a test can check the
#: simulated null against them; nothing in the experiment reads them, because
#: the experiment uses p-values and a table has none.
ENGLE_GRANGER_CRITICAL: Final[dict[int, dict[str, float]]] = {
    1: {"1%": -3.43, "5%": -2.86, "10%": -2.57},
    2: {"1%": -3.90, "5%": -3.34, "10%": -3.04},
    3: {"1%": -4.29, "5%": -3.74, "10%": -3.45},
}


def engle_granger_reference(variables: int) -> dict[str, float]:
    """The published asymptotic critical values for a scan of this width."""
    return dict(ENGLE_GRANGER_CRITICAL[int(variables)])


# --------------------------------------------------------------------------- #
# Alignment
# --------------------------------------------------------------------------- #

def spans_from_stamps(stamps: np.ndarray, step_ns: int,
                      max_gap_ns: int | None = None) -> list[tuple[int, int]]:
    """Contiguous runs of an aligned timestamp index, as ``[start, stop)``.

    Two consecutive entries are contiguous when they are exactly one bar apart
    -- or, at the daily grain where a weekend is not a hole, no more than
    ``max_gap_ns`` apart. Everything downstream works inside a run, so this is
    the one place the definition of "adjacent" lives.
    """
    values = np.asarray(stamps, dtype="int64")
    if values.size == 0:
        return []
    gaps = np.diff(values)
    limit = max_gap_ns if max_gap_ns is not None else step_ns
    breaks = np.nonzero((gaps < step_ns) | (gaps > limit))[0] + 1
    edges = [0, *breaks.tolist(), values.size]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)
            if edges[i + 1] > edges[i]]


def valid_rows(spans: Sequence[tuple[int, int]], need: int) -> np.ndarray:
    """Row indices ``t`` with ``t - need`` through ``t`` all inside one span.

    Every regression below reaches backwards a fixed number of observations,
    and a row that reaches across a hole is a row built from two different
    weeks. This is what stops it.
    """
    runs: list[np.ndarray] = []
    for start, stop in spans:
        low = start + int(need)
        if stop > low:
            runs.append(np.arange(low, stop, dtype="int64"))
    if not runs:
        return np.zeros(0, dtype="int64")
    return np.concatenate(runs)


class Aligned:
    """Several pairs' series on one common timestamp index.

    Attributes:
        pairs: Pair names, in the order the columns are in.
        stamps: The common timestamps, int64 epoch nanoseconds.
        returns: ``(T, n)`` log returns.
        log_price: ``(T, n)`` log mid at the closing bar of each return.
        spans: Contiguous runs of ``stamps``.
    """

    __slots__ = ("pairs", "stamps", "returns", "log_price", "spans")

    def __init__(self, pairs: Sequence[str], stamps: np.ndarray,
                 returns: np.ndarray, log_price: np.ndarray,
                 spans: list[tuple[int, int]]) -> None:
        self.pairs = list(pairs)
        self.stamps = stamps
        self.returns = returns
        self.log_price = log_price
        self.spans = spans

    def __len__(self) -> int:
        return int(self.stamps.size)

    def column(self, pair: str) -> int:
        """Column index of one pair."""
        return self.pairs.index(pair)

    def select(self, pairs: Sequence[str]) -> "Aligned":
        """The same index restricted to some of the pairs.

        The index is **not** re-intersected: a subset of pairs aligned on the
        whole universe's common index is a different sample from the same
        subset aligned on its own, and mixing the two inside one table would
        make two rows of it incomparable. A caller wanting the wider sample
        asks :func:`align` for exactly those pairs.
        """
        cols = [self.column(p) for p in pairs]
        return Aligned(pairs, self.stamps, self.returns[:, cols],
                       self.log_price[:, cols], self.spans)

    def window(self, low_ns: int, high_ns: int, step_ns: int,
               max_gap_ns: int | None = None) -> "Aligned":
        """The same pairs over a sub-window, with its own spans."""
        keep = (self.stamps >= low_ns) & (self.stamps <= high_ns)
        stamps = self.stamps[keep]
        return Aligned(self.pairs, stamps, self.returns[keep],
                       self.log_price[keep],
                       spans_from_stamps(stamps, step_ns, max_gap_ns))

    def drop(self, keep: np.ndarray, step_ns: int,
             max_gap_ns: int | None = None) -> "Aligned":
        """The same pairs with some rows removed, and its spans rebuilt.

        Removing a row leaves the rows either side adjacent in the index and
        no longer adjacent in time, so the spans have to be recomputed rather
        than inherited: a lag-1 pair straddling a dropped day is not a lag-1
        pair. That is the whole reason this is a method rather than a mask.
        """
        mask = np.asarray(keep, dtype=bool)
        stamps = self.stamps[mask]
        return Aligned(self.pairs, stamps, self.returns[mask],
                       self.log_price[mask],
                       spans_from_stamps(stamps, step_ns, max_gap_ns))

    def adjacent_share(self) -> float | None:
        """Share of consecutive rows that really are one bar apart."""
        if self.stamps.size < 2:
            return None
        inside = sum(stop - start - 1 for start, stop in self.spans)
        return float(inside / (self.stamps.size - 1))


def align(series_by_pair: dict[str, Any], pairs: Sequence[str], step_ns: int,
          max_gap_ns: int | None = None) -> Aligned | None:
    """Intersect several return series onto one timestamp index.

    Args:
        series_by_pair: :class:`research.character.Series` per pair.
        pairs: Which of them to align, in column order.
        step_ns: One bar, in nanoseconds.
        max_gap_ns: Largest gap still counted as contiguous; ``None`` means
            exactly one bar.

    Returns:
        The aligned block, or ``None`` when the intersection is empty or a
        requested pair is missing. ``None`` rather than an empty block: a
        caller that averages over nothing reports a number for a window it
        never had.
    """
    wanted = [p for p in pairs]
    if any(p not in series_by_pair for p in wanted):
        return None
    keys: list[np.ndarray] = []
    for pair in wanted:
        series = series_by_pair[pair]
        keys.append(series.ts[series.ret_pos])
    common = keys[0]
    for other in keys[1:]:
        common = np.intersect1d(common, other, assume_unique=False)
    if common.size < stats.MIN_SAMPLE:
        return None
    returns = np.empty((common.size, len(wanted)), dtype="float64")
    log_price = np.empty((common.size, len(wanted)), dtype="float64")
    for column, pair in enumerate(wanted):
        series = series_by_pair[pair]
        own = series.ts[series.ret_pos]
        where = np.searchsorted(own, common)
        returns[:, column] = series.returns[where]
        log_price[:, column] = np.log(series.mid_close[series.ret_pos[where]])
    return Aligned(wanted, common, returns, log_price,
                   spans_from_stamps(common, step_ns, max_gap_ns))


# --------------------------------------------------------------------------- #
# Correlation and its geometry
# --------------------------------------------------------------------------- #

def correlation_matrix(returns: np.ndarray) -> np.ndarray:
    """Pearson correlation across the columns of a return block."""
    centred = returns - returns.mean(axis=0, keepdims=True)
    sd = np.sqrt((centred ** 2).sum(axis=0))
    sd[sd <= 0.0] = np.nan
    return (centred.T @ centred) / np.outer(sd, sd)


def correlation_test(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    """Contemporaneous correlation with a z statistic and a p-value.

    Fisher's transform rather than ``rho sqrt(n)``, because these correlations
    reach 0.9 and the small-rho approximation is not usable there.
    """
    rho = stats.pearson(x, y)
    n = int(min(x.size, y.size))
    if rho is None or n < 4 or abs(rho) >= 1.0:
        return {"n": n, "rho": rho, "z": None, "p_value": None}
    z = math.atanh(rho) * math.sqrt(n - 3)
    return {"n": n, "rho": float(rho), "z": float(z),
            "p_value": stats.norm_two_sided(z)}


def lead_pairs(x: np.ndarray, y: np.ndarray, spans: Sequence[tuple[int, int]],
               lag: int) -> tuple[np.ndarray, np.ndarray]:
    """``(x_t, y_{t+lag})`` for every ``t`` with both ends inside one span."""
    lag = int(lag)
    if lag < 0:
        raise ValueError(f"lag must be >= 0, got {lag}")
    earlier: list[np.ndarray] = []
    later: list[np.ndarray] = []
    for start, stop in spans:
        if stop - start <= lag:
            continue
        earlier.append(x[start:stop - lag] if lag else x[start:stop])
        later.append(y[start + lag:stop])
    if not earlier:
        empty = np.zeros(0, dtype="float64")
        return empty, empty
    return np.concatenate(earlier), np.concatenate(later)


def lead_lag(x: np.ndarray, y: np.ndarray, spans: Sequence[tuple[int, int]],
             lag: int) -> dict[str, Any]:
    """Correlation of ``x`` today with ``y`` ``lag`` bars later.

    A positive lag asks whether ``x`` leads ``y``. The statistic is
    ``rho sqrt(n)`` as in T4, which assumes the pairs are independent draws --
    they overlap, so the p-value is optimistic in the same way T4's was, and
    the report says so beside the family size rather than in a footnote.
    """
    earlier, later = lead_pairs(x, y, spans, lag)
    n = int(earlier.size)
    rho = stats.pearson(earlier, later) if n >= stats.MIN_SAMPLE else None
    z = rho * math.sqrt(n) if rho is not None and n > 1 else None
    return {"lag": int(lag), "n": n, "rho": rho, "z": z,
            "p_value": stats.norm_two_sided(z) if z is not None else None}


def effective_bets(correlation: np.ndarray) -> dict[str, Any]:
    """How many independent things a correlated universe is really doing.

    Two measures, because they answer the question differently and a reader
    should see both:

    * the **participation ratio** ``(sum lambda)^2 / sum lambda^2``, which is
      ``n`` for an identity correlation and 1 for a universe moving as one
      thing;
    * the **entropy** measure ``exp(-sum p log p)`` over the normalised
      eigenvalues, which is the same two extremes and is less dominated by the
      largest eigenvalue in between.

    ``variance_explained`` is the first few principal components' share, and
    ``components_for_90pct`` is the count a portfolio would need to span most
    of the universe's variance.
    """
    matrix = np.asarray(correlation, dtype="float64")
    n = matrix.shape[0]
    finite = np.isfinite(matrix)
    if not finite.all() or n == 0:
        return {"n": int(n), "participation_ratio": None,
                "entropy_bets": None, "eigenvalues": [],
                "variance_explained": [], "components_for_90pct": None}
    values = np.linalg.eigvalsh((matrix + matrix.T) / 2.0)[::-1]
    values = np.clip(values, 0.0, None)
    total = float(values.sum())
    if total <= 0.0:
        return {"n": int(n), "participation_ratio": None,
                "entropy_bets": None, "eigenvalues": [],
                "variance_explained": [], "components_for_90pct": None}
    share = values / total
    positive = share[share > 0.0]
    entropy = float(-(positive * np.log(positive)).sum())
    cumulative = np.cumsum(share)
    reach = np.nonzero(cumulative >= 0.90)[0]
    return {
        "n": int(n),
        "participation_ratio": float(total ** 2 / float((values ** 2).sum())),
        "entropy_bets": float(math.exp(entropy)),
        "eigenvalues": [float(v) for v in values],
        "variance_explained": [float(s) for s in share],
        "components_for_90pct": int(reach[0] + 1) if reach.size else int(n),
    }


def correlation_distance(correlation: np.ndarray) -> np.ndarray:
    """``sqrt(2 (1 - rho))`` -- the standard metric on a correlation matrix."""
    matrix = np.clip(np.asarray(correlation, dtype="float64"), -1.0, 1.0)
    return np.sqrt(np.clip(2.0 * (1.0 - matrix), 0.0, None))


def average_linkage(distance: np.ndarray, labels: Sequence[str],
                    threshold: float) -> list[list[str]]:
    """Average-linkage agglomerative clusters, cut at ``threshold``.

    Deterministic to the last tie: the closest pair is chosen by distance and
    then by the lower index, so two runs on the same matrix produce the same
    clusters in the same order. A clustering that reordered itself between
    runs would show as a changed figure on every diff.
    """
    matrix = np.array(distance, dtype="float64")
    groups: list[list[int]] = [[i] for i in range(len(labels))]
    while len(groups) > 1:
        best = None
        best_value = float("inf")
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                block = matrix[np.ix_(groups[a], groups[b])]
                value = float(block.mean())
                if value < best_value - 1e-15:
                    best_value, best = value, (a, b)
        if best is None or best_value > threshold:
            break
        a, b = best
        groups[a] = groups[a] + groups[b]
        groups.pop(b)
    ordered = sorted(([labels[i] for i in sorted(group)] for group in groups),
                     key=lambda names: (-len(names), names[0]))
    return ordered


# --------------------------------------------------------------------------- #
# Regression helpers
# --------------------------------------------------------------------------- #

def _residual_of(target: np.ndarray, design: np.ndarray) -> np.ndarray:
    """Residual of ``target`` regressed on ``design``, one or many columns."""
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    return target - design @ coefficients


def ols(y: np.ndarray, x: np.ndarray, *,
        constant: bool = True) -> dict[str, Any]:
    """Least squares of ``y`` on ``x``, returning the fit and its residual."""
    matrix = np.atleast_2d(np.asarray(x, dtype="float64"))
    if matrix.shape[0] != y.size:
        matrix = matrix.T
    design = (np.column_stack([np.ones(y.size), matrix]) if constant
              else matrix)
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ coefficients
    return {"coefficients": [float(c) for c in coefficients],
            "intercept": float(coefficients[0]) if constant else 0.0,
            "beta": [float(c) for c in (coefficients[1:] if constant
                                        else coefficients)],
            "residual": residual,
            "r_squared": _r_squared(y, residual)}


def _r_squared(y: np.ndarray, residual: np.ndarray) -> float | None:
    """Share of variance the fit explains, or ``None`` for a constant target."""
    centred = y - y.mean()
    total = float(np.dot(centred, centred))
    if total <= 0.0:
        return None
    return float(1.0 - float(np.dot(residual, residual)) / total)


# --------------------------------------------------------------------------- #
# Unit roots, span aware
# --------------------------------------------------------------------------- #

def adf_segments(y: np.ndarray, spans: Sequence[tuple[int, int]], lags: int,
                 *, constant: bool = True) -> dict[str, Any]:
    """Augmented Dickey-Fuller over a series broken into contiguous spans.

    The same regression :func:`research.stats.adf` runs, restricted to rows
    whose whole lag window lies inside one span, and with the deterministic
    term optional: an Engle-Granger residual is mean zero by construction, and
    the published critical values for it are the ones with no constant in the
    residual regression.
    """
    values = np.asarray(y, dtype="float64")
    lags = max(0, int(lags))
    idx = valid_rows(spans, lags + 1)
    columns_needed = lags + 1 + (1 if constant else 0)
    if idx.size <= columns_needed + 3:
        return {"n": int(idx.size), "lags": lags, "tau": None, "gamma": None}
    target = values[idx] - values[idx - 1]
    columns: list[np.ndarray] = []
    if constant:
        columns.append(np.ones(idx.size, dtype="float64"))
    columns.append(values[idx - 1])
    level_column = len(columns) - 1
    for i in range(1, lags + 1):
        columns.append(values[idx - i] - values[idx - i - 1])
    design = np.column_stack(columns)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    dof = idx.size - design.shape[1]
    if dof <= 0:
        return {"n": int(idx.size), "lags": lags, "tau": None, "gamma": None}
    sigma2 = float(np.dot(residual, residual) / dof)
    try:
        covariance = np.linalg.inv(design.T @ design) * sigma2
    except np.linalg.LinAlgError:
        return {"n": int(idx.size), "lags": lags, "tau": None, "gamma": None}
    gamma = float(coefficients[level_column])
    se = math.sqrt(max(0.0, float(covariance[level_column, level_column])))
    return {"n": int(idx.size), "lags": lags, "gamma": gamma,
            "tau": float(gamma / se) if se > 0.0 else None}


def ar1_half_life(y: np.ndarray, spans: Sequence[tuple[int, int]]
                  ) -> dict[str, Any]:
    """Half-life of an Ornstein-Uhlenbeck fit, in bars.

    ``dy_t = a + b y_{t-1} + u`` is an AR(1) in ``phi = 1 + b``, whose shocks
    decay by ``|phi|`` a bar, so the half-life is ``-log 2 / log |phi|``. The
    absolute value matters at the fast end: a spread that overshoots its mean
    every bar has ``phi`` negative, and it is still reverting -- oscillating
    about the mean rather than crawling back to it -- so the magnitude is what
    decays. ``phi = 0`` is complete reversion in one bar and a half-life of
    zero.

    ``None`` when ``|phi| >= 1``: a series that is not pulling back towards
    anything has no half-life, and returning a negative one is how a table of
    half-lives ends up with entries nobody notices.

    **A half-life is only meaningful where the unit root was rejected**, and
    the estimator cannot enforce that on its own: least squares is biased
    downwards on a genuine random walk, so it will hand back a small negative
    ``b`` and a half-life of a few hundred bars for a series with no mean to
    revert to. That is why ``t_stat`` travels with the answer and why every
    caller here quotes a half-life beside the cointegration test that earned
    it rather than on its own.
    """
    values = np.asarray(y, dtype="float64")
    idx = valid_rows(spans, 1)
    if idx.size < stats.MIN_SAMPLE:
        return {"n": int(idx.size), "b": None, "t_stat": None,
                "half_life_bars": None}
    target = values[idx] - values[idx - 1]
    design = np.column_stack([np.ones(idx.size), values[idx - 1]])
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    b = float(coefficients[1])
    dof = idx.size - design.shape[1]
    t_stat = None
    if dof > 0:
        sigma2 = float(np.dot(residual, residual) / dof)
        try:
            covariance = np.linalg.inv(design.T @ design) * sigma2
            se = math.sqrt(max(0.0, float(covariance[1, 1])))
            t_stat = float(b / se) if se > 0.0 else None
        except np.linalg.LinAlgError:
            t_stat = None
    phi = 1.0 + b
    if b >= 0.0 or abs(phi) >= 1.0:
        return {"n": int(idx.size), "b": b, "t_stat": t_stat,
                "half_life_bars": None}
    if phi == 0.0:
        return {"n": int(idx.size), "b": b, "t_stat": t_stat,
                "half_life_bars": 0.0}
    return {"n": int(idx.size), "b": b, "t_stat": t_stat,
            "half_life_bars": float(-math.log(2.0) / math.log(abs(phi)))}


# --------------------------------------------------------------------------- #
# Cointegration
# --------------------------------------------------------------------------- #

def engle_granger(y: np.ndarray, x: np.ndarray,
                  spans: Sequence[tuple[int, int]], lags: int) -> dict[str, Any]:
    """Engle-Granger: regress the levels, then test the residual for a root.

    The cointegrating regression carries a constant and the residual test does
    not, which is the convention MacKinnon's published critical values are for
    and the convention :func:`simulate_null` reproduces.

    The residual's standard deviation is reported in basis points because that
    is what a pairs trade would capture: entering at one standard deviation
    from the mean and exiting at the mean earns about ``sd_bp``, once, for the
    round trips of every leg.
    """
    target = np.asarray(y, dtype="float64")
    fit = ols(target, x, constant=True)
    residual = fit["residual"]
    test = adf_segments(residual, spans, lags, constant=False)
    half = ar1_half_life(residual, spans)
    return {
        "n": int(target.size),
        "intercept": fit["intercept"],
        "beta": fit["beta"],
        "r_squared": fit["r_squared"],
        "tau": test["tau"],
        "adf_n": test["n"],
        "residual_sd_bp": float(residual.std(ddof=1) * BP)
        if residual.size > 1 else None,
        "residual_step_bp": float(np.median(np.abs(np.diff(residual))) * BP)
        if residual.size > 1 else None,
        "half_life_bars": half["half_life_bars"],
        "half_life_t_stat": half["t_stat"],
        "residual": residual,
    }


def johansen(levels: np.ndarray, spans: Sequence[tuple[int, int]],
             lags: int) -> dict[str, Any]:
    """Johansen's reduced-rank test, with an unrestricted constant.

    Returns the trace and maximum-eigenvalue statistics at every rank, the
    eigenvalues they come from, and the cointegrating vector belonging to the
    largest one -- normalised on its first element so two runs describe the
    same relationship the same way.

    ``levels_rank`` is the numerical rank of the levels' own covariance after
    the short-run regression. It matters here more than it usually would: five
    of the twelve pairs in this universe are exact triangular functions of
    others, so a triple containing one of those identities has levels that are
    linearly dependent up to quoting noise, and the eigenproblem is then
    singular rather than merely ill-conditioned. Reporting the rank is how
    that shows up as a fact rather than as a crash.
    """
    matrix = np.asarray(levels, dtype="float64")
    if matrix.ndim != 2:
        raise ValueError("johansen needs a (T, m) block of levels")
    m = matrix.shape[1]
    lags = max(0, int(lags))
    idx = valid_rows(spans, lags + 1)
    if idx.size <= (1 + lags * m) + 4 * m:
        return {"n": int(idx.size), "m": m, "trace": None, "max_eigen": None,
                "eigenvalues": None, "levels_rank": None, "vector": None}

    delta = matrix[idx] - matrix[idx - 1]
    lagged = matrix[idx - 1]
    short: list[np.ndarray] = [np.ones((idx.size, 1), dtype="float64")]
    for i in range(1, lags + 1):
        short.append(matrix[idx - i] - matrix[idx - i - 1])
    design = np.hstack(short)

    r0 = _residual_of(delta, design)
    r1 = _residual_of(lagged, design)
    rows = float(idx.size)
    s00 = r0.T @ r0 / rows
    s01 = r0.T @ r1 / rows
    s11 = r1.T @ r1 / rows

    weights, vectors = np.linalg.eigh((s11 + s11.T) / 2.0)
    largest = float(weights.max()) if weights.size else 0.0
    keep = weights > max(largest * 1e-12, 0.0)
    rank = int(keep.sum())
    if rank == 0:
        return {"n": int(idx.size), "m": m, "trace": None, "max_eigen": None,
                "eigenvalues": None, "levels_rank": 0, "vector": None}
    whiten = vectors[:, keep] / np.sqrt(weights[keep])
    try:
        middle = whiten.T @ s01.T @ np.linalg.solve(s00, s01) @ whiten
    except np.linalg.LinAlgError:
        return {"n": int(idx.size), "m": m, "trace": None, "max_eigen": None,
                "eigenvalues": None, "levels_rank": rank, "vector": None}
    middle = (middle + middle.T) / 2.0
    values, directions = np.linalg.eigh(middle)
    order = np.argsort(values)[::-1]
    values = np.clip(values[order], 0.0, 1.0 - 1e-15)
    directions = directions[:, order]

    trace = [float(-rows * float(np.log1p(-values[r:]).sum()))
             for r in range(rank)]
    max_eigen = [float(-rows * math.log1p(-float(values[r])))
                 for r in range(rank)]
    vector = whiten @ directions[:, 0]
    if abs(float(vector[0])) > 1e-12:
        vector = vector / float(vector[0])
    return {
        "n": int(idx.size), "m": m, "levels_rank": rank,
        "eigenvalues": [float(v) for v in values],
        "trace": trace, "max_eigen": max_eigen,
        "vector": [float(v) for v in vector],
        "levels_condition": float(largest / float(weights[keep].min()))
        if rank else None,
    }


# --------------------------------------------------------------------------- #
# The simulated null
# --------------------------------------------------------------------------- #

def simulate_null(seed: int, *, replications: int, length: int, lags: int,
                  widths: Sequence[int]) -> dict[str, Any]:
    """Null distributions for the cointegration statistics, from random walks.

    Under the null nothing cointegrates, so the null sample is independent
    Gaussian random walks put through **the same functions** the data goes
    through. That is the point of simulating rather than tabulating: the null
    cannot drift away from the estimator, because it is the estimator.

    The distribution of a trace statistic depends on the number of common
    trends rather than on the size of the system, so a system of ``m``
    independent walks gives the null for testing rank 0 against more when
    ``m = n - r``. The same simulation therefore serves a pairs-of-pairs test
    at rank 1 and a triple at rank 2.

    Args:
        seed: The experiment's seed. Nothing here is unseeded.
        replications: How many draws. The smallest attainable p-value is
            ``1/(replications + 1)`` and every caller is told when it lands
            there.
        length: Observations per draw. Long enough that the finite-sample
            correction is below the resolution of the simulation, short enough
            that the whole null costs under a minute.
        lags: The lag order, identical to the one used on the data.
        widths: System widths to simulate.

    Returns:
        Sorted null draws per statistic, plus the quantiles a reader would
        compare against a printed table.
    """
    rng = np.random.default_rng(int(seed))
    spans = [(0, int(length))]
    out: dict[str, Any] = {"replications": int(replications),
                           "length": int(length), "lags": int(lags),
                           "seed": int(seed), "widths": list(widths)}
    engle: dict[int, list[float]] = {int(w): [] for w in widths if w >= 2}
    trace: dict[int, list[float]] = {int(w): [] for w in widths}
    maximum: dict[int, list[float]] = {int(w): [] for w in widths}

    for width in widths:
        width = int(width)
        for _ in range(int(replications)):
            walks = np.cumsum(rng.standard_normal((int(length), width)),
                              axis=0)
            if width >= 2:
                fit = engle_granger(walks[:, 0], walks[:, 1:], spans, lags)
                if fit["tau"] is not None:
                    engle[width].append(float(fit["tau"]))
            result = johansen(walks, spans, lags)
            if result["trace"]:
                trace[width].append(float(result["trace"][0]))
                maximum[width].append(float(result["max_eigen"][0]))

    def finish(draws: dict[int, list[float]]) -> dict[str, Any]:
        return {str(width): np.sort(np.asarray(values, dtype="float64"))
                for width, values in draws.items() if values}

    out["engle_granger"] = finish(engle)
    out["trace"] = finish(trace)
    out["max_eigen"] = finish(maximum)
    out["quantiles"] = {
        name: {width: {label: float(np.quantile(draws, q))
                       for label, q in (("1%", 0.01), ("5%", 0.05),
                                        ("10%", 0.10), ("90%", 0.90),
                                        ("95%", 0.95), ("99%", 0.99))}
               for width, draws in block.items()}
        for name, block in (("engle_granger", out["engle_granger"]),
                            ("trace", out["trace"]),
                            ("max_eigen", out["max_eigen"]))}
    return out


def empirical_p(statistic: float | None, draws: np.ndarray | None,
                tail: str) -> dict[str, Any]:
    """A p-value from a simulated null, with its resolution floor flagged.

    ``(1 + #{as extreme}) / (R + 1)`` -- the plus-one form, which is the one
    that cannot return zero. A p-value of zero from a finite simulation is a
    statement the simulation is not entitled to make.
    """
    if statistic is None or draws is None or draws.size == 0:
        return {"p_value": None, P_FLOOR_KEY: False, "null_draws": 0}
    total = int(draws.size)
    if tail == "left":
        extreme = int(np.searchsorted(draws, float(statistic), side="right"))
    else:
        extreme = total - int(np.searchsorted(draws, float(statistic),
                                              side="left"))
    p = (1.0 + extreme) / (total + 1.0)
    return {"p_value": float(p), P_FLOOR_KEY: extreme == 0,
            "null_draws": total}


# --------------------------------------------------------------------------- #
# Currency strength
# --------------------------------------------------------------------------- #

def currency_design(pairs: Sequence[str],
                    currencies: Sequence[str]) -> np.ndarray:
    """The triangular structure of the universe, as a matrix.

    Row per pair, column per currency, ``+1`` for the base and ``-1`` for the
    quote: a pair's log return is its base currency's strength less its
    quote's, which is an identity rather than a model. What makes it a model
    is that eight currencies cannot reproduce twelve independent series, and
    the residual is what is left over.
    """
    index = {name: i for i, name in enumerate(currencies)}
    design = np.zeros((len(pairs), len(currencies)), dtype="float64")
    for row, pair in enumerate(pairs):
        design[row, index[pair[:3]]] += 1.0
        design[row, index[pair[3:]]] -= 1.0
    return design


def currency_strength(returns: np.ndarray, design: np.ndarray
                      ) -> dict[str, Any]:
    """Decompose returns into currency strengths, normalised to sum to zero.

    The design has rank ``currencies - 1`` -- only differences of strengths are
    observable -- so a normalisation is needed and the conventional one is
    that the strengths sum to zero. It is imposed as an extra equation rather
    than by dropping a currency, so no currency is silently made the numeraire.

    Returns:
        The strength series, the fitted returns, the residual, and the share
        of each pair's variance the factors explain. A universe whose pairs
        are exact triangular functions of one another will explain almost all
        of it, and that is a fact about the universe rather than a good model
        fit: twelve series with seven degrees of freedom are not twelve
        independent series.
    """
    values = np.asarray(returns, dtype="float64")
    n_currencies = design.shape[1]
    augmented = np.vstack([design, np.ones((1, n_currencies))])
    strengths = np.empty((values.shape[0], n_currencies), dtype="float64")
    padded = np.hstack([values, np.zeros((values.shape[0], 1))])
    solution, *_ = np.linalg.lstsq(augmented, padded.T, rcond=None)
    strengths[:] = solution.T
    fitted = strengths @ design.T
    residual = values - fitted
    explained: list[float | None] = []
    for column in range(values.shape[1]):
        explained.append(_r_squared(values[:, column], residual[:, column]))
    return {"strengths": strengths, "fitted": fitted, "residual": residual,
            "r_squared": explained}
