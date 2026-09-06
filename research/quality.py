"""The T3 experiment: what the store is, checked four ways.

The card has four steps and this is the deterministic reading of all of them.
Two produced their evidence out of band and are read back here rather than
re-run -- the full-store validation pass takes minutes across every file, and
the OANDA sample needs a third-party API to be reachable. Ruling D5 already
settled the principle for ingestion, and it applies for the same reason: a
judge that re-executed a network sample would be judging OANDA's uptime.

The other two are cheap and are re-derived here every time, on purpose:

* the **reconciliation** (Step 0.6), which is the audit that started this card
  -- manifests against the ingestion results against the files on disk. It is
  re-derived because a reconciliation whose inputs are cached is not one;
* the **calendar** (Step 2), which is re-derived from the manifests and then
  compared against the committed ``config/calendar.toml``. A disagreement is
  a hard failure. That is what keeps a tracked, hand-editable file honest: the
  calendar cannot drift away from the data that justified it without the gate
  noticing.

One manifest walk feeds both, because there are 2,904 shards and two walks
would cost a minute for nothing.

The bar tables are read through the loader, which is what puts this experiment
under the seal and under ruling R1, and gives the result an access log that
proves both. Comparing every pair's 1h bar timestamps against the stored hours
the manifests record is the end-to-end check the M2 audit only ran on two
pairs.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import tomllib
from typing import Any, Final, Sequence

from fxlab.ingestion.manifest import (MANIFEST_NAME, STATUS_CLOSED,
                                      STATUS_EMPTY, STATUS_GAP, STATUS_OK)
from fxlab.ingestion.sessions import is_market_open
from research import calendar_build
from research import crosscheck_class
from research import crosscheck_oanda as crosscheck
from research import crosscheck_spreads
from research import validate_store
from research.bulk_ingest import MANIFEST_DIRNAME
from research.exclusions import (EXCLUSIONS, clamp_window, is_excluded,
                                 summarise as summarise_exclusions)
from research.loader import exclusion_canary
from research.loader import canary as seal_canary
from research.seal import HOLDOUT_CUTOFF, as_date, is_sealed

_LOG: Final[logging.Logger] = logging.getLogger("research.quality")

#: Rows of a mismatch listing carried in the payload before truncation.
MAX_MISMATCH_ROWS: Final[int] = 50


def run(*, params: dict[str, Any], seed: int, loader: Any,
        costs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the store four ways. The T3 experiment entry point.

    Args:
        params: The ``[experiment.params]`` table.
        seed: Recorded for the hash; the OANDA sample used it to pick dates.
        loader: A :class:`~research.loader.ResearchLoader` in scoring mode.

    Returns:
        A JSON-plain payload: the reconciliation, the validation pass, the
        calendar derivation and its comparison against the committed file, the
        cross-check distribution, and the rulings in force.
    """
    base = pathlib.Path(loader.root).parent.parent
    store = pathlib.Path(loader.root)
    pairs = [str(p) for p in params.get("pairs", [])]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    experiment_dir = pathlib.Path(params["experiment_dir"])
    if not experiment_dir.is_absolute():
        experiment_dir = base / experiment_dir

    walk = walk_manifests(store, pairs, start, end)
    reconciliation = reconcile(base, store, walk, pairs, start, end,
                               [str(x) for x in params.get("reconcile", [])])
    calendar = build_calendar(walk, pairs, start, end, params)
    committed = compare_committed(base, params, calendar)
    validation = validate_store.summarise(
        validate_store.read_checkpoint(
            experiment_dir / validate_store.VALIDATION_NAME).values())
    cross = read_crosscheck(experiment_dir, params)
    classes = compare_committed_classes(
        base, params, cross["r7"], (start.isoformat(), end.isoformat()))
    # The classified rows are the input to both the summary and the committed
    # file, and there are 11,790 of them. They stay out of the payload: the
    # committed classification is where a consumer reads them, and duplicating
    # them here would multiply the result document by seven to say the same
    # thing twice.
    cross["r7"].pop("_classified", None)
    classes.pop("derived", None)
    bars = check_bars(loader, walk, pairs, start, end)

    return {
        "note": ("Data quality for the whole store: a reconciliation of the "
                 "manifests against the ingestion results and the files on "
                 "disk, an offline re-validation of every stored hour, the "
                 "holiday calendar derived from hour statuses, and a sampled "
                 "cross-check against a second venue -- re-issued under "
                 "ruling R7 by T4's Step 0, which re-thresholds the same "
                 "stored sample by density and commits the per-hour "
                 "classification. No EDA, no strategy content; the experiment "
                 "is not scorable."),
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "pairs": len(pairs)},
        "reconciliation": reconciliation,
        "validation": validation,
        "calendar": calendar,
        "calendar_committed": committed,
        "crosscheck": cross,
        "crosscheck_committed": classes,
        "bars": bars,
        "rulings": rulings(pairs),
        "loader": {"mode": loader.mode,
                   "sealed_dates_served": loader.access.sealed_dates(),
                   "excluded_dates_withheld": len(loader.access.excluded),
                   "excluded_pairs": loader.access.excluded_pairs()},
        "seed": int(seed),
    }


