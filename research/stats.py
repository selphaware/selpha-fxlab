"""Deterministic statistics for the EDA battery, in numpy and nothing else.

Every estimator T4 needs is here, implemented against its published definition
rather than imported. Three reasons, in order of how much they matter:

1. **Reproducibility is the gate's hardest check.** A result hash must
   reproduce exactly from a config and a seed. Every function here is a
   closed-form pass over an array in a fixed order, so it does, and it will
   still do so on a machine with a different BLAS or a different library
   version. A third-party estimator that silently changes its default lag rule
   between releases would turn a green gate red for a reason no message names.
2. **The judged surface should be readable.** These are the numbers the whole
   phase reasons from; a reviewer can check the variance-ratio statistic below
   against Lo and MacKinlay in five minutes, and cannot check a call into a
   library whose defaults they would have to go and read.
3. **No new dependency.** ``SPEC2.md`` permits installing ``scipy`` and
   ``statsmodels`` when a card needs them. This card does not: the two things
   they would have brought are a chi-squared tail and an ADF regression, and
   both are below, tested against published values in ``tests2/test_stats.py``.

Conventions that hold throughout: inputs are float64 numpy arrays with the
non-finite values already removed by the caller; every function returns plain
Python floats and ints, so a payload built from them is JSON-plain without
coercion; and a sample too small for an estimator returns ``None`` for it
rather than a number nobody should read.
"""

from __future__ import annotations

import math
from typing import Any, Final, Sequence

import numpy as np

#: ``Phi^-1((1+p)/2)`` for the tail probabilities the report quotes. Tabulated
#: rather than inverted at run time: three constants checkable against any
#: normal table beat a fifty-line inverse nobody will read.
GAUSSIAN_ABS_QUANTILE: Final[dict[str, float]] = {
    "0.99": 2.5758293035489004,
    "0.999": 3.2905267314919255,
    "0.9999": 3.8905918864103585,
}

#: Asymptotic Dickey-Fuller critical values for the constant-without-trend
#: case (MacKinnon 1991/2010, the ``beta_infinity`` row). The samples here run
#: from 5,000 to 750,000 observations, where the finite-sample corrections are
#: smaller than the last digit printed, so the asymptotic row is the honest one
#: to quote and the only one that does not need a response surface.
ADF_CRITICAL_VALUES: Final[dict[str, float]] = {
    "1%": -3.43035, "5%": -2.86154, "10%": -2.56677,
}

#: Below this many observations an estimator returns ``None``. Not a
#: significance rule -- a floor under arithmetic that stops being arithmetic.
MIN_SAMPLE: Final[int] = 32


# --------------------------------------------------------------------------- #
# Distribution tails, without scipy
# --------------------------------------------------------------------------- #

def norm_sf(z: float) -> float:
    """Upper tail of the standard normal, ``P(Z > z)``."""
    return 0.5 * math.erfc(float(z) / math.sqrt(2.0))


def norm_two_sided(z: float) -> float:
    """Two-sided normal p-value for a z statistic."""
    return math.erfc(abs(float(z)) / math.sqrt(2.0))


def _gamma_series(a: float, x: float) -> float:
    """Lower regularized incomplete gamma ``P(a, x)`` by its series."""
    total = term = 1.0 / a
    for n in range(1, 1000):
        term *= x / (a + n)
        total += term
        if abs(term) < abs(total) * 1e-16:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_cf(a: float, x: float) -> float:
    """Upper regularized incomplete gamma ``Q(a, x)`` by its continued fraction.

    The modified Lentz evaluation. Used for ``x >= a + 1``, where the series
    above converges slowly and this converges fast; together they cover the
    whole range to full double precision.
    """
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def chi2_sf(stat: float, df: int) -> float:
    """Upper tail of a chi-squared distribution, ``P(X > stat)``.

    Args:
        stat: The test statistic; negatives return 1.0.
        df: Degrees of freedom, at least 1.
    """
    x, a = float(stat), 0.5 * int(df)
    if df < 1:
        raise ValueError(f"chi2_sf needs df >= 1, got {df}")
    if x <= 0.0:
        return 1.0
    x *= 0.5
    if x < a + 1.0:
        return max(0.0, 1.0 - _gamma_series(a, x))
    return max(0.0, min(1.0, _gamma_cf(a, x)))


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #

