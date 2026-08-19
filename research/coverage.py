"""Task card T1: turn probe records into a coverage verdict per pair.

This module is pure. It reads the probe checkpoints written by
:mod:`research.coverage_probe`, and it touches neither the network nor the data
store. That split is what makes the experiment reproducible at all: the
research gate re-executes an experiment from its config and demands the same
result hash, and an entry point that re-probed the feed would produce a
different answer every time for reasons that have nothing to do with honesty.

The card asks three questions, and each is answered by a rule fixed here rather
than by judgement applied afterwards:

**From which date is a pair reliable enough to research on?**
    The earliest probed trading day that itself returned data, from which the
    data fraction is at least ``sustained_fraction`` both over the next
    ``sustained_window_days`` trading days *and* over the whole remainder of
    the window. The near window stops a date being chosen just before a long
    hole that twenty good years would dilute away; the far window stops one
    being chosen inside a short island of early coverage.

**Where are the material holes?**
    Maximal runs of at least ``gap_run_min`` consecutive probed trading days
    that did not return data, at or after the recommended start. Each is
    reported with its composition, because a run of ``empty`` (the feed's way
    of saying the market was closed) is a holiday and a run of ``missing`` is
    an absent history, and conflating them would invent gaps over Christmas.

**Is the early data any good?**
    Three probes per pair, decoded in full and put through the Phase 1
    validator. Presence is not usability.

Nothing here decides anything. A pair whose coverage argues for dropping it or
shortening it is flagged, and the decision belongs to the checkpoint review
(pre-reg #3).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
from typing import Any, Final, Iterable, Sequence

from research.coverage_probe import (PROBE_DATA, PROBE_KINDS, PROBES_NAME,
                                     QUALITY_NAME, ProbeKey, read_probes,
                                     trading_days)

_LOG: Final[logging.Logger] = logging.getLogger("research.coverage")

#: Bars a full trading day yields at each timeframe of pre-reg #6. Used only to
#: bound how much history a pair can support; a real bar build will differ by
#: the holidays and half-days this ignores, which is why the report says
#: "at most".
BARS_PER_TRADING_DAY: Final[dict[str, int]] = {
    "5m": 288, "30m": 48, "1h": 24, "4h": 6, "1d": 1,
}

#: Decimal places every reported fraction is rounded to, for hash stability.
ROUNDING: Final[int] = 6


# --------------------------------------------------------------------------- #
# Reading the checkpoints
# --------------------------------------------------------------------------- #

def project_root() -> pathlib.Path:
    """Repository root, derived from this file rather than configured."""
    return pathlib.Path(__file__).resolve().parents[1]


def experiment_dir(params: dict[str, Any],
                   base: pathlib.Path | None = None) -> pathlib.Path:
    """Where the probe checkpoints live, from the config's own declaration."""
    relative = str(params.get("experiment_dir", "experiments/T1-coverage"))
    return (base or project_root()) / relative


def dedupe(records: Iterable[dict[str, Any]]) -> dict[tuple[str, str, int],
                                                      dict[str, Any]]:
    """One record per probe identity, last write winning.

    A probe may legitimately appear twice: a session that was killed between
    writing a record and updating nothing else, or a deliberate re-probe of an
    hour that errored. Last-wins is the only rule under which re-probing an
    error can ever improve the record.
    """
    index: dict[tuple[str, str, int], dict[str, Any]] = {}
    for record in records:
        try:
            key = (str(record["pair"]), str(record["date"]),
                   int(record["hour"]))
        except (KeyError, TypeError, ValueError):
            continue
        index[key] = record
    return index


# --------------------------------------------------------------------------- #
# Per-pair series
# --------------------------------------------------------------------------- #

def pair_series(index: dict[tuple[str, str, int], dict[str, Any]], pair: str,
                days: Sequence[dt.date], hour: int) -> list[tuple[str, str]]:
    """``(date, kind)`` for every trading day, ``unprobed`` where none exists.

    Built from the expected calendar rather than from what happens to be in the
    file, so a day nobody probed shows up as a hole in the *survey* instead of
    quietly disappearing from the denominator.
    """
    series: list[tuple[str, str]] = []
    for day in days:
        text = day.isoformat()
        record = index.get((pair, text, hour))
        series.append((text, str(record["kind"]) if record else "unprobed"))
    return series


def counts_of(series: Sequence[tuple[str, str]]) -> dict[str, int]:
    """Classification counts over a series, every known kind present."""
    counts = {kind: 0 for kind in PROBE_KINDS}
    counts["unprobed"] = 0
    for _date, kind in series:
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _fraction(values: Sequence[bool]) -> float:
    """Mean of a boolean sequence; an empty sequence is zero."""
    return (sum(1 for v in values if v) / len(values)) if values else 0.0


