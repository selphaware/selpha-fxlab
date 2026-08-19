"""The tick recorder and the feed interface it is written against."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from fxlab.ingestion.store import TICK_SCHEMA, read_ticks, store_hours
from fxlab.recorder import FakeFeed, Feed, FeedUnavailableError, IBFeed, Tick
from fxlab.recorder.ib import import_ib_async
from fxlab.recorder.recorder import TickRecorder

BASE = dt.datetime(2026, 7, 14, 13, tzinfo=dt.timezone.utc)


def make_ticks(count: int, pair: str = "EURUSD", step_seconds: int = 30):
    return [Tick(pair, BASE + dt.timedelta(seconds=i * step_seconds),
                 1.1 + i * 1e-5, 1.1 + 2e-5 + i * 1e-5, 1.0, 2.0)
            for i in range(count)]


def test_tick_requires_an_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        Tick("EURUSD", dt.datetime(2026, 7, 14, 13), 1.1, 1.2)


def test_tick_derives_mid_and_spread() -> None:
    tick = Tick("EURUSD", BASE, 1.0999, 1.1001)
    assert tick.mid == pytest.approx(1.1)
    assert tick.spread == pytest.approx(0.0002)


def test_fake_and_ib_feeds_both_satisfy_the_protocol() -> None:
    assert isinstance(FakeFeed([]), Feed)
    assert isinstance(IBFeed(), Feed)


def test_fake_feed_replays_only_the_subscribed_pairs() -> None:
    feed = FakeFeed(make_ticks(3) + make_ticks(2, pair="USDJPY"))

    async def drain():
        return [t async for t in feed.subscribe(["USDJPY"])]

    assert {t.pair for t in asyncio.run(drain())} == {"USDJPY"}


def test_recorder_writes_the_same_schema_as_ingestion(store_dir) -> None:
    import pyarrow.parquet as pq

    recorder = TickRecorder(store_dir, flush_seconds=1e9)
    stats = asyncio.run(recorder.record(FakeFeed(make_ticks(10)), ["EURUSD"]))
    assert stats.ticks_written == 10
    table = pq.read_table(stats.files_written[0])
    assert table.schema.equals(TICK_SCHEMA)


def test_recorded_rows_are_tagged_as_live(store_dir) -> None:
    recorder = TickRecorder(store_dir, flush_seconds=1e9)
    asyncio.run(recorder.record(FakeFeed(make_ticks(5)), ["EURUSD"]))
    assert set(read_ticks(store_dir)["source"]) == {"ib_live"}


def test_rotation_by_row_budget(store_dir) -> None:
    recorder = TickRecorder(store_dir, max_buffered=25, flush_seconds=1e9)
    stats = asyncio.run(recorder.record(FakeFeed(make_ticks(100, step_seconds=1)),
                                        ["EURUSD"]))
    assert len(stats.files_written) == 4
    assert len(read_ticks(store_dir)) == 100


def test_rotation_when_the_hour_turns_over(store_dir) -> None:
    recorder = TickRecorder(store_dir, max_buffered=10_000, flush_seconds=1e9)
    asyncio.run(recorder.record(FakeFeed(make_ticks(240, step_seconds=60)),
                                ["EURUSD"]))
    hours = sorted({h for _p, _d, h in store_hours(store_dir)})
    assert hours == [13, 14, 15, 16]


def test_rotation_on_the_flush_interval(store_dir) -> None:
    ticks = iter([0.0, 0.0, 100.0, 100.0, 200.0, 200.0, 300.0, 300.0])
    recorder = TickRecorder(store_dir, max_buffered=10_000, flush_seconds=60.0,
                            clock=lambda: next(ticks))
    recorder.add(make_ticks(1)[0])
    written = recorder.add(make_ticks(2)[1])
    assert written


def test_buffered_ticks_are_flushed_even_when_the_consumer_stops_early(
        store_dir) -> None:
    recorder = TickRecorder(store_dir, max_buffered=10_000, flush_seconds=1e9)
    stats = asyncio.run(recorder.record(FakeFeed(make_ticks(50)), ["EURUSD"],
                                        max_ticks=7))
    assert stats.ticks_received == 7
    assert stats.ticks_written == 7


def test_flush_leaves_no_partial_file(store_dir) -> None:
    recorder = TickRecorder(store_dir, flush_seconds=1e9)
    asyncio.run(recorder.record(FakeFeed(make_ticks(10)), ["EURUSD"]))
    assert list(store_dir.rglob("*.tmp")) == []


def test_replaying_the_store_round_trips(store_dir) -> None:
    recorder = TickRecorder(store_dir, flush_seconds=1e9)
    asyncio.run(recorder.record(FakeFeed(make_ticks(20)), ["EURUSD"]))
    replay = FakeFeed.from_store(store_dir, pair="EURUSD")
    assert len(replay.ticks) == 20
    assert replay.ticks[0].bid == pytest.approx(1.1)


def test_ib_feed_explains_itself_when_the_dependency_is_absent() -> None:
    try:
        import ib_async  # noqa: F401
    except ImportError:
        with pytest.raises(FeedUnavailableError, match="ib_async"):
            import_ib_async()
    else:
        assert import_ib_async() is not None


def test_ib_ticker_conversion_drops_one_sided_updates() -> None:
    class Ticker:
        bid = None
        ask = 1.2
        contract = None
        time = BASE

    assert IBFeed._to_tick(Ticker()) is None
