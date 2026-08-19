"""Prove the research gate by breaking it, one failure mode at a time.

    python -E -s verify2/research_gate.py --selftest

A gate nobody has watched fail is not a gate. This builds a throwaway project
root, runs a good experiment through the real runner, then constructs one
deliberately broken variant per failure mode in BOOTSTRAP2 and asserts the gate
names the right reason for each:

============================  ==================================
broken variant                expected reason
============================  ==================================
leaky walk-forward            LEAKAGE_SELFCHECK_FAILED
sealed date in a config       SEAL_CONFIG_DATE
sealed Parquet on disk        SEAL_DATA_PRESENT
permissive loader             SEAL_LOADER_PERMISSIVE
scored result on sealed data  SEAL_SCOPE_BREACH
unseeded experiment           NOT_REPRODUCIBLE
non-integer seed              UNSEEDED
result with no ledger entry   LEDGER_MISSING_ENTRY
ledger written after result   LEDGER_AFTER_RESULT
wrong task card               LEDGER_TASKCARD_MISMATCH
zero-cost scorecard           COST_ZEROED
missing ladder rung           COST_LADDER_INCOMPLETE
per-rung cost model drift     COST_MODEL_DRIFT
unsupported verdict           SURVIVAL_VERDICT_MISMATCH
JPY scored before P0-A        NON_USD_COST_UNFIXED
Phase 1 gate exits 1 / 2 / 3  PHASE1_REGRESSION / harness / env
============================  ==================================

and finally that a good experiment passes the whole gate, Phase 1 regression
run included.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable

from verify2 import research_gate as gate

REPORT: list[tuple[str, str, bool, str]] = []


# --------------------------------------------------------------------------- #
# Building throwaway experiments
# --------------------------------------------------------------------------- #

def _toml_value(value: Any) -> str:
    """Render a Python value as TOML. Only the shapes the selftest uses."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"selftest cannot render {type(value).__name__} as TOML")


def write_config(base: pathlib.Path, experiment_id: str, *, taskcard: str = "TX",
                 seed: Any = 12345, mode: str = "scoring", scored: bool = False,
                 params: dict[str, Any] | None = None,
                 costs: dict[str, Any] | None = None,
                 data_root: str | None = None,
                 entry: str = "verify2.fixtures.exp_demo:run",
                 extra_comment: str = "") -> pathlib.Path:
    """Write an experiment config and return its path."""
    directory = base / "experiments" / experiment_id
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["# selftest fixture experiment"]
    if extra_comment:
        lines.append(f"# {extra_comment}")
    lines += ["", "[experiment]",
              f"id = {_toml_value(experiment_id)}",
              f"taskcard = {_toml_value(taskcard)}",
              f"entry = {_toml_value(entry)}",
              f"seed = {_toml_value(seed)}",
              f"mode = {_toml_value(mode)}",
              f"scored = {_toml_value(scored)}",
              'rerun_class = "full"']
    if data_root:
        lines.append(f"data_root = {_toml_value(data_root)}")
    lines += ["", "[experiment.params]"]
    for key, value in (params or {}).items():
        lines.append(f"{key} = {_toml_value(value)}")
    if costs:
        lines += ["", "[experiment.costs]"]
        for key, value in costs.items():
            lines.append(f"{key} = {_toml_value(value)}")
    path = directory / "config.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_taskcard(base: pathlib.Path, name: str) -> None:
    """Write a placeholder task card so the ledger check finds one."""
    cards = base / "taskcards"
    cards.mkdir(parents=True, exist_ok=True)
    (cards / f"{name}.md").write_text(
        f"# {name} - selftest placeholder\n\nScope: gate selftest only.\n",
        encoding="utf-8")


def run_experiment(base: pathlib.Path, config: pathlib.Path) -> int:
    """Run an experiment through the real runner, in the temp base."""
    proc = subprocess.run(
        [sys.executable, "-E", "-s", "-m", "research.run",
         "--config", str(config), "--base", str(base)],
        capture_output=True, text=True, cwd=str(gate.PROJECT), timeout=600)
    if proc.returncode != 0:
        print(f"      runner said: {proc.stdout.strip()} {proc.stderr.strip()}")
    return proc.returncode


def load_experiment(base: pathlib.Path, experiment_id: str) -> gate.Experiment:
    """Load a built experiment directory."""
    return gate.load_experiment(base / "experiments" / experiment_id)


def mutate_result(base: pathlib.Path, experiment_id: str,
                  mutate: Callable[[dict[str, Any]], None]) -> None:
    """Rewrite an experiment's result document in place."""
    path = base / "experiments" / experiment_id / "result.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, indent=2, sort_keys=True),
                    encoding="utf-8")


