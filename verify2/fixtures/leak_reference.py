"""Known-answer fixtures for walk-forward leakage, and the leaks themselves.

The same discipline as the Phase 1 backtest gate: a series small enough that
the answer is arithmetic rather than opinion, and an expected number written
down before any implementation is trusted. What is different here is that three
*wrong* answers are written down too. A leakage check that only knows the right
answer proves the engine agrees with the fixture; it does not prove the fixture
could have noticed a leak.

The series
----------

Twenty bars. The feature at bar ``i`` is the previous return
``p[i] - p[i-1]``, which is known at bar ``i``; the outcome at bar ``i`` is the
forward return ``p[i+1] - p[i]``, which is not. One window: train ``1..9``,
purge ``2``, test ``12..16``::

    i     0    1    2    3    4    5    6    7    8    9   10   11
    p   100  101  100  101  100  101  100  101  100  100  111  122
    x     -   +1   -1   +1   -1   +1   -1   +1   -1    0  +11  +11
          <---------------- train ---------------->  <- purge ->

    i    12   13   14   15   16   17   18   19
    p   123  126  125  126  125  136  147  158
    x    +1   +3   -1   +1   -1  +11  +11  +11
        <----------- test ----------->  <- embargo ->

The strategy is deliberately trivial: standardise the feature on the training
window, go long when the standardised value is positive and short when it is
negative. Since a positive standard deviation cannot change a sign, every
signal is the sign of ``x[i] - mean``, so each expected number below is exact
integer arithmetic and none of them depend on the standard deviation at all.

The four answers
----------------

The training features are ``+1 -1 +1 -1 +1 -1 +1 -1 0``, whose mean is exactly
**0**. So the honest signals on the test bars are the signs of ``+1 +3 -1 +1
-1`` -- that is ``+1 +1 -1 +1 -1`` -- and the P&L is

    (+1)(+3) + (+1)(-1) + (-1)(+1) + (+1)(-1) + (-1)(+11) = **-11**

Each leak moves that number, and moves it differently:

``peek``
    Uses ``x[i+1]`` -- tomorrow's feature -- to trade bar ``i``. Signals become
    ``+1 -1 +1 -1 +1`` and P&L becomes ``+3 +1 +1 +1 +11 = +17``. Note the
    sign: leakage does not merely perturb a result, it turns a losing rule into
    a profitable one. That is what makes it worth a gate.

``unpurged``
    Trains on ``1..11``, letting the two purge bars (``+11``, ``+11``) into the
    fit. The mean becomes ``22/11 = 2``, which flips every ``+1`` feature
    short. Signals ``-1 +1 -1 -1 -1``, P&L ``-3 -1 -1 +1 -11 = -15``.

``full_sample``
    Standardises on all nineteen features, mean ``58/19 = 3.05...``, which
    flips the ``+3`` bar short as well. Signals ``-1 -1 -1 -1 -1``, P&L
    ``-3 +1 -1 +1 -11 = -13``.

-11, +17, -15, -13: four distinct numbers, so the fixture discriminates.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Final

from research.walkforward import (WalkForwardWindow, forward_returns,
                                  previous_returns, run_walk_forward,
                                  walk_forward_windows, zscore_fit, zscore_sign)

#: The twenty-bar price path drawn above.
PRICES: Final[tuple[float, ...]] = (
    100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 100.0,
    111.0, 122.0, 123.0, 126.0, 125.0, 126.0, 125.0, 136.0, 147.0, 158.0)

#: Split parameters. ``start=1`` because bar 0 has no previous return.
SPLIT: Final[dict[str, int]] = {
    "train_size": 9, "test_size": 5, "purge": 2, "embargo": 2, "start": 1}

#: The one window those parameters must produce.
EXPECTED_TRAIN: Final[tuple[int, ...]] = tuple(range(1, 10))
EXPECTED_TEST: Final[tuple[int, ...]] = tuple(range(12, 17))

#: Hand-computed answers, derived in the module docstring above.
EXPECTED: Final[dict[str, dict[str, float]]] = {
    "correct": {"pnl": -11.0, "signal_sum": 1.0},
    "peek": {"pnl": 17.0, "signal_sum": 1.0},
    "unpurged": {"pnl": -15.0, "signal_sum": -3.0},
    "full_sample": {"pnl": -13.0, "signal_sum": -5.0},
}


def _series() -> tuple[list[float], list[float]]:
    """Features and outcomes for the fixture path."""
    return previous_returns(PRICES), forward_returns(PRICES)


def windows() -> list[WalkForwardWindow]:
    """The windows the real splitter produces for this fixture."""
    return walk_forward_windows(n_bars=len(PRICES), **SPLIT)


# -- the honest implementation, straight through the real engine ------------

def correct(_windows: list[WalkForwardWindow] | None = None) -> dict[str, float]:
    """Run the fixture through :func:`research.walkforward.run_walk_forward`."""
    features, returns = _series()
    run = run_walk_forward(_windows if _windows is not None else windows(),
                           features, returns, zscore_fit, zscore_sign)
    return {"pnl": run.total_pnl, "signal_sum": run.total_signal}


# -- three leaks, each bypassing the engine on purpose ----------------------
#
# Each one captures the full series from an enclosing scope, which is exactly
# what run_walk_forward's value-passing interface is designed to make awkward.
# They live here, on the judge side of the fence, and nothing in research/
# imports them.

def _mean(values: list[float]) -> float:
    """Arithmetic mean; the fixture never passes an empty list."""
    return sum(values) / len(values)


def _std(values: list[float], mean: float) -> float:
    """Population standard deviation about ``mean``."""
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _sign(value: float, mean: float, std: float) -> float:
    """Sign of the standardised deviation, flat on an exact zero."""
    deviation = value - mean
    if std > 0.0:
        deviation /= std
    return 1.0 if deviation > 0.0 else (-1.0 if deviation < 0.0 else 0.0)


def leak_peek(_windows: list[WalkForwardWindow] | None = None) -> dict[str, float]:
    """Trade bar ``i`` on the feature of bar ``i+1``: the classic look-ahead."""
    features, returns = _series()
    window = (_windows or windows())[0]
    train = [features[i] for i in window.train_index]
    mean = _mean(train)
    std = _std(train, mean)
    pnl = signal_sum = 0.0
    for i in window.test_index:
        position = _sign(features[i + 1], mean, std)   # <-- the leak
        signal_sum += position
        pnl += position * returns[i]
    return {"pnl": pnl, "signal_sum": signal_sum}


def leak_unpurged(_windows: list[WalkForwardWindow] | None = None
                  ) -> dict[str, float]:
    """Let the purge band into the training fit."""
    features, returns = _series()
    window = (_windows or windows())[0]
    unpurged = range(min(window.train_index), min(window.test_index))
    train = [features[i] for i in unpurged]            # <-- the leak
    mean = _mean(train)
    std = _std(train, mean)
    pnl = signal_sum = 0.0
    for i in window.test_index:
        position = _sign(features[i], mean, std)
        signal_sum += position
        pnl += position * returns[i]
    return {"pnl": pnl, "signal_sum": signal_sum}


def leak_full_sample(_windows: list[WalkForwardWindow] | None = None
                     ) -> dict[str, float]:
    """Standardise on the whole series, test window and future included."""
    features, returns = _series()
    window = (_windows or windows())[0]
    whole = features[1:]                                # <-- the leak
    mean = _mean(whole)
    std = _std(whole, mean)
    pnl = signal_sum = 0.0
    for i in window.test_index:
        position = _sign(features[i], mean, std)
        signal_sum += position
        pnl += position * returns[i]
    return {"pnl": pnl, "signal_sum": signal_sum}


#: Every implementation the gate runs, honest one first.
IMPLEMENTATIONS: Final[dict[str, Callable[..., dict[str, float]]]] = {
    "correct": correct,
    "peek": leak_peek,
    "unpurged": leak_unpurged,
    "full_sample": leak_full_sample,
}


# -- window geometry, checked separately from the arithmetic ----------------
#
# Expanding windows over 35 bars, so the embargo actually bites: window 2's
# training range reaches past window 0's test window, and the three bars
# immediately after it must be missing.

GEOMETRY: Final[dict[str, Any]] = {
    "n_bars": 35, "train_size": 10, "test_size": 5, "purge": 2, "embargo": 3,
    "start": 0, "expanding": True}

#: Hand-derived: test windows tile forward from bar 12 in fives; training runs
#: from bar 0 to ``test_start - purge``, minus the three bars after every
#: earlier test window (17-19, 22-24, 27-29).
EXPECTED_GEOMETRY: Final[tuple[dict[str, tuple[int, ...]], ...]] = (
    {"train_index": tuple(range(0, 10)),
     "test_index": tuple(range(12, 17))},
    {"train_index": tuple(range(0, 15)),
     "test_index": tuple(range(17, 22))},
    {"train_index": tuple(range(0, 17)),
     "test_index": tuple(range(22, 27))},
    {"train_index": tuple(range(0, 17)) + (20, 21),
     "test_index": tuple(range(27, 32))},
)
