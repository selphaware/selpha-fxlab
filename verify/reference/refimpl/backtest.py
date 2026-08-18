"""Reference backtest entrypoint: ``python -m fxlab.backtest --config <cfg.toml>``."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import tomllib

from ._core import backtest


def main() -> int:
    """Run the reference strategy and write the results file."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ns = ap.parse_args()

    with open(ns.config, "rb") as fh:
        config = tomllib.load(fh)
    results = backtest(config)
    out = pathlib.Path(config["backtest"]["out_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf8")
    logging.info("backtest net_pnl=%.2f", results["summary"]["net_pnl"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
