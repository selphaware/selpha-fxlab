"""Decoder for Dukascopy ``.bi5`` hourly tick files.

Confirmed against the live feed (SPEC.md), not assumed:

* the payload is raw **LZMA1 alone-format** (``lzma.FORMAT_ALONE``), header
  ``5d 00 00 40 00`` followed by an 8-byte little-endian uncompressed size;
* the decompressed body is a flat array of 20-byte big-endian records,
  ``>IIIff`` = ``(ms_offset_in_hour, ask_int, bid_int, ask_volume, bid_volume)``
  -- **ask precedes bid**, which is the single easiest field to get backwards
  and produces ``ask < bid`` on every tick when you do;
* integer prices scale by ``10 ** -display_precision``, per pair;
* an **empty body means the market was closed** for that hour. It is not an
  error and it is not a gap.

Timestamps are built by adding integer milliseconds to an explicit UTC hour
start. No naive datetime is ever constructed, because a naive one picks up the
local zone and shifts every tick by the UTC offset.
"""

from __future__ import annotations

import datetime as dt
import lzma
import struct
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

from fxlab.ingestion.pairs import PairSpec, pair_spec

#: ``struct`` spelling of one record, kept for documentation and tests.
RECORD_FORMAT: Final[str] = ">IIIff"
RECORD_SIZE: Final[int] = struct.calcsize(RECORD_FORMAT)

#: The same layout as a numpy dtype, which is how bulk decoding is done.
RECORD_DTYPE: Final[np.dtype] = np.dtype([
    ("ms", ">u4"),
    ("ask", ">u4"),
    ("bid", ">u4"),
    ("ask_volume", ">f4"),
    ("bid_volume", ">f4"),
])

#: LZMA1 alone-format magic, used only to give a better error message.
ALONE_MAGIC: Final[bytes] = b"\x5d\x00\x00"

_EPOCH: Final[dt.datetime] = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
_MS_PER_HOUR: Final[int] = 3_600_000


class Bi5DecodeError(ValueError):
    """Raised when a bi5 payload cannot be decoded into whole tick records."""


def epoch_micros(ts: dt.datetime) -> int:
    """Return microseconds since the Unix epoch, computed with integers only.

    Args:
        ts: A tz-aware timestamp.

    Returns:
        Microseconds since 1970-01-01T00:00:00Z.

    Going through ``float`` seconds would lose sub-microsecond exactness for
    modern timestamps; the arithmetic here cannot.
    """
    if ts.tzinfo is None:
        raise ValueError(f"{ts!r} is naive; hour starts must be tz-aware UTC")
    delta = ts.astimezone(dt.timezone.utc) - _EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


@dataclass(frozen=True, slots=True)
class DecodedHour:
    """The decoded contents of one hourly bi5 file.

    Attributes are parallel arrays rather than rows: an hour holds ten thousand
    ticks and a week holds a million, so the column layout is what gets written
    to Parquet and what the validator scans.
    """

    pair: str
    hour_start: dt.datetime
    ts_us: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    bid_volume: np.ndarray
    ask_volume: np.ndarray
    compressed_bytes: int
    decoded_bytes: int

    def __len__(self) -> int:
        """Number of decoded ticks."""
        return int(self.ts_us.size)

    @property
    def decoded_ticks(self) -> int:
        """Number of records decoded, before de-duplication."""
        return len(self)

    @property
    def hour_end(self) -> dt.datetime:
        """Exclusive end of the hour this file covers."""
        return self.hour_start + dt.timedelta(hours=1)


def decompress(payload: bytes) -> bytes:
    """Decompress a raw bi5 body.

    Args:
        payload: The bytes as served by the datafeed.

    Returns:
        The decompressed record block; empty for an empty payload.

    Raises:
        Bi5DecodeError: If the payload is not decodable LZMA1 alone-format.
    """
    if not payload:
        return b""
    try:
        return lzma.LZMADecompressor(lzma.FORMAT_ALONE).decompress(payload)
    except lzma.LZMAError as exc:
        head = payload[:5].hex(" ")
        hint = ""
        if not payload.startswith(ALONE_MAGIC):
            hint = (f" (first bytes {head!r}; alone-format files start "
                    f"{ALONE_MAGIC.hex(' ')!r} -- an HTML error page or an "
                    "xz-format stream will look like this)")
        raise Bi5DecodeError(f"LZMA1 alone-format decode failed: {exc}{hint}") from exc


def decode_records(block: bytes, pair: str, hour_start: dt.datetime,
                   *, spec: PairSpec | None = None,
                   compressed_bytes: int = 0) -> DecodedHour:
    """Turn a decompressed record block into columns.

    Args:
        block: The decompressed body, a whole number of 20-byte records.
        pair: Pair symbol, used for the price scale.
        hour_start: The UTC instant this file's hour opens.
        spec: Pre-resolved pair metadata; resolved from ``pair`` when omitted.
        compressed_bytes: Size of the original payload, recorded for the manifest.

    Returns:
        A :class:`DecodedHour`.

    Raises:
        Bi5DecodeError: If ``block`` is not a whole number of records.
    """
    spec = spec or pair_spec(pair)
    if len(block) % RECORD_SIZE:
        raise Bi5DecodeError(
            f"{pair} {hour_start:%Y-%m-%dT%H}Z: decoded body is {len(block)} bytes, "
            f"not a multiple of the {RECORD_SIZE}-byte record size -- the file is "
            "truncated or the layout is not the expected >IIIff")

    records = np.frombuffer(block, dtype=RECORD_DTYPE)
    divisor = spec.price_divisor
    ts_us = epoch_micros(hour_start) + records["ms"].astype(np.int64) * 1000
    return DecodedHour(
        pair=spec.name,
        hour_start=hour_start,
        ts_us=ts_us,
        bid=records["bid"].astype(np.float64) / divisor,
        ask=records["ask"].astype(np.float64) / divisor,
        bid_volume=records["bid_volume"].astype(np.float64),
        ask_volume=records["ask_volume"].astype(np.float64),
        compressed_bytes=compressed_bytes,
        decoded_bytes=len(block),
    )


def decode_bi5(payload: bytes, pair: str, hour_start: dt.datetime,
               *, spec: PairSpec | None = None) -> DecodedHour:
    """Decompress and decode one hourly bi5 payload.

    Args:
        payload: The bytes as served by the datafeed (possibly empty).
        pair: Pair symbol.
        hour_start: The UTC instant this file's hour opens.
        spec: Pre-resolved pair metadata.

    Returns:
        A :class:`DecodedHour`; empty payloads decode to zero ticks, which is
        how the feed says "the market was closed".
    """
    block = decompress(payload)
    return decode_records(block, pair, hour_start, spec=spec,
                          compressed_bytes=len(payload))


def unpack_record(raw: bytes) -> tuple[Any, ...]:
    """Unpack a single 20-byte record, for tests and interactive inspection."""
    if len(raw) != RECORD_SIZE:
        raise Bi5DecodeError(f"a record is {RECORD_SIZE} bytes, got {len(raw)}")
    return struct.unpack(RECORD_FORMAT, raw)
