"""Experiment definition, execution and result hashing.

An experiment is a TOML config plus an entry point. Running one always follows
the same order, and the order is the honesty mechanism:

1. hash the config,
2. append a **start** record to the ledger -- before any result exists,
3. build a loader in the declared mode, so the seal is enforced by the same
   code research uses,
4. run the entry point with the declared seed,
5. hash the payload and write ``result.json``,
6. append an **end** record.

The result hash covers the things that determine the answer -- experiment id,
task card, entry point, seed, parameters, the data scope actually served, and
the payload -- and deliberately excludes wall-clock time and absolute paths, so
that a re-run on the same inputs reproduces the hash exactly and a re-run on
different inputs cannot.

Config shape::

    [experiment]
    id           = "T0-spread-by-session"
    taskcard     = "T0"
    entry        = "research.spread_session:run"
    seed         = 20260819
    mode         = "mechanical"          # or "scoring"
    scored       = false
    rerun_class  = "full"                # or "deterministic-subset"
    data_root    = "data/live_week"      # required in mechanical mode

    [experiment.params]
    pair = "EURUSD"

    [experiment.costs]                   # required when scored = true
    commission_rate = 2e-05
    commission_min  = 2.0
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import logging
import pathlib
import tomllib
from typing import Any, Callable, Final

from research import ledger as ledger_mod
from research.loader import (MODE_MECHANICAL, MODE_SCORING, MODES,
                             ResearchLoader)
from research.seal import HOLDOUT_SEALED, RESEARCH_DATA_DIR, sealed_dates_in_text

_LOG: Final[logging.Logger] = logging.getLogger("research.experiment")

#: Filename every experiment writes its result to, inside its own directory.
RESULT_NAME: Final[str] = "result.json"

#: Named refusal reasons the runner emits verbatim on stderr.
BAD_EXPERIMENT_CONFIG: Final[str] = "BAD_EXPERIMENT_CONFIG"
MECHANICAL_NOT_SCORABLE: Final[str] = "MECHANICAL_NOT_SCORABLE"
SEAL_CONFIG_DATE: Final[str] = "SEAL_CONFIG_DATE"
SEAL_SCOPE_BREACH: Final[str] = "SEAL_SCOPE_BREACH"

#: Cost ladder rungs every scorecard reports (pre-reg #1). Strings, because
#: they are JSON object keys and 1.0 must not become "1".
LADDER: Final[tuple[str, ...]] = ("1.0", "1.2", "1.5", "2.0")

#: The pinned survival bar (pre-reg #1, pinned 2026-08-19).
SURVIVAL_BAR: Final[str] = "1.5"

#: The rung below which a candidate is dead rather than parked.
PARK_BAR: Final[str] = "1.2"


class ExperimentError(Exception):
    """A refusal to run or to record, carrying a named reason."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclasses.dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """A parsed and policed experiment config.

    Attributes:
        path: Where it was read from.
        raw: The exact bytes, which is what ``sha256`` is taken over.
        experiment_id: Unique id; also the experiment directory name.
        taskcard: The task card this experiment belongs to, e.g. ``T4``.
        entry: ``module:function`` to call.
        seed: Integer seed; recorded, passed to the entry, and required.
        mode: Loader mode.
        scored: Whether this experiment emits a scorecard.
        rerun_class: ``full`` or ``deterministic-subset``.
        subset_sha256: Hash of the declared subset, when the class demands one.
        data_root: Data root for the loader, project-relative.
        params: Free-form parameters handed to the entry point.
        costs: Cost-model parameters; required when scored.
    """

    path: pathlib.Path
    raw: bytes
    experiment_id: str
    taskcard: str
    entry: str
    seed: int
    mode: str
    scored: bool
    rerun_class: str
    subset_sha256: str | None
    data_root: str | None
    params: dict[str, Any]
    costs: dict[str, Any]

    @property
    def sha256(self) -> str:
        """Hash of the config file's exact bytes."""
        return hashlib.sha256(self.raw).hexdigest()


