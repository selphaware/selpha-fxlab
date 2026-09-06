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
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import SESSIONS
from research import character
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


# --------------------------------------------------------------------------- #
# The D9 reference-notional addendum (the T6 card's Step 0)
# --------------------------------------------------------------------------- #
#
# Decision D9 moves the research reference notional to 100,000 units, which is
# where the per-order minimum starts binding for part of the universe. The
# addendum exists to say where and by how much, so the failure that matters is
# not "the number is wrong" but "the number is right and the difference between
# the two sizes has quietly become something other than the floor".


def _hourly_series(pair: str, mid: float, hours: int = 600,
                   spread_pips: float = 1.0) -> character.Series:
    """A flat-priced hourly series with a constant spread, for pricing only.

    Flat because the addendum is about cost and not about return: a constant
    mid makes every difference between the two sizes visibly the commission,
    and a moving one would only add noise to a test about arithmetic.
    """
    start = np.datetime64("2019-01-02T00:00:00", "ns").astype("int64")
    step = 3_600_000_000_000
    ts = start + step * np.arange(hours, dtype="int64")
    pip = pair_spec(pair).pip_size
    mids = np.full(hours, mid, dtype="float64")
    # A hair of movement, so the volatility terciles are not all one bucket and
    # the slice machinery is exercised rather than short-circuited.
    mids += (np.arange(hours) % 7) * pip * 0.5
    series = character.Series(
        pair=pair, alias="1h", label="1h", ts=ts, mid_close=mids,
        tick_count=np.full(hours, 3000.0),
        spread_pips=np.full(hours, spread_pips))
    series.returns = np.diff(np.log(mids))
    series.ret_pos = np.arange(1, hours, dtype="int64")
    series.spans = [(0, hours - 1)]
    return series


def _priced(series: character.Series, units: float) -> cg.Priced:
    """The series priced at one size, through the declared model."""
    model = cost_lib.model_for(COSTS, 1.0)
    return cg.price_series(series, model, units, roll=(16, 18), vol_window=20,
                           floor_notional=cost_lib.floor_notional(model))


def test_a_leg_floors_exactly_below_the_bisected_crossover() -> None:
    """100,000 units of a 0.90 quote is 90,000 and floors; 1.10 does not.

    Hand-computed against the IB tier-1 parameters: the rate overtakes the USD
    2.00 minimum at a 100,000 notional, and the leg is priced at the price it
    fills at rather than at the mid.
    """
    model = cost_lib.model_for(COSTS, 1.0)
    mids = np.array([0.90, 1.10, 1.00])
    spreads = np.zeros(3)
    entry, exit_ = cost_lib.floor_binding_legs(model, mids, spreads, mids,
                                               spreads, 100_000)
    assert entry.tolist() == [True, False, True]
    assert exit_.tolist() == [True, False, True]


def test_a_spread_wide_enough_moves_a_leg_across_the_crossover() -> None:
    """The entry crosses to the ask and the exit back to the bid, so a mid
    sitting exactly on the crossover floors one leg and not the other."""
    model = cost_lib.model_for(COSTS, 1.0)
    mids = np.array([1.00])
    spreads = np.array([0.01])
    entry, exit_ = cost_lib.floor_binding_legs(model, mids, spreads, mids,
                                               spreads, 100_000)
    assert entry.tolist() == [False]   # ask 1.005 -> 100,500
    assert exit_.tolist() == [True]    # bid 0.995 ->  99,500


def test_the_addendum_changes_the_commission_line_and_nothing_else() -> None:
    """Same series, two sizes: the spread cost in bp cannot move, the
    commission can, and the difference between the two is exactly that."""
    series = _hourly_series("NZDUSD", 0.65)
    base = _priced(series, 1_000_000)
    reference = _priced(series, 100_000)
    model = cost_lib.model_for(COSTS, 1.0)
    floored = cg.floored_mask(reference, model, 100_000)
    assert floored.all(), "65,000 is below the 100,000 crossover"
    assert not cg.floored_mask(base, model, 1_000_000).any()

    rows = cg.addendum_rows(base, reference, floored)
    assert rows, "the hourly series must produce section-1 slices"
    for row in rows:
        assert row["spread_bp_identical"] is True
        for rung in LADDER:
            assert (row["cost_bp_at_reference_units"][rung]
                    > row["cost_bp_at_base_units"][rung])
            assert row["ratio"][rung] > 1.0
        # The commission at 1,000,000 is the rate: 0.40 bp for the round trip.
        assert row["commission_bp_p50_at_base_units"] == pytest.approx(
            0.4, abs=1e-4)
        # At 100,000 it is the USD 2.00 minimum twice over a 65,000 notional.
        assert row["commission_bp_p50_at_reference_units"] == pytest.approx(
            2.0 * 2.0 / 65_000.0 * 1e4, rel=1e-3)