# --------------------------------------------------------------------------- #
# One walk of the manifests, feeding everything that needs them
# --------------------------------------------------------------------------- #

def walk_manifests(store: pathlib.Path, pairs: Sequence[str], start: dt.date,
                   end: dt.date) -> dict[str, Any]:
    """Read every manifest shard once and bucket what the rest of T3 needs.

    Returns four things: status counts per pair-year, the set of stored files
    each pair-year claims, the per-date empty/traded counts the calendar is
    derived from, and the set of stored hour timestamps the bar check compares
    against. Splitting these into four passes would be clearer and would cost
    four minutes of a judge's time on every gate run.
    """
    lo, hi = start.isoformat(), end.isoformat()
    counts: dict[str, dict[str, dict[str, int]]] = {}
    claimed: dict[str, dict[str, set[str]]] = {}
    empty: dict[str, dict[str, int]] = {}
    traded: dict[str, dict[str, int]] = {}
    present: dict[str, set[str]] = {}
    stored_hours: dict[str, set[str]] = {}
    shards = 0

    for pair in pairs:
        root = store / MANIFEST_DIRNAME / f"pair={pair}"
        if not root.is_dir():
            continue
        for month_dir in sorted(root.iterdir()):
            shard = month_dir / MANIFEST_NAME
            if not month_dir.is_dir() or not shard.is_file():
                continue
            shards += 1
            document = json.loads(shard.read_text(encoding="utf-8"))
            for record in document.get("hours", []):
                date = str(record.get("date"))
                if not (lo <= date <= hi):
                    continue
                status = str(record.get("status"))
                year = date[:4]
                bucket = (counts.setdefault(pair, {})
                          .setdefault(year, {"ok": 0, "empty": 0,
                                             "closed": 0, "gap": 0,
                                             "ticks": 0, "dupes": 0}))
                if status in bucket:
                    bucket[status] += 1
                bucket["ticks"] += int(record.get("written_ticks", 0))
                bucket["dupes"] += int(record.get("duplicates_dropped", 0))

                hour = int(record.get("hour", -1))
                if status == STATUS_OK:
                    stored = record.get("path")
                    if stored:
                        (claimed.setdefault(pair, {}).setdefault(year, set())
                         .add(pathlib.Path(stored).name))
                    stored_hours.setdefault(pair, set()).add(
                        f"{date}T{hour:02d}")

                if status in (STATUS_EMPTY, STATUS_OK):
                    opens = dt.datetime.combine(
                        as_date(date), dt.time(hour=hour),
                        tzinfo=dt.timezone.utc)
                    if is_market_open(opens):
                        present.setdefault(date, set()).add(pair)
                        target = empty if status == STATUS_EMPTY else traded
                        row = target.setdefault(date, {})
                        row[pair] = row.get(pair, 0) + 1
    return {"counts": counts, "claimed": claimed, "shards": shards,
            "scan": {"empty": empty, "traded": traded,
                     "pairs_present": present},
            "stored_hours": stored_hours}


