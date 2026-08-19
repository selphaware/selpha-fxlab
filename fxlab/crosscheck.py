"""Command line entrypoint for the OANDA cross-check job.

Usage::

    python -m fxlab.crosscheck --config <ingest_cfg.toml> [--pair EURUSD]

FX has no consolidated tape, so agreement with a second, independent source is
the only evidence that the decoded ticks are the market rather than an artefact
of the decoder. This job resamples the stored ticks to hourly bars and compares
them with OANDA H1 candles over the same span.

What "agreement" means here is calibrated, not assumed. Dukascopy is an ECN
feed and OANDA is retail, so the Dukascopy bid sits above the OANDA bid and its
ask below -- measured at about +0.7 and -0.6 pip on EURUSD. Only the **mid**
difference is thresholded; the bid and ask offsets are reported, because
flagging them would be flagging the spread difference the two venues are meant
to have.

Requires ``OANDA_API_TOKEN``. Without it the job exits 3 and says so, which is
distinct from a disagreement.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import sys
from typing import Any, Final

from fxlab.config import ConfigError, IngestConfig, load_ingest_config
from fxlab.ingestion.bars import resample_ticks
from fxlab.ingestion.oanda import OandaClient, OandaError, cross_check
from fxlab.ingestion.store import read_ticks
from fxlab.logging_setup import configure_logging

_LOG: Final[logging.Logger] = logging.getLogger("fxlab.crosscheck")

EXIT_OK: Final[int] = 0
EXIT_DISAGREEMENT: Final[int] = 1
EXIT_CONFIG: Final[int] = 2
EXIT_UNAVAILABLE: Final[int] = 3

#: Report filename written inside the store root.
REPORT_NAME: Final[str] = "oanda_crosscheck.json"


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the cross-check entrypoint."""
    parser = argparse.ArgumentParser(
        prog="python -m fxlab.crosscheck",
        description="Compare stored Dukascopy hourly bars with OANDA H1 candles.")
    parser.add_argument("--config", required=True,
                        help="path to the ingest TOML config whose store to check")
    parser.add_argument("--pair", action="append", default=None,
                        help="restrict to this pair; repeatable")
    parser.add_argument("--out", default=None,
                        help="report path (default <out_dir>/oanda_crosscheck.json)")
    parser.add_argument("--log-level", default=None, help="logging level")
    return parser


def check_pair(config: IngestConfig, client: OandaClient, pair: str) -> dict[str, Any]:
    """Cross-check one pair and return its report fragment."""
    ticks = read_ticks(config.out_dir, pair=pair)
    if not len(ticks):
        _LOG.warning("%s: no stored ticks to check", pair)
        return {"pair": pair, "compared": 0, "ok": True, "stats": {}, "flagged": [],
                "note": "no stored ticks"}

    bars = resample_ticks(ticks, "1h", pair=pair).frame
    start = bars["ts"].iloc[0].to_pydatetime()
    end = bars["ts"].iloc[-1].to_pydatetime() + dt.timedelta(hours=1)
    _LOG.info("%s: %d stored hourly bar(s) from %s to %s",
              pair, len(bars), start.isoformat(), end.isoformat())

    candles = client.candles(pair, granularity=config.oanda.granularity,
                             start=start, end=end)
    _LOG.info("%s: %d OANDA %s candle(s) returned",
              pair, len(candles), config.oanda.granularity)

    result = cross_check(bars, candles, pair,
                         max_mid_diff_pips=config.oanda.max_mid_diff_pips)
    payload = result.to_dict()
    payload["oanda_candles"] = len(candles)
    payload["fxlab_bars"] = int(len(bars))
    _LOG.info("%s: compared %d hour(s); mid diff mean %.3f pips, max abs %.3f pips",
              pair, result.compared,
              result.stats.get("mid_diff_mean_pips", float("nan")),
              result.stats.get("mid_diff_max_abs_pips", float("nan")))
    if result.flagged:
        _LOG.warning("%s: %d hour(s) beyond the %.2f pip mid threshold",
                     pair, len(result.flagged), result.threshold_pips)
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run the cross-check and return a process exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = load_ingest_config(args.config)
    except ConfigError as exc:
        _LOG.error("configuration error: %s", exc)
        return EXIT_CONFIG

    pairs = args.pair or sorted({h.pair for h in config.hours})
    try:
        client = OandaClient(environment=config.oanda.env,
                             account_id=config.oanda.account_id,
                             timeout=config.oanda.timeout)
    except OandaError as exc:
        _LOG.error("%s", exc)
        return EXIT_UNAVAILABLE
    if not client.has_token:
        _LOG.error("OANDA_API_TOKEN is not set; the cross-check cannot run. "
                   "This is a missing credential, not a disagreement.")
        return EXIT_UNAVAILABLE

    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": client.host,
        "granularity": config.oanda.granularity,
        "threshold_pips": config.oanda.max_mid_diff_pips,
        "pairs": [],
    }
    try:
        for pair in pairs:
            report["pairs"].append(check_pair(config, client, pair))
    except OandaError as exc:
        _LOG.error("OANDA request failed: %s", exc)
        return EXIT_UNAVAILABLE
    except Exception as exc:  # noqa: BLE001 - the CLI must not traceback-crash
        _LOG.exception("cross-check failed: %s", exc)
        return EXIT_UNAVAILABLE

    out_path = pathlib.Path(args.out) if args.out else (
        pathlib.Path(config.out_dir) / REPORT_NAME)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf8")
    _LOG.info("cross-check report written to %s", out_path)

    if any(not entry.get("ok", True) for entry in report["pairs"]):
        return EXIT_DISAGREEMENT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
