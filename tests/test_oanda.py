"""The OANDA cross-check client: parsing, host selection and the read-only guard.

Every test here runs offline against canned payloads. The client is only ever
used to check the primary feed, so it has no business being able to do anything
else, and that restriction is tested rather than merely documented.
"""

from __future__ import annotations

import datetime as dt
import io
import json

import pandas as pd
import pytest

from fxlab.ingestion.oanda import (
    ENV_ENVIRONMENT,
    ENV_TOKEN,
    Candle,
    OandaClient,
    OandaError,
    check_path,
    cross_check,
    instrument_name,
    parse_rfc3339,
    resolve_host,
)

CANDLE_PAYLOAD = {
    "instrument": "EUR_USD",
    "granularity": "H1",
    "candles": [
        {"time": "2026-07-14T13:00:00.000000000Z", "complete": True, "volume": 9915,
         "bid": {"o": "1.14454", "h": "1.14548", "l": "1.14407", "c": "1.14483"},
         "ask": {"o": "1.14470", "h": "1.14562", "l": "1.14421", "c": "1.14499"},
         "mid": {"o": "1.14462", "h": "1.14555", "l": "1.14414", "c": "1.14491"}},
    ],
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def client_returning(payload, **kwargs) -> OandaClient:
    seen: list = []

    def opener(request, timeout=None):
        seen.append(request)
        return FakeResponse(json.dumps(payload).encode("utf8"))

    client = OandaClient(token="not-a-real-token", opener=opener, **kwargs)
    client.seen = seen  # type: ignore[attr-defined]
    return client


def test_nanosecond_timestamps_are_parsed_not_guessed() -> None:
    assert parse_rfc3339("2026-07-14T13:00:00.000000000Z") == dt.datetime(
        2026, 7, 14, 13, tzinfo=dt.timezone.utc)
    assert parse_rfc3339("2026-07-14T13:00:00.123456789Z").microsecond == 123456
    assert parse_rfc3339("2026-07-14T13:00:00Z").tzinfo is not None


def test_prices_arrive_as_strings_and_become_floats() -> None:
    candle = Candle.from_payload(CANDLE_PAYLOAD["candles"][0])
    assert candle.mid.open == pytest.approx(1.14462)
    assert isinstance(candle.mid.open, float)
    assert candle.volume == 9915
    assert candle.complete is True


def test_practice_is_the_default_environment(monkeypatch) -> None:
    monkeypatch.delenv(ENV_ENVIRONMENT, raising=False)
    assert resolve_host() == "https://api-fxpractice.oanda.com"
    assert resolve_host("live") == "https://api-fxtrade.oanda.com"
    with pytest.raises(OandaError):
        resolve_host("staging")


def test_environment_variable_selects_the_host(monkeypatch) -> None:
    monkeypatch.setenv(ENV_ENVIRONMENT, "live")
    assert OandaClient(token="x").host == "https://api-fxtrade.oanda.com"


def test_order_endpoints_are_refused_before_any_request() -> None:
    for path in ("/v3/accounts/1/orders", "/v3/accounts/1/trades",
                 "/v3/accounts/1/positions", "/v3/accounts/1/transactions"):
        with pytest.raises(OandaError, match="read-only"):
            check_path(path)


def test_only_candles_and_instruments_are_allowed() -> None:
    assert check_path("/v3/instruments/EUR_USD/candles")
    assert check_path("/v3/accounts/123/instruments")
    with pytest.raises(OandaError):
        check_path("/v3/accounts/123/summary")


def test_missing_token_is_an_explicit_error(monkeypatch) -> None:
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    with pytest.raises(OandaError, match=ENV_TOKEN):
        OandaClient().get("/v3/instruments/EUR_USD/candles")


def test_candles_request_is_read_only_and_parsed() -> None:
    client = client_returning(CANDLE_PAYLOAD)
    candles = client.candles("EURUSD", granularity="H1",
                             start=dt.datetime(2026, 7, 14, tzinfo=dt.timezone.utc))
    assert len(candles) == 1
    request = client.seen[0]
    assert request.get_method() == "GET"
    assert "/v3/instruments/EUR_USD/candles" in request.full_url
    assert "price=BAM" in request.full_url


def test_the_token_is_sent_but_never_returned() -> None:
    client = client_returning(CANDLE_PAYLOAD)
    client.candles("EURUSD")
    assert client.has_token is True
    assert not hasattr(client, "token")


def test_instrument_naming_matches_oanda() -> None:
    assert instrument_name("EURUSD") == "EUR_USD"
    assert instrument_name("GBPJPY") == "GBP_JPY"


def _bars(rows):
    """Build the minimal hourly bar frame the cross-check consumes."""
    return pd.DataFrame({
        "ts": [pd.Timestamp(t) for t, _b, _a, _m in rows],
        "bid_open": [b for _t, b, _a, _m in rows],
        "ask_open": [a for _t, _b, a, _m in rows],
        "mid_open": [m for _t, _b, _a, m in rows],
    })


def test_cross_check_reports_the_expected_ecn_versus_retail_offsets() -> None:
    # Dukascopy bid sits ABOVE the OANDA bid and its ask BELOW, because the ECN
    # spread is tighter. Mids agree closely. Flagging that as an error would be
    # a miscalibrated cross-check.
    candle = Candle.from_payload(CANDLE_PAYLOAD["candles"][0])
    bars = _bars([("2026-07-14T13:00:00+00:00", 1.14461, 1.14462, 1.144615)])
    result = cross_check(bars, [candle], "EURUSD", max_mid_diff_pips=1.0)
    assert result.compared == 1
    assert result.ok
    assert result.stats["bid_offset_mean_pips"] > 0
    assert result.stats["ask_offset_mean_pips"] < 0
    assert abs(result.stats["mid_diff_mean_pips"]) < 0.2


def test_cross_check_flags_a_real_disagreement() -> None:
    candle = Candle.from_payload(CANDLE_PAYLOAD["candles"][0])
    bars = _bars([("2026-07-14T13:00:00+00:00", 1.1500, 1.1502, 1.1501)])
    result = cross_check(bars, [candle], "EURUSD", max_mid_diff_pips=1.0)
    assert not result.ok
    assert result.flagged[0]["mid_diff_pips"] > 1.0


def test_cross_check_only_compares_hours_present_in_both() -> None:
    candle = Candle.from_payload(CANDLE_PAYLOAD["candles"][0])
    bars = _bars([("2026-07-14T13:00:00+00:00", 1.14461, 1.14462, 1.144615),
                  ("2026-07-14T14:00:00+00:00", 1.14, 1.1401, 1.14005)])
    result = cross_check(bars, [candle], "EURUSD")
    assert result.compared == 1
    assert result.to_dict()["compared"] == 1
