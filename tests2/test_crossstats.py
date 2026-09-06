"""T6's cross-series estimators, against systems whose answers are known.

The failure mode this file exists for is a cointegration scan that finds
relationships. A scan that finds nothing is obviously broken; a scan that finds
four hundred is not obviously anything, and the only way to tell the two apart
is to hand it systems whose answer is known in advance.

Four kinds of known answer are used:

**Constructed systems.** A pair built as ``y = 2x + stationary noise`` has a
cointegrating vector and a hedge ratio of 2, and the estimator has to find
both. Two independent random walks have neither, and the estimator has to not
find them.

**A printed table.** The simulated Engle-Granger null is checked against
MacKinnon's published asymptotic critical values. This is the load-bearing
test in the file: it validates the span-aware ADF, the residual convention and
the simulation in one comparison, and if it drifts, every p-value in the T6
scan drifts with it.

**Arithmetic identities.** ``log EURGBP = log EURUSD - log GBPUSD`` is a
definition, so the currency decomposition has to reproduce it exactly, and the
effective-number-of-bets measures have to return ``n`` for an identity
correlation matrix and 1 for a universe moving as one thing.

**Calibration.** A test at the 5% level has to reject 5% of the time when the
null is true. It is the only check that catches a p-value which is ordered
correctly and scaled wrongly.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research import crossstats as cs
from research import stats


# --------------------------------------------------------------------------- #
# Alignment and spans
# --------------------------------------------------------------------------- #

HOUR: int = 3_600_000_000_000


def test_spans_break_where_the_clock_does() -> None:
    """Exactly one bar apart is contiguous; anything else starts a new run."""
    stamps = np.array([0, 1, 2, 5, 6, 7, 8, 20], dtype="int64")
    assert cs.spans_from_stamps(stamps, 1) == [(0, 3), (3, 7), (7, 8)]


def test_a_daily_gap_rule_lets_a_weekend_through() -> None:
    """At the daily grain Friday to Monday is not a hole, so the run holds."""
    stamps = np.array([0, 1, 4, 5], dtype="int64")
    assert cs.spans_from_stamps(stamps, 1, max_gap_ns=4) == [(0, 4)]
    assert cs.spans_from_stamps(stamps, 1, max_gap_ns=2) == [(0, 2), (2, 4)]


def test_valid_rows_never_reach_across_a_hole() -> None:
    """A regression needing three lags cannot start until three bars in."""
    spans = [(0, 10), (10, 14)]
    assert cs.valid_rows(spans, 3).tolist() == [3, 4, 5, 6, 7, 8, 9, 13]
    assert cs.valid_rows([(0, 2)], 3).tolist() == []


class _Series:
    """The four attributes :func:`align` reads off a return series."""

    def __init__(self, stamps: np.ndarray, returns: np.ndarray,
                 mid: np.ndarray) -> None:
        self.ts = stamps
        self.ret_pos = np.arange(stamps.size, dtype="int64")
        self.returns = returns
        self.mid_close = mid


def _series(offsets: list[int], values: list[float]) -> _Series:
    stamps = np.array([o * HOUR for o in offsets], dtype="int64")
    returns = np.array(values, dtype="float64")
    return _Series(stamps, returns, np.exp(np.cumsum(returns) + 0.1))


def test_alignment_intersects_and_never_interpolates() -> None:
    """A bar one pair has and another does not is dropped from both.

    Filling it would invent a return nobody quoted, and every correlation
    downstream would be partly a correlation with an interpolation.
    """
    first = _series([0, 1, 2, 3, 4], [0.1, 0.2, 0.3, 0.4, 0.5])
    second = _series([0, 2, 3, 4, 5], [1.0, 2.0, 3.0, 4.0, 5.0])
    block = cs.align({"A": first, "B": second}, ["A", "B"], HOUR)
    assert block is None  # too few common stamps for MIN_SAMPLE

    long_first = _series(list(range(60)), [0.01] * 60)
    long_second = _series([i for i in range(60) if i != 30], [0.02] * 59)
    block = cs.align({"A": long_first, "B": long_second}, ["A", "B"], HOUR)
    assert block is not None
    assert len(block) == 59
    assert 30 * HOUR not in set(block.stamps.tolist())
    # The hole splits the run rather than being bridged.
    assert block.spans == [(0, 30), (30, 59)]


def test_a_missing_pair_is_a_refusal_and_not_an_empty_block() -> None:
    """A caller that averaged over nothing would report a number for a window
    it never had."""
    assert cs.align({"A": _series([0], [0.0])}, ["A", "B"], HOUR) is None


# --------------------------------------------------------------------------- #
# Correlation geometry
# --------------------------------------------------------------------------- #

def test_effective_bets_are_n_when_nothing_is_correlated() -> None:
    """The identity correlation matrix is n independent bets, on both
    measures. Anything else means the normalisation is wrong."""
    result = cs.effective_bets(np.eye(5))
    assert result["participation_ratio"] == pytest.approx(5.0)
    assert result["entropy_bets"] == pytest.approx(5.0)
    assert result["components_for_90pct"] == 5


def test_effective_bets_collapse_to_one_when_everything_moves_together() -> None:
    """A universe that goes to one is one bet, whatever its size."""
    ones = np.ones((6, 6))
    result = cs.effective_bets(ones)
    assert result["participation_ratio"] == pytest.approx(1.0)
    assert result["entropy_bets"] == pytest.approx(1.0)
    assert result["components_for_90pct"] == 1


def test_a_two_block_universe_clusters_into_its_two_blocks() -> None:
    """Average linkage on the correlation distance, cut where the blocks are."""
    correlation = np.array([
        [1.0, 0.9, 0.1, 0.1],
        [0.9, 1.0, 0.1, 0.1],
        [0.1, 0.1, 1.0, 0.9],
        [0.1, 0.1, 0.9, 1.0]])
    distance = cs.correlation_distance(correlation)
    clusters = cs.average_linkage(distance, ["A", "B", "C", "D"],
                                  threshold=float(np.sqrt(2 * (1 - 0.5))))
    assert clusters == [["A", "B"], ["C", "D"]]


def test_a_generous_threshold_merges_everything_and_a_mean_one_merges_nothing(
) -> None:
    """The cut is the whole of the clustering, so both ends are pinned."""
    correlation = np.full((3, 3), 0.4)
    np.fill_diagonal(correlation, 1.0)
    distance = cs.correlation_distance(correlation)
    assert len(cs.average_linkage(distance, ["A", "B", "C"], 2.0)) == 1
    assert len(cs.average_linkage(distance, ["A", "B", "C"], 0.01)) == 3


def test_the_correlation_test_uses_fishers_transform() -> None:
    """These correlations reach 0.9, where ``rho sqrt(n)`` is not usable."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal(500)
    y = 0.9 * x + 0.43589 * rng.standard_normal(500)
    out = cs.correlation_test(x, y)
    assert out["rho"] == pytest.approx(0.9, abs=0.05)
    assert out["z"] == pytest.approx(math.atanh(out["rho"]) * math.sqrt(497),
                                     rel=1e-9)