def recommended_start(series: Sequence[tuple[str, str]], *,
                      fraction: float, window: int) -> dict[str, Any]:
    """Apply the sustained-coverage rule and show its working.

    Args:
        series: ``(date, kind)`` over every trading day, in order.
        fraction: The data fraction both windows must clear.
        window: Length of the near window, in trading days.

    Returns:
        The chosen date and the evidence for it: the two fractions measured at
        that date, and the same two measured at the pair's very first data day,
        which is what distinguishes "coverage starts here" from "the first
        stray file the feed happens to hold".
    """
    probed = [(date, kind) for date, kind in series if kind != "unprobed"]
    flags = [kind == PROBE_DATA for _date, kind in probed]
    total = len(flags)
    if not total:
        return {"date": None, "rule": "no probed trading days",
                "near_fraction": None, "far_fraction": None,
                "first_data_date": None, "first_data_near_fraction": None,
                "first_data_far_fraction": None}

    suffix = [0] * (total + 1)
    for i in range(total - 1, -1, -1):
        suffix[i] = suffix[i + 1] + (1 if flags[i] else 0)

    def near(i: int) -> float:
        end = min(total, i + window)
        return _fraction(flags[i:end])

    def far(i: int) -> float:
        return (suffix[i] / (total - i)) if total - i else 0.0

    first_data = next((i for i, flag in enumerate(flags) if flag), None)
    chosen = None
    for i in range(total):
        if not flags[i]:
            continue
        if near(i) >= fraction and far(i) >= fraction:
            chosen = i
            break

    return {
        "date": probed[chosen][0] if chosen is not None else None,
        "rule": (f"earliest probed trading day returning data whose data "
                 f"fraction is >= {fraction} over both the next {window} "
                 f"trading days and the remainder of the window"),
        "near_fraction": (round(near(chosen), ROUNDING)
                          if chosen is not None else None),
        "far_fraction": (round(far(chosen), ROUNDING)
                         if chosen is not None else None),
        "trading_days_from_start": (total - chosen) if chosen is not None else 0,
        "first_data_date": (probed[first_data][0]
                            if first_data is not None else None),
        "first_data_near_fraction": (round(near(first_data), ROUNDING)
                                     if first_data is not None else None),
        "first_data_far_fraction": (round(far(first_data), ROUNDING)
                                    if first_data is not None else None),
    }


def holes(series: Sequence[tuple[str, str]], *, minimum: int,
          since: str | None) -> list[dict[str, Any]]:
    """Maximal runs of consecutive non-data trading days of at least ``minimum``.

    ``unprobed`` days break nothing and join nothing: they are counted inside a
    run so that the run's extent is honest, and reported separately so a hole
    that is really a hole in the survey cannot pass for a hole in the feed.
    """
    considered = [(date, kind) for date, kind in series
                  if since is None or date >= since]
    found: list[dict[str, Any]] = []
    run: list[tuple[str, str]] = []

    def close() -> None:
        if len(run) >= minimum:
            composition: dict[str, int] = {}
            for _date, kind in run:
                composition[kind] = composition.get(kind, 0) + 1
            found.append({
                "start": run[0][0], "end": run[-1][0],
                "trading_days": len(run),
                "composition": dict(sorted(composition.items())),
            })
        run.clear()

    for date, kind in considered:
        if kind == PROBE_DATA:
            close()
        else:
            run.append((date, kind))
    close()
    return found