def gap_aware_log_returns(prices: np.ndarray, stamps_ns: np.ndarray,
                          step_ns: int) -> tuple[np.ndarray, np.ndarray]:
    """Log returns between consecutive bars, with gapped pairs dropped.

    A bar table has holes in it -- every weekend, every holiday, every hour the
    feed served nothing -- and differencing straight through one produces a
    "return" spanning 65 hours that lands in the same sample as a 5-minute one.
    That single contamination is enough to dominate every kurtosis, every
    autocorrelation at lag 1 and every tail statistic in this report, so a
    consecutive pair is kept only when the two bars really are adjacent.

    Args:
        prices: Bar prices, strictly positive, in table order.
        stamps_ns: Bar open times as int64 epoch nanoseconds, same order.
        step_ns: The timeframe's own step, in nanoseconds.

    Returns:
        ``(returns, kept)`` where ``kept`` is the boolean mask over the
        ``n - 1`` consecutive pairs, so a caller can report what it dropped and
        can align any per-bar covariate to the surviving returns.
    """
    price = np.asarray(prices, dtype="float64")
    stamps = np.asarray(stamps_ns, dtype="int64")
    if price.size < 2:
        return np.zeros(0, dtype="float64"), np.zeros(0, dtype=bool)
    deltas = np.diff(stamps)
    kept = deltas == int(step_ns)
    logp = np.log(price)
    returns = np.diff(logp)[kept]
    return returns.astype("float64", copy=False), kept


# --------------------------------------------------------------------------- #
# Moments and tails
# --------------------------------------------------------------------------- #

def moments(x: np.ndarray) -> dict[str, Any]:
    """Count, mean, standard deviation, skewness and excess kurtosis.

    Sample standard deviation (``ddof=1``); skewness and kurtosis in their
    population (biased) form, which is what "excess kurtosis of 12" means
    everywhere it is quoted and what the Jarque-Bera statistic below expects.
    """
    values = np.asarray(x, dtype="float64")
    n = int(values.size)
    if n < 3:
        return {"n": n, "mean": None, "sd": None, "skew": None,
                "excess_kurtosis": None}
    mean = float(values.mean())
    centred = values - mean
    m2 = float(np.mean(centred ** 2))
    sd = float(values.std(ddof=1))
    if m2 <= 0.0:
        return {"n": n, "mean": mean, "sd": 0.0, "skew": None,
                "excess_kurtosis": None}
    m3 = float(np.mean(centred ** 3))
    m4 = float(np.mean(centred ** 4))
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "skew": m3 / m2 ** 1.5,
        "excess_kurtosis": m4 / m2 ** 2 - 3.0,
    }


def jarque_bera(x: np.ndarray) -> dict[str, Any]:
    """The Jarque-Bera normality statistic and its chi-squared p-value.

    Reported for completeness and read with care. At these sample sizes the
    statistic is enormous for every pair at every horizon and the p-value is
    zero to machine precision, which establishes only that financial returns
    are not Gaussian -- something nobody doubted. The numbers that carry
    information are the skewness and the excess kurtosis it is built from, and
    the report leads with those.
    """
    values = np.asarray(x, dtype="float64")
    n = int(values.size)
    if n < MIN_SAMPLE:
        return {"n": n, "statistic": None, "p_value": None}
    stats = moments(values)
    skew, kurt = stats["skew"], stats["excess_kurtosis"]
    if skew is None or kurt is None:
        return {"n": n, "statistic": None, "p_value": None}
    stat = n / 6.0 * (skew ** 2 + 0.25 * kurt ** 2)
    return {"n": n, "statistic": float(stat),
            "p_value": chi2_sf(stat, 2)}


