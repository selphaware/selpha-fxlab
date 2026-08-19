"""Render the T1 coverage survey as the Markdown deliverable the card asks for.

The report is generated from ``result.json`` rather than written by hand, for
the same reason the result is hashed: a number that appears in a report but not
in the result document is a number nobody can check. Every figure below is read
out of the result; the prose around them is parameterised by those figures, so
regenerating after a re-probe produces a report that still agrees with itself.

What it does **not** do is decide anything. Pairs whose coverage is materially
shorter than the rest are flagged by a stated mechanical rule and left flagged:
universe membership is a checkpoint decision (SPEC2 pre-reg #3, and the card's
non-goals say so explicitly).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
from typing import Any, Final, Sequence

from research.coverage import BARS_PER_TRADING_DAY
from research.coverage_probe import (PROBE_DATA, PROBE_EMPTY, PROBE_ERROR,
                                     PROBE_MISSING)

_LOG: Final[logging.Logger] = logging.getLogger("research.coverage_report")

#: A pair is flagged for review when its usable history is shorter than this
#: fraction of the longest pair's. Mechanical, stated up front, and advisory:
#: the flag goes in the report and the decision goes to the checkpoint.
SHORT_HISTORY_FRACTION: Final[float] = 0.9

#: A pair is flagged when this fraction of its trading days from the
#: recommended start onward did not return data.
HOLED_FRACTION: Final[float] = 0.02

#: The kinds a counts dict carries, in the order they are tabulated.
KINDS: Final[tuple[str, ...]] = (PROBE_DATA, PROBE_EMPTY, PROBE_MISSING,
                                 PROBE_ERROR, "unprobed")


def _row(cells: Sequence[Any]) -> str:
    """One Markdown table row."""
    return "| " + " | ".join("" if c is None else str(c) for c in cells) + " |"


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """A Markdown table, or a note when there is nothing to tabulate."""
    if not rows:
        return ["_none_", ""]
    return [_row(header), _row(["---"] * len(header)),
            *[_row(r) for r in rows], ""]


def _pct(part: int, whole: int) -> str:
    """A percentage to one decimal place, or an em dash when undefined."""
    return f"{100.0 * part / whole:.1f}%" if whole else "—"


def _fraction_from_start(entry: dict[str, Any]) -> float:
    """Fraction of trading days from the recommended start that returned data."""
    counts = entry.get("counts_from_start") or {}
    total = sum(counts.get(kind, 0) for kind in KINDS)
    return (counts.get(PROBE_DATA, 0) / total) if total else 0.0


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _header(document: dict[str, Any], trials: int) -> list[str]:
    """Title, provenance and the one-paragraph summary of what was done."""
    payload = document["payload"]
    window = payload["window"]
    totals = payload["totals"]
    thresholds = payload["thresholds"]
    return [
        "# T1 — Dukascopy coverage survey",
        "",
        f"**Task card:** `taskcards/T1.md` · **Experiment:** "
        f"`{document['experiment_id']}` · **Seed:** {document['seed']} · "
        f"**Result hash:** `{document['result_hash'][:16]}`",
        "",
        f"**Window probed:** {window['start']} → {window['end']}, "
        f"{window['expected_trading_days']:,} trading days per pair at "
        f"{window['probe_hour']:02d}:00 UTC. "
        f"**Trials ledgered under T1:** {trials} "
        "(SPEC2 pre-reg #10; the count includes the probe harvest sessions, "
        "which are data collection rather than analysis).",
        "",
        "This is a **sampling probe survey, not an ingestion**. Every number "
        "below comes from what the Dukascopy datafeed answered to a single "
        "hourly request; nothing was written to `data/`, no bars were built, "
        "and no strategy content appears anywhere in this report. The "
        "experiment is not scorable and carries no scorecard.",
        "",
        "## Method, and the rules applied",
        "",
        f"* **First pass.** One probe per pair per trading day at "
        f"{window['probe_hour']:02d}:00 UTC across the whole window — "
        f"{totals['planned_first_pass_probes']:,} probes. Each is classified "
        "`data` (HTTP 200, decodes, at least one tick), `empty` (HTTP 200, "
        "zero bytes — the feed's way of saying the market was closed), "
        "`missing` (HTTP 404) or `error` (every attempt failed).",
        f"* **Recommended start.** The earliest probed trading day that itself "
        f"returned data, and from which the data fraction is at least "
        f"{thresholds['sustained_fraction']:.0%} over **both** the next "
        f"{thresholds['sustained_window_days']} trading days and the whole "
        "remainder of the window. The near window rejects an island of early "
        "coverage; the far window rejects a start just before a long hole.",
        f"* **Material hole.** A maximal run of at least "
        f"{thresholds['gap_run_min']} consecutive trading days from the "
        "recommended start onward that did not return data. Each is reported "
        "with its composition, because a run of `empty` is a closed market and "
        "a run of `missing` is absent history.",
        "* **Refinement.** The first pass already dates every boundary and "
        "hole to the day, which is finer than the week the card asks for. "
        "Refinement therefore spends its probes on the other axis: alternate "
        "hours between 08:00 and 16:00 UTC around each boundary and inside "
        "each hole, which settles whether the hole is a property of the day or "
        "only of the survey hour.",
        "* **Quality.** Three probes per pair, spread across its history "
        "(earliest, midpoint, latest), decoded in full and put through the "
        "Phase 1 validator. Presence is not usability.",
        "",
    ]


def _completeness(payload: dict[str, Any]) -> list[str]:
    """Whether the survey itself is complete, before any conclusion from it."""
    totals = payload["totals"]
    planned = totals["planned_first_pass_probes"]
    lines = [
        "## Survey completeness",
        "",
        f"The survey is **{totals['survey_completeness']:.2%} complete**: "
        f"{planned - totals['unprobed']:,} of {planned:,} planned first-pass "
        "probes were answered. Everything below is conditional on that.",
        "",
    ]
    lines += _table(
        ["classification", "probes", "share of planned"],
        [[kind, f"{totals.get(kind, 0):,}", _pct(totals.get(kind, 0), planned)]
         for kind in KINDS])
    if totals["unprobed"]:
        lines += [
            f"> **{totals['unprobed']:,} planned probes were never answered.** "
            "They are counted as `unprobed`, never as absent data, and they "
            "widen every hole they fall inside. A conclusion drawn over them "
            "is a conclusion about the survey, not about the feed.",
            "",
        ]
    if totals.get(PROBE_ERROR):
        lines += [
            f"> {totals[PROBE_ERROR]:,} probes exhausted every attempt and are "
            "recorded as `error`. An error is not evidence of absence; the "
            "`retry` stage re-asks for them and the last record for an "
            "identity wins.",
            "",
        ]
    return lines


def _summary_table(payload: dict[str, Any]) -> list[str]:
    """The one table a reviewer reads first."""
    rows = []
    for pair, entry in payload["pairs"].items():
        start = entry["recommended_start"]
        bounds = entry["bounds"]
        quality = entry.get("quality") or []
        ok = sum(1 for q in quality if q.get("ok"))
        rows.append([
            f"`{pair}`",
            start["first_data_date"] or "—",
            f"**{start['date']}**" if start["date"] else "**none**",
            f"{bounds['years']:.1f}",
            f"{_fraction_from_start(entry):.2%}",
            len(entry["holes"]),
            f"{ok}/{len(quality)}" if quality else "—",
        ])
    return [
        "## Per-pair verdict",
        "",
        *_table(["pair", "first data", "recommended start", "years",
                 "data % from start", "material holes", "quality checks ok"],
                rows),
        "Read the recommended start as *the earliest date research may begin*, "
        "not as a claim that everything after it is flawless — the holes "
        "column is where that claim is qualified.",
        "",
    ]


def _pair_section(pair: str, entry: dict[str, Any],
                  thresholds: dict[str, Any]) -> list[str]:
    """One pair, in full: evidence for the start, holes, years, spot checks."""
    start = entry["recommended_start"]
    context = entry.get("start_context") or {}
    counts = entry["counts"]
    lines = [f"### {pair}", ""]

    if start["date"] is None:
        lines += [
            "**No recommended start.** No probed trading day cleared the "
            "sustained-coverage rule. "
            f"First day returning data: {start['first_data_date'] or 'none'}.",
            "",
        ]
    else:
        before = context.get("before") or {}
        after = context.get("after") or {}
        window = context.get("window", 0)
        if start["first_data_date"] == start["date"]:
            evidence = (
                f"**Recommended start: {start['date']}.** The feed's first "
                "answer carrying data for this pair is that same day, so "
                "coverage begins at or before the edge of the probe window. "
                f"Measured there, the data fraction is "
                f"{start['near_fraction']:.2%} over the near window and "
                f"{start['far_fraction']:.2%} over the remainder of the "
                "window.")
        else:
            evidence = (
                f"**Recommended start: {start['date']}.** The feed's first "
                f"answer carrying data is earlier, on "
                f"{start['first_data_date']}, but that day did *not* clear "
                f"the rule: measured there the data fraction is "
                f"{start['first_data_near_fraction']:.2%} over the near "
                f"window and {start['first_data_far_fraction']:.2%} over the "
                f"remainder. At {start['date']} they are "
                f"{start['near_fraction']:.2%} and "
                f"{start['far_fraction']:.2%}.")
        lines += [
            evidence + " Last day returning data: "
            f"{context.get('last_data_date') or '—'}.",
            "",
            f"Probe density around the boundary, {window} trading days each "
            "side:",
            "",
            *_table(["side", *KINDS],
                    [["before", *[before.get(k, 0) for k in KINDS]],
                     ["from start", *[after.get(k, 0) for k in KINDS]]]),
        ]

    lines += [
        f"Material holes (runs of at least {thresholds['gap_run_min']} "
        "consecutive non-data trading days at or after the recommended start):",
        "",
        *_table(["from", "to", "trading days", "composition", "refined days",
                 "days with data at another hour", "verdict"],
                [[h["start"], h["end"], h["trading_days"],
                  ", ".join(f"{k} {v}" for k, v in h["composition"].items()),
                  h["refined_days"], h["days_with_data_at_another_hour"],
                  h["verdict"]]
                 for h in entry["holes"]]),
    ]

    lines += ["Probe classifications across the whole window:", "",
              *_table(["classification", *KINDS][1:],
                      [[counts.get(k, 0) for k in KINDS]])]

    lines += _year_table(entry)
    lines += _quality_table(entry)
    return lines


def _year_table(entry: dict[str, Any]) -> list[str]:
    """Per-year counts, condensed to the years where something is not data."""
    rows = []
    for year, counts in entry["by_year"].items():
        non_data = sum(counts.get(k, 0) for k in KINDS if k != PROBE_DATA)
        if non_data:
            rows.append([year, counts.get(PROBE_DATA, 0),
                         *[counts.get(k, 0) for k in KINDS[1:]]])
    if not rows:
        return ["Every probed trading day in every year returned data.", "", ""]
    return ["Years containing anything other than `data`:", "",
            *_table(["year", *KINDS], rows)]


def _quality_table(entry: dict[str, Any]) -> list[str]:
    """The full-decode spot checks."""
    rows = []
    for check in entry.get("quality") or []:
        spread = check.get("spread_pips") or {}
        issues = ", ".join(f"{i['reason']}×{i['count']}"
                           for i in check.get("issues") or []) or "none"
        rows.append([
            f"{check['date']}T{int(check.get('hour', 0)):02d}Z",
            f"{check.get('ticks', 0):,}",
            check.get("duplicates_dropped", "—"),
            check.get("crossed_ticks", "—"),
            check.get("non_positive_ticks", "—"),
            f"{spread.get('median_pips', 0):.2f}" if spread else "—",
            f"{spread.get('p99_9_pips', 0):.2f}" if spread else "—",
            check.get("spread_ceiling_pips", "—"),
            issues,
            "ok" if check.get("ok") else "**FAIL**",
        ])
    return ["Data-quality spot checks (full decode, Phase 1 validator):", "",
            *_table(["hour", "ticks", "dupes", "crossed", "non-positive",
                     "median spread (pips)", "p99.9", "ceiling", "issues",
                     ""], rows)]


def _bounds_section(payload: dict[str, Any]) -> list[str]:
    """What this bounds: how much history, at which timeframes, and the flags."""
    pairs = payload["pairs"]
    usable = {pair: entry["bounds"]["trading_days_with_data"]
              for pair, entry in pairs.items()}
    longest = max(usable.values()) if usable else 0

    rows = []
    for pair, entry in pairs.items():
        bounds = entry["bounds"]
        bars = bounds.get("max_bars") or {}
        rows.append([
            f"`{pair}`",
            entry["recommended_start"]["date"] or "—",
            f"{bounds['years']:.1f}",
            f"{bounds['trading_days_with_data']:,}",
            *[f"{bars.get(tf, 0):,}" for tf in BARS_PER_TRADING_DAY],
        ])

    lines = [
        "## What this bounds",
        "",
        "Bar counts below are ceilings, not forecasts: they are the trading "
        "days that returned data multiplied by the bars a full session yields "
        f"at each timeframe of SPEC2 pre-reg #6 "
        f"({', '.join(f'{tf}={n}' for tf, n in BARS_PER_TRADING_DAY.items())}). "
        "Holidays, half days and intraday gaps will take real counts below "
        "these; nothing will take them above.",
        "",
        *_table(["pair", "research start", "years", "trading days with data",
                 *[f"≤ {tf} bars" for tf in BARS_PER_TRADING_DAY]], rows),
    ]

    flagged = []
    for pair, entry in pairs.items():
        reasons = []
        if entry["recommended_start"]["date"] is None:
            reasons.append("no date cleared the sustained-coverage rule")
        elif longest and usable[pair] < SHORT_HISTORY_FRACTION * longest:
            reasons.append(
                f"{usable[pair]:,} usable trading days against "
                f"{longest:,} for the longest pair "
                f"({usable[pair] / longest:.0%})")
        holed = 1.0 - _fraction_from_start(entry)
        if holed > HOLED_FRACTION:
            reasons.append(f"{holed:.2%} of trading days from its start did "
                           "not return data")
        if entry["holes"]:
            worst = max(entry["holes"], key=lambda h: h["trading_days"])
            if worst["trading_days"] >= 20:
                reasons.append(
                    f"longest hole {worst['trading_days']} trading days "
                    f"({worst['start']} → {worst['end']}, {worst['verdict']})")
        bad_quality = [q for q in (entry.get("quality") or [])
                       if not q.get("ok")]
        if bad_quality:
            reasons.append(f"{len(bad_quality)} of "
                           f"{len(entry.get('quality') or [])} quality spot "
                           "checks failed")
        if reasons:
            flagged.append([f"`{pair}`", "; ".join(reasons)])

    lines += [
        "### Flags for the checkpoint",
        "",
        "Flagged mechanically, by the rules stated here, and **not decided**: "
        "universe membership is a checkpoint decision (SPEC2 pre-reg #3), and "
        "this card's non-goals put it out of scope. A pair is flagged when it "
        f"has no start date at all, when its usable history is under "
        f"{SHORT_HISTORY_FRACTION:.0%} of the longest pair's, when more than "
        f"{HOLED_FRACTION:.0%} of its trading days from its start did not "
        "return data, when it carries a hole of 20 trading days or more, or "
        "when a quality spot check failed.",
        "",
        *_table(["pair", "why it is flagged"], flagged),
    ]
    if not flagged:
        lines += ["No pair met any flag condition.", ""]
    return lines


def _observations(payload: dict[str, Any]) -> list[str]:
    """Observations for review. Not proposals: the card forbids those."""
    pairs = payload["pairs"]
    starts = sorted(entry["recommended_start"]["date"]
                    for entry in pairs.values()
                    if entry["recommended_start"]["date"])
    hour_specific = sum(1 for entry in pairs.values()
                        for hole in entry["holes"]
                        if hole["verdict"] == "hour-specific")
    whole_day = sum(1 for entry in pairs.values()
                    for hole in entry["holes"]
                    if hole["verdict"] == "whole-day")
    lines = ["## Observations", "",
             "Recorded for the checkpoint review. Per the card, an observation "
             "worth chasing becomes a next card only after a checkpoint; "
             "nothing here proposes work.", ""]
    if starts:
        lines.append(
            f"* Recommended starts span {starts[0]} to {starts[-1]}. A "
            "portfolio study restricted to the common window would begin at "
            f"**{starts[-1]}**; one that accepts unequal histories per pair "
            f"could begin at {starts[0]} for the earliest.")
    lines.append(
        f"* Of the material holes that refinement reached, {hour_specific} "
        f"are hour-specific — the day has data at another liquid hour — and "
        f"{whole_day} are whole-day. Only the whole-day ones are gaps in the "
        "feed's history; the rest are gaps in this survey's chosen hour and "
        "will not appear in a full ingestion.")
    lines.append(
        "* Every hole's composition is reported. A run of `empty` is the "
        "feed reporting a closed market and is a candidate input to the "
        "holiday calendar of pre-reg #5, which is T3's work, not this card's.")
    lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# Rendering and CLI
# --------------------------------------------------------------------------- #

def harvest_cost(records: Sequence[dict[str, Any]],
                 experiment_id: str) -> dict[str, Any]:
    """Sum what the probe harvest actually cost, from the ledger end records.

    Not asked for by the card, and reported anyway: T2 is bulk ingestion of the
    same feed and its budget should come from a measurement rather than from
    optimism. Each harvest session's end record carries its own counters; this
    adds them up.
    """
    totals = {"sessions": 0, "probes": 0, "seconds": 0.0, "parked": 0.0,
              "throttles": 0, "outages": 0}
    for record in records:
        if (record.get("record") != "end"
                or record.get("experiment_id") != f"{experiment_id}-probe"):
            continue
        status = str(record.get("status", ""))
        _, _, blob = status.partition("{")
        try:
            summary = json.loads("{" + blob) if blob else {}
        except json.JSONDecodeError:
            summary = {}
        totals["sessions"] += 1
        totals["probes"] += int(summary.get("completed", 0))
        totals["seconds"] += float(summary.get("seconds", 0.0))
        totals["parked"] += float(summary.get("seconds_parked", 0.0))
        totals["throttles"] += int(summary.get("throttles", 0))
        totals["outages"] += int(summary.get("outages_ridden_out", 0))
    return totals


def _cost_section(cost: dict[str, Any]) -> list[str]:
    """What the harvest cost, as a budgeting input for T2."""
    if not cost["sessions"] or cost["seconds"] <= 0:
        return []
    rate = cost["probes"] / cost["seconds"]
    hours = cost["seconds"] / 3600.0
    return [
        "## What the survey cost",
        "",
        "Recorded because T2 ingests this same feed in bulk and should budget "
        "from a measurement rather than from optimism. These are the harvest "
        "sessions' own counters, summed from their ledger end records.",
        "",
        *_table(["measure", "value"], [
            ["harvest sessions", cost["sessions"]],
            ["probes completed", f"{cost['probes']:,}"],
            ["wall clock", f"{hours:.1f} h"],
            ["sustained rate", f"{rate:.2f} probes/s"],
            ["seconds parked waiting out the feed",
             f"{cost['parked']:,.0f} ({cost['parked'] / cost['seconds']:.0%} "
             "of wall clock, summed across both workers)"],
            ["throttled responses", f"{cost['throttles']:,}"],
            ["outages ridden out", cost["outages"]],
        ]),
        "One probe is one hourly file. A full ingestion asks for every hour of "
        "every day rather than one hour per trading day, so at this rate the "
        "arithmetic for T2 follows directly from the hour count it plans to "
        "fetch — and the parked share above is the part that no amount of "
        "client tuning removes, because it is the feed being unavailable.",
        "",
    ]


def render(document: dict[str, Any], trials: int,
           gate_status: str = "not yet run",
           cost: dict[str, Any] | None = None) -> str:
    """Render the whole report from a result document."""
    payload = document["payload"]
    lines: list[str] = []
    lines += _header(document, trials)
    lines += _completeness(payload)
    lines += _summary_table(payload)
    lines += ["## Per pair, in full", ""]
    for pair, entry in payload["pairs"].items():
        lines += _pair_section(pair, entry, payload["thresholds"])
    lines += _bounds_section(payload)
    lines += _cost_section(cost or {"sessions": 0, "seconds": 0.0})
    lines += _observations(payload)
    lines += [
        "## Provenance",
        "",
        f"* Config: `experiments/{document['experiment_id']}/config.toml` "
        f"(sha256 `{document['config_sha256'][:16]}`)",
        f"* Probe records: `experiments/{document['experiment_id']}/"
        "probes.jsonl` and `probes.parquet`; quality spot checks: "
        "`quality.jsonl`",
        f"* Result: `experiments/{document['experiment_id']}/result.json`, "
        f"hash `{document['result_hash']}`",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. The loader served "
        f"{len((document.get('access') or {}).get('files') or [])} files: a "
        "coverage survey reads the feed, not the data store.",
        f"* Research gate: {gate_status}",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.coverage_report",
        description="Render the T1 coverage report from its result document.")
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--taskcard", default="T1")
    parser.add_argument("--gate-status", default="not yet run")
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render the report and write it."""
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base
            else pathlib.Path(__file__).resolve().parents[1])
    from research import ledger as ledger_mod

    document = json.loads(args.result.read_text(encoding="utf-8"))
    records = ledger_mod.read(base)
    trials = ledger_mod.trial_count(records, args.taskcard)
    cost = harvest_cost(records, str(document["experiment_id"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(document, trials, args.gate_status, cost),
                        encoding="utf-8")
    _LOG.info("wrote %s", args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
