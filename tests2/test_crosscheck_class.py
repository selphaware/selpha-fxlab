"""Ruling R7: density-aware thresholds, the committed classification, the tag.

The tests that matter here are the ones that would catch R7 being applied as
something other than R7 -- a middle-band hour silently thresholded at 1.0 pip,
a thin hour given a verdict, an exempt hour losing its exemption, or an hour
nobody sampled reading back as if it had passed.
"""

from __future__ import annotations

import pathlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research import crosscheck_class as cc
from research import loader as loader_mod


def _row(ticks: int, worst: float, *, roll: bool = False,
         beyond: bool = False, pair: str = "EURUSD",
         date: str = "2016-05-04", hour: int = 9) -> dict:
    """One stored comparison row, shaped like ``oanda.jsonl``'s."""
    return {"pair": pair, "date": date, "hour": hour, "duka_ticks": ticks,
            "abs_worst_pips": worst, "roll_exempt": roll,
            "beyond_threshold": beyond}


# --------------------------------------------------------------------------- #
# The bands and the thresholds
# --------------------------------------------------------------------------- #

def test_bands_are_the_ones_the_ruling_names() -> None:
    """3,000 and 500, inclusive at the lower edge of each band."""
    assert cc.band_of(3000) == "dense"
    assert cc.band_of(2999) == "middle"
    assert cc.band_of(500) == "middle"
    assert cc.band_of(499) == "thin"


def test_dense_band_keeps_the_pinned_threshold() -> None:
    """R7 changed nothing for a dense hour."""
    assert cc.threshold_for(5000, 1.0, 0.4) == pytest.approx(1.0)


def test_middle_band_adds_the_hours_own_median_spread() -> None:
    """Which is the whole amendment."""
    assert cc.threshold_for(1200, 1.0, 3.0) == pytest.approx(4.0)


def test_thin_band_has_no_threshold_at_all() -> None:
    """R7 gives it no verdict rather than a lenient one."""
    assert cc.threshold_for(120, 1.0, 3.0) is None


def test_a_middle_band_hour_without_a_spread_is_an_error() -> None:
    """Defaulting it either way would silently re-apply the flat threshold."""
    with pytest.raises(cc.SpreadNotMeasured):
        cc.threshold_for(1200, 1.0, None)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def test_a_thin_hour_is_unverifiable_however_far_it_disagrees() -> None:
    """20 pips apart on 200 ticks says nothing about either feed."""
    out = cc.classify(_row(200, 20.0, beyond=True), 1.0, 2.5)
    assert out["r7_class"] == cc.CLASS_UNVERIFIABLE
    assert out["r7_threshold_pips"] is None


def test_the_roll_window_stays_exempt() -> None:
    """Pre-reg #4 and #7 survive the amendment untouched."""
    out = cc.classify(_row(5000, 9.0, roll=True), 1.0, 0.3)
    assert out["r7_class"] == cc.CLASS_ROLL_EXEMPT


def test_a_middle_band_hour_inside_its_own_spread_now_passes() -> None:
    """2.5 pips apart in an hour quoting a 3-pip spread is not a disagreement."""
    out = cc.classify(_row(900, 2.5, beyond=True), 1.0, 3.0)
    assert out["r7_class"] == cc.CLASS_PASS
    assert out["r7_threshold_pips"] == pytest.approx(4.0)


def test_a_middle_band_hour_beyond_its_own_spread_stays_blocked() -> None:
    """R7 changed the instrument, not the consequence."""
    out = cc.classify(_row(900, 6.0, beyond=True), 1.0, 3.0)
    assert out["r7_class"] == cc.CLASS_BLOCKED


def test_a_dense_hour_beyond_a_pip_stays_blocked() -> None:
    """The band the pinned threshold was right for keeps it."""
    out = cc.classify(_row(8000, 1.4, beyond=True), 1.0, 0.2)
    assert out["r7_class"] == cc.CLASS_BLOCKED
    assert out["r7_threshold_pips"] == pytest.approx(1.0)


def test_the_pinned_verdict_is_carried_not_overwritten() -> None:
    """An amendment that erases what it amended hides what it changed."""
    out = cc.classify(_row(900, 2.5, beyond=True), 1.0, 3.0)
    assert out["beyond_threshold"] is True
    assert out["r7_class"] == cc.CLASS_PASS


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #

def test_agreement_rate_excludes_unverifiable_hours() -> None:
    """An hour the check could not see is not an hour that agreed."""
    rows = [cc.classify(_row(8000, 0.2), 1.0, 0.2),
            cc.classify(_row(8000, 3.0), 1.0, 0.2),
            cc.classify(_row(100, 9.0), 1.0, None),
            cc.classify(_row(8000, 0.1, roll=True), 1.0, 0.2)]
    summary = cc.summarise(rows, 1.0)
    assert summary["counts"] == {cc.CLASS_PASS: 1, cc.CLASS_BLOCKED: 1,
                                 cc.CLASS_UNVERIFIABLE: 1,
                                 cc.CLASS_ROLL_EXEMPT: 1}
    assert summary["verifiable_hours"] == 2
    assert summary["agreement_rate"] == pytest.approx(0.5)


def test_summary_counts_what_the_amendment_moved() -> None:
    """The against-pinned block is how a reader sees the amendment's effect."""
    rows = [cc.classify(_row(900, 2.5, beyond=True), 1.0, 3.0),
            cc.classify(_row(100, 9.0, beyond=True), 1.0, None),
            cc.classify(_row(8000, 4.0, beyond=True), 1.0, 0.2)]
    against = cc.summarise(rows, 1.0)["against_pinned"]
    assert against["pinned_beyond_threshold"] == 3
    assert against["unblocked_to_pass"] == 1
    assert against["unblocked_to_unverifiable"] == 1
    assert against["r7_blocked"] == 1
    assert against["newly_blocked"] == 0


def test_r7_can_block_an_hour_the_flat_threshold_passed() -> None:
    """It cannot here -- R7's thresholds are never tighter -- and the counter
    exists so that a future band change could not slip past unnoticed."""
    rows = [cc.classify(_row(8000, 0.5), 1.0, 0.2)]
    assert cc.summarise(rows, 1.0)["against_pinned"]["newly_blocked"] == 0


# --------------------------------------------------------------------------- #
# The committed file
# --------------------------------------------------------------------------- #

def test_the_committed_file_round_trips(tmp_path: pathlib.Path) -> None:
    """Every class survives being written and read back."""
    rows = [
        cc.classify(_row(8000, 0.2, date="2016-05-04", hour=9), 1.0, 0.2),
        cc.classify(_row(8000, 4.0, date="2016-05-04", hour=15), 1.0, 0.2),
        cc.classify(_row(100, 9.0, date="2016-05-05", hour=1), 1.0, None),
        cc.classify(_row(8000, 0.1, roll=True, date="2016-05-05", hour=21),
                    1.0, 0.2),
    ]
    document = cc.derive(rows, base_pips=1.0,
                         window=("2005-01-03", "2025-02-28"), roll=(16, 18))
    path = tmp_path / "crosscheck.toml"
    path.write_text(cc.render_toml(document), encoding="utf-8")
    classes = cc.load_classes(path)
    assert classes.classify("EURUSD", "2016-05-04", 9) == cc.CLASS_PASS
    assert classes.classify("EURUSD", "2016-05-04", 15) == cc.CLASS_BLOCKED
    assert classes.classify("EURUSD", "2016-05-05", 1) == cc.CLASS_UNVERIFIABLE
    assert classes.classify("EURUSD", "2016-05-05", 21) == cc.CLASS_ROLL_EXEMPT


def test_an_hour_nobody_sampled_is_unsampled_not_pass(
        tmp_path: pathlib.Path) -> None:
    """The cross-check covers a sample; silence is not corroboration."""
    document = cc.derive([cc.classify(_row(8000, 0.2), 1.0, 0.2)],
                         base_pips=1.0, window=("2005-01-03", "2025-02-28"),
                         roll=(16, 18))
    path = tmp_path / "crosscheck.toml"
    path.write_text(cc.render_toml(document), encoding="utf-8")
    classes = cc.load_classes(path)
    assert classes.classify("EURUSD", "2016-05-04", 10) == cc.CLASS_UNSAMPLED
    assert classes.classify("GBPUSD", "2016-05-04", 9) == cc.CLASS_UNSAMPLED