def tail_profile(x: np.ndarray) -> dict[str, Any]:
    """How much fatter the tails are than a Gaussian with the same variance.

    Two independent readings, because either alone is easy to misread:

    * **quantile ratios** -- the empirical quantile of ``|r|`` divided by the
      Gaussian quantile at the same probability. A value of 1.0 is Gaussian;
      2.0 says the 1-in-1,000 move is twice the size a normal distribution
      would put there;
    * **exceedance counts** -- the observed share of ``|r|`` beyond 4 and 6
      standard deviations against the Gaussian expectation. A ratio here is the
      answer to "how much more often", where the quantile ratio answers "how
      much bigger", and a distribution can be extreme on one and ordinary on
      the other.
    """
    values = np.asarray(x, dtype="float64")
    n = int(values.size)
    if n < MIN_SAMPLE:
        return {"n": n}
    sd = float(values.std(ddof=1))
    absolute = np.abs(values - float(values.mean()))
    out: dict[str, Any] = {"n": n, "sd": sd}
    for label, gaussian in GAUSSIAN_ABS_QUANTILE.items():
        empirical = float(np.quantile(absolute, float(label)))
        out[f"q{label}"] = empirical
        out[f"tail_ratio_{label}"] = (empirical / (gaussian * sd)
                                      if sd > 0 else None)
    for sigmas in (4.0, 6.0):
        observed = float(np.mean(absolute > sigmas * sd)) if sd > 0 else None
        expected = 2.0 * norm_sf(sigmas)
        out[f"share_beyond_{int(sigmas)}sd"] = observed
        out[f"gaussian_share_beyond_{int(sigmas)}sd"] = expected
        out[f"excess_beyond_{int(sigmas)}sd"] = (
            observed / expected if observed is not None and expected > 0
            else None)
    return out


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #

def acf(x: np.ndarray, nlags: int) -> list[float]:
    """Sample autocorrelation at lags ``1..nlags``.

    The standard biased estimator: one denominator for every lag, which is what
    makes the sequence positive semi-definite and what Ljung-Box assumes.
    """
    values = np.asarray(x, dtype="float64")
    n = int(values.size)
    if n <= nlags + 1:
        return []
    centred = values - values.mean()
    denominator = float(np.dot(centred, centred))
    if denominator <= 0.0:
        return [0.0] * nlags
    return [float(np.dot(centred[k:], centred[:-k]) / denominator)
            for k in range(1, nlags + 1)]


def ljung_box(rho: Sequence[float], n: int) -> dict[str, Any]:
    """The Ljung-Box portmanteau statistic over the lags given."""
    m = len(rho)
    if m == 0 or n <= m:
        return {"lags": m, "statistic": None, "p_value": None}
    stat = float(n * (n + 2) * sum(r ** 2 / (n - k)
                                   for k, r in enumerate(rho, 1)))
    return {"lags": m, "statistic": stat, "p_value": chi2_sf(stat, m)}


