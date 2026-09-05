"""Re-validate every stored hour offline, against the rules that stored it.

T3 Step 1. The ingestion validated each hour once, in flight, and then wrote it
down. This re-opens all of it and asks the same questions again, from the other
side: not "did the feed serve something valid" but "is what is on disk still
what the manifest says it is".

The two are not the same check and the difference is the point. Between them
sit a Parquet writer, a resumable driver that rewrites shards, a power loss
mid-chunk (HANDOFF2), and a store that two cards filled into the same tree.
Every one of those is a way for the manifest and the files to drift apart
without either being individually wrong.

What is checked, per stored hour:

* the pinned Arrow schema, column by column, with no extra columns -- the same
  assertion the Phase 1 gate makes about a freshly ingested hour;
* ``written_ticks`` against the actual row count;
* ``ask >= bid`` and both strictly positive, the ``CROSSED_QUOTE`` and
  ``NON_POSITIVE_PRICE`` rules;
* timestamps non-decreasing, UTC, and inside the hour the file is named for;
* the hour open under the derived FX week, which is ``CLOSED_MARKET_TICK``
  -- and, because every tick is proven inside its own hour, a Saturday tick
  cannot hide in a Friday file.

The pass is checkpointed per pair-month to ``validation.jsonl`` and resumable,
for the reason the ingestion was: a run measured in hours that cannot be
stopped is a run nobody dares start. The experiment entry point then reads that
file back, so the judged, hashed artefact is a deterministic reading of a
completed pass rather than a re-execution of it (ruling D5).

A failure is reported, never repaired. The card says so, and it is right to:
a discrepancy between the manifest and the store is evidence about how the
store came to be, and repairing it in place destroys that evidence.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json
import logging
import os
import pathlib
from typing import Any, Final, Iterable, Sequence

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from fxlab.ingestion.manifest import MANIFEST_NAME, STATUS_OK
from fxlab.ingestion.sessions import is_market_open
from research.bulk_ingest import MANIFEST_DIRNAME
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.validate_store")

#: The checkpoint, one JSON object per pair-month.
VALIDATION_NAME: Final[str] = "validation.jsonl"

#: The pinned tick schema, restated here rather than imported.
#:
#: Importing the writer's own schema would make this test tautological: a
#: writer that drifted would drift the assertion with it. The Phase 1 contract
#: is written down in CLAUDE.md, and this is that contract, typed out.
TICK_SCHEMA: Final[pa.Schema] = pa.schema([
    ("pair", pa.large_string()),
    ("ts", pa.timestamp("us", tz="UTC")),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("bid_volume", pa.float64()),
    ("ask_volume", pa.float64()),
    ("source", pa.large_string()),
])

#: Named failure kinds. The Phase 1 reason tokens where one applies, so a
#: failure here is greppable against the same vocabulary the ingestion used.
CROSSED_QUOTE: Final[str] = "CROSSED_QUOTE"
NON_POSITIVE_PRICE: Final[str] = "NON_POSITIVE_PRICE"
CLOSED_MARKET_TICK: Final[str] = "CLOSED_MARKET_TICK"
SCHEMA_DRIFT: Final[str] = "SCHEMA_DRIFT"
ROW_COUNT_MISMATCH: Final[str] = "ROW_COUNT_MISMATCH"
TS_NOT_MONOTONIC: Final[str] = "TS_NOT_MONOTONIC"
TS_OUT_OF_HOUR: Final[str] = "TS_OUT_OF_HOUR"
FILE_MISSING: Final[str] = "FILE_MISSING"
FILE_UNREADABLE: Final[str] = "FILE_UNREADABLE"

#: Failure details carried per pair-month before truncation. A pass that fails
#: everywhere should not write a gigabyte of identical messages.
MAX_FAILURE_DETAILS: Final[int] = 40


def month_labels(start: dt.date, end: dt.date) -> list[str]:
    """Every ``YYYY-MM`` from ``start`` to ``end`` inclusive."""
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def validate_hour(path: pathlib.Path, record: dict[str, Any],
                  ) -> list[dict[str, Any]]:
    """Every way one stored hour disagrees with its manifest entry.

    Args:
        path: The Parquet file the manifest points at.
        record: The manifest hour record.

    Returns:
        A list of failure dicts, empty when the hour is exactly as recorded.
    """
    pair = str(record.get("pair"))
    date = str(record.get("date"))
    hour = int(record.get("hour", -1))
    label = f"{pair} {date}T{hour:02d}:00Z"
    out: list[dict[str, Any]] = []

    def fail(kind: str, detail: str) -> None:
        out.append({"kind": kind, "pair": pair, "date": date, "hour": hour,
                    "detail": f"{label}: {detail}"})

    if not path.is_file():
        fail(FILE_MISSING, f"manifest points at {path.name}, which is absent")
        return out
    try:
        table = pq.read_table(path)
    except Exception as exc:  # noqa: BLE001 - an unreadable file is a finding
        fail(FILE_UNREADABLE, f"{type(exc).__name__}: {exc}")
        return out

    got = table.schema
    if [f.name for f in got] != [f.name for f in TICK_SCHEMA]:
        fail(SCHEMA_DRIFT,
             f"columns {[f.name for f in got]} against the pinned "
             f"{[f.name for f in TICK_SCHEMA]}")
        return out
    for field, expected in zip(got, TICK_SCHEMA):
        if not field.type.equals(expected.type):
            fail(SCHEMA_DRIFT,
                 f"column {field.name} is {field.type}, pinned as "
                 f"{expected.type}")
    if out:
        return out

    rows = table.num_rows
    written = int(record.get("written_ticks", 0))
    if rows != written:
        fail(ROW_COUNT_MISMATCH,
             f"{rows} row(s) on disk against {written} recorded")

    if not rows:
        return out

    bid, ask, stamps = table["bid"], table["ask"], table["ts"]
    crossed = int(pc.sum(pc.greater(bid, ask)).as_py() or 0)
    if crossed:
        fail(CROSSED_QUOTE, f"{crossed} tick(s) with bid > ask")
    nonpositive = int(pc.sum(pc.or_(pc.less_equal(bid, 0),
                                    pc.less_equal(ask, 0))).as_py() or 0)
    if nonpositive:
        fail(NON_POSITIVE_PRICE, f"{nonpositive} tick(s) with a price <= 0")

    if rows > 1:
        ordered = pc.all(pc.less_equal(stamps.slice(0, rows - 1),
                                       stamps.slice(1))).as_py()
        if not ordered:
            fail(TS_NOT_MONOTONIC, "timestamps are not non-decreasing")

    opens = dt.datetime.combine(as_date(date), dt.time(hour=hour),
                                tzinfo=dt.timezone.utc)
    closes = opens + dt.timedelta(hours=1)
    first = pc.min(stamps).as_py()
    last = pc.max(stamps).as_py()
    if first < opens or last >= closes:
        fail(TS_OUT_OF_HOUR,
             f"span {first.isoformat()} .. {last.isoformat()} leaves "
             f"[{opens.isoformat()}, {closes.isoformat()})")

    # The derived FX week, not a hardcoded UTC hour. Every tick is proven
    # inside its own hour above, so an open hour cannot hide a Saturday tick
    # and this one check covers the whole `CLOSED_MARKET_TICK` rule.
    if not is_market_open(opens):
        fail(CLOSED_MARKET_TICK,
             "stored with ticks in an hour the derived week calls shut")
    return out


def validate_chunk(base: pathlib.Path, pair: str,
                   month: str) -> dict[str, Any]:
    """Re-validate one pair-month. The unit of work and of the checkpoint."""
    shard = (base / MANIFEST_DIRNAME / f"pair={pair}" / month / MANIFEST_NAME)
    row: dict[str, Any] = {"pair": pair, "month": month, "hours": 0,
                           "ticks": 0, "failures": 0, "by_kind": {},
                           "details": [], "shard": bool(shard.is_file())}
    if not shard.is_file():
        return row
    document = json.loads(shard.read_text(encoding="utf-8"))
    for record in document.get("hours", []):
        if str(record.get("status")) != STATUS_OK:
            continue
        row["hours"] += 1
        row["ticks"] += int(record.get("written_ticks", 0))
        stored = record.get("path")
        path = (pathlib.Path(stored) if stored
                else _derived_path(base, record))
        for failure in validate_hour(path, record):
            row["failures"] += 1
            kind = failure["kind"]
            row["by_kind"][kind] = row["by_kind"].get(kind, 0) + 1
            if len(row["details"]) < MAX_FAILURE_DETAILS:
                row["details"].append(failure)
    row["by_kind"] = dict(sorted(row["by_kind"].items()))
    return row


def _derived_path(base: pathlib.Path, record: dict[str, Any]) -> pathlib.Path:
    """Where an hour's Parquet lives, when the record does not say."""
    pair = str(record.get("pair"))
    date = str(record.get("date"))
    hour = int(record.get("hour", 0))
    return (base / "ticks" / f"pair={pair}" / f"date={date}"
            / f"{pair}_{date}_{hour:02d}h.parquet")


