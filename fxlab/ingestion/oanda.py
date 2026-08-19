"""Read-only OANDA v20 client, used only to cross-check the primary feed.

FX has no consolidated tape, so a second independent source is the only way to
tell a decoding bug from a market fact. This client exists for that and nothing
else: it is restricted to the instruments and candles endpoints, it only ever
issues GET, and any attempt to reach an order, trade or position endpoint
raises before a request is made.

Confirmed response shape (``price=BAM&granularity=H1``): top-level
``instrument`` / ``granularity`` / ``candles``, each candle carrying ``time``,
``complete``, ``volume`` and ``bid`` / ``ask`` / ``mid`` OHLC objects. Prices
are **strings** and ``time`` is RFC3339 with **nanosecond** precision. Both
need deliberate parsing; letting a JSON decoder guess produces either strings
where floats belong or a truncation nobody notices.

The token comes from ``OANDA_API_TOKEN`` and is never logged, printed, stored
or committed. ``OANDA_ENV`` selects the environment and defaults to practice.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Final

#: Environment variables this module reads. Nothing else is consulted.
ENV_TOKEN: Final[str] = "OANDA_API_TOKEN"
ENV_ENVIRONMENT: Final[str] = "OANDA_ENV"
ENV_ACCOUNT: Final[str] = "OANDA_ACCOUNT_ID"

#: Hosts by environment name. Practice is the default everywhere.
HOSTS: Final[dict[str, str]] = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
DEFAULT_ENVIRONMENT: Final[str] = "practice"

#: Path fragments that would move money. Reaching one is a programming error.
FORBIDDEN_FRAGMENTS: Final[tuple[str, ...]] = (
    "/orders", "/trades", "/positions", "/transactions", "/pricing/stream")

#: Endpoint suffixes this client is allowed to request.
ALLOWED_SUFFIXES: Final[tuple[str, ...]] = ("/candles", "/instruments")


class OandaError(RuntimeError):
    """Raised for a missing token, a rejected endpoint or a bad response."""


@dataclass(frozen=True, slots=True)
class OHLC:
    """One open/high/low/close quartet."""

    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> OHLC:
        """Parse the string-valued OHLC object OANDA returns."""
        try:
            return cls(open=float(payload["o"]), high=float(payload["h"]),
                       low=float(payload["l"]), close=float(payload["c"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise OandaError(f"malformed OHLC object: {payload!r}") from exc


@dataclass(frozen=True, slots=True)
class Candle:
    """One OANDA candle with bid, ask and mid quartets."""

    ts: dt.datetime
    complete: bool
    volume: int
    bid: OHLC
    ask: OHLC
    mid: OHLC

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Candle:
        """Parse one candle, including its nanosecond RFC3339 timestamp."""
        return cls(
            ts=parse_rfc3339(payload["time"]),
            complete=bool(payload.get("complete", False)),
            volume=int(payload.get("volume", 0)),
            bid=OHLC.from_payload(payload["bid"]),
            ask=OHLC.from_payload(payload["ask"]),
            mid=OHLC.from_payload(payload["mid"]),
        )


def parse_rfc3339(value: str) -> dt.datetime:
    """Parse an RFC3339 timestamp that may carry nanosecond precision.

    Args:
        value: For example ``2026-07-14T13:00:00.000000000Z``.

    Returns:
        A tz-aware UTC datetime, truncated to microseconds.

    ``datetime.fromisoformat`` rejects nine fractional digits, so the fraction
    is trimmed explicitly rather than by hoping the field is shorter.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        digits = ""
        while rest and rest[0].isdigit():
            digits += rest[0]
            rest = rest[1:]
        text = f"{head}.{digits[:6]:0<6}{rest}"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def resolve_host(environment: str | None = None) -> str:
    """Return the API host for ``environment`` or ``OANDA_ENV``.

    Args:
        environment: ``practice`` or ``live``; the environment variable is
            consulted when omitted, defaulting to practice.

    Returns:
        The host URL.

    Raises:
        OandaError: If the environment name is not recognised.
    """
    name = (environment or os.environ.get(ENV_ENVIRONMENT)
            or DEFAULT_ENVIRONMENT).strip().lower()
    if name not in HOSTS:
        raise OandaError(
            f"unknown OANDA environment {name!r}; expected one of {sorted(HOSTS)}")
    return HOSTS[name]


