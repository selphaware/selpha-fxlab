"""Decoding real bi5 bytes: counts, boundary ticks, scaling and failure modes."""

from __future__ import annotations

import datetime as dt
import lzma
import struct

import pytest

from fxlab.ingestion.bi5 import (
    RECORD_DTYPE,
    RECORD_FORMAT,
    RECORD_SIZE,
    Bi5DecodeError,
    decode_bi5,
    epoch_micros,
)
from tests.conftest import RAW_DIR, hour_start, read_fixture


def test_record_layout_is_twenty_bytes_big_endian() -> None:
    assert RECORD_FORMAT == ">IIIff"
    assert RECORD_SIZE == 20
    assert RECORD_DTYPE.itemsize == 20
    assert RECORD_DTYPE.names == ("ms", "ask", "bid", "ask_volume", "bid_volume")


def test_ask_precedes_bid_in_the_record() -> None:
    # The single easiest field to get backwards. If they were swapped, every
    # decoded tick would show ask below bid.
    packed = struct.pack(RECORD_FORMAT, 1000, 114462, 114461, 1.35, 0.9)
    decoded = decode_bi5(
        lzma.compress(packed, format=lzma.FORMAT_ALONE),
        "EURUSD", hour_start("2026-07-14", 13))
    assert decoded.ask[0] == pytest.approx(1.14462)
    assert decoded.bid[0] == pytest.approx(1.14461)
    assert decoded.ask[0] > decoded.bid[0]


def test_decodes_every_frozen_hour_exactly(clean_hours: list[dict]) -> None:
    for spec in clean_hours:
        payload = read_fixture(RAW_DIR, spec["file"])
        decoded = decode_bi5(payload, spec["pair"],
                             hour_start(spec["date"], spec["hour"]))
        assert len(decoded) == spec["tick_count"], spec["file"]
        assert decoded.decoded_bytes == spec["decoded_bytes"]
        assert decoded.compressed_bytes == spec["compressed_bytes"]


def test_boundary_ticks_match_the_frozen_values(clean_hours: list[dict]) -> None:
    for spec in clean_hours:
        payload = read_fixture(RAW_DIR, spec["file"])
        decoded = decode_bi5(payload, spec["pair"],
                             hour_start(spec["date"], spec["hour"]))
        stamps = decoded.ts_us.astype("datetime64[us]")
        for which, index in (("first_tick", 0), ("last_tick", -1)):
            want = spec[which]
            assert str(stamps[index]) + "+00:00" == want["ts"], (spec["file"], which)
            for field in ("bid", "ask", "bid_volume", "ask_volume"):
                got = float(getattr(decoded, field)[index])
                assert got == pytest.approx(want[field], abs=1e-9), (
                    spec["file"], which, field)


def test_every_tick_falls_inside_its_own_hour(clean_hours: list[dict]) -> None:
    for spec in clean_hours:
        payload = read_fixture(RAW_DIR, spec["file"])
        start = hour_start(spec["date"], spec["hour"])
        decoded = decode_bi5(payload, spec["pair"], start)
        low = epoch_micros(start)
        assert int(decoded.ts_us.min()) >= low
        assert int(decoded.ts_us.max()) < low + 3_600_000_000


def test_price_scale_is_per_pair_not_global() -> None:
    eurusd = decode_bi5(read_fixture(RAW_DIR, "EURUSD_2026-07-14_13h.bi5"),
                        "EURUSD", hour_start("2026-07-14", 13))
    usdjpy = decode_bi5(read_fixture(RAW_DIR, "USDJPY_2026-07-14_13h.bi5"),
                        "USDJPY", hour_start("2026-07-14", 13))
    assert 1.0 < float(eurusd.bid[0]) < 2.0
    assert 100.0 < float(usdjpy.bid[0]) < 200.0


def test_empty_payload_means_closed_not_broken() -> None:
    decoded = decode_bi5(b"", "EURUSD", hour_start("2026-07-11", 13))
    assert len(decoded) == 0
    assert decoded.decoded_bytes == 0


def test_truncated_record_block_is_rejected() -> None:
    payload = read_fixture(RAW_DIR, "EURUSD_2026-07-19_21h.bi5")
    block = lzma.LZMADecompressor(lzma.FORMAT_ALONE).decompress(payload)
    truncated = lzma.compress(block[:-7], format=lzma.FORMAT_ALONE)
    with pytest.raises(Bi5DecodeError, match="record size"):
        decode_bi5(truncated, "EURUSD", hour_start("2026-07-19", 21))


def test_non_lzma_payload_is_rejected_with_a_useful_message() -> None:
    with pytest.raises(Bi5DecodeError, match="alone-format"):
        decode_bi5(b"<html>503 Service Unavailable</html>", "EURUSD",
                   hour_start("2026-07-14", 13))


def test_epoch_micros_is_exact_and_refuses_naive_input() -> None:
    assert epoch_micros(dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)) == 0
    assert epoch_micros(hour_start("2026-07-14", 13)) % 3_600_000_000 == 0
    with pytest.raises(ValueError):
        epoch_micros(dt.datetime(2026, 7, 14, 13))
