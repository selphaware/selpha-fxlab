"""Walk-forward validation with purge and embargo, built so leakage is awkward.

Pre-registered decision #8: walk-forward with purge and embargo, never random
K-fold, tuning only inside training windows. This module is the only place a
research task is allowed to split time.

The design choice worth stating plainly: :func:`run_walk_forward` hands the
callbacks **values, not indices**. ``fit`` receives a list of the training
values and nothing else; ``signal`` receives one bar's value and the fitted
state. A callback therefore has no handle on the full series and cannot peek
past its own bar without deliberately capturing the array from an enclosing
scope -- which is visible in review and is exactly what the gate's leaky
reference implementations do, to prove the known-answer fixtures can tell the
difference.

Structural prevention is not proof, so the research gate also runs a
hand-computed known-answer fixture through this engine and through three
deliberately leaky implementations (test-window peeking, unpurged overlap,
full-sample normalisation), asserting all four produce their own distinct,
written-down number. An engine nobody has watched fail is not an engine.

Geometry, for one window:

    ... train ...  [purge]  ... test ...  [embargo]  ...
                  ^^^^^^^^^                ^^^^^^^^^^
                  gap of exactly           removed from every
                  ``purge`` bars           *later* training set

"""

from __future__ import annotations

import dataclasses
import logging
import math
from typing import Any, Callable, Final, Sequence

from fxlab.ingestion.bars import TimeframeError, offset_alias

_LOG: Final[logging.Logger] = logging.getLogger("research.walkforward")

#: Purge and embargo floors per timeframe, recorded in SPEC2.md pre-reg #8.
#: Keyed by the pandas offset alias the Phase 1 store uses. One trading day at
#: the intraday timeframes, one trading week at 4h and 1d, on the reasoning
#: that FX serial structure at those horizons is a multi-day phenomenon.
PURGE_EMBARGO_BARS: Final[dict[str, int]] = {
    "1min": 1440,
    "5min": 288,
    "15min": 96,
    "30min": 48,
    "1h": 24,
    "4h": 30,
    "1D": 5,
}


class WalkForwardError(Exception):
    """Raised when a split cannot be constructed as asked."""


@dataclasses.dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """One train/test split, as explicit index tuples.

    Explicit indices rather than slice bounds because embargo punches holes in
    the training set: a pair of bounds cannot express "everything up to bar 25
    except 17-19 and 22-24", and a representation that cannot express the
    embargo will quietly drop it.

    Attributes:
        index: Window number, zero-based, in time order.
        train_index: Bar indices usable for fitting.
        test_index: Bar indices to evaluate on, contiguous and forward of train.
    """

    index: int
    train_index: tuple[int, ...]
    test_index: tuple[int, ...]

    @property
    def gap(self) -> int:
        """Bars between the last training bar and the first test bar."""
        return min(self.test_index) - max(self.train_index) - 1

    def to_dict(self) -> dict[str, Any]:
        """Compact, deterministic serialisation for results and ledgers."""
        return {
            "index": self.index,
            "train_start": min(self.train_index),
            "train_end": max(self.train_index),
            "n_train": len(self.train_index),
            "test_start": min(self.test_index),
            "test_end": max(self.test_index),
            "n_test": len(self.test_index),
            "gap": self.gap,
        }


def purge_embargo(timeframe: str, holding_period_bars: int = 1) -> tuple[int, int]:
    """Effective purge and embargo for a timeframe and holding period.

    Both are ``max(table floor, holding period)``, so a strategy that holds
    longer than the floor widens its own purge and embargo instead of
    under-purging. This is the mechanical reading of pre-reg #8's "embargo >= 1
    holding period of the strategy under test".

    Args:
        timeframe: Any spelling :func:`fxlab.ingestion.bars.offset_alias` knows.
        holding_period_bars: Bars a position is held, at that timeframe.

    Returns:
        ``(purge, embargo)`` in bars.

    Raises:
        WalkForwardError: For an unknown timeframe, a timeframe with no
            recorded floor, or a non-positive holding period.
    """
    try:
        alias = offset_alias(timeframe)
    except TimeframeError as exc:
        raise WalkForwardError(str(exc)) from exc
    if alias not in PURGE_EMBARGO_BARS:
        raise WalkForwardError(
            f"no purge/embargo floor recorded for timeframe {alias!r}; "
            f"known: {sorted(PURGE_EMBARGO_BARS)}. Record one in SPEC2.md "
            "pre-reg #8 before using it.")
    if holding_period_bars < 1:
        raise WalkForwardError(
            f"holding_period_bars must be >= 1, got {holding_period_bars}")
    floor = PURGE_EMBARGO_BARS[alias]
    effective = max(floor, int(holding_period_bars))
    return effective, effective


