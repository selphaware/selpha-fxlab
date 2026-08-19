"""Bar resampling and the bar-open timestamp convention."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fxlab.ingestion.bars import (
    BAR_COLUMNS,
    BAR_SCHEMA,
    TimeframeError,
    bars_path,
    offset_alias,
    resample_ticks,
    write_bars,
)


def _ticks(rows):
    """Build a tick frame from (offset_seconds, bid, ask) triples."""
    base = dt.datetime(2026, 7, 14, 13, tzinfo=dt.timezone.utc)
    return pd.DataFrame({
        "pair": ["EURUSD"] * len(rows),
        "ts": [base + dt.timedelta(seconds=s) for s, _b, _a in rows],
        "bid": [b for _s, b, _a in rows],
        "ask": [a for _s, _b, a in rows],
        "bid_volume": [1.0] * len(rows),
        "ask_volume": [1.0] * len(rows),
        "source": ["dukascopy"] * len(rows),
    })


def test_timeframe_spellings_normalise() -> None:
    assert offset_alias("1m") == offset_alias("1min") == "1min"
    assert offset_alias("H1") == offset_alias("1h") == "1h"
    with pytest.raises(TimeframeError):
        offset_alias("fortnightly")


def test_bar_timestamp_is_the_bar_open() -> None:
    bars = resample_ticks(_ticks([(0, 1.0, 1.2), (59, 2.0, 2.2)]), "1min").frame
    assert len(bars) == 1
    assert bars["ts"].iloc[0] == pd.Timestamp("2026-07-14T13:00:00+00:00")


def test_bar_covers_open_inclusive_to_next_open_exclusive() -> None:
    # A tick at exactly 60s belongs to the second bar, not the first.
    result = resample_ticks(_ticks([(0, 1.0, 1.2), (60, 2.0, 2.2)]), "1min")
    assert list(result.frame["ts"]) == [
        pd.Timestamp("2026-07-14T13:00:00+00:00"),
        pd.Timestamp("2026-07-14T13:01:00+00:00")]
    assert list(result.frame["tick_count"]) == [1, 1]


def test_ohlc_uses_first_and_last_by_time() -> None:
    bars = resample_ticks(
        _ticks([(0, 1.0, 1.1), (10, 3.0, 3.1), (20, 0.5, 0.6), (30, 2.0, 2.1)]),
        "1min").frame
    row = bars.iloc[0]
    assert (row["bid_open"], row["bid_close"]) == (1.0, 2.0)
    assert (row["bid_high"], row["bid_low"]) == (3.0, 0.5)
    assert row["mid_open"] == pytest.approx(1.05)
    assert row["tick_count"] == 4


def test_spread_statistics_are_reported_per_bar() -> None:
    bars = resample_ticks(_ticks([(0, 1.0, 1.2), (10, 1.0, 1.1)]), "1min").frame
    assert bars["spread_mean"].iloc[0] == pytest.approx(0.15)
    assert bars["spread_max"].iloc[0] == pytest.approx(0.2)


def test_empty_bins_are_dropped_and_listed_never_left_as_nan() -> None:
    result = resample_ticks(_ticks([(0, 1.0, 1.2), (180, 2.0, 2.2)]), "1min")
    assert len(result.frame) == 2
    assert result.empty_bins == ["2026-07-14T13:01:00+00:00",
                                 "2026-07-14T13:02:00+00:00"]
    assert not result.frame.isna().any().any()


def test_bar_frame_matches_the_pinned_schema_and_order() -> None:
    result = resample_ticks(_ticks([(0, 1.0, 1.2)]), "1min")
    assert list(result.frame.columns) == list(BAR_COLUMNS)
    assert str(BAR_SCHEMA.field("ts").type) == "timestamp[us, tz=UTC]"
    assert str(BAR_SCHEMA.field("tick_count").type) == "int64"


def test_written_bars_round_trip(tmp_path) -> None:
    import pyarrow.parquet as pq

    result = resample_ticks(_ticks([(0, 1.0, 1.2), (61, 2.0, 2.2)]), "1min")
    path = write_bars(tmp_path, result)
    assert path == bars_path(tmp_path, "EURUSD", "1min")
    table = pq.read_table(path)
    assert table.schema.equals(BAR_SCHEMA)
    assert table.num_rows == 2


def test_no_ticks_produces_a_typed_empty_frame() -> None:
    result = resample_ticks(_ticks([]).iloc[0:0], "1min", pair="EURUSD")
    assert len(result) == 0
    assert list(result.frame.columns) == list(BAR_COLUMNS)
