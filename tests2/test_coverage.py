"""The T1 coverage survey: the rules that turn probes into a verdict.

Every rule the card asks for is checked against a hand-built series whose right
answer is obvious by eye, so a change in behaviour shows up as a disagreement
with arithmetic rather than as a slightly different report.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from research import coverage
from research.coverage_probe import (PROBE_DATA, PROBE_EMPTY, PROBE_ERROR,
                                     PROBE_MISSING, ProbeKey, ProbeRecord,
                                     Pacer, first_pass_keys, probe_index,
                                     quality_targets, read_probes,
                                     trading_days, write_parquet)


# --------------------------------------------------------------------------- #
# The trading-day calendar
# --------------------------------------------------------------------------- #

def test_trading_days_excludes_the_weekend_at_the_survey_hour() -> None:
    """13:00 UTC lands inside the FX week Monday to Friday and nowhere else."""
    days = trading_days(dt.date(2024, 1, 1), dt.date(2024, 1, 14), 13)
    assert dt.date(2024, 1, 6) not in days     # Saturday
    assert dt.date(2024, 1, 7) not in days     # Sunday
    assert dt.date(2024, 1, 5) in days         # Friday
    assert dt.date(2024, 1, 8) in days         # Monday
    assert len(days) == 10


def test_trading_days_is_derived_not_hardcoded_to_weekdays() -> None:
    """At 22:00 UTC the FX week already includes Sunday evening in New York.

    This is the reason the calendar goes through ``is_market_open`` rather than
    ``weekday() < 5``: the two agree at 13:00 and disagree at the week open,
    and only one of them is right.
    """
    days = trading_days(dt.date(2024, 1, 6), dt.date(2024, 1, 8), 22)
    assert dt.date(2024, 1, 7) in days         # Sunday 17:00 New York
    assert dt.date(2024, 1, 6) not in days     # Saturday


def test_first_pass_interleaves_pairs_within_a_day() -> None:
    """A half-finished sweep must leave every pair half done, not six done."""
    keys = first_pass_keys(["EURUSD", "GBPUSD"], dt.date(2024, 1, 2),
                           dt.date(2024, 1, 3), 13)
    assert [k.pair for k in keys] == ["EURUSD", "GBPUSD", "EURUSD", "GBPUSD"]
    assert [k.date for k in keys[:2]] == ["2024-01-02", "2024-01-02"]


# --------------------------------------------------------------------------- #
# Checkpoint handling
# --------------------------------------------------------------------------- #

def test_read_probes_drops_a_truncated_final_line(tmp_path: pathlib.Path) -> None:
    """A process killed mid-write must not cost the whole checkpoint."""
    path = tmp_path / "probes.jsonl"
    path.write_text('{"pair":"EURUSD","date":"2024-01-02","hour":13}\n'
                    '{"pair":"EURUSD","date":"2024-01-0',
                    encoding="utf-8")
    records = read_probes(path)
    assert len(records) == 1
    assert records[0]["date"] == "2024-01-02"


def test_dedupe_keeps_the_last_record_for_an_identity() -> None:
    """Re-probing an error can only help if the later record wins."""
    records = [
        {"pair": "EURUSD", "date": "2024-01-02", "hour": 13, "kind": PROBE_ERROR},
        {"pair": "EURUSD", "date": "2024-01-02", "hour": 13, "kind": PROBE_DATA},
    ]
    index = coverage.dedupe(records)
    assert len(index) == 1
    assert index[("EURUSD", "2024-01-02", 13)]["kind"] == PROBE_DATA


def test_probe_index_is_the_resume_key() -> None:
    """Resumability is exactly this set membership."""
    record = ProbeRecord(ProbeKey("EURUSD", "2024-01-02", 13), PROBE_DATA,
                         200, 1234, 99, 1, "first")
    assert probe_index([record.to_dict()]) == {("EURUSD", "2024-01-02", 13)}


def test_probe_record_omits_empty_fields() -> None:
    """Sixty thousand records live in one file; absent fields are not written."""
    record = ProbeRecord(ProbeKey("EURUSD", "2024-01-02", 13), PROBE_DATA,
                         200, 10, 5, 1, "first").to_dict()
    assert "detail" not in record
    assert record["status"] == 200


# --------------------------------------------------------------------------- #
# The series and the rules
# --------------------------------------------------------------------------- #

def _index(pair: str, series: list[tuple[str, str]], hour: int = 13):
    """Build a probe index from ``(date, kind)`` pairs."""
    return {(pair, date, hour): {"pair": pair, "date": date, "hour": hour,
                                 "kind": kind}
            for date, kind in series}


def test_pair_series_marks_days_nobody_probed() -> None:
    """A day missing from the file is a hole in the survey, not in the feed."""
    days = trading_days(dt.date(2024, 1, 2), dt.date(2024, 1, 4), 13)
    index = _index("EURUSD", [("2024-01-02", PROBE_DATA)])
    series = coverage.pair_series(index, "EURUSD", days, 13)
    assert series == [("2024-01-02", PROBE_DATA), ("2024-01-03", "unprobed"),
                      ("2024-01-04", "unprobed")]
    assert coverage.counts_of(series)["unprobed"] == 2


def test_recommended_start_skips_an_island_of_early_coverage() -> None:
    """Three early data days followed by a long absence are not a start date.

    The near window is what rejects them: the far window alone would accept the
    island once enough good years followed it.
    """
    series = ([(f"2005-01-{d:02d}", PROBE_DATA) for d in (3, 4, 5)]
              + [(f"2005-02-{d:02d}", PROBE_MISSING) for d in range(1, 21)]
              + [(f"2005-03-{d:02d}", PROBE_DATA) for d in range(1, 21)])
    start = coverage.recommended_start(series, fraction=0.95, window=10)
    assert start["first_data_date"] == "2005-01-03"
    assert start["date"] == "2005-03-01"
    assert start["near_fraction"] == 1.0


def test_recommended_start_requires_the_far_window_too() -> None:
    """A clean run of ten days is not a start if the rest of history is holed."""
    series = ([(f"2005-01-{d:02d}", PROBE_DATA) for d in range(1, 11)]
              + [(f"2005-02-{d:02d}", PROBE_MISSING) for d in range(1, 21)])
    start = coverage.recommended_start(series, fraction=0.95, window=5)
    assert start["date"] is None


def test_recommended_start_needs_data_on_the_day_itself() -> None:
    """The recommendation is a date research can start on, so it has data."""
    series = [("2005-01-03", PROBE_EMPTY)] + [
        (f"2005-01-{d:02d}", PROBE_DATA) for d in range(4, 25)]
    start = coverage.recommended_start(series, fraction=0.95, window=5)
    assert start["date"] == "2005-01-04"


def test_recommended_start_ignores_unprobed_days_in_its_denominators() -> None:
    """An unprobed day must not read as an absent one and move the verdict."""
    series = [("2005-01-03", "unprobed")] + [
        (f"2005-01-{d:02d}", PROBE_DATA) for d in range(4, 25)]
    start = coverage.recommended_start(series, fraction=0.95, window=5)
    assert start["date"] == "2005-01-04"
    assert start["far_fraction"] == 1.0


def test_recommended_start_on_an_empty_series() -> None:
    """No probes means no recommendation, not a crash."""
    start = coverage.recommended_start([], fraction=0.95, window=5)
    assert start["date"] is None
    assert start["first_data_date"] is None


def test_holes_are_maximal_runs_at_or_above_the_minimum() -> None:
    """A four-day run is not material; a five-day run is."""
    series = ([("2010-01-01", PROBE_DATA)]
              + [(f"2010-01-{d:02d}", PROBE_MISSING) for d in range(2, 6)]
              + [("2010-01-06", PROBE_DATA)]
              + [(f"2010-01-{d:02d}", PROBE_MISSING) for d in range(7, 12)]
              + [("2010-01-12", PROBE_DATA)])
    found = coverage.holes(series, minimum=5, since=None)
    assert len(found) == 1
    assert found[0]["start"] == "2010-01-07"
    assert found[0]["end"] == "2010-01-11"
    assert found[0]["trading_days"] == 5


def test_holes_record_their_composition() -> None:
    """A run of 'empty' is a closed market; a run of 'missing' is absent data."""
    series = [(f"2010-01-{d:02d}", PROBE_EMPTY) for d in range(1, 6)]
    found = coverage.holes(series, minimum=5, since=None)
    assert found[0]["composition"] == {PROBE_EMPTY: 5}


def test_holes_before_the_recommended_start_are_not_reported() -> None:
    """The card asks for holes between the start date and the window end."""
    series = ([(f"2005-01-{d:02d}", PROBE_MISSING) for d in range(1, 11)]
              + [(f"2005-02-{d:02d}", PROBE_DATA) for d in range(1, 11)])
    found = coverage.holes(series, minimum=5, since="2005-02-01")
    assert found == []


def test_by_year_counts_every_kind() -> None:
    """Per-year counts are how a reviewer sees where history thins out."""
    series = [("2005-01-03", PROBE_MISSING), ("2006-01-03", PROBE_DATA)]
    years = coverage.by_year(series)
    assert years["2005"][PROBE_MISSING] == 1
    assert years["2006"][PROBE_DATA] == 1
    assert years["2005"][PROBE_DATA] == 0


# --------------------------------------------------------------------------- #
# Refinement and spot checks
# --------------------------------------------------------------------------- #

def test_quality_targets_are_the_ends_and_the_middle() -> None:
    """Deterministic and explicable: earliest, midpoint, latest."""
    records = [{"pair": "EURUSD", "date": f"2010-01-{d:02d}", "hour": 13,
                "kind": PROBE_DATA} for d in range(1, 6)]
    keys = quality_targets(records, "EURUSD", 3)
    assert [k.date for k in keys] == ["2010-01-01", "2010-01-03", "2010-01-05"]


def test_quality_targets_ignore_non_data_probes() -> None:
    """There is nothing to decode in a 404."""
    records = [{"pair": "EURUSD", "date": "2010-01-01", "hour": 13,
                "kind": PROBE_MISSING}]
    assert quality_targets(records, "EURUSD", 3) == []


def test_refine_targets_cover_the_boundary_and_the_holes() -> None:
    """Refinement spends its probes on the hour axis, on the days that matter."""
    days = trading_days(dt.date(2024, 1, 1), dt.date(2024, 3, 29), 13)
    kinds = []
    for i, day in enumerate(days):
        kinds.append((day.isoformat(),
                      PROBE_MISSING if (i < 5 or 20 <= i < 25) else PROBE_DATA))
    records = [{"pair": "EURUSD", "date": date, "hour": 13, "kind": kind}
               for date, kind in kinds]
    params = {"pairs": ["EURUSD"], "start_date": "2024-01-01",
              "end_date": "2024-03-29", "probe_hour": 13,
              "refine_hours": [9, 15], "boundary_days": 2,
              "sustained_fraction": 0.9, "sustained_window_days": 10,
              "gap_run_min": 5}
    targets = coverage.refine_targets(records, params)
    assert targets, "refinement produced nothing to probe"
    assert {k.hour for k in targets} == {9, 15}
    assert all(k.pair == "EURUSD" for k in targets)
    # The hole runs at positions 20..24; its first day must be refined.
    assert kinds[20][0] in {k.date for k in targets}


def test_annotate_holes_separates_hour_specific_from_whole_day() -> None:
    """A hole with data at 09:00 is a hole in the survey hour, not in the day."""
    hole = [{"start": "2010-01-01", "end": "2010-01-05", "trading_days": 5,
             "composition": {PROBE_MISSING: 5}}]
    evidence = {"2010-01-02": {"hours": {"09": PROBE_DATA}, "data_hours": 1,
                               "any_data": True}}
    annotated = coverage.annotate_holes(hole, evidence)
    assert annotated[0]["verdict"] == "hour-specific"
    assert annotated[0]["days_with_data_at_another_hour"] == 1

    silent = {"2010-01-02": {"hours": {"09": PROBE_MISSING}, "data_hours": 0,
                             "any_data": False}}
    assert coverage.annotate_holes(hole, silent)[0]["verdict"] == "whole-day"
    assert coverage.annotate_holes(hole, {})[0]["verdict"] == "unrefined"


def test_refinement_evidence_ignores_the_survey_hour() -> None:
    """Only alternate hours are evidence about the survey hour."""
    index = {("EURUSD", "2010-01-01", 13): {"kind": PROBE_MISSING},
             ("EURUSD", "2010-01-01", 9): {"kind": PROBE_DATA}}
    evidence = coverage.refinement_evidence(index, "EURUSD", 13)
    assert evidence["2010-01-01"]["hours"] == {"09": PROBE_DATA}
    assert evidence["2010-01-01"]["any_data"] is True


# --------------------------------------------------------------------------- #
# Rate control
# --------------------------------------------------------------------------- #

def test_pacer_does_not_park_for_an_isolated_failure() -> None:
    """The first failure of a burst is retried, not waited out.

    This is the single change that quadrupled the survey's throughput: an
    isolated 503 arrives in 25ms and the retry usually succeeds, so pausing for
    it spent four fifths of the wall clock asleep.
    """
    pacer = Pacer(floor=0.4, ceiling=4.0, factor=1.25,
                  cooldowns=(0.0, 1.0, 3.0, 8.0))
    assert pacer.penalise() == 0.0
    assert pacer.penalise() == 1.0
    assert pacer.penalise() == 3.0
    pacer.reward()
    assert pacer.penalise() == 0.0, "a success must end the burst"


def test_pacer_cooldown_schedule_repeats_its_last_entry() -> None:
    """A long outage parks at the ceiling rather than growing without bound."""
    pacer = Pacer(cooldowns=(0.0, 1.0, 3.0))
    assert [pacer.penalise() for _ in range(5)] == [0.0, 1.0, 3.0, 3.0, 3.0]
    assert pacer.parked == 10.0


def test_pacer_gap_never_leaves_its_bounds() -> None:
    """Neither runaway backoff nor a rate the feed has already refused."""
    pacer = Pacer(floor=0.4, ceiling=4.0, factor=1.4, decay=0.5)
    for _ in range(50):
        pacer.penalise()
    assert pacer.gap == 4.0
    for _ in range(50):
        pacer.reward()
    assert pacer.gap == 0.4


# --------------------------------------------------------------------------- #
# The Parquet mirror
# --------------------------------------------------------------------------- #

def test_write_parquet_pins_its_schema(tmp_path: pathlib.Path) -> None:
    """Pinned like the Phase 1 tick schema; an inferred one is nobody's."""
    import pyarrow.parquet as pq

    records = [ProbeRecord(ProbeKey("EURUSD", "2024-01-03", 13), PROBE_DATA,
                           200, 100, 7, 1, "first").to_dict(),
               ProbeRecord(ProbeKey("EURUSD", "2024-01-02", 13), PROBE_MISSING,
                           404, 0, 0, 1, "first").to_dict()]
    path = write_parquet(records, tmp_path / "probes.parquet")
    table = pq.read_table(path)
    assert table.column_names == ["pair", "date", "hour", "kind", "status",
                                  "compressed_bytes", "ticks", "attempts",
                                  "stage"]
    assert str(table.schema.field("pair").type) == "large_string"
    assert str(table.schema.field("hour").type) == "int16"
    # Sorted, so the file does not depend on the order probes finished in.
    assert table.column("date").to_pylist() == ["2024-01-02", "2024-01-03"]


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #

def _write_survey(tmp_path: pathlib.Path) -> dict:
    """A tiny but complete probe checkpoint, and the params that read it."""
    days = trading_days(dt.date(2024, 1, 1), dt.date(2024, 3, 29), 13)
    lines = []
    for i, day in enumerate(days):
        kind = PROBE_MISSING if (i < 4 or 30 <= i < 36) else PROBE_DATA
        lines.append({"pair": "EURUSD", "date": day.isoformat(), "hour": 13,
                      "kind": kind, "bytes": 10 if kind == PROBE_DATA else 0,
                      "ticks": 5 if kind == PROBE_DATA else 0,
                      "attempts": 1, "stage": "first"})
    lines.append({"pair": "EURUSD", "date": days[31].isoformat(), "hour": 9,
                  "kind": PROBE_DATA, "bytes": 10, "ticks": 5, "attempts": 1,
                  "stage": "refine"})
    (tmp_path / "probes.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    (tmp_path / "quality.jsonl").write_text(
        json.dumps({"pair": "EURUSD", "date": days[10].isoformat(), "hour": 13,
                    "ok": True, "ticks": 5}) + "\n", encoding="utf-8")
    return {"pairs": ["EURUSD"], "start_date": "2024-01-01",
            "end_date": "2024-03-29", "probe_hour": 13,
            "experiment_dir": tmp_path.name, "refine_hours": [9],
            "sustained_fraction": 0.9, "sustained_window_days": 10,
            "gap_run_min": 5}