def variance_ratio(r: np.ndarray, q: int) -> dict[str, Any]:
    """Lo-MacKinlay variance ratio at horizon ``q``, heteroskedasticity-robust.

    ``VR(q) > 1`` says a q-period move is larger than q independent one-period
    moves would be -- returns reinforce, which is what a trend looks like.
    ``VR(q) < 1`` says they cancel, which is what mean reversion looks like.
    The whole profile across ``q`` is the fingerprint the T4 card asks for, and
    it is worth more than any single value because a series can trend at one
    horizon and revert at another.

    The test statistic is Lo and MacKinlay's ``z*``, which is robust to
    heteroskedasticity. The homoskedastic form would reject on volatility
    clustering alone, and FX returns cluster in volatility at every horizon
    measured here -- so the robust form is not a refinement, it is the
    difference between measuring memory and measuring variance.

    Reference: Lo, A. W. and MacKinlay, A. C. (1988), *Stock Market Prices Do
    Not Follow Random Walks*, Review of Financial Studies 1(1), equations
    (9)-(12) and the heteroskedasticity-consistent statistic of section 2.2.
    """
    values = np.asarray(r, dtype="float64")
    n = int(values.size)
    if q < 2 or n < max(MIN_SAMPLE, 2 * q):
        return {"q": int(q), "n": n, "vr": None, "z": None, "p_value": None,
                "se": None}
    mu = float(values.mean())
    centred = values - mu
    var_1 = float(np.dot(centred, centred) / (n - 1))
    if var_1 <= 0.0:
        return {"q": int(q), "n": n, "vr": None, "z": None, "p_value": None}

    # Overlapping q-period sums, with Lo-MacKinlay's unbiasing denominator.
    cumulative = np.concatenate(([0.0], np.cumsum(values)))
    sums = cumulative[q:] - cumulative[:-q]
    m = q * (n - q + 1) * (1.0 - q / n)
    if m <= 0.0:
        return {"q": int(q), "n": n, "vr": None, "z": None, "p_value": None,
                "se": None}
    deviations = sums - q * mu
    var_q = float(np.dot(deviations, deviations) / m)
    vr = var_q / var_1

    # Heteroskedasticity-consistent asymptotic variance.
    squared = centred ** 2
    denominator = float(np.dot(squared, np.ones_like(squared))) ** 2
    if denominator <= 0.0:
        return {"q": int(q), "n": n, "vr": float(vr), "z": None,
                "p_value": None, "se": None}
    theta = 0.0
    for j in range(1, q):
        numerator = float(np.dot(squared[j:], squared[:-j]))
        delta = numerator / denominator
        theta += ((2.0 * (q - j)) / q) ** 2 * delta
    if theta <= 0.0:
        return {"q": int(q), "n": n, "vr": float(vr), "z": None,
                "p_value": None, "se": None}
    # theta is already the asymptotic variance of ``VR - 1``: its denominator
    # is the square of a sum over n terms, so the sample size is inside it. An
    # extra sqrt(n) here is the classic way to turn this statistic into one
    # that grows with the sample and rejects everything -- checked against the
    # homoskedastic closed form 2(2q-1)(q-1)/(3qn) in tests2/test_stats.py.
    z = (vr - 1.0) / math.sqrt(theta)
    return {"q": int(q), "n": n, "vr": float(vr), "z": float(z),
            "p_value": norm_two_sided(z), "se": math.sqrt(theta)}


def sign_persistence(r: np.ndarray) -> dict[str, Any]:
    """How often a return keeps the sign of the one before it.

    Two readings of the same sequence. ``p_same`` is the effect size a strategy
    would trade -- 0.51 is a coin with a lean, 0.55 is a business -- and the
    Wald-Wolfowitz runs statistic is the significance test that accounts for an
    unbalanced sign split, which a naive binomial on ``p_same`` does not. A
    negative runs z means fewer runs than chance: signs cluster, which is
    persistence.
    """
    values = np.asarray(r, dtype="float64")
    signs = np.sign(values)
    keep = signs != 0
    signs = signs[keep]
    n = int(signs.size)
    if n < MIN_SAMPLE:
        return {"n": n, "p_same": None, "z": None, "p_value": None,
                "runs_z": None, "runs_p_value": None}
    same = signs[1:] == signs[:-1]
    pairs = int(same.size)
    p_same = float(same.mean())
    z = (p_same - 0.5) / math.sqrt(0.25 / pairs) if pairs else None

    positives = int(np.count_nonzero(signs > 0))
    negatives = n - positives
    runs = int(1 + np.count_nonzero(signs[1:] != signs[:-1]))
    runs_z = runs_p = None
    if positives > 0 and negatives > 0:
        expected = 2.0 * positives * negatives / n + 1.0
        variance = (2.0 * positives * negatives
                    * (2.0 * positives * negatives - n)
                    / (n * n * (n - 1.0)))
        if variance > 0:
            runs_z = (runs - expected) / math.sqrt(variance)
            runs_p = norm_two_sided(runs_z)
    return {"n": n, "pairs": pairs, "p_same": p_same,
            "z": float(z) if z is not None else None,
            "p_value": norm_two_sided(z) if z is not None else None,
            "runs": runs,
            "runs_z": float(runs_z) if runs_z is not None else None,
            "runs_p_value": runs_p}