def walk_forward_windows(n_bars: int, train_size: int, test_size: int,
                         purge: int, embargo: int, start: int = 0,
                         expanding: bool = False) -> list[WalkForwardWindow]:
    """Build forward-rolling train/test windows with purge and embargo.

    Args:
        n_bars: Length of the series.
        train_size: Training bars per window (the minimum, when expanding).
        test_size: Test bars per window. Test windows tile forward without
            overlap, so every bar is tested at most once.
        purge: Bars dropped between the end of training and the start of test.
        embargo: Bars after each test window excluded from all later training.
        start: First usable index, for series whose head is undefined (a
            one-bar return has no value at index 0).
        expanding: True for an anchored expanding training window, False for a
            fixed-length rolling one.

    Returns:
        Windows in time order; empty if the series is too short for even one.

    Raises:
        WalkForwardError: For non-positive sizes, negative purge or embargo, or
            a window whose training set is emptied by the embargo.
    """
    if train_size < 1 or test_size < 1:
        raise WalkForwardError("train_size and test_size must both be >= 1")
    if purge < 0 or embargo < 0:
        raise WalkForwardError("purge and embargo must be >= 0")
    if start < 0:
        raise WalkForwardError("start must be >= 0")

    windows: list[WalkForwardWindow] = []
    embargoed: set[int] = set()
    test_start = start + train_size + purge

    while test_start + test_size <= n_bars:
        test_index = tuple(range(test_start, test_start + test_size))
        train_hi = test_start - purge
        train_lo = start if expanding else max(start, train_hi - train_size)
        train_index = tuple(i for i in range(train_lo, train_hi)
                            if i not in embargoed)
        if not train_index:
            raise WalkForwardError(
                f"window {len(windows)} has an empty training set after "
                f"embargo; train range [{train_lo}, {train_hi}) was fully "
                "embargoed. Shorten the embargo or lengthen the training "
                "window rather than dropping the rule.")
        windows.append(WalkForwardWindow(index=len(windows),
                                         train_index=train_index,
                                         test_index=test_index))
        test_end = test_start + test_size
        embargoed.update(range(test_end, min(test_end + embargo, n_bars)))
        test_start = test_end

    _LOG.debug("built %d walk-forward window(s) over %d bars", len(windows), n_bars)
    return windows


@dataclasses.dataclass(frozen=True, slots=True)
class WindowResult:
    """What one window produced.

    Attributes:
        index: Window number.
        n_train: Training bars used.
        n_test: Test bars evaluated.
        signal_sum: Sum of the signals taken, a cheap fingerprint of behaviour.
        pnl: Sum of ``signal * forward return`` over the test bars, in the
            units of ``returns``.
    """

    index: int
    n_train: int
    n_test: int
    signal_sum: float
    pnl: float

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialisation."""
        return {"index": self.index, "n_train": self.n_train,
                "n_test": self.n_test, "signal_sum": self.signal_sum,
                "pnl": self.pnl}


@dataclasses.dataclass(frozen=True, slots=True)
class WalkForwardRun:
    """Aggregate of every window in one walk-forward.

    Attributes:
        windows: Per-window results, in time order.
        total_pnl: Out-of-sample P&L summed across windows.
        total_signal: Signals summed across windows.
    """

    windows: tuple[WindowResult, ...]
    total_pnl: float
    total_signal: float

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialisation, windows included.

        Window-by-window results are carried, not just the aggregate, because
        the scorecard requires them and because an aggregate that hides three
        losing windows behind one winner is the number nobody should see first.
        """
        return {
            "windows": [w.to_dict() for w in self.windows],
            "total_pnl": self.total_pnl,
            "total_signal": self.total_signal,
            "n_windows": len(self.windows),
        }