def test_an_absent_classification_raises_rather_than_reading_empty(
        tmp_path: pathlib.Path) -> None:
    """"Nothing was checked" and "no file" are different claims."""
    with pytest.raises(FileNotFoundError):
        cc.load_classes(tmp_path / "missing.toml")


def test_hours_in_class_enumerates_the_blocked_set(
        tmp_path: pathlib.Path) -> None:
    """The blocked set is per hour, which is what a consumer needs to see."""
    rows = [cc.classify(_row(8000, 4.0, date="2016-05-04", hour=15), 1.0, 0.2),
            cc.classify(_row(8000, 0.2, date="2016-05-04", hour=9), 1.0, 0.2)]
    document = cc.derive(rows, base_pips=1.0,
                         window=("2005-01-03", "2025-02-28"), roll=(16, 18))
    path = tmp_path / "crosscheck.toml"
    path.write_text(cc.render_toml(document), encoding="utf-8")
    classes = cc.load_classes(path)
    assert classes.hours_in_class(cc.CLASS_BLOCKED) == [
        ("EURUSD", "2016-05-04", 15)]


# --------------------------------------------------------------------------- #
# The loader tag
# --------------------------------------------------------------------------- #

def _seed_classes(base: pathlib.Path) -> None:
    """Write a two-hour classification into a throwaway project root."""
    rows = [cc.classify(_row(8000, 0.2, date="2016-05-04", hour=9), 1.0, 0.2),
            cc.classify(_row(8000, 4.0, date="2016-05-04", hour=15), 1.0, 0.2)]
    document = cc.derive(rows, base_pips=1.0,
                         window=("2005-01-03", "2025-02-28"), roll=(16, 18))
    path = base / cc.CLASSES_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cc.render_toml(document), encoding="utf-8")


def test_the_loader_tags_an_hour_with_its_class(tmp_path: pathlib.Path) -> None:
    """The T4 card asks the loader to carry the tag; this is it carrying it."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    _seed_classes(tmp_path)
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=tmp_path)
    assert loader.crosscheck_class("EURUSD", "2016-05-04", 9) == cc.CLASS_PASS
    assert loader.crosscheck_class("EURUSD", "2016-05-04", 15) == cc.CLASS_BLOCKED
    assert loader.crosscheck_class("EURUSD", "2016-05-04", 10) == cc.CLASS_UNSAMPLED


def test_the_loader_tag_does_not_filter(tmp_path: pathlib.Path) -> None:
    """A blocked hour is still served. Tagging and filtering are different
    jobs, and a loader that quietly did the second would make every downstream
    number depend on a decision nobody recorded."""
    partition = (tmp_path / "data" / "research" / "ticks" / "pair=EURUSD"
                 / "date=2016-05-04")
    partition.mkdir(parents=True)
    _seed_classes(tmp_path)
    table = pa.table({"pair": pa.array(["EURUSD"], pa.large_string()),
                      "bid": pa.array([1.1], pa.float64()),
                      "ask": pa.array([1.2], pa.float64())})
    pq.write_table(table, partition / "EURUSD_2016-05-04_15h.parquet")
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=tmp_path)
    served = loader.load_tick_hour("EURUSD", "2016-05-04", 15,
                                   columns=["bid", "ask"])
    assert served is not None and served.num_rows == 1
    assert loader.crosscheck_class("EURUSD", "2016-05-04", 15) == cc.CLASS_BLOCKED


def test_load_tick_hour_is_policed_by_the_seal(tmp_path: pathlib.Path) -> None:
    """The new read is inside the chokepoint, not beside it."""
    from research.seal import SealBreach

    (tmp_path / "data" / "research").mkdir(parents=True)
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=tmp_path)
    with pytest.raises(SealBreach):
        loader.load_tick_hour("EURUSD", "2025-03-01", 9)


def test_load_tick_hour_returns_none_for_an_hour_not_in_the_store(
        tmp_path: pathlib.Path) -> None:
    """None rather than an empty table: "served nothing" and "not stored" are
    different facts and a zero-row table says the first."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=tmp_path)
    assert loader.load_tick_hour("EURUSD", "2016-05-04", 9) is None
