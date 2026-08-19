"""Known answers for USD accounting (SPEC2 prerequisite P0-A).

Phase 1 computes notional as ``units * fill_price``, which is the **quote**
currency. Eight of the twelve universe pairs are not USD-quoted, so the USD
2.00 commission floor is tested against a JPY, CHF, CAD or GBP number, and
``BacktestResult.gross_pnl`` sums per-trade P&L across pairs without
converting. Until that is fixed, no cross-pair number means anything.

This module holds the arithmetic that will decide whether the fix is real. It
runs only when ``fxlab.costs.USD_ACCOUNTING`` is True; until then the research
gate fails any scored experiment touching a non-USD-quoted pair, which is the
same verdict reached a cheaper way.

The interface the fix must expose, recorded here so the task is written against
a fixed target rather than against whatever the test happens to call:

* ``fxlab.costs.USD_ACCOUNTING`` -- ``True`` once conversion is implemented;
* ``fxlab.costs.quote_to_usd(pair, rates)`` -- the factor converting one unit
  of ``pair``'s quote currency into USD, given a mapping of conversion pair to
  its **fill-time** mid;
* ``IBCostModel.commission_for(units, fill_price, *, quote_to_usd=1.0)`` and
  ``IBCostModel.spread_cost_for(units, fill_price, mid, *, quote_to_usd=1.0)``
  -- both returning USD.

Setting the flag without the conversion is caught here, not excused.

The numbers
-----------

Rates at fill time: ``USDJPY = 150.00``, ``USDCHF = 0.90``.

============================  ==========================  ================
case                          quote-currency arithmetic   USD answer
============================  ==========================  ================
50,000 USDJPY at 150.00,      0.20bp of 7,500,000 JPY     notional is
commission                    = 1,500 JPY, under a floor  50,000 USD, 0.20bp
                              read as 1,500 "dollars"     = 1.00, floored to
                                                          **2.00**
1,000,000 USDJPY at 150.00,   0.20bp of 150,000,000 JPY   **20.00**
commission                    = 30,000 JPY
100,000 USDJPY bought at      0.015 * 100,000             **10.00**
150.015 against a 150.00 mid  = 1,500 JPY
============================  ==========================  ================

Portfolio, three quote currencies: +300.00 USD on EURUSD, +3,000 JPY on
USDJPY, -180.00 CHF on EURCHF. Converted: 300.00 + 20.00 - 200.00 =
**+120.00 USD**. Added blind: 300 + 3000 - 180 = 3,120, a number in no
currency at all.
"""

from __future__ import annotations

from typing import Any, Final

#: The 12-pair universe (pre-reg #9) and the 8 that are not USD-quoted.
UNIVERSE: Final[tuple[str, ...]] = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDJPY")

NON_USD_QUOTED: Final[tuple[str, ...]] = tuple(
    pair for pair in UNIVERSE if pair[3:] != "USD")

#: Fill-time mids used by every case below.
RATES: Final[dict[str, float]] = {"USDJPY": 150.0, "USDCHF": 0.90}

#: Tolerance for a USD figure: a hundredth of a cent.
TOL: Final[float] = 1e-6


def quote_currency(pair: str) -> str:
    """The quote currency of a six-letter pair name."""
    return pair[3:]


def _close(actual: float, expected: float) -> bool:
    """True if two USD amounts agree to within :data:`TOL`."""
    return abs(float(actual) - float(expected)) <= TOL


def fix_landed() -> bool:
    """True if ``fxlab.costs`` declares USD accounting."""
    import fxlab.costs as costs
    return bool(getattr(costs, "USD_ACCOUNTING", False))


def check() -> list[str]:
    """Run the known answers. Returns a list of failure descriptions.

    An empty list means the fix is real. Call only when :func:`fix_landed` is
    True; calling it earlier reports the missing interface as failures, which
    is accurate but not useful.
    """
    failures: list[str] = []
    import fxlab.costs as costs

    converter = getattr(costs, "quote_to_usd", None)
    if not callable(converter):
        return ["fxlab.costs.USD_ACCOUNTING is set but there is no callable "
                "fxlab.costs.quote_to_usd(pair, rates)"]

    model_cls = getattr(costs, "IBCostModel", None)
    if model_cls is None:
        return ["fxlab.costs has no IBCostModel"]
    model = model_cls()

    # -- the conversion factor itself ------------------------------------
    for pair, expected in (("EURUSD", 1.0),
                           ("USDJPY", 1.0 / 150.0),
                           ("EURCHF", 1.0 / 0.90),
                           ("EURJPY", 1.0 / 150.0)):
        try:
            actual = float(converter(pair, RATES))
        except Exception as exc:  # noqa: BLE001 - report, do not crash the gate
            failures.append(f"quote_to_usd({pair!r}) raised {type(exc).__name__}: {exc}")
            continue
        if not _close(actual, expected):
            failures.append(
                f"quote_to_usd({pair!r}) = {actual!r}, expected {expected!r}")

    jpy = 1.0 / 150.0

    # -- the floor, which is where the quote-currency bug bites -----------
    failures.extend(_commission_case(model, 50_000, 150.0, jpy, 2.00,
                                     "USD 2.00 floor on a 50,000 USD notional"))
    failures.extend(_commission_case(model, 1_000_000, 150.0, jpy, 20.00,
                                     "0.20bp of a 1,000,000 USD notional"))

    # -- spread, converted the same way -----------------------------------
    try:
        spread = float(model.spread_cost_for(100_000, 150.015, 150.0,
                                             quote_to_usd=jpy))
        if not _close(spread, 10.00):
            failures.append(f"spread_cost_for(100,000 USDJPY, half-spread "
                            f"0.015) = {spread!r} USD, expected 10.00")
    except TypeError as exc:
        failures.append(f"spread_cost_for does not accept quote_to_usd: {exc}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"spread_cost_for raised {type(exc).__name__}: {exc}")

    # -- the portfolio sum, three quote currencies ------------------------
    legs = (("EURUSD", 300.0), ("USDJPY", 3000.0), ("EURCHF", -180.0))
    total = 0.0
    for pair, amount in legs:
        try:
            total += amount * float(converter(pair, RATES))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"converting {pair} leg raised "
                            f"{type(exc).__name__}: {exc}")
            total = float("nan")
            break
    if total == total and not _close(total, 120.00):
        failures.append(
            f"portfolio of +300 USD, +3,000 JPY and -180 CHF converted to "
            f"{total!r} USD, expected 120.00 (blind sum would be 3,120)")

    return failures


def _commission_case(model: Any, units: float, fill: float, factor: float,
                     expected: float, label: str) -> list[str]:
    """One commission known answer, reported rather than raised."""
    try:
        actual = float(model.commission_for(units, fill, quote_to_usd=factor))
    except TypeError as exc:
        return [f"commission_for does not accept quote_to_usd ({label}): {exc}"]
    except Exception as exc:  # noqa: BLE001
        return [f"commission_for raised {type(exc).__name__} ({label}): {exc}"]
    if not _close(actual, expected):
        return [f"{label}: commission_for = {actual!r} USD, expected {expected!r}"]
    return []
