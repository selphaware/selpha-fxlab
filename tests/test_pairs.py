"""Per-pair scaling: the one global constant that cannot be right for everyone."""

from __future__ import annotations

import pytest

from fxlab.ingestion.pairs import (
    UNIVERSE,
    UnknownPairError,
    pair_spec,
    pip_size,
    price_scale,
)


def test_universe_has_the_twelve_phase_one_pairs() -> None:
    assert len(UNIVERSE) == 12
    assert len(set(UNIVERSE)) == 12
    assert "EURUSD" in UNIVERSE and "AUDJPY" in UNIVERSE


@pytest.mark.parametrize("pair", [p for p in UNIVERSE if p.endswith("JPY")])
def test_jpy_quoted_pairs_scale_by_one_thousandth(pair: str) -> None:
    assert pair_spec(pair).display_precision == 3
    assert price_scale(pair) == pytest.approx(1e-3)
    assert pip_size(pair) == pytest.approx(0.01)


@pytest.mark.parametrize("pair", [p for p in UNIVERSE if not p.endswith("JPY")])
def test_other_pairs_scale_by_one_hundred_thousandth(pair: str) -> None:
    assert pair_spec(pair).display_precision == 5
    assert price_scale(pair) == pytest.approx(1e-5)
    assert pip_size(pair) == pytest.approx(1e-4)


def test_price_divisor_is_an_exact_integer() -> None:
    assert pair_spec("EURUSD").price_divisor == 100_000
    assert pair_spec("USDJPY").price_divisor == 1_000


def test_integer_division_reproduces_the_frozen_price_exactly() -> None:
    # Dividing by the integer divisor is exactly rounded; multiplying by 1e-5
    # is not. Both pass the gate tolerance, but only one is exact.
    assert 114462 / pair_spec("EURUSD").price_divisor == 1.14462
    assert 161911 / pair_spec("USDJPY").price_divisor == 161.911


def test_unlisted_pair_still_resolves_from_its_quote_currency() -> None:
    assert pair_spec("EURNOK").display_precision == 5
    assert pair_spec("CHFJPY").display_precision == 3


def test_oanda_spelling_is_accepted_and_produced() -> None:
    assert pair_spec("EUR_USD").name == "EURUSD"
    assert pair_spec("EURUSD").oanda_instrument == "EUR_USD"


@pytest.mark.parametrize("bad", ["EUR", "EURUSDX", "12USD3", ""])
def test_nonsense_symbols_are_rejected(bad: str) -> None:
    with pytest.raises(UnknownPairError):
        pair_spec(bad)