# --------------------------------------------------------------------------- #
# Lead-lag
# --------------------------------------------------------------------------- #

def test_a_constructed_lead_is_found_at_its_own_lag_and_nowhere_else() -> None:
    """``y_t = 0.5 x_{t-2}`` plus noise leads by exactly two bars."""
    rng = np.random.default_rng(7)
    n = 4000
    x = rng.standard_normal(n)
    y = np.zeros(n)
    y[2:] = 0.5 * x[:-2] + 0.5 * rng.standard_normal(n - 2)
    spans = [(0, n)]
    rhos = {lag: cs.lead_lag(x, y, spans, lag)["rho"] for lag in range(1, 5)}
    assert rhos[2] == pytest.approx(0.707, abs=0.05)
    for lag in (1, 3, 4):
        assert abs(rhos[lag]) < 0.1


def test_a_lead_pair_never_straddles_a_hole() -> None:
    """Two runs of five, a lag of two: three usable pairs in each."""
    x = np.arange(10, dtype="float64")
    y = np.arange(10, dtype="float64") * 2.0
    earlier, later = cs.lead_pairs(x, y, [(0, 5), (5, 10)], 2)
    assert earlier.tolist() == [0.0, 1.0, 2.0, 5.0, 6.0, 7.0]
    assert later.tolist() == [4.0, 6.0, 8.0, 14.0, 16.0, 18.0]


# --------------------------------------------------------------------------- #
# Unit roots
# --------------------------------------------------------------------------- #

