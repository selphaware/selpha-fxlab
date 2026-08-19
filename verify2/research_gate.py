"""The Phase 2 research gate.

    python -E -s verify2/research_gate.py <experiment_dir>
    python -E -s verify2/research_gate.py --fast
    python -E -s verify2/research_gate.py --selftest

A binary gate cannot judge whether a research finding is *true*. It can judge
whether the finding was produced *honestly*, and that is the whole job here:
make the six ways a research result can be worthless-but-plausible mechanically
detectable, so that what reaches a human review is worth deciding on.

What it judges
--------------

1. **Regression.** The Phase 1 gate still exits 0. It is run, not reimplemented.
2. **Research unit tests.** ``tests2/`` passes. Kept out of ``tests/`` so that a
   research bug never reports itself as a Phase 1 regression.
3. **Leakage.** The walk-forward engine reproduces a hand-computed known
   answer, and three deliberately leaky implementations each produce their own
   different hand-computed answer -- so the fixture is known to discriminate.
4. **Seal.** No sealed-date Parquet under ``data/research/``; the loader refuses
   a canary request for a sealed date with ``HOLDOUT_SEALED``; no config that
   targets the research tree names a sealed date; and a scored result ran in
   scoring mode having touched no sealed date.
5. **Ledger.** Every result file has a start entry written before it, the task
   card matches, and starts outnumber results.
6. **Cost honesty.** A scorecard carries the full 1.0/1.2/1.5/2.0 ladder from
   one cost model, the arithmetic reconciles, costs are not zeroed, and the
   verdict is the one the pinned survival bar implies. Any scored experiment
   touching a non-USD-quoted pair fails until SPEC2 prerequisite P0-A lands.
7. **Reproducibility.** The experiment is re-executed from its recorded config
   and seed, and the result hash must match exactly.

Exit codes, matching the Phase 1 gate so the hook can translate them the same
way: 0 pass, 1 deliverable failure, 2 harness error, 3 environment error.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import Any, Callable, Final

GATE_DIR: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
PROJECT: Final[pathlib.Path] = GATE_DIR.parent
ARTIFACTS: Final[pathlib.Path] = GATE_DIR / "artifacts"

#: The Phase 1 gate, run as a subprocess and never reimplemented.
PHASE1_GATE: Final[pathlib.Path] = PROJECT / "verify" / "smoke_test.py"
PHASE1_DELIVERABLE: Final[str] = "fxlab"

#: Research unit tests, deliberately separate from the Phase 1 suite.
RESEARCH_TESTS: Final[pathlib.Path] = PROJECT / "tests2"

EXIT_PASS: Final[int] = 0
EXIT_DELIVERABLE: Final[int] = 1
EXIT_HARNESS: Final[int] = 2
EXIT_ENV: Final[int] = 3

#: Named reasons. The hook, the reports and any future tooling grep for these,
#: so they are stable strings and not formatted messages.
PHASE1_REGRESSION: Final[str] = "PHASE1_REGRESSION"
RESEARCH_TESTS_FAILED: Final[str] = "RESEARCH_TESTS_FAILED"
LEAKAGE_SELFCHECK_FAILED: Final[str] = "LEAKAGE_SELFCHECK_FAILED"
SEAL_LOADER_PERMISSIVE: Final[str] = "SEAL_LOADER_PERMISSIVE"
SEAL_DATA_PRESENT: Final[str] = "SEAL_DATA_PRESENT"
SEAL_CONFIG_DATE: Final[str] = "SEAL_CONFIG_DATE"
SEAL_SCOPE_BREACH: Final[str] = "SEAL_SCOPE_BREACH"
LEDGER_MALFORMED: Final[str] = "LEDGER_MALFORMED"
LEDGER_MISSING_ENTRY: Final[str] = "LEDGER_MISSING_ENTRY"
LEDGER_AFTER_RESULT: Final[str] = "LEDGER_AFTER_RESULT"
LEDGER_TASKCARD_MISMATCH: Final[str] = "LEDGER_TASKCARD_MISMATCH"
LEDGER_INCOMPLETE: Final[str] = "LEDGER_INCOMPLETE"
TASKCARD_MISSING: Final[str] = "TASKCARD_MISSING"
COST_LADDER_INCOMPLETE: Final[str] = "COST_LADDER_INCOMPLETE"
COST_LADDER_INCONSISTENT: Final[str] = "COST_LADDER_INCONSISTENT"
COST_ARITHMETIC: Final[str] = "COST_ARITHMETIC"
COST_ZEROED: Final[str] = "COST_ZEROED"
COST_MODEL_DRIFT: Final[str] = "COST_MODEL_DRIFT"
COST_CURRENCY: Final[str] = "COST_CURRENCY"
NON_USD_COST_UNFIXED: Final[str] = "NON_USD_COST_UNFIXED"
COST_FIX_MISDECLARED: Final[str] = "COST_FIX_MISDECLARED"
SURVIVAL_VERDICT_MISMATCH: Final[str] = "SURVIVAL_VERDICT_MISMATCH"
UNSEEDED: Final[str] = "UNSEEDED"
NOT_REPRODUCIBLE: Final[str] = "NOT_REPRODUCIBLE"
CONFIG_DRIFT: Final[str] = "CONFIG_DRIFT"
BAD_EXPERIMENT_DIR: Final[str] = "BAD_EXPERIMENT_DIR"

#: Money comparisons: a hundredth of a cent is far below anything meaningful.
MONEY_TOL: Final[float] = 1e-6


class HarnessError(Exception):
    """The judge itself is broken. Never the deliverable's fault."""


