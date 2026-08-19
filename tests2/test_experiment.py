"""Experiment configs, hashing and the survival verdict."""

from __future__ import annotations

import pathlib

import pytest

from research import experiment as exp

GOOD = """
[experiment]
id = "E1"
taskcard = "T1"
entry = "verify2.fixtures.exp_demo:run"
seed = 42
mode = "scoring"
scored = false
rerun_class = "full"

[experiment.params]
n = 3
"""


def _write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    """Write a config and return its path."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_good_config_parses(tmp_path: pathlib.Path) -> None:
    """The happy path, so the failures below mean something."""
    config = exp.load_config(_write(tmp_path, GOOD))
    assert config.experiment_id == "E1"
    assert config.seed == 42
    assert config.params == {"n": 3}
    assert len(config.sha256) == 64


def test_a_missing_seed_is_refused(tmp_path: pathlib.Path) -> None:
    """An experiment without a seed cannot be reproduced."""
    text = GOOD.replace("seed = 42\n", "")
    with pytest.raises(exp.ExperimentError) as caught:
        exp.load_config(_write(tmp_path, text))
    assert caught.value.reason == exp.BAD_EXPERIMENT_CONFIG


def test_a_non_integer_seed_is_refused(tmp_path: pathlib.Path) -> None:
    """A string seed is not a seed."""
    text = GOOD.replace("seed = 42", 'seed = "later"')
    with pytest.raises(exp.ExperimentError):
        exp.load_config(_write(tmp_path, text))


def test_a_scoring_config_may_not_name_a_sealed_date(
        tmp_path: pathlib.Path) -> None:
    """Not even in a comment."""
    text = GOOD + '\n# window ends 2025-06-30\n'
    with pytest.raises(exp.ExperimentError) as caught:
        exp.load_config(_write(tmp_path, text))
    assert caught.value.reason == exp.SEAL_CONFIG_DATE


def test_a_mechanical_config_may_name_the_quarantined_week(
        tmp_path: pathlib.Path) -> None:
    """The live week is 2026 data; naming it is the point of mechanical mode."""
    text = (GOOD.replace('mode = "scoring"',
                         'mode = "mechanical"\ndata_root = "data/live_week"')
            + '\n# live week 2026-08-10\n')
    config = exp.load_config(_write(tmp_path, text))
    assert config.mode == "mechanical"


def test_a_mechanical_experiment_may_not_claim_to_score(
        tmp_path: pathlib.Path) -> None:
    """Ruling A: the quarantine can never produce a scorecard."""
    text = (GOOD.replace('mode = "scoring"',
                         'mode = "mechanical"\ndata_root = "data/live_week"')
            .replace("scored = false", "scored = true"))
    with pytest.raises(exp.ExperimentError) as caught:
        exp.load_config(_write(tmp_path, text))
    assert caught.value.reason == exp.MECHANICAL_NOT_SCORABLE


def test_a_scored_experiment_must_declare_its_cost_model(
        tmp_path: pathlib.Path) -> None:
    """Same cost model for every candidate, declared rather than defaulted."""
    text = GOOD.replace("scored = false", "scored = true")
    with pytest.raises(exp.ExperimentError):
        exp.load_config(_write(tmp_path, text))


def test_a_subset_rerun_needs_its_hash_declared(tmp_path: pathlib.Path) -> None:
    """Ruling D5, enforced at config load as well as at ledger write."""
    text = GOOD.replace('rerun_class = "full"',
                        'rerun_class = "deterministic-subset"')
    with pytest.raises(exp.ExperimentError):
        exp.load_config(_write(tmp_path, text))


def test_the_hash_is_stable_under_key_order(tmp_path: pathlib.Path) -> None:
    """Canonical JSON, so an identical result cannot hash differently."""
    first = exp.result_hash({"a": 1, "b": {"c": 2, "d": 3}})
    second = exp.result_hash({"b": {"d": 3, "c": 2}, "a": 1})
    assert first == second


def test_the_hash_moves_when_the_payload_moves() -> None:
    """A hash that does not notice a changed number verifies nothing."""
    assert exp.result_hash({"a": 1.0}) != exp.result_hash({"a": 1.0000001})


@pytest.mark.parametrize("net_15,net_12,verdict", [
    (10.0, 50.0, "SURVIVES"),
    (-1.0, 50.0, "PARKED"),
    (-1.0, -1.0, "DEAD"),
    (0.0, 5.0, "PARKED"),      # exactly zero at 1.5x does not survive
])
def test_survival_verdict_follows_the_pinned_bar(net_15: float, net_12: float,
                                                 verdict: str) -> None:
    """Pre-reg #1 as pinned: net > 0 at 1.5x survives, at 1.2x parks."""
    ladder = {"1.0": {"net_pnl": 99.0}, "1.2": {"net_pnl": net_12},
              "1.5": {"net_pnl": net_15}, "2.0": {"net_pnl": -99.0}}
    assert exp.verdict_for(ladder) == verdict


def test_ladder_rungs_are_the_pre_registered_four() -> None:
    """1.0, 1.2, 1.5, 2.0 -- no more, no fewer."""
    assert exp.LADDER == ("1.0", "1.2", "1.5", "2.0")
    assert exp.SURVIVAL_BAR == "1.5"


def test_entry_resolution_reports_a_bad_spelling() -> None:
    """``module:function``, or a named refusal."""
    with pytest.raises(exp.ExperimentError):
        exp.resolve_entry("research.spread_session.run")
    assert callable(exp.resolve_entry("research.spread_session:run"))
