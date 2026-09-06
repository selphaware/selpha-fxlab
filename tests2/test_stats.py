"""The EDA estimators, against published values and constructed known answers.

``research.stats`` is written rather than imported, so it has to earn that:
every estimator below is checked either against a value from a printed table
(the chi-squared and normal tails, the Dickey-Fuller behaviour) or against a
series whose answer can be worked out by hand (the variance ratio of an
alternating sequence, the autocorrelation of one, the half-life of a geometric
decay). A test that only asserts the code agrees with itself would leave the
whole battery resting on nothing.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from research import stats


# --------------------------------------------------------------------------- #
# Distribution tails
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("statistic,df,expected", [
    (3.841459, 1, 0.05),
    (5.991465, 2, 0.05),
    (18.307038, 10, 0.05),
    (2.705543, 1, 0.10),
    (23.209251, 10, 0.01),
    (0.0, 3, 1.0),
])
def test_chi2_upper_tail_matches_the_printed_table(statistic: float, df: int,
                                                   expected: float) -> None:
    """The critical values every chi-squared table prints."""
    assert stats.chi2_sf(statistic, df) == pytest.approx(expected, abs=1e-6)


def test_chi2_is_continuous_across_its_two_algorithms() -> None:
    """Series below ``a + 1``, continued fraction above; they must agree."""
    for df in (1, 2, 5, 10, 30):
        edge = df + 2.0
        below = stats.chi2_sf(edge - 1e-7, df)
        above = stats.chi2_sf(edge + 1e-7, df)
        assert abs(below - above) < 1e-7


def test_normal_tails() -> None:
    """Two values anybody can check."""
    assert stats.norm_sf(1.959964) == pytest.approx(0.025, abs=1e-7)
    assert stats.norm_two_sided(1.959964) == pytest.approx(0.05, abs=1e-7)
    assert stats.norm_sf(0.0) == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# Returns and gaps
# --------------------------------------------------------------------------- #

def test_gap_aware_returns_drop_the_pair_that_spans_a_hole() -> None:
    """Four bars with the third an hour late: two returns survive, not three."""
    hour = 3_600_000_000_000
    stamps = np.array([0, hour, 3 * hour, 4 * hour], dtype="int64")
    prices = np.array([1.0, 2.0, 4.0, 8.0], dtype="float64")
    returns, kept = stats.gap_aware_log_returns(prices, stamps, hour)
    assert list(kept) == [True, False, True]
    assert returns == pytest.approx([math.log(2.0), math.log(2.0)])


def test_a_weekend_gap_would_otherwise_dominate_the_tail() -> None:
    """The reason the mask exists, stated as a test.

    A 65-hour gap differenced as if it were one hour produces a return two
    orders of magnitude past anything real, which lands in the same kurtosis.
    """
    hour = 3_600_000_000_000
    stamps = np.array([0, hour, 66 * hour], dtype="int64")
    prices = np.array([1.0, 1.0001, 1.02], dtype="float64")
    returns, _ = stats.gap_aware_log_returns(prices, stamps, hour)
    assert returns.size == 1
    assert returns[0] == pytest.approx(math.log(1.0001))


# --------------------------------------------------------------------------- #
# Moments and tails
# --------------------------------------------------------------------------- #

def test_moments_against_hand_arithmetic() -> None:
    """A four-point sample whose moments are exact."""
    x = np.array([-2.0, -1.0, 1.0, 2.0])
    out = stats.moments(x)
    assert out["n"] == 4
    assert out["mean"] == pytest.approx(0.0)
    assert out["sd"] == pytest.approx(math.sqrt(10.0 / 3.0))
    assert out["skew"] == pytest.approx(0.0)
    # m2 = 2.5, m4 = 8.5 -> 8.5/6.25 - 3 = -1.64
    assert out["excess_kurtosis"] == pytest.approx(-1.64)


def test_a_gaussian_sample_has_tail_ratios_near_one() -> None:
    """The scale the tail ratio is read on: 1.0 is Gaussian."""
    rng = np.random.default_rng(20260906)
    profile = stats.tail_profile(rng.standard_normal(400_000))
    assert profile["tail_ratio_0.99"] == pytest.approx(1.0, abs=0.03)
    assert profile["tail_ratio_0.999"] == pytest.approx(1.0, abs=0.05)


def test_a_fat_tailed_sample_reads_above_one() -> None:
    """A Student-t-like mixture must not read as Gaussian."""
    rng = np.random.default_rng(20260906)
    base = rng.standard_normal(200_000)
    spike = rng.standard_normal(200_000) * 6.0
    mixed = np.where(rng.random(200_000) < 0.02, spike, base)
    profile = stats.tail_profile(mixed)
    assert profile["tail_ratio_0.999"] > 1.3
    assert profile["excess_beyond_4sd"] > 3.0


def test_jarque_bera_does_not_reject_a_gaussian_sample() -> None:
    """A normality statistic that rejects normal data is not one."""
    rng = np.random.default_rng(7)
    out = stats.jarque_bera(rng.standard_normal(50_000))
    assert out["p_value"] > 0.01


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #

def test_autocorrelation_of_an_alternating_series() -> None:
    """+1, -1, +1, ... has rho(1) = -1 and rho(2) = +1, up to the edge term."""
    x = np.array([1.0, -1.0] * 500)
    rho = stats.acf(x, 3)
    assert rho[0] == pytest.approx(-1.0, abs=0.002)
    assert rho[1] == pytest.approx(1.0, abs=0.003)
    assert rho[2] == pytest.approx(-1.0, abs=0.004)


def test_ljung_box_matches_its_definition() -> None:
    """Computed the long way for one small case."""
    rho = [0.2, -0.1]
    n = 100
    expected = n * (n + 2) * (0.2 ** 2 / 99 + 0.1 ** 2 / 98)
    out = stats.ljung_box(rho, n)
    assert out["statistic"] == pytest.approx(expected)
    assert out["p_value"] == pytest.approx(stats.chi2_sf(expected, 2))


def test_variance_ratio_of_white_noise_is_one() -> None:
    """And the robust statistic does not reject it."""
    rng = np.random.default_rng(11)
    out = stats.variance_ratio(rng.standard_normal(200_000), 4)
    assert out["vr"] == pytest.approx(1.0, abs=0.02)
    assert abs(out["z"]) < 3.0


@pytest.mark.parametrize("q", [2, 4, 8, 16])
def test_variance_ratio_standard_error_matches_the_closed_form(q: int) -> None:
    """Under homoskedasticity the asymptotic variance of VR(q) - 1 is
    ``2(2q-1)(q-1) / (3qn)``. The heteroskedasticity-robust estimator must land
    on it for iid data, and this is the check that caught a spurious sqrt(n)
    turning every horizon into a rejection."""
    rng = np.random.default_rng(19)
    n = 200_000
    out = stats.variance_ratio(rng.standard_normal(n), q)
    closed_form = math.sqrt(2.0 * (2 * q - 1) * (q - 1) / (3.0 * q * n))
    assert out["se"] == pytest.approx(closed_form, rel=0.05)


def test_variance_ratio_of_a_perfectly_reverting_series_is_near_zero() -> None:
    """Alternating +1/-1 cancels over two periods, so VR(2) collapses."""
    out = stats.variance_ratio(np.array([1.0, -1.0] * 5_000), 2)
    assert out["vr"] < 0.01
    assert out["z"] is not None and out["z"] < -10.0


def test_variance_ratio_rises_above_one_for_a_trending_series() -> None:
    """An AR(1) with positive phi: VR(2) tends to 1 + phi."""
    rng = np.random.default_rng(3)
    noise = rng.standard_normal(200_000)
    series = np.zeros_like(noise)
    for i in range(1, series.size):
        series[i] = 0.3 * series[i - 1] + noise[i]
    out = stats.variance_ratio(series, 2)
    assert out["vr"] == pytest.approx(1.3, abs=0.05)
    assert out["z"] is not None and out["z"] > 10.0


def test_sign_persistence_of_an_alternating_series() -> None:
    """Never the same sign twice, and the runs statistic says so loudly."""
    out = stats.sign_persistence(np.array([1.0, -1.0] * 500))
    assert out["p_same"] == pytest.approx(0.0)
    assert out["runs_z"] > 10.0


def test_sign_persistence_of_a_clustered_series() -> None:
    """Signs in blocks of ten: persistent, and the runs z goes negative."""
    out = stats.sign_persistence(np.array([1.0] * 10 + [-1.0] * 10) .repeat(1)
                                 .tolist() * 100)
    assert out["p_same"] > 0.85
    assert out["runs_z"] < -10.0


def test_adf_does_not_reject_a_random_walk() -> None:
    """The null is a unit root and a random walk has one."""
    rng = np.random.default_rng(5)
    walk = np.cumsum(rng.standard_normal(20_000))
    out = stats.adf(walk, lags=5)
    assert out["tau"] > stats.ADF_CRITICAL_VALUES["10%"]
    assert out["rejects_unit_root_1pct"] is False


def test_adf_rejects_white_noise() -> None:
    """Which is stationary, and emphatically so at this sample size."""
    rng = np.random.default_rng(5)
    out = stats.adf(rng.standard_normal(20_000), lags=5)
    assert out["tau"] < stats.ADF_CRITICAL_VALUES["1%"]
    assert out["rejects_unit_root_1pct"] is True


def test_half_life_of_a_geometric_decay() -> None:
    """rho_k = 0.5 ** k halves every lag, so the half-life is exactly one."""
    assert stats.decay_half_life([0.5 ** k for k in range(1, 12)]) == \
        pytest.approx(1.0)
    assert stats.decay_half_life([0.9 ** k for k in range(1, 30)]) == \
        pytest.approx(math.log(2) / -math.log(0.9))


def test_half_life_refuses_a_sequence_that_does_not_decay() -> None:
    """A half-life quoted for a rising sequence is a number with no referent."""
    assert stats.decay_half_life([0.1, 0.2, 0.3, 0.4]) is None
    assert stats.decay_half_life([-0.1, -0.2]) is None


# --------------------------------------------------------------------------- #
# Association and multiple testing
# --------------------------------------------------------------------------- #

def test_spearman_is_one_on_a_monotone_transform() -> None:
    """Which Pearson would not be."""
    x = list(range(1, 51))
    y = [v ** 3 for v in x]
    assert stats.spearman(x, y) == pytest.approx(1.0)
    assert stats.spearman(x, [-v for v in y]) == pytest.approx(-1.0)


def test_spearman_handles_ties() -> None:
    """Averaged ranks, so a constant block does not become an ordering."""
    assert stats.spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1.0)


def test_benjamini_hochberg_on_a_worked_example() -> None:
    """Four p-values at alpha = 0.05: the two smallest reject."""
    out = stats.benjamini_hochberg([0.001, 0.008, 0.039, 0.9], alpha=0.05)
    assert out["family_size"] == 4
    assert out["rejected"] == 2
    assert out["q_values"][0] == pytest.approx(0.004)
    assert out["q_values"][3] == pytest.approx(0.9)


def test_benjamini_hochberg_q_values_are_monotone() -> None:
    """Step-up enforcement: a larger p-value can never carry a smaller q."""
    rng = np.random.default_rng(2)
    p = sorted(rng.random(200).tolist())
    q = stats.benjamini_hochberg(p)["q_values"]
    assert all(q[i] <= q[i + 1] + 1e-12 for i in range(len(q) - 1))


def test_benjamini_hochberg_carries_missing_tests_through_as_none() -> None:
    """A test that could not run is not a test that failed to reject."""
    out = stats.benjamini_hochberg([0.01, None, 0.02])
    assert out["family_size"] == 2
    assert out["q_values"][1] is None


# --------------------------------------------------------------------------- #
# Conditioning without leaking
# --------------------------------------------------------------------------- #

def test_trailing_volatility_never_sees_its_own_observation() -> None:
    """The shift is the difference between conditioning and circularity."""
    values = np.arange(1.0, 21.0)
    vol = stats.trailing_volatility(values, window=5)
    assert np.isnan(vol[:5]).all()
    assert vol[5] == pytest.approx(float(np.std(values[0:5], ddof=1)))
    assert vol[19] == pytest.approx(float(np.std(values[14:19], ddof=1)))


def test_trailing_volatility_of_a_spike_lags_the_spike() -> None:
    """A single huge return raises the estimate afterwards, never at it."""
    values = np.zeros(30)
    values[10] = 100.0
    vol = stats.trailing_volatility(values, window=5)
    assert vol[10] == pytest.approx(0.0)
    assert vol[11] > 10.0


def test_tercile_edges_refuse_a_degenerate_sample() -> None:
    """A constant series has no terciles and must not be given three."""
    assert stats.tercile_edges(np.ones(500)) is None
    edges = stats.tercile_edges(np.arange(1000.0))
    assert edges is not None and edges[0] < edges[1]