def adf(y: np.ndarray, lags: int = 10) -> dict[str, Any]:
    """Augmented Dickey-Fuller test, constant and no trend.

    Regresses ``dy_t`` on a constant, ``y_{t-1}`` and ``lags`` lagged
    differences, and reports ``tau = gamma_hat / se(gamma_hat)`` against the
    asymptotic critical values in :data:`ADF_CRITICAL_VALUES`. The null is a
    unit root, so a tau **below** the critical value rejects it.

    The lag order is fixed rather than selected. Schwert's rule would put ~110
    lags on a 750,000-observation series -- a regression matrix of several
    hundred megabytes to answer a question whose answer is already obvious at
    ten, and a number that would then depend on the sample length in a way no
    reader could see. Ten is stated, applied everywhere, and reported.

    This is the sanity check the T4 card asks for and nothing more: with
    hundreds of thousands of observations it has the power to reject on
    economically meaningless departures, so the report reads the sign and the
    magnitude, not a verdict.
    """
    values = np.asarray(y, dtype="float64")
    lags = max(0, int(lags))
    n_raw = int(values.size)
    if n_raw < max(MIN_SAMPLE, 4 * (lags + 3)):
        return {"n": n_raw, "lags": lags, "tau": None, "gamma": None,
                "p_reject_1pct": None}
    dy = np.diff(values)
    rows = dy.size - lags
    if rows <= lags + 3:
        return {"n": n_raw, "lags": lags, "tau": None, "gamma": None,
                "p_reject_1pct": None}
    target = dy[lags:]
    columns = [np.ones(rows, dtype="float64"), values[lags:-1]]
    for i in range(1, lags + 1):
        columns.append(dy[lags - i:-i] if i else dy[lags:])
    design = np.column_stack(columns)
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design @ coefficients
    dof = rows - design.shape[1]
    if dof <= 0:
        return {"n": n_raw, "lags": lags, "tau": None, "gamma": None,
                "p_reject_1pct": None}
    sigma2 = float(np.dot(residuals, residuals) / dof)
    try:
        covariance = np.linalg.inv(design.T @ design) * sigma2
    except np.linalg.LinAlgError:
        return {"n": n_raw, "lags": lags, "tau": None, "gamma": None,
                "p_reject_1pct": None}
    gamma = float(coefficients[1])
    se = math.sqrt(max(0.0, float(covariance[1, 1])))
    tau = gamma / se if se > 0 else None
    return {
        "n": int(rows), "lags": lags, "gamma": gamma,
        "tau": float(tau) if tau is not None else None,
        "rejects_unit_root_1pct": (None if tau is None
                                   else bool(tau < ADF_CRITICAL_VALUES["1%"])),
        "critical_1pct": ADF_CRITICAL_VALUES["1%"],
    }


def decay_half_life(values: Sequence[float]) -> float | None:
    """Half-life in lags of a decaying autocorrelation sequence.

    Fits ``log rho_k = a + b k`` by least squares over the leading run of
    strictly positive values, and returns ``-log 2 / b``. Returns ``None`` when
    the sequence does not decay -- a half-life quoted for a series that is not
    decaying is a number with no referent, and returning one anyway is how a
    table of half-lives ends up containing negative entries nobody notices.
    """
    array = np.asarray(list(values), dtype="float64")
    positive = 0
    for value in array:
        if value <= 0.0:
            break
        positive += 1
    if positive < 3:
        return None
    lags = np.arange(1, positive + 1, dtype="float64")
    logs = np.log(array[:positive])
    slope = float(np.polyfit(lags, logs, 1)[0])
    if slope >= 0.0:
        return None
    return float(-math.log(2.0) / slope)


# --------------------------------------------------------------------------- #
# Association
# --------------------------------------------------------------------------- #

def pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    """Pearson correlation, or ``None`` when either side is constant."""
    x = np.asarray(a, dtype="float64")
    y = np.asarray(b, dtype="float64")
    if x.size != y.size or x.size < 3:
        return None
    x = x - x.mean()
    y = y - y.mean()
    denominator = math.sqrt(float(np.dot(x, x)) * float(np.dot(y, y)))
    if denominator <= 0.0:
        return None
    return float(np.dot(x, y) / denominator)


