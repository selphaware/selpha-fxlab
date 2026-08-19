"""Command line entrypoint for the backtester.

Usage::

    python -m fxlab.backtest --config <cfg.toml>

A thin CLI over :mod:`fxlab.backtest.engine`: load bars, build the cost model
and the reference strategy from config, run, write the results document.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from typing import Final

from fxlab.backtest.engine import bars_from_frame, run_backtest
from fxlab.backtest.results import result_to_dict, write_results
from fxlab.backtest.strategy import MovingAverageCrossStrategy
from fxlab.config import BacktestConfig, ConfigError, load_backtest_config
from fxlab.costs import IBCostModel
from fxlab.logging_setup import configure_logging

_LOG: Final[logging.Logger] = logging.getLogger("fxlab.backtest")

EXIT_OK: Final[int] = 0
EXIT_FAILED: Final[int] = 1
EXIT_CONFIG: Final[int] = 2


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the backtest entrypoint."""
    parser = argparse.ArgumentParser(
        prog="python -m fxlab.backtest",
        description="Run the reference strategy over stored bars.")
    parser.add_argument("--config", required=True,
                        help="path to the backtest TOML config")
    parser.add_argument("--log-level", default=None,
                        help="logging level (default INFO, or FXLAB_LOG_LEVEL)")
    return parser


def load_bars(config: BacktestConfig) -> dict[str, list]:
    """Read every configured bar table, keyed by pair.

    Args:
        config: The backtest configuration.

    Returns:
        Bars per pair, each list in ascending time order.

    Raises:
        FileNotFoundError: If a bars file is missing.
        ValueError: If a file holds no rows for its pair.
    """
    import pyarrow.parquet as pq

    bars_by_pair: dict[str, list] = {}
    for instrument in config.instruments:
        path = pathlib.Path(instrument.bars_path)
        if not path.exists():
            raise FileNotFoundError(f"bars file not found: {path}")
        frame = pq.read_table(path).to_pandas()
        if "pair" in frame.columns:
            frame = frame[frame["pair"] == instrument.pair]
        if not len(frame):
            raise ValueError(f"{path} holds no bars for pair {instrument.pair}")
        bars_by_pair[instrument.pair] = bars_from_frame(frame, instrument.pair)
    return bars_by_pair


def main(argv: list[str] | None = None) -> int:
    """Run a backtest and return a process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = load_backtest_config(args.config)
    except ConfigError as exc:
        _LOG.error("configuration error: %s", exc)
        return EXIT_CONFIG

    try:
        bars_by_pair = load_bars(config)
        cost_model = IBCostModel.from_config(config.costs)
        strategy = MovingAverageCrossStrategy(config.fast, config.slow, config.units)
        result = run_backtest(bars_by_pair, cost_model, strategy,
                              initial_equity=config.initial_equity)
        payload = result_to_dict(result, extra={
            "instruments": [{"pair": i.pair, "bars_path": str(i.bars_path)}
                            for i in config.instruments],
            "bars_path": str(config.bars_path),
            "pair": config.pair,
            "units": config.units,
            "fast": config.fast,
            "slow": config.slow,
            "commission_rate": config.costs.commission_rate,
            "commission_min": config.costs.commission_min,
            "cost_multiplier": config.costs.cost_multiplier,
        })
        out_path = write_results(config.out_path, payload)
    except Exception as exc:  # noqa: BLE001 - the CLI must not traceback-crash
        _LOG.exception("backtest failed: %s", exc)
        return EXIT_FAILED

    summary = payload["summary"]
    _LOG.info("backtest ok: %d trade(s), gross=%.2f costs=%.2f net=%.2f -> %s",
              summary["trade_count"], summary["gross_pnl"],
              summary["total_costs"], summary["net_pnl"], out_path)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
