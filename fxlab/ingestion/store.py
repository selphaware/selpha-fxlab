"""Parquet tick store with an explicitly pinned Arrow schema.

The schema below is written out field by field on purpose. pandas 3 changed
the default string dtype to an Arrow-backed ``large_string`` and turned
``future.infer_string`` on; code that lets pandas infer types produces
different Parquet files on different library versions, and nothing in the
pipeline notices. Pinning makes the storage layer version-proof and makes any
drift a loud failure rather than a quiet one.

Layout::

    <out_dir>/ticks/pair=<PAIR>/date=<YYYY-MM-DD>/<PAIR>_<DATE>_<HH>h.parquet

One file per ingested hour, so an hour can be re-ingested without rewriting a
day, and a partial day is still readable.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from fxlab.ingestion.validation import TickBatch

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: The tick schema. Every column, every type, fixed.
TICK_SCHEMA: Final[pa.Schema] = pa.schema([
    pa.field("pair", pa.large_string(), nullable=False),
    pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("bid", pa.float64(), nullable=False),
    pa.field("ask", pa.float64(), nullable=False),
    pa.field("bid_volume", pa.float64(), nullable=False),
    pa.field("ask_volume", pa.float64(), nullable=False),
    pa.field("source", pa.large_string(), nullable=False),
])

#: Column order, as the contract states it.
TICK_COLUMNS: Final[tuple[str, ...]] = tuple(TICK_SCHEMA.names)

#: Matches the hour in a stored filename, whether written by ingestion
#: (EURUSD_2026-07-14_13h.parquet) or by the recorder, which appends a
#: rotation sequence (EURUSD_2026-07-14_13h_0001.parquet).
_HOUR_IN_NAME: Final[re.Pattern[str]] = re.compile(r"_(\d{2})h(?:_|$)")

#: Parquet codec. Snappy ships with every pyarrow build; tick data compresses
#: about 4x, which is enough that codec exotica is not worth the risk.
COMPRESSION: Final[str] = "snappy"


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What one hour of ticks became on disk."""

    path: pathlib.Path
    rows: int
    file_bytes: int


def partition_dir(out_dir: pathlib.Path, pair: str, date_str: str) -> pathlib.Path:
    """Return the partition directory for one pair-day."""
    return pathlib.Path(out_dir) / "ticks" / f"pair={pair}" / f"date={date_str}"


def hour_file(out_dir: pathlib.Path, pair: str, date_str: str, hour: int) -> pathlib.Path:
    """Return the Parquet path for one pair-day-hour."""
    return partition_dir(out_dir, pair, date_str) / f"{pair}_{date_str}_{hour:02d}h.parquet"


def tick_table(batch: TickBatch, source: str) -> pa.Table:
    """Build the Arrow table for one hour, against the pinned schema.

    Args:
        batch: De-duplicated, sorted ticks.
        source: Provenance tag stored on every row (``dukascopy``, ``ib_live``).

    Returns:
        A table whose schema is exactly :data:`TICK_SCHEMA`.
    """
    n = len(batch)
    return pa.Table.from_arrays(
        [
            pa.array([batch.pair] * n, type=pa.large_string()),
            pa.array(np.asarray(batch.ts_us, dtype="int64").astype("datetime64[us]"),
                     type=pa.timestamp("us", tz="UTC")),
            pa.array(np.asarray(batch.bid, dtype="float64"), type=pa.float64()),
            pa.array(np.asarray(batch.ask, dtype="float64"), type=pa.float64()),
            pa.array(np.asarray(batch.bid_volume, dtype="float64"), type=pa.float64()),
            pa.array(np.asarray(batch.ask_volume, dtype="float64"), type=pa.float64()),
            pa.array([source] * n, type=pa.large_string()),
        ],
        schema=TICK_SCHEMA,
    )


def write_table_atomic(table: pa.Table, path: pathlib.Path) -> WriteResult:
    """Write ``table`` to ``path`` via a temporary file and an atomic rename.

    A half-written Parquet file that a later run treats as complete is the kind
    of corruption that only shows up months later, so the rename is the commit.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression=COMPRESSION)
    os.replace(tmp, path)
    return WriteResult(path=path, rows=table.num_rows, file_bytes=path.stat().st_size)


def write_ticks(out_dir: pathlib.Path, batch: TickBatch, source: str) -> WriteResult:
    """Write one hour of ticks into the partitioned store.

    Args:
        out_dir: Root of the store.
        batch: De-duplicated, sorted, already-validated ticks.
        source: Provenance tag.

    Returns:
        Where the data landed and how much of it there was.
    """
    date_str = batch.hour_start.date().isoformat()
    path = hour_file(out_dir, batch.pair, date_str, batch.hour_start.hour)
    result = write_table_atomic(tick_table(batch, source), path)
    _LOG.debug("wrote %d ticks to %s", result.rows, path)
    return result


def read_ticks(out_dir: pathlib.Path, pair: str | None = None,
               dates: list[str] | None = None) -> Any:
    """Read stored ticks back as a time-sorted pandas DataFrame.

    Args:
        out_dir: Root of the store.
        pair: Restrict to one pair; all pairs when omitted.
        dates: Restrict to these ISO dates; all dates when omitted.

    Returns:
        A DataFrame with the tick columns, sorted by timestamp. Empty (but
        correctly typed) when nothing matches.
    """
    root = pathlib.Path(out_dir) / "ticks"
    parts: list[pathlib.Path] = []
    if not root.exists():
        return TICK_SCHEMA.empty_table().to_pandas()
    for pair_dir in sorted(root.glob("pair=*")):
        if pair is not None and pair_dir.name != f"pair={pair}":
            continue
        for date_dir in sorted(pair_dir.glob("date=*")):
            if dates is not None and date_dir.name.removeprefix("date=") not in dates:
                continue
            parts.extend(sorted(date_dir.glob("*.parquet")))
    if not parts:
        return TICK_SCHEMA.empty_table().to_pandas()
    table = pq.read_table(parts, schema=TICK_SCHEMA)
    frame = table.to_pandas()
    return frame.sort_values("ts", kind="stable").reset_index(drop=True)


def store_hours(out_dir: pathlib.Path) -> list[tuple[str, str, int]]:
    """List the (pair, date, hour) triples currently present in the store."""
    root = pathlib.Path(out_dir) / "ticks"
    found: list[tuple[str, str, int]] = []
    if not root.exists():
        return found
    for pair_dir in sorted(root.glob("pair=*")):
        pair = pair_dir.name.removeprefix("pair=")
        for date_dir in sorted(pair_dir.glob("date=*")):
            date_str = date_dir.name.removeprefix("date=")
            for parquet in sorted(date_dir.glob("*.parquet")):
                match = _HOUR_IN_NAME.search(parquet.stem)
                if match is None:
                    continue
                found.append((pair, date_str, int(match.group(1))))
    return found


def utc_day(ts: dt.datetime) -> str:
    """ISO date string of ``ts`` in UTC, the partition key."""
    return ts.astimezone(dt.timezone.utc).date().isoformat()