def by_year(series: Sequence[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Classification counts per calendar year."""
    years: dict[str, dict[str, int]] = {}
    for date, kind in series:
        year = date[:4]
        bucket = years.setdefault(year, {k: 0 for k in PROBE_KINDS}
                                  | {"unprobed": 0})
        bucket[kind] = bucket.get(kind, 0) + 1
    return dict(sorted(years.items()))


# --------------------------------------------------------------------------- #
# Refinement
# --------------------------------------------------------------------------- #

def _analysis_inputs(params: dict[str, Any]) -> tuple[list[str], list[dt.date],
                                                      int, dict[str, Any]]:
    """Pairs, trading-day calendar, probe hour and thresholds from params."""
    pairs = [str(p).upper() for p in params["pairs"]]
    start = dt.date.fromisoformat(str(params["start_date"]))
    end = dt.date.fromisoformat(str(params["end_date"]))
    hour = int(params["probe_hour"])
    thresholds = {
        "sustained_fraction": float(params.get("sustained_fraction", 0.95)),
        "sustained_window_days": int(params.get("sustained_window_days", 120)),
        "gap_run_min": int(params.get("gap_run_min", 5)),
    }
    return pairs, trading_days(start, end, hour), hour, thresholds


def refine_targets(records: Sequence[dict[str, Any]],
                   params: dict[str, Any]) -> list[ProbeKey]:
    """Which alternate-hour probes the refinement sweep should run.

    The card asks for the coverage boundary and every material hole to be dated
    to within a week. The first pass already probes *every* trading day, so the
    date is known to the day before refinement starts; what is not known is
    whether a boundary or a hole is a property of the feed or an artefact of
    asking for 13:00 in particular. So refinement spends its probes on the
    other axis -- alternate hours between 08:00 and 16:00 UTC, on the days that
    matter -- which settles the question the daily sweep cannot.

    Targets, per pair:

    * the recommended start day and ``boundary_days`` trading days either side
      of it, so the boundary is confirmed rather than assumed;
    * for every material hole, its first, middle and last day, plus the trading
      day before and after it.
    """
    index = dedupe(records)
    pairs, days, hour, thresholds = _analysis_inputs(params)
    alternates = [int(h) for h in params.get("refine_hours", [9, 11, 15])]
    boundary_days = int(params.get("boundary_days", 5))
    cap = int(params.get("refine_max_per_pair", 400))

    targets: list[ProbeKey] = []
    for pair in pairs:
        series = pair_series(index, pair, days, hour)
        dates = [date for date, _kind in series]
        start = recommended_start(
            series, fraction=thresholds["sustained_fraction"],
            window=thresholds["sustained_window_days"])
        wanted: list[str] = []

        if start["date"] is not None:
            centre = dates.index(start["date"])
            low = max(0, centre - boundary_days)
            high = min(len(dates), centre + boundary_days + 1)
            wanted.extend(dates[low:high])

        for hole in holes(series, minimum=thresholds["gap_run_min"],
                          since=start["date"]):
            first = dates.index(hole["start"])
            last = dates.index(hole["end"])
            middle = (first + last) // 2
            for position in (first - 1, first, middle, last, last + 1):
                if 0 <= position < len(dates):
                    wanted.append(dates[position])

        seen: set[str] = set()
        ordered = [d for d in wanted if not (d in seen or seen.add(d))]
        pair_targets = [ProbeKey(pair, date, alt)
                        for date in ordered for alt in alternates]
        targets.extend(pair_targets[:cap])
    return targets


def refinement_evidence(index: dict[tuple[str, str, int], dict[str, Any]],
                        pair: str, hour: int) -> dict[str, dict[str, Any]]:
    """What the alternate-hour probes found, keyed by date.

    Returns one entry per refined date: the hours probed, how many returned
    data, and therefore whether that day has data *somewhere* even though the
    survey hour did not.
    """
    per_date: dict[str, dict[str, Any]] = {}
    for (record_pair, date, probed_hour), record in index.items():
        if record_pair != pair or probed_hour == hour:
            continue
        entry = per_date.setdefault(date, {"hours": {}, "data_hours": 0})
        entry["hours"][f"{probed_hour:02d}"] = str(record.get("kind"))
        if str(record.get("kind")) == PROBE_DATA:
            entry["data_hours"] += 1
    for entry in per_date.values():
        entry["hours"] = dict(sorted(entry["hours"].items()))
        entry["any_data"] = entry["data_hours"] > 0
    return dict(sorted(per_date.items()))


def annotate_holes(hole_list: list[dict[str, Any]],
                   evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold the alternate-hour findings into each hole.

    A hole whose days return data at 09:00 is a hole in the 13:00 hour, not in
    the day, and the two have completely different consequences for T2.
    """
    annotated: list[dict[str, Any]] = []
    for hole in hole_list:
        probed = {date: entry for date, entry in evidence.items()
                  if hole["start"] <= date <= hole["end"]}
        with_data = sorted(d for d, e in probed.items() if e["any_data"])
        annotated.append(hole | {
            "refined_days": len(probed),
            "days_with_data_at_another_hour": len(with_data),
            "examples": with_data[:3],
            "verdict": ("hour-specific" if with_data else
                        ("whole-day" if probed else "unrefined")),
        })
    return annotated


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #

def run(params: dict[str, Any], seed: int, loader: Any) -> dict[str, Any]:
    """Summarise Dukascopy coverage per pair from the recorded probes.

    Args:
        params: The card's parameters -- pairs, window, probe hour and the
            thresholds the rules above are stated in.
        seed: Recorded, not used to draw anything. Every rule here is
            deterministic by construction; the seed is carried so the result
            document says what the run was configured with and so an unseeded
            variant cannot be written by omission.
        loader: The research loader. Deliberately unused: a coverage survey
            reads the feed's answers, not the data store, and the empty access
            record it produces is the evidence that nothing was ingested.

    Returns:
        A JSON-serialisable payload, one entry per pair plus totals.
    """
    directory = experiment_dir(params)
    probes = read_probes(directory / PROBES_NAME)
    quality = read_probes(directory / QUALITY_NAME)
    index = dedupe(probes)
    pairs, days, hour, thresholds = _analysis_inputs(params)

    per_pair: dict[str, Any] = {}
    for pair in pairs:
        series = pair_series(index, pair, days, hour)
        start = recommended_start(
            series, fraction=thresholds["sustained_fraction"],
            window=thresholds["sustained_window_days"])
        evidence = refinement_evidence(index, pair, hour)
        raw_holes = holes(series, minimum=thresholds["gap_run_min"],
                          since=start["date"])
        covered = sum(1 for date, kind in series
                      if kind == PROBE_DATA
                      and (start["date"] is None or date >= start["date"]))
        per_pair[pair] = {
            "counts": counts_of(series),
            "expected_trading_days": len(days),
            "recommended_start": start,
            "holes": annotate_holes(raw_holes, evidence),
            "by_year": by_year(series),
            "refined_dates": len(evidence),
            "refined_days_with_data_elsewhere": sum(
                1 for e in evidence.values() if e["any_data"]),
            "quality": sorted(
                (q for q in quality if str(q.get("pair")) == pair),
                key=lambda q: (str(q.get("date")), int(q.get("hour", 0)))),
            "bounds": _bounds(start["date"], covered,
                              days[-1].isoformat() if days else None),
        }

    payload = {
        "window": {"start": days[0].isoformat() if days else None,
                   "end": days[-1].isoformat() if days else None,
                   "probe_hour": hour,
                   "expected_trading_days": len(days)},
        "thresholds": thresholds,
        "seed": int(seed),
        "pairs": per_pair,
        "totals": _totals(per_pair, len(days), len(pairs)),
        "probe_records": len(index),
        "loader": {"mode": getattr(loader, "mode", None),
                   "pairs_read": sorted(getattr(loader, "access").pairs)
                   if hasattr(loader, "access") else []},
        "note": ("Coverage survey by sampling probe. No ticks were stored and "
                 "no data was read from the research tree; every number here "
                 "comes from what the Dukascopy datafeed answered. Pair "
                 "membership is flagged, never decided (pre-reg #3)."),
    }
    _LOG.info("coverage survey: %d pairs, %d probe records, %d expected "
              "trading days", len(pairs), len(index), len(days))
    return payload


def _bounds(start: str | None, covered_days: int,
            end: str | None) -> dict[str, Any]:
    """How much history a pair's recommended start actually buys."""
    if start is None or end is None:
        return {"years": 0.0, "trading_days_with_data": 0, "max_bars": {}}
    span = ((dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
            / 365.25)
    return {
        "years": round(span, 2),
        "trading_days_with_data": covered_days,
        "max_bars": {tf: covered_days * per_day
                     for tf, per_day in BARS_PER_TRADING_DAY.items()},
    }


def _totals(per_pair: dict[str, Any], expected: int,
            pair_count: int) -> dict[str, Any]:
    """Survey-wide counters, including how complete the survey itself is."""
    totals = {kind: 0 for kind in PROBE_KINDS}
    totals["unprobed"] = 0
    material_holes = 0
    for entry in per_pair.values():
        for kind, count in entry["counts"].items():
            totals[kind] = totals.get(kind, 0) + count
        material_holes += len(entry["holes"])
    planned = expected * pair_count
    totals["planned_first_pass_probes"] = planned
    totals["material_holes"] = material_holes
    totals["survey_completeness"] = round(
        (planned - totals["unprobed"]) / planned, ROUNDING) if planned else 0.0
    return totals


def summarise(path: pathlib.Path) -> str:
    """Human-readable one-liner per pair, for watching a harvest progress."""
    document = json.loads(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for pair, entry in document["payload"]["pairs"].items():
        start = entry["recommended_start"]["date"]
        lines.append(f"{pair:8s} start={start} "
                     f"holes={len(entry['holes'])} "
                     f"counts={entry['counts']}")
    return "\n".join(lines)
