"""Run one experiment, ledger first.

Usage::

    python -m research.run --config experiments/<id>/config.toml
    python -m research.run --config <cfg> --reproduce --out <path>

The plain form is what a task card's loop runs: it appends a **start** record
to the ledger, executes, writes ``result.json`` next to the config, and appends
an **end** record. The ``--reproduce`` form is what the research gate runs: same
execution, same hash, no ledger writes and no result file unless one is asked
for, so that verifying an experiment cannot be mistaken for performing one.

Named reasons go to stderr verbatim. Any failure exits non-zero, and a failure
after the start record still gets an end record with ``status = "failed"`` --
an abandoned trial is a trial, and pre-reg #10 counts it.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from typing import Final

from fxlab.logging_setup import configure_logging
from research import ledger as ledger_mod
from research.experiment import (RESULT_NAME, ExperimentError, execute,
                                 load_config, write_result)
from research.loader import LoaderRefusal
from research.seal import SealBreach

_LOG: Final[logging.Logger] = logging.getLogger("research.run")


def project_root() -> pathlib.Path:
    """Repository root, derived from this file."""
    return pathlib.Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.run",
        description="Run one ledgered research experiment.")
    parser.add_argument("--config", required=True, type=pathlib.Path,
                        help="experiment config TOML")
    parser.add_argument("--reproduce", action="store_true",
                        help="re-execute without writing to the ledger")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="where to write the result document")
    parser.add_argument("--base", type=pathlib.Path, default=None,
                        help="project root; derived when omitted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run an experiment and return a process exit code."""
    args = parse_args(argv)
    configure_logging()
    base = pathlib.Path(args.base).resolve() if args.base else project_root()

    try:
        config = load_config(args.config)
    except ExperimentError as exc:
        print(exc.reason, file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    if args.reproduce:
        return _reproduce(config, base, args.out)

    started = False
    try:
        ledger_mod.append_start(
            base, experiment_id=config.experiment_id, taskcard=config.taskcard,
            config_sha256=config.sha256, seed=config.seed, mode=config.mode,
            rerun_class=config.rerun_class, subset_sha256=config.subset_sha256)
        started = True

        document = execute(config, base)
        out = args.out or (config.path.parent / RESULT_NAME)
        write_result(document, out)

        ledger_mod.append_end(
            base, experiment_id=config.experiment_id, status="ok",
            result_files=[_rel(out, base)],
            result_hash=document["result_hash"], scored=config.scored)
        print(f"RESULT_HASH {document['result_hash']}")
        _LOG.info("experiment %s complete", config.experiment_id)
        return 0

    except (ExperimentError, SealBreach, LoaderRefusal, ValueError) as exc:
        reason = getattr(exc, "reason", "EXPERIMENT_FAILED")
        print(reason, file=sys.stderr)
        print(str(exc), file=sys.stderr)
        if started:
            ledger_mod.append_end(base, experiment_id=config.experiment_id,
                                  status=f"failed:{reason}", result_files=[],
                                  result_hash=None, scored=config.scored)
        return 1
    except Exception as exc:  # noqa: BLE001 - record the trial, then re-raise
        if started:
            ledger_mod.append_end(base, experiment_id=config.experiment_id,
                                  status="failed:UNHANDLED", result_files=[],
                                  result_hash=None, scored=config.scored)
        _LOG.exception("experiment %s raised", config.experiment_id)
        print("EXPERIMENT_FAILED", file=sys.stderr)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _reproduce(config, base: pathlib.Path,
               out: pathlib.Path | None) -> int:
    """Re-execute without ledger side effects, printing the hash."""
    try:
        document = execute(config, base)
    except (ExperimentError, SealBreach, LoaderRefusal, ValueError) as exc:
        print(getattr(exc, "reason", "EXPERIMENT_FAILED"), file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    if out is not None:
        write_result(document, out)
    print(f"RESULT_HASH {document['result_hash']}")
    return 0


def _rel(path: pathlib.Path, base: pathlib.Path) -> str:
    """Project-relative POSIX path where possible."""
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return path.resolve().as_posix()


if __name__ == "__main__":
    sys.exit(main())
