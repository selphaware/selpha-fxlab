"""Coverage and spread reporting over a stored dataset.

Usage::

    python -m fxlab.report --config <ingest_cfg.toml> [--pair EURUSD]

This is validation reporting, not research: what arrived, what did not, and how
wide the market was while it did. Those three things decide whether a dataset
can be trusted, and the spread distribution by session in particular is what a
cost model has to be sanity-checked against.

Sessions come from :mod:`fxlab.ingestion.sessions`, whose windows are evaluated
in each financial centre local clock, so the London and New York boundaries move
independently with their own daylight saving rules.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any, Final

import numpy as np

from fxlab.config import ConfigError, load_ingest_config
from fxlab.ingestion.manifest import STATUS_GAP, STATUS_OK, Manifest, load_manifest
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import SESSIONS, session_labels
from fxlab.ingestion.store import read_ticks
from fxlab.logging_setup import configure_logging

_LOG: Final[logging.Logger] = logging.getLogger("fxlab.report")

#: Report filename written inside the store root.
REPORT_NAME: Final[str] = "coverage_report.json"

#: Percentiles reported for every spread distribution.
PERCENTILES: Final[tuple[float, ...]] = (50.0, 75.0, 90.0, 99.0, 99.9)


def coverage_summary(manifest: Manifest) -> dict[str, Any]:
    """Summarise what the manifest says arrived, and what did not."""
    hours = manifest.hours
    statuses: dict[str, int] = {}
    for record in hours:
        statuses[record.status] = statuses.get(record.status, 0) + 1
    gaps = [{"pair": r.pair, "date": r.date, "hour": r.hour,
             "reasons": [i["reason"] for i in r.issues]}
            for r in hours if r.status == STATUS_GAP]
    return {
        "hours_requested": len(hours),
        "by_status": statuses,
        "ticks": sum(r.written_ticks for r in hours),
        "duplicates_dropped": sum(r.duplicates_dropped for r in hours),
        "gaps": gaps,
        "by_day": manifest.coverage()["by_day"],
    }


def _percentiles(values: np.ndarray) -> dict[str, float]:
    """Return the reported percentiles of ``values`` as a plain dict."""
    if values.size == 0:
        return {}
    computed = np.percentile(values, list(PERCENTILES))
    out = {f"p{p:g}".replace(".", "_"): float(v)
           for p, v in zip(PERCENTILES, computed)}
    out["mean"] = float(np.mean(values))
    out["max"] = float(np.max(values))
    return out


def spread_by_session(ticks: Any, pair: str) -> dict[str, Any]:
    """Spread distribution in pips, split by intraday session."""
    if not len(ticks):
        return {}
    pip = pair_spec(pair).pip_size
    pips = ((ticks["ask"] - ticks["bid"]) / pip).to_numpy()
    labels = session_labels(ticks["ts"])
    out: dict[str, Any] = {"all": {"ticks": int(len(pips)), **_percentiles(pips)}}
    for session in SESSIONS:
        selected = pips[labels == session]
        if selected.size:
            out[session] = {"ticks": int(selected.size), **_percentiles(selected)}
    return out


def hourly_profile(ticks: Any) -> dict[str, int]:
    """Tick counts by UTC hour of day, which is where session shape shows up."""
    if not len(ticks):
        return {}
    hours = ticks["ts"].dt.hour.to_numpy()
    counts = np.bincount(hours, minlength=24)
    return {f"{h:02d}": int(counts[h]) for h in range(24)}


def build_report(out_dir: pathlib.Path, pairs: list[str] | None = None) -> dict[str, Any]:
    """Assemble the full report for a store."""
    manifest = load_manifest(out_dir)
    known = pairs or sorted({r.pair for r in manifest.hours})
    report: dict[str, Any] = {
        "store": str(out_dir),
        "coverage": coverage_summary(manifest),
        "warnings": manifest.to_dict()["validation"]["warnings"],
        "pairs": {},
    }
    for pair in known:
        ticks = read_ticks(out_dir, pair=pair)
        stored = [r for r in manifest.hours
                  if r.pair == pair and r.status == STATUS_OK]
        report["pairs"][pair] = {
            "ticks": int(len(ticks)),
            "hours_stored": len(stored),
            "first_ts": str(ticks["ts"].iloc[0]) if len(ticks) else None,
            "last_ts": str(ticks["ts"].iloc[-1]) if len(ticks) else None,
            "spread_pips": spread_by_session(ticks, pair),
            "ticks_by_utc_hour": hourly_profile(ticks),
        }
    return report


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the report entrypoint."""
    parser = argparse.ArgumentParser(
        prog="python -m fxlab.report",
        description="Report coverage, gaps and spread distributions for a store.")
    parser.add_argument("--config", required=True,
                        help="path to the ingest TOML config whose store to report on")
    parser.add_argument("--pair", action="append", default=None,
                        help="restrict to this pair; repeatable")
    parser.add_argument("--out", default=None,
                        help="report path (default <out_dir>/coverage_report.json)")
    parser.add_argument("--log-level", default=None, help="logging level")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the report and return a process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = load_ingest_config(args.config)
    except ConfigError as exc:
        _LOG.error("configuration error: %s", exc)
        return 2

    report = build_report(pathlib.Path(config.out_dir), args.pair)
    out_path = pathlib.Path(args.out) if args.out else (
        pathlib.Path(config.out_dir) / REPORT_NAME)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf8")

    coverage = report["coverage"]
    _LOG.info("%d hour(s) requested: %s; %d ticks, %d duplicate(s) dropped",
              coverage["hours_requested"], coverage["by_status"],
              coverage["ticks"], coverage["duplicates_dropped"])
    for pair, entry in report["pairs"].items():
        overall = entry["spread_pips"].get("all", {})
        _LOG.info("%s: %d ticks, median spread %.2f pips, p99 %.2f pips",
                  pair, entry["ticks"], overall.get("p50", float("nan")),
                  overall.get("p99", float("nan")))
    _LOG.info("report written to %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
