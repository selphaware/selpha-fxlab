"""Typed configuration objects parsed from TOML.

Every entrypoint takes ``--config <file.toml>``; nothing is read from
positional arguments or ambient defaults. Parsing is strict about the keys the
contract requires and tolerant about extra keys, so a config can carry
annotations the code does not (yet) consume.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import Any, Final

#: Ingest modes. "fixture" reads local bi5 files and must never touch the
#: network; "live" fetches from the Dukascopy datafeed.
VALID_MODES: Final[frozenset[str]] = frozenset({"fixture", "live"})

#: Bar timeframes the resampler understands, as pandas offset aliases.
DEFAULT_TIMEFRAMES: Final[tuple[str, ...]] = ("1min", "5min", "1h")


class ConfigError(ValueError):
    """Raised when a configuration file is missing, malformed or incomplete."""


def load_toml(path: str | pathlib.Path) -> dict[str, Any]:
    """Read and parse a TOML file.

    Args:
        path: Path to the TOML file.

    Returns:
        The parsed document as a plain dict.

    Raises:
        ConfigError: If the file is absent or is not valid TOML.
    """
    p = pathlib.Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    try:
        with p.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: not valid TOML: {exc}") from exc


def _section(doc: dict[str, Any], name: str, origin: pathlib.Path) -> dict[str, Any]:
    """Return a required top-level table."""
    value = doc.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"{origin}: missing required [{name}] section")
    return value


def _require(table: dict[str, Any], key: str, where: str) -> Any:
    """Return a required key from ``table`` or explain what is missing."""
    if key not in table:
        raise ConfigError(f"{where}: missing required key {key!r}")
    return table[key]


def _as_date(value: Any, where: str) -> dt.date:
    """Coerce a TOML scalar to a date.

    TOML dates may arrive already typed (bare 2026-07-14) or as strings
    ("2026-07-14"); both spellings are accepted.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{where}: {value!r} is not an ISO date") from exc
    raise ConfigError(f"{where}: {value!r} is not a date")


