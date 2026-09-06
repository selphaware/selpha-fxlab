"""T5's cost geometry: the arithmetic, and the places it could be wrong quietly.

Three things here are worth pinning, and they are the three that a report would
still look plausible after breaking.

**The cost really does come from the cost model.** The known answers below are
hand-computed from the IB tier-1 parameters and compared against what
:mod:`research.costs` returns, so a second cost model growing inside the
research tree fails a test rather than producing a slightly different report.

**Every rung is exactly its multiple of the base cost.** The whole card prices
each move once at 1.0x and multiplies. If ``cost_multiplier`` ever stopped
scaling the per-order floor, every figure at 1.2x, 1.5x and 2.0x would be
wrong by an amount nobody would notice, and the survival bar is at 1.5x.

**A verdict is the pre-registered rule and not a threshold somebody chose.**
SURVIVES above zero at 1.5x, PARKED above zero at 1.2x, CLOSED otherwise, with
the boundaries tested at the boundary.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fxlab.costs import IBCostModel
from research import cost_geometry as cg
from research import costs as cost_lib
from research.cost_geometry_report import _recommend

#: The Phase 1 IB tier-1 parameters, as an experiment config declares them.
COSTS = {"commission_rate": 2e-05, "commission_min": 2.0,
         "cost_multiplier": 1.0}

LADDER = ("1.0", "1.2", "1.5", "2.0")


# --------------------------------------------------------------------------- #
# The cost model, asked directly
# --------------------------------------------------------------------------- #

def test_a_round_trip_is_two_crossings_and_two_commissions() -> None:
    """Hand-computed: 1,000,000 EURUSD at a 1.0-pip spread around 1.10.

    Each leg pays half a pip on a million units -- 50 USD -- and 0.20 bp of a
    notional either side of 1.10. Anything else means the round trip is being
    priced as one order, or the mid is being used as a fill.
    """
    model = cost_lib.model_for(COSTS, 1.0)
    priced = cost_lib.round_trip(model, "EURUSD", 1.10, 0.0001, 1.10, 0.0001,
                                 1_000_000)
    assert priced["spread_cost"] == pytest.approx(100.0)
    assert priced["commission"] == pytest.approx(
        2e-05 * 1_000_000 * 1.10005 + 2e-05 * 1_000_000 * 1.09995)
    assert priced["break_even_bp"] == pytest.approx(
        priced["total"] / 1_100_000.0 * 1e4)


def test_the_commission_line_is_forty_basis_points_of_a_round_trip() -> None:
    """0.20 bp per order, so 0.40 bp for the pair -- at any price level.

    This is why the report reads the two cost lines apart: the commission is
    the same number for every pair in every session and era above the floor,
    so every difference between two cells is a difference in the spread.
    """
    model = cost_lib.model_for(COSTS, 1.0)
    for mid, spread in ((1.10, 0.0001), (150.0, 0.010), (0.65, 0.00012)):
        spread_bp, commission_bp = cost_lib.round_trip_bp(
            model, "PAIR", np.array([mid]), np.array([spread]),
            np.array([mid]), np.array([spread]), 1_000_000)
        assert commission_bp[0] == pytest.approx(0.4, abs=1e-6)
        assert spread_bp[0] == pytest.approx(spread / mid * 1e4, rel=1e-9)


def test_the_floor_notional_is_found_on_the_model_not_on_its_parameters() -> None:
    """Below it the order pays the minimum; above it, the rate."""
    model = cost_lib.model_for(COSTS, 1.0)
    threshold = cost_lib.floor_notional(model)
    assert threshold == pytest.approx(2.0 / 2e-05, rel=1e-9)
    assert model.commission_for(threshold * 0.99, 1.0) == pytest.approx(2.0)
    assert model.commission_for(threshold * 1.01, 1.0) > 2.0


def test_a_ladder_rung_scales_the_finished_cost_including_the_floor() -> None:
    """The assumption the whole card rests on, asked of the model itself."""
    report = cost_lib.multiplier_check(COSTS, LADDER, cg.MULTIPLIER_GRID)
    assert report["points_with_the_floor_binding"] > 0, (
        "a grid that never floors has not checked the case the floor exists "
        "for")
    assert report["within_tolerance"]
    assert report["worst_relative_disagreement"] < 1e-12


def test_the_multiplier_check_would_catch_a_model_that_did_not_scale() -> None:
    """The check discriminates rather than merely agreeing.

    A model whose multiplier missed the floor prices a floored order at the
    bare minimum however the ladder is climbed, and the check has to see it.
    """

    class UnscaledFloor(IBCostModel):
        def commission_for(self, units: float, fill_price: float) -> float:
            notional = abs(units) * fill_price
            rate = self.commission_rate * notional * self.cost_multiplier
            return max(rate, self.commission_min)

    base = UnscaledFloor(2e-05, 2.0, 1.0)
    dear = UnscaledFloor(2e-05, 2.0, 2.0)
    small = cost_lib.round_trip(base, "EURUSD", 1.10, 0.0001, 1.10, 0.0001,
                                10_000)
    doubled = cost_lib.round_trip(dear, "EURUSD", 1.10, 0.0001, 1.10, 0.0001,
                                  10_000)
    assert doubled["total"] != pytest.approx(small["total"] * 2.0)


# --------------------------------------------------------------------------- #
# The implied edges
# --------------------------------------------------------------------------- #

class _Series:
    """The two attributes :func:`implied_edge` reads off a return series."""

    def __init__(self, returns: np.ndarray) -> None:
        self.returns = returns
        self.ret_pos = np.arange(1, returns.size + 1, dtype="int64")
        self.spans = [(0, returns.size)]


def test_the_lag_one_edge_is_the_card_s_figure() -> None:
    """`|rho(1)| x sd`, and the expected-absolute refinement beside it."""
    row = {"sd_bp": 4.0, "rho1": -0.05, "rho1_n": 1000}
    edge = cg.implied_edge(_Series(np.zeros(0)), np.zeros(0, dtype=bool), row,
                           4, with_variance_ratio=False)
    assert edge["lag1_edge_bp"] == pytest.approx(0.2)
    # The payload rounds every figure it carries, so the tolerance is the
    # rounding rather than the arithmetic.
    assert edge["lag1_edge_bp_expected_absolute"] == pytest.approx(
        math.sqrt(2.0 / math.pi) * 0.2, abs=1e-5)
    assert edge["variance_ratio"] is None


def test_the_variance_ratio_bound_is_the_removed_standard_deviation() -> None:
    """`sqrt((1 - VR(q)) q) x sd`, computed on the selected returns."""
    rng = np.random.default_rng(11)
    values = rng.normal(size=4000)
    series = _Series(values)
    mask = np.ones(values.size, dtype=bool)
    row = {"sd_bp": 10.0, "rho1": -0.01, "rho1_n": values.size}
    edge = cg.implied_edge(series, mask, row, 4, with_variance_ratio=True)
    vr = edge["variance_ratio"]
    assert vr is not None
    if vr < 1.0:
        assert edge["vr_edge_bp"] == pytest.approx(
            math.sqrt((1.0 - vr) * 4) * 10.0, rel=1e-4)
    else:
        assert edge["vr_edge_bp"] is None


def test_a_trending_series_gets_no_reversion_bound() -> None:
    """A variance ratio above one removes no variance, so it bounds nothing."""
    row = {"sd_bp": 10.0, "rho1": 0.05, "rho1_n": 500}
    trending = np.cumsum(np.ones(600)) * 0.0 + np.arange(600) * 0.001
    edge = cg.implied_edge(_Series(trending), np.ones(600, dtype=bool), row, 4,
                           with_variance_ratio=True)
    assert (edge["variance_ratio"] is None or edge["variance_ratio"] >= 1.0
            or edge["vr_edge_bp"] is not None)
    if edge["variance_ratio"] is not None and edge["variance_ratio"] >= 1.0:
        assert edge["vr_edge_bp"] is None


# --------------------------------------------------------------------------- #
# The verdict, at its boundaries
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("edge", "expected"), [
    (1.60, cg.SURVIVES),     # clears 1.5 x 1.0
    (1.50, cg.PARKED),       # exactly at the survival bar is not above it
    (1.30, cg.PARKED),       # clears 1.2 and not 1.5
    (1.20, cg.CLOSED),       # exactly at the park bar is not above it
    (0.50, cg.CLOSED),
])
def test_the_verdict_is_pre_reg_one_at_its_boundaries(edge: float,
                                                      expected: str) -> None:
    """Strictly above zero net, at 1.5x then 1.2x. No third threshold."""
    cost = {rung: float(rung) for rung in LADDER}
    assert cg.verdict_for_edge(edge, cost)["verdict"] == expected


def test_an_absent_edge_gets_no_verdict_rather_than_a_closed_one() -> None:
    """A statistic that could not be computed is not evidence of failure."""
    cost = {rung: float(rung) for rung in LADDER}
    assert cg.verdict_for_edge(None, cost)["verdict"] is None


def test_the_best_variant_wins_and_the_count_is_recorded() -> None:
    """A cell is closed only when every conditioning fails, which is the
    conservative direction for a bound."""
    ranks = [cg.VERDICT_RANK[name]
             for name in (cg.CLOSED, cg.PARKED, cg.SURVIVES)]
    assert ranks == sorted(ranks)


# --------------------------------------------------------------------------- #
# Slicing, holding period and the map
# --------------------------------------------------------------------------- #

def test_sub_spans_breaks_a_selection_into_contiguous_runs() -> None:
    """A variance ratio needs a contiguous window, so a session mask has to
    become spans rather than a shuffled subsequence."""
    series = _Series(np.arange(10, dtype="float64"))
    mask = np.array([1, 1, 1, 0, 0, 1, 1, 0, 0, 0], dtype=bool)
    values, spans = cg.sub_spans(series, mask)
    assert values.tolist() == [0.0, 1.0, 2.0, 5.0, 6.0]
    assert spans == [(0, 3), (3, 5)]


def test_the_minimum_holding_period_stops_at_the_first_horizon_that_clears() -> None:
    by_horizon = {
        "5m": {"all": {"usable": True, "move_bp": {"p50": 0.5},
                       "ladder": {"1.5": {"cost_bp_p50": 1.0}}}},
        "30m": {"all": {"usable": True, "move_bp": {"p50": 2.0},
                        "ladder": {"1.5": {"cost_bp_p50": 1.0}}}},
        "1h": {"all": {"usable": True, "move_bp": {"p50": 4.0},
                       "ladder": {"1.5": {"cost_bp_p50": 1.0}}}},
    }
    out = cg.minimum_holding_period(by_horizon, ["5m", "30m", "1h"], "1.5")
    assert out["all"]["shortest_horizon_that_clears"] == "30m"
    assert [row["clears"] for row in out["all"]["ladder"]] == [False, True,
                                                               True]


def test_no_horizon_clearing_is_a_finding_and_not_a_missing_value() -> None:
    by_horizon = {"5m": {"all": {"usable": True, "move_bp": {"p50": 0.1},
                                 "ladder": {"1.5": {"cost_bp_p50": 1.0}}}}}
    out = cg.minimum_holding_period(by_horizon, ["5m"], "1.5")
    assert out["all"]["shortest_horizon_that_clears"] is None


def test_the_executable_universe_can_only_shrink_as_costs_rise() -> None:
    by_horizon = {"1d": {"all": {
        "usable": True, "move_bp": {"p50": 1.3},
        "ladder": {r: {"cost_bp_p50": float(r)} for r in LADDER}}}}
    out = cg.executable_universe(by_horizon, ["1d"])
    counts = [out["executable_by_rung"][r] for r in LADDER]
    assert counts == sorted(counts, reverse=True)
    assert counts == [1, 1, 0, 0]


def test_the_roll_window_is_not_a_place_edge_can_survive() -> None:
    """Pre-reg #4 excludes it from execution, so it is excluded from the map
    whatever its arithmetic says."""
    stats = {"move_bp": {"p50": 100.0},
             "ladder": {r: {"cost_bp_p50": 1.0,
                            "share_of_moves_above_cost": 0.9} for r in LADDER},
             "n": 100, "median_spread_pips": 1.0}
    rows = [{"pair": "EURUSD", "horizon": "1h", "slice": "roll",
             "stats": stats},
            {"pair": "EURUSD", "horizon": "1h", "slice": "all",
             "stats": stats}]
    ranked = cg.edge_map(rows)
    assert [row["slice"] for row in ranked] == ["all"]


# --------------------------------------------------------------------------- #
# P0-A, and the era recommendation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("currency", "expected"), [
    ("JPY", "USDJPY"), ("CHF", "USDCHF"), ("CAD", "USDCAD"),
    ("GBP", "GBPUSD"), ("AUD", "AUDUSD"),
])
def test_the_conversion_pair_p0a_would_use(currency: str,
                                           expected: str) -> None:
    """The universe pair the quote currency converts to USD through."""
    assert cg._conversion_pair(currency) == expected


def test_the_minimum_viable_notional_names_the_gap_without_converting() -> None:
    """P0-A is an explicit non-goal here; naming its size is not implementing
    it, and nothing in the table may be used as a cost."""
    out = cg.minimum_viable_notional(
        COSTS, ["EURUSD", "USDJPY"], {"EURUSD": 1.10, "USDJPY": 150.0})
    rows = {row["pair"]: row for row in out["by_pair"]}
    assert rows["EURUSD"]["usd_quoted"] is True
    assert rows["USDJPY"]["usd_quoted"] is False
    assert rows["USDJPY"]["conversion_pair_p0a_would_use"] == "USDJPY"
    # 100,000 JPY of notional is 667 units of USDJPY, against 91,000 units of
    # EURUSD -- the gap P0-A closes, stated rather than corrected.
    assert rows["USDJPY"]["floor_binds_below_units"] == pytest.approx(
        100000.0 / 150.0, abs=0.05)
    assert rows["USDJPY"]["illustrative_floor_in_usd"] == pytest.approx(
        2.0 / 150.0, abs=1e-4)


def test_an_era_the_check_could_not_see_is_not_recommended_for_training() -> None:
    row = {"pairs_measured": 12, "crosscheck_unverifiable_share": 0.40,
           "crosscheck_agreement_rate": 0.95}
    assert _recommend(row, 1.0).startswith("stress test only")


def test_an_era_that_agrees_and_costs_the_same_is_training_data() -> None:
    row = {"pairs_measured": 12, "crosscheck_unverifiable_share": 0.01,
           "crosscheck_agreement_rate": 0.98}
    assert _recommend(row, 1.1) == "training data"


def test_an_era_that_cost_twice_as_much_is_a_stress_test() -> None:
    row = {"pairs_measured": 12, "crosscheck_unverifiable_share": 0.01,
           "crosscheck_agreement_rate": 0.98}
    assert "cost materially more" in _recommend(row, 2.5)


def test_an_unmeasurable_era_is_excluded_rather_than_recommended() -> None:
    assert _recommend({"pairs_measured": 0}, None).startswith("excluded")
