"""The canonical manifest reading, and the derivations that replace prose.

Each test here pins one of the six defects the T3 audit found. They are written
against hand-built manifest fragments rather than the store, because the point
is not "does this agree with the store today" -- it is "does this still agree
after somebody re-ingests an hour", which is exactly the situation that made
the shard warning list and the hour records disagree in the first place.
"""

from __future__ import annotations

from typing import Any

import pytest

from research import ingest_summary as summary


def _record(date: str, hour: int, status: str, *,
            issues: list[str] | None = None) -> dict[str, Any]:
    """One manifest hour record, minimal but shaped like the real thing."""
    return {
        "pair": "AUDUSD", "date": date, "hour": hour, "status": status,
        "issues": [{"reason": r, "detail": f"{r} at {date}T{hour:02d}"}
                   for r in (issues or [])],
    }


# --------------------------------------------------------------------------- #
# _tally: which copy of a flag counts, and against which hours
# --------------------------------------------------------------------------- #

def _tally_one(record: dict[str, Any], market_open: bool = True) -> tuple[
        dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    """Run one record through the tally and return its three buckets."""
    warnings: dict[str, int] = {}
    errors: dict[str, int] = {}
    warn_hour: dict[str, dict[str, int]] = {}
    warn_year: dict[str, dict[str, int]] = {}
    summary._tally(record, record["status"], market_open,
                   warnings, errors, warn_hour, warn_year)
    return warnings, errors, warn_year


def test_a_stored_hours_soft_flag_is_a_warning() -> None:
    """A flag on an hour that is in the store describes the store."""
    warnings, errors, by_year = _tally_one(
        _record("2013-05-02", 21, "ok", issues=["SPREAD_OUTLIER"]))
    assert warnings == {"SPREAD_OUTLIER": 1}
    assert errors == {}
    assert by_year == {"SPREAD_OUTLIER": {"2013": 1}}


def test_a_rejected_hours_flag_is_a_rejection_not_a_warning() -> None:
    """The same token means something else on an hour that was thrown away."""
    warnings, errors, _ = _tally_one(
        _record("2008-04-02", 3, "gap", issues=["CROSSED_QUOTE"]))
    assert warnings == {}
    assert errors == {"CROSSED_QUOTE": 1}


def test_a_fetch_failure_lands_with_the_rejections() -> None:
    """It is a reason an hour is missing, whatever originally filed it.

    T2b's report carried ``FETCH_ERROR`` under *Warnings* because the shard
    filed it there, which put a missing hour in the table of hours that are
    present.
    """
    _, errors, _ = _tally_one(
        _record("2008-04-03", 9, "gap", issues=["FETCH_ERROR"]))
    assert errors == {"FETCH_ERROR": 1}


def test_an_empty_open_hour_is_empty_trading_hour_by_status() -> None:
    """Ruling R5: derived from the status, never looked up in a warning list."""
    warnings, _, by_year = _tally_one(_record("2013-12-25", 14, "empty"))
    assert warnings == {"EMPTY_TRADING_HOUR": 1}
    assert by_year == {"EMPTY_TRADING_HOUR": {"2013": 1}}


def test_an_empty_hour_outside_the_week_is_not_a_trading_hour() -> None:
    """A shut hour serving nothing is the calendar, not a holiday candidate."""
    warnings, _, _ = _tally_one(_record("2013-12-21", 23, "empty"),
                                market_open=False)
    assert warnings == {}


def test_a_closed_hour_files_nothing() -> None:
    """The derived week already accounts for it."""
    warnings, errors, _ = _tally_one(_record("2013-12-21", 23, "closed"),
                                     market_open=False)
    assert warnings == {} and errors == {}


def test_the_hour_bucket_uses_a_zero_padded_key() -> None:
    """Midnight must not collide with noon, nor sort before the rest."""
    warnings: dict[str, int] = {}
    warn_hour: dict[str, dict[str, int]] = {}
    summary._tally(_record("2013-05-02", 0, "ok", issues=["SPREAD_OUTLIER"]),
                   "ok", True, warnings, {}, warn_hour, {})
    assert warn_hour == {"SPREAD_OUTLIER": {"00": 1}}


# --------------------------------------------------------------------------- #
# _sublabels: ruling R2
# --------------------------------------------------------------------------- #

def test_a_closed_market_rejection_gains_the_pre_open_sublabel() -> None:
    """R2 names the class so a later card need not re-derive the finding."""
    record = _record("2012-01-01", 21, "gap", issues=["CLOSED_MARKET_TICK"])
    assert summary._sublabels(record, market_open=False) == [
        summary.PRE_OPEN_FEED_DATA]


def test_other_rejections_gain_no_sublabel() -> None:
    """The sub-label answers one question and stays out of the others."""
    record = _record("2008-04-02", 3, "gap", issues=["CROSSED_QUOTE"])
    assert summary._sublabels(record, market_open=False) == []


def test_the_sublabel_needs_the_hour_to_have_been_shut() -> None:
    """The label claims the feed published *before the week opened*.

    If the derived week called the hour open, that claim is not the one the
    evidence supports, whatever the reason token says.
    """
    record = _record("2012-01-02", 10, "gap", issues=["CLOSED_MARKET_TICK"])
    assert summary._sublabels(record, market_open=True) == []


# --------------------------------------------------------------------------- #
# Gap attribution and episodes: the numbers that used to be prose
# --------------------------------------------------------------------------- #

def _gap(pair: str, date: str, hour: int, reason: str) -> dict[str, Any]:
    return {"pair": pair, "date": date, "hour": hour, "reasons": [reason],
            "sublabels": [], "detail": ""}


def test_a_reason_total_is_split_by_pair() -> None:
    """The defect: one pair's name attached to every pair's total."""
    gaps = [_gap("AUDUSD", "2008-04-02", 3, "CROSSED_QUOTE"),
            _gap("AUDUSD", "2008-04-02", 4, "CROSSED_QUOTE"),
            _gap("USDJPY", "2008-11-24", 12, "CROSSED_QUOTE")]
    assert summary._gap_reason_pairs(gaps) == {
        "CROSSED_QUOTE": {"AUDUSD": 2, "USDJPY": 1}}


def test_episodes_break_on_a_clean_month() -> None:
    """A month with no gap ends a run; that is the whole rule."""
    gaps = ([_gap("AUDUSD", "2007-04-02", h, "CROSSED_QUOTE") for h in (1, 2)]
            + [_gap("AUDUSD", "2007-05-02", 1, "CROSSED_QUOTE")]
            + [_gap("AUDUSD", "2007-08-02", 1, "CROSSED_QUOTE")])
    episodes = summary._gap_episodes(gaps)
    assert [(e["first_month"], e["last_month"], e["months"], e["hours"])
            for e in episodes] == [("2007-04", "2007-05", 2, 3),
                                   ("2007-08", "2007-08", 1, 1)]


def test_episodes_are_split_per_pair_and_per_reason() -> None:
    """Two pairs failing the same month are not one episode."""
    gaps = [_gap("AUDUSD", "2008-04-02", 1, "CROSSED_QUOTE"),
            _gap("USDJPY", "2008-04-02", 1, "CROSSED_QUOTE"),
            _gap("AUDUSD", "2008-04-03", 9, "FETCH_ERROR")]
    episodes = summary._gap_episodes(gaps)
    assert {(e["pair"], e["reason"]) for e in episodes} == {
        ("AUDUSD", "CROSSED_QUOTE"), ("USDJPY", "CROSSED_QUOTE"),
        ("AUDUSD", "FETCH_ERROR")}


def test_an_episode_crossing_a_year_boundary_stays_one_episode() -> None:
    """December to January is contiguous; a naive month key says otherwise."""
    gaps = [_gap("AUDUSD", "2007-12-03", 1, "CROSSED_QUOTE"),
            _gap("AUDUSD", "2008-01-03", 1, "CROSSED_QUOTE")]
    episodes = summary._gap_episodes(gaps)
    assert len(episodes) == 1
    assert episodes[0]["months"] == 2


def test_sublabels_are_counted_by_pair() -> None:
    """The R2 table is an attribution too."""
    rows = [{"pair": "EURJPY", "date": "2012-01-01", "hour": 21,
             "reasons": ["CLOSED_MARKET_TICK"],
             "sublabels": [summary.PRE_OPEN_FEED_DATA]},
            {"pair": "USDJPY", "date": "2012-01-01", "hour": 21,
             "reasons": ["CLOSED_MARKET_TICK"],
             "sublabels": [summary.PRE_OPEN_FEED_DATA]}]
    assert summary._gap_sublabels(rows) == {
        summary.PRE_OPEN_FEED_DATA: {"EURJPY": 1, "USDJPY": 1}}


# --------------------------------------------------------------------------- #
# Ruling R6: a count may not be typed into prose
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("note", [
    "12,998 crossed quotes in AUDUSD",
    "468 flags fired in the crisis year",
    "the sweep recovered 111 of them",
])
def test_a_note_carrying_a_count_is_refused(note: str) -> None:
    """These are the exact shapes that went stale in T2b's report."""
    from research.ingest_report import check_note

    with pytest.raises(ValueError) as caught:
        check_note(note)
    assert "R6" in str(caught.value)


@pytest.mark.parametrize("note", [
    "the episode ran 2007-04 to 2008-09",
    "on 2026-08-22 the host lost power mid-chunk",
    "p99.9 over a thin hour is a weak instrument",
    "level 4 was reachable and never holdable",
])
def test_dates_versions_and_labels_are_not_counts(note: str) -> None:
    """The guard must not make an honest note unwritable."""
    from research.ingest_report import check_note

    assert check_note(note) is None