def disk_listing(store: pathlib.Path, pairs: Sequence[str], start: dt.date,
                 end: dt.date) -> dict[str, dict[str, set[str]]]:
    """Every stored tick Parquet, per pair and year, straight off the disk.

    Deliberately a directory listing rather than anything cleverer: the whole
    point of a reconciliation is that the two sides are gathered by different
    means. Asking the manifest where the files are and then asking the manifest
    whether they are there would prove nothing.
    """
    lo, hi = start.isoformat(), end.isoformat()
    out: dict[str, dict[str, set[str]]] = {}
    for pair in pairs:
        root = store / "ticks" / f"pair={pair}"
        if not root.is_dir():
            continue
        with os.scandir(root) as days:
            for day in days:
                if not day.is_dir() or not day.name.startswith("date="):
                    continue
                date = day.name.split("=", 1)[1]
                if not (lo <= date <= hi):
                    continue
                bucket = out.setdefault(pair, {}).setdefault(date[:4], set())
                with os.scandir(day.path) as entries:
                    for entry in entries:
                        if entry.name.endswith(".parquet"):
                            bucket.add(entry.name)
    return out


# --------------------------------------------------------------------------- #
# Step 0.6: the reconciliation
# --------------------------------------------------------------------------- #

def reconcile(base: pathlib.Path, store: pathlib.Path, walk: dict[str, Any],
              pairs: Sequence[str], start: dt.date, end: dt.date,
              experiments: Sequence[str]) -> dict[str, Any]:
    """Manifests against the ingestion results against the files on disk.

    Three sources that should agree and are gathered three different ways. The
    M2 audit ran this by hand and found the reports wrong in six places; the
    card asks for it to be re-run against the fixed reports, and running it
    inside the experiment is what stops it being a one-off.
    """
    counts = walk["counts"]
    claimed = walk["claimed"]
    disk = disk_listing(store, pairs, start, end)

    rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for pair in pairs:
        years = sorted(set(counts.get(pair, {})) | set(disk.get(pair, {})))
        for year in years:
            bucket = counts.get(pair, {}).get(year, {})
            on_disk = disk.get(pair, {}).get(year, set())
            in_manifest = claimed.get(pair, {}).get(year, set())
            row = {
                "pair": pair, "year": year,
                "ok": int(bucket.get("ok", 0)),
                "empty": int(bucket.get("empty", 0)),
                "closed": int(bucket.get("closed", 0)),
                "gap": int(bucket.get("gap", 0)),
                "ticks": int(bucket.get("ticks", 0)),
                "dupes": int(bucket.get("dupes", 0)),
                "files_on_disk": len(on_disk),
                "manifest_only": len(in_manifest - on_disk),
                "disk_only": len(on_disk - in_manifest),
            }
            rows.append(row)
            if (row["ok"] != row["files_on_disk"] or row["manifest_only"]
                    or row["disk_only"]):
                mismatches.append(row)

    totals = {key: sum(int(r[key]) for r in rows)
              for key in ("ok", "empty", "closed", "gap", "ticks", "dupes",
                          "files_on_disk", "manifest_only", "disk_only")}
    return {
        "shards_read": int(walk["shards"]),
        "pair_years": len(rows),
        "totals": totals,
        "mismatching_pair_years": len(mismatches),
        "mismatches": mismatches[:MAX_MISMATCH_ROWS],
        "against_experiments": _against_experiments(base, rows, experiments),
        "by_year": _reconciliation_by_year(rows),
    }