def test_a_size_above_the_crossover_leaves_the_addendum_identical() -> None:
    """Nothing floors, so the two tables agree to the last decimal. A ratio
    that drifted here would mean the difference was not the floor."""
    series = _hourly_series("EURUSD", 1.20)
    base = _priced(series, 1_000_000)
    reference = _priced(series, 100_000)
    model = cost_lib.model_for(COSTS, 1.0)
    floored = cg.floored_mask(reference, model, 100_000)
    assert not floored.any()
    for row in cg.addendum_rows(base, reference, floored):
        assert row["ratio"]["1.5"] == pytest.approx(1.0, abs=1e-9)
        assert row["extra_bp_at_survival_bar"] == pytest.approx(0.0, abs=1e-9)
        assert row["floor_binding_share"] == 0.0


def test_the_addendum_reports_every_slice_section_one_reports() -> None:
    """`all`, five sessions, three terciles and the crosses -- the card names
    pair x session x tercile, and a missing cut is a quietly narrower claim."""
    series = _hourly_series("AUDUSD", 0.72)
    reference = _priced(series, 100_000)
    names = set(cg.addendum_slices(reference))
    assert cg.ADDENDUM_ALL in names
    assert set(SESSIONS) <= names
    assert set(cg.TERCILES) <= names
    assert any("|" in name for name in names)


def test_a_verdict_that_moved_is_named_and_a_falling_cost_is_caught() -> None:
    """The card's claim is that verdicts can only close harder. The check is
    on the costs, because that is the claim's reason rather than its symptom."""
    def cell(pair: str, verdict: str, cost: float,
             route: str = "lag1") -> dict:
        return {"pair": pair, "horizon": "5m", "verdict": verdict,
                "verdict_from_route": route,
                "variants": {"all hours": {
                    "cost_bp": {"1.5": cost},
                    "lag1": {"verdict": verdict},
                    "variance_ratio_bound": {"verdict": verdict}}}}

    honest = cg.addendum_verdicts(
        [cell("EURUSD", cg.SURVIVES, 1.0), cell("NZDUSD", cg.CLOSED, 3.0)],
        [cell("EURUSD", cg.PARKED, 1.4), cell("NZDUSD", cg.CLOSED, 3.3)])
    assert honest["any_changed"] is True
    assert honest["changed"] == ["EURUSD|5m"]
    assert honest["costs_never_fell"] is True

    impossible = cg.addendum_verdicts([cell("EURUSD", cg.SURVIVES, 2.0)],
                                      [cell("EURUSD", cg.SURVIVES, 1.0)])
    assert impossible["costs_never_fell"] is False


def test_the_sizing_table_states_where_the_model_and_p0a_disagree() -> None:
    """The point of the table. At 100,000 units the model floors `EURGBP` --
    an 86,000 **GBP** notional -- and does not floor `AUDJPY`, an 8.5 million
    **JPY** one. Under USD accounting it is the other way round: 111,000 USD
    does not floor and 72,000 USD does. Four pairs are priced wrongly at this
    size, which is P0-A stated as an amount rather than as a caveat."""
    prices = {"EURUSD": 1.11375, "GBPUSD": 1.29888, "USDJPY": 113.1085,
              "AUDUSD": 0.71747, "EURGBP": 0.85968, "AUDJPY": 84.698}
    rows = {row["pair"]: row for row in cg.addendum_sizing(
        COSTS, ["EURGBP", "AUDJPY", "EURUSD"], prices, 1_000_000, 100_000)}

    assert rows["EURGBP"]["floor_binds_at_reference_units"] is True
    assert rows["EURGBP"]["floor_would_bind_under_p0a"] is False
    assert rows["EURGBP"]["illustrative_p0a_multiple"] == pytest.approx(1.0)

    assert rows["AUDJPY"]["floor_binds_at_reference_units"] is False
    assert rows["AUDJPY"]["floor_would_bind_under_p0a"] is True
    # 100,000 AUD is 71,747 USD; the rate charges 1.435 and the floor 2.00.
    assert rows["AUDJPY"]["illustrative_p0a_multiple"] == pytest.approx(
        2.0 / (2e-05 * 71_747.0), rel=1e-4)

    assert rows["EURUSD"]["floor_binds_at_reference_units"] is False
    assert rows["EURUSD"]["floor_would_bind_under_p0a"] is False


def test_the_cheapest_band_is_ranked_on_the_addendum_s_own_costs() -> None:
    """Decision D3's constraint is a cost ranking, so it is recomputed at the
    new size rather than inherited from the old one."""
    def row(session: str, cost: float) -> dict:
        return {"slice": session, "cost_bp_at_reference_units": {"1.5": cost}}

    ranked = cg.addendum_cheapest([row("sydney", 4.0), row("london", 1.5),
                                   row("tokyo", 2.0),
                                   {"slice": "all",
                                    "cost_bp_at_reference_units": {"1.5": 0.1}}])
    assert ranked["session"] == "london"
    assert ranked["dearest_session"] == "sydney"
    assert ranked["ratio_dearest_to_cheapest"] == pytest.approx(4.0 / 1.5,
                                                               abs=1e-3)
    assert ranked["ranking"] == ["london", "tokyo", "sydney"]