def load_config(path: pathlib.Path) -> ExperimentConfig:
    """Read, parse and police one experiment config.

    Raises:
        ExperimentError: With ``BAD_EXPERIMENT_CONFIG`` for anything missing or
            unknown, ``SEAL_CONFIG_DATE`` for a scoring config that names a
            sealed date, or ``MECHANICAL_NOT_SCORABLE`` for a mechanical
            experiment that claims to score.
    """
    path = pathlib.Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG, f"cannot read {path}: {exc}")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG, f"{path}: {exc}")

    block = parsed.get("experiment")
    if not isinstance(block, dict):
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"{path}: no [experiment] table")

    required = ("id", "taskcard", "entry", "seed", "mode")
    missing = [key for key in required if key not in block]
    if missing:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"{path}: missing {missing} in [experiment]")

    mode = str(block["mode"])
    if mode not in MODES:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"{path}: mode {mode!r}, known {list(MODES)}")

    seed = block["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{path}: seed must be an integer, got {seed!r}. An experiment "
            "without a seed cannot be reproduced and therefore does not exist.")

    scored = bool(block.get("scored", False))
    if scored and mode == MODE_MECHANICAL:
        raise ExperimentError(
            MECHANICAL_NOT_SCORABLE,
            f"{path}: mechanical mode reads quarantined data inside the seal "
            "and may never produce a scorecard (ruling A)")

    rerun_class = str(block.get("rerun_class", ledger_mod.RERUN_FULL))
    if rerun_class not in ledger_mod.RERUN_CLASSES:
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{path}: rerun_class {rerun_class!r}, "
            f"known {list(ledger_mod.RERUN_CLASSES)}")
    subset = block.get("subset_sha256")
    if rerun_class == ledger_mod.RERUN_SUBSET and not subset:
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{path}: rerun_class 'deterministic-subset' requires "
            "subset_sha256, declared before results exist (ruling D5)")

    data_root = block.get("data_root")
    if mode == MODE_MECHANICAL and not data_root:
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{path}: mechanical mode must name its data_root explicitly")

    # A scoring config may not so much as mention a sealed date. Mechanical
    # configs are exempt: their whole purpose is the quarantined live week,
    # which is 2026 data, and the gate lists them rather than trusting them.
    if mode == MODE_SCORING:
        sealed = sealed_dates_in_text(raw.decode("utf-8", errors="replace"))
        if sealed:
            raise ExperimentError(
                SEAL_CONFIG_DATE,
                f"{path} names sealed date(s) {sealed}; {HOLDOUT_SEALED}")

    costs = parsed.get("experiment", {}).get("costs")
    if costs is None:
        costs = block.get("costs", {})
    if scored and not costs:
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{path}: a scored experiment must declare [experiment.costs] so "
            "every candidate is scored on the same cost model")

    return ExperimentConfig(
        path=path, raw=raw, experiment_id=str(block["id"]),
        taskcard=str(block["taskcard"]), entry=str(block["entry"]), seed=seed,
        mode=mode, scored=scored, rerun_class=rerun_class,
        subset_sha256=(str(subset) if subset else None),
        data_root=(str(data_root) if data_root else None),
        params=dict(block.get("params", {}) or {}),
        costs=dict(costs or {}))


def resolve_entry(entry: str) -> Callable[..., dict[str, Any]]:
    """Import ``module:function`` and return the callable.

    Raises:
        ExperimentError: If the spelling, module or attribute is wrong.
    """
    if ":" not in entry:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"entry {entry!r} must be 'module:function'")
    module_name, _, func_name = entry.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"cannot import {module_name!r}: {exc}")
    func = getattr(module, func_name, None)
    if func is None or not callable(func):
        raise ExperimentError(BAD_EXPERIMENT_CONFIG,
                              f"{module_name!r} has no callable {func_name!r}")
    return func


def canonical(payload: Any) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace, UTF-8 escaped.

    Sorting is what makes the hash independent of dict insertion order, which
    is the most common way an "identical" re-run produces a different hash.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=_unjsonable)


def _unjsonable(value: Any) -> Any:
    """Last-resort coercion so a stray numpy scalar cannot break the hash."""
    for attr in ("item", "isoformat"):
        method = getattr(value, attr, None)
        if callable(method):
            return method()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable; "
                    "results must be plain data")