def _as_path(value: Any, where: str) -> pathlib.Path:
    """Coerce a TOML scalar to a filesystem path."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a non-empty path string, got {value!r}")
    return pathlib.Path(value)


@dataclass(frozen=True, slots=True)
class HourRequest:
    """One requested (pair, UTC date, UTC hour) of tick data."""

    pair: str
    day: dt.date
    hour: int

    def __post_init__(self) -> None:
        if not self.pair:
            raise ConfigError("hour request has an empty pair")
        if not 0 <= self.hour <= 23:
            raise ConfigError(
                f"{self.pair} {self.day}: hour {self.hour} is outside 0..23")

    @property
    def date_str(self) -> str:
        """ISO date, the form used in manifest entries and partition paths."""
        return self.day.isoformat()

    @property
    def key(self) -> tuple[str, str, int]:
        """Manifest identity of this hour."""
        return (self.pair, self.date_str, self.hour)

    @property
    def start(self) -> dt.datetime:
        """The UTC instant the hour opens; tick offsets are added to this."""
        return dt.datetime(self.day.year, self.day.month, self.day.day,
                           self.hour, tzinfo=dt.timezone.utc)

    @property
    def fixture_name(self) -> str:
        """Filename this hour takes in a fixture raw_dir."""
        return f"{self.pair}_{self.date_str}_{self.hour:02d}h.bi5"

    def label(self) -> str:
        """Human-readable identity used in log lines and error messages."""
        return f"{self.pair} {self.date_str}T{self.hour:02d}:00Z"


@dataclass(frozen=True, slots=True)
class DukascopyConfig:
    """Tunables for the live Dukascopy datafeed client."""

    base_url: str = "https://datafeed.dukascopy.com/datafeed"
    max_concurrency: int = 4
    max_retries: int = 5
    backoff_initial: float = 1.0
    backoff_factor: float = 2.0
    backoff_max: float = 30.0
    timeout: float = 30.0
    user_agent: str = "fxlab/1.0 (research data ingest)"

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency <= 4:
            raise ConfigError(
                "ingest.dukascopy.max_concurrency must be between 1 and 4 "
                "(the feed throttles; politeness is a hard requirement)")
        if self.max_retries < 0:
            raise ConfigError("ingest.dukascopy.max_retries must be >= 0")


@dataclass(frozen=True, slots=True)
class OandaConfig:
    """Read-only OANDA v20 cross-check settings.

    The token never appears here: it is read from OANDA_API_TOKEN at call
    time. ``env`` selects the host and defaults to the practice environment.
    """

    enabled: bool = False
    env: str | None = None
    account_id: str | None = None
    granularity: str = "H1"
    max_mid_diff_pips: float = 1.0
    timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class IngestConfig:
    """Everything the ingest entrypoint needs."""

    mode: str
    out_dir: pathlib.Path
    hours: tuple[HourRequest, ...]
    raw_dir: pathlib.Path | None = None
    source: str = "dukascopy"
    resume: bool = True
    fail_on_gap: bool = True
    checkpoint_every: int = 25
    archive_raw_dir: pathlib.Path | None = None
    bar_timeframes: tuple[str, ...] = ()
    dukascopy: DukascopyConfig = field(default_factory=DukascopyConfig)
    oanda: OandaConfig = field(default_factory=OandaConfig)
    origin: pathlib.Path | None = None

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ConfigError(
                f"ingest.mode is {self.mode!r}, expected one of {sorted(VALID_MODES)}")
        if self.mode == "fixture" and self.raw_dir is None:
            raise ConfigError("ingest.mode = 'fixture' requires ingest.raw_dir")
        if self.checkpoint_every < 1:
            raise ConfigError("ingest.checkpoint_every must be >= 1")
        if not self.hours:
            raise ConfigError(
                "no hours requested: give at least one [[ingest.hours]] or "
                "[[ingest.range]] entry")


def _parse_hours(table: dict[str, Any], origin: pathlib.Path) -> tuple[HourRequest, ...]:
    """Build the requested hour list from ``hours`` and/or ``range`` entries.

    ``[[ingest.hours]]`` names single hours; ``[[ingest.range]]`` expands an
    inclusive date range across one or more pairs, which is what a real
    multi-day pull uses. Config order is preserved and exact repeats collapse.
    """
    out: list[HourRequest] = []

    for i, entry in enumerate(table.get("hours") or []):
        where = f"{origin}: [[ingest.hours]] #{i}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: expected a table")
        out.append(HourRequest(
            pair=str(_require(entry, "pair", where)),
            day=_as_date(_require(entry, "date", where), where),
            hour=int(_require(entry, "hour", where)),
        ))

    for i, entry in enumerate(table.get("range") or []):
        where = f"{origin}: [[ingest.range]] #{i}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: expected a table")
        pairs = entry.get("pairs")
        if pairs is None:
            pairs = [_require(entry, "pair", where)]
        if not isinstance(pairs, list) or not pairs:
            raise ConfigError(f"{where}: pairs must be a non-empty list")
        start = _as_date(_require(entry, "start", where), where)
        end = _as_date(_require(entry, "end", where), where)
        if end < start:
            raise ConfigError(f"{where}: end {end} precedes start {start}")
        hours = entry.get("hours")
        if hours is None:
            hour_list = list(range(24))
        elif isinstance(hours, list) and hours:
            hour_list = [int(h) for h in hours]
        else:
            raise ConfigError(f"{where}: hours must be a non-empty list of ints")
        for pair in pairs:
            for offset in range((end - start).days + 1):
                day = start + dt.timedelta(days=offset)
                for hour in hour_list:
                    out.append(HourRequest(pair=str(pair), day=day, hour=hour))

    seen: set[tuple[str, str, int]] = set()
    unique: list[HourRequest] = []
    for req in out:
        if req.key in seen:
            continue
        seen.add(req.key)
        unique.append(req)
    return tuple(unique)


def load_ingest_config(path: str | pathlib.Path) -> IngestConfig:
    """Parse an ingest config file.

    Args:
        path: Path to the TOML config.

    Returns:
        A fully validated :class:`IngestConfig`.

    Raises:
        ConfigError: On any missing or malformed setting.
    """
    origin = pathlib.Path(path)
    doc = load_toml(origin)
    table = _section(doc, "ingest", origin)
    where = f"{origin}: [ingest]"

    duka = table.get("dukascopy") or {}
    if not isinstance(duka, dict):
        raise ConfigError(f"{origin}: [ingest.dukascopy] must be a table")
    oanda = table.get("oanda") or {}
    if not isinstance(oanda, dict):
        raise ConfigError(f"{origin}: [ingest.oanda] must be a table")

    timeframes = table.get("bar_timeframes")
    if timeframes is None:
        bar_timeframes: tuple[str, ...] = ()
    elif isinstance(timeframes, list):
        bar_timeframes = tuple(str(t) for t in timeframes)
    else:
        raise ConfigError(f"{where}: bar_timeframes must be a list of strings")

    raw_dir = table.get("raw_dir")
    archive = table.get("archive_raw_dir")
    try:
        duka_cfg = DukascopyConfig(**duka)
        oanda_cfg = OandaConfig(**oanda)
    except TypeError as exc:
        raise ConfigError(f"{origin}: unknown key in a [ingest.*] table: {exc}") from exc

    return IngestConfig(
        mode=str(_require(table, "mode", where)),
        out_dir=_as_path(_require(table, "out_dir", where), f"{where}.out_dir"),
        raw_dir=None if raw_dir is None else _as_path(raw_dir, f"{where}.raw_dir"),
        hours=_parse_hours(table, origin),
        source=str(table.get("source", "dukascopy")),
        resume=bool(table.get("resume", True)),
        fail_on_gap=bool(table.get("fail_on_gap", True)),
        checkpoint_every=int(table.get("checkpoint_every", 25)),
        archive_raw_dir=(None if archive is None
                         else _as_path(archive, f"{where}.archive_raw_dir")),
        bar_timeframes=bar_timeframes,
        dukascopy=duka_cfg,
        oanda=oanda_cfg,
        origin=origin,
    )


@dataclass(frozen=True, slots=True)
class CostConfig:
    """Parameters of the IB cost model.

    ``commission_rate`` is a fraction of notional (2e-05 == 0.20 bp),
    ``commission_min`` is the per-order floor in the quote currency, and
    ``cost_multiplier`` scales every cost line for stress runs (1.5x, 2x).
    """

    commission_rate: float = 2e-05
    commission_min: float = 2.0
    cost_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.commission_rate < 0:
            raise ConfigError("backtest.costs.commission_rate must be >= 0")
        if self.commission_min < 0:
            raise ConfigError("backtest.costs.commission_min must be >= 0")
        if self.cost_multiplier < 0:
            raise ConfigError("backtest.costs.cost_multiplier must be >= 0")


@dataclass(frozen=True, slots=True)
class Instrument:
    """One pair and the bar table it is backtested over."""

    pair: str
    bars_path: pathlib.Path


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Everything the backtest entrypoint needs.

    ``pair`` and ``bars_path`` describe a single-instrument run. A multi-pair
    run adds ``[[backtest.instruments]]`` tables, each with its own ``pair`` and
    ``bars_path``; the engine is multi-pair either way.
    """

    bars_path: pathlib.Path
    pair: str
    units: int
    fast: int
    slow: int
    out_path: pathlib.Path
    costs: CostConfig = field(default_factory=CostConfig)
    initial_equity: float = 0.0
    instruments: tuple[Instrument, ...] = ()
    origin: pathlib.Path | None = None

    def __post_init__(self) -> None:
        if self.fast < 1 or self.slow < 1:
            raise ConfigError("backtest.fast and backtest.slow must be >= 1")
        if self.fast >= self.slow:
            raise ConfigError(
                f"backtest.fast ({self.fast}) must be strictly shorter than "
                f"backtest.slow ({self.slow})")
        if self.units <= 0:
            raise ConfigError("backtest.units must be > 0")


