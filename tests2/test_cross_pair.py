"""T6's cross-pair experiment: the decisions that would fail quietly.

Four of them, and each is a place where the report would still look plausible
after the code broke.

**The identity flag.** Five of the twelve pairs are exact triangular functions
of the others. If the flag stops firing, the cointegration scan's ranked table
fills with arbitrage definitions presented as discoveries, and every number in
it is still correct.

**The two-leg cost.** A relationship pays one round trip per leg, weighted by
the hedge ratio. Pricing it as one leg halves the answer, and halving the
answer is what turns a closed relationship into an open one.

**The confirmation rule.** It is stated in the config before results exist,
which is only worth anything if the code applies the rule that was stated: the
p-value **and** the hedge ratio, in both directions of the tolerance.

**The regime labels.** Terciles come from a trailing volatility computed
strictly before the row they label. A one-row shift makes every regime finding
circular and changes nothing a reader could see.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from research import cross_pair as cp
from research import crossstats as cs


UNIVERSE = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
            "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY"]
CURRENCIES = cp.currencies_of(UNIVERSE)
LADDER = ("1.0", "1.2", "1.5", "2.0")


# --------------------------------------------------------------------------- #
# The triangular structure
# --------------------------------------------------------------------------- #

def test_the_universe_has_seven_degrees_of_freedom() -> None:
    """Twelve pairs, eight currencies, rank seven. Five are determined."""
    summary = cp.identity_summary(UNIVERSE, CURRENCIES)
    assert summary["currencies"] == 8
    assert summary["rank"] == 7
    assert summary["dependent_pairs"] == 5
    assert len(summary["one_spanning_set"]) == 7
    assert len(summary["pairs_it_determines"]) == 5
    assert set(summary["one_spanning_set"]) | set(
        summary["pairs_it_determines"]) == set(UNIVERSE)


@pytest.mark.parametrize("members", [
    ("EURGBP", "EURUSD", "GBPUSD"),
    ("EURJPY", "EURUSD", "USDJPY"),
    ("GBPJPY", "GBPUSD", "USDJPY"),
    ("EURCHF", "EURUSD", "USDCHF"),
    ("AUDJPY", "AUDUSD", "USDJPY"),
])
def test_every_declared_control_really_is_an_identity(members) -> None:
    """The five known-answer controls, checked against the design matrix
    rather than against the comment that declares them."""
    flags = cp.identity_of(members, CURRENCIES)
    assert flags["identity"] is True
    assert flags["rank"] == 2
    assert flags["combination"] is not None
    # +1 on the cross, -1 on each leg, up to the sign of the whole vector.
    weights = sorted(abs(round(float(v), 3)) for v in flags["combination"])
    assert weights == [1.0, 1.0, 1.0]


@pytest.mark.parametrize("members", [
    ("AUDUSD", "NZDUSD", "USDCAD"),
    ("USDJPY", "EURJPY", "GBPJPY"),
    ("EURGBP", "EURCHF", "GBPUSD"),
    ("EURUSD", "GBPUSD"),
    ("USDCHF", "USDJPY"),
])
def test_an_economically_motivated_set_is_not_an_identity(members) -> None:
    """The failure that matters is the other way round: a scan that labelled
    a real relationship an identity would hide it."""
    assert cp.identity_of(members, CURRENCIES)["identity"] is False


def test_a_currency_in_one_pair_is_exactly_determined() -> None:
    """`CAD` and `NZD` appear once each, so their equations are satisfiable
    exactly and those pairs' residuals are zero by construction. Reading a
    residual of zero as a good fit is the mistake this measures away."""
    counts = cp.currency_appearances(UNIVERSE, CURRENCIES)
    assert counts["USD"] == 7
    assert counts["CAD"] == 1
    assert counts["NZD"] == 1
    assert sorted(c for c, n in counts.items() if n == 1) == ["CAD", "NZD"]


# --------------------------------------------------------------------------- #
# The cost of a relationship
# --------------------------------------------------------------------------- #

def _costs(**pairs: float) -> dict:
    return {pair: {"cost_bp": {rung: value * float(rung) for rung in LADDER},
                   "floor_binding_share": 0.0}
            for pair, value in pairs.items()}


def test_a_relationship_pays_one_round_trip_per_leg() -> None:
    """Hand-computed: one unit of `A` against two of `B`, at 1.0 and 2.0 bp,
    is 1.0 + 2 x 2.0 = 5.0 bp of the first leg's notional."""
    priced = cp.two_leg_cost(["A", "B"], [2.0], _costs(A=1.0, B=2.0))
    assert priced["cost_bp"]["1.0"] == pytest.approx(5.0)
    assert priced["cost_bp"]["1.5"] == pytest.approx(7.5)
    assert [leg["weight"] for leg in priced["legs"]] == [1.0, 2.0]


