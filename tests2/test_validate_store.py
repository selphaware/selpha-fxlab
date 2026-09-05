"""The offline re-validation pass: one test per way a stored hour can be wrong.

Every check here is written against a file this test builds and then damages.
Validating a store that happens to be clean proves the pass runs, not that it
discriminates -- the same distinction SPEC2 draws about the walk-forward known
answer, which is deliberately four different wrong numbers rather than one
right one.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research import validate_store as vs


def _write(path: pathlib.Path, rows: list[tuple[str, float, float]], *,
           pair: str = "EURUSD",
           schema: pa.Schema | None = None) -> pathlib.Path:
    """Write a tick file from ``(timestamp, bid, ask)`` triples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamps = [dt.datetime.fromisoformat(t) for t, _, _ in rows]
    table = pa.table({
        "pair": pa.array([pair] * len(rows), type=pa.large_string()),
        "ts": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
        "bid": pa.array([b for _, b, _ in rows], type=pa.float64()),
        "ask": pa.array([a for _, _, a in rows], type=pa.float64()),
        "bid_volume": pa.array([1.0] * len(rows), type=pa.float64()),
        "ask_volume": pa.array([1.0] * len(rows), type=pa.float64()),
        "source": pa.array(["dukascopy"] * len(rows), type=pa.large_string()),
    }, schema=schema)
    pq.write_table(table, path)
    return path


def _record(**overrides: Any) -> dict[str, Any]:
    """A manifest hour record for a clean Wednesday hour."""
    row = {"pair": "EURUSD", "date": "2019-06-12", "hour": 13,
           "status": "ok", "written_ticks": 2}
    row.update(overrides)
    return row


CLEAN = [("2019-06-12T13:00:01+00:00", 1.1300, 1.1301),
         ("2019-06-12T13:59:59+00:00", 1.1310, 1.1311)]


def test_a_clean_hour_reports_nothing(tmp_path: pathlib.Path) -> None:
    """The baseline every other test perturbs by one thing."""
    path = _write(tmp_path / "h.parquet", CLEAN)
    assert vs.validate_hour(path, _record()) == []


def test_a_missing_file_is_reported_not_raised(
        tmp_path: pathlib.Path) -> None:
    """A manifest pointing at nothing is a finding, not a crash."""
    failures = vs.validate_hour(tmp_path / "absent.parquet", _record())
    assert [f["kind"] for f in failures] == [vs.FILE_MISSING]


def test_a_row_count_that_disagrees_with_the_manifest(
        tmp_path: pathlib.Path) -> None:
    """The one check that compares the file against the record, not a rule."""
    path = _write(tmp_path / "h.parquet", CLEAN)
    failures = vs.validate_hour(path, _record(written_ticks=99))
    assert [f["kind"] for f in failures] == [vs.ROW_COUNT_MISMATCH]
    assert "99" in failures[0]["detail"]


def test_a_crossed_quote_is_caught(tmp_path: pathlib.Path) -> None:
    """bid > ask, the Phase 1 rejection, re-checked from the other side."""
    rows = [("2019-06-12T13:00:01+00:00", 1.1305, 1.1301),
            ("2019-06-12T13:59:59+00:00", 1.1310, 1.1311)]
    path = _write(tmp_path / "h.parquet", rows)
    failures = vs.validate_hour(path, _record())
    assert [f["kind"] for f in failures] == [vs.CROSSED_QUOTE]


def test_a_non_positive_price_is_caught(tmp_path: pathlib.Path) -> None:
    """Zero counts, not only negative: a zero price is not a price."""
    rows = [("2019-06-12T13:00:01+00:00", 0.0, 1.1301),
            ("2019-06-12T13:59:59+00:00", 1.1310, 1.1311)]
    path = _write(tmp_path / "h.parquet", rows)
    kinds = [f["kind"] for f in vs.validate_hour(path, _record())]
    assert vs.NON_POSITIVE_PRICE in kinds


def test_out_of_order_timestamps_are_caught(tmp_path: pathlib.Path) -> None:
    """A resampler that assumed sorted input would produce silent nonsense."""
    rows = [("2019-06-12T13:59:59+00:00", 1.1310, 1.1311),
            ("2019-06-12T13:00:01+00:00", 1.1300, 1.1301)]
    path = _write(tmp_path / "h.parquet", rows)
    kinds = [f["kind"] for f in vs.validate_hour(path, _record())]
    assert vs.TS_NOT_MONOTONIC in kinds


def test_a_tick_belonging_to_another_hour_is_caught(
        tmp_path: pathlib.Path) -> None:
    """The file is named for an hour; its contents must be inside it."""
    rows = [("2019-06-12T13:00:01+00:00", 1.1300, 1.1301),
            ("2019-06-12T14:00:00+00:00", 1.1310, 1.1311)]
    path = _write(tmp_path / "h.parquet", rows)
    kinds = [f["kind"] for f in vs.validate_hour(path, _record())]
    assert vs.TS_OUT_OF_HOUR in kinds


def test_the_hour_boundary_is_half_open(tmp_path: pathlib.Path) -> None:
    """``[open, open+1h)``: the first instant belongs, the last does not."""
    rows = [("2019-06-12T13:00:00+00:00", 1.1300, 1.1301),
            ("2019-06-12T13:59:59.999999+00:00", 1.1310, 1.1311)]
    path = _write(tmp_path / "h.parquet", rows)
    assert vs.validate_hour(path, _record()) == []


