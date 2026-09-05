"""The T2a experiment entry: what the bulk ingestion actually produced.

The download is not the experiment. Ruling D5 says ingestion is never re-run, so
the ledgered, reproducible artefact is this: a deterministic reading of what is
on disk once the pulling has stopped. It opens the sharded manifests, the
per-chunk progress records and the session records, walks the tick store for its
footprint, and reads every bar table **through the research loader** so that the
access log -- not an assertion in a report -- is what proves no sealed date
reached the research tree.

It computes nothing that is not coverage, validation or cost bookkeeping. T2a's
non-goals are explicit: no EDA, no statistics beyond coverage and validation
counts, no strategy content.

Expected hours come from the same derived FX week boundary the ingestion used
(:func:`fxlab.ingestion.sessions.is_market_open`), never from a hardcoded UTC
hour, so "complete" means complete against the calendar the data was stored by.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
from typing import Any, Final, Iterator, Sequence

from fxlab.ingestion.bars import offset_alias
from fxlab.ingestion.manifest import (MANIFEST_NAME, STATUS_CLOSED,
                                      STATUS_EMPTY, STATUS_GAP, STATUS_OK)
from fxlab.ingestion.sessions import is_market_open
from research.bulk_ingest import (CHUNKS_NAME, MANIFEST_DIRNAME,
                                  SESSIONS_NAME, TIMEFRAMES, read_chunk_log)
from research.loader import project_root
from research.seal import as_date, is_sealed

_LOG: Final[logging.Logger] = logging.getLogger("research.ingest_summary")

#: Gap detail rows carried in the payload. The counts are always complete; this
#: bounds the listing so a result document stays readable. T1 measured no
#: missing region in this range, so a listing this long is itself a finding.
MAX_GAP_ROWS: Final[int] = 2000

#: Warning reasons worth breaking out per pair rather than only in the total.
#: ``EMPTY_TRADING_HOUR`` is the holiday-calendar input of pre-reg #5 and is
#: T3's material, counted here and interpreted nowhere.
PER_PAIR_WARNINGS: Final[tuple[str, ...]] = (
    "EMPTY_TRADING_HOUR", "TICK_COUNT_OUTLIER", "SPREAD_CEILING")


def run(*, params: dict[str, Any], seed: int, loader: Any) -> dict[str, Any]:
    """Summarise the stored research window. The T2a experiment entry point.

    Args:
        params: The ``[experiment.params]`` table.
        seed: Recorded for the hash; nothing here is stochastic.
        loader: A :class:`~research.loader.ResearchLoader` in scoring mode.

    Returns:
        A JSON-plain payload: per-pair coverage against the derived calendar,
        the gap table, duplicate and warning counts, storage footprint, bar
        tables, and the throughput the pull achieved.
    """
    base = pathlib.Path(loader.root)
    pairs = [str(p) for p in params.get("pairs", [])]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    timeframes = [offset_alias(t) for t in params.get("timeframes", TIMEFRAMES)]
    verify = [offset_alias(t)
              for t in params.get("verify_timeframes", timeframes)]
    experiment_dir = pathlib.Path(params["experiment_dir"])
    if not experiment_dir.is_absolute():
        experiment_dir = project_root() / experiment_dir

    expected = expected_hours(start, end)
    calendar = open_map(start, end)
    coverage = {pair: read_pair(base, pair, start, end, expected, calendar)
                for pair in pairs}
    for pair in pairs:
        coverage[pair]["storage"] = tick_footprint(base, pair, start, end)
        coverage[pair]["bars"] = read_bars(loader, pair, verify)

    gaps = sorted(
        (row for pair in pairs for row in coverage[pair].pop("gap_rows")),
        key=lambda r: (r["pair"], r["date"], r["hour"]))
    throughput = read_throughput(experiment_dir)

    totals = {
        key: sum(int(coverage[p]["totals"][key]) for p in pairs)
        for key in ("hours_recorded", "hours_ok", "hours_empty",
                    "hours_closed", "hours_gap", "ticks",
                    "duplicates_dropped", "compressed_bytes")
    }
    totals["expected_open_hours"] = expected["open"] * len(pairs)
    totals["expected_hours"] = expected["total"] * len(pairs)
    totals["stored_bytes"] = sum(int(coverage[p]["storage"]["bytes"])
                                 for p in pairs)
    totals["stored_files"] = sum(int(coverage[p]["storage"]["files"])
                                 for p in pairs)
    totals["bar_rows"] = sum(int(row["rows"]) for p in pairs
                             for row in coverage[p]["bars"].values())

    boundary = {
        "derived_closed_hours_fetched": sum(
            int(coverage[p]["boundary"]["fetched"]) for p in pairs),
        "derived_closed_hours_with_ticks": sum(
            int(coverage[p]["boundary"]["closed_but_traded"]) for p in pairs),
        "derived_open_hours_empty": sum(
            int(coverage[p]["boundary"]["open_but_empty"]) for p in pairs),
    }

    return {
        "note": ("Bulk ingestion of the research window, read back from the "
                 "sharded manifests and the store itself. Coverage, validation "
                 "and cost bookkeeping only: T2a's non-goals put EDA, holiday "
                 "calendars and cross-venue checks in later cards."),
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "days": (end - start).days + 1,
                   "expected_hours_per_pair": expected["total"],
                   "expected_open_hours_per_pair": expected["open"],
                   "expected_closed_hours_per_pair": expected["closed"],
                   "pairs": len(pairs)},
        "totals": totals,
        "pairs": coverage,
        "gaps": {"count": len(gaps), "listed": min(len(gaps), MAX_GAP_ROWS),
                 "by_reason": _gap_reasons(gaps),
                 "rows": gaps[:MAX_GAP_ROWS]},
        "week_boundary": boundary,
        "warning_profile": _warning_profile(pairs, coverage),
        "throughput": throughput,
        "loader": {"mode": loader.mode,
                   "timeframes_verified": sorted(verify),
                   "sealed_dates_served": loader.access.sealed_dates()},
        "seed": int(seed),
    }


# --------------------------------------------------------------------------- #
# The calendar the data was stored by
# --------------------------------------------------------------------------- #

def expected_hours(start: dt.date, end: dt.date) -> dict[str, Any]:
    """Hours the derived FX week says the range contains.

    Args:
        start: First UTC date, inclusive.
        end: Last UTC date, inclusive.

    Returns:
        ``{"total", "open", "closed", "open_by_year"}``. The boundary tracks
        17:00 ``America/New_York`` and is derived, never a fixed UTC hour --
        hardcoding 21:00 is wrong for about half of every year.
    """
    open_by_year: dict[str, int] = {}
    total = opened = 0
    for day in _days(start, end):
        for hour in range(24):
            total += 1
            if is_market_open(dt.datetime(day.year, day.month, day.day, hour,
                                          tzinfo=dt.timezone.utc)):
                opened += 1
                key = f"{day.year:04d}"
                open_by_year[key] = open_by_year.get(key, 0) + 1
    return {"total": total, "open": opened, "closed": total - opened,
            "open_by_year": open_by_year}


def open_map(start: dt.date, end: dt.date) -> dict[tuple[str, int], bool]:
    """``(date, hour) -> is the market open``, computed once for every pair.

    The same derived boundary the ingestion stored by. Built as a lookup because
    a zoneinfo conversion per manifest record, over a million of them, costs
    more than the whole rest of this summary.
    """
    out: dict[tuple[str, int], bool] = {}
    for day in _days(start, end):
        iso = day.isoformat()
        for hour in range(24):
            out[(iso, hour)] = is_market_open(
                dt.datetime(day.year, day.month, day.day, hour,
                            tzinfo=dt.timezone.utc))
    return out


def _days(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    """Every date in an inclusive range."""
    for offset in range((end - start).days + 1):
        yield start + dt.timedelta(days=offset)


def _months(start: dt.date, end: dt.date) -> list[str]:
    """Every ``YYYY-MM`` the inclusive range touches, oldest first."""
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


# --------------------------------------------------------------------------- #
# The manifests
# --------------------------------------------------------------------------- #

def read_pair(base: pathlib.Path, pair: str, start: dt.date, end: dt.date,
              expected: dict[str, Any],
              calendar: dict[tuple[str, int], bool]) -> dict[str, Any]:
    """Aggregate one pair's manifest shards, one shard at a time.

    Shards are read and discarded rather than accumulated: a decade of twelve
    pairs is over a million hour records, and holding them all is neither
    necessary nor possible to do comfortably.
    """
    totals = {"hours_recorded": 0, "hours_ok": 0, "hours_empty": 0,
              "hours_closed": 0, "hours_gap": 0, "ticks": 0,
              "duplicates_dropped": 0, "compressed_bytes": 0,
              "shards": 0, "shards_missing": 0}
    by_year: dict[str, dict[str, int]] = {}
    warnings: dict[str, int] = {}
    # Where the spread warnings fall. Per-pair ceilings were tuned on the modern
    # era, so a flag is only interesting once you know whether the flagged hours
    # cluster somewhere explicable -- the daily roll, say -- or scatter.
    warn_hour: dict[str, dict[str, int]] = {}
    warn_year: dict[str, dict[str, int]] = {}
    errors: dict[str, int] = {}
    gap_rows: list[dict[str, Any]] = []
    days_with_data: set[str] = set()
    first_ts: str | None = None
    last_ts: str | None = None
    # The derived-boundary audit. The driver fetches the shut hour either side
    # of every week boundary rather than assuming it; these two counters are
    # what that buys. `closed_but_traded` above zero means the derivation is
    # shutting the week too early somewhere, which is the failure that
    # hardcoding 21:00 UTC produces for half of every year.
    boundary = {"fetched": 0, "closed_but_traded": 0, "open_but_empty": 0}

    for month in _months(start, end):
        path = (base / MANIFEST_DIRNAME / f"pair={pair}" / month / MANIFEST_NAME)
        if not path.is_file():
            totals["shards_missing"] += 1
            continue
        totals["shards"] += 1
        document = json.loads(path.read_text(encoding="utf-8"))
        year = month[:4]
        bucket = by_year.setdefault(year, {
            "hours_recorded": 0, "hours_ok": 0, "hours_empty": 0,
            "hours_closed": 0, "hours_gap": 0, "ticks": 0,
            "duplicates_dropped": 0, "days_with_data": 0})

        for record in document.get("hours", []):
            status = str(record.get("status", ""))
            ticks = int(record.get("written_ticks", 0))
            slot = (str(record.get("date")), int(record.get("hour", -1)))
            market_open = calendar.get(slot, True)
            if not market_open and str(record.get("origin") or "") != "derived:market-closed":
                boundary["fetched"] += 1
                if status == STATUS_OK:
                    boundary["closed_but_traded"] += 1
            if market_open and status == STATUS_EMPTY:
                boundary["open_but_empty"] += 1
            dupes = int(record.get("duplicates_dropped", 0))
            totals["hours_recorded"] += 1
            bucket["hours_recorded"] += 1
            totals["ticks"] += ticks
            bucket["ticks"] += ticks
            totals["duplicates_dropped"] += dupes
            bucket["duplicates_dropped"] += dupes
            totals["compressed_bytes"] += int(record.get("compressed_bytes", 0))
            if status == STATUS_OK:
                totals["hours_ok"] += 1
                bucket["hours_ok"] += 1
                days_with_data.add(str(record.get("date")))
                stamp = record.get("first_ts")
                if stamp and (first_ts is None or stamp < first_ts):
                    first_ts = str(stamp)
                stamp = record.get("last_ts")
                if stamp and (last_ts is None or stamp > last_ts):
                    last_ts = str(stamp)
            elif status == STATUS_EMPTY:
                totals["hours_empty"] += 1
                bucket["hours_empty"] += 1
            elif status == STATUS_CLOSED:
                totals["hours_closed"] += 1
                bucket["hours_closed"] += 1
            elif status == STATUS_GAP:
                totals["hours_gap"] += 1
                bucket["hours_gap"] += 1
                gap_rows.append({
                    "pair": pair, "date": str(record.get("date")),
                    "hour": int(record.get("hour", -1)),
                    "reasons": sorted({str(i.get("reason"))
                                       for i in record.get("issues", [])}),
                    "detail": _first_detail(record.get("issues", [])),
                })

        validation = document.get("validation") or {}
        for entry in validation.get("warnings", []):
            reason = str(entry.get("reason", "UNKNOWN"))
            warnings[reason] = warnings.get(reason, 0) + 1
            hour = entry.get("hour")
            if hour is not None:
                bucket_h = warn_hour.setdefault(reason, {})
                key = f"{int(hour):02d}"
                bucket_h[key] = bucket_h.get(key, 0) + 1
            date = str(entry.get("date") or "")
            if len(date) >= 4:
                bucket_y = warn_year.setdefault(reason, {})
                bucket_y[date[:4]] = bucket_y.get(date[:4], 0) + 1
        for entry in validation.get("errors", []):
            reason = str(entry.get("reason", "UNKNOWN"))
            errors[reason] = errors.get(reason, 0) + 1

    for year, bucket in by_year.items():
        bucket["days_with_data"] = sum(1 for d in days_with_data
                                       if d.startswith(year))
        bucket["expected_open_hours"] = int(
            expected["open_by_year"].get(year, 0))
        bucket["open_hours_accounted"] = (bucket["hours_ok"]
                                          + bucket["hours_empty"])

    accounted = totals["hours_ok"] + totals["hours_empty"]
    return {
        "totals": totals,
        "boundary": boundary,
        "by_year": {k: by_year[k] for k in sorted(by_year)},
        "days_with_data": len(days_with_data),
        "first_tick": first_ts,
        "last_tick": last_ts,
        "expected_open_hours": expected["open"],
        "open_hours_accounted": accounted,
        "open_hour_completeness": (round(accounted / expected["open"], 6)
                                   if expected["open"] else 0.0),
        "warnings": {k: warnings[k] for k in sorted(warnings)},
        "warnings_by_hour": {k: dict(sorted(warn_hour[k].items()))
                             for k in sorted(warn_hour)},
        "warnings_by_year": {k: dict(sorted(warn_year[k].items()))
                             for k in sorted(warn_year)},
        "errors": {k: errors[k] for k in sorted(errors)},
        "gap_rows": gap_rows,
    }


def _first_detail(issues: Sequence[dict[str, Any]]) -> str:
    """The first issue detail on a record, truncated to stay readable."""
    for issue in issues:
        detail = str(issue.get("detail", ""))
        if detail:
            return detail[:240]
    return ""


# --------------------------------------------------------------------------- #
# The store itself
# --------------------------------------------------------------------------- #

def tick_footprint(base: pathlib.Path, pair: str,
                   start: dt.date | None = None,
                   end: dt.date | None = None) -> dict[str, Any]:
    """Bytes and files one pair's ticks occupy, by directory listing.

    Bounded by the experiment's window when one is given. The store is shared:
    T2a filled 2015-2025 into the same tree that T2b fills 2005-2014, so a walk
    that counted every ``date=`` partition would report each card the whole
    store as though it were its own. A card asked for the storage it *added*.
    """
    root = base / "ticks" / f"pair={pair}"
    files = 0
    total = 0
    days = 0
    lo = start.isoformat() if start else None
    hi = end.isoformat() if end else None
    if not root.is_dir():
        return {"files": 0, "bytes": 0, "days": 0, "bytes_per_tick": 0.0}
    with os.scandir(root) as day_dirs:
        for day_dir in day_dirs:
            if not day_dir.is_dir():
                continue
            date = day_dir.name.partition("=")[2]
            if (lo is not None and date < lo) or (hi is not None and date > hi):
                continue
            days += 1
            with os.scandir(day_dir.path) as entries:
                for entry in entries:
                    if entry.name.endswith(".parquet"):
                        files += 1
                        total += entry.stat().st_size
    return {"files": files, "bytes": total, "days": days}


def read_bars(loader: Any, pair: str,
              timeframes: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Read each bar table through the loader and report what it holds.

    Going through the loader rather than opening the Parquet is the point: it
    polices every date it serves against the seal and records them, so the
    result's own access log is the evidence that the stored bars stop where the
    research window stops.
    """
    out: dict[str, dict[str, Any]] = {}
    for timeframe in timeframes:
        alias = offset_alias(timeframe)
        try:
            frame = loader.load_bars(pair, alias)
        except FileNotFoundError:
            out[alias] = {"rows": 0, "first": None, "last": None,
                          "present": False, "bytes": 0}
            continue
        rows = int(len(frame))
        first = last = None
        if rows:
            first = frame["ts"].iloc[0].isoformat()
            last = frame["ts"].iloc[-1].isoformat()
        from fxlab.ingestion.bars import bars_path

        path = bars_path(pathlib.Path(loader.root), pair, alias)
        out[alias] = {
            "rows": rows, "first": first, "last": last, "present": True,
            "bytes": int(path.stat().st_size) if path.is_file() else 0,
            "sealed": bool(last and is_sealed(last[:10])),
        }
        del frame
    return out


