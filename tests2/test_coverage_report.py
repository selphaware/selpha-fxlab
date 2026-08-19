"""The T1 report: generated from the result, and flagging without deciding."""

from __future__ import annotations

from typing import Any

import pytest

from research import coverage_report
from research.coverage_probe import PROBE_DATA, PROBE_ERROR, PROBE_MISSING


def _counts(**kwargs: int) -> dict[str, int]:
    """A counts dict with every kind present."""
    base = {PROBE_DATA: 0, "empty": 0, PROBE_MISSING: 0, PROBE_ERROR: 0,
            "unprobed": 0}
    base.update(kwargs)
    return base


def _pair(start: str | None, data: int, missing: int = 0,
          holes: list[dict[str, Any]] | None = None,
          quality_ok: bool = True) -> dict[str, Any]:
    """One pair's entry, shaped exactly as ``research.coverage.run`` emits it."""
    total = data + missing
    return {
        "counts": _counts(**{PROBE_DATA: data, PROBE_MISSING: missing}),
        "counts_from_start": _counts(**{PROBE_DATA: data,
                                        PROBE_MISSING: missing}),
        "expected_trading_days": total,
        "recommended_start": {
            "date": start, "rule": "the rule",
            "near_fraction": 1.0, "far_fraction": 1.0,
            "trading_days_from_start": total,
            "first_data_date": start,
            "first_data_near_fraction": 1.0,
            "first_data_far_fraction": 1.0},
        "start_context": {"window": 20, "before": _counts(),
                          "after": _counts(**{PROBE_DATA: 20}),
                          "last_data_date": "2025-02-28"},
        "holes": holes or [],
        "by_year": {"2005": _counts(**{PROBE_DATA: data,
                                       PROBE_MISSING: missing})},
        "refined_dates": 0,
        "refined_days_with_data_elsewhere": 0,
        "quality": [{"pair": "X", "date": "2010-01-04", "hour": 13,
                     "ticks": 1000, "duplicates_dropped": 0,
                     "crossed_ticks": 0, "non_positive_ticks": 0,
                     "spread_pips": {"median_pips": 0.3, "p99_9_pips": 4.0},
                     "spread_ceiling_pips": 20.0, "issues": [],
                     "ok": quality_ok}],
        "bounds": {"years": round(total / 252, 2),
                   "trading_days_with_data": data,
                   "max_bars": {"5m": data * 288, "30m": data * 48,
                                "1h": data * 24, "4h": data * 6, "1d": data}},
    }


def _document(pairs: dict[str, Any], unprobed: int = 0) -> dict[str, Any]:
    """A full result document around some pair entries."""
    planned = sum(e["expected_trading_days"] for e in pairs.values()) + unprobed
    totals = _counts(unprobed=unprobed)
    for entry in pairs.values():
        for kind, count in entry["counts"].items():
            totals[kind] = totals.get(kind, 0) + count
    totals["planned_first_pass_probes"] = planned
    totals["material_holes"] = sum(len(e["holes"]) for e in pairs.values())
    totals["survey_completeness"] = round((planned - unprobed) / planned, 6)
    return {
        "experiment_id": "T1-coverage", "taskcard": "T1", "seed": 20260819,
        "mode": "scoring", "scored": False, "rerun_class": "full",
        "result_hash": "a" * 64, "config_sha256": "b" * 64,
        "access": {"files": []},
        "payload": {
            "window": {"start": "2005-01-03", "end": "2025-02-28",
                       "probe_hour": 13, "expected_trading_days": 5260},
            "thresholds": {"sustained_fraction": 0.95,
                           "sustained_window_days": 120, "gap_run_min": 5},
            "pairs": pairs, "totals": totals},
    }


def test_report_carries_the_cards_required_sections() -> None:
    """Start dates, holes, counts, spot checks, and what it all bounds."""
    document = _document({"EURUSD": _pair("2005-01-03", 5000)})
    text = coverage_report.render(document, trials=3, gate_status="exit 0")

    for heading in ("# T1 — Dukascopy coverage survey", "## Survey completeness",
                    "## Per-pair verdict", "## What this bounds",
                    "### Flags for the checkpoint", "## Observations",
                    "## Provenance"):
        assert heading in text, f"missing section {heading!r}"
    assert "Trials ledgered under T1:** 3" in text
    assert "Research gate: exit 0" in text
    assert "2005-01-03" in text


def test_report_says_so_when_the_survey_is_incomplete() -> None:
    """An incomplete survey must qualify every conclusion drawn from it."""
    document = _document({"EURUSD": _pair("2005-01-03", 100)}, unprobed=900)
    text = coverage_report.render(document, trials=1)
    assert "planned probes were never answered" in text
    assert "10.00% complete" in text