def test_run_produces_a_verdict_per_pair(tmp_path: pathlib.Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point is a pure function of the checkpoints on disk."""
    params = _write_survey(tmp_path)
    monkeypatch.setattr(coverage, "project_root", lambda: tmp_path.parent)

    payload = coverage.run(params, seed=20260819, loader=None)
    pair = payload["pairs"]["EURUSD"]

    assert payload["window"]["probe_hour"] == 13
    assert pair["counts"][PROBE_DATA] > 0
    assert pair["counts"]["unprobed"] == 0
    assert pair["recommended_start"]["date"] is not None
    assert len(pair["holes"]) == 1
    assert pair["holes"][0]["trading_days"] == 6
    assert pair["holes"][0]["verdict"] == "hour-specific"
    assert pair["quality"][0]["ok"] is True
    assert pair["bounds"]["max_bars"]["1h"] == (
        pair["bounds"]["trading_days_with_data"] * 24)
    assert payload["totals"]["material_holes"] == 1
    assert payload["totals"]["survey_completeness"] == 1.0


def test_run_is_deterministic(tmp_path: pathlib.Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate re-executes and demands an identical hash; prove it can be."""
    from research.experiment import canonical

    params = _write_survey(tmp_path)
    monkeypatch.setattr(coverage, "project_root", lambda: tmp_path.parent)
    first = canonical(coverage.run(params, seed=1, loader=None))
    second = canonical(coverage.run(params, seed=1, loader=None))
    assert first == second


def test_run_reports_an_incomplete_survey_rather_than_hiding_it(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pair with no probes at all must show as unprobed, not as absent data."""
    params = _write_survey(tmp_path)
    params["pairs"] = ["EURUSD", "GBPUSD"]
    monkeypatch.setattr(coverage, "project_root", lambda: tmp_path.parent)

    payload = coverage.run(params, seed=1, loader=None)
    missing = payload["pairs"]["GBPUSD"]
    assert missing["counts"]["unprobed"] == missing["expected_trading_days"]
    assert missing["counts"][PROBE_MISSING] == 0
    assert missing["recommended_start"]["date"] is None
    assert payload["totals"]["survey_completeness"] < 1.0
