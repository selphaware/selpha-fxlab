"""Command line entrypoint for tick ingestion.

Usage::

    python -m fxlab.ingest --config <cfg.toml>

A thin CLI over :mod:`fxlab.ingestion.pipeline`: parse the config, run the
pipeline, translate the outcome into an exit code. Exit codes are 0 for a clean
run, 2 for a configuration problem, and 1 for anything the data did wrong --
including a rejected hour, whose reason token is on stderr verbatim.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Final

from fxlab.config import ConfigError, load_ingest_config
from fxlab.ingestion.pipeline import ingest
from fxlab.logging_setup import configure_logging

_LOG: Final[logging.Logger] = logging.getLogger("fxlab.ingest")

EXIT_OK: Final[int] = 0
EXIT_DATA: Final[int] = 1
EXIT_CONFIG: Final[int] = 2


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the ingest entrypoint."""
    parser = argparse.ArgumentParser(
        prog="python -m fxlab.ingest",
        description="Ingest Dukascopy tick hours into the Parquet store.")
    parser.add_argument("--config", required=True,
                        help="path to the ingest TOML config")
    parser.add_argument("--log-level", default=None,
                        help="logging level (default INFO, or FXLAB_LOG_LEVEL)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run ingestion and return a process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = load_ingest_config(args.config)
    except ConfigError as exc:
        _LOG.error("configuration error: %s", exc)
        return EXIT_CONFIG

    try:
        report = ingest(config)
    except Exception as exc:  # noqa: BLE001 - the CLI must not traceback-crash
        _LOG.exception("ingest failed: %s", exc)
        return EXIT_DATA

    if not report.ok:
        _LOG.error("ingest rejected %d hour(s); see %s",
                   report.hours_gap, report.manifest_file)
        return EXIT_DATA

    _LOG.info("ingest ok: %s", report.summary_line())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
