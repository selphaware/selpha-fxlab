"""The feed protocol and the tick value type the recorder consumes.

The recorder is written against this protocol, never against a broker API, so
the whole recording path can be exercised offline with a replayable fake feed.
The live IB implementation is then the only untested-by-CI piece, and it is a
thin adapter rather than a tangle.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True, slots=True)
class Tick:
    """One two-sided quote from a live or replayed feed."""

    pair: str
    ts: dt.datetime
    bid: float
    ask: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError(
                f"{self.pair}: tick timestamps must be tz-aware UTC, got {self.ts!r}")

    @property
    def mid(self) -> float:
        """The mid price."""
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        """The quoted spread in price units."""
        return self.ask - self.bid


@runtime_checkable
class Feed(Protocol):
    """A source of live or replayed ticks."""

    name: str

    def subscribe(self, pairs: Sequence[str]) -> AsyncIterator[Tick]:
        """Return an async iterator of ticks for ``pairs``."""
        ...


class FeedUnavailableError(RuntimeError):
    """Raised when a feed cannot be used in this environment."""


def tick_from_row(row: Any, pair: str | None = None) -> Tick:
    """Build a :class:`Tick` from a stored tick row."""
    ts = row.ts.to_pydatetime() if hasattr(row.ts, "to_pydatetime") else row.ts
    return Tick(
        pair=str(pair or getattr(row, "pair")),
        ts=ts,
        bid=float(row.bid),
        ask=float(row.ask),
        bid_volume=float(getattr(row, "bid_volume", 0.0) or 0.0),
        ask_volume=float(getattr(row, "ask_volume", 0.0) or 0.0),
    )
