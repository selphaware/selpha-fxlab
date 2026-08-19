"""A replayable feed, used by the tests and the gate.

Replaying real stored ticks rather than synthesising them keeps the recorder
honest: the same values that came off the wire go back through it.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import AsyncIterator, Iterable, Sequence

from fxlab.recorder.feed import Tick, tick_from_row


class FakeFeed:
    """Replays a fixed list of ticks as an async stream.

    Args:
        ticks: The ticks to replay, in the order they should arrive.
        delay: Optional pause between ticks, in seconds. Zero by default so
            tests do not spend real time.
    """

    name = "fake"

    def __init__(self, ticks: Iterable[Tick], delay: float = 0.0) -> None:
        self.ticks = list(ticks)
        self.delay = float(delay)

    @classmethod
    def from_store(cls, out_dir: pathlib.Path, pair: str | None = None,
                   limit: int | None = None, delay: float = 0.0) -> FakeFeed:
        """Build a feed by replaying ticks already in the Parquet store."""
        from fxlab.ingestion.store import read_ticks

        frame = read_ticks(out_dir, pair=pair)
        if limit is not None:
            frame = frame.head(limit)
        return cls([tick_from_row(row) for row in frame.itertuples(index=False)],
                   delay=delay)

    async def subscribe(self, pairs: Sequence[str]) -> AsyncIterator[Tick]:
        """Yield every tick whose pair is in ``pairs``."""
        wanted = {p.upper() for p in pairs}
        for tick in self.ticks:
            if tick.pair.upper() not in wanted:
                continue
            if self.delay:
                await asyncio.sleep(self.delay)
            yield tick