def check_path(path: str) -> str:
    """Reject any endpoint that is not read-only.

    Args:
        path: The API path about to be requested.

    Returns:
        The path, unchanged, when it is allowed.

    Raises:
        OandaError: If the path could place or modify an order.
    """
    lowered = path.lower()
    for fragment in FORBIDDEN_FRAGMENTS:
        if fragment in lowered:
            raise OandaError(
                f"refusing to call {path}: this client is restricted to read-only "
                "instruments and candles endpoints")
    if not any(lowered.endswith(suffix) for suffix in ALLOWED_SUFFIXES):
        raise OandaError(
            f"refusing to call {path}: allowed endpoints end with "
            f"{list(ALLOWED_SUFFIXES)}")
    return path


def instrument_name(pair: str) -> str:
    """Translate ``EURUSD`` into the ``EUR_USD`` spelling OANDA uses."""
    from fxlab.ingestion.pairs import pair_spec

    return pair_spec(pair).oanda_instrument


class OandaClient:
    """Minimal read-only v20 client.

    Args:
        environment: ``practice`` or ``live``; defaults to ``OANDA_ENV``.
        token: API token; defaults to ``OANDA_API_TOKEN``. Never logged.
        account_id: Account for the instruments endpoint; defaults to
            ``OANDA_ACCOUNT_ID``.
        timeout: Per-request timeout in seconds.
        opener: Injected for tests, so no test ever needs the network.
    """

    def __init__(self, environment: str | None = None, token: str | None = None,
                 account_id: str | None = None, timeout: float = 30.0,
                 opener: Callable[..., Any] | None = None) -> None:
        self.host = resolve_host(environment)
        self._token = token or os.environ.get(ENV_TOKEN)
        self.account_id = account_id or os.environ.get(ENV_ACCOUNT)
        self.timeout = float(timeout)
        self._opener = opener or urllib.request.urlopen
        self._log = logging.getLogger(__name__)

    @property
    def has_token(self) -> bool:
        """True when a token is available, without revealing it."""
        return bool(self._token)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue one authenticated GET against an allowed endpoint.

        Raises:
            OandaError: If there is no token, the endpoint is not allowed, or
                the response is not JSON.
        """
        if not self._token:
            raise OandaError(
                f"no OANDA token: set {ENV_TOKEN} in the environment. "
                "Tokens are never read from config files or source.")
        check_path(path)
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.host}{path}{query}"
        self._log.debug("OANDA GET %s", path)
        request = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Bearer {self._token}",
            "Accept-Datetime-Format": "RFC3339",
            "Content-Type": "application/json",
        })
        with self._opener(request, timeout=self.timeout) as response:
            body = response.read()
        try:
            return json.loads(body.decode("utf8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OandaError(f"{path}: response was not JSON") from exc

    def candles(self, pair: str, granularity: str = "H1",
                start: dt.datetime | None = None, end: dt.datetime | None = None,
                count: int | None = None, price: str = "BAM") -> list[Candle]:
        """Fetch candles for one pair.

        Args:
            pair: Pair symbol in either spelling.
            granularity: OANDA granularity, e.g. ``H1`` or ``D``.
            start: Inclusive start instant.
            end: Exclusive end instant.
            count: Number of candles, when a range is not given.
            price: ``B``, ``A``, ``M`` or any combination; ``BAM`` by default.

        Returns:
            The parsed candles, oldest first.
        """
        params: dict[str, Any] = {"granularity": granularity, "price": price}
        if start is not None:
            params["from"] = start.astimezone(dt.timezone.utc).isoformat()
        if end is not None:
            params["to"] = end.astimezone(dt.timezone.utc).isoformat()
        if count is not None:
            params["count"] = int(count)
        payload = self.get(f"/v3/instruments/{instrument_name(pair)}/candles", params)
        return [Candle.from_payload(c) for c in payload.get("candles", [])]

    def instruments(self) -> list[dict[str, Any]]:
        """Fetch instrument metadata, the authoritative per-pair precision.

        Raises:
            OandaError: If no account id is available.
        """
        if not self.account_id:
            raise OandaError(
                f"no OANDA account id: set {ENV_ACCOUNT} to use the instruments "
                "endpoint")
        payload = self.get(f"/v3/accounts/{self.account_id}/instruments")
        return list(payload.get("instruments", []))


@dataclass(slots=True)
class CrossCheckResult:
    """Outcome of comparing Dukascopy hourly bars against OANDA H1 candles."""

    pair: str
    compared: int
    stats: dict[str, float]
    flagged: list[dict[str, Any]]
    threshold_pips: float

    @property
    def ok(self) -> bool:
        """True when no hour exceeded the configured mid-difference threshold."""
        return not self.flagged

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, as written into the cross-check report."""
        return {
            "pair": self.pair,
            "compared": self.compared,
            "threshold_pips": self.threshold_pips,
            "ok": self.ok,
            "stats": self.stats,
            "flagged": self.flagged,
        }