def clone_experiment(base: pathlib.Path, source: str, target: str
                     ) -> pathlib.Path:
    """Copy an experiment directory, rewriting its id, and return the copy."""
    src = base / "experiments" / source
    dst = base / "experiments" / target
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for path in dst.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        document["experiment_id"] = target
        path.write_text(json.dumps(document, indent=2, sort_keys=True),
                        encoding="utf-8")
    config = dst / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(f'"{source}"', f'"{target}"'),
        encoding="utf-8")
    return dst


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #

def expect(label: str, reason: str, failures: list[gate.Failure]) -> bool:
    """Record whether ``reason`` is among ``failures``."""
    reasons = [f.reason for f in failures]
    ok = reason in reasons
    detail = (next(f.detail for f in failures if f.reason == reason)
              if ok else f"got {reasons or 'no failures'}")
    REPORT.append((label, reason, ok, detail))
    print(f"    {'ok  ' if ok else 'FAIL'} {label} -> {reason}"
          f"{'' if ok else f' (got {reasons or []})'}")
    return ok


def expect_clean(label: str, failures: list[gate.Failure]) -> bool:
    """Record whether a check passed with no findings at all."""
    ok = not failures
    REPORT.append((label, "(no failures)", ok,
                   "" if ok else "; ".join(str(f) for f in failures)))
    print(f"    {'ok  ' if ok else 'FAIL'} {label} -> clean"
          f"{'' if ok else f' (got {[f.reason for f in failures]})'}")
    return ok


# --------------------------------------------------------------------------- #
# The cases
# --------------------------------------------------------------------------- #

def case_leakage() -> None:
    """Each leaky implementation must fail the leakage check."""
    from verify2.fixtures import leak_reference as fixture

    expect_clean("honest walk-forward engine", gate.check_leakage())
    for name in ("peek", "unpurged", "full_sample"):
        expect(f"leaky walk-forward ({name})", gate.LEAKAGE_SELFCHECK_FAILED,
               gate.check_leakage(engine=fixture.IMPLEMENTATIONS[name]))


