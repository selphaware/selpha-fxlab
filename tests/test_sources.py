"""The datafeed URL, and the three response shapes that must stay distinct."""

from __future__ import annotations

import datetime as dt
import io
import urllib.error

import pytest

from fxlab.config import DukascopyConfig, HourRequest
from fxlab.ingestion.sources import (
    AVAILABILITY_EMPTY,
    AVAILABILITY_MISSING,
    AVAILABILITY_PRESENT,
    BlockedSource,
    DukascopySource,
    FeedError,
    FixtureSource,
    OfflineError,
    bi5_url,
    build_source,
)
from tests.conftest import RAW_DIR


def request_for(pair="EURUSD", date="2026-07-14", hour=13) -> HourRequest:
    return HourRequest(pair, dt.date.fromisoformat(date), hour)


class FakeResponse(io.BytesIO):
    """Minimal stand-in for an http response object."""

    def __init__(self, body: bytes, status: int = 200, headers=None) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_month_in_the_url_is_zero_based() -> None:
    # July is 06, not 07. Confirmed against the Last-Modified header the feed
    # returns for /2026/06/11/, which names Sat 11 Jul 2026.
    assert bi5_url("EURUSD", dt.date(2026, 7, 14), 13).endswith(
        "/EURUSD/2026/06/14/13h_ticks.bi5")
    assert bi5_url("EURUSD", dt.date(2026, 1, 9), 21).endswith(
        "/EURUSD/2026/00/09/21h_ticks.bi5")
    assert bi5_url("EURUSD", dt.date(2026, 12, 31), 0).endswith(
        "/EURUSD/2026/11/31/00h_ticks.bi5")


def test_fixture_source_distinguishes_present_empty_and_missing() -> None:
    source = FixtureSource(RAW_DIR)
    assert source.fetch(request_for()).availability == AVAILABILITY_PRESENT
    assert source.fetch(request_for(date="2026-07-11")).availability == \
        AVAILABILITY_EMPTY
    assert source.fetch(request_for(date="2099-01-01", hour=5)).availability == \
        AVAILABILITY_MISSING


def test_empty_body_is_closed_not_an_error() -> None:
    source = DukascopySource(opener=lambda *a, **k: FakeResponse(b""))
    fetched = source.fetch(request_for())
    assert fetched.availability == AVAILABILITY_EMPTY
    assert fetched.payload == b""


def test_404_is_a_missing_hour_and_is_not_retried() -> None:
    calls = []

    def opener(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    source = DukascopySource(DukascopyConfig(max_retries=3), opener=opener,
                             sleep=lambda _s: None)
    assert source.fetch(request_for()).availability == AVAILABILITY_MISSING
    assert len(calls) == 1


def test_503_is_throttling_and_is_retried_with_growing_backoff() -> None:
    delays: list[float] = []
    attempts = {"n": 0}

    def opener(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable",
                                         {}, None)
        return FakeResponse(b"payload")

    source = DukascopySource(
        DukascopyConfig(max_retries=4, backoff_initial=1.0, backoff_factor=2.0),
        opener=opener, sleep=delays.append)
    assert source.fetch(request_for()).payload == b"payload"
    assert attempts["n"] == 3
    assert delays == [1.0, 2.0]


def test_giving_up_names_the_hour_and_the_url() -> None:
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 503, "nope", {}, None)

    source = DukascopySource(DukascopyConfig(max_retries=1), opener=opener,
                             sleep=lambda _s: None)
    with pytest.raises(FeedError, match="EURUSD 2026-07-14T13:00Z"):
        source.fetch(request_for())


def test_403_explains_the_egress_reputation_block() -> None:
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    source = DukascopySource(opener=opener, sleep=lambda _s: None)
    with pytest.raises(FeedError, match="egress"):
        source.fetch(request_for())


def test_concurrency_above_four_is_refused() -> None:
    with pytest.raises(Exception, match="max_concurrency"):
        DukascopyConfig(max_concurrency=16)


def test_blocked_source_makes_fixture_mode_provable() -> None:
    with pytest.raises(OfflineError):
        BlockedSource("fixture mode does not fetch").fetch(request_for())


def test_build_source_picks_by_mode() -> None:
    assert isinstance(build_source("fixture", raw_dir=RAW_DIR), FixtureSource)
    assert isinstance(build_source("live"), DukascopySource)
    with pytest.raises(ValueError):
        build_source("fixture")
    with pytest.raises(ValueError):
        build_source("telepathy")
