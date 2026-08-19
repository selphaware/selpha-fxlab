"""Tick ingestion: Dukascopy bi5 download, decode, validation and storage.

The step order is deliberate and is the order the modules are listed in:

1. :mod:`fxlab.ingestion.sources` gets the raw bytes (fixture or live).
2. :mod:`fxlab.ingestion.bi5` decodes them, using
   :mod:`fxlab.ingestion.pairs` for the per-pair price scale.
3. :mod:`fxlab.ingestion.validation` de-duplicates and checks, naming every
   rejection, with the FX week supplied by :mod:`fxlab.ingestion.sessions`.
4. :mod:`fxlab.ingestion.store` writes Parquet against a pinned Arrow schema.
5. :mod:`fxlab.ingestion.manifest` records what happened to every hour.
6. :mod:`fxlab.ingestion.bars` resamples bars from the stored ticks, never by
   downloading them again.

:mod:`fxlab.ingestion.oanda` is the read-only second source used to cross-check
the first; :mod:`fxlab.ingestion.pipeline` runs steps 1 to 6 in order.
"""

from __future__ import annotations

from fxlab.ingestion.bi5 import DecodedHour, decode_bi5
from fxlab.ingestion.manifest import HourRecord, Manifest, load_manifest, write_manifest
from fxlab.ingestion.pairs import PairSpec, UNIVERSE, pair_spec
from fxlab.ingestion.pipeline import IngestReport, ingest
from fxlab.ingestion.sessions import is_market_open, session_of, week_bounds
from fxlab.ingestion.sources import RawHour, bi5_url, build_source
from fxlab.ingestion.store import TICK_SCHEMA, read_ticks, write_ticks
from fxlab.ingestion.validation import TickBatch, deduplicate, validate

__all__ = [
    "DecodedHour",
    "HourRecord",
    "IngestReport",
    "Manifest",
    "PairSpec",
    "RawHour",
    "TICK_SCHEMA",
    "TickBatch",
    "UNIVERSE",
    "bi5_url",
    "build_source",
    "decode_bi5",
    "deduplicate",
    "ingest",
    "is_market_open",
    "load_manifest",
    "pair_spec",
    "read_ticks",
    "session_of",
    "validate",
    "week_bounds",
    "write_manifest",
    "write_ticks",
]