def test_an_hour_the_derived_week_calls_shut_is_caught(
        tmp_path: pathlib.Path) -> None:
    """Stored ticks on a Saturday, which no derived week calls open."""
    rows = [("2019-06-15T13:00:01+00:00", 1.1300, 1.1301)]
    path = _write(tmp_path / "h.parquet", rows)
    record = _record(date="2019-06-15", written_ticks=1)
    kinds = [f["kind"] for f in vs.validate_hour(path, record)]
    assert vs.CLOSED_MARKET_TICK in kinds


def test_a_renamed_column_is_schema_drift(tmp_path: pathlib.Path) -> None:
    """And stops the pass before it reads a column that is not there."""
    path = tmp_path / "h.parquet"
    pq.write_table(pa.table({"pair": pa.array(["EURUSD"],
                                              type=pa.large_string())}), path)
    failures = vs.validate_hour(path, _record(written_ticks=1))
    assert [f["kind"] for f in failures] == [vs.SCHEMA_DRIFT]


def test_a_narrowed_column_type_is_schema_drift(
        tmp_path: pathlib.Path) -> None:
    """float32 prices would round a fifth-decimal quote away in silence."""
    schema = pa.schema([
        ("pair", pa.large_string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("bid", pa.float32()),
        ("ask", pa.float32()),
        ("bid_volume", pa.float64()),
        ("ask_volume", pa.float64()),
        ("source", pa.large_string()),
    ])
    path = _write(tmp_path / "h.parquet", CLEAN, schema=schema)
    failures = vs.validate_hour(path, _record())
    assert {f["kind"] for f in failures} == {vs.SCHEMA_DRIFT}
    assert any("bid" in f["detail"] for f in failures)


def test_a_naive_timestamp_column_is_schema_drift(
        tmp_path: pathlib.Path) -> None:
    """A timestamp without a zone is the bug that moves a whole store."""
    schema = pa.schema([
        ("pair", pa.large_string()),
        ("ts", pa.timestamp("us")),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("bid_volume", pa.float64()),
        ("ask_volume", pa.float64()),
        ("source", pa.large_string()),
    ])
    path = _write(tmp_path / "h.parquet", CLEAN, schema=schema)
    kinds = {f["kind"] for f in vs.validate_hour(path, _record())}
    assert kinds == {vs.SCHEMA_DRIFT}


def test_an_extra_column_is_schema_drift(tmp_path: pathlib.Path) -> None:
    """"No extra columns" is part of the contract, not a formatting nicety."""
    path = tmp_path / "h.parquet"
    table = pa.table({
        "pair": pa.array(["EURUSD"], type=pa.large_string()),
        "ts": pa.array([dt.datetime(2019, 6, 12, 13, 0,
                                    tzinfo=dt.timezone.utc)],
                       type=pa.timestamp("us", tz="UTC")),
        "bid": pa.array([1.13], type=pa.float64()),
        "ask": pa.array([1.1301], type=pa.float64()),
        "bid_volume": pa.array([1.0], type=pa.float64()),
        "ask_volume": pa.array([1.0], type=pa.float64()),
        "source": pa.array(["dukascopy"], type=pa.large_string()),
        "extra": pa.array([1], type=pa.int64()),
    })
    pq.write_table(table, path)
    failures = vs.validate_hour(path, _record(written_ticks=1))
    assert [f["kind"] for f in failures] == [vs.SCHEMA_DRIFT]


def test_several_faults_are_all_reported(tmp_path: pathlib.Path) -> None:
    """A pass that stopped at the first fault would understate the damage."""
    rows = [("2019-06-12T13:00:01+00:00", 1.1305, 1.1301),
            ("2019-06-12T14:30:00+00:00", -1.0, 1.1311)]
    path = _write(tmp_path / "h.parquet", rows)
    kinds = {f["kind"] for f in vs.validate_hour(path, _record())}
    assert kinds == {vs.CROSSED_QUOTE, vs.NON_POSITIVE_PRICE,
                     vs.TS_OUT_OF_HOUR}


def test_month_labels_span_the_window_inclusive() -> None:
    """Both endpoints belong, and a year boundary is not a break."""
    labels = vs.month_labels(dt.date(2019, 11, 3), dt.date(2020, 2, 28))
    assert labels == ["2019-11", "2019-12", "2020-01", "2020-02"]


def test_the_summary_folds_kinds_and_pairs() -> None:
    """The numbers a report states, from the checkpoint rows."""
    rows = [
        {"pair": "EURUSD", "month": "2019-06", "hours": 10, "ticks": 100,
         "failures": 0, "by_kind": {}, "details": [], "shard": True},
        {"pair": "EURUSD", "month": "2019-07", "hours": 5, "ticks": 50,
         "failures": 2, "by_kind": {vs.CROSSED_QUOTE: 2},
         "details": [{"kind": vs.CROSSED_QUOTE, "pair": "EURUSD",
                      "date": "2019-07-01", "hour": 3, "detail": "x"}],
         "shard": True},
    ]
    summary = vs.summarise(rows)
    assert summary["hours_validated"] == 15
    assert summary["ticks_validated"] == 150
    assert summary["failures"] == 2
    assert summary["by_kind"] == {vs.CROSSED_QUOTE: 2}
    assert summary["by_pair"]["EURUSD"]["failures"] == 2


def test_a_truncated_checkpoint_line_is_dropped_not_fatal(
        tmp_path: pathlib.Path) -> None:
    """A pass killed mid-write costs one pair-month, not the whole run."""
    path = tmp_path / vs.VALIDATION_NAME
    path.write_text('{"pair": "EURUSD", "month": "2019-06", "hours": 1}\n'
                    '{"pair": "EURUSD", "month": "2019-0',
                    encoding="utf-8")
    rows = vs.read_checkpoint(path)
    assert list(rows) == [("EURUSD", "2019-06")]
