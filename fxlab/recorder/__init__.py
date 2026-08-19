"""Live tick recording against a feed interface.

The recorder writes the same tick schema and partition layout as ingestion,
tagged with its own ``source``, so recorded venue spreads can be read by the
same code that reads research ticks. The IB feed is a thin adapter; everything
else is exercised offline through :class:`~fxlab.recorder.fake.FakeFeed`.
"""

from __future__ import annotations

from fxlab.recorder.fake import FakeFeed
from fxlab.recorder.feed import Feed, FeedUnavailableError, Tick, tick_from_row
from fxlab.recorder.ib import IBFeed
from fxlab.recorder.recorder import RecorderStats, TickRecorder

__all__ = [
    "FakeFeed",
    "Feed",
    "FeedUnavailableError",
    "IBFeed",
    "RecorderStats",
    "Tick",
    "TickRecorder",
    "tick_from_row",
]