class EnvError(Exception):
    """The machine cannot be judged. Never the deliverable's fault."""


@dataclasses.dataclass(frozen=True, slots=True)
class Failure:
    """One named finding.

    Attributes:
        reason: A stable token from the list above.
        detail: What went wrong, in a sentence a person can act on.
    """

    reason: str
    detail: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}"


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #

def preflight() -> None:
    """Establish that the gate and the machine can do their jobs at all."""
    if sys.version_info[:2] != (3, 12):
        raise EnvError(
            f"interpreter is Python {sys.version.split()[0]}, expected 3.12.x -- "
            r"run the gate with E:\CODE\selpha-fxlab\env_fxlab\Scripts\python.exe")

    base = pathlib.Path(sys.base_prefix)
    if not base.exists():
        raise EnvError(
            f"the interpreter's base prefix {base} is unreachable -- this venv's "
            "stdlib lives on a mapped network drive; restore the mapping and "
            "retry. This is NOT a problem with research/.")

    missing = [mod for mod in ("pandas", "pyarrow", "pytest")
               if not _importable(mod)]
    if missing:
        raise HarnessError(
            f"gate dependencies missing from {sys.executable}: "
            f"{', '.join(missing)}. Install them into that interpreter; do not "
            "create a new venv.")

    if not PHASE1_GATE.exists():
        raise HarnessError(
            f"the Phase 1 gate is missing at {PHASE1_GATE}; the research gate "
            "runs it rather than reimplementing it, so it cannot judge "
            "regression without it.")

    for name in ("leak_reference", "exp_demo", "cost_known_answers"):
        if not (GATE_DIR / "fixtures" / f"{name}.py").exists():
            raise HarnessError(f"missing gate fixture {name}.py in {GATE_DIR}")

    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    if not _importable("research"):
        raise HarnessError(
            "the research package is not importable from the project root; "
            "the gate cannot exercise the loader or the walk-forward engine.")


def _importable(module: str) -> bool:
    """True if ``module`` imports cleanly."""
    try:
        __import__(module)
    except ImportError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Check 1: Phase 1 regression
# --------------------------------------------------------------------------- #

def check_phase1() -> list[Failure]:
    """Run the Phase 1 gate. It must still exit 0.

    Its exit 2 and 3 are passed through as harness and environment errors,
    because a broken Phase 1 harness is not a Phase 2 research failure and
    telling the loop otherwise costs it a dozen iterations on a phantom bug.
    """
    proc = subprocess.run(
        [sys.executable, "-E", "-s", str(PHASE1_GATE), PHASE1_DELIVERABLE],
        capture_output=True, text=True, cwd=str(PROJECT), timeout=900)
    _artifact("phase1_gate.txt", proc.stdout + proc.stderr)
    if proc.returncode == 0:
        return []
    if proc.returncode == 2:
        raise HarnessError(f"the Phase 1 gate reported a harness error:\n"
                           f"{_tail(proc.stdout + proc.stderr)}")
    if proc.returncode == 3:
        raise EnvError(f"the Phase 1 gate reported an environment error:\n"
                       f"{_tail(proc.stdout + proc.stderr)}")
    return [Failure(PHASE1_REGRESSION,
                    "the Phase 1 gate no longer passes (exit "
                    f"{proc.returncode}). Phase 1 is frozen and is now a "
                    f"regression gate; fix that before anything else.\n"
                    f"{_tail(proc.stdout + proc.stderr)}")]


# --------------------------------------------------------------------------- #
# Check 2: research unit tests
# --------------------------------------------------------------------------- #