def test_a_negative_hedge_ratio_still_costs_a_round_trip() -> None:
    """The direction of the second leg is a sign, not a discount."""
    positive = cp.two_leg_cost(["A", "B"], [1.5], _costs(A=1.0, B=2.0))
    negative = cp.two_leg_cost(["A", "B"], [-1.5], _costs(A=1.0, B=2.0))
    assert positive["cost_bp"] == negative["cost_bp"]


def test_a_three_leg_relationship_pays_three_round_trips() -> None:
    priced = cp.two_leg_cost(["A", "B", "C"], [1.0, -1.0],
                             _costs(A=1.0, B=1.0, C=1.0))
    assert priced["cost_bp"]["1.0"] == pytest.approx(3.0)


def test_a_missing_leg_is_a_refusal_and_not_a_cheaper_trade() -> None:
    """Dropping a leg whose cost is unknown would price the relationship at
    exactly the wrong number, and in the flattering direction."""
    assert cp.two_leg_cost(["A", "B"], [1.0], _costs(A=1.0)) is None


def test_the_floor_binding_legs_are_named() -> None:
    """At decision D9's notional the per-order minimum binds for part of the
    universe, so a cost quoted without saying where is a cost whose currency
    nobody stated."""
    costs = _costs(A=1.0, B=2.0)
    costs["B"]["floor_binding_share"] = 0.4
    priced = cp.two_leg_cost(["A", "B"], [1.0], costs)
    assert priced["legs_with_a_binding_floor"] == ["B"]


def test_the_break_even_entry_is_the_cost_over_the_amplitude() -> None:
    """A spread of 10 bp against a 4 bp round trip pays at 0.4 sigma; one of
    2 bp against the same round trip needs 2 sigma."""
    generous = cp.cost_verdict(10.0, 1.0,
                               {"cost_bp": {rung: 4.0 for rung in LADDER}})
    assert generous["break_even_entry_sd"] == pytest.approx(0.4)
    assert generous["amplitude_over_cost"]["1.5"] == pytest.approx(2.5)
    assert generous["pays_at_survival_bar"] is True

    tight = cp.cost_verdict(2.0, 1.0,
                            {"cost_bp": {rung: 4.0 for rung in LADDER}})
    assert tight["break_even_entry_sd"] == pytest.approx(2.0)
    assert tight["pays_at_survival_bar"] is False


def test_the_dearest_rung_is_the_dearest_one_the_edge_still_clears() -> None:
    cost = {"cost_bp": {"1.0": 2.0, "1.2": 2.4, "1.5": 3.0, "2.0": 4.0}}
    assert cp.cost_verdict(10.0, 1.0, cost)["dearest_rung_it_pays"] == "2.0"
    # 2.5 bp clears the 2.4 at 1.2x and not the 3.0 at 1.5x.
    assert cp.cost_verdict(2.5, 1.0, cost)["dearest_rung_it_pays"] == "1.2"
    assert cp.cost_verdict(2.1, 1.0, cost)["dearest_rung_it_pays"] == "1.0"
    assert cp.cost_verdict(1.0, 1.0, cost)["dearest_rung_it_pays"] is None


def test_a_relationship_with_no_amplitude_gets_no_verdict() -> None:
    """A statistic that could not be computed is not evidence of failure."""
    assert cp.cost_verdict(None, None, {"cost_bp": {"1.5": 1.0}})[
        "pays_at_survival_bar"] is None


# --------------------------------------------------------------------------- #
# The confirmation rule
# --------------------------------------------------------------------------- #

def _found(p: float, beta: list[float]) -> dict:
    return {"eg_p_value": p, "beta": beta, "half_life_bars": 10.0,
            "residual_sd_bp": 1.0, "n": 1000}


