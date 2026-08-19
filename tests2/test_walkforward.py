"""Walk-forward geometry, the purge/embargo table, and the engine's interface."""

from __future__ import annotations

import pytest

from research import walkforward as wf


def test_purge_embargo_table_matches_the_recorded_values() -> None:
    """SPEC2 pre-reg #8 records these; the code and the spec must agree."""
    assert wf.PURGE_EMBARGO_BARS["5min"] == 288
    assert wf.PURGE_EMBARGO_BARS["30min"] == 48
    assert wf.PURGE_EMBARGO_BARS["1h"] == 24
    assert wf.PURGE_EMBARGO_BARS["4h"] == 30
    assert wf.PURGE_EMBARGO_BARS["1D"] == 5


def test_purge_embargo_accepts_any_known_timeframe_spelling() -> None:
    """``30m`` and ``30min`` are the same timeframe."""
    assert wf.purge_embargo("30m") == wf.purge_embargo("30min") == (48, 48)


def test_embargo_never_falls_below_one_holding_period() -> None:
    """The pre-registered floor: embargo >= 1 holding period."""
    purge, embargo = wf.purge_embargo("1h", holding_period_bars=100)
    assert (purge, embargo) == (100, 100)


def test_unknown_timeframe_is_refused_rather_than_guessed() -> None:
    """A missing floor is a spec decision, not a default."""
    with pytest.raises(wf.WalkForwardError):
        wf.purge_embargo("15s")


def test_windows_leave_exactly_the_purge_gap() -> None:
    """The gap between the last training bar and the first test bar is purge."""
    windows = wf.walk_forward_windows(n_bars=100, train_size=20, test_size=10,
                                      purge=5, embargo=0)
    assert windows
    for window in windows:
        assert window.gap == 5
        assert not set(window.train_index) & set(window.test_index)


def test_test_windows_tile_forward_without_overlap() -> None:
    """Every bar is tested at most once, in time order."""
    windows = wf.walk_forward_windows(n_bars=100, train_size=20, test_size=10,
                                      purge=2, embargo=2)
    tested: list[int] = []
    for window in windows:
        tested.extend(window.test_index)
    assert tested == sorted(tested)
    assert len(tested) == len(set(tested))


def test_embargo_removes_bars_after_earlier_test_windows() -> None:
    """The bars immediately after a test window never train a later one."""
    windows = wf.walk_forward_windows(n_bars=35, train_size=10, test_size=5,
                                      purge=2, embargo=3, expanding=True)
    assert len(windows) == 4
    later_training = set(windows[2].train_index)
    assert {17, 18, 19}.isdisjoint(later_training)
    assert 16 in later_training


def test_training_never_reaches_into_the_future() -> None:
    """Every training index precedes every test index."""
    windows = wf.walk_forward_windows(n_bars=60, train_size=15, test_size=5,
                                      purge=3, embargo=3, expanding=True)
    for window in windows:
        assert max(window.train_index) < min(window.test_index)


def test_a_short_series_yields_no_windows_rather_than_a_bad_one() -> None:
    """Too little data is nothing to validate on, not a smaller window."""
    assert wf.walk_forward_windows(n_bars=10, train_size=20, test_size=5,
                                   purge=2, embargo=2) == []


def test_fit_sees_only_training_values() -> None:
    """The engine hands over values, not the series, so peeking takes effort."""
    features = list(range(20))
    seen: list[list[float]] = []

    def fit(values: list[float]) -> float:
        seen.append(list(values))
        return 0.0

    windows = wf.walk_forward_windows(n_bars=20, train_size=9, test_size=5,
                                      purge=2, embargo=2, start=1)
    wf.run_walk_forward(windows, features, [0.0] * 20, fit,
                        lambda state, value: 1.0)
    assert seen == [[float(i) for i in range(1, 10)]]


def test_signal_sees_only_its_own_bar() -> None:
    """A signal callback is given one value; it cannot look at bar i+1."""
    features = [float(i) for i in range(20)]
    seen: list[float] = []

    windows = wf.walk_forward_windows(n_bars=20, train_size=9, test_size=5,
                                      purge=2, embargo=2, start=1)
    wf.run_walk_forward(windows, features, [0.0] * 20, wf.zscore_fit,
                        lambda state, value: seen.append(value) or 0.0)
    assert seen == [12.0, 13.0, 14.0, 15.0, 16.0]


def test_mismatched_series_lengths_are_refused() -> None:
    """Features and outcomes must line up bar for bar."""
    with pytest.raises(wf.WalkForwardError):
        wf.run_walk_forward([], [1.0, 2.0], [1.0], wf.zscore_fit, wf.zscore_sign)


def test_zscore_sign_is_flat_on_an_exact_zero() -> None:
    """No position rather than an arbitrary one."""
    state = wf.zscore_fit([1.0, -1.0])
    assert wf.zscore_sign(state, 0.0) == 0.0
    assert wf.zscore_sign(state, 1.0) == 1.0
    assert wf.zscore_sign(state, -1.0) == -1.0


def test_zscore_fit_refuses_an_empty_window() -> None:
    """An empty training window is a bug upstream, not a zero mean."""
    with pytest.raises(wf.WalkForwardError):
        wf.zscore_fit([])


def test_previous_and_forward_returns_line_up() -> None:
    """``previous_returns[i+1] == forward_returns[i]`` by construction."""
    prices = [100.0, 101.0, 99.0, 104.0]
    previous = wf.previous_returns(prices)
    forward = wf.forward_returns(prices)
    assert previous[0] == 0.0 and forward[-1] == 0.0
    for i in range(len(prices) - 1):
        assert previous[i + 1] == forward[i]
