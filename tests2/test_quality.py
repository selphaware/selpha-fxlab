"""The T3 entry point's own logic: reconciliation and the calendar guard.

Both are checks whose only job is to notice a disagreement, so a test that
feeds them agreeing inputs proves nothing. Each one here disagrees on purpose.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from research import quality


def _row(pair: str, year: str, *, ok: int, files: int, manifest_only: int = 0,
         disk_only: int = 0) -> dict[str, Any]:
    """One reconciliation row."""
    return {"pair": pair, "year": year, "ok": ok, "empty": 0, "closed": 0,
            "gap": 0, "ticks": 0, "dupes": 0, "files_on_disk": files,
            "manifest_only": manifest_only, "disk_only": disk_only}


# --------------------------------------------------------------------------- #
# The reconciliation against an ingestion result
# --------------------------------------------------------------------------- #

def _write_result(base: pathlib.Path, name: str, *, start: str, end: str,
                  ok: int, ticks: int = 0) -> None:
    """A minimal ingestion result document at the expected path."""
    path = base / "experiments" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "result.json").write_text(json.dumps({
        "result_hash": "f" * 64,
        "payload": {
            "window": {"start": start, "end": end},
            "totals": {"hours_ok": ok, "hours_empty": 0, "hours_closed": 0,
                       "hours_gap": 0, "ticks": ticks,
                       "duplicates_dropped": 0},
        },
    }), encoding="utf-8")


def test_a_result_matching_the_walk_agrees(tmp_path: pathlib.Path) -> None:
    """The baseline: the report and the store say the same thing."""
    _write_result(tmp_path, "T2a-ingestion", start="2015-01-01",
                  end="2016-12-31", ok=30)
    rows = [_row("EURUSD", "2015", ok=10, files=10),
            _row("EURUSD", "2016", ok=20, files=20)]
    out = quality._against_experiments(tmp_path, rows, ["T2a-ingestion"])
    assert out[0]["agrees"] is True
    assert out[0]["differences"] == {}


def test_a_result_overstating_its_hours_is_caught(
        tmp_path: pathlib.Path) -> None:
    """The defect class this card exists to close, in miniature."""
    _write_result(tmp_path, "T2a-ingestion", start="2015-01-01",
                  end="2016-12-31", ok=99)
    rows = [_row("EURUSD", "2015", ok=10, files=10)]
    out = quality._against_experiments(tmp_path, rows, ["T2a-ingestion"])
    assert out[0]["agrees"] is False
    assert out[0]["differences"] == {"ok": 89}


def test_only_the_result_own_window_is_compared(
        tmp_path: pathlib.Path) -> None:
    """The store is shared, so a walk of it all would fail every card."""
    _write_result(tmp_path, "T2b-backfill", start="2005-01-03",
                  end="2014-12-31", ok=10)
    rows = [_row("EURUSD", "2014", ok=10, files=10),
            _row("EURUSD", "2015", ok=999, files=999)]
    out = quality._against_experiments(tmp_path, rows, ["T2b-backfill"])
    assert out[0]["agrees"] is True


def test_a_missing_result_is_reported_not_skipped(
        tmp_path: pathlib.Path) -> None:
    """A reconciliation with an absent input must not silently pass."""
    out = quality._against_experiments(tmp_path, [], ["T2a-ingestion"])
    assert out == [{"experiment": "T2a-ingestion", "present": False}]


def test_the_by_year_fold_sums_across_pairs() -> None:
    """One line per year is what makes a whole-store table readable."""
    rows = [_row("EURUSD", "2015", ok=10, files=10),
            _row("GBPUSD", "2015", ok=7, files=7),
            _row("EURUSD", "2016", ok=3, files=3)]
    folded = quality._reconciliation_by_year(rows)
    assert folded["2015"]["ok"] == 17
    assert folded["2016"]["files_on_disk"] == 3


# --------------------------------------------------------------------------- #
# The committed-calendar guard
# --------------------------------------------------------------------------- #

def _calendar_file(base: pathlib.Path, full: list[str],
                   partial: dict[str, list[str]] | None = None,
                   *, min_empty: int = 6, min_pairs: int = 3) -> None:
    """Write a committed calendar with the given contents."""
    lines = ["[calendar]",
             f"min_empty_hours = {min_empty}",
             f"min_pairs_partial = {min_pairs}",
             "[calendar.full]"]
    lines += [f'"{d}" = ""' for d in full]
    lines.append("[calendar.partial]")
    for date, pairs in (partial or {}).items():
        joined = ", ".join(f'"{p}"' for p in pairs)
        lines.append(f'"{date}" = [{joined}]')
    (base / "config").mkdir(parents=True, exist_ok=True)
    (base / "config" / "calendar.toml").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")


def _derived(full: list[str],
             partial: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """A re-derivation to compare the committed file against."""
    return {"rules": {"min_empty_hours": 6, "min_pairs_partial": 3},
            "full": full, "partial": partial or {}}


PARAMS = {"calendar_path": "config/calendar.toml"}


def test_a_calendar_that_matches_agrees(tmp_path: pathlib.Path) -> None:
    """The baseline."""
    _calendar_file(tmp_path, ["2019-12-25"], {"2019-01-02": ["USDJPY"]})
    out = quality.compare_committed(
        tmp_path, PARAMS, _derived(["2019-12-25"],
                                   {"2019-01-02": ["USDJPY"]}))
    assert out["agrees"] is True


def test_a_holiday_added_by_hand_is_caught(tmp_path: pathlib.Path) -> None:
    """The whole reason a tracked file gets re-derived on every run."""
    _calendar_file(tmp_path, ["2019-12-25", "2019-07-04"])
    out = quality.compare_committed(tmp_path, PARAMS,
                                    _derived(["2019-12-25"]))
    assert out["agrees"] is False
    assert out["committed_not_derived"] == ["2019-07-04"]


def test_a_holiday_deleted_by_hand_is_caught(tmp_path: pathlib.Path) -> None:
    """The other direction, which a one-sided check would miss."""
    _calendar_file(tmp_path, [])
    out = quality.compare_committed(tmp_path, PARAMS,
                                    _derived(["2019-12-25"]))
    assert out["agrees"] is False
    assert out["derived_not_committed"] == ["2019-12-25"]


def test_an_edited_partial_holiday_is_caught(tmp_path: pathlib.Path) -> None:
    """A partial holiday names pairs, and the pair list is part of the claim."""
    _calendar_file(tmp_path, [], {"2019-01-02": ["USDJPY", "EURJPY"]})
    out = quality.compare_committed(
        tmp_path, PARAMS, _derived([], {"2019-01-02": ["USDJPY"]}))
    assert out["partial_agrees"] is False
    assert out["agrees"] is False


def test_an_edited_rule_is_caught_even_when_the_dates_match(
        tmp_path: pathlib.Path) -> None:
    """A calendar derived under a different rule is a different calendar."""
    _calendar_file(tmp_path, ["2019-12-25"], min_empty=1)
    out = quality.compare_committed(tmp_path, PARAMS,
                                    _derived(["2019-12-25"]))
    assert out["rules_agree"] is False


def test_a_missing_calendar_does_not_pass(tmp_path: pathlib.Path) -> None:
    """Absent must never read as agreeing."""
    out = quality.compare_committed(tmp_path, PARAMS, _derived([]))
    assert out["present"] is False and out["agrees"] is False