def test_a_window_that_rejects_and_keeps_its_hedge_ratio_confirms() -> None:
    verdict = cp.confirm(_found(0.001, [1.0]), _found(0.01, [1.1]),
                         alpha=0.05, beta_tolerance=2.0)
    assert verdict["verdict"] == cp.CONFIRMED
    assert verdict["beta_held"] is True


def test_a_window_that_does_not_reject_does_not_confirm() -> None:
    verdict = cp.confirm(_found(0.001, [1.0]), _found(0.20, [1.0]),
                         alpha=0.05, beta_tolerance=2.0)
    assert verdict["verdict"] == cp.NOT_CONFIRMED
    assert verdict["rejects"] is False


@pytest.mark.parametrize(("after", "expected"), [
    (1.0, cp.CONFIRMED),          # unchanged
    (1.9, cp.CONFIRMED),          # inside the tolerance
    (2.1, cp.NOT_CONFIRMED),      # doubled and more
    (0.6, cp.CONFIRMED),          # halved, but not quite
    (0.4, cp.NOT_CONFIRMED),      # the tolerance is symmetric in ratio
    (-1.0, cp.NOT_CONFIRMED),     # sign flipped: a different relationship
])
def test_the_hedge_ratio_tolerance_applies_in_both_directions(
        after: float, expected: str) -> None:
    """A hedge ratio that halves has changed as much as one that doubles, and
    one that flips sign is describing a different relationship entirely."""
    verdict = cp.confirm(_found(0.001, [1.0]), _found(0.001, [after]),
                         alpha=0.05, beta_tolerance=2.0)
    assert verdict["verdict"] == expected


def test_a_window_that_does_not_exist_is_not_a_failure() -> None:
    """Ruling R1 costs `AUDUSD` relationships the early window. Counting that
    as a rejection would make an exclusion look like evidence."""
    verdict = cp.confirm(_found(0.001, [1.0]), None, alpha=0.05,
                         beta_tolerance=2.0)
    assert verdict["verdict"] == cp.NO_WINDOW
    assert verdict["verdict"] != cp.NOT_CONFIRMED


# --------------------------------------------------------------------------- #
# Qualification and ranking
# --------------------------------------------------------------------------- #

def _relationship(survives: bool, confirmation: str, early: str,
                  pays: bool, ratio: float = 1.0) -> dict:
    return {
        "survives_correction": survives,
        "confirmation": {cp.W_CONFIRM: {"verdict": confirmation},
                         cp.W_EARLY: {"verdict": early}},
        "cost": {"pays_at_survival_bar": pays,
                 "amplitude_over_cost": {"1.5": ratio}},
    }


def test_all_three_conditions_are_required_and_named_when_they_fail() -> None:
    """A relationship that fails on cost and one that fails on stability are
    different findings, so a single boolean would hide which."""
    good = cp.qualification(_relationship(True, cp.CONFIRMED, cp.CONFIRMED,
                                          True))
    assert good["qualifies"] is True
    assert good["fails_on"] == []

    unstable = cp.qualification(_relationship(True, cp.CONFIRMED,
                                              cp.NOT_CONFIRMED, True))
    assert unstable["qualifies"] is False
    assert unstable["fails_on"] == ["out-of-window"]

    dear = cp.qualification(_relationship(True, cp.CONFIRMED, cp.CONFIRMED,
                                          False))
    assert dear["fails_on"] == ["cost"]

    nothing = cp.qualification(_relationship(False, cp.NOT_CONFIRMED,
                                             cp.NO_WINDOW, False))
    assert nothing["fails_on"] == ["correction", "out-of-window", "cost"]


def test_a_missing_early_window_cannot_be_stable_out_of_window() -> None:
    """The card's rule is that a relationship must hold in **both** untouched
    windows. A window that does not exist is not one it held in."""
    verdict = cp.qualification(_relationship(True, cp.CONFIRMED, cp.NO_WINDOW,
                                             True))
    assert verdict["stable_out_of_window"] is False
    assert verdict["qualifies"] is False