def cross_check(bars: Any, candles: list[Candle], pair: str,
                max_mid_diff_pips: float = 1.0) -> CrossCheckResult:
    """Compare hourly bars resampled from ticks against OANDA H1 candles.

    Args:
        bars: Bar frame produced by :func:`fxlab.ingestion.bars.resample_ticks`
            at 1h, carrying ``ts``, ``mid_open``, ``bid_open`` and ``ask_open``.
        candles: OANDA candles for the same pair and period.
        pair: Pair symbol, used for the pip size.
        max_mid_diff_pips: Hours whose mid differs by more than this are
            flagged.

    Returns:
        A :class:`CrossCheckResult`.

    Calibration matters here. Dukascopy is an ECN feed and OANDA is retail, so
    the Dukascopy bid sits **above** the OANDA bid and its ask **below** the
    OANDA ask, measured at about +0.7 and -0.6 pip on EURUSD. Mids agree to
    roughly 0.15 pip. A cross-check that flags that difference as an error is
    miscalibrated, which is why only the **mid** difference is thresholded and
    the bid and ask offsets are merely reported.
    """
    import numpy as np

    from fxlab.ingestion.pairs import pair_spec

    pip = pair_spec(pair).pip_size
    by_ts = {c.ts: c for c in candles}

    mid_diffs: list[float] = []
    bid_offsets: list[float] = []
    ask_offsets: list[float] = []
    flagged: list[dict[str, Any]] = []

    for row in bars.itertuples(index=False):
        ts = row.ts.to_pydatetime() if hasattr(row.ts, "to_pydatetime") else row.ts
        candle = by_ts.get(ts)
        if candle is None:
            continue
        mid_diff = (float(row.mid_open) - candle.mid.open) / pip
        mid_diffs.append(mid_diff)
        bid_offsets.append((float(row.bid_open) - candle.bid.open) / pip)
        ask_offsets.append((float(row.ask_open) - candle.ask.open) / pip)
        if abs(mid_diff) > max_mid_diff_pips:
            flagged.append({"ts": ts.isoformat(), "mid_diff_pips": mid_diff,
                            "fxlab_mid": float(row.mid_open),
                            "oanda_mid": candle.mid.open})

    def _describe(values: list[float], prefix: str) -> dict[str, float]:
        if not values:
            return {}
        array = np.asarray(values, dtype="float64")
        return {
            f"{prefix}_mean_pips": float(np.mean(array)),
            f"{prefix}_median_pips": float(np.median(array)),
            f"{prefix}_p95_abs_pips": float(np.percentile(np.abs(array), 95)),
            f"{prefix}_max_abs_pips": float(np.max(np.abs(array))),
        }

    stats: dict[str, float] = {}
    stats.update(_describe(mid_diffs, "mid_diff"))
    stats.update(_describe(bid_offsets, "bid_offset"))
    stats.update(_describe(ask_offsets, "ask_offset"))

    return CrossCheckResult(pair=pair, compared=len(mid_diffs), stats=stats,
                            flagged=flagged, threshold_pips=max_mid_diff_pips)
