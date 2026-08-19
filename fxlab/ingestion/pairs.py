"""Per-pair metadata: display precision, price scale and pip size.

The Dukascopy tick files store prices as integers. Turning them back into
prices needs the pair's display precision, and one global constant cannot be
right for both JPY-quoted pairs (3 dp) and the rest (5 dp) -- getting this
wrong is the classic "prices are 100x off" bug.

The rule below is derived, not tabulated: the precision follows the quote
currency. The explicit table exists so that the Phase 1 universe is documented
in one place and so that an exception (a metals or exotic pair) has somewhere
to live, but an unlisted pair still resolves correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The Phase 1 universe, in the order given in spec.md.
UNIVERSE: Final[tuple[str, ...]] = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY",
)

#: Display precision by quote currency. JPY quotes to 3 dp, everything else in
#: the universe to 5. Confirmed against the live feed and cross-checked against
#: OANDA displayPrecision for all 12 pairs (see SPEC.md).
_PRECISION_BY_QUOTE: Final[dict[str, int]] = {"JPY": 3}
_DEFAULT_PRECISION: Final[int] = 5

#: Pairs whose precision is not implied by the quote currency go here.
_PRECISION_OVERRIDES: Final[dict[str, int]] = {}


class UnknownPairError(ValueError):
    """Raised when a symbol cannot be interpreted as a six-letter FX pair."""


@dataclass(frozen=True, slots=True)
class PairSpec:
    """Static description of a tradeable FX pair."""

    name: str
    base: str
    quote: str
    display_precision: int

    @property
    def price_scale(self) -> float:
        """Multiplier turning a stored integer price into a real price."""
        return 10.0 ** -self.display_precision

    @property
    def price_divisor(self) -> int:
        """Exact integer divisor for the same conversion.

        Dividing by ``10 ** precision`` is exactly rounded, whereas multiplying
        by ``1e-5`` accumulates a representation error in the last bits. Both
        are within tolerance, but the division is free and correct.
        """
        return 10 ** self.display_precision

    @property
    def pip_size(self) -> float:
        """One pip in price units: 0.01 for JPY quotes, 0.0001 otherwise."""
        return 10.0 ** -(self.display_precision - 1)

    @property
    def is_jpy_quoted(self) -> bool:
        """True when the quote currency is JPY."""
        return self.quote == "JPY"

    @property
    def oanda_instrument(self) -> str:
        """The same pair in OANDA's ``BASE_QUOTE`` spelling."""
        return f"{self.base}_{self.quote}"


def pair_spec(symbol: str) -> PairSpec:
    """Resolve a six-letter pair symbol to its :class:`PairSpec`.

    Args:
        symbol: Pair name such as ``EURUSD``; case-insensitive, and an OANDA
            style ``EUR_USD`` is accepted too.

    Returns:
        The pair's static description.

    Raises:
        UnknownPairError: If the symbol is not six letters.
    """
    name = symbol.replace("_", "").replace("/", "").upper()
    if len(name) != 6 or not name.isalpha():
        raise UnknownPairError(
            f"{symbol!r} is not a six-letter FX pair (expected e.g. 'EURUSD')")
    base, quote = name[:3], name[3:]
    precision = _PRECISION_OVERRIDES.get(
        name, _PRECISION_BY_QUOTE.get(quote, _DEFAULT_PRECISION))
    return PairSpec(name=name, base=base, quote=quote, display_precision=precision)


def price_scale(symbol: str) -> float:
    """Convenience wrapper returning only the price scale for ``symbol``."""
    return pair_spec(symbol).price_scale


def pip_size(symbol: str) -> float:
    """Convenience wrapper returning only the pip size for ``symbol``."""
    return pair_spec(symbol).pip_size