def read_checkpoint(path: pathlib.Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every pair-month already validated, keyed by ``(pair, month)``.

    A truncated final line is tolerated and dropped: a pass killed mid-write
    should cost one pair-month of repeated work, not the whole run.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not pathlib.Path(path).is_file():
        return out
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _LOG.warning("dropping a truncated checkpoint line")
            continue
        out[(str(row.get("pair")), str(row.get("month")))] = row
    return out


def _append(path: pathlib.Path, row: dict[str, Any]) -> None:
    """Append one checkpoint record and flush it to the OS."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_pass(base: pathlib.Path, pairs: Sequence[str], start: dt.date,
             end: dt.date, checkpoint: pathlib.Path,
             workers: int) -> dict[str, Any]:
    """Validate every stored hour in the window, resuming where left off.

    Args:
        base: The store root, ``data/research``.
        pairs: Pairs to walk.
        start: First date of the window.
        end: Last date of the window.
        checkpoint: Where to append per-pair-month results.
        workers: Parallel processes. The work is IO-bound per file and
            CPU-bound per column check, and there are one and a half million
            files; serially this is over an hour.

    Returns:
        A small summary of what the pass did.
    """
    months = month_labels(start, end)
    done = read_checkpoint(checkpoint)
    todo = [(p, m) for p in pairs for m in months if (p, m) not in done]
    _LOG.info("%d pair-month(s) to validate, %d already checkpointed",
              len(todo), len(done))
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    completed = 0
    if todo:
        with futures.ProcessPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(validate_chunk, base, pair, month):
                       (pair, month) for pair, month in todo}
            for future in futures.as_completed(pending):
                pair, month = pending[future]
                row = future.result()
                _append(checkpoint, row)
                completed += 1
                if row["failures"]:
                    _LOG.error("%s %s: %d failure(s) %s", pair, month,
                               row["failures"], row["by_kind"])
                if completed % 100 == 0:
                    _LOG.info("%d/%d pair-months validated", completed,
                              len(todo))
    rows = read_checkpoint(checkpoint)
    return summarise(rows.values())


def summarise(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Fold checkpoint rows into the numbers a report states."""
    by_kind: dict[str, int] = {}
    by_pair: dict[str, dict[str, int]] = {}
    hours = ticks = failures = shards = 0
    details: list[dict[str, Any]] = []
    for row in rows:
        hours += int(row.get("hours", 0))
        ticks += int(row.get("ticks", 0))
        failures += int(row.get("failures", 0))
        shards += 1 if row.get("shard") else 0
        pair = str(row.get("pair"))
        bucket = by_pair.setdefault(pair, {"hours": 0, "ticks": 0,
                                           "failures": 0})
        bucket["hours"] += int(row.get("hours", 0))
        bucket["ticks"] += int(row.get("ticks", 0))
        bucket["failures"] += int(row.get("failures", 0))
        for kind, count in (row.get("by_kind") or {}).items():
            by_kind[kind] = by_kind.get(kind, 0) + int(count)
        details.extend(row.get("details") or [])
    details.sort(key=lambda d: (str(d.get("pair")), str(d.get("date")),
                                int(d.get("hour", 0)), str(d.get("kind"))))
    return {
        "pair_months": len(list(by_pair)) and shards,
        "hours_validated": hours,
        "ticks_validated": ticks,
        "failures": failures,
        "by_kind": dict(sorted(by_kind.items())),
        "by_pair": {k: by_pair[k] for k in sorted(by_pair)},
        "details": details[:MAX_FAILURE_DETAILS],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.validate_store",
        description="Re-validate every stored hour against its manifest.")
    parser.add_argument("--config", required=True, type=pathlib.Path,
                        help="the T3 experiment config")
    parser.add_argument("--workers", type=int, default=0,
                        help="parallel processes; 0 picks from the CPU count")
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the pass and print what it found."""
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
    pairs = [str(p) for p in params["pairs"]]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    store = base / str(params.get("data_dir", "data/research"))
    experiment_dir = base / str(params["experiment_dir"])
    workers = args.workers or max(2, min(24, (os.cpu_count() or 4) - 2))

    summary = run_pass(store, pairs, start, end,
                       experiment_dir / VALIDATION_NAME, workers)
    print(json.dumps({k: v for k, v in summary.items() if k != "details"},
                     indent=2))
    if summary["failures"]:
        print(f"VALIDATION_FAILURES {summary['failures']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
