"""Incremental bar building (SPEC2 prerequisite P0-B).

Two claims are tested here, and they are the two the design rests on:

1. **Rolling up is exact.** Bars aggregated from 1m bars are the same bars
   resampling the ticks directly would have produced -- including
   ``spread_mean``, which is a tick-weighted mean and not a mean of means.
2. **Only what changed is rebuilt.** A second run over an unchanged store
   resamples nothing, and a day that gains an hour is rebuilt without the rest
   of the store being read.

The second claim is the whole point: T2a stores roughly 764,000 hours, and a
bar builder that re-reads the store per run does not finish.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from fxlab.ingestion import store as store_mod
from fxlab.ingestion.bars import (
    BAR_SCHEMA,
    aggregate_bars,
    alias_seconds,
    bars_path,
    build_bars_incremental,
    load_bar_state,
    resample_ticks,
    splice_bars,
    tick_day_signatures,
)
from fxlab.ingestion.store import write_ticks
from fxlab.ingestion.validation import TickBatch

PAIR = "EURUSD"


def _batch(day: str, hour: int, count: int = 90) -> TickBatch:
    """One hour of synthetic ticks with a moving price and a moving spread."""
    start = dt.datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00")
    base_us = int(start.timestamp() * 1_000_000)
    step_us = 3_600_000_000 // count
    ts = base_us + np.arange(count, dtype=np.int64) * step_us
    rng = np.random.default_rng(hour * 1000 + int(day.replace("-", "")) % 997)
    bid = 1.10 + rng.normal(0.0, 0.0005, count).cumsum()
    spread = 0.00005 + 0.00004 * rng.random(count)
    return TickBatch(
        pair=PAIR, hour_start=start, ts_us=ts, bid=bid, ask=bid + spread,
        bid_volume=np.ones(count), ask_volume=np.ones(count),
        decoded_ticks=count, duplicates_dropped=0,
        compressed_bytes=0, decoded_bytes=0)


def _tick_frame(batches):
    """The same ticks as a DataFrame, in the stored column order."""
    frames = []
    for batch in batches:
        frames.append(pd.DataFrame({
            "pair": [batch.pair] * len(batch),
            "ts": pd.to_datetime(batch.ts_us, unit="us", utc=True),
            "bid": batch.bid, "ask": batch.ask,
            "bid_volume": batch.bid_volume, "ask_volume": batch.ask_volume,
            "source": ["dukascopy"] * len(batch),
        }))
    return pd.concat(frames, ignore_index=True).sort_values(
        "ts", kind="stable").reset_index(drop=True)


def _store(tmp_path, hours):
    """Write ``(day, hour)`` pairs into a store and return its root."""
    root = tmp_path / "data"
    for day, hour in hours:
        write_ticks(root, _batch(day, hour), "dukascopy")
    return root


# --------------------------------------------------------------------------- #
# Roll-up is exact
# --------------------------------------------------------------------------- #

def test_alias_seconds_sizes_every_research_timeframe() -> None:
    assert alias_seconds("1min") == 60
    assert alias_seconds("5min") == 300
    assert alias_seconds("30min") == 1800
    assert alias_seconds("1h") == 3600
    assert alias_seconds("4h") == 14400
    assert alias_seconds("1D") == 86400


@pytest.mark.parametrize("timeframe", ["5min", "30min", "1h", "4h", "1D"])
def test_rolling_up_from_one_minute_equals_resampling_the_ticks(timeframe) -> None:
    ticks = _tick_frame([_batch("2024-06-03", h) for h in (9, 10, 11, 12)])
    minutes = resample_ticks(ticks, "1min", pair=PAIR)

    rolled = aggregate_bars(minutes.frame, timeframe, pair=PAIR).frame
    direct = resample_ticks(ticks, timeframe, pair=PAIR).frame

    assert len(rolled) == len(direct) > 0
    assert list(rolled["ts"]) == list(direct["ts"])
    assert list(rolled["tick_count"]) == list(direct["tick_count"])
    for column in ("bid_open", "bid_high", "bid_low", "bid_close",
                   "ask_open", "ask_high", "ask_low", "ask_close",
                   "mid_open", "mid_high", "mid_low", "mid_close",
                   "spread_max"):
        assert rolled[column].to_numpy() == pytest.approx(direct[column].to_numpy())
    # A mean of means would be wrong here whenever bins hold unequal tick
    # counts, which is why this is weighted rather than averaged.
    assert rolled["spread_mean"].to_numpy() == pytest.approx(
        direct["spread_mean"].to_numpy(), rel=1e-12)


def test_rolling_up_an_empty_frame_is_typed_and_empty() -> None:
    empty = resample_ticks(_tick_frame([_batch("2024-06-03", 9)]).iloc[0:0],
                           "1min", pair=PAIR)
    assert len(aggregate_bars(empty.frame, "1h", pair=PAIR)) == 0


# --------------------------------------------------------------------------- #
# Only what changed is rebuilt
# --------------------------------------------------------------------------- #

def test_first_build_produces_every_requested_timeframe(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-03", 10),
                             ("2024-06-04", 9)])
    updates = build_bars_incremental(root, PAIR, ["1m", "5m", "1h", "1d"])

    assert {u.timeframe for u in updates} == {"1min", "5min", "1h", "1D"}
    for update in updates:
        assert update.dates_built == 2
        assert update.path.is_file()
        assert pq.read_table(update.path).schema.equals(BAR_SCHEMA)
    daily = pq.read_table(bars_path(root, PAIR, "1d")).to_pandas()
    assert len(daily) == 2  # one bar per UTC day, and days never straddle


def test_a_second_run_over_an_unchanged_store_builds_nothing(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-04", 9)])
    build_bars_incremental(root, PAIR, ["1m", "1h"])
    assert build_bars_incremental(root, PAIR, ["1m", "1h"]) == []


def test_an_unchanged_store_is_not_even_read(tmp_path, monkeypatch) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-04", 9)])
    build_bars_incremental(root, PAIR, ["1m", "1h"])

    def explode(*_a, **_k):
        raise AssertionError("an up-to-date store must not be re-read")

    monkeypatch.setattr(store_mod, "read_ticks", explode)
    assert build_bars_incremental(root, PAIR, ["1m", "1h"]) == []


def test_a_new_day_rebuilds_only_that_day(tmp_path, monkeypatch) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-03", 10)])
    build_bars_incremental(root, PAIR, ["1m", "1h"])
    write_ticks(root, _batch("2024-06-04", 9), "dukascopy")

    read_dates: list[list[str]] = []
    real = store_mod.read_ticks

    def spy(out_dir, pair=None, dates=None):
        read_dates.append(list(dates or []))
        return real(out_dir, pair=pair, dates=dates)

    monkeypatch.setattr(store_mod, "read_ticks", spy)
    updates = build_bars_incremental(root, PAIR, ["1m", "1h"])

    assert read_dates == [["2024-06-04"]]
    assert all(u.dates_built == 1 for u in updates)
    hourly = pq.read_table(bars_path(root, PAIR, "1h")).to_pandas()
    assert len(hourly) == 3  # the two already-built hours survived the splice


def test_a_day_that_gains_an_hour_is_rebuilt_not_duplicated(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9)])
    build_bars_incremental(root, PAIR, ["1h", "1d"])
    assert len(pq.read_table(bars_path(root, PAIR, "1h"))) == 1

    write_ticks(root, _batch("2024-06-03", 10), "dukascopy")
    updates = build_bars_incremental(root, PAIR, ["1h", "1d"])

    assert [u.dates_built for u in updates] == [1, 1]
    assert len(pq.read_table(bars_path(root, PAIR, "1h"))) == 2
    daily = pq.read_table(bars_path(root, PAIR, "1d")).to_pandas()
    assert len(daily) == 1 and daily["tick_count"].iloc[0] == 180


def test_a_deleted_bar_table_is_rebuilt_whatever_the_state_says(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9)])
    build_bars_incremental(root, PAIR, ["1h"])
    bars_path(root, PAIR, "1h").unlink()

    updates = build_bars_incremental(root, PAIR, ["1h"])
    assert [u.dates_built for u in updates] == [1]
    assert bars_path(root, PAIR, "1h").is_file()


def test_the_state_file_records_what_was_folded_in(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9)])
    build_bars_incremental(root, PAIR, ["1h"])
    state = load_bar_state(root, PAIR)
    assert set(state) == {"1h"}
    assert state["1h"]["2024-06-03"] == tick_day_signatures(
        root, PAIR)["2024-06-03"]


def test_bars_stay_time_sorted_when_history_arrives_backwards(tmp_path) -> None:
    """T2a ingests newest month first, so older bars are spliced in behind."""
    root = _store(tmp_path, [("2024-06-10", 9)])
    build_bars_incremental(root, PAIR, ["1h"])
    write_ticks(root, _batch("2024-06-03", 9), "dukascopy")
    build_bars_incremental(root, PAIR, ["1h"])

    stored = pq.read_table(bars_path(root, PAIR, "1h")).to_pandas()
    assert list(stored["ts"]) == sorted(stored["ts"])


def test_splice_replaces_a_date_rather_than_appending_it(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-04", 9)])
    build_bars_incremental(root, PAIR, ["1h"])
    path = bars_path(root, PAIR, "1h")

    replacement = resample_ticks(_tick_frame([_batch("2024-06-03", 9, 30)]),
                                 "1h", pair=PAIR)
    from fxlab.ingestion.bars import bar_table

    written, total = splice_bars(path, bar_table(replacement.frame),
                                 ["2024-06-03"])
    assert (written, total) == (1, 2)
    stored = pq.read_table(path).to_pandas().set_index("ts")
    assert stored["tick_count"].loc["2024-06-03T09:00:00+00:00"] == 30


def test_signatures_are_a_listing_not_a_read(tmp_path) -> None:
    root = _store(tmp_path, [("2024-06-03", 9), ("2024-06-03", 10)])
    signatures = tick_day_signatures(root, PAIR)
    assert set(signatures) == {"2024-06-03"}
    assert signatures["2024-06-03"].startswith("2:")
    assert tick_day_signatures(root, PAIR, ["2024-06-04"]) == {}
