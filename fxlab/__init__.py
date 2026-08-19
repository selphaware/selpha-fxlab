"""``fxlab`` -- FX data and research plumbing.

Phase 1 scope is deliberately narrow: ingest and validate tick data, store it
with a pinned schema, model execution costs, and run an event-ordered
backtester over the result. No strategies, no live trading.

Sub-packages
------------
``fxlab.ingestion``
    Dukascopy bi5 download/decode, validation, Parquet store, bar resampling
    and the read-only OANDA cross-check client.
``fxlab.costs``
    The ``CostModel`` protocol and the IB-calibrated implementation.
``fxlab.backtest``
    Event-ordered, multi-pair backtester plus the reference MA-cross strategy
    used only to exercise order -> fill -> cost -> P&L.
``fxlab.recorder``
    ``Feed`` protocol, a replayable fake feed and the IB feed stub, with a
    recorder that writes the same tick schema as ingestion.
``fxlab.config``
    TOML configuration loading. No path or secret is ever hardcoded.

Entrypoints
-----------
``python -m fxlab.ingest --config <cfg.toml>``
``python -m fxlab.backtest --config <cfg.toml>``
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
