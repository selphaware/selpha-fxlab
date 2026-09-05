"""Render an ingestion report from its result document.

Generated rather than written by hand, for the reason the result is hashed: a
number that appears in a report but not in the result document is a number
nobody can check. Every figure below is read out of ``result.json``; the prose
around them is parameterised by those figures, so regenerating after a further
ingest session produces a report that still agrees with itself.

Ruling R6 is what this module now has to satisfy end to end. The first version
of it hardcoded the string ``T2a`` in six places and rendered T2b's numbers
under T2a's name; worse, three of its sharpest claims -- the AUDUSD gap split,
the episode totals and the by-year spread counts -- arrived as authored ``--note``
prose and were left behind when the store changed underneath them. Everything
identifying the card now comes from the result document, and everything
countable comes from the payload. ``--note`` survives for facts a result
genuinely cannot carry, and a note carrying a number is a bug in the note.

The one external reading is T1's result, used for the completeness comparison
the card asks for. It is read from ``experiments/T1-coverage/result.json`` and
quoted, not recomputed.

Nothing here decides anything. Coverage shortfalls, boundary anomalies and
validation warnings are tabulated and left tabulated: what they mean for the
holiday calendar is T3's card, what they mean for a strategy is T4's onward,
and universe membership is a checkpoint decision (SPEC2 pre-reg #3).
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
from typing import Any, Final, Sequence

_LOG: Final[logging.Logger] = logging.getLogger("research.ingest_report")

#: Gap rows shown in full before the table is truncated with a count.
MAX_GAP_TABLE_ROWS: Final[int] = 120


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


def _pct(fraction: float) -> str:
    """A fraction rendered as a percentage to two decimals."""
    return f"{100.0 * float(fraction):.2f}%"


def _gib(value: Any) -> str:
    """Bytes as GiB to two decimals."""
    return f"{int(value) / (1024 ** 3):.2f} GiB"


def _mib(value: Any) -> str:
    """Bytes as MiB to one decimal."""
    return f"{int(value) / (1024 ** 2):.1f} MiB"


def _hours(seconds: Any) -> str:
    """Seconds as hours to one decimal."""
    return f"{float(seconds) / 3600.0:.1f} h"


def render(document: dict[str, Any], t1: dict[str, Any] | None,
           trials: int, gate_status: str,
           notes: Sequence[str] = (),
           experiment_dir: str | None = None) -> str:
    """Build the whole report.

    Args:
        document: The result document of the ingestion being reported.
        t1: T1's result document, for the completeness comparison; may be None.
        trials: Ledger trial count for this task card (pre-reg #10).
        gate_status: What the research gate said, quoted in the provenance.
        notes: Authored observations for facts the result cannot carry.
        experiment_dir: Project-relative experiment directory, for provenance.
            Derived from the result's own location when omitted.

    Returns:
        The report as Markdown.
    """
    payload = document["payload"]
    window = payload["window"]
    totals = payload["totals"]
    pairs = payload["pairs"]
    names = sorted(pairs)
    card = str(document.get("taskcard") or document.get("experiment_id") or "?")
    home = experiment_dir or f"experiments/{document.get('experiment_id', '?')}"
    lines: list[str] = []

    lines += _header(document, window, trials, card)
    lines += _totals(window, totals, payload)
    lines += _exclusions(payload)
    lines += _per_pair(names, pairs, t1)
    lines += _by_year(names, pairs)
    lines += _gaps(payload)
    lines += _validation(names, pairs, payload)
    lines += _throughput(payload, window)
    lines += _storage(names, pairs, totals)
    lines += _bars(names, pairs, payload, totals)
    lines += _observations(names, pairs, payload, t1, notes)
    lines += _provenance(document, gate_status, home)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def _header(document: dict[str, Any], window: dict[str, Any],
            trials: int, card: str) -> list[str]:
    """Title, provenance line and what this is."""
    return [
        f"# {card} — Bulk ingestion, "
        f"{window['start']} → {window['end']}, {window['pairs']} pairs",
        "",
        f"**Task card:** `taskcards/{card}.md` · **Experiment:** "
        f"`{document['experiment_id']}` · **Seed:** {document['seed']} · "
        f"**Result hash:** `{document['result_hash'][:16]}`",
        "",
        f"**Trials ledgered under {card}:** {trials} (SPEC2 pre-reg #10; the "
        "count includes the bulk-ingest sessions, which are data collection "
        "rather than analysis).",
        "",
        "This is an **ingestion**, not an analysis. Every number below is read "
        "back off disk — from the sharded manifests, from the tick store's own "
        "directory listings, and from the bar tables through the research "
        "loader. No strategy content appears anywhere in it, the experiment is "
        "not scorable and it carries no scorecard.",
        "",
        "Two things are worth stating before the numbers, because they decide "
        "what the numbers mean.",
        "",
        "* **Every hour went through the identical Phase 1 pipeline.** The "
        "driver decides the order, the rate and what to do about an outage; it "
        "decodes, validates and stores nothing. Crossed quotes, non-positive "
        "prices, Saturday ticks and out-of-hour ticks reject an hour here "
        "exactly as they do in the Phase 1 gate, and duplicates are dropped and "
        "counted rather than tolerated silently.",
        "* **Closed hours are derived, not assumed.** The FX week tracks 17:00 "
        "`America/New_York`, so it sits at 21:00 UTC in northern summer and "
        "22:00 UTC in winter. Hours the derived boundary calls shut are recorded "
        "as `closed` without being fetched — with one deliberate exception: the "
        "shut hour on either side of every boundary **is** fetched, so the "
        "derivation is checked against the feed every week rather than trusted. "
        "The result of that check is in *Validation anomalies*.",
        "",
    ]


def _totals(window: dict[str, Any], totals: dict[str, Any],
            payload: dict[str, Any]) -> list[str]:
    """The one-table answer to 'what is in the store'."""
    accounted = int(totals["hours_ok"]) + int(totals["hours_empty"])
    expected = int(totals["expected_open_hours"])
    return [
        "## What is in the store",
        "",
        *_table(["measure", "value"], [
            ["window", f"{window['start']} → {window['end']} "
                       f"({_n(window['days'])} days)"],
            ["pairs", _n(window["pairs"])],
            ["hours in the range, per pair", _n(window["expected_hours_per_pair"])],
            ["of which the derived week calls open, per pair",
             _n(window["expected_open_hours_per_pair"])],
            ["**open hours expected, all pairs**", f"**{_n(expected)}**"],
            ["open hours accounted for (`ok` + `empty`)",
             f"{_n(accounted)} ({_pct(accounted / expected if expected else 0)})"],
            ["hours stored with ticks (`ok`)", _n(totals["hours_ok"])],
            ["open hours the feed served empty (`empty`)", _n(totals["hours_empty"])],
            ["hours recorded closed (`closed`)", _n(totals["hours_closed"])],
            ["**gaps**", f"**{_n(totals['hours_gap'])}**"],
            ["manifest entries written", _n(totals["hours_recorded"])],
            ["ticks stored", _n(totals["ticks"])],
            ["duplicate ticks dropped", _n(totals["duplicates_dropped"])],
            ["tick Parquet files", _n(totals["stored_files"])],
            ["tick store on disk", _gib(totals["stored_bytes"])],
            ["bar rows inside this window", _n(totals["bar_rows"])],
            ["compressed bytes served by the feed",
             _gib(totals["compressed_bytes"])],
        ]),
        "An hour is `ok` when it decoded, validated and stored; `empty` when the "
        "feed served a zero-byte body during an hour the derived week calls "
        "open; `closed` when the week was shut; and a `gap` when it could not be "
        "had at all. Every requested hour has exactly one entry, closed ones "
        "included — a pipeline whose failures are invisible produces a dataset "
        "whose holes are invisible too.",
        "",
    ]


def _exclusions(payload: dict[str, Any]) -> list[str]:
    """What a data-quality exclusion removes from this window (R1).

    Stated as a number rather than as absence, because absence is what a pair
    that was never pulled also looks like. The hours below **are** in the
    store; research may not read them, and every report that touches the pair
    has to say so.
    """
    block = payload.get("exclusions") or {}
    rulings = block.get("rulings") or []
    if not rulings:
        return []
    hours = block.get("hours_excluded") or {}
    bars = block.get("bar_rows_excluded") or {}
    rows = []
    for entry in rulings:
        pair = entry["pair"]
        counts = hours.get(pair) or {}
        rows.append([
            f"`{pair}`", entry["ruling"], entry["window"],
            _n(counts.get("ok", 0)), _n(counts.get("empty", 0)),
            _n(counts.get("gap", 0)), _n(counts.get("ticks", 0)),
            _n(sum((bars.get(pair) or {}).values())),
        ])
    detail = []
    for entry in rulings:
        detail.append(f"* `{entry['pair']}` {entry['window']} "
                      f"(ruling {entry['ruling']}): {entry['why']}.")
    return [
        "## Data excluded from research (ruling R1)",
        "",
        "These hours were ingested, validated and stored. A ruling then put "
        "them out of reach of research, and `research.loader` refuses them "
        "with `PAIR_EXCLUDED_WINDOW`. The counts are what the ruling costs "
        "this window; every other table in this report describes the "
        "**ingestion**, which did happen, and so still includes them.",
        "",
        *_table(["pair", "ruling", "window", "ok hours", "empty", "gap",
                 "ticks", "bar rows"], rows),
        *detail,
        "",
    ]


def _per_pair(names: Sequence[str], pairs: dict[str, Any],
              t1: dict[str, Any] | None) -> list[str]:
    """Coverage per pair, against the derived calendar and against T1."""
    years = sorted({y for pair in names for y in pairs[pair]["by_year"]})
    t1_fraction = _t1_data_fraction(t1, years)
    rows = []
    for pair in names:
        entry = pairs[pair]
        totals = entry["totals"]
        expected = int(entry["expected_open_hours"])
        rows.append([
            f"`{pair}`",
            _n(totals["hours_ok"]),
            _n(totals["hours_empty"]),
            _n(totals["hours_closed"]),
            _n(totals["hours_gap"]),
            _pct(entry["open_hour_completeness"]),
            (_pct(t1_fraction[pair]) if pair in t1_fraction else "—"),
            _n(entry["days_with_data"]),
            _n(totals["ticks"]),
            _n(totals["duplicates_dropped"]),
        ])
    return [
        "## Per-pair coverage",
        "",
        *_table(["pair", "ok", "empty", "closed", "gap",
                 "open-hour completeness", "T1 data %, same years",
                 "days with data", "ticks", "dupes dropped"], rows),
        "**Open-hour completeness** is `(ok + empty) / open hours the derived "
        "week contains`. It reaches 100% when every open hour of the range is "
        "accounted for — including the ones the feed answered empty, which are "
        "an answer rather than a hole.",
        "",
        "**T1 data %** is the comparison the card asks for, quoted from the "
        "coverage survey and re-totalled over exactly the years this card "
        "covers: the share of trading days whose 13:00 UTC probe returned data. "
        "It is still a *different* measurement — one hour a day against every "
        "hour of every day — so the two columns are not expected to be equal. "
        "T1's number is depressed by closed trading days its single probe could "
        "not tell apart from absent ones; this column separates them.",
        "",
    ]


def _by_year(names: Sequence[str], pairs: dict[str, Any]) -> list[str]:
    """Completeness by pair and year, which is where a hole would show."""
    years = sorted({y for pair in names for y in pairs[pair]["by_year"]})
    rows = []
    for pair in names:
        cells: list[Any] = [f"`{pair}`"]
        for year in years:
            bucket = pairs[pair]["by_year"].get(year)
            if not bucket:
                cells.append("—")
                continue
            expected = int(bucket.get("expected_open_hours", 0))
            accounted = int(bucket.get("open_hours_accounted", 0))
            cells.append(_pct(accounted / expected) if expected else "—")
        rows.append(cells)
    gap_rows = []
    for pair in names:
        for year in years:
            bucket = pairs[pair]["by_year"].get(year) or {}
            if int(bucket.get("hours_gap", 0)):
                gap_rows.append([f"`{pair}`", year, _n(bucket["hours_gap"]),
                                 _n(bucket.get("hours_ok", 0))])
    return [
        "## Completeness by year",
        "",
        "Open-hour completeness per pair per year. This is the table a missing "
        "region would show up in.",
        "",
        *_table(["pair", *years], rows),
        "Years carrying at least one gap:",
        "",
        *_table(["pair", "year", "gap hours", "ok hours"], gap_rows),
    ]


def _gaps(payload: dict[str, Any]) -> list[str]:
    """The gap table the card asks for, with dates."""
    gaps = payload["gaps"]
    rows = [[f"`{r['pair']}`", r["date"], f"{int(r['hour']):02d}:00Z",
             ", ".join(r["reasons"]) or "—", (r.get("detail") or "")[:160]]
            for r in gaps["rows"][:MAX_GAP_TABLE_ROWS]]
    trailer = []
    if int(gaps["count"]) > MAX_GAP_TABLE_ROWS:
        trailer = [f"…and {_n(int(gaps['count']) - MAX_GAP_TABLE_ROWS)} more. "
                   f"The result document lists {_n(gaps['listed'])} of "
                   f"{_n(gaps['count'])}; the counts everywhere else in this "
                   "report are complete.", ""]
    body = [
        "## Gaps",
        "",
        f"**{_n(gaps['count'])}** hour(s) could not be had. A gap is an hour "
        "that exhausted its retries *while the feed was answering everything "
        "else*, or that arrived and would not decode or validate. An hour that "
        "failed while the feed was answering nothing at all is not a gap: the "
        "session parked and asked again, and an hour nobody finished asking "
        "about was left unsettled rather than recorded as a hole.",
        "",
    ]
    if not int(gaps["count"]):
        body += ["No hour of the range is missing.", ""]
    else:
        body += _table(["pair", "date", "hour", "reason", "detail"],
                       rows) + trailer

    history = (payload.get("throughput") or {}).get("gap_history") or {}
    recorded = int(history.get("recorded_during_pull", 0))
    if recorded:
        recovered = int(history.get("recovered_by_sweep", 0))
        affected = int(history.get("pair_months_affected", 0))
        # What the survivors *are* decides what this section may claim. An
        # hour the feed never served is absent history; an hour it served and
        # validation refused is the opposite -- history the pipeline declined
        # to store. Only the reason split tells them apart, so the wording is
        # derived from it rather than assumed.
        reasons = dict(gaps.get("by_reason") or {})
        fetchish = sum(v for k, v in reasons.items()
                       if k in {"FETCH_ERROR", "EMPTY_BODY", "UNKNOWN"})
        refused = sum(reasons.values()) - fetchish
        if recovered == recorded and recorded:
            verdict = ("Every gap this run recorded was the second kind. None "
                       "of them was a hole in Dukascopy's history; all of them "
                       "were hours the feed declined at the moment it was "
                       "first asked and served without complaint when asked "
                       "again.")
        elif refused and refused >= fetchish:
            verdict = (
                f"{_n(recorded - recovered)} hour(s) survived, and they are "
                f"not the first kind either: **{_n(refused)}** of them carry a "
                "validation reason rather than a fetch failure. The feed "
                "served those hours; this pipeline refused them. They are "
                "neither absent history nor a feed in a bad mood, but data "
                "that arrived and did not pass — which is why the reason "
                "token, not the count, is the part worth reading."
                + (f" The remaining {_n(fetchish)} could not be had at all."
                   if fetchish else ""))
        else:
            verdict = (f"{_n(recorded - recovered)} hour(s) survived the sweep "
                       "and are listed above; those are the ones that look "
                       "like absent history.")
        body += [
            "### What the pull recorded, and what the sweep recovered",
            "",
            f"The count above is the **end** state, and on its own it flatters "
            f"the run. During the pull itself **{_n(recorded)}** hour(s) across "
            f"{_n(affected)} pair-month(s) were recorded as gaps. The card's "
            f"closing sweep re-asked every one of them, and "
            f"**{_n(recovered)}** came back.",
            "",
            f"That is the difference between a gap meaning *absent history* and "
            f"a gap meaning *a feed in a bad mood on the Tuesday it was asked*. "
            + verdict,
            "",
            "Re-asking every gap is what makes the distinction available at "
            "all: a transient refusal clears on the second ask and a "
            "deterministic one does not. A run that reported only its final "
            "gap count would have hidden which kind it had.",
            "",
        ]
        if reasons:
            body += [
                "Surviving gaps by reason:",
                "",
                # The result document is serialised with sorted keys so its
                # hash is stable, which discards any ordering the summary
                # chose. Rank here, where it survives into the page.
                *_table(["reason", "hours"],
                        [[f"`{k}`", _n(v)] for k, v in
                         sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))]),
            ]
        body += _gap_attribution(gaps)
    return body


def _gap_attribution(gaps: dict[str, Any]) -> list[str]:
    """Which pair each reason belongs to, and what shape the runs have.

    A reason total is not an attribution. "12,998 `CROSSED_QUOTE` in AUDUSD"
    was written when 12,996 of them were AUDUSD's and two were USDJPY's, and
    the two extra then reappeared in the next sentence as though they were a
    separate finding. Both halves are derived here from the same rows.
    """
    by_pair = gaps.get("by_reason_pair") or {}
    episodes = gaps.get("episodes") or []
    sublabels = gaps.get("by_sublabel") or {}
    if not by_pair:
        return []

    rows = []
    for reason in sorted(by_pair, key=lambda r: (-sum(by_pair[r].values()), r)):
        counts = by_pair[reason]
        total = sum(counts.values())
        worst = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        rows.append([
            f"`{reason}`", _n(total), _n(len(counts)),
            f"`{worst[0]}` ({_n(worst[1])}, {worst[1] / total:.2%})",
            ", ".join(f"`{p}` {_n(c)}" for p, c in sorted(counts.items())),
        ])

    body = [
        "Which pair each reason belongs to — the attribution, not the total:",
        "",
        *_table(["reason", "hours", "pairs", "largest share", "by pair"], rows),
    ]

    long_runs = [e for e in episodes if int(e["months"]) > 1]
    if long_runs:
        body += [
            "Gaps of one reason in one pair, grouped into contiguous runs of "
            "affected months. A month with no gap ends a run, so a run here is "
            "a bounded episode rather than a period average:",
            "",
            *_table(["pair", "reason", "from", "to", "months", "hours"],
                    [[f"`{e['pair']}`", f"`{e['reason']}`", e["first_month"],
                      e["last_month"], _n(e["months"]), _n(e["hours"])]
                     for e in sorted(long_runs,
                                     key=lambda e: (-int(e["hours"]),
                                                    e["pair"],
                                                    e["first_month"]))]),
            f"The remaining {_n(len(episodes) - len(long_runs))} run(s) are a "
            "single month each.",
            "",
        ]

    if sublabels:
        body += [
            "Sub-labels, which name what *kind* of rejection a reason token "
            "covers when the token alone is ambiguous:",
            "",
            *_table(["sub-label", "hours", "by pair"],
                    [[f"`{label}`", _n(sum(counts.values())),
                      ", ".join(f"`{p}` {_n(c)}"
                                for p, c in sorted(counts.items()))]
                     for label, counts in sorted(sublabels.items())]),
            "`PRE_OPEN_FEED_DATA` is ruling R2. `CLOSED_MARKET_TICK` alone "
            "covers both a boundary that drifted and a feed that opened early; "
            "the derivation was checked and is correct on these dates, so this "
            "is the second, and the sub-label records which without anyone "
            "having to re-derive it.",
            "",
        ]
    return body


def _spread_profile(payload: dict[str, Any]) -> list[str]:
    """Where the spread warnings fall, by hour of day and by year.

    The per-pair ceilings were tuned on the modern era, so the count alone is
    uninterpretable: it could be the market or it could be the ceiling. Which
    hour they land on decides it.
    """
    profile = (payload.get("warning_profile") or {})
    hours = (profile.get("by_hour") or {}).get("SPREAD_OUTLIER") or {}
    years = (profile.get("by_year") or {}).get("SPREAD_OUTLIER") or {}
    if not hours:
        return []
    total = sum(hours.values())
    ranked = sorted(hours.items(), key=lambda kv: -kv[1])
    top_hour, top_count = ranked[0]
    share = top_count / total if total else 0.0
    top3 = sum(c for _, c in ranked[:3])
    rows = [[f"{h}:00Z", _n(c), f"{c / total:.1%}"] for h, c in ranked[:6]]
    roll = (hours.get("21", 0) + hours.get("22", 0)) / total if total else 0.0
    verdict = (
        f"{share:.0%} fall on {top_hour}:00Z alone and **{roll:.0%} on 21:00Z "
        "and 22:00Z together**. Those two hours are not two phenomena: they are "
        "the same one, the 17:00 `America/New_York` roll, which sits at 21:00Z "
        "in northern summer and 22:00Z in winter. The flags track the boundary "
        "as it moves with daylight saving, which is the same derivation the "
        "closed-hour logic uses and an independent check on it. At the roll "
        "liquidity is handed between sessions and the spread on a thin cross "
        "legitimately blows out; a flag that concentrates there is describing "
        "the market, where one scattered evenly across the clock would have "
        "been describing a ceiling set too low."
        if share >= 0.4 else
        f"They do not concentrate: the busiest hour, {top_hour}:00Z, takes "
        f"only {share:.0%}. A flag that scatters this evenly is more likely to "
        "be describing the ceiling than the market, and is worth revisiting "
        "before any card leans on the spread series."
    )
    body = [
        "#### Where the spread flags fall",
        "",
        f"`SPREAD_OUTLIER` fired on {_n(total)} hour(s). {verdict}",
        "",
        *_table(["hour (UTC)", "hours flagged", "share"], rows),
    ]
    if years:
        body += [
            "By year:",
            "",
            *_table(["year", "hours flagged"],
                    [[y, _n(c)] for y, c in sorted(years.items())]),
            "**Ruling R3 applies to this table and forbids the obvious reading "
            "of it.** A flag fires when an hour's p99.9 spread clears a fixed "
            "ceiling, and p99.9 over an hour holding a thousand ticks is not "
            "the same instrument as p99.9 over one holding six thousand. So a "
            "year with more flags may have had wider spreads, or more ticks, "
            "and this column cannot tell you which. Comparing spread regimes "
            "across eras requires a statistic that controls for ticks per hour "
            "— medians, p90, fixed-sample — and that is T5's work, not a "
            "conclusion available here.",
            "",
        ]
    return body


def _validation(names: Sequence[str], pairs: dict[str, Any],
                payload: dict[str, Any]) -> list[str]:
    """Validation anomalies, and the derived-boundary audit."""
    reasons: dict[str, int] = {}
    errors: dict[str, int] = {}
    rejected: dict[str, int] = {}
    for pair in names:
        for reason, count in pairs[pair]["warnings"].items():
            reasons[reason] = reasons.get(reason, 0) + int(count)
        for reason, count in pairs[pair]["errors"].items():
            errors[reason] = errors.get(reason, 0) + int(count)
        for reason, count in (pairs[pair].get("flags_on_rejected_hours")
                              or {}).items():
            rejected[reason] = rejected.get(reason, 0) + int(count)

    boundary = payload["week_boundary"]
    per_pair = [[f"`{p}`",
                 _n(pairs[p]["boundary"]["fetched"]),
                 _n(pairs[p]["boundary"]["closed_but_traded"]),
                 _n(pairs[p]["boundary"]["open_but_empty"])]
                for p in names]

    verdict = (
        "The derivation and the feed agree: no hour the derived week called "
        "shut came back carrying ticks."
        if not int(boundary["derived_closed_hours_with_ticks"]) else
        f"**{_n(boundary['derived_closed_hours_with_ticks'])} hour(s) the "
        "derived week called shut came back carrying ticks.** That is the "
        "boundary sitting later than the feed's, and it is exactly the failure "
        "a hardcoded 21:00 UTC produces for half of every year. The hours are "
        "stored as `ok` — the data is kept, not discarded — and the discrepancy "
        "is flagged here for the checkpoint.")

    return [
        "## Validation anomalies",
        "",
        "### Hard rejections",
        "",
        "A hard validation failure rejects the hour, which is recorded as a gap "
        "carrying its reason token. These are the Phase 1 tokens, unchanged.",
        "",
        *_table(["reason", "hours"],
                [[f"`{k}`", _n(v)] for k, v in sorted(errors.items())]),
        "Counted from the rejected hours' own records, so a fetch failure "
        "appears here with the validation rejections rather than among the "
        "warnings — it is a reason an hour is missing, whatever filed it.",
        "",
        "### Warnings on stored data",
        "",
        "A warning records something worth knowing that is not a reason to "
        "reject data. Every count here is a flag carried by an hour that **is** "
        "in the store. `EMPTY_TRADING_HOUR` — the feed serving nothing during "
        "an hour the derived week calls open — is derived from the hour's "
        "status per ruling R5, so this row and the boundary audit below cannot "
        "disagree. It is the holiday-calendar input of pre-reg #5, counted here "
        "and interpreted nowhere.",
        "",
        *_table(["reason", "hours"],
                [[f"`{k}`", _n(v)] for k, v in sorted(reasons.items())]),
        *_rejected_flags(rejected),
        *_spread_profile(payload),
        "### The derived week boundary, checked against the feed",
        "",
        "The shut hour either side of every week boundary was fetched rather "
        "than assumed, at about 1.7% more requests than skipping them. What "
        "came back:",
        "",
        *_table(["pair", "shut hours fetched", "shut but carried ticks",
                 "open but served empty"], per_pair),
        verdict,
        "",
        f"Across the universe, {_n(boundary['derived_open_hours_empty'])} hour(s) "
        "the derived week calls open were served empty. That is the same "
        "number as the `EMPTY_TRADING_HOUR` row above, and necessarily so: "
        "both are the count of hours whose status is `empty` inside the "
        "trading week. Most of them are holidays, and T3's calendar is what "
        "decides which.",
        "",
    ]


def _rejected_flags(rejected: dict[str, int]) -> list[str]:
    """Flags observed on hours whose data was then discarded.

    A separate table on purpose. These hours are not in the store, so a spread
    flag on one describes data nobody can read — folding them into the warning
    counts would let a table about the store be moved by hours the store does
    not contain. This is the one question SPEC2 lets the session warning log
    answer, because the hour records cannot: a rejected record keeps only the
    reasons that rejected it.
    """
    if not rejected:
        return []
    return [
        "### Flags observed on hours that were then rejected",
        "",
        "These hours are **not** in the store. The counts come from the "
        "ingestion session log rather than the hour records, which is the only "
        "question that log may be asked (SPEC2 §The canonical manifest "
        "reading): a rejected record keeps its hard reasons and drops the rest.",
        "",
        *_table(["reason", "hours"],
                [[f"`{k}`", _n(v)] for k, v in sorted(rejected.items())]),
    ]


def _throughput(payload: dict[str, Any], window: dict[str, Any]) -> list[str]:
    """What the pull cost, which is T2b's budget."""
    tp = payload["throughput"]
    levels = [[level,
               _n(row["chunks"]), _n(row["requests"]), _n(row["throttles"]),
               _pct(row["throttle_rate"]), f"{row['requests_per_second']:.2f}",
               _hours(row["seconds"])]
              for level, row in tp["by_level"].items()]

    transitions: list[list[Any]] = []
    for session_index, calibration in enumerate(tp.get("calibration") or [], 1):
        for move in calibration.get("transitions", []):
            transitions.append([session_index, f"{float(move['at']) / 60:.0f} min",
                                move["level"], move["why"]])

    sessions = [[i, row.get("status", "?"), _n(row.get("chunks_done", 0)),
                 _n(row.get("hours_ok", 0)), _hours(row.get("seconds", 0)),
                 f"{float(row.get('requests_per_second', 0)):.2f}",
                 _n(row.get("outages_ridden_out", 0)),
                 _hours(row.get("seconds_parked", 0))]
                for i, row in enumerate(tp.get("session_rows") or [], 1)]

    return [
        "## Throughput, and what it cost",
        "",
        "Recorded because T2b ingests the same feed for the years before this "
        "range and should budget from a measurement rather than from optimism.",
        "",
        *_table(["measure", "value", "source"], [
            ["sessions that finished", _n(tp["sessions"]),
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["pair-months completed", _n(tp["chunks_recorded"]),
             f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
            ["requests attributable to the stored chunks",
             _n(tp["requests"]), f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
            ["throttled responses on those",
             f"{_n(tp['throttles'])} ({_pct(tp['throttle_rate'])})",
             f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
            ["time inside the ingest pipeline", _hours(tp["ingest_seconds"]),
             f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
            ["rate over pipeline time",
             f"{tp['requests_per_second']:.2f} requests/s",
             f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
            ["requests the sessions actually issued",
             _n(tp.get("session_requests", 0)),
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["throttled responses on those",
             f"{_n(tp.get('session_throttles', 0))} "
             f"({_pct(tp.get('session_throttle_rate', 0))})",
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["wall clock across sessions", _hours(tp["session_wall_seconds"]),
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["of which parked waiting out the feed",
             f"{_hours(tp['session_parked_seconds'])} "
             f"({_pct(tp['session_parked_seconds'] / tp['session_wall_seconds']) if tp['session_wall_seconds'] else '—'})",
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["rate over wall clock",
             f"{tp.get('session_requests_per_second', 0):.2f} requests/s",
             f"`{tp.get('wall_source', 'sessions.jsonl')}`"],
            ["time building bars", _hours(tp["bar_seconds"]),
             f"`{tp.get('requests_source', 'chunks.jsonl')}`"],
        ]),
        "**Two request counts, and they are different numbers.** The chunk log "
        "is keyed by pair-month and rewritten whenever a chunk is re-worked, so "
        "it reports the requests attributable to the store as it now stands. "
        "The session log is append-only, so it reports every request the "
        "process ever issued, including ones for chunks later re-done. Neither "
        "is wrong and each is the right answer to a different question — but a "
        "rate that divided one log's numerator by the other log's denominator "
        "would be the answer to neither, so each rate above stays inside one "
        "source and the column says which.",
        "",
        "### Concurrency calibration",
        "",
        "The rule was fixed before the run: start at level 2 — T1's "
        "proven-safe setting — and after an unbroken clean hour probe the next "
        "level, to the card's ceiling of 4. A level is judged against the "
        "measured throttle rate of the level below it, and two consecutive "
        "ten-minute windows above 1.5× that rate (or two percentage points "
        "above it) back the level off and block it for six hours.",
        "",
        "A level here is both a connection count and a paced rate: level *n* "
        "means *n* connections and a gap of `0.8/n` seconds. Raising the "
        "connection count alone changes nothing measurable — a fetch costs "
        "about a second, so the worker count is what binds — and probing a "
        "concurrency that cannot offer more load would not be a probe.",
        "",
        *_table(["level", "pair-months", "requests", "throttles",
                 "throttle rate", "requests/s", "ingest time"], levels),
        "Transitions, with the measurement that caused each:",
        "",
        *_table(["session", "at", "to level", "why"], transitions),
        "### Sessions",
        "",
        *_table(["#", "status", "pair-months", "hours ok", "wall",
                 "requests/s", "outages ridden out", "parked"], sessions),
        "A session that was interrupted leaves a ledger start record and no end "
        "record, which is what the ledger is for. Only sessions that finished "
        "and reported their own counters appear here.",
        "",
    ]


def _storage(names: Sequence[str], pairs: dict[str, Any],
             totals: dict[str, Any]) -> list[str]:
    """Storage footprint per pair."""
    rows = []
    for pair in names:
        storage = pairs[pair]["storage"]
        ticks = int(pairs[pair]["totals"]["ticks"])
        rows.append([
            f"`{pair}`", _n(storage["files"]), _n(storage["days"]),
            _gib(storage["bytes"]), _n(ticks),
            f"{storage['bytes'] / ticks:.1f}" if ticks else "—",
        ])
    return [
        "## Storage footprint",
        "",
        *_table(["pair", "tick files", "day partitions", "on disk", "ticks",
                 "bytes/tick"], rows),
        f"Total tick store: **{_gib(totals['stored_bytes'])}** across "
        f"{_n(totals['stored_files'])} files — one Parquet per ingested hour, so "
        "an hour can be re-ingested without rewriting a day and a partial day is "
        "still readable.",
        "",
    ]


def _bars(names: Sequence[str], pairs: dict[str, Any],
          payload: dict[str, Any], totals: dict[str, Any]) -> list[str]:
    """Bar tables built, and what building them cost."""
    aliases = sorted({a for p in names for a in pairs[p]["bars"]},
                     key=lambda a: _alias_order(a))
    rows = []
    for pair in names:
        cells: list[Any] = [f"`{pair}`"]
        for alias in aliases:
            entry = pairs[pair]["bars"].get(alias) or {}
            cells.append(_n(entry.get("rows", 0)))
        rows.append(cells)

    timings = payload["throughput"].get("bars_by_timeframe") or {}
    timing_rows = [[f"`{alias}`", _n(row["builds"]), _n(row["dates"]),
                    _n(row["rows"]), f"{row['seconds']:.0f} s",
                    f"{row['seconds'] / row['builds'] * 1000:.0f} ms"
                    if row["builds"] else "—"]
                   for alias, row in sorted(timings.items(),
                                            key=lambda kv: _alias_order(kv[0]))]
    bytes_rows = [[f"`{alias}`",
                   _mib(sum(int((pairs[p]["bars"].get(alias) or {}).get("bytes", 0))
                            for p in names))]
                  for alias in aliases]

    return [
        "## Bar tables",
        "",
        "Bars are built incrementally (SPEC2 prerequisite P0-B, landed for this "
        "card). Only the days whose stored ticks changed since the last build "
        "are resampled, and the coarser timeframes are rolled up from the 1m "
        "bars rather than re-read from ticks — which is exact, because every "
        "timeframe in the research set tiles UTC days and the bins nest.",
        "",
        "Rows per pair and timeframe, **counted inside this card's window**. A "
        "bar table is one file per pair covering the whole store, so an "
        "unbounded count would hand this card every row every other card ever "
        "built; where ruling R1 excludes part of the window the count stops at "
        "the exclusion and the *Data excluded* table above carries the "
        f"remainder ({_n(totals.get('bar_rows_excluded', 0))} row(s)).",
        "",
        *_table(["pair", *[f"`{a}`" for a in aliases]], rows),
        "Build cost, one build per pair-month:",
        "",
        *_table(["timeframe", "builds", "days folded in", "rows spliced",
                 "total time", "per build"], timing_rows),
        "On disk:",
        "",
        *_table(["timeframe", "size"], bytes_rows),
    ]


def _alias_order(alias: str) -> int:
    """Sort key putting timeframes in ascending bar length."""
    order = {"1min": 0, "5min": 1, "15min": 2, "30min": 3, "1h": 4,
             "4h": 5, "1D": 6}
    return order.get(alias, 99)


def _observations(names: Sequence[str], pairs: dict[str, Any],
                  payload: dict[str, Any], t1: dict[str, Any] | None,
                  notes: Sequence[str] = ()) -> list[str]:
    """Observations for the checkpoint. Nothing here proposes work."""
    totals = payload["totals"]
    worst = min(names, key=lambda p: pairs[p]["open_hour_completeness"],
                default=None)
    lines = [
        "## Observations",
        "",
        "Recorded for the checkpoint review. Per the card, an observation worth "
        "chasing becomes a next card only after a checkpoint; nothing here "
        "proposes work.",
        "",
    ]
    if worst is not None:
        lines.append(
            f"* The least complete pair is `{worst}` at "
            f"{_pct(pairs[worst]['open_hour_completeness'])} of the open hours "
            "the derived week contains. T1 found no missing region in this "
            "range and predicted near-complete coverage; that prediction is "
            "what this column tests.")
    dupes = int(totals["duplicates_dropped"])
    lines.append(
        f"* **{_n(dupes)} duplicate tick(s)** were dropped across the whole "
        "store. Duplicates are never a hard failure — they are dropped and "
        "counted, because a de-duplication nobody can see is indistinguishable "
        "from a decoder that loses records."
        if dupes else
        "* **No duplicate ticks at all.** De-duplication is on the whole record, "
        "so two ticks sharing a millisecond but differing in price or volume "
        "are both kept; the feed served none that were identical.")
    empties = sum(int(pairs[p]["boundary"]["open_but_empty"]) for p in names)
    lines.append(
        f"* {_n(empties)} hour(s) the derived week calls open were served "
        "empty. Those are candidate holidays and are pre-reg #5's raw material; "
        "T3 turns them into a calendar, and until it does an empty open hour "
        "stays a warning rather than a `closed`.")
    lines.append(
        "* The tick store averages "
        f"{int(totals['stored_bytes']) / max(1, int(totals['ticks'])):.1f} bytes "
        "per stored tick after Snappy, which is what a later card should size "
        "a comparable pull with.")
    lines += _gap_observations(payload)
    lines += _exclusion_observations(payload)
    for note in notes:
        lines.append(f"* {note}")
    lines.append("")
    return lines


def _gap_observations(payload: dict[str, Any]) -> list[str]:
    """The gap story, attributed from the rows rather than from memory."""
    gaps = payload.get("gaps") or {}
    total = int(gaps.get("count", 0))
    if not total:
        return []
    by_pair = gaps.get("by_reason_pair") or {}
    per_pair: dict[str, int] = {}
    for counts in by_pair.values():
        for pair, count in counts.items():
            per_pair[pair] = per_pair.get(pair, 0) + int(count)
    worst_pair, worst_count = max(per_pair.items(),
                                  key=lambda kv: (kv[1], kv[0]))
    reason_totals = {r: sum(c.values()) for r, c in by_pair.items()}
    worst_reason, reason_count = max(reason_totals.items(),
                                     key=lambda kv: (kv[1], kv[0]))
    in_worst = int((by_pair.get(worst_reason) or {}).get(worst_pair, 0))
    episodes = [e for e in (gaps.get("episodes") or [])
                if e["pair"] == worst_pair and e["reason"] == worst_reason
                and int(e["months"]) > 1]
    lines = [
        f"* **The gaps concentrate.** `{worst_pair}` carries {_n(worst_count)} "
        f"of {_n(total)} surviving gap(s) ({worst_count / total:.2%}), and "
        f"`{worst_reason}` is {_n(reason_count)} of them across "
        f"{_n(len(by_pair.get(worst_reason) or {}))} pair(s) — of which "
        f"{_n(in_worst)} are `{worst_pair}`'s. The reason total and the "
        "per-pair attribution are different numbers and the table above keeps "
        "them apart."]
    if episodes:
        spans = "; ".join(
            f"{e['first_month']} → {e['last_month']} "
            f"({_n(e['months'])} months, {_n(e['hours'])} hours)"
            for e in sorted(episodes, key=lambda e: e["first_month"]))
        lines.append(
            f"* **`{worst_pair}` `{worst_reason}` falls in "
            f"{_n(len(episodes))} bounded episode(s):** {spans}. The feed "
            "served those hours and validation refused them, so they are "
            "neither absent history nor a transient refusal.")
    return lines


def _exclusion_observations(payload: dict[str, Any]) -> list[str]:
    """State the exclusion wherever the affected pair appears (R1)."""
    block = payload.get("exclusions") or {}
    rulings = block.get("rulings") or []
    if not rulings:
        return []
    hours = block.get("hours_excluded") or {}
    out = []
    for entry in rulings:
        pair = entry["pair"]
        counts = hours.get(pair) or {}
        stored = int(counts.get("ok", 0))
        if not stored:
            continue
        out.append(
            f"* **`{pair}` is excluded {entry['window']} (ruling "
            f"{entry['ruling']}).** {_n(stored)} stored hour(s) and "
            f"{_n(counts.get('ticks', 0))} tick(s) inside this window are on "
            "disk and out of reach: `research.loader` refuses them with "
            "`PAIR_EXCLUDED_WINDOW`. Any cross-pair work spanning that window "
            "runs on the remaining pairs and has to say so.")
    return out


def _provenance(document: dict[str, Any], gate_status: str,
                home: str) -> list[str]:
    """Where every number came from."""
    access = document.get("access") or {}
    excluded = access.get("excluded") or []
    return [
        "## Provenance",
        "",
        f"* Config: `{home}/config.toml` (sha256 "
        f"`{str(document['config_sha256'])[:16]}`)",
        "* Manifests: `data/research/manifests/pair=<PAIR>/<YYYY-MM>/manifest.json`"
        " — one shard per pair-month, one entry per requested hour. Read the "
        "canonical way (SPEC2 §The canonical manifest reading): hour records "
        "and the derived coverage block, never the session warning log.",
        f"* Progress records: `{home}/chunks.jsonl` and `{home}/sessions.jsonl`",
        f"* Result: `{home}/result.json`, hash `{document['result_hash']}`",
        f"* Loader mode `{document['mode']}`, scored `{document['scored']}`, "
        f"re-run class `{document['rerun_class']}`. The loader served "
        f"{len(access.get('files', []))} bar file(s) across "
        f"{len(access.get('pairs', []))} pair(s) and "
        f"{len(access.get('dates', []))} date(s); sealed dates served: "
        f"{document['payload']['loader']['sealed_dates_served'] or 'none'}; "
        f"dates withheld by an exclusion window: {_n(len(excluded))}.",
        f"* Research gate: {gate_status}",
        "",
    ]


# --------------------------------------------------------------------------- #
# T1's comparison figures
# --------------------------------------------------------------------------- #

def _t1_data_fraction(t1: dict[str, Any] | None,
                      years: Sequence[str]) -> dict[str, float]:
    """T1's data fraction per pair, restricted to this card's years.

    T1 probed one hour of each trading day from 2005 onward. Comparing its
    whole-window figure against a 2015-onward ingestion would be comparing two
    different windows, so its per-year probe counts are re-totalled over exactly
    the years this card covers. The measurement is still a different one -- one
    hour a day against every hour -- and the report says so.
    """
    if not t1:
        return {}
    wanted = set(years)
    out: dict[str, float] = {}
    for pair, entry in (t1.get("payload", {}).get("pairs") or {}).items():
        data = total = 0
        for year, counts in (entry.get("by_year") or {}).items():
            if year not in wanted:
                continue
            data += int(counts.get("data", 0))
            total += sum(int(v) for v in counts.values())
        if total:
            out[pair] = data / total
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _rel_dir(path: pathlib.Path, base: pathlib.Path) -> str:
    """Project-relative POSIX directory, absolute where that is impossible."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.ingest_report",
        description="Render an ingestion report from its result document.")
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--taskcard", default=None,
                        help=("fallback task card for the ledger trial count. "
                              "The result document's own `taskcard` wins when "
                              "it has one, so a report cannot be rendered "
                              "under a card the experiment did not declare."))
    parser.add_argument("--t1-result", type=pathlib.Path,
                        default=pathlib.Path("experiments/T1-coverage/result.json"))
    parser.add_argument("--gate-status", default="not yet run")
    parser.add_argument("--note", action="append", default=[],
                        help=("an authored observation to append, for facts "
                              "the result document cannot carry. Repeatable. "
                              "Kept in the command so the report stays "
                              "regenerable rather than hand-edited. May not "
                              "contain a count -- see ruling R6."))
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


#: A quantity written into prose: a thousands-separated number, or a run of
#: three or more digits that is not a year and not part of a ``YYYY-MM`` date.
_COUNT_IN_PROSE: Final[re.Pattern[str]] = re.compile(
    r"\d{1,3}(?:,\d{3})+|(?<![\d-])(?!19\d\d|20\d\d)\d{3,}")


def check_note(note: str) -> None:
    """Refuse an authored note that carries a number (ruling R6).

    The three wrong figures the T3 audit found were all of this shape: a count
    typed into a bullet, correct when written and silently stale afterwards. A
    note is for a fact the result document cannot carry; a *number* it cannot
    carry is a gap in the payload, and the fix is to put it there.

    Raises:
        ValueError: When the note contains a count.
    """
    found = _COUNT_IN_PROSE.findall(note)
    if found:
        raise ValueError(
            f"note carries the number(s) {found[:3]}: {note[:80]!r}. Ruling R6 "
            "keeps counts out of authored prose -- derive it from the payload, "
            "or add it to the payload so it can be derived.")


def main(argv: list[str] | None = None) -> int:
    """Render the report and write it."""
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base
            else pathlib.Path(__file__).resolve().parents[1])
    from research import ledger as ledger_mod

    document = json.loads(args.result.read_text(encoding="utf-8"))
    for note in args.note:
        check_note(note)
    t1 = None
    t1_path = args.t1_result if args.t1_result.is_absolute() else base / args.t1_result
    if t1_path.is_file():
        t1 = json.loads(t1_path.read_text(encoding="utf-8"))
    # The card the report names is the card the result was run under, not one
    # named on the command line. `--taskcard` remains only for a ledger count
    # that has to be taken under a different card than the result declares.
    card = str(document.get("taskcard") or args.taskcard)
    trials = ledger_mod.trial_count(ledger_mod.read(base), card)
    home = _rel_dir(args.result.resolve().parent, base)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render(document, t1, trials, args.gate_status, args.note, home),
        encoding="utf-8")
    _LOG.info("wrote %s", args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