def _reconciliation_by_year(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The same rows folded to one line per year, across all pairs."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = out.setdefault(str(row["year"]), {
            "ok": 0, "empty": 0, "closed": 0, "gap": 0, "ticks": 0,
            "files_on_disk": 0})
        for key in bucket:
            bucket[key] += int(row[key])
    return {k: out[k] for k in sorted(out)}


def _against_experiments(base: pathlib.Path, rows: Sequence[dict[str, Any]],
                         experiments: Sequence[str]) -> list[dict[str, Any]]:
    """Each ingestion result's totals against this walk of the manifests.

    The ingestion results are what the reports print. If they and a fresh walk
    disagree, at least one report is stating something the store does not say,
    which is the class of defect this whole card exists to close.
    """
    out: list[dict[str, Any]] = []
    for name in experiments:
        path = base / "experiments" / name / "result.json"
        if not path.is_file():
            out.append({"experiment": name, "present": False})
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        payload = document.get("payload") or {}
        window = payload.get("window") or {}
        totals = payload.get("totals") or {}
        lo, hi = str(window.get("start", "")), str(window.get("end", ""))
        mine = {"ok": 0, "empty": 0, "closed": 0, "gap": 0, "ticks": 0,
                "dupes": 0}
        for row in rows:
            # Compare like with like: each ingestion card covers whole years,
            # so a year-grained walk lines up with its window exactly.
            if lo[:4] <= str(row["year"]) <= hi[:4]:
                for key in mine:
                    mine[key] += int(row[key])
        theirs = {
            "ok": int(totals.get("hours_ok", 0)),
            "empty": int(totals.get("hours_empty", 0)),
            "closed": int(totals.get("hours_closed", 0)),
            "gap": int(totals.get("hours_gap", 0)),
            "ticks": int(totals.get("ticks", 0)),
            "dupes": int(totals.get("duplicates_dropped", 0)),
        }
        out.append({
            "experiment": name, "present": True,
            "window": {"start": lo, "end": hi},
            "result_hash": str(document.get("result_hash", ""))[:16],
            "manifest_walk": mine, "result_totals": theirs,
            "agrees": mine == theirs,
            "differences": {k: theirs[k] - mine[k] for k in mine
                            if theirs[k] != mine[k]},
        })
    return out


# --------------------------------------------------------------------------- #
# Step 2: the calendar, re-derived and checked against the committed file
# --------------------------------------------------------------------------- #

def build_calendar(walk: dict[str, Any], pairs: Sequence[str], start: dt.date,
                   end: dt.date, params: dict[str, Any]) -> dict[str, Any]:
    """Re-derive the calendar from this run's manifest walk."""
    min_empty = int(params["calendar_min_empty_hours"])
    min_pairs = int(params["calendar_min_pairs_partial"])
    classified = calendar_build.classify(
        walk["scan"], pairs, min_empty_hours=min_empty,
        min_pairs_partial=min_pairs)
    static = calendar_build.static_holidays(range(start.year, end.year + 1))
    return {
        "rules": {"min_empty_hours": min_empty,
                  "min_pairs_partial": min_pairs},
        "counts": classified["counts"],
        "full": sorted(d for d, r in classified["dates"].items()
                       if r["kind"] == calendar_build.FULL),
        "partial": {d: r["pairs_empty_deep"]
                    for d, r in sorted(classified["dates"].items())
                    if r["kind"] == calendar_build.PARTIAL},
        "static_count": len(static),
        "comparison": calendar_build.compare_static(
            classified, static, walk["scan"], start, end),
        "unexplained": calendar_build.unexplained_profile(
            classified, pairs, static),
    }


