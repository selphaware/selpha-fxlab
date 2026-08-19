"""Interactive Brokers feed adapter (stub until the account is approved).

This compiles, is unit-tested against the :class:`~fxlab.recorder.feed.Feed`
protocol, and raises a specific, actionable error rather than an ImportError
when ``ib_async`` is absent or no Gateway is running. A live connection is
explicitly **not** required for Phase 1 to be done; it is validated by hand
once IB approval and market data are in place.

Credentials never appear here or anywhere else in this repository. IB
authentication lives in the IBC configuration outside the repo.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Final, Sequence

from fxlab.recorder.feed import FeedUnavailableError, Tick

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: Default IB Gateway paper-trading endpoint. Configuration, not a secret.
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 4002
DEFAULT_CLIENT_ID: Final[int] = 17


def import_ib_async() -> Any:
    """Import ``ib_async``, or explain precisely what is missing.

    Raises:
        FeedUnavailableError: If the package is not installed.
    """
    try:
        import ib_async  # noqa: PLC0415 - optional dependency, imported on use
    except ImportError as exc:
        raise FeedUnavailableError(
            "ib_async is not installed in this environment. It is an optional "
            "dependency: Phase 1 runs and is judged entirely without it. Install "
            "it only when a Gateway is available to connect to."
        ) from exc
    return ib_async


class IBFeed:
    """Streams IDEALPRO bid/ask ticks from a running IB Gateway.

    Args:
        host: Gateway host.
        port: Gateway port; 4002 is paper, 4001 is live.
        client_id: IB client id, unique per connection.
        exchange: Venue for the FX contracts.
    """

    name = "ib_live"

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 client_id: int = DEFAULT_CLIENT_ID,
                 exchange: str = "IDEALPRO") -> None:
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.exchange = exchange
        self._ib: Any | None = None

    def connect(self) -> Any:
        """Connect to the Gateway.

        Raises:
            FeedUnavailableError: If ``ib_async`` is missing or no Gateway
                answers on the configured host and port.
        """
        ib_async = import_ib_async()
        ib = ib_async.IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id)
        except Exception as exc:  # noqa: BLE001 - any connect failure is the same story
            raise FeedUnavailableError(
                f"could not reach IB Gateway at {self.host}:{self.port}. Phase 1 "
                "does not require this connection; it is validated by hand once "
                "the account is approved and market data is subscribed."
            ) from exc
        self._ib = ib
        return ib

    def contract_for(self, pair: str) -> Any:
        """Build the IDEALPRO cash contract for ``pair``."""
        from fxlab.ingestion.pairs import pair_spec

        ib_async = import_ib_async()
        spec = pair_spec(pair)
        return ib_async.Forex(spec.name, exchange=self.exchange)

    async def subscribe(self, pairs: Sequence[str]) -> AsyncIterator[Tick]:
        """Yield ticks for ``pairs`` from a live Gateway connection.

        Raises:
            FeedUnavailableError: If the Gateway is unavailable.
        """
        ib = self._ib or self.connect()
        tickers = [ib.reqMktData(self.contract_for(pair), "", False, False)
                   for pair in pairs]
        _LOG.info("subscribed to %d IDEALPRO pair(s)", len(tickers))
        async for update in ib.pendingTickersEvent:
            for ticker in update:
                tick = self._to_tick(ticker)
                if tick is not None:
                    yield tick

    @staticmethod
    def _to_tick(ticker: Any) -> Tick | None:
        """Convert one ib_async ticker update into a :class:`Tick`.

        Returns ``None`` for updates with no usable two-sided quote, which the
        API emits routinely and which must not become rows of NaN.
        """
        import datetime as dt

        bid = getattr(ticker, "bid", None)
        ask = getattr(ticker, "ask", None)
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        contract = getattr(ticker, "contract", None)
        pair = getattr(contract, "localSymbol", None) or getattr(
            contract, "symbol", "")
        ts = getattr(ticker, "time", None) or dt.datetime.now(dt.timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        return Tick(
            pair=str(pair).replace(".", "").replace("/", ""),
            ts=ts, bid=float(bid), ask=float(ask),
            bid_volume=float(getattr(ticker, "bidSize", 0.0) or 0.0),
            ask_volume=float(getattr(ticker, "askSize", 0.0) or 0.0),
        )