def test_the_span_aware_adf_agrees_with_t4s_on_one_span() -> None:
    """One span is the un-segmented case, so the two must agree exactly.

    T4's estimator is already tested against a hand-computed series; this makes
    the segmented one the same estimator rather than a second one.
    """
    rng = np.random.default_rng(19)
    series = np.cumsum(rng.standard_normal(3000))
    mine = cs.adf_segments(series, [(0, series.size)], 10, constant=True)
    theirs = stats.adf(series, lags=10)
    assert mine["tau"] == pytest.approx(theirs["tau"], rel=1e-9)
    assert mine["gamma"] == pytest.approx(theirs["gamma"], rel=1e-9)


def test_a_random_walk_keeps_its_unit_root_and_noise_does_not() -> None:
    """The direction of the test, on the two cases it exists to separate."""
    rng = np.random.default_rng(23)
    walk = np.cumsum(rng.standard_normal(4000))
    noise = rng.standard_normal(4000)
    assert cs.adf_segments(walk, [(0, 4000)], 4)["tau"] > -3.0
    assert cs.adf_segments(noise, [(0, 4000)], 4)["tau"] < -10.0


def test_the_half_life_of_a_known_process_is_the_one_it_was_built_with() -> None:
    """``e_t = 0.9 e_{t-1} + u`` has a half-life of ``log 2 / log(1/0.9)``."""
    rng = np.random.default_rng(29)
    n = 200_000
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.9 * e[i - 1] + rng.standard_normal()
    result = cs.ar1_half_life(e, [(0, n)])
    assert result["half_life_bars"] == pytest.approx(
        -math.log(2.0) / math.log(0.9), rel=0.05)


def test_a_random_walk_gets_a_long_half_life_and_a_statistic_that_says_so(
) -> None:
    """The trap this estimator sets, pinned so nobody walks into it.

    Least squares is biased downwards on a genuine random walk, so it hands
    back a small negative coefficient and a half-life of a few hundred bars
    for a series with no mean to revert to. The half-life is therefore only
    meaningful where the unit root was rejected, and ``t_stat`` is what says
    whether it was: on a walk it is a couple of units, and on a reverting
    series it is enormous.
    """
    rng = np.random.default_rng(31)
    walk = np.cumsum(rng.standard_normal(2000))
    loose = cs.ar1_half_life(walk, [(0, 2000)])
    assert loose["half_life_bars"] is None or loose["half_life_bars"] > 100.0
    assert abs(loose["t_stat"]) < 3.0

    reverting = np.zeros(2000)
    for i in range(1, 2000):
        reverting[i] = 0.9 * reverting[i - 1] + rng.standard_normal()
    tight = cs.ar1_half_life(reverting, [(0, 2000)])
    assert tight["half_life_bars"] == pytest.approx(
        -math.log(2.0) / math.log(0.9), rel=0.25)
    assert tight["t_stat"] < -10.0


def test_an_explosive_series_has_no_half_life_rather_than_a_negative_one(
) -> None:
    """A positive coefficient is divergence, and quoting a half-life for it
    is how a table ends up with entries nobody notices."""
    values = np.array([1.02 ** i for i in range(500)], dtype="float64")
    assert cs.ar1_half_life(values, [(0, 500)])["half_life_bars"] is None


# --------------------------------------------------------------------------- #
# Cointegration, on systems whose answer is known
# --------------------------------------------------------------------------- #

