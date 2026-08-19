"""The pinned Parquet schema and the partitioned layout."""

from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from fxlab.ingestion.bi5 import decode_bi5
from fxlab.ingestion.store import (
    TICK_COLUMNS,
    TICK_SCHEMA,
    hour_file,
    partition_dir,
    read_ticks,
    store_hours,
    write_ticks,
)
from fxlab.ingestion.validation import deduplicate
from tests.conftest import RAW_DIR, hour_start, read_fixture

#: The contract, restated independently of the code under test.
CONTRACT_TYPES = {
    "pair": "large_string",
    "ts": "timestamp[us, tz=UTC]",
    "bid": "double",
    "ask": "double",
    "bid_volume": "double",
    "ask_volume": "double",
    "source": "large_string",
}


def _batch(name="EURUSD_2026-07-14_13h.bi5", pair="EURUSD",
           date="2026-07-14", hour=13):
    return deduplicate(decode_bi5(read_fixture(RAW_DIR, name), pair,
                                  hour_start(date, hour)))


def test_schema_matches_the_contract_exactly() -> None:
    got = {field.name: str(field.type) for field in TICK_SCHEMA}
    assert got == CONTRACT_TYPES
    assert list(TICK_COLUMNS) == list(CONTRACT_TYPES)


def test_written_parquet_carries_the_pinned_types(store_dir) -> None:
    write_ticks(store_dir, _batch(), "dukascopy")
    table = pq.read_table(partition_dir(store_dir, "EURUSD", "2026-07-14"))
    assert {f.name: str(f.type) for f in table.schema} == CONTRACT_TYPES


def test_layout_is_pair_then_date(store_dir) -> None:
    result = write_ticks(store_dir, _batch(), "dukascopy")
    assert result.path == hour_file(store_dir, "EURUSD", "2026-07-14", 13)
    assert result.path.parent.name == "date=2026-07-14"
    assert result.path.parent.parent.name == "pair=EURUSD"


def test_round_trip_preserves_every_tick(store_dir) -> None:
    batch = _batch()
    write_ticks(store_dir, batch, "dukascopy")
    frame = read_ticks(store_dir)
    assert len(frame) == len(batch)
    assert str(frame["ts"].dt.tz) == "UTC"
    assert frame["bid"].iloc[0] == pytest.approx(1.14461)
    assert frame["ask"].iloc[-1] == pytest.approx(1.14517)
    assert set(frame["source"]) == {"dukascopy"}


def test_reads_are_time_sorted_across_hours(store_dir) -> None:
    for hour in (14, 12, 13):
        write_ticks(store_dir,
                    _batch(f"EURUSD_2026-07-14_{hour:02d}h.bi5", hour=hour),
                    "dukascopy")
    frame = read_ticks(store_dir)
    assert len(frame) == 11_297 + 9_915 + 7_663
    assert frame["ts"].is_monotonic_increasing


def test_partition_filters_by_pair(store_dir) -> None:
    write_ticks(store_dir, _batch(), "dukascopy")
    write_ticks(store_dir, _batch("USDJPY_2026-07-14_13h.bi5", "USDJPY"),
                "dukascopy")
    assert set(read_ticks(store_dir, pair="USDJPY")["pair"]) == {"USDJPY"}
    assert len(read_ticks(store_dir)) == 9_915 + 11_781


def test_store_hours_reports_what_is_present(store_dir) -> None:
    write_ticks(store_dir, _batch(), "dukascopy")
    assert store_hours(store_dir) == [("EURUSD", "2026-07-14", 13)]


def test_reading_an_empty_store_returns_typed_emptiness(store_dir) -> None:
    frame = read_ticks(store_dir)
    assert len(frame) == 0
    assert list(frame.columns) == list(TICK_COLUMNS)


def test_write_leaves_no_temporary_file_behind(store_dir) -> None:
    write_ticks(store_dir, _batch(), "dukascopy")
    leftovers = list(partition_dir(store_dir, "EURUSD", "2026-07-14").glob("*.tmp"))
    assert leftovers == []