def check_research_tests() -> list[Failure]:
    """Run ``tests2/``. Absent is a harness error, failing is a real failure."""
    if not RESEARCH_TESTS.is_dir():
        raise HarnessError(
            f"no research test suite at {RESEARCH_TESTS}; the gate expects "
            "research unit tests to live there, separate from the Phase 1 "
            "suite in tests/.")
    proc = subprocess.run(
        [sys.executable, "-E", "-s", "-m", "pytest", str(RESEARCH_TESTS), "-q",
         "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=str(PROJECT), timeout=900)
    output = proc.stdout + proc.stderr
    _artifact("research_tests.txt", output)
    if "no tests ran" in output or "collected 0 items" in output:
        return [Failure(RESEARCH_TESTS_FAILED,
                        "tests2/ collected no tests; research code in the "
                        "judged surface needs real unit tests")]
    if proc.returncode != 0:
        return [Failure(RESEARCH_TESTS_FAILED,
                        f"pytest exit {proc.returncode}:\n{_tail(output)}")]
    return []


# --------------------------------------------------------------------------- #
# Check 3: leakage known answers
# --------------------------------------------------------------------------- #

def check_leakage(engine: Callable[..., dict[str, float]] | None = None
                  ) -> list[Failure]:
    """Run the known-answer leakage fixtures.

    Args:
        engine: The implementation under test. Defaults to the honest one,
            which routes through :func:`research.walkforward.run_walk_forward`.
            The selftest passes each leaky reference here instead, to prove the
            fixture notices.

    Returns:
        Failures, empty when the engine matches the hand-computed answer, the
        splitter geometry is exactly as derived, and every leaky reference
        still produces its own different hand-computed number.
    """
    from verify2.fixtures import leak_reference as fixture

    failures: list[Failure] = []

    # Geometry first: if the splitter is wrong, the arithmetic below is
    # measuring the wrong window and every other verdict is noise.
    from research.walkforward import walk_forward_windows
    built = walk_forward_windows(**fixture.GEOMETRY)
    expected = fixture.EXPECTED_GEOMETRY
    if len(built) != len(expected):
        failures.append(Failure(
            LEAKAGE_SELFCHECK_FAILED,
            f"splitter produced {len(built)} window(s) over "
            f"{fixture.GEOMETRY['n_bars']} bars, expected {len(expected)}"))
    else:
        for window, want in zip(built, expected):
            if window.train_index != want["train_index"]:
                failures.append(Failure(
                    LEAKAGE_SELFCHECK_FAILED,
                    f"window {window.index} train indices {window.train_index} "
                    f"!= expected {want['train_index']} -- purge or embargo is "
                    "not being applied as recorded in SPEC2 pre-reg #8"))
            if window.test_index != want["test_index"]:
                failures.append(Failure(
                    LEAKAGE_SELFCHECK_FAILED,
                    f"window {window.index} test indices {window.test_index} "
                    f"!= expected {want['test_index']}"))

    windows = fixture.windows()
    if len(windows) != 1:
        failures.append(Failure(
            LEAKAGE_SELFCHECK_FAILED,
            f"the arithmetic fixture must build exactly one window, got "
            f"{len(windows)}"))
    elif (windows[0].train_index != fixture.EXPECTED_TRAIN
            or windows[0].test_index != fixture.EXPECTED_TEST):
        failures.append(Failure(
            LEAKAGE_SELFCHECK_FAILED,
            f"fixture window is train={windows[0].train_index} "
            f"test={windows[0].test_index}, expected "
            f"train={fixture.EXPECTED_TRAIN} test={fixture.EXPECTED_TEST}"))

    # The engine under test must produce the honest answer.
    under_test = engine if engine is not None else fixture.correct
    want_correct = fixture.EXPECTED["correct"]
    got = under_test(windows)
    for key, expected_value in want_correct.items():
        if abs(float(got.get(key, float("nan"))) - expected_value) > MONEY_TOL:
            failures.append(Failure(
                LEAKAGE_SELFCHECK_FAILED,
                f"walk-forward {key} = {got.get(key)!r}, hand-computed answer "
                f"is {expected_value!r}. The fixture's training mean is exactly "
                "zero, so this number is integer arithmetic; a mismatch means "
                "the window, the fit or the fill timing is wrong."))

    # And each leak must still be caught by these same numbers.
    for name, impl in fixture.IMPLEMENTATIONS.items():
        if name == "correct":
            continue
        leaked = impl(windows)
        want = fixture.EXPECTED[name]
        for key, expected_value in want.items():
            if abs(float(leaked[key]) - expected_value) > MONEY_TOL:
                failures.append(Failure(
                    LEAKAGE_SELFCHECK_FAILED,
                    f"leaky reference {name!r} produced {key}={leaked[key]!r}, "
                    f"expected {expected_value!r}; the fixture no longer "
                    "demonstrates that leak and cannot be trusted to catch it"))
        if all(abs(float(leaked[k]) - want_correct[k]) <= MONEY_TOL
               for k in want_correct):
            failures.append(Failure(
                LEAKAGE_SELFCHECK_FAILED,
                f"leaky reference {name!r} is indistinguishable from the honest "
                "result on this fixture; the check is vacuous"))

    return failures


# --------------------------------------------------------------------------- #
# Check 4: the seal
# --------------------------------------------------------------------------- #

def check_seal(base: pathlib.Path,
               canary_fn: Callable[..., tuple[bool, str]] | None = None
               ) -> tuple[list[Failure], dict[str, Any]]:
    """Loader canary, on-disk research tree, and every config that targets it.

    Args:
        base: Project root.
        canary_fn: Injected by the selftest to prove a permissive loader fails.

    Returns:
        ``(failures, detail)`` where detail is recorded in the artifact.
    """
    from research.loader import sealed_parquet_under
    from research import loader as loader_mod
    from research.seal import HOLDOUT_CUTOFF, RESEARCH_DATA_DIR

    failures: list[Failure] = []
    detail: dict[str, Any] = {"cutoff": HOLDOUT_CUTOFF.isoformat()}

    # (a) the loader must refuse, and must refuse before looking at disk.
    canary = canary_fn if canary_fn is not None else loader_mod.canary
    refused, message = canary(base=base)
    detail["canary"] = {"refused": refused, "message": message}
    if not refused:
        failures.append(Failure(
            SEAL_LOADER_PERMISSIVE,
            f"a scoring-mode loader did not refuse {HOLDOUT_CUTOFF.isoformat()} "
            f"with HOLDOUT_SEALED: {message}. The seal cannot depend on the "
            "data being absent -- absence stops protecting anything the moment "
            "somebody downloads it."))

    # (b) nothing sealed may sit under the research data root.
    research_root = base / RESEARCH_DATA_DIR
    offenders = sealed_parquet_under(research_root)
    detail["research_root"] = research_root.as_posix()
    detail["sealed_parquet"] = offenders
    if offenders:
        failures.append(Failure(
            SEAL_DATA_PRESENT,
            f"{len(offenders)} Parquet file(s) under {RESEARCH_DATA_DIR} carry "
            f"sealed dates: {offenders[:5]}. Phase 2 must not download the "
            "holdout at all; delete these and record how they arrived."))

    # (c) configs that target the research tree may not name a sealed date.
    config_failures, scanned = scan_configs(base)
    failures.extend(config_failures)
    detail["configs"] = scanned
    return failures, detail


def scan_configs(base: pathlib.Path) -> tuple[list[Failure], dict[str, Any]]:
    """Scan every TOML for sealed dates, scoped by what it targets.

    Ruling A: the seal is enforced by scope. A config whose output or data root
    is the research tree may not name a sealed date. A config targeting the
    quarantined live week necessarily names 2026 dates, so it is exempt and is
    listed instead -- exempt and invisible is how a carve-out becomes a hole.
    """
    from research.seal import RESEARCH_DATA_DIR, sealed_dates_in_file

    failures: list[Failure] = []
    judged: list[str] = []
    exempt: list[str] = []

    roots = [base / "config", base / "experiments"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.toml")):
            targets_research, why = _targets_research(path, RESEARCH_DATA_DIR)
            relative = _rel(path, base)
            if not targets_research:
                exempt.append(f"{relative} ({why})")
                continue
            judged.append(f"{relative} ({why})")
            sealed = sealed_dates_in_file(path)
            if sealed:
                failures.append(Failure(
                    SEAL_CONFIG_DATE,
                    f"{relative} targets the research tree and names sealed "
                    f"date(s) {sealed[:5]}. HOLDOUT_SEALED."))
    return failures, {"judged": judged, "exempt": exempt}


def _targets_research(path: pathlib.Path, research_dir: str) -> tuple[bool, str]:
    """Whether a config writes into or reads from the research tree."""
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return True, f"unparseable, judged conservatively: {exc}"

    ingest = parsed.get("ingest")
    if isinstance(ingest, dict):
        for key in ("out_dir", "archive_raw_dir", "raw_dir"):
            value = str(ingest.get(key, "")).replace("\\", "/")
            if value and research_dir in value:
                return True, f"ingest.{key} -> {value}"
        return False, "ingest config outside the research tree"

    experiment = parsed.get("experiment")
    if isinstance(experiment, dict):
        mode = str(experiment.get("mode", ""))
        if mode == "scoring":
            return True, "scoring experiment"
        return False, f"{mode or 'unknown'}-mode experiment"

    return False, "no ingest or experiment table"


# --------------------------------------------------------------------------- #
# The experiment under judgement
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True, slots=True)
class Experiment:
    """A completed experiment directory.

    Attributes:
        directory: Where it lives.
        config_path: The TOML it was run from.
        config: The parsed config.
        results: ``(path, document)`` for every result JSON in the directory.
    """

    directory: pathlib.Path
    config_path: pathlib.Path
    config: dict[str, Any]
    results: tuple[tuple[pathlib.Path, dict[str, Any]], ...]


def load_experiment(directory: pathlib.Path) -> Experiment:
    """Read an experiment directory.

    Raises:
        HarnessError: Never -- a malformed experiment is a deliverable failure,
            reported through :class:`ExperimentLoadError`.
    """
    directory = pathlib.Path(directory).resolve()
    if not directory.is_dir():
        raise ExperimentLoadError(
            f"{directory} is not a directory. Point the gate at one experiment "
            "directory containing config.toml and its result JSON.")
    config_path = directory / "config.toml"
    if not config_path.exists():
        raise ExperimentLoadError(f"no config.toml in {directory}")
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ExperimentLoadError(f"{config_path}: {exc}")

    results: list[tuple[pathlib.Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExperimentLoadError(f"{path}: {exc}")
        if not isinstance(document, dict):
            raise ExperimentLoadError(f"{path}: result must be a JSON object")
        results.append((path, document))
    if not results:
        raise ExperimentLoadError(
            f"no result JSON in {directory}; an experiment directory with no "
            "result is not a completed experiment")
    return Experiment(directory=directory, config_path=config_path,
                      config=config, results=tuple(results))


class ExperimentLoadError(Exception):
    """The experiment directory is not shaped like one."""


# --------------------------------------------------------------------------- #
# Check 5: ledger integrity
# --------------------------------------------------------------------------- #

def check_ledger(base: pathlib.Path,
                 experiment: Experiment | None) -> list[Failure]:
    """Every result must have a start entry written before it existed."""
    from research import ledger as ledger_mod

    failures: list[Failure] = []
    records = ledger_mod.read(base)
    malformed = [r for r in records if r.get("record") == "malformed"]
    if malformed:
        failures.append(Failure(
            LEDGER_MALFORMED,
            f"{len(malformed)} unreadable ledger line(s), first at line "
            f"{malformed[0].get('line')}: {malformed[0].get('error')}"))

    starts = [r for r in records if r.get("record") == ledger_mod.RECORD_START]
    all_results = sorted((base / "experiments").rglob("*.json"))
    if len(starts) < len(all_results):
        failures.append(Failure(
            LEDGER_INCOMPLETE,
            f"{len(starts)} ledger start entr(ies) for {len(all_results)} "
            "result file(s). Every run writes its entry before it runs, so "
            "results cannot outnumber entries unless entries were removed."))

    if experiment is None:
        return failures

    for path, document in experiment.results:
        experiment_id = str(document.get("experiment_id", ""))
        entries = ledger_mod.starts_for(records, experiment_id)
        if not entries:
            failures.append(Failure(
                LEDGER_MISSING_ENTRY,
                f"{_rel(path, base)} reports experiment_id "
                f"{experiment_id!r} with no ledger start entry. A result "
                "without a ledger entry is an untracked trial, which is how "
                "multiple-testing honesty dies quietly."))
            continue

        created = str(document.get("created_at", ""))
        earliest = min(str(e.get("started_at", "")) for e in entries)
        if created and earliest and earliest > created:
            failures.append(Failure(
                LEDGER_AFTER_RESULT,
                f"{_rel(path, base)} was created at {created} but the earliest "
                f"ledger entry for it is {earliest}. The entry must be written "
                "before the experiment runs, not after its result is seen."))

        card = str(document.get("taskcard", ""))
        mismatched = [e for e in entries if str(e.get("taskcard", "")) != card]
        if mismatched:
            failures.append(Failure(
                LEDGER_TASKCARD_MISMATCH,
                f"{_rel(path, base)} declares task card {card!r} but its "
                f"ledger entry declares "
                f"{mismatched[0].get('taskcard')!r}. The ledger is how a "
                "review checks the loop stayed inside its card."))
        if card and not (base / "taskcards" / f"{card}.md").exists():
            failures.append(Failure(
                TASKCARD_MISSING,
                f"task card {card!r} has no file at taskcards/{card}.md; "
                "cards are committed before the loop starts."))

    return failures


# --------------------------------------------------------------------------- #
# Check 6: cost honesty
# --------------------------------------------------------------------------- #

def check_costs(experiment: Experiment) -> list[Failure]:
    """Judge every scorecard in the experiment, and the pairs it scored."""
    from research.experiment import LADDER, PARK_BAR, SURVIVAL_BAR, verdict_for
    from verify2.fixtures import cost_known_answers as known

    failures: list[Failure] = []
    fix_landed = known.fix_landed()

    if fix_landed:
        problems = known.check()
        if problems:
            failures.append(Failure(
                COST_FIX_MISDECLARED,
                "fxlab.costs declares USD_ACCOUNTING but the known answers "
                f"disagree: {problems[:3]}"))

    for path, document in experiment.results:
        if not document.get("scored"):
            continue
        payload = document.get("payload") or {}
        card = payload.get("scorecard")
        if not isinstance(card, dict):
            failures.append(Failure(
                COST_LADDER_INCOMPLETE,
                f"{path.name} is marked scored but carries no scorecard. A "
                "scored result without the cost ladder is not judgeable."))
            continue

        pairs = [str(p) for p in card.get("pairs", [])]
        non_usd = [p for p in pairs if p[3:] != "USD"]
        if non_usd and not fix_landed:
            failures.append(Failure(
                NON_USD_COST_UNFIXED,
                f"{path.name} scores non-USD-quoted pair(s) {non_usd} while "
                "SPEC2 prerequisite P0-A is unfixed: commission is floored "
                "against a quote-currency notional and cross-pair P&L is summed "
                "without conversion. Land P0-A (and set "
                "fxlab.costs.USD_ACCOUNTING) before scoring these."))

        currency = str(card.get("accounting_currency", ""))
        if currency != "USD":
            failures.append(Failure(
                COST_CURRENCY,
                f"{path.name} reports accounting_currency {currency!r}; the "
                "pinned survival bar is denominated in USD."))

        ladder = card.get("ladder")
        if not isinstance(ladder, dict):
            failures.append(Failure(COST_LADDER_INCOMPLETE,
                                    f"{path.name} scorecard has no ladder"))
            continue
        missing = [rung for rung in LADDER if rung not in ladder]
        if missing:
            failures.append(Failure(
                COST_LADDER_INCOMPLETE,
                f"{path.name} is missing ladder rung(s) {missing}; pre-reg #1 "
                f"requires all of {list(LADDER)} on every scorecard."))

        base_model = card.get("cost_model") or {}
        totals: dict[str, float] = {}
        for rung in LADDER:
            entry = ladder.get(rung)
            if not isinstance(entry, dict):
                continue
            failures.extend(_check_rung(path.name, rung, entry, base_model))
            totals[rung] = float(entry.get("total_costs", 0.0))

        ordered = [totals[r] for r in LADDER if r in totals]
        if len(ordered) > 1 and any(b < a - MONEY_TOL
                                    for a, b in zip(ordered, ordered[1:])):
            failures.append(Failure(
                COST_LADDER_INCONSISTENT,
                f"{path.name} total costs do not increase with the multiplier: "
                f"{ {r: totals[r] for r in LADDER if r in totals} }. A higher "
                "cost multiplier cannot cost less."))

        survival = card.get("survival") or {}
        stated = str(survival.get("verdict", ""))
        implied = verdict_for(ladder)
        if stated and stated != implied:
            failures.append(Failure(
                SURVIVAL_VERDICT_MISMATCH,
                f"{path.name} states verdict {stated!r} but the ladder implies "
                f"{implied!r} under the pinned bar (net > 0 at {SURVIVAL_BAR}x "
                f"survives, at {PARK_BAR}x parks, otherwise dead)."))

    return failures


def _check_rung(name: str, rung: str, entry: dict[str, Any],
                base_model: dict[str, Any]) -> list[Failure]:
    """Arithmetic and cost-model checks for one ladder rung."""
    failures: list[Failure] = []
    gross = float(entry.get("gross_pnl", 0.0))
    spread = float(entry.get("spread_cost", 0.0))
    commission = float(entry.get("commission", 0.0))
    total = float(entry.get("total_costs", 0.0))
    net = float(entry.get("net_pnl", 0.0))
    trades = int(entry.get("trade_count", 0))

    if abs((spread + commission) - total) > MONEY_TOL:
        failures.append(Failure(
            COST_ARITHMETIC,
            f"{name} rung {rung}: spread {spread} + commission {commission} "
            f"!= total_costs {total}"))
    if abs((gross - total) - net) > MONEY_TOL:
        failures.append(Failure(
            COST_ARITHMETIC,
            f"{name} rung {rung}: gross {gross} - total_costs {total} != "
            f"net_pnl {net}. Gross is measured mid to mid and the spread is a "
            "separate line precisely so this identity holds exactly."))
    if trades > 0 and total <= MONEY_TOL:
        failures.append(Failure(
            COST_ZEROED,
            f"{name} rung {rung}: {trades} trade(s) at zero total cost. A "
            "backtest that reports gross == net has had its costs switched "
            "off."))
    if trades > 0 and abs(gross - net) <= MONEY_TOL:
        failures.append(Failure(
            COST_ZEROED,
            f"{name} rung {rung}: gross equals net across {trades} trade(s)."))

    own_model = entry.get("cost_model")
    if own_model is not None and own_model != base_model:
        failures.append(Failure(
            COST_MODEL_DRIFT,
            f"{name} rung {rung} carries its own cost model {own_model}, "
            f"differing from the scorecard's {base_model}. Every rung and "
            "every candidate is priced on one model, or the ladder compares "
            "nothing."))
    return failures


# --------------------------------------------------------------------------- #
# Check 7: seal scope of this experiment
# --------------------------------------------------------------------------- #

def check_scope(experiment: Experiment) -> list[Failure]:
    """A scored result must have run in scoring mode over unsealed dates."""
    from research.seal import is_sealed

    failures: list[Failure] = []
    for path, document in experiment.results:
        access = document.get("access") or {}
        dates = [str(d) for d in (document.get("scope") or {}).get("dates", [])]
        sealed = [d for d in dates if is_sealed(d)]
        scored = bool(document.get("scored"))
        mode = str(document.get("mode", ""))

        if scored and mode != "scoring":
            failures.append(Failure(
                SEAL_SCOPE_BREACH,
                f"{path.name} is scored but ran in {mode!r} mode. Mechanical "
                "mode reads quarantined data inside the seal and may never "
                "produce a scorecard (ruling A)."))
        if scored and not access.get("scorable", False):
            failures.append(Failure(
                SEAL_SCOPE_BREACH,
                f"{path.name} is scored but its loader reported the data as "
                f"not scorable (root {access.get('root')!r})."))
        if scored and sealed:
            failures.append(Failure(
                SEAL_SCOPE_BREACH,
                f"{path.name} is scored and touched sealed date(s) {sealed[:5]}. "
                "HOLDOUT_SEALED."))
        if not scored and sealed and mode != "mechanical":
            failures.append(Failure(
                SEAL_SCOPE_BREACH,
                f"{path.name} touched sealed date(s) {sealed[:5]} in {mode!r} "
                "mode; only mechanical mode may read the quarantine."))
    return failures


# --------------------------------------------------------------------------- #
# Check 8: reproducibility
# --------------------------------------------------------------------------- #

def check_reproducibility(base: pathlib.Path,
                          experiment: Experiment) -> list[Failure]:
    """Re-execute from the recorded config and seed; hashes must match."""
    failures: list[Failure] = []

    block = experiment.config.get("experiment") or {}
    seed = block.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        return [Failure(UNSEEDED,
                        f"{experiment.config_path.name} declares seed {seed!r}; "
                        "an experiment without an integer seed cannot be "
                        "reproduced and therefore does not exist.")]

    on_disk = hashlib.sha256(experiment.config_path.read_bytes()).hexdigest()
    for path, document in experiment.results:
        recorded = str(document.get("config_sha256", ""))
        if recorded and recorded != on_disk:
            failures.append(Failure(
                CONFIG_DRIFT,
                f"{path.name} was produced from config {recorded[:12]} but "
                f"config.toml now hashes to {on_disk[:12]}. Re-running would "
                "verify a different experiment from the one reported."))

    proc = subprocess.run(
        [sys.executable, "-E", "-s", "-m", "research.run",
         "--config", str(experiment.config_path), "--reproduce",
         "--base", str(base)],
        capture_output=True, text=True, cwd=str(PROJECT), timeout=1800)
    output = proc.stdout + proc.stderr
    _artifact("reproduce.txt", output)
    if proc.returncode != 0:
        return failures + [Failure(
            NOT_REPRODUCIBLE,
            f"re-running the experiment exited {proc.returncode}:\n"
            f"{_tail(output)}")]

    rerun_hash = _parse_hash(proc.stdout)
    if rerun_hash is None:
        raise HarnessError(
            "the re-run printed no RESULT_HASH line; the gate cannot compare "
            f"hashes.\n{_tail(output)}")

    for path, document in experiment.results:
        recorded = str(document.get("result_hash", ""))
        if recorded != rerun_hash:
            failures.append(Failure(
                NOT_REPRODUCIBLE,
                f"{path.name} reports result hash {recorded[:16]} but a re-run "
                f"from the same config and seed produced {rerun_hash[:16]}. A "
                "number that a re-run does not reproduce exactly does not "
                "exist; look for unseeded randomness, order-dependent "
                "operations or wall-clock dependence."))
    return failures


def _parse_hash(stdout: str) -> str | None:
    """Pull the ``RESULT_HASH`` line out of a re-run's stdout."""
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT_HASH "):
            return line.split(" ", 1)[1].strip()
    return None


# --------------------------------------------------------------------------- #
# Driving
# --------------------------------------------------------------------------- #

def run_gate(experiment_dir: pathlib.Path | None, base: pathlib.Path,
             fast: bool) -> tuple[list[Failure], dict[str, Any]]:
    """Run the checks and return failures plus a summary for the artifact."""
    failures: list[Failure] = []
    summary: dict[str, Any] = {"mode": "fast" if fast else "full",
                               "base": base.as_posix()}

    print("  [1] Phase 1 regression gate")
    found = check_phase1()
    failures.extend(found)
    print(f"      {'FAIL' if found else 'phase 1 gate exit 0'}")

    if not fast:
        print("  [2] research unit tests")
        found = check_research_tests()
        failures.extend(found)
        print(f"      {'FAIL' if found else 'tests2/ passed'}")

    print("  [3] walk-forward leakage known answers")
    found = check_leakage()
    failures.extend(found)
    print(f"      {'FAIL' if found else 'engine matches -11; three leaks give +17 / -15 / -13'}")

    print("  [4] holdout seal")
    seal_failures, seal_detail = check_seal(base)
    failures.extend(seal_failures)
    summary["seal"] = seal_detail
    print(f"      {'FAIL' if seal_failures else 'loader refuses sealed dates; research tree clean'}")

    experiment: Experiment | None = None
    if experiment_dir is not None:
        try:
            experiment = load_experiment(experiment_dir)
        except ExperimentLoadError as exc:
            failures.append(Failure(BAD_EXPERIMENT_DIR, str(exc)))

    print("  [5] ledger integrity")
    found = check_ledger(base, experiment)
    failures.extend(found)
    print(f"      {'FAIL' if found else 'every result has an entry written before it'}")

    if experiment is not None and not fast:
        print("  [6] cost honesty")
        found = check_costs(experiment)
        failures.extend(found)
        print(f"      {'FAIL' if found else 'ladder, arithmetic and verdict consistent'}")

        print("  [7] experiment seal scope")
        found = check_scope(experiment)
        failures.extend(found)
        print(f"      {'FAIL' if found else 'scope consistent with the declared mode'}")

        print("  [8] reproducibility")
        found = check_reproducibility(base, experiment)
        failures.extend(found)
        print(f"      {'FAIL' if found else 'result hash reproduced exactly'}")

    summary["failures"] = [{"reason": f.reason, "detail": f.detail}
                           for f in failures]
    if experiment is not None:
        summary["experiment"] = {
            "directory": _rel(experiment.directory, base),
            "results": [_rel(p, base) for p, _ in experiment.results]}
    return failures, summary


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the gate and translate the verdict."""
    parser = argparse.ArgumentParser(
        prog="research_gate.py",
        description="Judge whether a research experiment was produced honestly.")
    parser.add_argument("experiment", nargs="?", type=pathlib.Path, default=None,
                        help="the completed experiment directory to judge")
    parser.add_argument("--fast", action="store_true",
                        help="hook subset: Phase 1 gate, leakage, seal, ledger")
    parser.add_argument("--selftest", action="store_true",
                        help="prove the gate by breaking it, one failure mode "
                             "at a time")
    parser.add_argument("--base", type=pathlib.Path, default=PROJECT,
                        help="project root; defaults to the repository")
    args = parser.parse_args(argv)

    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    try:
        preflight()
        if args.selftest:
            from verify2 import _selftest
            return _selftest.main()

        base = pathlib.Path(args.base).resolve()
        print(f"RESEARCH GATE  base       : {base}")
        print(f"               experiment : {args.experiment or '(none)'}")
        print(f"               mode       : {'fast' if args.fast else 'full'}")
        failures, summary = run_gate(args.experiment, base, args.fast)
        (ARTIFACTS / "research_gate.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8")

        if failures:
            print("\nRESEARCH GATE FAIL", file=sys.stderr)
            for failure in failures:
                print(failure.reason, file=sys.stderr)
                print(f"    {failure.detail}", file=sys.stderr)
            return EXIT_DELIVERABLE
        print("\nRESEARCH GATE PASS")
        return EXIT_PASS

    except HarnessError as exc:
        print(f"\nHARNESS ERROR -- the judge is broken, not the research.\n{exc}",
              file=sys.stderr)
        return EXIT_HARNESS
    except EnvError as exc:
        print(f"\nENVIRONMENT ERROR -- the machine is not judgable.\n{exc}",
              file=sys.stderr)
        return EXIT_ENV
    except subprocess.TimeoutExpired as exc:
        print(f"\nHARNESS ERROR -- a gate subprocess timed out: {exc}",
              file=sys.stderr)
        return EXIT_HARNESS


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

def _artifact(name: str, text: str) -> None:
    """Write a gate artifact, best effort."""
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / name).write_text(text, encoding="utf-8")
    except OSError:
        pass


def _tail(text: str, lines: int = 30) -> str:
    """The last ``lines`` lines of some output."""
    kept = text.strip().splitlines()[-lines:]
    return "\n".join(kept)


def _rel(path: pathlib.Path, base: pathlib.Path) -> str:
    """Project-relative POSIX path where possible."""
    try:
        return pathlib.Path(path).resolve().relative_to(base).as_posix()
    except ValueError:
        return pathlib.Path(path).resolve().as_posix()


if __name__ == "__main__":
    sys.exit(main())