def _cointegrated(seed: int, n: int = 4000, beta: float = 2.0,
                  noise: float = 0.4) -> tuple[np.ndarray, np.ndarray]:
    """``y = beta x + stationary``: a cointegrating vector by construction."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.standard_normal(n))
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.95 * e[i - 1] + noise * rng.standard_normal()
    return x, beta * x + e


def test_engle_granger_recovers_the_hedge_ratio_it_was_built_with() -> None:
    x, y = _cointegrated(41)
    fit = cs.engle_granger(y, x, [(0, x.size)], 4)
    assert fit["beta"][0] == pytest.approx(2.0, abs=0.05)
    assert fit["tau"] < cs.ENGLE_GRANGER_CRITICAL[2]["1%"]
    assert fit["half_life_bars"] == pytest.approx(
        -math.log(2.0) / math.log(0.95), rel=0.30)


def test_engle_granger_does_not_find_a_relationship_between_two_walks() -> None:
    """The failure that matters: a scan that finds everything."""
    rng = np.random.default_rng(43)
    x = np.cumsum(rng.standard_normal(4000))
    y = np.cumsum(rng.standard_normal(4000))
    fit = cs.engle_granger(y, x, [(0, 4000)], 4)
    assert fit["tau"] > cs.ENGLE_GRANGER_CRITICAL[2]["10%"]


def test_johansen_finds_one_relationship_in_a_system_that_has_one() -> None:
    """Rank 0 is rejected and rank 1 is not, which is the whole test."""
    x, y = _cointegrated(47)
    result = cs.johansen(np.column_stack([y, x]), [(0, x.size)], 4)
    assert result["levels_rank"] == 2
    assert result["trace"][0] > 40.0     # far beyond any tabulated value
    assert result["trace"][1] < 10.0     # and the second rank is not rejected
    # Normalised on its first element, the vector is (1, -beta).
    assert result["vector"][1] == pytest.approx(-2.0, abs=0.1)


def test_johansen_finds_none_in_a_system_that_has_none() -> None:
    rng = np.random.default_rng(53)
    walks = np.cumsum(rng.standard_normal((4000, 2)), axis=0)
    result = cs.johansen(walks, [(0, 4000)], 4)
    assert result["trace"][0] < 20.0


def test_a_linearly_dependent_system_reports_its_rank_rather_than_crashing(
) -> None:
    """Five of the twelve pairs are exact functions of others, so a triple
    containing an identity has singular levels. That has to be a fact in the
    result rather than an exception in the log."""
    rng = np.random.default_rng(59)
    a = np.cumsum(rng.standard_normal(3000))
    b = np.cumsum(rng.standard_normal(3000))
    exact = np.column_stack([a, b, a - b])
    result = cs.johansen(exact, [(0, 3000)], 4)
    assert result["levels_rank"] == 2
    assert result["trace"] is None or len(result["trace"]) == 2


# --------------------------------------------------------------------------- #
# The simulated null, against a printed table
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def null() -> dict:
    """One simulation, shared: it is the slowest thing in this file."""
    return cs.simulate_null(20260906, replications=800, length=1500, lags=1,
                            widths=(1, 2, 3))


def test_the_simulated_null_matches_mackinnons_printed_values(null) -> None:
    """The load-bearing test. If this drifts, every p-value in T6 drifts.

    MacKinnon's asymptotic critical values for the Engle-Granger residual test
    with a constant in the cointegrating regression: -3.34 at 5% for two
    variables, -3.74 for three. The simulation reproduces them from random
    walks through the same code the data goes through, so agreement here says
    the span-aware ADF, the residual convention and the simulation are all the
    ones the table is about.
    """
    for width in (2, 3):
        simulated = null["quantiles"]["engle_granger"][str(width)]
        published = cs.engle_granger_reference(width)
        for level in ("5%", "10%"):
            assert simulated[level] == pytest.approx(published[level],
                                                     abs=0.20), (
                f"width {width} at {level}: simulated {simulated[level]:.3f} "
                f"against published {published[level]:.3f}")


def test_the_trace_null_grows_with_the_number_of_common_trends(null) -> None:
    """No printed table is asserted here, and that is deliberate.

    The Johansen trace statistic's distribution depends on the deterministic
    terms, and the published tables are keyed to variants that are easy to
    quote and easy to quote wrongly. So the trace test is validated by what it
    does -- the constructed systems above, and the calibration below -- and
    only its shape is pinned here: a statistic summing over more common trends
    is stochastically larger, at every quantile, which is the one thing a
    broken rank loop would break.
    """
    for level in (0.50, 0.90, 0.95, 0.99):
        one = float(np.quantile(null["trace"]["1"], level))
        two = float(np.quantile(null["trace"]["2"], level))
        three = float(np.quantile(null["trace"]["3"], level))
        assert one < two < three
    assert float(np.quantile(null["trace"]["1"], 0.95)) > 0.0


def test_the_trace_p_value_is_calibrated_on_fresh_walks(null) -> None:
    """The same calibration check the Engle-Granger side gets, for the test
    whose critical values this file deliberately does not tabulate."""
    rng = np.random.default_rng(103)
    spans = [(0, 1500)]
    rejected = 0
    trials = 150
    for _ in range(trials):
        walks = np.cumsum(rng.standard_normal((1500, 2)), axis=0)
        result = cs.johansen(walks, spans, 1)
        statistic = result["trace"][0] if result["trace"] else None
        p = cs.empirical_p(statistic, null["trace"]["2"], "right")
        rejected += int((p["p_value"] or 1.0) < 0.05)
    assert 0.005 <= rejected / trials <= 0.13, (
        f"{rejected}/{trials} rejected at the 5% level")


def test_a_p_value_cannot_be_zero_and_says_when_it_is_on_the_floor() -> None:
    """A finite simulation is not entitled to claim a p-value of zero."""
    draws = np.arange(100, dtype="float64")
    beyond = cs.empirical_p(-5.0, draws, "left")
    assert beyond["p_value"] == pytest.approx(1.0 / 101.0)
    assert beyond[cs.P_FLOOR_KEY] is True
    middle = cs.empirical_p(50.0, draws, "left")
    assert middle["p_value"] == pytest.approx(52.0 / 101.0)
    assert middle[cs.P_FLOOR_KEY] is False
    right = cs.empirical_p(99.5, draws, "right")
    assert right[cs.P_FLOOR_KEY] is True


def test_the_null_is_calibrated_at_the_level_it_claims(null) -> None:
    """Fresh random walks rejected at the 5% level about 5% of the time.

    Ordering a p-value correctly and scaling it wrongly is the one failure a
    monotone check cannot see.
    """
    rng = np.random.default_rng(101)
    spans = [(0, 1500)]
    rejected = 0
    trials = 200
    for _ in range(trials):
        walks = np.cumsum(rng.standard_normal((1500, 2)), axis=0)
        fit = cs.engle_granger(walks[:, 0], walks[:, 1:], spans, 1)
        p = cs.empirical_p(fit["tau"], null["engle_granger"]["2"], "left")
        rejected += int((p["p_value"] or 1.0) < 0.05)
    assert 0.01 <= rejected / trials <= 0.12, (
        f"{rejected}/{trials} rejected at the 5% level")


# --------------------------------------------------------------------------- #
# Currency strength
# --------------------------------------------------------------------------- #

UNIVERSE = ("EURUSD", "GBPUSD", "EURGBP")
CURRENCIES = ("USD", "EUR", "GBP")


def test_the_design_matrix_is_the_triangular_structure() -> None:
    design = cs.currency_design(UNIVERSE, CURRENCIES)
    assert design[0].tolist() == [-1.0, 1.0, 0.0]     # EUR - USD
    assert design[1].tolist() == [-1.0, 0.0, 1.0]     # GBP - USD
    assert design[2].tolist() == [0.0, 1.0, -1.0]     # EUR - GBP


def test_an_exact_triangular_universe_leaves_no_residual() -> None:
    """``EURGBP = EURUSD - GBPUSD`` is a definition, so the decomposition has
    to reproduce it exactly. A residual here would mean the factorisation is
    losing something a cross rate cannot lose."""
    rng = np.random.default_rng(61)
    eurusd = rng.standard_normal(500) * 1e-4
    gbpusd = rng.standard_normal(500) * 1e-4
    returns = np.column_stack([eurusd, gbpusd, eurusd - gbpusd])
    design = cs.currency_design(UNIVERSE, CURRENCIES)
    result = cs.currency_strength(returns, design)
    assert np.abs(result["residual"]).max() < 1e-12
    for value in result["r_squared"]:
        assert value == pytest.approx(1.0, abs=1e-9)
    # The normalisation is the one that was asked for.
    assert np.abs(result["strengths"].sum(axis=1)).max() < 1e-12


def test_a_universe_with_something_of_its_own_keeps_a_residual() -> None:
    """Break the identity and the residual appears where it was broken."""
    rng = np.random.default_rng(67)
    eurusd = rng.standard_normal(500) * 1e-4
    gbpusd = rng.standard_normal(500) * 1e-4
    own = rng.standard_normal(500) * 1e-4
    returns = np.column_stack([eurusd, gbpusd, eurusd - gbpusd + own])
    result = cs.currency_strength(returns,
                                  cs.currency_design(UNIVERSE, CURRENCIES))
    assert np.abs(result["residual"]).max() > 1e-6
    assert result["r_squared"][2] < 0.99