def compare_committed(base: pathlib.Path, params: dict[str, Any],
                      calendar: dict[str, Any]) -> dict[str, Any]:
    """The committed ``config/calendar.toml`` against the re-derivation.

    A tracked file anybody can open is a file anybody can edit, and a holiday
    quietly added by hand would propagate into every card that trusts the
    calendar. Re-deriving and comparing on every gate run makes that edit fail
    loudly instead.
    """
    path = base / str(params.get("calendar_path", "config/calendar.toml"))
    if not path.is_file():
        return {"present": False, "agrees": False,
                "detail": f"{path.name} has not been built"}
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    block = parsed.get("calendar") or {}
    committed_full = sorted((block.get("full") or {}))
    committed_partial = {k: sorted(v) for k, v in
                         sorted((block.get("partial") or {}).items())}
    derived_full = sorted(calendar["full"])
    derived_partial = {k: sorted(v) for k, v in
                       sorted(calendar["partial"].items())}
    missing = sorted(set(derived_full) - set(committed_full))
    extra = sorted(set(committed_full) - set(derived_full))
    # The informational section (ruling R8) is compared for the same reason
    # the holidays are: it is tracked, so it is editable, and a class quietly
    # changed by hand would reach every reader of the file. It marks no hour
    # ineligible for anything -- comparing it is about the file staying
    # derived, not about the calendar's authority.
    committed_unexplained = block.get("unexplained") or {}
    committed_classes = {str(k): str(v) for k, v in
                         (committed_unexplained.get("by_date") or {}).items()}
    derived_classes = {
        str(k): str(v) for k, v in
        ((calendar["unexplained"].get("classified") or {}).get("dates")
         or {}).items()}
    unexplained_agrees = (committed_classes == derived_classes
                          and int(committed_unexplained.get("excluded_only", -1))
                          == int(calendar["unexplained"]["excluded_only"]["dates"]))
    return {
        "present": True,
        "path": str(params.get("calendar_path", "config/calendar.toml")),
        "rules_agree": (int(block.get("min_empty_hours", -1))
                        == calendar["rules"]["min_empty_hours"]
                        and int(block.get("min_pairs_partial", -1))
                        == calendar["rules"]["min_pairs_partial"]),
        "full_agrees": derived_full == committed_full,
        "partial_agrees": derived_partial == committed_partial,
        "unexplained_agrees": unexplained_agrees,
        "agrees": (derived_full == committed_full
                   and derived_partial == committed_partial
                   and unexplained_agrees),
        "derived_not_committed": missing,
        "committed_not_derived": extra,
        "full_days": len(committed_full),
        "partial_days": len(committed_partial),
        "unexplained_dates": len(committed_classes),
        "excluded_only_dates": int(committed_unexplained.get(
            "excluded_only", 0)),
    }


# --------------------------------------------------------------------------- #
# Step 3: the cross-check, read back
# --------------------------------------------------------------------------- #

def read_crosscheck(experiment_dir: pathlib.Path,
                    params: dict[str, Any]) -> dict[str, Any]:
    """Fold the checkpointed OANDA sample into a distribution.

    Two verdicts come out of one sample. The **pinned** one is pre-reg #7 as it
    was written and as T3 first applied it: a flat 1.0 pip on every hour. The
    **re-issued** one is ruling R7, which thresholds by density. Both are
    carried, because an amendment that erases what it amended leaves nobody
    able to see what changed -- and what changed here is most of the answer.
    """
    threshold = float(params["crosscheck_threshold_pips"])
    rows = crosscheck.read_checkpoint(
        experiment_dir / crosscheck.CROSSCHECK_NAME)
    summary = crosscheck.summarise(rows.values(), threshold)
    summary["r7"] = reissue_under_r7(experiment_dir, rows, params, threshold)
    summary["pair_dates_sampled"] = len(rows)
    summary["hours_sampled_per_date"] = len(params["crosscheck_hours"])
    summary["sample_hours_utc"] = [int(h) for h in params["crosscheck_hours"]]
    summary["dates_per_year"] = int(params["crosscheck_dates_per_year"])
    summary["roll_window_ny"] = [int(params["crosscheck_roll_start_hour_ny"]),
                                 int(params["crosscheck_roll_end_hour_ny"])]
    path = experiment_dir / crosscheck.AVAILABILITY_NAME
    rows_avail: list[dict[str, Any]] = []
    hosts: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            hosts.add(str(entry.pop("host", "")))
            rows_avail.append(entry)
    summary["availability"] = {
        "host": sorted(h for h in hosts if h),
        "pairs": sorted(rows_avail, key=lambda r: str(r.get("pair"))),
    }
    # Pre-reg #7: an hour beyond threshold outside the roll window blocks
    # **that hour** from research use pending review. Per hour, not per pair
    # and not per year: the pre-registration says "that data", and widening it
    # to the pair-year would block decades over a handful of thin hours, which
    # is a decision nobody registered. The verdict is stated here, in the
    # payload, so no report has to decide it and none can soften it.
    flagged = summary["flagged"]
    summary["blocked_hours"] = [
        {"pair": f["pair"], "date": f["date"], "hour": int(f["hour"])}
        for f in flagged]
    rollup: dict[tuple[str, str], int] = {}
    for f in flagged:
        key = (str(f["pair"]), str(f["date"])[:4])
        rollup[key] = rollup.get(key, 0) + 1
    summary["blocked_by_pair_year"] = [
        {"pair": p, "year": y, "hours": n}
        for (p, y), n in sorted(rollup.items())]
    summary["blocked_count"] = len(flagged)
    summary["verdict"] = ("BLOCKED" if flagged else "CLEAR")
    summary["flagged"] = flagged[:MAX_MISMATCH_ROWS]
    return summary


