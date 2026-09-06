"""Measure each cross-checked hour's own median spread, for ruling R7.

R7's middle band -- 500 to 2,999 ticks -- is thresholded at ``1.0 pip + that
hour's own median spread``. Nothing in the store carries that number: the
hourly bar table holds a mean spread, and a mean is not a median in a
distribution this skewed, so the ruling has to be answered from the ticks.

This is a checkpointed pass for exactly the reasons ``crosscheck_oanda`` is:
about 11,800 hourly Parquet files at roughly 70 files a second is minutes of
wall clock, which is fine once and wrong on every gate run. It writes
``spreads.jsonl`` beside ``oanda.jsonl``, resumes by skipping pair-dates it has
already measured, and the experiment reads it back (ruling D5).

The hours it measures are taken **from the stored cross-check sample**, not
re-sampled. The T4 card says to re-issue the verdict "from its stored sample",
and re-drawing a sample would be re-running the experiment rather than
re-reading it -- a different thing, with a different answer, and no way for a
reader to tell which they were looking at.

Every read goes through :class:`research.loader.ResearchLoader`, so the seal
and ruling R1 police these reads like any other.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
from typing import Any, Final

import numpy as np

from research.crosscheck_class import median_spread_pips
from research.crosscheck_oanda import CROSSCHECK_NAME, read_checkpoint

_LOG: Final[logging.Logger] = logging.getLogger("research.crosscheck_spreads")

#: The checkpoint, one JSON object per pair-date. ``.jsonl`` and not ``.json``:
#: the research gate treats every ``*.json`` in an experiment directory as a
#: result document to be ledgered and re-hashed, and a checkpoint is not one.
SPREADS_NAME: Final[str] = "spreads.jsonl"


def hour_median_spread(loader: Any, pair: str, date: str,
                       hour: int) -> dict[str, Any] | None:
    """The median spread of one stored hour, in pips and in price units.

    Returns ``None`` when the hour is not stored -- which should not happen for
    an hour the cross-check compared, and is reported rather than defaulted
    because a missing measurement is what R7's middle band cannot tolerate.
    """
    table = loader.load_tick_hour(pair, date, hour, columns=["bid", "ask"])
    if table is None or table.num_rows == 0:
        return None
    spread = (np.asarray(table["ask"], dtype="float64")
              - np.asarray(table["bid"], dtype="float64"))
    median = float(np.median(spread))
    return {
        "ticks": int(table.num_rows),
        "median_spread": round(median, 9),
        "median_spread_pips": round(median_spread_pips(pair, median), 4),
        "mean_spread_pips": round(
            median_spread_pips(pair, float(spread.mean())), 4),
    }


def read_spreads(path: pathlib.Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every pair-date already measured, keyed by ``(pair, date)``."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    path = pathlib.Path(path)
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _LOG.warning("dropping a truncated checkpoint line")
            continue
        out[(str(row.get("pair")), str(row.get("date")))] = row
    return out


def spread_index(rows: Any) -> dict[tuple[str, str, int], float]:
    """``(pair, date, hour) -> median spread in pips`` from the checkpoint."""
    index: dict[tuple[str, str, int], float] = {}
    for row in rows:
        pair, date = str(row.get("pair")), str(row.get("date"))
        for hour, entry in (row.get("hours") or {}).items():
            value = (entry or {}).get("median_spread_pips")
            if value is None:
                continue
            index[(pair, date, int(hour))] = float(value)
    return index


def _append(path: pathlib.Path, row: dict[str, Any]) -> None:
    """Append one checkpoint record and flush it to the OS."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_pass(loader: Any, experiment_dir: pathlib.Path) -> dict[str, Any]:
    """Measure every hour the stored cross-check compared. Resumable."""
    compared = read_checkpoint(experiment_dir / CROSSCHECK_NAME)
    checkpoint = experiment_dir / SPREADS_NAME
    done = read_spreads(checkpoint)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    todo = [key for key in sorted(compared) if key not in done]
    _LOG.info("%d pair-date(s) in the stored sample, %d already measured, "
              "%d to read", len(compared), len(done), len(todo))

    measured = missing = 0
    for index, key in enumerate(todo, 1):
        pair, date = key
        hours: dict[str, Any] = {}
        for entry in compared[key].get("hours") or []:
            hour = int(entry["hour"])
            found = hour_median_spread(loader, pair, date, hour)
            if found is None:
                missing += 1
                continue
            hours[str(hour)] = found
            measured += 1
        _append(checkpoint, {"pair": pair, "date": date, "hours": hours})
        if index % 200 == 0:
            _LOG.info("%d/%d pair-dates measured", index, len(todo))

    rows = read_spreads(checkpoint)
    return {"pair_dates": len(rows), "hours_measured": measured,
            "hours_missing": missing,
            "hours_on_disk": len(spread_index(rows.values()))}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.crosscheck_spreads",
        description="Measure the median spread of every cross-checked hour.")
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--base", type=pathlib.Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the pass and print what it measured."""
    from research.experiment import build_loader, load_config
    from research.loader import project_root

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z")
    args = parse_args(argv)
    base = (pathlib.Path(args.base).resolve() if args.base else project_root())
    config = load_config(args.config)
    loader = build_loader(config, base)
    experiment_dir = base / str(config.params["experiment_dir"])
    summary = run_pass(loader, experiment_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["hours_missing"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
