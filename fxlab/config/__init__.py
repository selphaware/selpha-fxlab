"""Configuration loading for fxlab.

TOML only. ``tomllib`` ships with Python 3.12; PyYAML is deliberately not a
dependency (the pinned runtime does not have it, and one config format is one
fewer thing to get wrong). Nothing in this package hardcodes a path, a host or
a secret -- those arrive from the config file or the environment.
"""

from __future__ import annotations

from fxlab.config.loader import (
    BacktestConfig,
    ConfigError,
    CostConfig,
    DukascopyConfig,
    HourRequest,
    IngestConfig,
    OandaConfig,
    load_backtest_config,
    load_ingest_config,
    load_toml,
)

__all__ = [
    "BacktestConfig",
    "ConfigError",
    "CostConfig",
    "DukascopyConfig",
    "HourRequest",
    "IngestConfig",
    "OandaConfig",
    "load_backtest_config",
    "load_ingest_config",
    "load_toml",
]