def test_the_ranking_puts_the_qualifying_relationships_first() -> None:
    rows = []
    for survives, confirmation, early, pays, ratio in (
            (False, cp.NOT_CONFIRMED, cp.NOT_CONFIRMED, True, 900.0),
            (True, cp.CONFIRMED, cp.CONFIRMED, False, 0.4),
            (True, cp.CONFIRMED, cp.CONFIRMED, True, 1.2),
            (True, cp.NOT_CONFIRMED, cp.CONFIRMED, True, 50.0)):
        row = _relationship(survives, confirmation, early, pays, ratio)
        row.update(cp.qualification(row))
        rows.append(row)
    ranked = cp.rank_relationships(rows)
    assert ranked[0]["qualifies"] is True
    assert ranked[1]["stable_out_of_window"] is True
    # The spurious 900x ratio is last, because it fails every condition.
    assert ranked[-1]["fails_on"] == ["correction", "out-of-window"]


@pytest.mark.parametrize(("agreement", "label"), [
    (1.00, "STABLE"), (0.90, "STABLE"), (0.89, "MOSTLY-STABLE"),
    (0.75, "MOSTLY-STABLE"), (0.74, "MIXED"), (0.60, "MIXED"),
    (0.59, "UNSTABLE"), (0.0, "UNSTABLE"), (None, None),
])
def test_the_stability_label_is_t4s_scale_and_not_a_new_one(
        agreement, label) -> None:
    """A card that invented its own thresholds would be describing the same
    evidence on a different ruler."""
    assert cp.stability_label(agreement) == label


# --------------------------------------------------------------------------- #
# Regimes, shocks and the calendar
# --------------------------------------------------------------------------- #

def _block(days: int = 400, step_days: int = 1) -> cs.Aligned:
    """A two-pair daily block starting 2015-01-10, for masks and windows."""
    day = 86_400_000_000_000
    start = int(np.datetime64("2015-01-10", "ns").astype("int64"))
    stamps = start + day * step_days * np.arange(days, dtype="int64")
    rng = np.random.default_rng(5)
    returns = rng.standard_normal((days, 2)) * 1e-3
    prices = np.cumsum(returns, axis=0)
    return cs.Aligned(["A", "B"], stamps, returns, prices,
                      cs.spans_from_stamps(stamps, day * step_days))


def test_the_volatility_regime_is_conditioned_and_never_fitted() -> None:
    """The first rows have no trailing estimate, so they get no label. A
    regime that labelled them would be labelling them with themselves."""
    block = _block(300)
    labels = cp.universe_terciles(block, 20)
    assert labels[0] == ""
    assert set(labels[60:]) <= {"low", "mid", "high"}
    counts = {name: int((labels == name).sum()) for name in cp.TERCILES}
    assert min(counts.values()) > 0
    assert max(counts.values()) - min(counts.values()) <= 2


def test_the_shock_mask_drops_exactly_the_declared_days() -> None:
    block = _block(400)
    keep = cp.shock_mask(block, ["2015-01-15", "2015-01-16"])
    assert int((~keep).sum()) == 2
    excluded = block.drop(keep, 86_400_000_000_000)
    assert len(excluded) == len(block) - 2
    # The rows either side are no longer adjacent, so the run splits.
    assert len(excluded.spans) > len(block.spans)


def test_an_empty_shock_list_drops_nothing() -> None:
    block = _block(50)
    assert cp.shock_mask(block, []).all()


@pytest.mark.parametrize(("start", "months", "expected"), [
    ((2015, 1, 1), 12, (2016, 1, 1)),
    ((2015, 1, 31), 1, (2015, 2, 28)),      # clamped to a short month
    ((2016, 1, 31), 1, (2016, 2, 29)),      # and a leap one
    ((2015, 12, 15), 6, (2016, 6, 15)),
    ((2015, 7, 1), 6, (2016, 1, 1)),
])
def test_the_rolling_window_calendar_handles_short_months(
        start, months, expected) -> None:
    """Two-year windows stepped six months walk over February every other
    step, and a day that does not exist is a window that silently moves."""
    assert cp._add_months(dt.date(*start), months) == dt.date(*expected)


def test_the_rolling_windows_step_and_stop_inside_the_data() -> None:
    block = _block(1200)
    windows = cp.rolling_windows(block, window_years=2, step_months=6)
    assert windows
    assert all(end > start for start, end in windows)
    assert all((end - start).days >= 730 for start, end in windows)
    last_stamp = dt.date.fromisoformat(cp._day(block.stamps[-1]))
    assert windows[-1][1] <= last_stamp


def test_a_relationship_key_names_its_members_and_its_horizon() -> None:
    assert cp.relationship_key(["EURUSD", "GBPUSD"], "1h") == \
        "EURUSD+GBPUSD@1h"