def _ranks(x: np.ndarray) -> np.ndarray:
    """Average ranks, ties shared, so Spearman is the Pearson of these."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(x.size, dtype="float64")
    ranks[order] = np.arange(1, x.size + 1, dtype="float64")
    values = x[order]
    start = 0
    for index in range(1, x.size + 1):
        if index == x.size or values[index] != values[start]:
            if index - start > 1:
                ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Spearman rank correlation, ties averaged."""
    x = np.asarray(list(a), dtype="float64")
    y = np.asarray(list(b), dtype="float64")
    if x.size != y.size or x.size < 3:
        return None
    return pearson(_ranks(x), _ranks(y))


# --------------------------------------------------------------------------- #
# Multiple testing
# --------------------------------------------------------------------------- #

def benjamini_hochberg(p_values: Sequence[float],
                       alpha: float = 0.05) -> dict[str, Any]:
    """Benjamini-Hochberg step-up FDR control over one family of tests.

    The T4 card requires a false-discovery correction wherever p-values are
    reported across pairs and horizons, and requires it stated. BH rather than
    Bonferroni because the family here is 12 pairs times 5 horizons of tests on
    overlapping data, where control of the expected false-discovery proportion
    is the honest target and family-wise error is a target nothing would
    survive.

    Args:
        p_values: One family. ``None`` entries are carried through as ``None``
            and are not counted in the family size -- a test that could not run
            is not a test that failed to reject.
        alpha: The FDR level.

    Returns:
        ``q_values`` aligned to the input, the ``threshold`` p-value below
        which the family rejects, how many rejected, and the family size the
        report must state.
    """
    values = list(p_values)
    indexed = [(i, float(p)) for i, p in enumerate(values) if p is not None]
    m = len(indexed)
    q_values: list[float | None] = [None] * len(values)
    if m == 0:
        return {"family_size": 0, "alpha": alpha, "threshold": None,
                "rejected": 0, "q_values": q_values}
    indexed.sort(key=lambda item: (item[1], item[0]))
    running = 1.0
    for rank in range(m, 0, -1):
        index, p = indexed[rank - 1]
        running = min(running, p * m / rank)
        q_values[index] = running
    threshold = None
    rejected = 0
    for rank in range(m, 0, -1):
        _index, p = indexed[rank - 1]
        if p <= alpha * rank / m:
            threshold = p
            rejected = rank
            break
    return {"family_size": m, "alpha": alpha, "threshold": threshold,
            "rejected": rejected, "q_values": q_values}


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

def quantiles(x: np.ndarray, probabilities: Sequence[float]) -> list[float]:
    """Empirical quantiles, linear interpolation, in the order asked for."""
    values = np.asarray(x, dtype="float64")
    if values.size == 0:
        return [float("nan")] * len(probabilities)
    return [float(np.quantile(values, float(p))) for p in probabilities]


def tercile_edges(x: np.ndarray) -> tuple[float, float] | None:
    """The 1/3 and 2/3 quantiles, or ``None`` when the sample is too small."""
    values = np.asarray(x, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size < 3 * MIN_SAMPLE:
        return None
    low, high = np.quantile(values, [1.0 / 3.0, 2.0 / 3.0])
    if not (float(low) < float(high)):
        return None
    return float(low), float(high)


def trailing_volatility(r: np.ndarray, window: int) -> np.ndarray:
    """Standard deviation of the ``window`` returns **ending before** each one.

    Strictly backward-looking, and shifted by one so the value at ``t`` uses
    ``r[t-window..t-1]`` and never ``r[t]``. That shift is the difference
    between a conditioning variable and a leak: bucketing a return by a
    volatility estimate that contains it would put the largest returns in the
    highest bucket by construction and make every regime finding circular.

    Returns:
        An array the length of ``r``, ``NaN`` where there is not enough
        history.
    """
    values = np.asarray(r, dtype="float64")
    n = values.size
    out = np.full(n, np.nan, dtype="float64")
    if n <= window or window < 2:
        return out
    squares = np.concatenate(([0.0], np.cumsum(values ** 2)))
    sums = np.concatenate(([0.0], np.cumsum(values)))
    # Window ending at index t-1 inclusive: [t-window, t-1].
    idx = np.arange(window, n)
    total = sums[idx] - sums[idx - window]
    total_sq = squares[idx] - squares[idx - window]
    variance = (total_sq - total * total / window) / (window - 1)
    out[idx] = np.sqrt(np.maximum(variance, 0.0))
    return out