def reissue_under_r7(experiment_dir: pathlib.Path,
                     rows: dict[tuple[str, str], dict[str, Any]],
                     params: dict[str, Any],
                     threshold: float) -> dict[str, Any]:
    """Re-classify the stored cross-check sample under ruling R7.

    The T4 card's Step 0. The sample is the one on disk -- re-drawing it would
    be re-running the experiment rather than re-reading it, and a reader could
    not tell which they were looking at. Each hour's own median spread comes
    from the checkpointed :mod:`research.crosscheck_spreads` pass; a middle-band
    hour without one is a hard failure rather than a default, because
    defaulting it either way silently re-applies the flat threshold R7 exists
    to replace.
    """
    spreads = crosscheck_spreads.spread_index(
        crosscheck_spreads.read_spreads(
            experiment_dir / crosscheck_spreads.SPREADS_NAME).values())
    classified: list[dict[str, Any]] = []
    unmeasured: list[str] = []
    for (pair, date), record in sorted(rows.items()):
        for hour in record.get("hours") or []:
            spread = spreads.get((pair, date, int(hour["hour"])))
            try:
                classified.append(
                    crosscheck_class.classify(hour, threshold, spread))
            except crosscheck_class.SpreadNotMeasured:
                unmeasured.append(f"{pair} {date} {int(hour['hour']):02d}")
    summary = crosscheck_class.summarise(classified, threshold)
    summary["hours_without_a_measured_spread"] = len(unmeasured)
    summary["unmeasured"] = sorted(unmeasured)[:MAX_MISMATCH_ROWS]
    summary["spread_pass"] = {
        "checkpoint": crosscheck_spreads.SPREADS_NAME,
        "hours_measured": len(spreads),
    }
    summary["_classified"] = classified
    return summary


