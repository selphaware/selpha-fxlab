"""The experiment ledger: append-only, written before results exist.

Multiple-testing honesty (pre-reg #10) dies silently when a result is reported
without a record of what else was tried. The ledger is the record, and the
ordering rule is the load-bearing part: a **start** entry is appended *before*
the experiment runs, so an experiment that is abandoned, that crashes, or whose
result is quietly deleted still leaves a mark. Writing the ledger afterwards
would let the loop choose which trials count, which is exactly the failure.

Format is JSON Lines, one object per line, at ``experiments/ledger.jsonl``.
Append-only by convention and by the gate: the gate compares result files
against ledger entries and fails a result with no start entry preceding it.

Two record kinds:

``start``
    ``experiment_id``, ``taskcard``, ``config_sha256``, ``code_commit``,
    ``code_dirty``, ``seed``, ``mode``, ``rerun_class``, ``subset_sha256``,
    ``started_at``.

``end``
    ``experiment_id``, ``ended_at``, ``status``, ``result_files``,
    ``result_hash``, ``scored``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import subprocess
from typing import Any, Final, Iterable

_LOG: Final[logging.Logger] = logging.getLogger("research.ledger")

#: Ledger location relative to the project root.
LEDGER_RELPATH: Final[str] = "experiments/ledger.jsonl"

RECORD_START: Final[str] = "start"
RECORD_END: Final[str] = "end"

#: Re-run classes (ruling D5). ``full`` is the default and is mandatory for any
#: experiment that decides survival or kill unless a full re-run exceeds about
#: two hours.
RERUN_FULL: Final[str] = "full"
RERUN_SUBSET: Final[str] = "deterministic-subset"
RERUN_CLASSES: Final[tuple[str, ...]] = (RERUN_FULL, RERUN_SUBSET)

#: Fields a start record must carry for the gate to accept it.
START_FIELDS: Final[tuple[str, ...]] = (
    "experiment_id", "taskcard", "config_sha256", "code_commit", "seed",
    "mode", "rerun_class", "started_at")

#: Fields an end record must carry.
END_FIELDS: Final[tuple[str, ...]] = (
    "experiment_id", "ended_at", "status", "result_files", "result_hash")


def now_iso() -> str:
    """Current UTC time, ISO-8601 with a ``+00:00`` offset."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def ledger_path(base: pathlib.Path) -> pathlib.Path:
    """The ledger file under a project root."""
    return base / LEDGER_RELPATH


def code_commit(base: pathlib.Path) -> tuple[str, bool]:
    """Return ``(commit, dirty)`` for the working tree.

    A dirty tree is recorded rather than rejected: refusing to run would stop
    the loop mid-task, while a silent record of "commit abc, dirty" tells the
    reviewer exactly how much the commit hash is worth.
    """
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(base),
                              capture_output=True, text=True, timeout=30)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(base),
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        _LOG.warning("could not read git state: %s", exc)
        return "unknown", True
    if head.returncode != 0:
        return "unknown", True
    return head.stdout.strip(), bool(status.stdout.strip())


def append(base: pathlib.Path, record: dict[str, Any]) -> pathlib.Path:
    """Append one record to the ledger and return the ledger path."""
    path = ledger_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return path


def append_start(base: pathlib.Path, *, experiment_id: str, taskcard: str,
                 config_sha256: str, seed: int, mode: str,
                 rerun_class: str = RERUN_FULL,
                 subset_sha256: str | None = None,
                 note: str = "") -> dict[str, Any]:
    """Write the start record. Call this **before** the experiment runs.

    Raises:
        ValueError: For an unknown re-run class, or a subset class with no
            declared subset hash -- the subset must be fixed before results
            exist, which is the whole point of recording its hash here.
    """
    if rerun_class not in RERUN_CLASSES:
        raise ValueError(f"unknown rerun_class {rerun_class!r}; "
                         f"known: {list(RERUN_CLASSES)}")
    if rerun_class == RERUN_SUBSET and not subset_sha256:
        raise ValueError("rerun_class 'deterministic-subset' requires "
                         "subset_sha256, declared before results exist")
    commit, dirty = code_commit(base)
    record = {
        "record": RECORD_START,
        "experiment_id": experiment_id,
        "taskcard": taskcard,
        "config_sha256": config_sha256,
        "code_commit": commit,
        "code_dirty": dirty,
        "seed": int(seed),
        "mode": mode,
        "rerun_class": rerun_class,
        "subset_sha256": subset_sha256,
        "started_at": now_iso(),
        "note": note,
    }
    append(base, record)
    _LOG.info("ledger start %s (taskcard %s, config %s)",
              experiment_id, taskcard, config_sha256[:12])
    return record


def append_end(base: pathlib.Path, *, experiment_id: str, status: str,
               result_files: Iterable[str], result_hash: str | None,
               scored: bool) -> dict[str, Any]:
    """Write the end record once results are on disk."""
    record = {
        "record": RECORD_END,
        "experiment_id": experiment_id,
        "ended_at": now_iso(),
        "status": status,
        "result_files": sorted(result_files),
        "result_hash": result_hash,
        "scored": bool(scored),
    }
    append(base, record)
    _LOG.info("ledger end %s (%s, hash %s)", experiment_id, status,
              (result_hash or "none")[:12])
    return record


def read(base: pathlib.Path) -> list[dict[str, Any]]:
    """Read every ledger record. Malformed lines are reported, not skipped.

    A malformed line becomes ``{"record": "malformed", ...}`` so the gate sees
    it and fails, rather than a corrupt ledger quietly reading as a short one.
    """
    path = ledger_path(base)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            records.append({"record": "malformed", "line": number,
                            "error": str(exc)})
            continue
        if not isinstance(parsed, dict):
            records.append({"record": "malformed", "line": number,
                            "error": "not a JSON object"})
            continue
        records.append(parsed)
    return records


def starts_for(records: Iterable[dict[str, Any]],
               experiment_id: str) -> list[dict[str, Any]]:
    """Every start record for one experiment id, in file order."""
    return [r for r in records
            if r.get("record") == RECORD_START
            and r.get("experiment_id") == experiment_id]


def ends_for(records: Iterable[dict[str, Any]],
             experiment_id: str) -> list[dict[str, Any]]:
    """Every end record for one experiment id, in file order."""
    return [r for r in records
            if r.get("record") == RECORD_END
            and r.get("experiment_id") == experiment_id]


def trial_count(records: Iterable[dict[str, Any]],
                taskcard: str | None = None) -> int:
    """How many experiments were started, optionally for one task card.

    This is the number a review must state next to any highlighted result
    (pre-reg #10). It counts starts, not results, so abandoned trials count.
    """
    starts = [r for r in records if r.get("record") == RECORD_START]
    if taskcard is not None:
        starts = [r for r in starts if r.get("taskcard") == taskcard]
    return len(starts)