def run_walk_forward(windows: Sequence[WalkForwardWindow],
                     features: Sequence[float], returns: Sequence[float],
                     fit: Callable[[list[float]], Any],
                     signal: Callable[[Any, float], float]) -> WalkForwardRun:
    """Run a signal over walk-forward windows, fitting on training bars only.

    Args:
        windows: Splits from :func:`walk_forward_windows`.
        features: The value the signal reads, one per bar.
        returns: The forward return realised by acting on bar ``i``, one per
            bar. Computing it is the caller's job; it is an outcome, not an
            input to the decision.
        fit: Called once per window with **only** the training values.
        signal: Called per test bar with the fitted state and **only** that
            bar's value. Returns a position, conventionally -1, 0 or +1.

    Returns:
        The aggregate and per-window results.

    Raises:
        WalkForwardError: If ``features`` and ``returns`` differ in length, or a
            window indexes past the end of either.
    """
    if len(features) != len(returns):
        raise WalkForwardError(
            f"features ({len(features)}) and returns ({len(returns)}) must be "
            "the same length")

    results: list[WindowResult] = []
    for window in windows:
        highest = max(max(window.train_index), max(window.test_index))
        if highest >= len(features):
            raise WalkForwardError(
                f"window {window.index} indexes bar {highest} of a "
                f"{len(features)}-bar series")
        state = fit([float(features[i]) for i in window.train_index])
        pnl = 0.0
        signal_sum = 0.0
        for i in window.test_index:
            position = float(signal(state, float(features[i])))
            signal_sum += position
            pnl += position * float(returns[i])
        results.append(WindowResult(index=window.index,
                                    n_train=len(window.train_index),
                                    n_test=len(window.test_index),
                                    signal_sum=signal_sum, pnl=pnl))

    return WalkForwardRun(windows=tuple(results),
                          total_pnl=sum(r.pnl for r in results),
                          total_signal=sum(r.signal_sum for r in results))


# -- small fitting/signal pieces the fixtures and tasks share ---------------

@dataclasses.dataclass(frozen=True, slots=True)
class ZScoreState:
    """Mean and standard deviation fitted on a training window."""

    mean: float
    std: float


def zscore_fit(values: list[float]) -> ZScoreState:
    """Fit mean and population standard deviation on training values only.

    Raises:
        WalkForwardError: On an empty training window.
    """
    if not values:
        raise WalkForwardError("cannot fit a z-score on an empty window")
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return ZScoreState(mean=mean, std=math.sqrt(variance))


def zscore_sign(state: ZScoreState, value: float) -> float:
    """Position from the sign of the standardised value: +1, -1 or 0 flat.

    A zero standard deviation degrades to the sign of the deviation itself
    rather than dividing by zero; the sign is what the position depends on, and
    it is unchanged by any positive scale.
    """
    deviation = value - state.mean
    if state.std > 0.0:
        deviation /= state.std
    if deviation > 0.0:
        return 1.0
    if deviation < 0.0:
        return -1.0
    return 0.0


def forward_returns(prices: Sequence[float]) -> list[float]:
    """Return ``price[i+1] - price[i]`` per bar, with 0.0 at the final bar.

    The final bar has no forward return; a strategy cannot act on it and the
    walk-forward windows never place a test bar there in practice.
    """
    out = [float(prices[i + 1]) - float(prices[i]) for i in range(len(prices) - 1)]
    out.append(0.0)
    return out


def previous_returns(prices: Sequence[float]) -> list[float]:
    """Return ``price[i] - price[i-1]`` per bar, with 0.0 at bar 0.

    This is the causal feature: at bar ``i`` it is known. Bar 0 has no previous
    bar, which is why splits over this feature start at index 1.
    """
    out = [0.0]
    out.extend(float(prices[i]) - float(prices[i - 1])
               for i in range(1, len(prices)))
    return out