def load_backtest_config(path: str | pathlib.Path) -> BacktestConfig:
    """Parse a backtest config file.

    Args:
        path: Path to the TOML config.

    Returns:
        A fully validated :class:`BacktestConfig`.

    Raises:
        ConfigError: On any missing or malformed setting.
    """
    origin = pathlib.Path(path)
    doc = load_toml(origin)
    table = _section(doc, "backtest", origin)
    where = f"{origin}: [backtest]"

    costs_table = table.get("costs") or {}
    if not isinstance(costs_table, dict):
        raise ConfigError(f"{origin}: [backtest.costs] must be a table")
    try:
        costs = CostConfig(**costs_table)
    except TypeError as exc:
        raise ConfigError(f"{origin}: unknown key in [backtest.costs]: {exc}") from exc

    instruments: list[Instrument] = []
    for i, entry in enumerate(table.get("instruments") or []):
        entry_where = f"{origin}: [[backtest.instruments]] #{i}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{entry_where}: expected a table")
        instruments.append(Instrument(
            pair=str(_require(entry, "pair", entry_where)),
            bars_path=_as_path(_require(entry, "bars_path", entry_where),
                               f"{entry_where}.bars_path")))

    if instruments:
        pair = str(table.get("pair", instruments[0].pair))
        bars_path = (_as_path(table["bars_path"], f"{where}.bars_path")
                     if "bars_path" in table else instruments[0].bars_path)
    else:
        pair = str(_require(table, "pair", where))
        bars_path = _as_path(_require(table, "bars_path", where), f"{where}.bars_path")
        instruments = [Instrument(pair=pair, bars_path=bars_path)]

    return BacktestConfig(
        bars_path=bars_path,
        pair=pair,
        instruments=tuple(instruments),
        units=int(_require(table, "units", where)),
        fast=int(_require(table, "fast", where)),
        slow=int(_require(table, "slow", where)),
        out_path=_as_path(_require(table, "out_path", where), f"{where}.out_path"),
        costs=costs,
        initial_equity=float(table.get("initial_equity", 0.0)),
        origin=origin,
    )