# --------------------------------------------------------------------------- #
# What the pull cost
# --------------------------------------------------------------------------- #

def _gap_reasons(gaps: Sequence[dict[str, Any]]) -> dict[str, int]:
    """How many surviving gaps carry each reason token.

    The count alone cannot say what a gap *is*. An hour the feed never served
    and an hour it served with a crossed quote are both gaps, and they mean
    opposite things: the first is absent history, the second is history the
    pipeline refused. Reporting the split is what lets the report say which.
    """
    out: dict[str, int] = {}
    for row in gaps:
        for reason in (row.get("reasons") or ["UNKNOWN"]):
            out[str(reason)] = out.get(str(reason), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _warning_profile(pairs: Sequence[str],
                     coverage: dict[str, Any]) -> dict[str, Any]:
    """Warning counts folded across pairs, by UTC hour and by year.

    A flag that piles onto one hour of the day is telling you about market
    microstructure; one that scatters evenly is telling you about the ceiling
    that raised it. Reporting only the total tells you neither.
    """
    by_hour: dict[str, dict[str, int]] = {}
    by_year: dict[str, dict[str, int]] = {}
    for pair in pairs:
        for reason, hours in (coverage[pair].get("warnings_by_hour") or {}).items():
            bucket = by_hour.setdefault(reason, {})
            for hour, count in hours.items():
                bucket[hour] = bucket.get(hour, 0) + int(count)
        for reason, years in (coverage[pair].get("warnings_by_year") or {}).items():
            bucket = by_year.setdefault(reason, {})
            for year, count in years.items():
                bucket[year] = bucket.get(year, 0) + int(count)
    return {
        "by_hour": {k: dict(sorted(by_hour[k].items())) for k in sorted(by_hour)},
        "by_year": {k: dict(sorted(by_year[k].items())) for k in sorted(by_year)},
    }


def _gap_history(path: pathlib.Path,
                 final: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the pull recorded as gaps, and what survived the closing sweep.

    The store's gap count is the *end* state. On its own it flatters the run:
    an hour the feed refused on a Tuesday and served on a Friday leaves no
    trace once the sweep has been. The difference between the two is the
    interesting number, because it says whether a gap meant absent history or
    only a feed in a bad mood.

    Only records marked ``complete`` are counted. An aborted chunk records
    every hour it never got to as a gap, then has them stripped as unsettled
    and re-asked; folding those in would invent gaps that never existed.
    """
    worst: dict[str, int] = {}
    if pathlib.Path(path).exists():
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or not record.get("complete"):
                continue
            key = str(record.get("chunk", ""))
            gaps = int(record.get("hours_gap", 0))
            if key and gaps > worst.get(key, 0):
                worst[key] = gaps
    recorded = sum(worst.values())
    remaining = sum(int(r.get("hours_gap", 0)) for r in final.values())
    return {
        "recorded_during_pull": recorded,
        "pair_months_affected": sum(1 for v in worst.values() if v),
        "remaining_after_sweep": remaining,
        "recovered_by_sweep": max(0, recorded - remaining),
    }


def read_throughput(experiment_dir: pathlib.Path) -> dict[str, Any]:
    """Aggregate the per-chunk and per-session records the driver left behind.

    Read from the experiment directory rather than from the ledger on purpose:
    the ledger grows by one line every time this experiment runs, so hashing a
    reading of it would make the result irreproducible by construction.
    """
    chunks = read_chunk_log(experiment_dir / CHUNKS_NAME)
    sessions = _read_sessions(experiment_dir / SESSIONS_NAME)
    gap_history = _gap_history(experiment_dir / CHUNKS_NAME, chunks)

    by_level: dict[str, dict[str, Any]] = {}
    by_timeframe: dict[str, dict[str, Any]] = {}
    ingest_seconds = bar_seconds = 0.0
    requests = throttles = 0
    for record in chunks.values():
        for entry in record.get("bars", []):
            alias = str(entry.get("timeframe", "?"))
            bucket = by_timeframe.setdefault(alias, {"builds": 0, "dates": 0,
                                                     "rows": 0, "seconds": 0.0})
            bucket["builds"] += 1
            bucket["dates"] += int(entry.get("dates", 0))
            bucket["rows"] += int(entry.get("rows", 0))
            bucket["seconds"] += float(entry.get("seconds", 0.0))
        level = str(record.get("level", "?"))
        bucket = by_level.setdefault(level, {"chunks": 0, "requests": 0,
                                             "throttles": 0, "seconds": 0.0})
        bucket["chunks"] += 1
        bucket["requests"] += int(record.get("requests", 0))
        bucket["throttles"] += int(record.get("throttles", 0))
        bucket["seconds"] += float(record.get("ingest_seconds", 0.0))
        requests += int(record.get("requests", 0))
        throttles += int(record.get("throttles", 0))
        ingest_seconds += float(record.get("ingest_seconds", 0.0))
        bar_seconds += float(record.get("bar_seconds", 0.0))
    for bucket in by_level.values():
        bucket["seconds"] = round(bucket["seconds"], 1)
        bucket["throttle_rate"] = (round(bucket["throttles"] / bucket["requests"], 5)
                                   if bucket["requests"] else 0.0)
        bucket["requests_per_second"] = (
            round(bucket["requests"] / bucket["seconds"], 3)
            if bucket["seconds"] else 0.0)

    for bucket in by_timeframe.values():
        bucket["seconds"] = round(bucket["seconds"], 2)

    wall = sum(float(s.get("seconds", 0.0)) for s in sessions)
    parked = sum(float(s.get("seconds_parked", 0.0)) for s in sessions)
    return {
        "gap_history": gap_history,
        "chunks_recorded": len(chunks),
        "sessions": len(sessions),
        "requests": requests,
        "throttles": throttles,
        "throttle_rate": round(throttles / requests, 5) if requests else 0.0,
        "ingest_seconds": round(ingest_seconds, 1),
        "bar_seconds": round(bar_seconds, 1),
        "session_wall_seconds": round(wall, 1),
        "session_parked_seconds": round(parked, 1),
        "requests_per_second": (round(requests / wall, 3) if wall else 0.0),
        "by_level": {k: by_level[k] for k in sorted(by_level)},
        "bars_by_timeframe": {k: by_timeframe[k] for k in sorted(by_timeframe)},
        "calibration": [s.get("calibration") for s in sessions
                        if s.get("calibration")],
        "session_rows": sessions,
    }


def _read_sessions(path: pathlib.Path) -> list[dict[str, Any]]:
    """Every session record, oldest first, tolerating a truncated final line."""
    out: list[dict[str, Any]] = []
    if not pathlib.Path(path).exists():
        return out
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out