def compare_committed_classes(base: pathlib.Path, params: dict[str, Any],
                              reissue: dict[str, Any],
                              window: tuple[str, str]) -> dict[str, Any]:
    """The committed ``config/crosscheck.toml`` against the re-derivation.

    Held to exactly the discipline ``config/calendar.toml`` is held to, and for
    the same reason: it is a tracked file anybody can open, every card
    downstream will trust what it says about which hours were corroborated, and
    a hand-edited line in it would propagate silently. Re-deriving and
    comparing on every gate run makes that edit fail loudly instead.
    """
    path = base / str(params.get("crosscheck_classes_path",
                                 crosscheck_class.CLASSES_RELPATH))
    roll = (int(params["crosscheck_roll_start_hour_ny"]),
            int(params["crosscheck_roll_end_hour_ny"]))
    derived = crosscheck_class.derive(
        reissue["_classified"], base_pips=float(params["crosscheck_threshold_pips"]),
        window=window, roll=roll)
    if not path.is_file():
        return {"present": False, "agrees": False,
                "detail": f"{path.name} has not been built",
                "derived": derived}
    committed = crosscheck_class.load_classes(path)
    mismatched: list[str] = []
    derived_hours = derived["hours"]
    for pair in sorted(set(derived_hours) | set(committed.sampled_pairs())):
        entries = derived_hours.get(pair, {})
        for key, code in sorted(entries.items()):
            date, _, hour = key.partition(" ")
            if committed.classify(pair, date, int(hour)) != \
                    crosscheck_class.BY_CODE[code]:
                mismatched.append(f"{pair} {key}")
    return {
        "present": True,
        "path": str(params.get("crosscheck_classes_path",
                               crosscheck_class.CLASSES_RELPATH)),
        "rules_agree": (committed.rules["dense_ticks"]
                        == crosscheck_class.DENSE_TICKS
                        and committed.rules["unverifiable_ticks"]
                        == crosscheck_class.UNVERIFIABLE_TICKS
                        and abs(committed.rules["base_pips"]
                                - float(params["crosscheck_threshold_pips"]))
                        < 1e-12),
        "counts_agree": (committed.counts["classified"]
                         == derived["counts"]["classified"]
                         and committed.counts[crosscheck_class.CLASS_BLOCKED]
                         == derived["counts"][crosscheck_class.CLASS_BLOCKED]),
        "hours_disagreeing": len(mismatched),
        "disagreements": sorted(mismatched)[:MAX_MISMATCH_ROWS],
        "agrees": not mismatched,
        "hours_committed": committed.counts["classified"],
        "derived": derived,
    }


# --------------------------------------------------------------------------- #
# Bars against the hours the manifests record
# --------------------------------------------------------------------------- #

def check_bars(loader: Any, walk: dict[str, Any], pairs: Sequence[str],
               start: dt.date, end: dt.date) -> dict[str, Any]:
    """Every pair's 1h bar timestamps against its stored hours.

    One bar per stored hour, at the hour's open, and nothing else: a bar that
    no stored hour backs is a bar built from data that is no longer there, and
    a stored hour with no bar is an hour no strategy will ever see. The M2
    audit checked two pairs by hand; there are twelve.
    """
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        window = clamp_window(pair, start, end)
        all_hours = walk["stored_hours"].get(pair, set())
        stored = {h for h in all_hours if not is_excluded(pair, h[:10])}
        # Tell the loader what this caller clamped away. The loader never
        # clamps on its own, so without this the drop leaves no trace and an
        # exclusion becomes indistinguishable from data that was never pulled.
        withheld = sorted({h[:10] for h in all_hours - stored})
        if withheld:
            loader.note_excluded(pair, withheld)
        if window is None:
            rows.append({"pair": pair, "bars": 0, "stored_hours": len(stored),
                         "readable": False})
            continue
        frame = loader.load_bars(pair, "1h", start=window[0], end=window[1])
        stamps = frame["ts"]
        bars = {f"{t.date().isoformat()}T{t.hour:02d}" for t in stamps}
        on_hour = bool((stamps.dt.minute == 0).all()
                       and (stamps.dt.second == 0).all()
                       and (stamps.dt.microsecond == 0).all())
        increasing = bool(stamps.is_monotonic_increasing
                          and not stamps.duplicated().any())
        rows.append({
            "pair": pair, "readable": True,
            "bars": int(len(frame)),
            "stored_hours": len(stored),
            "bars_without_a_stored_hour": len(bars - stored),
            "stored_hours_without_a_bar": len(stored - bars),
            "timestamps_on_the_hour": on_hour,
            "timestamps_strictly_increasing": increasing,
            "first": stamps.iloc[0].isoformat() if len(frame) else None,
            "last": stamps.iloc[-1].isoformat() if len(frame) else None,
        })
    mismatched = [r for r in rows
                  if r.get("readable")
                  and (r["bars_without_a_stored_hour"]
                       or r["stored_hours_without_a_bar"]
                       or not r["timestamps_on_the_hour"]
                       or not r["timestamps_strictly_increasing"])]
    return {"timeframe": "1h", "by_pair": rows,
            "pairs_agreeing": sum(1 for r in rows if r.get("readable")
                                  and r not in mismatched),
            "pairs_mismatching": len(mismatched)}