def case_seal(base: pathlib.Path) -> None:
    """Seal checks: canary, on-disk data, and config scanning."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    failures, _ = gate.check_seal(base)
    expect_clean("seal on a clean tree", failures)

    def permissive(base: pathlib.Path | None = None) -> tuple[bool, str]:
        return False, "served 2025-03-01 without refusing"

    failures, _ = gate.check_seal(base, canary_fn=permissive)
    expect("permissive loader", gate.SEAL_LOADER_PERMISSIVE, failures)

    # A sealed-date Parquet under the research tree.
    sealed_dir = (base / "data" / "research" / "ticks" / "pair=EURUSD"
                  / "date=2025-06-02")
    sealed_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"pair": ["EURUSD"]}), sealed_dir / "x.parquet")
    failures, _ = gate.check_seal(base)
    expect("sealed Parquet under data/research", gate.SEAL_DATA_PRESENT, failures)
    shutil.rmtree(base / "data")

    # A scoring config that names a sealed date.
    write_config(base, "SEAL-config", taskcard="TX",
                 extra_comment="research window 2025-03-05 to 2025-04-01")
    failures, _ = gate.scan_configs(base)
    expect("sealed date in a scoring config", gate.SEAL_CONFIG_DATE, failures)
    shutil.rmtree(base / "experiments" / "SEAL-config")


def case_scope(base: pathlib.Path, good_scored: str) -> None:
    """A scored result that touched sealed dates must fail."""
    expect_clean("scope of a good scored result",
                 gate.check_scope(load_experiment(base, good_scored)))

    clone_experiment(base, good_scored, "SCOPE-sealed")
    mutate_result(base, "SCOPE-sealed",
                  lambda d: d.__setitem__("scope", {"pairs": ["EURUSD"],
                                                    "dates": ["2025-06-01"],
                                                    "timeframes": []}))
    expect("scored result over a sealed date", gate.SEAL_SCOPE_BREACH,
           gate.check_scope(load_experiment(base, "SCOPE-sealed")))

    shutil.rmtree(base / "experiments" / "SCOPE-sealed")

    clone_experiment(base, good_scored, "SCOPE-mechanical")
    mutate_result(base, "SCOPE-mechanical",
                  lambda d: d.__setitem__("mode", "mechanical"))
    expect("scored result from mechanical mode", gate.SEAL_SCOPE_BREACH,
           gate.check_scope(load_experiment(base, "SCOPE-mechanical")))
    shutil.rmtree(base / "experiments" / "SCOPE-mechanical")


def case_ledger(base: pathlib.Path, good: str) -> None:
    """Ledger integrity: missing, late and mismatched entries."""
    expect_clean("ledger for a good experiment",
                 gate.check_ledger(base, load_experiment(base, good)))

    clone_experiment(base, good, "LEDGER-orphan")
    expect("result with no ledger entry", gate.LEDGER_MISSING_ENTRY,
           gate.check_ledger(base, load_experiment(base, "LEDGER-orphan")))
    shutil.rmtree(base / "experiments" / "LEDGER-orphan")

    clone_experiment(base, good, "LEDGER-late")
    mutate_result(base, "LEDGER-late",
                  lambda d: d.__setitem__("experiment_id", good))
    mutate_result(base, "LEDGER-late",
                  lambda d: d.__setitem__("created_at", "2000-01-01T00:00:00+00:00"))
    expect("ledger entry written after the result", gate.LEDGER_AFTER_RESULT,
           gate.check_ledger(base, load_experiment(base, "LEDGER-late")))
    shutil.rmtree(base / "experiments" / "LEDGER-late")

    clone_experiment(base, good, "LEDGER-card")
    mutate_result(base, "LEDGER-card",
                  lambda d: d.__setitem__("experiment_id", good))
    mutate_result(base, "LEDGER-card",
                  lambda d: d.__setitem__("taskcard", "T99"))
    expect("result claiming the wrong task card",
           gate.LEDGER_TASKCARD_MISMATCH,
           gate.check_ledger(base, load_experiment(base, "LEDGER-card")))
    shutil.rmtree(base / "experiments" / "LEDGER-card")


def case_costs(base: pathlib.Path, good_scored: str) -> None:
    """Cost honesty: zeroed, incomplete, drifting, misjudged and non-USD."""
    expect_clean("costs on a good scorecard",
                 gate.check_costs(load_experiment(base, good_scored)))

    def broken(name: str, mutate: Callable[[dict[str, Any]], None],
               reason: str, label: str) -> None:
        clone_experiment(base, good_scored, name)
        mutate_result(base, name, mutate)
        expect(label, reason, gate.check_costs(load_experiment(base, name)))
        shutil.rmtree(base / "experiments" / name)

    def zero_costs(document: dict[str, Any]) -> None:
        ladder = document["payload"]["scorecard"]["ladder"]
        gross = float(ladder["1.0"]["gross_pnl"])
        for rung in ladder:
            ladder[rung].update({"spread_cost": 0.0, "commission": 0.0,
                                 "total_costs": 0.0, "net_pnl": gross})

    def drop_rung(document: dict[str, Any]) -> None:
        document["payload"]["scorecard"]["ladder"].pop("1.5", None)

    def drift(document: dict[str, Any]) -> None:
        ladder = document["payload"]["scorecard"]["ladder"]
        ladder["2.0"]["cost_model"] = {"commission_rate": 4e-05,
                                       "commission_min": 5.0}

    def wrong_verdict(document: dict[str, Any]) -> None:
        document["payload"]["scorecard"]["survival"]["verdict"] = "SURVIVES"
        ladder = document["payload"]["scorecard"]["ladder"]
        for rung in ladder:
            ladder[rung]["net_pnl"] = -abs(ladder[rung]["net_pnl"]) - 1.0
            ladder[rung]["gross_pnl"] = (ladder[rung]["net_pnl"]
                                         + ladder[rung]["total_costs"])

    def jpy(document: dict[str, Any]) -> None:
        document["payload"]["scorecard"]["pairs"] = ["USDJPY"]

    broken("COST-zero", zero_costs, gate.COST_ZEROED, "zero-cost scorecard")
    broken("COST-rung", drop_rung, gate.COST_LADDER_INCOMPLETE,
           "cost ladder missing 1.5x")
    broken("COST-drift", drift, gate.COST_MODEL_DRIFT,
           "per-rung cost-model drift")
    broken("COST-verdict", wrong_verdict, gate.SURVIVAL_VERDICT_MISMATCH,
           "verdict the ladder does not support")
    broken("COST-jpy", jpy, gate.NON_USD_COST_UNFIXED,
           "JPY-quoted pair scored before P0-A")


def case_reproducibility(base: pathlib.Path, good: str) -> None:
    """Reproducibility: a good run reproduces, an unseeded one does not."""
    expect_clean("reproducibility of a good experiment",
                 gate.check_reproducibility(base, load_experiment(base, good)))

    config = write_config(base, "REPRO-unseeded", taskcard="TX", seed=777,
                          params={"n": 4, "ignore_seed": True})
    if run_experiment(base, config) != 0:
        REPORT.append(("unseeded experiment", "setup", False,
                       "the runner refused to build the fixture"))
    else:
        expect("unseeded experiment", gate.NOT_REPRODUCIBLE,
               gate.check_reproducibility(
                   base, load_experiment(base, "REPRO-unseeded")))

    clone_experiment(base, good, "REPRO-noseed")
    config = base / "experiments" / "REPRO-noseed" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("seed = 12345",
                                                   'seed = "not-a-seed"'),
        encoding="utf-8")
    expect("non-integer seed", gate.UNSEEDED,
           gate.check_reproducibility(base,
                                      load_experiment(base, "REPRO-noseed")))
    shutil.rmtree(base / "experiments" / "REPRO-noseed")


def case_phase1(tmp: pathlib.Path) -> None:
    """The Phase 1 exit codes must translate to the right verdicts.

    ``verify/`` is frozen and deny-edited, so the regression check is exercised
    against a stand-in gate that exits 1, 2 and 3 on demand. What is being
    proven is the translation -- a failing Phase 1 gate is a research failure, a
    broken or unjudgable one is not.
    """
    real = gate.PHASE1_GATE
    stub = tmp / "fake_phase1.py"
    try:
        for code, label in ((1, "Phase 1 gate exits 1"),
                            (2, "Phase 1 gate exits 2"),
                            (3, "Phase 1 gate exits 3")):
            stub.write_text(
                "import sys\nprint('stand-in Phase 1 gate')\n"
                f"sys.exit({code})\n", encoding="utf-8")
            gate.PHASE1_GATE = stub
            if code == 1:
                expect(label, gate.PHASE1_REGRESSION, gate.check_phase1())
                continue
            expected = gate.HarnessError if code == 2 else gate.EnvError
            try:
                gate.check_phase1()
            except expected:
                REPORT.append((label, expected.__name__, True, ""))
                print(f"    ok   {label} -> {expected.__name__}")
            except Exception as exc:  # noqa: BLE001
                REPORT.append((label, expected.__name__, False, repr(exc)))
                print(f"    FAIL {label} -> {type(exc).__name__}")
            else:
                REPORT.append((label, expected.__name__, False, "no exception"))
                print(f"    FAIL {label} -> no exception raised")
    finally:
        gate.PHASE1_GATE = real


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def main() -> int:
    """Build the fixtures, run every case and report."""
    print("RESEARCH GATE SELFTEST")
    with tempfile.TemporaryDirectory(prefix="fxlab_gate_selftest_") as tmp:
        base = pathlib.Path(tmp).resolve()
        (base / "experiments").mkdir(parents=True, exist_ok=True)
        write_taskcard(base, "TX")

        print("  building reference experiments")
        good = "GOOD-unscored"
        config = write_config(base, good, taskcard="TX", seed=12345,
                              params={"n": 5})
        if run_experiment(base, config) != 0:
            print("HARNESS: the good unscored experiment failed to run",
                  file=sys.stderr)
            return gate.EXIT_HARNESS

        good_scored = "GOOD-scored"
        config = write_config(
            base, good_scored, taskcard="TX", seed=24680, scored=True,
            params={"n": 5, "scorecard": True, "pairs": ["EURUSD"],
                    "gross_pnl": 1000.0, "spread_cost": 200.0,
                    "commission": 120.0, "trade_count": 12},
            costs={"commission_rate": 2e-05, "commission_min": 2.0})
        if run_experiment(base, config) != 0:
            print("HARNESS: the good scored experiment failed to run",
                  file=sys.stderr)
            return gate.EXIT_HARNESS

        print("  leakage")
        case_leakage()
        print("  seal")
        case_seal(base)
        print("  experiment scope")
        case_scope(base, good_scored)
        print("  ledger")
        case_ledger(base, good)
        print("  cost honesty")
        case_costs(base, good_scored)
        print("  reproducibility")
        case_reproducibility(base, good)
        print("  Phase 1 exit-code translation")
        case_phase1(base)

        print("  full gate on the good scored experiment "
              "(runs the real Phase 1 gate)")
        failures, _ = gate.run_gate(base / "experiments" / good_scored,
                                    base, fast=False)
        expect_clean("good experiment passes the whole gate", failures)

    passed = sum(1 for _, _, ok, _ in REPORT if ok)
    total = len(REPORT)
    print(f"\n{passed}/{total} selftest assertions held")
    if passed != total:
        print("\nSELFTEST FAIL", file=sys.stderr)
        for label, reason, ok, detail in REPORT:
            if not ok:
                print(f"  {label}: expected {reason}; {detail}", file=sys.stderr)
        return gate.EXIT_HARNESS
    print("SELFTEST PASS")
    return gate.EXIT_PASS
