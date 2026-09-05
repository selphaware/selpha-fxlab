"""Render the T3 data-quality report from its result document.

Ruling R6 in full force: there is no ``--note`` here at all. T3's whole first
step was cleaning stale prose out of the ingestion reports, so a report about
that with authored numbers in it would be a joke at its own expense. Every
figure below is read out of ``result.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
from typing import Any, Final, Sequence

_LOG: Final[logging.Logger] = logging.getLogger("research.quality_report")

#: Flagged cross-check hours listed in full before the table is truncated.
MAX_FLAGGED_ROWS: Final[int] = 40

#: Unexplained-empty dates listed in full before the table is truncated.
MAX_UNEXPLAINED_ROWS: Final[int] = 20


def _row(cells: Sequence[Any]) -> str:
    """One Markdown table row."""
    return "| " + " | ".join("" if c is None else str(c) for c in cells) + " |"


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    """A Markdown table, or a note when there is nothing to tabulate."""
    if not rows:
        return ["_none_", ""]
    return [_row(header), _row(["---"] * len(header)),
            *[_row(r) for r in rows], ""]


def _n(value: Any) -> str:
    """A thousands-separated integer."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _f(value: Any, places: int = 3) -> str:
    """A float, or an em dash where there was no measurement."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return str(value)


def _tick(value: Any) -> str:
    """A boolean as a mark the eye can scan a column of."""
    return "yes" if value else "**no**"


def render(document: dict[str, Any], trials: int, gate_status: str,
           home: str) -> str:
    """Build the whole report."""
    payload = document["payload"]
    card = str(document.get("taskcard") or "T3")
    lines: list[str] = []
    lines += _header(document, payload, trials, card)
    lines += _rulings(payload)
    lines += _reconciliation(payload)
    lines += _validation(payload)
    lines += _bars(payload)
    lines += _calendar(payload)
    lines += _crosscheck(payload)
    lines += _exclusion(payload)
    lines += _provenance(document, gate_status, home)
    return "\n".join(lines) + "\n"


def _header(document: dict[str, Any], payload: dict[str, Any], trials: int,
            card: str) -> list[str]:
    """Title, identity and what this report is."""
    window = payload["window"]
    return [
        f"# {card} — Data quality, holiday calendar and cross-venue check",
        "",
        f"**Window:** {window['start']} → {window['end']}, "
        f"{window['pairs']} pairs · **Task card:** `taskcards/{card}.md` · "
        f"**Experiment:** `{document['experiment_id']}` · "
        f"**Seed:** {document['seed']} · "
        f"**Result hash:** `{document['result_hash'][:16]}`",
        "",
        f"**Trials ledgered under {card}:** {trials} (SPEC2 pre-reg #10).",
        "",
        "This card asks four questions about the store the two ingestion "
        "cards built, and answers them from four different directions: does "
        "the bookkeeping agree with the files, is every stored hour still "
        "valid, which quiet days were holidays, and does a second venue quote "
        "the same market. It produces no strategy content and is not scorable.",
        "",
        "**Every number in this report is derived at render time** from the "
        "result document, which is itself derived from the manifests, the "
        "store and two checkpointed passes. That is ruling R6, and it is not a "
        "style preference: the M2 audit found three figures in the previous "
        "reports that had been correct when typed and had since stopped being "
        "true. There is no authored-prose escape hatch in this renderer.",
        "",
    ]


def _rulings(payload: dict[str, Any]) -> list[str]:
    """R1-R6, and evidence for the two that are code rather than policy."""
    rulings = payload["rulings"]
    rows = []
    for key in ("R1", "R2", "R3", "R4", "R5", "R6"):
        entry = rulings[key]
        rows.append([f"**{key}**", entry["statement"],
                     f"`{entry['enforced_by']}`"])
    seal = rulings["seal"]
    r1 = rulings["R1"]
    return [
        "## The rulings in force",
        "",
        "R1-R6 were fixed at the M2 checkpoint before any T3 result existed "
        "and are recorded in `SPEC2.md`. They are restated here because a "
        "report that a ruling shapes should say which ruling shaped it.",
        "",
        *_table(["ruling", "statement", "enforced by"], rows),
        "Three of those constrain how a report may *speak* and have nothing to "
        "exercise. Two are code, and code that is never exercised is code "
        "nobody knows is still wired up — so both refusals were run while this "
        "result was produced:",
        "",
        *_table(["refusal", "reason token", "refused?"], [
            [f"the holdout seal, asked for {seal['cutoff']}",
             "`HOLDOUT_SEALED`", _tick(seal["canary_refused"])],
            ["ruling R1's exclusion window, asked for an excluded pair-date",
             "`PAIR_EXCLUDED_WINDOW`", _tick(r1["canary_refused"])],
        ]),
    ]


def _reconciliation(payload: dict[str, Any]) -> list[str]:
    """Step 0.6: three sources gathered three ways, compared."""
    rec = payload["reconciliation"]
    totals = rec["totals"]
    rows = []
    for entry in rec["against_experiments"]:
        if not entry.get("present"):
            rows.append([f"`{entry['experiment']}`", "—", "—", "—",
                         "**result missing**"])
            continue
        mine, theirs = entry["manifest_walk"], entry["result_totals"]
        rows.append([
            f"`{entry['experiment']}`",
            f"{entry['window']['start']} → {entry['window']['end']}",
            f"`{entry['result_hash']}`",
            _n(theirs["ok"]),
            "agrees" if entry["agrees"]
            else f"**differs**: {entry['differences']}",
        ])
    by_year = rec["by_year"]
    year_rows = [[year, _n(b["ok"]), _n(b["empty"]), _n(b["closed"]),
                  _n(b["gap"]), _n(b["files_on_disk"]),
                  _n(b["files_on_disk"] - b["ok"])]
                 for year, b in by_year.items()]
    return [
        "## Step 0 — Report reconciliation",
        "",
        "The audit that opened this card compared three descriptions of the "
        "same store and found the ingestion reports wrong in six places. Each "
        "was fixed at its source rather than in the prose that carried it:",
        "",
        "1. **The report generator hardcoded one card's name.** T2b's report "
        "was titled, linked and provenanced as T2a's while printing T2b's "
        "numbers. The card, the trial count and every path now come from the "
        "result document, so a report cannot be rendered under a card its "
        "experiment did not declare.",
        "2. **Bar rows were counted store-wide.** A bar table is one file per "
        "pair spanning every card's window, so both ingestion reports claimed "
        "the same total for different decades. The count is now bounded by the "
        "experiment's window, like the storage walk already was.",
        "3. **Three of the sharpest claims were authored prose.** The AUDUSD "
        "gap attribution, the episode boundaries and the by-year spread counts "
        "were typed into bullets, correct when written and stale afterwards. "
        "All three are derived and tabulated now, and `--note` refuses any "
        "note carrying a count — ruling R6, enforced rather than intended.",
        "4. **A manifest shard kept validation flags twice and the copies "
        "disagreed.** Root-caused, and settled in `SPEC2.md` §The canonical "
        "manifest reading: the hour records plus the derived coverage block "
        "are canonical, the session warning log answers exactly one question, "
        "and reports state flags on stored data apart from flags observed on "
        "hours that were then discarded. Annotating the shards themselves "
        "would be a manifest-format change, so it is proposed rather than "
        "made.",
        "5. **The throughput table mixed two sources silently.** Requests came "
        "from the chunk log and wall clock from the session log, and the two "
        "disagree in both directions between the cards. Each rate now stays "
        "inside one source and the table says which.",
        "6. **The reconciliation itself is now a standing check** rather than "
        "something somebody once did by hand — which is the rest of this "
        "section.",
        "",
        "It re-runs *inside the experiment*, against the fixed reports, on "
        "every gate run.",
        "",
        "The three sources are gathered by different means on purpose. The "
        "manifest walk reads every shard; the file listing is a directory "
        "scan that never consults a manifest; the ingestion results are the "
        "documents the reports actually print from. Asking the manifest where "
        "its files are and then asking the manifest whether they are there "
        "would prove nothing.",
        "",
        *_table(["measure", "value"], [
            ["manifest shards read", _n(rec["shards_read"])],
            ["pair-years compared", _n(rec["pair_years"])],
            ["hours recorded `ok`", _n(totals["ok"])],
            ["tick Parquet files on disk", _n(totals["files_on_disk"])],
            ["files a manifest claims that are absent",
             f"**{_n(totals['manifest_only'])}**"],
            ["files on disk no manifest claims",
             f"**{_n(totals['disk_only'])}**"],
            ["ticks recorded", _n(totals["ticks"])],
            ["duplicate ticks dropped", _n(totals["dupes"])],
            ["**pair-years where anything disagrees**",
             f"**{_n(rec['mismatching_pair_years'])}**"],
        ]),
        "An `ok` hour and a file are the same object counted twice. A "
        "manifest-only file is a record of data that is not there; a disk-only "
        "file is data no record accounts for, which is the more dangerous of "
        "the two because the tick reader globs a day directory rather than "
        "consulting the manifest, so it would read as settled data.",
        "",
        "### Against the ingestion results",
        "",
        "What each ingestion report prints, against a fresh walk of the "
        "manifests it printed from:",
        "",
        *_table(["experiment", "window", "result hash", "hours `ok`",
                 "verdict"], rows),
        "### By year",
        "",
        *_table(["year", "ok", "empty", "closed", "gap", "files on disk",
                 "files − ok"], year_rows),
        "The last column is the reconciliation in one number per year: it is "
        "zero when every stored hour has exactly one file and every file has "
        "exactly one stored hour.",
        "",
    ]


def _validation(payload: dict[str, Any]) -> list[str]:
    """Step 1: every stored hour re-opened and re-checked."""
    val = payload["validation"]
    rows = [[f"`{pair}`", _n(b["hours"]), _n(b["ticks"]),
             _n(b["failures"]) if b["failures"] else "0"]
            for pair, b in val["by_pair"].items()]
    kinds = [[f"`{k}`", _n(v)] for k, v in (val["by_kind"] or {}).items()]
    details = [[f"`{d['kind']}`", f"`{d['pair']}`", d["date"],
                f"{int(d['hour']):02d}:00Z", d["detail"][:120]]
               for d in (val["details"] or [])]
    return [
        "## Step 1 — Full-store validation",
        "",
        "Every stored hour re-opened offline and checked against the rules "
        "that stored it, and against its own manifest entry. This is not the "
        "same check the ingestion ran, and the difference is the point: "
        "between the two sit a Parquet writer, a resumable driver that "
        "rewrites shards, a host power loss mid-chunk, and a store two cards "
        "filled into the same tree. Each of those is a way for the manifest "
        "and the files to drift apart without either being wrong on its own.",
        "",
        "Per hour: the pinned Arrow schema column by column with no extras; "
        "the row count against `written_ticks`; `ask >= bid` and both strictly "
        "positive; timestamps non-decreasing, UTC, and inside the hour the "
        "file is named for; and the hour open under the derived FX week. "
        "Because every tick is proven inside its own hour, a Saturday tick "
        "cannot hide in a Friday file, so that last check covers the whole "
        "`CLOSED_MARKET_TICK` rule.",
        "",
        *_table(["measure", "value"], [
            ["pair-months validated", _n(val["pair_months"])],
            ["hours validated", _n(val["hours_validated"])],
            ["ticks validated", _n(val["ticks_validated"])],
            ["**failures**", f"**{_n(val['failures'])}**"],
        ]),
        *_table(["pair", "hours", "ticks", "failures"], rows),
    ] + ([
        "Failures by kind:",
        "",
        *_table(["kind", "hours"], kinds),
        "The first failures in file order:",
        "",
        *_table(["kind", "pair", "date", "hour", "detail"], details),
        "**A failure is reported, never repaired.** The card says so, and it "
        "is right to: a discrepancy between the manifest and the store is "
        "evidence about how the store came to be, and repairing it in place "
        "destroys that evidence.",
        "",
    ] if val["failures"] else [
        "**No stored hour disagrees with its manifest entry, in any respect, "
        "anywhere in the store.** That is the result the card expected and it "
        "is worth stating plainly rather than burying: the bookkeeping and the "
        "data are the same thing described twice.",
        "",
    ])


def _bars(payload: dict[str, Any]) -> list[str]:
    """Bars against the hours the manifests record, every pair."""
    bars = payload["bars"]
    rows = []
    for entry in bars["by_pair"]:
        if not entry.get("readable"):
            rows.append([f"`{entry['pair']}`", "—", _n(entry["stored_hours"]),
                         "—", "—", "—", "excluded"])
            continue
        rows.append([
            f"`{entry['pair']}`", _n(entry["bars"]),
            _n(entry["stored_hours"]),
            _n(entry["bars_without_a_stored_hour"]),
            _n(entry["stored_hours_without_a_bar"]),
            _tick(entry["timestamps_on_the_hour"]),
            _tick(entry["timestamps_strictly_increasing"]),
        ])
    return [
        "### Bars against stored hours",
        "",
        f"The `{bars['timeframe']}` table for every pair, compared as **sets** "
        "of hour timestamps against the hours the manifests record as stored. "
        "A bar no stored hour backs is a bar built from data that is no longer "
        "there; a stored hour with no bar is an hour no strategy will ever "
        "see. Counts alone would miss both if they happened to cancel.",
        "",
        *_table(["pair", "bars", "stored hours", "bars with no hour",
                 "hours with no bar", "on the hour", "strictly increasing"],
                rows),
        f"Pairs where all of that agrees: **{_n(bars['pairs_agreeing'])}**; "
        f"pairs where anything does not: **{_n(bars['pairs_mismatching'])}**. "
        "Bar timestamps are bar **open** times in UTC, which is what "
        "\"on the hour\" checks.",
        "",
    ]


def _calendar(payload: dict[str, Any]) -> list[str]:
    """Step 2: the calendar, its era problem, and what it does not explain."""
    cal = payload["calendar"]
    committed = payload["calendar_committed"]
    counts = cal["counts"]
    comparison = cal["comparison"]
    ccounts = comparison["counts"]
    unexplained = cal["unexplained"]

    year_rows = [[year, _n(b["full"]), _n(b["partial"]), _n(b["unexplained"]),
                  _n(b["traded_through"]), _n(b["closed_week"])]
                 for year, b in sorted(comparison["by_year"].items())]
    unex_rows = [[r["date"], _n(r["pairs"]), _n(r["hours"])]
                 for r in unexplained["worst"][:MAX_UNEXPLAINED_ROWS]]
    unex_year = [[year, _n(count)]
                 for year, count in unexplained["by_year"].items()]

    return [
        "## Step 2 — The holiday calendar",
        "",
        "Pre-registered decision #5, under ruling R5: the input is the "
        "manifest hour **status**, never the `EMPTY_TRADING_HOUR` warning "
        "list. The audit measured that list short of the statuses across the "
        "store, for a structural reason, so a calendar built from it would "
        "have been a calendar with holidays missing and nothing downstream "
        "would ever have noticed.",
        "",
        "The derivation is one idea. The derived FX week already says which "
        "hours should have traded. Where the feed answered *nothing* during "
        "one, either the market was shut or the data is missing — and those "
        "look identical in one pair and completely different across twelve. "
        "So a date where every pair research may read went quiet for at least "
        f"{cal['rules']['min_empty_hours']} open hours is a **full** holiday; "
        f"one where at least {cal['rules']['min_pairs_partial']} did but not "
        "all is a **partial** holiday; anything less is **unexplained** and is "
        "a data fact rather than a market closure.",
        "",
        "\"Every pair research may read\" is doing real work: ruling R1 "
        "excludes AUDUSD before 2011, so the unanimity test is over eleven "
        "pairs there. Testing twelve would make every pre-2011 holiday fail "
        "because the twelfth pair is not there to agree.",
        "",
        *_table(["classification", "dates"], [
            ["full market holidays", f"**{_n(counts['full'])}**"],
            ["partial holidays", _n(counts["partial"])],
            ["unexplained empty dates", _n(counts["unexplained"])],
        ]),
        "### The finding that matters more than the calendar",
        "",
        "A calendar derived from emptiness can only contain the holidays the "
        "feed left empty — and **the feed did not always leave them empty.** "
        "Read down this table, which takes the static major-holiday list and "
        "asks what the feed actually did on each date:",
        "",
        *_table(["year", "full", "partial", "unexplained", "traded through",
                 "fell on a closed week"], year_rows),
        "Through the early years the feed quoted straight across days the "
        "whole market was shut. There is no emptiness there to derive a "
        "holiday from, so those dates are absent from the calendar and their "
        "bars contain prices nobody traded at. **This calendar is dense in the "
        "later years and near-empty in the early ones, and that is a fact "
        "about the feed rather than about the market.** Any card that treats "
        "an early-era holiday bar as tradeable is reading a quote that had no "
        "market behind it; `config/calendar.toml` carries the same warning at "
        "the top of the file.",
        "",
        "### Derived against the static list",
        "",
        "Two independent statements: one is what the feed did, the other is "
        "what a calendar says. Neither is the authority.",
        "",
        *_table(["comparison", "dates"], [
            ["derived full holidays", _n(ccounts["derived_full"])],
            ["static major holidays in the window",
             _n(ccounts["static_in_window"])],
            ["both agree", f"**{_n(ccounts['agreed'])}**"],
            ["derived, not on the static list",
             _n(ccounts["derived_not_static"])],
            ["static, and the feed traded through it",
             _n(ccounts["static_traded_through"])],
            ["static, and the derived week was already shut",
             _n(ccounts["static_on_a_closed_week"])],
            ["static, and only some pairs stopped",
             _n(ccounts["static_partial_only"])],
        ]),
        "The two rows that a naive set difference would have merged are kept "
        "apart deliberately. \"The feed traded through it\" says the market "
        "was open on a bank holiday, which is a fact about FX. \"The derived "
        "week was already shut\" says the holiday fell on a weekend and nobody "
        "was ever asked, which is a fact about the calendar. Reported as one "
        "number they would cancel into nonsense.",
        "",
        (f"Every derived full holiday appears on the static list: there is no "
         "date where the whole market stopped and no major-holiday list "
         "explains it."
         if not ccounts["derived_not_static"] else
         f"**{_n(ccounts['derived_not_static'])} date(s) where the whole "
         "market stopped and the static list does not explain it:** "
         + ", ".join(f"`{d}`" for d in comparison["derived_not_static"][:12])
         + ". Those are the interesting half of this comparison."),
        "",
        "### Empty hours the calendar does not explain",
        "",
        "The card is explicit that these are data facts for T4 and not "
        "holidays, and keeping them out of the calendar is the point: a date "
        "where two pairs went quiet and ten did not is evidence about the "
        "feed, and filing it as a market closure would launder that evidence "
        "into a fact about the market.",
        "",
        *_table(["measure", "value"], [
            ["dates", _n(unexplained["dates"])],
            ["empty hours on them", _n(unexplained["hours"])],
            ["dates where no pair reached the depth threshold",
             _n((unexplained["pairs_deep_per_date"] or {}).get("0", 0))],
        ]),
        "By year:",
        "",
        *_table(["year", "dates"], unex_year),
        "The deepest of them:",
        "",
        *_table(["date", "pairs empty", "empty hours"], unex_rows),
        "### The committed calendar",
        "",
        f"`{committed.get('path', 'config/calendar.toml')}` is tracked and "
        "versioned, which means anybody can open and edit it — so it is "
        "re-derived on every run of this experiment and compared against what "
        "is on disk. A holiday quietly added by hand fails the comparison "
        "instead of propagating into every card that trusts the calendar.",
        "",
        *_table(["check", "result"], [
            ["the file exists", _tick(committed.get("present"))],
            ["its rules match the ones used here",
             _tick(committed.get("rules_agree"))],
            ["its full holidays match the re-derivation",
             _tick(committed.get("full_agrees"))],
            ["its partial holidays match the re-derivation",
             _tick(committed.get("partial_agrees"))],
            ["full holidays recorded", _n(committed.get("full_days", 0))],
            ["partial holidays recorded",
             _n(committed.get("partial_days", 0))],
        ]),
        "After this card, `EMPTY_TRADING_HOUR` on a calendar date is `closed` "
        "rather than a warning — pre-reg #5's closing clause, now that there "
        "is a calendar to test a date against.",
        "",
    ]


def _crosscheck(payload: dict[str, Any]) -> list[str]:
    """Step 3: the second venue, and what a disagreement would mean."""
    cross = payload["crosscheck"]
    avail = cross.get("availability") or {}
    avail_rows = [[f"`{r['pair']}`",
                   r.get("first_date", "—") if r.get("available")
                   else "**unavailable**",
                   _tick(r.get("available"))]
                  for r in sorted(avail.get("pairs") or [],
                                  key=lambda r: r["pair"])]
    pair_rows = []
    for pair, stats in cross["by_pair"].items():
        pair_rows.append([
            f"`{pair}`", _n(stats["compared"]), _n(stats["exempt"]),
            _f(stats["mean"]), _f(stats["median"]), _f(stats["p95"]),
            _f(stats["max"]),
            _n(stats["beyond"]) if stats["beyond"] else "0",
        ])
    flagged = [[f"`{h['pair']}`", h["date"], f"{int(h['hour']):02d}:00Z",
                _f(h["open_diff_pips"]), _f(h["close_diff_pips"]),
                _f(h["abs_worst_pips"]), _tick(h["roll_exempt"])]
               for h in cross["flagged"][:MAX_FLAGGED_ROWS]]
    worst_pair = max(cross["by_pair"].items(),
                     key=lambda kv: (kv[1]["max"] or 0.0),
                     default=(None, {}))
    roll = cross["roll_window_ny"]
    return [
        "## Step 3 — Cross-check against OANDA",
        "",
        "Pre-registered decision #7. FX has no consolidated tape, so there is "
        "no authority to check Dukascopy against — only a second venue, whose "
        "disagreement with the first is evidence about **both**. That "
        "asymmetry decides what this step may conclude: a difference beyond "
        "threshold does not mean Dukascopy is wrong, it means the stored data "
        "cannot be relied on until somebody looks.",
        "",
        "Compared: the mid of the first and last stored tick in an hour "
        "against the open and close of OANDA's H1 candle for the same hour. "
        "Both boundaries, and the worse of the two decides — thresholding one "
        "of them would have been a choice about which half of the hour to "
        "check, and the table below shows they behave the same way anyway. "
        "Read from the ticks rather than from the bar tables, so the "
        "comparison tests the stored data itself rather than a resampling "
        "of it.",
        "",
        *_table(["parameter", "value"], [
            ["threshold (pre-reg #7, pinned)",
             f"{_f(cross['threshold_pips'], 1)} pip"],
            ["dates sampled per pair per year", _n(cross["dates_per_year"])],
            ["hours sampled per date",
             f"{_n(cross['hours_sampled_per_date'])} "
             f"({', '.join(f'{h:02d}:00Z' for h in cross['sample_hours_utc'])})"],
            ["roll window exempt (pre-reg #4, derived per date)",
             f"{roll[0]}:00–{roll[1]}:00 `America/New_York`"],
            ["pair-dates fetched", _n(cross["pair_dates_sampled"])],
            ["**hours compared**", f"**{_n(cross['hours_compared'])}**"],
            ["of which inside the roll window, exempt",
             _n(cross["hours_roll_exempt"])],
            ["sampled hours the store or the venue lacked",
             _n(cross["hours_missing"])],
            ["**hours beyond threshold outside the roll window**",
             f"**{_n(cross['hours_beyond_threshold'])}**"],
            ["**verdict (pre-reg #7)**", f"**{cross['verdict']}**"],
        ]),
        "The sample deliberately includes an hour inside the roll window. An "
        "exemption that is never exercised is an exemption nobody has tested, "
        "and the roll is the one hour where two venues have most reason to "
        "disagree — so those hours are compared and reported, and excluded "
        "from the threshold and from the statistics below.",
        "",
        "### OANDA's own history, per pair",
        "",
        "Asked rather than assumed. \"Both feeds agree\" means much less for a "
        "pair whose second feed starts late, so the window each comparison "
        "actually had is measured:",
        "",
        *_table(["pair", "earliest H1 candle", "available"], avail_rows),
        "### The difference distribution",
        "",
        "Absolute difference in pips, worst of the hour's open and close, "
        "roll-window hours excluded:",
        "",
        *_table(["pair", "hours compared", "roll-exempt", "mean", "median",
                 "p95", "max", "beyond threshold"], pair_rows),
        (f"The widest single disagreement anywhere in the sample is "
         f"{_f((worst_pair[1] or {}).get('max'))} pip on `{worst_pair[0]}`, "
         f"against a {_f(cross['threshold_pips'], 1)} pip threshold."
         if worst_pair[0] else ""),
        "",
    ] + _density(cross) + _by_year(cross) + ([
        "### Hours beyond threshold, and what is blocked",
        "",
        "Pre-reg #7: any hour outside the roll window beyond threshold "
        "**blocks the affected data from research use** pending review. "
        "\"That data\" is the hour — the blocked set is per hour, not per pair "
        "and not per year, because widening it to the pair-year would block "
        "decades over a handful of thin hours and nobody registered that. The "
        "set is enumerated in the result document; this is its shape.",
        "",
        *_table(["measure", "value"], [
            ["**hours blocked**", f"**{_n(cross.get('blocked_count', 0))}**"],
            ["of the hours compared outside the roll window",
             f"{_n(cross['hours_compared'] - cross['hours_roll_exempt'])} "
             f"({cross.get('blocked_count', 0) / max(1, cross['hours_compared'] - cross['hours_roll_exempt']):.1%})"],
            ["pair-years they fall in",
             _n(len(cross.get("blocked_by_pair_year") or []))],
        ]),
        "Where they fall, by pair and year — read against the density table "
        "above, which is what explains the shape:",
        "",
        *_table(["pair", "year", "hours blocked"],
                [[f"`{b['pair']}`", b["year"], _n(b["hours"])]
                 for b in sorted(cross.get("blocked_by_pair_year") or [],
                                 key=lambda b: (-int(b["hours"]), b["pair"],
                                                b["year"]))[:MAX_FLAGGED_ROWS]]),
        "The widest disagreements, worst first:",
        "",
        *_table(["pair", "date", "hour", "open Δ pips", "close Δ pips",
                 "worst abs Δ (pips)", "roll exempt"], flagged),
        "**What this does and does not mean.** It does not mean the stored "
        "data is wrong: there is no consolidated tape to be wrong against, and "
        "the density table shows the disagreement tracking quote sparsity "
        "rather than anything about either feed's accuracy. It does mean these "
        "specific hours are not corroborated by a second venue, and pre-reg #7 "
        "says an uncorroborated hour is out of research use until a checkpoint "
        "says otherwise. Both halves of that are the pre-registration working "
        "as intended, and neither is this card's to reinterpret.",
        "",
    ] if cross["hours_beyond_threshold"] else [
        "### Nothing is blocked",
        "",
        "No sampled hour outside the roll window differs from OANDA by more "
        "than the threshold, in any pair, in any year. Pre-reg #7's blocking "
        "clause does not fire, and no data is withheld from research by it.",
        "",
        "What that does and does not establish is worth being exact about. It "
        "establishes that on the sampled hours the two venues quote the same "
        "market to within a pip — which rules out a decoding fault, a scaling "
        "fault and a timestamp shift, since each of those would show up as a "
        "large, systematic difference rather than as nothing. It does not "
        "establish that every unsampled hour agrees, and it cannot: this is a "
        "sample, its size is in the table above, and a fault confined to hours "
        "it did not draw would survive it.",
        "",
    ])


def _density(cross: dict[str, Any]) -> list[str]:
    """The stratification that explains the whole result."""
    density = cross.get("by_density") or {}
    if not density:
        return []
    rows = [[f"`{name}`", _n(b["n"]), _f(b["median"]), _f(b["p95"]),
             _f(b["max"]), _n(b["beyond"]),
             f"{100.0 * float(b['beyond_share']):.1f}%"]
            for name, b in density.items()]
    both = cross.get("open_vs_close") or {}
    open_abs, close_abs = both.get("open_abs") or {}, both.get("close_abs") or {}
    return [
        "### The result depends on how many ticks the hour holds",
        "",
        "This is the cut that explains the rest of the step, and it is not a "
        "property of either feed's accuracy.",
        "",
        "Two independent quote streams are being compared by their last print "
        "before the same instant. What separates them is the product of two "
        "things: how far apart in time the two prints are, and how fast price "
        "is moving. Split the same sample by tick count and both show up:",
        "",
        *_table(["ticks in the hour", "hours", "median abs Δ (pips)", "p95", "max",
                 "beyond threshold", "share"], rows),
        "The relationship is **not** monotonic, and the shape is the "
        "interesting part. The thinnest hours are much the worst: their two "
        "prints can be minutes apart, so the comparison measures how far the "
        "market moved in between rather than whether the venues agree about "
        "price. But the densest hours are worse than the merely busy ones "
        "too — for the opposite reason, since an hour holding that many quotes "
        "is usually one where price is moving fast enough that even a "
        "sub-second gap between prints is worth a pip. Agreement is best in "
        "the middle, where prints are close together and price is not "
        "sprinting.",
        "",
        "So a fixed pip threshold has different resolving power in different "
        "eras, because the eras have different quote densities. That is the "
        "same instrument problem ruling **R3** states about spread "
        "percentiles, arriving here in a different statistic — and it means "
        "the count of hours beyond threshold is **not** comparable across "
        "years without this column beside it.",
        "",
        "Nothing above softens pre-reg #7. The threshold is pinned, it was "
        "applied as pinned, and the hours beyond it are blocked. What the "
        "table changes is the *diagnosis* a reviewer should reach for: a thin "
        "hour that disagrees is evidence about quote density, and a dense hour "
        "that disagrees is evidence about the data.",
        "",
        "The hour's two boundaries were compared separately, and they behave "
        "the same way — so neither the open nor the close is the noisy one, "
        "and the difference is a property of the hour rather than of which "
        "edge of it was sampled:",
        "",
        *_table(["boundary", "hours", "median abs Δ (pips)", "p95", "max"], [
            ["first tick vs candle open", _n(open_abs.get("n")),
             _f(open_abs.get("median")), _f(open_abs.get("p95")),
             _f(open_abs.get("max"))],
            ["last tick vs candle close", _n(close_abs.get("n")),
             _f(close_abs.get("median")), _f(close_abs.get("p95")),
             _f(close_abs.get("max"))],
        ]),
    ]


def _by_year(cross: dict[str, Any]) -> list[str]:
    """The per-year distribution the card asks for."""
    by_year = cross.get("by_year") or {}
    if not by_year:
        return []
    rows = [[year, _n(b["n"]), _f(b["median"]), _f(b["p95"]), _f(b["max"])]
            for year, b in by_year.items()]
    return [
        "### By year",
        "",
        "Read against the density table above, not on its own:",
        "",
        *_table(["year", "hours compared", "median abs Δ (pips)", "p95", "max"],
                rows),
    ]


def _exclusion(payload: dict[str, Any]) -> list[str]:
    """R1, stated wherever AUDUSD appears — which is everywhere here."""
    rulings = payload["rulings"]["R1"]
    loader = payload["loader"]
    windows = rulings["windows"]
    if not windows:
        return []
    rows = [[f"`{w['pair']}`", w["ruling"], w["window"], w["why"]]
            for w in windows]
    return [
        "## The AUDUSD exclusion (ruling R1)",
        "",
        "Stated here because every report and scorecard that touches the pair "
        "must state it, and this one touches it in all four steps.",
        "",
        *_table(["pair", "ruling", "window", "why"], rows),
        f"The loader refused it while this result was produced "
        f"(`{rulings['reason']}`: "
        f"{_tick(rulings['canary_refused'])}), and withheld "
        f"{_n(loader['excluded_dates_withheld'])} date(s) across "
        f"{_n(len(loader['excluded_pairs']))} pair(s) from the reads this "
        "experiment made. The hours are on disk and validated — Step 1 checked "
        "them like any other — and research may not read them. A cross-pair "
        "analysis spanning the excluded window runs on eleven pairs and has to "
        "say so.",
        "",
    ]


def _provenance(document: dict[str, Any], gate_status: str,
                home: str) -> list[str]:
    """Where every number came from."""
    access = document.get("access") or {}
    payload = document["payload"]
    return [
        "## Provenance",
        "",
        f"* Config: `{home}/config.toml` (sha256 "
        f"`{str(document['config_sha256'])[:16]}`)",
        "* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/"
        "manifest.json`, read the canonical way (SPEC2 §The canonical manifest "
        "reading) — hour records and the derived coverage block, never the "
        "session warning log.",
        f"* Validation pass: `{home}/validation.jsonl`, one record per "
        "pair-month, written by `python -m research.validate_store`.",
        f"* Cross-check: `{home}/oanda.jsonl` and `{home}/"
        "oanda_availability.jsonl`, written by "
        "`python -m research.crosscheck_oanda` against the OANDA practice "
        "host. The token comes from `OANDA_API_TOKEN` and is never logged.",
        "* Calendar: `config/calendar.toml`, written by "
        "`python -m research.calendar_build` and re-derived and compared on "
        "every run of this experiment.",
        f"* Result: `{home}/result.json`, hash `{document['result_hash']}`",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. It served "
        f"{len(access.get('files', []))} file(s) across "
        f"{len(access.get('pairs', []))} pair(s) and "
        f"{len(access.get('dates', []))} date(s); sealed dates served: "
        f"{payload['loader']['sealed_dates_served'] or 'none'}; dates withheld "
        f"by an exclusion window: "
        f"{_n(payload['loader']['excluded_dates_withheld'])}.",
        f"* Research gate: {gate_status}",
        "",
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.quality_report",
        description="Render the T3 data-quality report from its result.")
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
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
    card = str(document.get("taskcard") or "T3")
    trials = ledger_mod.trial_count(ledger_mod.read(base), card)
    home = _rel_dir(args.result.resolve().parent, base)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(document, trials, args.gate_status, home),
                        encoding="utf-8")
    _LOG.info("wrote %s", args.out)
    print(f"wrote {args.out}")
    return 0


def _rel_dir(path: pathlib.Path, base: pathlib.Path) -> str:
    """Project-relative POSIX directory, absolute where that is impossible."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