# --------------------------------------------------------------------------- #
# The rulings, recorded where a report can read them
# --------------------------------------------------------------------------- #

def rulings(pairs: Sequence[str]) -> dict[str, Any]:
    """R1-R6 and the enforcement evidence for the ones that are enforceable.

    R3, R4 and R6 constrain how a report may *speak*, so there is nothing here
    to exercise -- they are recorded so a reader can see the whole set. R1 and
    R5 are code, and the canaries below are them running.
    """
    refused_seal, seal_detail = seal_canary()
    refused_excl, excl_detail = exclusion_canary()
    return {
        "R1": {
            "statement": ("AUDUSD before 2011-01-01 is excluded from "
                          "research; the crossed-quote rule is unchanged"),
            "enforced_by": "research.exclusions via research.loader.check_date",
            "reason": "PAIR_EXCLUDED_WINDOW",
            "canary_refused": bool(refused_excl),
            "canary_detail": excl_detail[:240],
            "windows": summarise_exclusions(pairs),
        },
        "R2": {
            "statement": ("the JPY Sunday pre-open hours stay rejected and "
                          "gain the sub-label PRE_OPEN_FEED_DATA"),
            "enforced_by": "research.ingest_summary._sublabels",
            "sublabel": "PRE_OPEN_FEED_DATA",
        },
        "R3": {
            "statement": ("spread-regime comparisons across eras must control "
                          "for ticks per hour; never a raw SPREAD_OUTLIER "
                          "count"),
            "enforced_by": "report framing; a T5 requirement",
        },
        "R4": {
            "statement": ("tick counts are not a volume or activity proxy "
                          "until a T4 card has characterised the density "
                          "series"),
            "enforced_by": "report framing",
        },
        "R5": {
            "statement": ("the holiday calendar derives from manifest status "
                          "== empty, never from the EMPTY_TRADING_HOUR "
                          "warning list"),
            "enforced_by": "research.calendar_build.scan",
        },
        "R6": {
            "statement": ("no hand-written numbers in reports; every figure "
                          "is derived at render time"),
            "enforced_by": "research.ingest_report.check_note",
        },
        "R7": {
            "statement": ("the cross-check threshold is density-aware: 1.0 "
                          "pip at >= 3,000 ticks, 1.0 pip + the hour's own "
                          "median spread at 500-2,999, UNVERIFIABLE below "
                          "500; the roll window stays exempt and a failing "
                          "hour stays BLOCKED"),
            "enforced_by": ("research.crosscheck_class, tagged through "
                            "research.loader.crosscheck_class"),
            "amends": "pre-registered decision #7",
            "bands": {"dense_ticks": crosscheck_class.DENSE_TICKS,
                      "unverifiable_ticks":
                          crosscheck_class.UNVERIFIABLE_TICKS},
        },
        "R8": {
            "statement": ("the static major-holiday list marks hours "
                          "ineligible for execution in every backtest, in "
                          "every year, regardless of whether the feed served "
                          "data; the empties-derived calendar component is "
                          "informational"),
            "enforced_by": "a backtester rule, to be implemented before T7",
            "status": "stated, not yet implemented",
        },
        "seal": {
            "cutoff": HOLDOUT_CUTOFF.isoformat(),
            "canary_refused": bool(refused_seal),
            "canary_detail": seal_detail[:240],
        },
    }
