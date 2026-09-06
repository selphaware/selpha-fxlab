"""Derive the holiday calendar from what the feed actually served.

T3 Step 2, pre-registered decision #5, under ruling R5: the input is the
manifest hour **status**, never the ``EMPTY_TRADING_HOUR`` warning list. The
T3 audit measured that list 1,183 hours short of the statuses across the store,
for a structural reason (SPEC2 §The canonical manifest reading) -- so a
calendar built from it would be a calendar with holidays missing, and nothing
downstream would ever notice.

The derivation is one idea. The FX week boundary already tells us which hours
should have traded. Where the feed answered *nothing* during an hour the
derived week calls open, either the market was shut or the data is missing.
Those two look identical in one pair and completely different across twelve:
a market holiday shuts everybody at once, and a data problem does not. So:

* a date where **every readable pair** was empty for at least
  ``min_empty_hours`` open hours is a **full holiday**;
* a date where **some** pairs were, at least ``min_pairs_partial`` of them, is
  a **partial holiday** -- overwhelmingly a single currency's national day,
  when its own centre shuts and the crosses keep trading;
* everything else -- one pair alone, or a handful of scattered hours -- is
  **unexplained**, and the card is explicit that those are data facts for T4
  rather than holidays. Calling them holidays would be the whole failure mode
  this split exists to avoid;
* a date whose *only* quiet pairs are ones an exclusion window removes is
  **excluded_only**: the readable universe saw nothing happen, so there is
  nothing to explain. T5 Step 0 separated these out. Before it, they fell
  through to ``unexplained`` and were handed on as data facts -- 236 of T3's
  312, every one of them 2007-2010, where ruling R1's AUDUSD window is.

"Every readable pair" is doing real work in the first rule. Ruling R1 excludes
AUDUSD before 2011, so on those dates the universe is eleven pairs and the
unanimity test is over eleven. Testing twelve would make every pre-2011 holiday
fail, because the twelfth pair is not there to agree.

The derived calendar is then compared against a static list of the major FX
holidays. The two are independent: one is what the feed did, the other is what
a calendar says. Agreement is evidence; disagreement in either direction is the
interesting part, and the report states both.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
from typing import Any, Final, Iterable, Sequence

from fxlab.ingestion.manifest import (MANIFEST_NAME, STATUS_EMPTY, STATUS_OK)
from fxlab.ingestion.sessions import is_market_open
from research.bulk_ingest import MANIFEST_DIRNAME
from research.exclusions import is_excluded
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.calendar_build")

#: Classifications a date can receive.
FULL: Final[str] = "full"
PARTIAL: Final[str] = "partial"
UNEXPLAINED: Final[str] = "unexplained"

#: The fourth outcome, added by T5 Step 0: every pair that went quiet on the
#: date is inside an exclusion window, so the readable universe observed
#: nothing at all. Not a classification of the market -- a record that the
#: filter cast a shadow -- and deliberately not folded into ``UNEXPLAINED``,
#: which is where 236 of them spent T3 and T4 masquerading as data facts.
EXCLUDED_ONLY: Final[str] = "excluded_only"

#: How an unexplained-empty date is classified. The order below is the
#: priority order, and each rule is a statement about evidence rather than a
#: guess. Lives here rather than in the card that first needed it, because two
#: cards deriving the same verdict two ways is how the two quietly disagree.
EMPTY_CLASSES: Final[tuple[str, ...]] = (
    "r1_artefact", "week_boundary", "calendar_holiday", "currency_holiday",
    "feed_artefact", "unknown")

#: The three buckets a class rolls into, plus the honest fourth.
EMPTY_KINDS: Final[dict[str, str]] = {
    "r1_artefact": "bookkeeping artefact",
    "week_boundary": "feed artefact",
    "feed_artefact": "feed artefact",
    "calendar_holiday": "partial holiday",
    "currency_holiday": "partial holiday",
    "unknown": "unknown",
}

#: A Sunday or Friday date this shallow is the week edge, not a closure. The FX
#: week opens Sunday 17:00 New York and closes Friday 17:00, so those two days
#: carry a handful of open hours whose exact extent the feed and the derived
#: boundary need not agree on to the hour.
WEEK_EDGE_DAYS: Final[tuple[str, ...]] = ("Sun", "Fri")
WEEK_EDGE_MAX_HOURS: Final[int] = 3

#: Easter Sunday by year, Western computus. Tabulated rather than computed:
#: the anonymous Gregorian algorithm is four lines and every one of them is a
#: place to be quietly wrong for one year in twenty, which a test would not
#: catch and a report would not show. These are the dates Good Friday and
#: Easter Monday are derived from, and they are checkable against any almanac.
EASTER_SUNDAY: Final[dict[int, str]] = {
    2005: "2005-03-27", 2006: "2006-04-16", 2007: "2007-04-08",
    2008: "2008-03-23", 2009: "2009-04-12", 2010: "2010-04-04",
    2011: "2011-04-24", 2012: "2012-04-08", 2013: "2013-03-31",
    2014: "2014-04-20", 2015: "2015-04-05", 2016: "2016-03-27",
    2017: "2017-04-16", 2018: "2018-04-01", 2019: "2019-04-21",
    2020: "2020-04-12", 2021: "2021-04-04", 2022: "2022-04-17",
    2023: "2023-04-09", 2024: "2024-03-31", 2025: "2025-04-20",
}


def static_holidays(years: Iterable[int]) -> dict[str, str]:
    """The major FX holidays, by date, for the years given.

    Deliberately short. This list exists to be *compared against* the derived
    calendar, so every entry it carries that the feed does not show is a
    question worth asking -- which stops being true the moment it is padded
    with regional dates nobody expected the whole market to observe.
    """
    out: dict[str, str] = {}
    for year in years:
        out[f"{year:04d}-01-01"] = "New Year's Day"
        out[f"{year:04d}-12-25"] = "Christmas Day"
        out[f"{year:04d}-12-26"] = "Boxing Day"
        out[f"{year:04d}-07-04"] = "US Independence Day"
        easter = EASTER_SUNDAY.get(year)
        if easter:
            sunday = as_date(easter)
            out[(sunday - dt.timedelta(days=2)).isoformat()] = "Good Friday"
            out[(sunday + dt.timedelta(days=1)).isoformat()] = "Easter Monday"
        out[_thanksgiving(year).isoformat()] = "US Thanksgiving"
    return dict(sorted(out.items()))


def _thanksgiving(year: int) -> dt.date:
    """The fourth Thursday of November."""
    day = dt.date(year, 11, 1)
    day += dt.timedelta(days=(3 - day.weekday()) % 7)   # first Thursday
    return day + dt.timedelta(days=21)


def scan(base: pathlib.Path, pairs: Sequence[str], start: dt.date,
         end: dt.date) -> dict[str, Any]:
    """Per date, how many open hours each pair was served empty for.

    Reads the manifests once. Only ``empty`` and ``ok`` statuses matter: a
    ``gap`` is an hour nobody has an answer about, and folding it in would let
    a validation rejection masquerade as a public holiday.
    """
    empty: dict[str, dict[str, int]] = {}
    traded: dict[str, dict[str, int]] = {}
    seen_pairs: dict[str, set[str]] = {}
    months = _months(start, end)
    for pair in pairs:
        for month in months:
            shard = (base / MANIFEST_DIRNAME / f"pair={pair}" / month
                     / MANIFEST_NAME)
            if not shard.is_file():
                continue
            document = json.loads(shard.read_text(encoding="utf-8"))
            for record in document.get("hours", []):
                date = str(record.get("date"))
                if not (start.isoformat() <= date <= end.isoformat()):
                    continue
                status = str(record.get("status"))
                if status not in (STATUS_EMPTY, STATUS_OK):
                    continue
                hour = int(record.get("hour", -1))
                opens = dt.datetime.combine(as_date(date), dt.time(hour=hour),
                                            tzinfo=dt.timezone.utc)
                if not is_market_open(opens):
                    continue
                seen_pairs.setdefault(date, set()).add(pair)
                target = empty if status == STATUS_EMPTY else traded
                target.setdefault(date, {})[pair] = (
                    target.setdefault(date, {}).get(pair, 0) + 1)
    return {"empty": empty, "traded": traded, "pairs_present": seen_pairs}


def _months(start: dt.date, end: dt.date) -> list[str]:
    """Every ``YYYY-MM`` in the window."""
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def readable_pairs(pairs: Sequence[str], date: str) -> list[str]:
    """The pairs research may read on ``date`` (ruling R1)."""
    return [p for p in pairs if not is_excluded(p, date)]


def classify(scanned: dict[str, Any], pairs: Sequence[str], *,
             min_empty_hours: int,
             min_pairs_partial: int) -> dict[str, Any]:
    """Turn the per-date empty counts into full, partial and unexplained.

    Returns:
        ``{"dates": {date: row}, "counts": {...}}`` where each row states the
        classification, which pairs were empty, and for how many hours.
    """
    empty = scanned["empty"]
    rows: dict[str, dict[str, Any]] = {}
    shadow: dict[str, dict[str, Any]] = {}
    for date in sorted(empty):
        readable = readable_pairs(pairs, date)
        if not readable:
            continue
        counts = {p: c for p, c in sorted(empty[date].items()) if p in readable}
        deep = sorted(p for p, c in counts.items() if c >= min_empty_hours)
        row = {
            "date": date,
            "pairs_readable": len(readable),
            "pairs_empty": sorted(counts),
            "pairs_empty_deep": deep,
            "hours_by_pair": counts,
            "max_hours": max(counts.values()) if counts else 0,
        }
        if not counts:
            # T5 Step 0. Every pair that went quiet here is inside an
            # exclusion window, so the readable universe saw nothing happen
            # at all: the row is about the filter, not about the market. It
            # used to fall straight through to UNEXPLAINED and be handed to
            # the next card as a data fact, which put 236 dates of nothing in
            # front of T4. Recorded separately -- deleting it would lose the
            # evidence that the shadow exists, and keeping it in the
            # unexplained pile is what the repair is for.
            row["kind"] = EXCLUDED_ONLY
            shadow[date] = row
            continue
        if len(deep) == len(readable):
            row["kind"] = FULL
        elif len(deep) >= min_pairs_partial:
            row["kind"] = PARTIAL
        else:
            row["kind"] = UNEXPLAINED
        rows[date] = row
    counts = {kind: sum(1 for r in rows.values() if r["kind"] == kind)
              for kind in (FULL, PARTIAL, UNEXPLAINED)}
    counts[EXCLUDED_ONLY] = len(shadow)
    return {"dates": rows, "counts": counts, "excluded_only": shadow}


def classify_empty_date(row: dict[str, Any], static: dict[str, str],
                        readable: int) -> dict[str, Any]:
    """Classify one unexplained-empty date by what the evidence supports.

    ``r1_artefact`` is kept as a verdict even though :func:`classify` no
    longer routes those rows here: the T4 result characterised 312 dates and
    re-hashes against this function, and a class that stops existing would
    make a closed card unreproducible to say the same thing a count already
    says. After T5 Step 0 the caller that supplies real rows never produces
    it, which is the repair.
    """
    date = str(row["date"])
    pairs = [str(p) for p in row["pairs_empty"]]
    weekday = as_date(date).strftime("%a")
    max_hours = int(row["max_hours"])
    currencies = [set((p[:3], p[3:])) for p in pairs]
    shared = set.intersection(*currencies) if currencies else set()
    if not pairs:
        verdict = "r1_artefact"
    elif weekday in WEEK_EDGE_DAYS and max_hours <= WEEK_EDGE_MAX_HOURS:
        verdict = "week_boundary"
    elif date in static:
        verdict = "calendar_holiday"
    elif shared and len(pairs) >= 2:
        verdict = "currency_holiday"
    elif readable and len(pairs) >= max(2, readable // 2):
        verdict = "feed_artefact"
    else:
        verdict = "unknown"
    return {
        "date": date,
        "weekday": weekday,
        "pairs_empty": pairs,
        "pairs_empty_deep": [str(p) for p in row["pairs_empty_deep"]],
        "hours_by_pair": {str(k): int(v)
                          for k, v in sorted(row["hours_by_pair"].items())},
        "hours": int(sum(row["hours_by_pair"].values())),
        "max_hours": max_hours,
        "readable_pairs": readable,
        "static_holiday": static.get(date, ""),
        "shared_currency": sorted(shared),
        "class": verdict,
        "kind": EMPTY_KINDS[verdict],
    }


def classify_unexplained(classified: dict[str, Any], pairs: Sequence[str],
                         static: dict[str, str]) -> list[dict[str, Any]]:
    """Every surviving unexplained date, classified, in date order."""
    return sorted(
        (classify_empty_date(row, static,
                             len(readable_pairs(pairs, str(row["date"]))))
         for row in classified["dates"].values()
         if row["kind"] == UNEXPLAINED),
        key=lambda r: r["date"])


def compare_static(classified: dict[str, Any], static: dict[str, str],
                   scanned: dict[str, Any], start: dt.date,
                   end: dt.date) -> dict[str, Any]:
    """Where the derived calendar and the static list agree and differ.

    A static holiday the feed traded through is not an error in either -- it is
    the difference between a bank holiday and a market one, and most of the
    list is expected to land there. What must not be folded in with it is a
    static holiday that fell on a **weekend**, where the derived week already
    called the market shut and the feed was never asked. Both come out of a
    naive set difference as "static, not derived", and they are opposite
    findings: one says the market traded, the other says nobody looked.
    """
    rows = classified["dates"]
    present = scanned["pairs_present"]
    derived_full = {d for d, r in rows.items() if r["kind"] == FULL}
    derived_any = {d for d, r in rows.items() if r["kind"] in (FULL, PARTIAL)}
    in_window = {d for d in static
                 if start.isoformat() <= d <= end.isoformat()}
    weekend = {d for d in in_window if not present.get(d)}
    traded = in_window - derived_any - weekend
    return {
        "agreed": sorted(derived_full & in_window),
        "derived_not_static": sorted(derived_full - in_window),
        "static_traded_through": sorted(traded),
        "static_on_a_closed_week": sorted(weekend),
        "static_partial_only": sorted((in_window & derived_any) - derived_full),
        "counts": {
            "derived_full": len(derived_full),
            "static_in_window": len(in_window),
            "agreed": len(derived_full & in_window),
            "derived_not_static": len(derived_full - in_window),
            "static_traded_through": len(traded),
            "static_on_a_closed_week": len(weekend),
            "static_partial_only": len((in_window & derived_any)
                                       - derived_full),
        },
        "by_year": _static_by_year(classified, in_window, present),
    }


def _static_by_year(classified: dict[str, Any], in_window: set[str],
                    present: dict[str, set[str]]) -> dict[str, dict[str, int]]:
    """What the feed did on each year's static holidays.

    The single most consequential table this card produces. Read down it and
    the feed's own behaviour changes: in the early era it served quotes right
    through the days the whole market was shut, and later it stopped. A
    calendar derived from emptiness can only find the holidays the feed left
    empty, so this table is what tells a reader which years the calendar
    actually covers.
    """
    rows = classified["dates"]
    shadow = classified.get("excluded_only") or {}
    out: dict[str, dict[str, int]] = {}
    for date in sorted(in_window):
        bucket = out.setdefault(date[:4], {
            FULL: 0, PARTIAL: 0, UNEXPLAINED: 0, EXCLUDED_ONLY: 0,
            "traded_through": 0, "closed_week": 0})
        if not present.get(date):
            bucket["closed_week"] += 1
            continue
        row = rows.get(date)
        if row is not None:
            bucket[row["kind"]] += 1
        elif date in shadow:
            # The only pair that went quiet is one an exclusion window
            # removes, so as far as the readable universe is concerned the
            # feed quoted through. Counted apart from "traded through" all
            # the same: the two are the same observation about different
            # universes, and folding them together would put a claim about
            # eleven pairs in a column headed by a claim about twelve.
            bucket[EXCLUDED_ONLY] += 1
        else:
            # No empty hour at all on a day the market was open: the feed
            # quoted straight through the holiday.
            bucket["traded_through"] += 1
    return out


def unexplained_profile(classified: dict[str, Any],
                        pairs: Sequence[str] = (),
                        static: dict[str, str] | None = None) -> dict[str, Any]:
    """The empty hours the calendar does **not** explain.

    The card is explicit that these are data facts for T4 rather than
    holidays, and keeping them out of the calendar is the point: a date where
    two pairs went quiet and ten did not is evidence about the feed, and
    filing it as a market closure would launder that evidence into a fact
    about the market.

    Since T5 Step 0 this counts only dates on which a *readable* pair went
    quiet. The dates whose only quiet pairs were excluded ones are counted
    separately, under ``excluded_only``, because "236 rows the filter made" and
    "76 facts about the feed" are different claims and the first one used to be
    reported as the second. Give ``pairs`` and ``static`` and the survivors are
    classified too.
    """
    rows = [r for r in classified["dates"].values()
            if r["kind"] == UNEXPLAINED]
    by_year: dict[str, int] = {}
    by_pair: dict[str, dict[str, int]] = {}
    for row in rows:
        year = row["date"][:4]
        by_year[year] = by_year.get(year, 0) + 1
        for pair, hours in row["hours_by_pair"].items():
            bucket = by_pair.setdefault(pair, {"dates": 0, "hours": 0})
            bucket["dates"] += 1
            bucket["hours"] += int(hours)
    widths: dict[str, int] = {}
    for row in rows:
        key = str(len(row["pairs_empty_deep"]))
        widths[key] = widths.get(key, 0) + 1
    shadow = classified.get("excluded_only") or {}
    profile: dict[str, Any] = {
        "dates": len(rows),
        "hours": sum(sum(r["hours_by_pair"].values()) for r in rows),
        "by_year": dict(sorted(by_year.items())),
        "by_pair": {k: by_pair[k] for k in sorted(by_pair)},
        "pairs_deep_per_date": dict(sorted(widths.items())),
        "worst": sorted(
            ({"date": r["date"], "pairs": len(r["hours_by_pair"]),
              "hours": sum(r["hours_by_pair"].values())} for r in rows),
            key=lambda r: (-r["hours"], r["date"]))[:20],
        "excluded_only": {
            "dates": len(shadow),
            "by_year": dict(sorted(
                (year, sum(1 for d in shadow if d[:4] == year))
                for year in sorted({d[:4] for d in shadow}))),
        },
    }
    if pairs:
        classes = classify_unexplained(classified, pairs, static or {})
        by_class = {name: 0 for name in EMPTY_CLASSES}
        by_kind: dict[str, int] = {}
        by_weekday: dict[str, int] = {}
        class_by_year: dict[str, dict[str, int]] = {}
        for row in classes:
            by_class[row["class"]] += 1
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
            by_weekday[row["weekday"]] = by_weekday.get(row["weekday"], 0) + 1
            class_by_year.setdefault(
                row["date"][:4],
                {n: 0 for n in EMPTY_CLASSES})[row["class"]] += 1
        order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        profile["classified"] = {
            "by_class": by_class,
            "by_kind": {k: by_kind[k] for k in sorted(by_kind)},
            "by_weekday": {d: by_weekday[d] for d in order if d in by_weekday},
            "by_year": {k: class_by_year[k] for k in sorted(class_by_year)},
            "dates": {row["date"]: row["class"] for row in classes},
            "all": classes,
        }
    return profile


def build(base: pathlib.Path, pairs: Sequence[str], start: dt.date,
          end: dt.date, *, min_empty_hours: int,
          min_pairs_partial: int) -> dict[str, Any]:
    """The whole derivation, as one JSON-plain document."""
    scanned = scan(base, pairs, start, end)
    classified = classify(scanned, pairs, min_empty_hours=min_empty_hours,
                          min_pairs_partial=min_pairs_partial)
    static = static_holidays(range(start.year, end.year + 1))
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "pairs": list(pairs),
        "rules": {"min_empty_hours": min_empty_hours,
                  "min_pairs_partial": min_pairs_partial},
        "classified": classified,
        "static": static,
        "comparison": compare_static(classified, static, scanned, start, end),
        "unexplained": unexplained_profile(classified, pairs, static),
    }


def render_toml(document: dict[str, Any]) -> str:
    """The calendar as a tracked, diffable TOML file.

    Every line is derived; the file carries the rule that produced it and the
    window it was derived over, so a reader can tell whether it still applies
    to a store that has since grown.
    """
    window = document["window"]
    rules = document["rules"]
    rows = document["classified"]["dates"]
    static = document["static"]
    lines = [
        "# config/calendar.toml -- the FX holiday calendar, derived.",
        "#",
        "# Generated by `python -m research.calendar_build`. Do not hand-edit:",
        "# every entry below is derived from manifest hour statuses (ruling",
        "# R5) and regenerating is how it stays true. Pre-registered decision",
        "# #5: after T3, EMPTY_TRADING_HOUR on a calendar date is `closed`,",
        "# not a warning.",
        "#",
        "# `full`      every pair research may read on that date was served",
        "#             empty for at least min_empty_hours open hours.",
        "# `partial`   at least min_pairs_partial were, but not all -- mostly",
        "#             one currency's national day, with the crosses trading.",
        "#",
        "# Dates the feed left empty that meet neither test are NOT here. They",
        "# are data facts, not holidays, and live in the T3 report.",
        "#",
        "# READ THIS BEFORE USING THE CALENDAR. It is derived from emptiness,",
        "# so it can only contain holidays the feed left empty -- and the feed",
        "# did not always. Through the early era it served quotes right across",
        "# days the whole market was shut, so those days are absent here and",
        "# their bars contain prices nobody traded at. The T3 report has the",
        "# year-by-year table; the short version is that this calendar is",
        "# dense in the later years and near-empty in the early ones, and that",
        "# is a fact about the feed rather than about the market.",
        "",
        "[calendar]",
        f'derived_from = "manifest status == empty (SPEC2 ruling R5)"',
        f'window_start = "{window["start"]}"',
        f'window_end = "{window["end"]}"',
        f'min_empty_hours = {rules["min_empty_hours"]}',
        f'min_pairs_partial = {rules["min_pairs_partial"]}',
        f'full_days = {sum(1 for r in rows.values() if r["kind"] == FULL)}',
        f'partial_days = {sum(1 for r in rows.values() if r["kind"] == PARTIAL)}',
        "",
        "# Full market holidays: date = the static-list name where one matches,",
        "# otherwise an empty string. An unnamed date is a real finding -- the",
        "# whole market stopped and no major-holiday list explains it.",
        "[calendar.full]",
    ]
    for date in sorted(d for d, r in rows.items() if r["kind"] == FULL):
        lines.append(f'"{date}" = "{static.get(date, "")}"')
    lines += [
        "",
        "# Partial holidays: date = the pairs that were shut.",
        "[calendar.partial]",
    ]
    for date in sorted(d for d, r in rows.items() if r["kind"] == PARTIAL):
        pairs = ", ".join(f'"{p}"' for p in rows[date]["pairs_empty_deep"])
        lines.append(f'"{date}" = [{pairs}]')

    universe = list(document.get("pairs") or [])
    survivors = (classify_unexplained(document["classified"], universe, static)
                 if universe else [])
    shadow = document["classified"].get("excluded_only") or {}
    lines += [
        "",
        "# ---------------------------------------------------------------",
        "# INFORMATIONAL (ruling R8). Nothing below marks an hour ineligible",
        "# for execution -- that is the static major-holiday list's job, in",
        "# every year, whether or not the feed served data. This section is",
        "# the empties-derived component: dates the feed left empty that are",
        "# neither a full nor a partial holiday, with the class the evidence",
        "# supports. Regenerated with the rest of the file; do not hand-edit.",
        "#",
        "# `week_boundary`    a Sunday or Friday date at most three hours",
        "#                    deep -- the FX week edge, where the feed and the",
        "#                    derived boundary need not agree to the hour.",
        "# `calendar_holiday` the static major-holiday list names the date.",
        "# `currency_holiday` every empty pair shares a currency, so that",
        "#                    centre shut and the crosses kept trading.",
        "# `feed_artefact`    at least half the readable universe went quiet,",
        "#                    too shallowly to be a market closure.",
        "# `unknown`          none of the above, said rather than guessed.",
        "#",
        "# `excluded_only` counts dates whose ONLY quiet pairs sit inside an",
        "# exclusion window (ruling R1). The readable universe saw nothing",
        "# happen on them, so they are not unexplained -- they are the",
        "# filter's own shadow. Before T5 Step 0 they were counted as",
        "# unexplained empty dates, which is what that step repaired.",
        "",
        "[calendar.unexplained]",
        f"dates = {len(survivors)}",
        f"empty_hours = {sum(r['hours'] for r in survivors)}",
        f"excluded_only = {len(shadow)}",
    ]
    for name in EMPTY_CLASSES:
        count = sum(1 for r in survivors if r["class"] == name)
        if name == "r1_artefact" and not count:
            continue
        lines.append(f"{name} = {count}")
    lines += [
        "",
        "# Every surviving date, with its class.",
        "[calendar.unexplained.by_date]",
    ]
    for row in survivors:
        lines.append(f'"{row["date"]}" = "{row["class"]}"')
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Reading the calendar back
# --------------------------------------------------------------------------- #

class Calendar:
    """The committed calendar, queryable.

    Pre-registered decision #5 ends with a consequence: *after T3,
    ``EMPTY_TRADING_HOUR`` on a calendar date is ``closed``, not a warning.*
    That clause needs somebody to be able to ask, so this is the asking. A
    calendar nobody can query is a text file.

    Note what this does **not** do: it does not rewrite the manifests. The
    stored hours keep the status the ingestion gave them, because rewriting a
    decade of shards to relabel a few hundred hours would destroy the record of
    what the feed actually served and invalidate two hashed results to change
    nothing a consumer cannot work out for itself. The reclassification happens
    where the data is read, not where it is stored.
    """

    __slots__ = ("full", "partial", "window", "rules")

    def __init__(self, full: dict[str, str], partial: dict[str, list[str]],
                 window: tuple[str, str], rules: dict[str, int]) -> None:
        self.full = full
        self.partial = partial
        self.window = window
        self.rules = rules

    def is_holiday(self, date: object, pair: str | None = None) -> bool:
        """True when the market was shut on ``date``.

        Args:
            date: Anything :func:`research.seal.as_date` accepts.
            pair: When given, a partial holiday counts only for the pairs it
                actually shut. Without it, only full holidays count -- a
                partial holiday is by definition not a market-wide closure and
                treating it as one would shut eleven pairs that were trading.
        """
        text = as_date(date).isoformat()
        if text in self.full:
            return True
        if pair is None:
            return False
        return pair in self.partial.get(text, ())

    def covers(self, date: object) -> bool:
        """True when ``date`` is inside the window the calendar was derived on.

        A date outside it is not "not a holiday" -- it is unexamined, and the
        difference matters at both ends: before the window nothing was
        measured, and after it the seal begins.
        """
        return self.window[0] <= as_date(date).isoformat() <= self.window[1]

    def classify_empty_hour(self, date: object,
                            pair: str | None = None) -> str:
        """``"closed"`` on a calendar date, ``"warning"`` otherwise.

        Pre-reg #5's closing clause, as a function. An empty hour outside the
        derivation window stays a warning, because the calendar has nothing to
        say about it.
        """
        if self.covers(date) and self.is_holiday(date, pair):
            return "closed"
        return "warning"


def load_calendar(path: pathlib.Path) -> Calendar:
    """Read the committed calendar from TOML.

    Raises:
        FileNotFoundError: If the calendar has not been built. Deliberately not
            an empty calendar: "no holidays" and "no calendar" are different
            claims, and silently returning the first for the second would make
            every holiday check quietly pass.
    """
    import tomllib

    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"no calendar at {path}; run `python -m research.calendar_build` "
            "-- an absent calendar is not an empty one")
    block = (tomllib.loads(path.read_text(encoding="utf-8"))
             .get("calendar") or {})
    return Calendar(
        full={str(k): str(v) for k, v in (block.get("full") or {}).items()},
        partial={str(k): [str(p) for p in v]
                 for k, v in (block.get("partial") or {}).items()},
        window=(str(block.get("window_start", "")),
                str(block.get("window_end", ""))),
        rules={"min_empty_hours": int(block.get("min_empty_hours", 0)),
               "min_pairs_partial": int(block.get("min_pairs_partial", 0))},
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.calendar_build",
        description="Derive the FX holiday calendar from manifest statuses.")
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Derive the calendar and write it."""
    from research.experiment import load_config
    from research.loader import project_root

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base else project_root())
    config = load_config(args.config)
    params = config.params
    document = build(
        base / str(params.get("data_dir", "data/research")),
        [str(p) for p in params["pairs"]],
        as_date(str(params["start_date"])), as_date(str(params["end_date"])),
        min_empty_hours=int(params["calendar_min_empty_hours"]),
        min_pairs_partial=int(params["calendar_min_pairs_partial"]))
    target = base / str(params.get("calendar_path", "config/calendar.toml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_toml(document), encoding="utf-8")
    print(json.dumps(document["classified"]["counts"], indent=2))
    print(json.dumps(document["comparison"]["counts"], indent=2))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