def test_report_flags_a_short_history_without_deciding() -> None:
    """Pre-reg #3: the loop flags, the checkpoint decides."""
    document = _document({"EURUSD": _pair("2005-01-03", 5000),
                          "AUDJPY": _pair("2015-01-05", 2000)})
    text = coverage_report.render(document, trials=1)
    assert "`AUDJPY`" in text.split("### Flags for the checkpoint")[1]
    assert "usable history" in text
    assert "not decided" in text
    # Nothing anywhere may read as a membership decision.
    lowered = text.lower()
    for forbidden in ("we should drop", "recommend dropping", "will be dropped",
                      "remove audjpy"):
        assert forbidden not in lowered


def test_report_flags_a_pair_with_no_start_date() -> None:
    """A pair the rule never accepted is the loudest flag there is."""
    document = _document({"EURUSD": _pair("2005-01-03", 5000),
                          "EURCHF": _pair(None, 0, missing=5000)})
    text = coverage_report.render(document, trials=1)
    flags = text.split("### Flags for the checkpoint")[1]
    assert "`EURCHF`" in flags
    assert "no date cleared the sustained-coverage rule" in flags


def test_report_flags_a_failed_quality_check() -> None:
    """Presence is not usability, and the report must not let that pass."""
    document = _document({"EURUSD": _pair("2005-01-03", 5000,
                                          quality_ok=False)})
    text = coverage_report.render(document, trials=1)
    assert "quality spot checks failed" in text
    assert "**FAIL**" in text


def test_report_separates_hour_specific_holes_from_whole_day_ones() -> None:
    """The two have completely different consequences for T2."""
    hole = {"start": "2012-01-02", "end": "2012-01-10", "trading_days": 7,
            "composition": {PROBE_MISSING: 7}, "refined_days": 3,
            "days_with_data_at_another_hour": 3, "verdict": "hour-specific"}
    document = _document({"EURUSD": _pair("2005-01-03", 5000, holes=[hole])})
    text = coverage_report.render(document, trials=1)
    assert "hour-specific" in text
    assert "1 are hour-specific" in text
    assert "0 are whole-day" in text or "and 0 are whole-day" in text


def test_bar_ceilings_are_stated_as_ceilings() -> None:
    """A bar count that reads as a forecast is a promise nobody made."""
    document = _document({"EURUSD": _pair("2005-01-03", 5000)})
    text = coverage_report.render(document, trials=1)
    assert "ceilings, not forecasts" in text
    assert "≤ 1h bars" in text
    assert f"{5000 * 24:,}" in text


@pytest.mark.parametrize("trials", [1, 7])
def test_trial_count_is_stated(trials: int) -> None:
    """Pre-reg #10: the trial count sits next to the result, always."""
    document = _document({"EURUSD": _pair("2005-01-03", 10)})
    assert f"**Trials ledgered under T1:** {trials}" in coverage_report.render(
        document, trials=trials)


def test_harvest_cost_sums_the_ledger_end_records() -> None:
    """T2 budgets from this, so it must add up the sessions rather than one."""
    records = [
        {"record": "start", "experiment_id": "T1-coverage-probe"},
        {"record": "end", "experiment_id": "T1-coverage-probe",
         "status": 'ok {"completed": 100, "seconds": 200.0, '
                   '"seconds_parked": 50.0, "throttles": 7, '
                   '"outages_ridden_out": 1}'},
        {"record": "end", "experiment_id": "T1-coverage-probe",
         "status": 'ok {"completed": 300, "seconds": 400.0, '
                   '"seconds_parked": 10.0, "throttles": 3, '
                   '"outages_ridden_out": 0}'},
        {"record": "end", "experiment_id": "T0-spread-by-session",
         "status": "ok"},
    ]
    cost = coverage_report.harvest_cost(records, "T1-coverage")
    assert cost == {"sessions": 2, "probes": 400, "seconds": 600.0,
                    "parked": 60.0, "throttles": 10, "outages": 1}


def test_harvest_cost_survives_an_unparseable_status() -> None:
    """A session killed mid-write must not take the whole cost table with it."""
    records = [{"record": "end", "experiment_id": "T1-coverage-probe",
                "status": "failed:INTERRUPTED {broken"}]
    cost = coverage_report.harvest_cost(records, "T1-coverage")
    assert cost["sessions"] == 1
    assert cost["probes"] == 0


def test_cost_section_appears_only_when_there_is_a_cost_to_report() -> None:
    """An empty cost table is noise; T2 needs the numbers or nothing."""
    document = _document({"EURUSD": _pair("2005-01-03", 10)})
    assert "## What the survey cost" not in coverage_report.render(
        document, trials=1)
    text = coverage_report.render(
        document, trials=1, cost={"sessions": 2, "probes": 63120,
                                  "seconds": 50000.0, "parked": 12000.0,
                                  "throttles": 900, "outages": 4})
    assert "## What the survey cost" in text
    assert "1.26 probes/s" in text
    assert "13.9 h" in text
