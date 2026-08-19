"""Tick recorder: writes a live feed into the same store the ingest writes.

Same Arrow schema, same partition layout, different ``source`` tag. That is
deliberate: a recorded spread distribution is only useful for calibrating the
cost model if it can be read with exactly the code that reads research ticks.

Two properties matter for something that runs unattended for weeks:

* **Rotation.** Buffers are flushed per pair-day-hour and per row budget, so no
  single file grows without bound and a day being recorded stays readable.
* **Crash-safe flush.** Every flush writes a complete Parquet file to a
  temporary name and renames it into place. A killed process can lose the ticks
  still in memory; it cannot leave a half-written file that later looks whole.
"""

from __future__ import annotations

import datetime as dt
import logging
import pathlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Final, Sequence

import numpy as np

from fxlab.ingestion.store import partition_dir, tick_table, write_table_atomic
from fxlab.ingestion.validation import TickBatch
from fxlab.recorder.feed import Feed, Tick

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: Provenance tag written on every recorded row.
DEFAULT_SOURCE: Final[str] = "ib_live"


@dataclass(slots=True)
class RecorderStats:
    """What one recording session did."""

    ticks_received: int = 0
    ticks_written: int = 0
    files_written: list[str] = field(default_factory=list)
    pairs: set[str] = field(default_factory=set)


def batch_from_ticks(pair: str, date_str: str, hour: int,
                     ticks: list[Tick]) -> TickBatch:
    """Pack buffered ticks into the column layout the store writes."""
    ordered = sorted(ticks, key=lambda t: t.ts)
    ts_us = np.array(
        [int(t.ts.astimezone(dt.timezone.utc).timestamp() * 1_000_000)
         for t in ordered], dtype="int64")
    hour_start = dt.datetime.fromisoformat(f"{date_str}T{hour:02d}:00:00+00:00")
    return TickBatch(
        pair=pair, hour_start=hour_start, ts_us=ts_us,
        bid=np.array([t.bid for t in ordered], dtype="float64"),
        ask=np.array([t.ask for t in ordered], dtype="float64"),
        bid_volume=np.array([t.bid_volume for t in ordered], dtype="float64"),
        ask_volume=np.array([t.ask_volume for t in ordered], dtype="float64"),
        decoded_ticks=len(ordered), duplicates_dropped=0,
        compressed_bytes=0, decoded_bytes=0)


class TickRecorder:
    """Buffers ticks by pair-day-hour and flushes them to Parquet.

    Args:
        out_dir: Store root, the same shape ingestion writes.
        source: Provenance tag for every row.
        max_buffered: Flush once this many ticks are held in total.
        flush_seconds: Flush at least this often, even when quiet.
        clock: Monotonic clock, injected for tests.
    """

    def __init__(self, out_dir: pathlib.Path, source: str = DEFAULT_SOURCE,
                 max_buffered: int = 50_000, flush_seconds: float = 60.0,
                 clock: Callable[[], float] | None = None) -> None:
        self.out_dir = pathlib.Path(out_dir)
        self.source = source
        self.max_buffered = int(max_buffered)
        self.flush_seconds = float(flush_seconds)
        self._clock = clock or time.monotonic
        self._buffers: dict[tuple[str, str, int], list[Tick]] = defaultdict(list)
        self._sequence: dict[tuple[str, str, int], int] = defaultdict(int)
        self._buffered = 0
        self._last_flush = self._clock()
        self.stats = RecorderStats()

    @staticmethod
    def partition_key(tick: Tick) -> tuple[str, str, int]:
        """Partition key for one tick: pair, UTC date, UTC hour."""
        utc = tick.ts.astimezone(dt.timezone.utc)
        return (tick.pair, utc.date().isoformat(), utc.hour)

    def add(self, tick: Tick) -> list[pathlib.Path]:
        """Buffer one tick, flushing if a rotation trigger fires."""
        key = self.partition_key(tick)
        self._buffers[key].append(tick)
        self._buffered += 1
        self.stats.ticks_received += 1
        self.stats.pairs.add(tick.pair)

        written: list[pathlib.Path] = []
        stale = [k for k in self._buffers
                 if k[0] == key[0] and (k[1], k[2]) != (key[1], key[2])]
        for old in stale:
            written.extend(self._flush_key(old))
        if self._buffered >= self.max_buffered:
            written.extend(self.flush())
        elif self._clock() - self._last_flush >= self.flush_seconds:
            written.extend(self.flush())
        return written

    def _flush_key(self, key: tuple[str, str, int]) -> list[pathlib.Path]:
        """Write one buffer out and clear it."""
        ticks = self._buffers.get(key)
        if not ticks:
            self._buffers.pop(key, None)
            return []
        pair, date_str, hour = key
        batch = batch_from_ticks(pair, date_str, hour, ticks)
        self._sequence[key] += 1
        name = f"{pair}_{date_str}_{hour:02d}h_{self._sequence[key]:04d}.parquet"
        path = partition_dir(self.out_dir, pair, date_str) / name
        result = write_table_atomic(tick_table(batch, self.source), path)
        self._buffered -= len(ticks)
        self._buffers.pop(key, None)
        self.stats.ticks_written += result.rows
        self.stats.files_written.append(str(path))
        _LOG.info("recorder flushed %d tick(s) to %s", result.rows, path)
        return [path]

    def flush(self) -> list[pathlib.Path]:
        """Flush every buffer. Safe at any time, including on shutdown."""
        written: list[pathlib.Path] = []
        for key in list(self._buffers):
            written.extend(self._flush_key(key))
        self._last_flush = self._clock()
        return written

    async def record(self, feed: Feed, pairs: Sequence[str],
                     max_ticks: int | None = None) -> RecorderStats:
        """Consume ``feed`` until it ends or ``max_ticks`` have arrived.

        Args:
            feed: Any :class:`~fxlab.recorder.feed.Feed`.
            pairs: Pairs to subscribe to.
            max_ticks: Stop after this many ticks; unbounded when omitted.

        Returns:
            The session statistics. Buffers are always flushed, including when
            the consumer stops early or the feed raises.
        """
        try:
            async for tick in feed.subscribe(pairs):
                self.add(tick)
                if max_ticks is not None and self.stats.ticks_received >= max_ticks:
                    break
        finally:
            self.flush()
        return self.stats
