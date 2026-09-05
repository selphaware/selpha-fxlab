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
  this split exists to avoid.

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
        if len(deep) == len(readable):
            row["kind"] = FULL
        elif len(deep) >= min_pairs_partial:
            row["kind"] = PARTIAL
        else:
            row["kind"] = UNEXPLAINED
        rows[date] = row
    counts = {kind: sum(1 for r in rows.values() if r["kind"] == kind)
              for kind in (FULL, PARTIAL, UNEXPLAINED)}
    return {"dates": rows, "counts": counts}


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
    out: dict[str, dict[str, int]] = {}
    for date in sorted(in_window):
        bucket = out.setdefault(date[:4], {
            FULL: 0, PARTIAL: 0, UNEXPLAINED: 0,
            "traded_through": 0, "closed_week": 0})
        if not present.get(date):
            bucket["closed_week"] += 1
            continue
        row = rows.get(date)
        if row is None:
            # No empty hour at all on a day the market was open: the feed
            # quoted straight through the holiday.
            bucket["traded_through"] += 1
        else:
            bucket[row["kind"]] += 1
    return out


def unexplained_profile(classified: dict[str, Any]) -> dict[str, Any]:
    """The empty hours the calendar does **not** explain.

    The card is explicit that these are data facts for T4 rather than
    holidays, and keeping them out of the calendar is the point: a date where
    two pairs went quiet and ten did not is evidence about the feed, and
    filing it as a market closure would launder that evidence into a fact
    about the market.
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
    return {
        "dates": len(rows),
        "hours": sum(sum(r["hours_by_pair"].values()) for r in rows),
        "by_year": dict(sorted(by_year.items())),
        "by_pair": {k: by_pair[k] for k in sorted(by_pair)},
        "pairs_deep_per_date": dict(sorted(widths.items())),
        "worst": sorted(
            ({"date": r["date"], "pairs": len(r["hours_by_pair"]),
              "hours": sum(r["hours_by_pair"].values())} for r in rows),
            key=lambda r: (-r["hours"], r["date"]))[:20],
    }


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
        "unexplained": unexplained_profile(classified),
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
    lines.append("")
    return "\n".join(lines)


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
