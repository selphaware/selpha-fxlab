"""Strategies for the backtester skeleton.

Phase 1 has no research content. The one strategy here exists to exercise the
path signal -> order -> fill -> cost -> P&L, and nothing about it is a claim
that a moving-average cross makes money.

Strategies see one bar at a time and hold their own state. That is more
awkward than computing indicator columns over the whole series, and it is the
point: a strategy that is physically handed bars in order cannot accidentally
read a future one.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Strategy(Protocol):
    """Turns a stream of bars into a target position, per pair."""

    def reset(self) -> None:
        """Discard all state, so one instance can run several backtests."""
        ...

    def on_bar(self, bar: Any) -> float:
        """Return the desired signed position in units for ``bar.pair``.

        The returned target may not be acted on before the next bar: the engine
        enforces that, not the strategy.
        """
        ...


class MovingAverageCrossStrategy:
    """Long while the fast mean of mid closes is above the slow mean, else flat.

    Long-or-flat rather than long-or-short, deliberately: it is the smallest
    thing that still produces a complete round trip through the cost model.

    Args:
        fast: Fast window length in bars.
        slow: Slow window length in bars; must exceed ``fast``.
        units: Position size, in units of the base currency.
    """

    def __init__(self, fast: int, slow: int, units: float) -> None:
        if fast < 1 or slow < 1:
            raise ValueError("fast and slow must be >= 1")
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")
        self.fast = int(fast)
        self.slow = int(slow)
        self.units = float(units)
        self._closes: dict[str, deque[float]] = {}

    def reset(self) -> None:
        """Forget every pair seen so far."""
        self._closes.clear()

    def on_bar(self, bar: Any) -> float:
        """Update state with ``bar`` and return the target position."""
        window = self._closes.setdefault(bar.pair, deque(maxlen=self.slow))
        window.append(float(bar.mid_close))
        if len(window) < self.slow:
            return 0.0
        values = list(window)
        fast_mean = sum(values[-self.fast:]) / self.fast
        slow_mean = sum(values) / self.slow
        return self.units if fast_mean > slow_mean else 0.0

    def __repr__(self) -> str:
        """Readable identity, used in the results summary."""
        return (f"MovingAverageCrossStrategy(fast={self.fast}, slow={self.slow}, "
                f"units={self.units:g})")