def result_hash(body: dict[str, Any]) -> str:
    """SHA-256 of the canonical form of the hashed body."""
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def hashed_body(config: ExperimentConfig, scope: dict[str, Any],
                payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a result that must reproduce byte for byte."""
    return {
        "experiment_id": config.experiment_id,
        "taskcard": config.taskcard,
        "entry": config.entry,
        "seed": config.seed,
        "mode": config.mode,
        "scored": config.scored,
        "params": config.params,
        "costs": config.costs,
        "scope": scope,
        "payload": payload,
    }


def build_loader(config: ExperimentConfig,
                 base: pathlib.Path) -> ResearchLoader:
    """Construct the loader the config declares."""
    root = config.data_root
    if config.mode == MODE_SCORING and root is None:
        root = RESEARCH_DATA_DIR
    return ResearchLoader(config.mode, root=root, base=base)


def execute(config: ExperimentConfig, base: pathlib.Path) -> dict[str, Any]:
    """Run the entry point and return the full result document.

    Does no ledger writing and touches no files: the caller decides whether
    this is a recorded run or a gate re-run. That separation is what lets the
    gate re-execute an experiment without polluting the ledger with re-runs.

    Raises:
        ExperimentError: With ``SEAL_SCOPE_BREACH`` if a scored experiment
            turns out to have touched sealed dates or unscorable data.
    """
    func = resolve_entry(config.entry)
    loader = build_loader(config, base)
    # ``costs`` is handed over rather than left for the entry point to find,
    # because SPEC2's cost rule is one model with one set of parameters for
    # every candidate, *declared in the experiment config*. An entry point that
    # read them from anywhere else would be a second declaration, and an entry
    # point that defaulted them would be a third.
    payload = func(params=dict(config.params), seed=config.seed, loader=loader,
                   costs=dict(config.costs))
    if not isinstance(payload, dict):
        raise ExperimentError(
            BAD_EXPERIMENT_CONFIG,
            f"{config.entry} returned {type(payload).__name__}, expected a dict")

    access = loader.access
    if config.scored:
        sealed = access.sealed_dates()
        if sealed:
            raise ExperimentError(
                SEAL_SCOPE_BREACH,
                f"scored experiment {config.experiment_id} read sealed "
                f"date(s) {sealed}; {HOLDOUT_SEALED}")
        if not access.scorable:
            raise ExperimentError(
                MECHANICAL_NOT_SCORABLE,
                f"scored experiment {config.experiment_id} read from a "
                f"non-scorable root {access.root}")

    scope = {"pairs": sorted(access.pairs), "dates": sorted(access.dates),
             "timeframes": sorted(access.timeframes)}
    body = hashed_body(config, scope, payload)
    return {
        "experiment_id": config.experiment_id,
        "taskcard": config.taskcard,
        "entry": config.entry,
        "seed": config.seed,
        "mode": config.mode,
        "scored": config.scored,
        "rerun_class": config.rerun_class,
        "subset_sha256": config.subset_sha256,
        "config_sha256": config.sha256,
        "config_path": config.path.name,
        "created_at": ledger_mod.now_iso(),
        "result_hash": result_hash(body),
        "access": access.to_dict(),
        "scope": scope,
        "payload": payload,
    }


def write_result(document: dict[str, Any], path: pathlib.Path) -> pathlib.Path:
    """Write a result document as indented JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True,
                               default=_unjsonable) + "\n", encoding="utf-8")
    _LOG.info("result written to %s (hash %s)", path,
              document.get("result_hash", "")[:12])
    return path


def verdict_for(ladder: dict[str, Any]) -> str:
    """The survival verdict the pinned bar implies for a cost ladder.

    Pre-reg #1 as pinned 2026-08-19: net P&L above zero at 1.5x survives, above
    zero at 1.2x is parked, otherwise dead. Nothing else is thresholded here,
    because nothing else is pre-registered as a threshold.
    """
    def net(rung: str) -> float:
        return float((ladder.get(rung) or {}).get("net_pnl", 0.0))

    if net(SURVIVAL_BAR) > 0.0:
        return "SURVIVES"
    if net(PARK_BAR) > 0.0:
        return "PARKED"
    return "DEAD"
