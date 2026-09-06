"""The T4 experiment: what each pair looks like, at each horizon, over time.

Seven questions, one per section of ``taskcards/T4.md``, asked of twelve pairs
across five horizons and answered from the stored bars. It produces evidence
and hypotheses and nothing else -- no strategy, no P&L, no scorecard, and no
pair dropped or promoted. Those are the checkpoint's to make, and this card is
explicit that they are not its.

Four decisions shape every number below, and each is here rather than buried:

**Returns never span a hole.** A bar table has weekends, holidays and the hours
the feed served nothing in it. Differencing straight through one produces a
"5-minute return" covering 65 hours, and a single one of those dominates the
kurtosis, the lag-1 autocorrelation and every tail statistic in the section it
lands in. So a consecutive pair is kept only when the two bars are genuinely
adjacent, the survivors are carried as contiguous *spans*, and every memory
estimator works inside a span (:func:`research.stats.segments_of`). The
exception is the daily horizon, where Friday-to-Monday **is** the standard
daily return: there the rule is one to four days, and the Sunday stub bars --
the two or three hours between the weekly open and midnight UTC, a fortieth of
a weekday's ticks -- are dropped rather than counted as days.

**Volatility regimes are conditioned, never fitted.** The regime label at bar
``t`` comes from the returns strictly before ``t``
(:func:`research.stats.trailing_volatility`). Bucketing a return by a
volatility estimate that contains it would put the largest returns in the
highest bucket by construction, and every regime finding would be circular.

**Stability is a first-class result, not a robustness footnote.** Section 5 is
the load-bearing one per the card: every headline statistic is recomputed on
two halves and on rolling two-year windows, and a property whose sign flips is
reported as unstable rather than averaged into a number that describes neither
half.

**Multiple testing is counted, not waved at.** Every hypothesis test is
registered with its family, and Benjamini-Hochberg runs within each family; the
report states the family size next to any claim that rests on a p-value. The
register lives inside the hashed result, so a test cannot be forgotten after
its p-value is seen.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import pathlib
import zoneinfo
from dataclasses import dataclass, field
from typing import Any, Final, Sequence

import numpy as np
import pandas as pd

from fxlab.ingestion.bars import offset_alias
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import SESSIONS, session_labels
from research import calendar_build, stats
from research import crosscheck_class as cc
from research.exclusions import clamp_window, summarise as summarise_exclusions
from research.seal import as_date

_LOG: Final[logging.Logger] = logging.getLogger("research.character")

#: The exchange the roll window is expressed in (pre-reg #4).
NEW_YORK: Final[zoneinfo.ZoneInfo] = zoneinfo.ZoneInfo("America/New_York")

#: Nanoseconds per bar, by pandas offset alias.
STEP_NS: Final[dict[str, int]] = {
    "5min": 300 * 10 ** 9, "30min": 1800 * 10 ** 9, "1h": 3600 * 10 ** 9,
    "4h": 14400 * 10 ** 9, "1D": 86400 * 10 ** 9,
}

#: The daily horizon accepts a gap of up to this many days between bars, so
#: Friday-to-Monday counts and a fortnight's hole does not.
MAX_DAILY_GAP_DAYS: Final[int] = 4

#: Tick-count bands the spread statistics are compared inside (ruling R3).
DENSITY_BANDS: Final[tuple[tuple[str, int, int], ...]] = (
    ("<500", 0, 500), ("500-1k", 500, 1000), ("1k-3k", 1000, 3000),
    ("3k-10k", 3000, 10000), (">=10k", 10000, 10 ** 9),
)

#: The band deep enough to compare across eras without the instrument changing
#: under the comparison. Ruling R3 in one constant.
R3_REFERENCE_BAND: Final[str] = "3k-10k"

#: Labels for how often a rolling window agrees with the full-window sign.
#: Descriptive, not decisions: nothing is dropped or promoted on them.
STABILITY_LABELS: Final[tuple[tuple[float, str], ...]] = (
    (0.90, "STABLE"), (0.75, "MOSTLY-STABLE"), (0.60, "MIXED"),
    (0.0, "UNSTABLE"),
)


# --------------------------------------------------------------------------- #
# One pair at one horizon
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Series:
    """A pair's bars at one timeframe, with its gap-aware return series.

    Attributes:
        pair, alias, label: Identity; ``alias`` is the stored spelling
            (``5min``) and ``label`` the card's (``5m``).
        ts: Bar open times, int64 epoch nanoseconds.
        mid_close, tick_count, spread_pips: Per-bar covariates.
        returns: Log returns, holes removed.
        ret_pos: For each return, the index of its **closing** bar, so any
            per-bar covariate can be aligned to it.
        spans: Contiguous runs inside ``returns``.
        dropped: Consecutive bar pairs the gap rule rejected.
        bars_dropped_stub: Sunday stub bars removed at the daily horizon.
    """

    pair: str
    alias: str
    label: str
    ts: np.ndarray
    mid_close: np.ndarray
    tick_count: np.ndarray
    spread_pips: np.ndarray
    returns: np.ndarray = field(default_factory=lambda: np.zeros(0))
    ret_pos: np.ndarray = field(default_factory=lambda: np.zeros(0, "int64"))
    spans: list[tuple[int, int]] = field(default_factory=list)
    dropped: int = 0
    bars_dropped_stub: int = 0

    def __len__(self) -> int:
        return int(self.returns.size)

    def covariate(self, values: np.ndarray) -> np.ndarray:
        """A per-bar array aligned to the return series."""
        return np.asarray(values)[self.ret_pos]

    def stamps(self) -> pd.DatetimeIndex:
        """Bar open times as a tz-aware index."""
        return pd.DatetimeIndex(self.ts.astype("datetime64[ns]"), tz="UTC")

    def adjacent_share(self) -> float | None:
        """Share of consecutive returns that are genuinely adjacent in time."""
        if self.returns.size < 2:
            return None
        inside = sum(stop - start - 1 for start, stop in self.spans)
        return float(inside / (self.returns.size - 1))


def load_series(loader: Any, pair: str, timeframe: str, start: dt.date,
                end: dt.date) -> Series | None:
    """Read one pair-timeframe through the loader and build its return series.

    Returns ``None`` when ruling R1 leaves no readable window, which is a state
    a caller must handle rather than a zero-length series it can average over.
    """
    # ``as_unit("ns")`` is load-bearing. The stored tables are
    # ``timestamp[us]`` and pandas 3 preserves the unit, so ``asi8`` would hand
    # back microseconds while every step in STEP_NS is nanoseconds -- and the
    # gap rule would then reject every pair in the store rather than the
    # thousandth of them that really span a hole.
    alias = offset_alias(timeframe)
    window = clamp_window(pair, start, end)
    if window is None:
        return None
    # The loader never clamps on its own, so a caller that asked for the
    # permitted part of a range has to say what it gave up -- otherwise ruling
    # R1 costs the appendix six years of AUDUSD and leaves no trace anywhere
    # that it did. This is the trace.
    if window[0] > start:
        loader.note_excluded(
            pair, [(start + dt.timedelta(days=offset)).isoformat()
                   for offset in range((window[0] - start).days)])
    frame = loader.load_bars(pair, alias, start=window[0], end=window[1])
    if not len(frame):
        return None
    stamps = pd.DatetimeIndex(frame["ts"]).as_unit("ns")
    ts = stamps.asi8.astype("int64")
    stub = 0
    if alias == "1D":
        # Sunday's daily bar is the two or three hours between the weekly open
        # and midnight UTC -- a fortieth of a weekday's ticks. Counting it as a
        # day would insert a stub between every Friday and Monday and truncate
        # every Monday return.
        weekday = stamps.dayofweek.to_numpy() < 5
        stub = int((~weekday).sum())
        frame = frame.loc[weekday].reset_index(drop=True)
        stamps = pd.DatetimeIndex(frame["ts"]).as_unit("ns")
        ts = stamps.asi8.astype("int64")
    pip = pair_spec(pair).pip_size
    series = Series(
        pair=pair, alias=alias, label=str(timeframe),
        ts=ts,
        mid_close=np.asarray(frame["mid_close"], dtype="float64"),
        tick_count=np.asarray(frame["tick_count"], dtype="float64"),
        spread_pips=np.asarray(frame["spread_mean"], dtype="float64") / pip,
        bars_dropped_stub=stub)
    step = STEP_NS[alias]
    max_gap = (MAX_DAILY_GAP_DAYS * STEP_NS["1D"] if alias == "1D" else None)
    returns, kept = stats.gap_aware_log_returns(
        series.mid_close, series.ts, step, max_gap_ns=max_gap)
    series.returns = returns
    series.ret_pos = (np.nonzero(kept)[0] + 1).astype("int64")
    series.spans = stats.segments_of(series.ret_pos)
    series.dropped = int((~kept).sum())
    return series


# --------------------------------------------------------------------------- #
# The test register
# --------------------------------------------------------------------------- #

class Register:
    """Every hypothesis test performed, with the family it belongs to.

    The T4 card asks for every test to be a ledgered trial. The ledger records
    *experiments* -- one entry per run of ``research.run``, written before the
    run -- and filling it with three thousand individual t-statistics would
    destroy the thing it is for. So the register is here instead, at the
    granularity the card actually needs: inside the hashed result, so a test
    cannot be dropped from the family after its p-value has been seen, and
    grouped into families so Benjamini-Hochberg has something to run over and
    the report has a number to state next to a claim.
    """

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def add(self, family: str, key: str, result: dict[str, Any],
            statistic: str = "z") -> dict[str, Any]:
        """Record one test and return the result unchanged, for chaining."""
        self._rows.append({
            "family": family, "key": key,
            "statistic": _r(result.get(statistic), 6),
            "p_value": _r(result.get("p_value"), 12),
        })
        return result

    def summarise(self, alpha: float) -> dict[str, Any]:
        """Family sizes, BH thresholds and rejection counts."""
        families: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows:
            families.setdefault(row["family"], []).append(row)
        out: dict[str, Any] = {}
        for name in sorted(families):
            rows = families[name]
            correction = stats.benjamini_hochberg(
                [row["p_value"] for row in rows], alpha=alpha)
            for row, q in zip(rows, correction["q_values"]):
                row["q_value"] = _r(q, 12)
            out[name] = {
                "tests": len(rows),
                "usable": correction["family_size"],
                "alpha": alpha,
                "bh_threshold": _r(correction["threshold"], 12),
                "rejected": correction["rejected"],
                "rejected_share": _r(
                    correction["rejected"] / correction["family_size"]
                    if correction["family_size"] else None, 4),
            }
        return {"families": out, "total_tests": len(self._rows),
                "alpha": alpha}

    def rows(self) -> list[dict[str, Any]]:
        """Every registered test, sorted for a stable hash."""
        return sorted(self._rows, key=lambda r: (r["family"], r["key"]))

    def q_lookup(self) -> dict[str, float | None]:
        """``family|key`` to BH q-value, for a report that wants both."""
        return {f"{row['family']}|{row['key']}": row.get("q_value")
                for row in self._rows}


def _r(value: Any, places: int) -> Any:
    """Round to a fixed number of places, passing ``None`` and infinities on.

    Everything stored in the payload goes through here. The result document is
    hashed and the hash must reproduce exactly, so a float that carries its
    last two bits from the order a sum happened in is a float that will
    eventually fail the gate for no reason a message can name.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(number):
        return None
    return round(number, places)


# --------------------------------------------------------------------------- #
# Section 1 -- return distributions by horizon
# --------------------------------------------------------------------------- #

BP: Final[float] = 1e4


def section_returns(series: Series, register: Register) -> dict[str, Any]:
    """Moments, tails and normality for one pair at one horizon."""
    returns = series.returns
    moments = stats.moments(returns)
    tails = stats.tail_profile(returns)
    jb = stats.jarque_bera(returns)
    register.add("jarque_bera", f"{series.pair}|{series.label}", jb,
                 statistic="statistic")
    span_days = ((series.ts[-1] - series.ts[0]) / STEP_NS["1D"]
                 if series.ts.size > 1 else 0.0)
    years = span_days / 365.25 if span_days else None
    sd = moments.get("sd")
    return {
        "n": moments["n"],
        "bars": int(series.ts.size),
        "dropped_pairs": series.dropped,
        "stub_bars_dropped": series.bars_dropped_stub,
        "adjacent_share": _r(series.adjacent_share(), 6),
        "spans": len(series.spans),
        "first": _iso(series.ts[0]) if series.ts.size else None,
        "last": _iso(series.ts[-1]) if series.ts.size else None,
        "mean_bp": _r((moments["mean"] or 0.0) * BP, 5),
        "sd_bp": _r((sd or 0.0) * BP, 4),
        "sd_annualised_pct": _r(
            (sd * math.sqrt(moments["n"] / years) * 100.0)
            if sd and years and years > 0 else None, 3),
        "skew": _r(moments["skew"], 4),
        "excess_kurtosis": _r(moments["excess_kurtosis"], 3),
        "jb_statistic": _r(jb["statistic"], 1),
        "jb_p_value": _r(jb["p_value"], 12),
        "tail_ratio_p99": _r(tails.get("tail_ratio_0.99"), 4),
        "tail_ratio_p999": _r(tails.get("tail_ratio_0.999"), 4),
        "tail_ratio_p9999": _r(tails.get("tail_ratio_0.9999"), 4),
        "q999_bp": _r(tails.get("q0.999", 0.0) * BP, 3),
        "share_beyond_4sd": _r(tails.get("share_beyond_4sd"), 8),
        "excess_beyond_4sd": _r(tails.get("excess_beyond_4sd"), 3),
        "count_beyond_4sd": tails.get("count_beyond_4sd"),
        "gaussian_count_beyond_4sd": _r(
            tails.get("gaussian_count_beyond_4sd"), 3),
        "count_beyond_6sd": tails.get("count_beyond_6sd"),
        "gaussian_count_beyond_6sd": _r(
            tails.get("gaussian_count_beyond_6sd"), 5),
        "excess_beyond_6sd": _r(tails.get("excess_beyond_6sd"), 3),
        "extremes": _extremes(series),
    }


def _extremes(series: Series, count: int = 3) -> list[dict[str, Any]]:
    """The largest absolute returns, with the bar each one closed on.

    A kurtosis of sixty-five thousand is not a distributional property, it is
    an event with a date, and a table that reports the first without the second
    leaves a reader unable to tell a fat tail from a single afternoon. These
    are the afternoons.
    """
    if series.returns.size == 0:
        return []
    order = np.argsort(np.abs(series.returns))[::-1][:count]
    stamps = series.ts[series.ret_pos]
    return [{"ts": pd.Timestamp(int(stamps[i])).isoformat(),
             "return_bp": _r(float(series.returns[i]) * BP, 3),
             "sigmas": _r(float(abs(series.returns[i])
                                / series.returns.std(ddof=1)), 1)}
            for i in order]


# --------------------------------------------------------------------------- #
# Section 2 -- stationarity and memory
# --------------------------------------------------------------------------- #

def section_memory(series: Series, register: Register, *, acf_lags: int,
                   vr_horizons: Sequence[int], adf_lags: int,
                   continuation_horizons: Sequence[int]) -> dict[str, Any]:
    """Unit-root sanity, the variance-ratio profile, autocorrelation, signs."""
    key = f"{series.pair}|{series.label}"
    returns, spans = series.returns, series.spans

    # The level series is rebuilt from the gap-filtered returns rather than
    # taken from the price column, so the regression differences the same
    # series everything else here measures, and never across a weekend.
    level = np.concatenate(([0.0], np.cumsum(returns)))
    adf_level = stats.adf(level, lags=adf_lags)
    adf_returns = stats.adf(returns, lags=adf_lags)

    acf_rows = []
    for lag in range(1, acf_lags + 1):
        out = stats.autocorr_at(returns, spans, lag)
        acf_rows.append({"lag": lag, "rho": _r(out["rho"], 6),
                         "n": out["n"], "p_value": _r(out["p_value"], 12)})
    rho = [row["rho"] or 0.0 for row in acf_rows]
    box = stats.ljung_box(rho, int(returns.size))
    register.add("return_ljung_box", key, box, statistic="statistic")

    vr_rows = []
    for q in vr_horizons:
        out = stats.variance_ratio_segments(returns, spans, int(q))
        register.add("variance_ratio", f"{key}|q{int(q)}", out)
        vr_rows.append({"q": int(q), "vr": _r(out["vr"], 5),
                        "z": _r(out["z"], 3), "p_value": _r(out["p_value"], 12),
                        "se": _r(out["se"], 6)})

    signs = stats.sign_persistence_at(returns, spans)
    register.add("sign_persistence", key, signs)

    continuation = []
    for horizon in continuation_horizons:
        out = stats.forward_continuation(returns, spans, int(horizon))
        register.add("forward_continuation", f"{key}|h{int(horizon)}", out)
        continuation.append({"horizon": int(horizon), "rho": _r(out["rho"], 6),
                             "z": _r(out["z"], 3),
                             "p_value": _r(out["p_value"], 12)})

    return {
        "adf_levels": {"tau": _r(adf_level["tau"], 3), "lags": adf_lags,
                       "rejects_unit_root_1pct":
                           adf_level.get("rejects_unit_root_1pct")},
        "adf_returns": {"tau": _r(adf_returns["tau"], 3), "lags": adf_lags,
                        "rejects_unit_root_1pct":
                            adf_returns.get("rejects_unit_root_1pct")},
        "acf": acf_rows,
        "acf_abs_sum": _r(float(np.abs(np.array(rho)).sum()), 5),
        "ljung_box": {"lags": box["lags"], "statistic": _r(box["statistic"], 1),
                      "p_value": _r(box["p_value"], 12)},
        "variance_ratio": vr_rows,
        "sign_persistence": {"n": signs["n"], "p_same": _r(signs["p_same"], 6),
                             "z": _r(signs["z"], 3),
                             "p_value": _r(signs["p_value"], 12)},
        "forward_continuation": continuation,
    }


# --------------------------------------------------------------------------- #
# Section 3 -- volatility
# --------------------------------------------------------------------------- #

def section_volatility(series: Series, register: Register, *,
                       vol_acf_lags: int, vol_window: int,
                       regime_horizon: int) -> dict[str, Any]:
    """Clustering, regimes, and how memory differs inside each regime."""
    key = f"{series.pair}|{series.label}"
    returns, spans = series.returns, series.spans
    absolute = np.abs(returns)

    acf_abs = [stats.autocorr_at(absolute, spans, lag)["rho"]
               for lag in range(1, vol_acf_lags + 1)]
    acf_sq = [stats.autocorr_at(returns ** 2, spans, lag)["rho"]
              for lag in range(1, vol_acf_lags + 1)]
    box_abs = stats.ljung_box([v or 0.0 for v in acf_abs], int(returns.size))
    register.add("volatility_ljung_box", key, box_abs, statistic="statistic")

    regimes = _volatility_regimes(series, register, vol_window=vol_window,
                                  horizon=regime_horizon)
    return {
        "acf_abs": [_r(v, 6) for v in acf_abs],
        "acf_squared": [_r(v, 6) for v in acf_sq],
        "half_life_abs": _r(stats.decay_half_life(
            [v for v in acf_abs if v is not None]), 2),
        "half_life_squared": _r(stats.decay_half_life(
            [v for v in acf_sq if v is not None]), 2),
        "ljung_box_abs": {"lags": box_abs["lags"],
                          "statistic": _r(box_abs["statistic"], 1),
                          "p_value": _r(box_abs["p_value"], 12)},
        "regimes": regimes,
    }


def _volatility_regimes(series: Series, register: Register, *,
                        vol_window: int, horizon: int) -> dict[str, Any]:
    """Terciles of trailing volatility, and the memory inside each.

    The label at bar ``t`` is built from returns strictly before ``t``. That
    shift is what separates a conditioning variable from a circular one, and it
    is why the low tercile is not simply "the bars whose returns were small".
    """
    returns, spans = series.returns, series.spans
    trailing = stats.trailing_volatility(returns, vol_window)
    edges = stats.tercile_edges(trailing)
    if edges is None:
        return {"window": vol_window, "usable": False}
    low, high = edges
    masks = {
        "low": np.isfinite(trailing) & (trailing <= low),
        "mid": np.isfinite(trailing) & (trailing > low) & (trailing <= high),
        "high": np.isfinite(trailing) & (trailing > high),
    }
    rows: dict[str, Any] = {}
    for name, mask in masks.items():
        selected = returns[mask]
        rho = stats.autocorr_at(returns, spans, 1, select=mask)
        signs = stats.sign_persistence_at(returns, spans, select=mask)
        continuation = stats.forward_continuation(returns, spans, horizon,
                                                  select=mask)
        register.add("regime_autocorr", f"{series.pair}|{series.label}|{name}",
                     rho)
        register.add("regime_continuation",
                     f"{series.pair}|{series.label}|{name}", continuation)
        rows[name] = {
            "bars": int(mask.sum()),
            "mean_abs_bp": _r(float(np.abs(selected).mean()) * BP
                              if selected.size else None, 4),
            "sd_bp": _r(float(selected.std(ddof=1)) * BP
                        if selected.size > 1 else None, 4),
            "rho1": _r(rho["rho"], 6),
            "rho1_p_value": _r(rho["p_value"], 12),
            "p_same": _r(signs["p_same"], 6),
            "p_same_p_value": _r(signs["p_value"], 12),
            "continuation_rho": _r(continuation["rho"], 6),
            "continuation_p_value": _r(continuation["p_value"], 12),
        }
    return {
        "window": vol_window,
        "usable": True,
        "horizon": horizon,
        "edges_bp": [_r(low * BP, 4), _r(high * BP, 4)],
        "by_regime": rows,
        "high_over_low_vol": _r(
            (rows["high"]["sd_bp"] / rows["low"]["sd_bp"])
            if rows["low"]["sd_bp"] else None, 3),
    }


# --------------------------------------------------------------------------- #
# Section 4 -- session and spread structure
# --------------------------------------------------------------------------- #

def in_roll_window(stamps: pd.DatetimeIndex, start_hour: int,
                   end_hour: int) -> np.ndarray:
    """Which bar opens fall inside the derived roll window (pre-reg #4).

    Derived per instant through ``zoneinfo``, never pinned to a UTC hour: 17:00
    New York is 21:00 UTC in summer and 22:00 in winter, and a rule written in
    UTC is wrong for half of every year.
    """
    local = stamps.tz_convert(NEW_YORK)
    hour = local.hour.to_numpy()
    return (hour >= start_hour) & (hour < end_hour)


def density_band(counts: np.ndarray) -> np.ndarray:
    """Label each bar with its tick-count band (ruling R3)."""
    labels = np.full(counts.size, DENSITY_BANDS[-1][0], dtype=object)
    for name, low, high in DENSITY_BANDS:
        labels[(counts >= low) & (counts < high)] = name
    return labels


def section_sessions(series: Series, register: Register, *,
                     roll: tuple[int, int]) -> dict[str, Any]:
    """Returns, volatility, spread and density by session, and the roll hour.

    Computed on the hourly series. The roll window is two hours wide and the
    session map moves with three separate daylight-saving rules, so an hourly
    grain is the coarsest that resolves both and the finest at which a spread
    statistic is not mostly counting quotes.
    """
    stamps = series.stamps()
    labels = session_labels(stamps)
    roll_mask = in_roll_window(stamps, roll[0], roll[1])
    absolute = np.abs(series.returns) * BP
    ret_labels = labels[series.ret_pos]
    ret_roll = roll_mask[series.ret_pos]

    by_session: dict[str, Any] = {}
    for name in SESSIONS:
        bars = labels == name
        selected = ret_labels == name
        by_session[name] = _regime_row(series, bars, selected, absolute,
                                       register, f"session|{name}")

    roll_row = _regime_row(series, roll_mask, ret_roll, absolute, register,
                           "roll")
    off_row = _regime_row(series, ~roll_mask, ~ret_roll, absolute, register,
                          "off-roll")

    bands = _spread_by_band(series, labels, roll_mask)
    spread_vs_vol = _spread_versus_volatility(series, absolute, register)
    return {
        "timeframe": series.label,
        "by_session": by_session,
        "roll_window_ny": [roll[0], roll[1]],
        "roll": roll_row,
        "off_roll": off_row,
        "roll_vs_rest": {
            "vol_ratio": _r((roll_row["mean_abs_bp"] / off_row["mean_abs_bp"])
                            if off_row["mean_abs_bp"] else None, 3),
            "spread_ratio": _r(
                (roll_row["median_spread_pips"]
                 / off_row["median_spread_pips"])
                if off_row["median_spread_pips"] else None, 3),
            "density_ratio": _r(
                (roll_row["median_ticks"] / off_row["median_ticks"])
                if off_row["median_ticks"] else None, 3),
        },
        "spread_by_density_band": bands,
        "spread_versus_volatility": spread_vs_vol,
    }


def _regime_row(series: Series, bar_mask: np.ndarray, ret_mask: np.ndarray,
                absolute_bp: np.ndarray, register: Register,
                key: str) -> dict[str, Any]:
    """One slice of the hourly series, described the same way every time."""
    returns = series.returns[ret_mask]
    spread = series.spread_pips[bar_mask]
    ticks = series.tick_count[bar_mask]
    rho = stats.autocorr_at(series.returns, series.spans, 1, select=ret_mask)
    register.add("session_autocorr", f"{series.pair}|{key}", rho)
    return {
        "bars": int(bar_mask.sum()),
        "returns": int(ret_mask.sum()),
        "mean_abs_bp": _r(float(absolute_bp[ret_mask].mean())
                          if ret_mask.any() else None, 4),
        "sd_bp": _r(float(returns.std(ddof=1)) * BP
                    if returns.size > 1 else None, 4),
        "mean_return_bp": _r(float(returns.mean()) * BP
                             if returns.size else None, 5),
        "median_spread_pips": _r(float(np.median(spread))
                                 if spread.size else None, 4),
        "p90_spread_pips": _r(float(np.quantile(spread, 0.9))
                              if spread.size else None, 4),
        "median_ticks": _r(float(np.median(ticks)) if ticks.size else None, 1),
        "rho1": _r(rho["rho"], 6),
    }


def _spread_by_band(series: Series, labels: np.ndarray,
                    roll_mask: np.ndarray) -> dict[str, Any]:
    """Spread statistics inside tick-count bands -- ruling R3, as a table.

    R3 says a spread comparison across eras must control for ticks per hour,
    because a percentile taken over a thousand-tick hour and one taken over six
    thousand are not the same instrument. The control is this: compare inside a
    band, never across one.
    """
    bands = density_band(series.tick_count)
    out: dict[str, Any] = {}
    for name, _low, _high in DENSITY_BANDS:
        mask = bands == name
        if not mask.any():
            continue
        spread = series.spread_pips[mask]
        out[name] = {
            "bars": int(mask.sum()),
            "median_spread_pips": _r(float(np.median(spread)), 4),
            "p90_spread_pips": _r(float(np.quantile(spread, 0.9)), 4),
            "share_in_roll": _r(float(roll_mask[mask].mean()), 4),
            "sessions": {
                name2: _r(float(np.median(series.spread_pips[mask & (labels == name2)]))
                          if (mask & (labels == name2)).any() else None, 4)
                for name2 in SESSIONS},
        }
    return out


def _spread_versus_volatility(series: Series, absolute_bp: np.ndarray,
                              register: Register) -> dict[str, Any]:
    """How the spread moves with volatility, overall and inside a band.

    The unconditional correlation mixes two things: spreads and volatility both
    change with the era and with the hour of day. The within-band figure is the
    same question asked where the instrument is held still (R3), and where the
    two disagree the unconditional one is the one to distrust.
    """
    spread = series.covariate(series.spread_pips)
    ticks = series.covariate(series.tick_count)
    overall = stats.pearson(np.log(np.maximum(spread, 1e-9)),
                            np.log(np.maximum(absolute_bp, 1e-9)))
    bands = density_band(ticks)
    within: dict[str, Any] = {}
    for name, _low, _high in DENSITY_BANDS:
        mask = bands == name
        if int(mask.sum()) < stats.MIN_SAMPLE:
            continue
        within[name] = _r(stats.pearson(
            np.log(np.maximum(spread[mask], 1e-9)),
            np.log(np.maximum(absolute_bp[mask], 1e-9))), 4)
    density_vs_vol = stats.pearson(np.log(np.maximum(ticks, 1.0)),
                                   np.log(np.maximum(absolute_bp, 1e-9)))
    density_vs_spread = stats.pearson(np.log(np.maximum(ticks, 1.0)),
                                      np.log(np.maximum(spread, 1e-9)))
    return {
        "log_spread_vs_log_abs_return": _r(overall, 4),
        "within_density_band": within,
        "log_ticks_vs_log_abs_return": _r(density_vs_vol, 4),
        "log_ticks_vs_log_spread": _r(density_vs_spread, 4),
    }


def section_clock(series: Series) -> dict[str, Any]:
    """Volatility, spread and density by hour of day and day of week."""
    stamps = series.stamps()
    hour = stamps.hour.to_numpy()
    weekday = stamps.dayofweek.to_numpy()
    absolute = np.abs(series.returns) * BP
    ret_hour = hour[series.ret_pos]
    ret_dow = weekday[series.ret_pos]

    def bucket(bar_mask: np.ndarray, ret_mask: np.ndarray) -> dict[str, Any]:
        spread = series.spread_pips[bar_mask]
        ticks = series.tick_count[bar_mask]
        return {
            "bars": int(bar_mask.sum()),
            "mean_abs_bp": _r(float(absolute[ret_mask].mean())
                              if ret_mask.any() else None, 4),
            "median_spread_pips": _r(float(np.median(spread))
                                     if spread.size else None, 4),
            "median_ticks": _r(float(np.median(ticks))
                               if ticks.size else None, 1),
        }

    return {
        "by_hour_utc": {f"{h:02d}": bucket(hour == h, ret_hour == h)
                        for h in range(24)},
        "by_weekday": {name: bucket(weekday == index, ret_dow == index)
                       for index, name in enumerate(
                           ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
                       if int((weekday == index).sum())},
    }


# --------------------------------------------------------------------------- #
# Section 5 -- stability
# --------------------------------------------------------------------------- #

def headline(series: Series, mask: np.ndarray | None = None) -> dict[str, Any]:
    """The statistics the character table ranks on, for one slice of a series.

    Deliberately short. A headline set that grows to twenty entries is one
    nobody checks for stability, and stability is the section this card calls
    load-bearing.
    """
    if mask is None:
        returns, spans = series.returns, series.spans
        positions = np.arange(series.returns.size)
    else:
        positions = np.nonzero(mask)[0]
        if positions.size < stats.MIN_SAMPLE:
            return {"n": int(positions.size)}
        returns = series.returns[positions]
        spans = stats.segments_of(series.ret_pos[positions])
    if returns.size < stats.MIN_SAMPLE:
        return {"n": int(returns.size)}
    moments = stats.moments(returns)
    rho1 = stats.autocorr_at(returns, spans, 1)
    vr4 = stats.variance_ratio_segments(returns, spans, 4)
    signs = stats.sign_persistence_at(returns, spans)
    vol_rho1 = stats.autocorr_at(np.abs(returns), spans, 1)
    return {
        "n": moments["n"],
        "sd_bp": _r((moments["sd"] or 0.0) * BP, 4),
        "excess_kurtosis": _r(moments["excess_kurtosis"], 3),
        "rho1": _r(rho1["rho"], 6),
        "vr4": _r(vr4["vr"], 5),
        "vr4_z": _r(vr4["z"], 3),
        "p_same": _r(signs["p_same"], 6),
        "vol_rho1": _r(vol_rho1["rho"], 6),
    }


#: The headline statistics whose *sign* is the thing stability is about. ``sd``
#: and ``excess_kurtosis`` are positive by construction, so a sign test on them
#: would be vacuous; they are checked by rank instead.
SIGNED_HEADLINES: Final[tuple[str, ...]] = ("rho1", "p_same", "vr4",
                                            "vol_rho1")

#: The value each signed statistic is compared against. ``p_same`` and ``vr4``
#: have a null of their own that is not zero, and testing them against zero
#: would report every pair as trending forever.
HEADLINE_NULL: Final[dict[str, float]] = {
    "rho1": 0.0, "p_same": 0.5, "vr4": 1.0, "vol_rho1": 0.0,
}


def section_stability(series: Series, *, split: dt.date, window_years: int,
                      step_months: int) -> dict[str, Any]:
    """Split-half and rolling-window recomputation of the headline statistics.

    A property that flips sign between halves is reported as unstable, not
    averaged: the average of a trend and a reversion is a number that describes
    neither regime and would be traded in both.
    """
    stamps = series.stamps()
    ret_stamps = stamps[series.ret_pos]
    split_ns = int(pd.Timestamp(split, tz="UTC").value)
    ret_ns = ret_stamps.asi8

    first = headline(series, ret_ns < split_ns)
    second = headline(series, ret_ns >= split_ns)
    whole = headline(series)

    flips = {}
    for name in SIGNED_HEADLINES:
        null = HEADLINE_NULL[name]
        a, b = first.get(name), second.get(name)
        flips[name] = {
            "first": a, "second": b, "whole": whole.get(name),
            "same_side": (None if a is None or b is None
                          else bool((a - null) * (b - null) > 0)),
            "change": _r(None if a is None or b is None else b - a, 6),
        }

    windows = _rolling_windows(series, ret_ns, window_years, step_months)
    rolling: dict[str, Any] = {}
    for name in SIGNED_HEADLINES:
        null = HEADLINE_NULL[name]
        values = [w["stats"].get(name) for w in windows]
        usable = [v for v in values if v is not None]
        reference = whole.get(name)
        if reference is None or not usable:
            rolling[name] = {"windows": len(usable), "sign_agreement": None,
                             "label": "UNMEASURED"}
            continue
        agree = sum(1 for v in usable if (v - null) * (reference - null) > 0)
        share = agree / len(usable)
        rolling[name] = {
            "windows": len(usable),
            "sign_agreement": _r(share, 4),
            "label": _stability_label(share),
            "min": _r(min(usable), 6),
            "max": _r(max(usable), 6),
            "median": _r(float(np.median(usable)), 6),
        }
    return {
        "split_date": split.isoformat(),
        "first_half": first,
        "second_half": second,
        "whole": whole,
        "split_half": flips,
        "rolling": rolling,
        "rolling_windows": windows,
    }


def _stability_label(share: float) -> str:
    """Turn a sign-agreement share into the label the character table prints."""
    for threshold, label in STABILITY_LABELS:
        if share >= threshold:
            return label
    return STABILITY_LABELS[-1][1]


def _rolling_windows(series: Series, ret_ns: np.ndarray, window_years: int,
                     step_months: int) -> list[dict[str, Any]]:
    """Headline statistics on overlapping windows of a fixed span."""
    if ret_ns.size == 0:
        return []
    first = pd.Timestamp(ret_ns[0]).tz_localize("UTC")
    last = pd.Timestamp(ret_ns[-1]).tz_localize("UTC")
    out: list[dict[str, Any]] = []
    start = pd.Timestamp(year=first.year, month=first.month, day=1, tz="UTC")
    while True:
        stop = start + pd.DateOffset(years=window_years)
        if stop > last + pd.Timedelta(days=1):
            break
        mask = (ret_ns >= start.value) & (ret_ns < stop.value)
        out.append({
            "start": start.date().isoformat(),
            "end": (stop - pd.Timedelta(days=1)).date().isoformat(),
            "stats": headline(series, mask),
        })
        start = start + pd.DateOffset(months=step_months)
    return out


# --------------------------------------------------------------------------- #
# Section 6 -- tick density (ruling R4)
# --------------------------------------------------------------------------- #

def density_profile(series: Series, register: Register) -> dict[str, Any]:
    """Density by year and by session, and what it tracks.

    Ruling R4 forbids treating a tick count as a volume or activity proxy until
    a T4 card has characterised the series. This is that characterisation, and
    it is the reason section 6 of the report ends with an explicit verdict
    rather than a table.
    """
    stamps = series.stamps()
    years = stamps.year.to_numpy()
    labels = session_labels(stamps)
    bands = density_band(series.tick_count)
    absolute = np.abs(series.returns) * BP
    ret_years = years[series.ret_pos]

    by_year: dict[str, Any] = {}
    for year in sorted(set(int(y) for y in years)):
        mask = years == year
        ret_mask = ret_years == year
        ticks = series.tick_count[mask]
        spread = series.spread_pips[mask]
        # Ruling R3: a spread compared across eras must be compared inside a
        # tick-count band, because a median taken over a thousand-tick hour and
        # one taken over six thousand are not the same instrument. The
        # uncontrolled median is reported beside it so a reader can see how
        # much of the era trend the control removes.
        in_band = mask & (bands == R3_REFERENCE_BAND)
        banded = series.spread_pips[in_band]
        by_year[str(year)] = {
            "hours": int(mask.sum()),
            "median_ticks": _r(float(np.median(ticks)), 1),
            "mean_ticks": _r(float(ticks.mean()), 1),
            "median_spread_pips": _r(float(np.median(spread)), 4),
            "hours_in_reference_band": int(in_band.sum()),
            "median_spread_pips_in_band": _r(float(np.median(banded))
                                             if banded.size else None, 4),
            "p90_spread_pips_in_band": _r(float(np.quantile(banded, 0.9))
                                          if banded.size else None, 4),
            "realised_vol_bp": _r(float(series.returns[ret_mask].std(ddof=1))
                                  * BP if int(ret_mask.sum()) > 1 else None, 4),
            "by_session": {
                name: _r(float(np.median(series.tick_count[mask & (labels == name)]))
                         if (mask & (labels == name)).any() else None, 1)
                for name in SESSIONS},
        }

    usable_years = [y for y, row in by_year.items()
                    if row["realised_vol_bp"] is not None]
    density_series = [by_year[y]["median_ticks"] for y in usable_years]
    vol_series = [by_year[y]["realised_vol_bp"] for y in usable_years]
    spread_series = [by_year[y]["median_spread_pips"] for y in usable_years]
    banded_years = [y for y in usable_years
                    if by_year[y]["median_spread_pips_in_band"] is not None]

    ticks_bar = series.covariate(series.tick_count)
    return {
        "by_year": by_year,
        "annual_density_vs_vol_pearson": _r(
            stats.pearson(np.log(np.maximum(density_series, 1.0)),
                          np.array(vol_series, dtype="float64")), 4),
        "annual_density_vs_vol_spearman": _r(
            stats.spearman(density_series, vol_series), 4),
        "annual_density_vs_spread_spearman": _r(
            stats.spearman(density_series, spread_series), 4),
        "annual_density_vs_banded_spread_spearman": _r(
            stats.spearman(
                [by_year[y]["median_ticks"] for y in banded_years],
                [by_year[y]["median_spread_pips_in_band"]
                 for y in banded_years]), 4),
        "reference_band": R3_REFERENCE_BAND,
        "bar_density_vs_vol_pearson": _r(
            stats.pearson(np.log(np.maximum(ticks_bar, 1.0)),
                          np.log(np.maximum(absolute, 1e-9))), 4),
        "year_over_year": _year_over_year(by_year),
    }


def _year_over_year(by_year: dict[str, Any]) -> list[dict[str, Any]]:
    """Log change in median density from one year to the next."""
    years = sorted(by_year)
    out: list[dict[str, Any]] = []
    for previous, current in zip(years, years[1:]):
        before = by_year[previous]["median_ticks"]
        after = by_year[current]["median_ticks"]
        if not before or not after:
            continue
        out.append({"year": current,
                    "log_change": _r(math.log(after / before), 5)})
    return out


def density_breaks(profiles: dict[str, Any], multiple: float) -> dict[str, Any]:
    """Structural-break candidates in the density series.

    The rule is stated rather than tuned: a pair-year is a candidate when its
    year-over-year log change in median density exceeds ``multiple`` times the
    median absolute year-over-year change across the whole store. That scale is
    derived from the data being described rather than chosen against the answer,
    which is the only thing that keeps a break list from being a list of the
    years somebody expected.
    """
    changes = [abs(row["log_change"]) for profile in profiles.values()
               for row in profile["year_over_year"]]
    if not changes:
        return {"threshold": None, "candidates": []}
    scale = float(np.median(changes))
    threshold = multiple * scale
    candidates = [
        {"pair": pair, "year": row["year"], "log_change": row["log_change"],
         "ratio": _r(math.exp(row["log_change"]), 3)}
        for pair, profile in sorted(profiles.items())
        for row in profile["year_over_year"]
        if abs(row["log_change"]) > threshold]
    candidates.sort(key=lambda c: (-abs(c["log_change"]), c["pair"], c["year"]))
    by_year: dict[str, int] = {}
    for candidate in candidates:
        by_year[candidate["year"]] = by_year.get(candidate["year"], 0) + 1
    return {
        "rule": (f"|d log median ticks per hour| > {multiple} x the store-wide "
                 "median absolute year-over-year change"),
        "median_abs_change": _r(scale, 5),
        "threshold": _r(threshold, 5),
        "pair_years_examined": len(changes),
        "candidates": candidates,
        "by_year": {k: by_year[k] for k in sorted(by_year)},
        "years_where_most_pairs_move": sorted(
            (y for y, n in by_year.items() if n >= 6)),
    }


# --------------------------------------------------------------------------- #
# Section 7 -- the unexplained empty dates
# --------------------------------------------------------------------------- #

#: The empty-date classifier moved to :mod:`research.calendar_build` in T5
#: Step 0, which is where the rows it classifies are produced. Re-exported
#: here because this card's report and tests ask for it by this name, and
#: because two modules owning one verdict is how the two stop agreeing.
#:
#: ``r1_artefact`` is the class nobody expected and the one Step 0 repaired.
#: T3 filtered a date's empty pairs down to the ones research may read, and
#: ruling R1 removes AUDUSD before 2011 -- so a date on which *only* AUDUSD
#: went quiet in 2008 survived as a row whose pair list was then empty, and
#: was counted as an unexplained date. It is not a fact about the readable
#: universe at all; it is the exclusion filter casting a shadow. Since Step 0
#: :func:`research.calendar_build.classify` files those rows under
#: ``excluded_only`` instead, and this section reads both lists so that what
#: T4 measured stays exactly what T4 measured.
EMPTY_CLASSES: Final[tuple[str, ...]] = calendar_build.EMPTY_CLASSES
EMPTY_KINDS: Final[dict[str, str]] = calendar_build.EMPTY_KINDS
WEEK_EDGE_DAYS: Final[tuple[str, ...]] = calendar_build.WEEK_EDGE_DAYS
WEEK_EDGE_MAX_HOURS: Final[int] = calendar_build.WEEK_EDGE_MAX_HOURS
classify_empty_date = calendar_build.classify_empty_date


def section_empties(store: pathlib.Path, pairs: Sequence[str], start: dt.date,
                    end: dt.date, *, min_empty_hours: int,
                    min_pairs_partial: int) -> dict[str, Any]:
    """Characterise the empty dates the holiday calendar does not explain.

    Re-derived here through the same :mod:`research.calendar_build` code T3
    used, rather than read out of T3's result. Two cards deriving the same set
    two ways is how the two quietly stop agreeing; one function called twice
    cannot.
    """
    scanned = calendar_build.scan(store, pairs, start, end)
    classified = calendar_build.classify(
        scanned, pairs, min_empty_hours=min_empty_hours,
        min_pairs_partial=min_pairs_partial)
    static = calendar_build.static_holidays(range(start.year, end.year + 1))
    # Both lists, deliberately. Since T5 Step 0 the dates whose only quiet
    # pairs were excluded ones live under ``excluded_only`` rather than
    # falling through to UNEXPLAINED -- that separation is the repair. This
    # card's question was how big the shadow is, so it reads the shadow too,
    # and the 312 dates it characterises stay the 312 dates it characterised.
    candidates = [row for row in classified["dates"].values()
                  if row["kind"] == calendar_build.UNEXPLAINED]
    candidates += list((classified.get("excluded_only") or {}).values())
    rows = [
        classify_empty_date(
            row, static,
            len(calendar_build.readable_pairs(pairs, str(row["date"]))))
        for row in candidates]
    rows.sort(key=lambda r: r["date"])

    by_class: dict[str, int] = {name: 0 for name in EMPTY_CLASSES}
    by_kind: dict[str, int] = {}
    by_year: dict[str, dict[str, int]] = {}
    by_weekday: dict[str, int] = {}
    by_pair: dict[str, dict[str, int]] = {}
    for row in rows:
        by_class[row["class"]] += 1
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        year = row["date"][:4]
        by_year.setdefault(year,
                           {n: 0 for n in EMPTY_CLASSES})[row["class"]] += 1
        by_weekday[row["weekday"]] = by_weekday.get(row["weekday"], 0) + 1
        for pair, hours in row["hours_by_pair"].items():
            bucket = by_pair.setdefault(pair, {"dates": 0, "hours": 0})
            bucket["dates"] += 1
            bucket["hours"] += int(hours)
    readable_rows = [r for r in rows if r["class"] != "r1_artefact"]
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "rules": {"min_empty_hours": min_empty_hours,
                  "min_pairs_partial": min_pairs_partial},
        "dates": len(rows),
        "hours": sum(row["hours"] for row in rows),
        "dates_with_a_readable_empty_pair": len(readable_rows),
        "hours_on_those_dates": sum(row["hours"] for row in readable_rows),
        "by_class": by_class,
        "by_kind": {k: by_kind[k] for k in sorted(by_kind)},
        "by_weekday_readable": _weekday_counts(readable_rows),
        "by_year": {k: by_year[k] for k in sorted(by_year)},
        "by_weekday": {k: by_weekday[k] for k in
                       sorted(by_weekday,
                              key=lambda d: ["Mon", "Tue", "Wed", "Thu", "Fri",
                                             "Sat", "Sun"].index(d))},
        "by_pair": {k: by_pair[k] for k in sorted(by_pair)},
        "deepest": sorted(rows, key=lambda r: (-r["hours"], r["date"]))[:25],
        "all": rows,
    }


def _weekday_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Weekday tally in calendar order rather than alphabetical."""
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["weekday"]] = counts.get(row["weekday"], 0) + 1
    return {day: counts[day] for day in order if day in counts}


def _iso(nanoseconds: int) -> str:
    """An int64 epoch-nanosecond stamp as an ISO date."""
    return pd.Timestamp(int(nanoseconds)).date().isoformat()


# --------------------------------------------------------------------------- #
# The appendix -- full history, era-tagged against R7's agreement table
# --------------------------------------------------------------------------- #

#: Era boundaries on the share of a year's sampled hours that R7 could not
#: verify. Derived from the ruling rather than from the answer: an era is
#: defined by how well the cross-check could see it, which is the only property
#: of a year this card can establish before looking at the year's statistics.
ERA_BOUNDS: Final[tuple[tuple[str, float], ...]] = (
    ("corroborated", 0.02), ("partly-corroborated", 0.15), ("thin", 1.01),
)


def eras_from_classes(classes: cc.CrosscheckClasses) -> dict[str, Any]:
    """Tag each year by how much of it the cross-check could verify.

    Read from the committed classification rather than from T3's result
    document, so the appendix's era labels and the file every later card will
    query are the same object. A year the check could barely see is not a year
    whose statistics are wrong -- it is a year whose statistics have no second
    opinion, and the appendix says which is which rather than dropping them.
    """
    counts: dict[str, dict[str, int]] = {}
    for name in classes.sampled_pairs():
        for verdict in (cc.CLASS_PASS, cc.CLASS_BLOCKED,
                        cc.CLASS_UNVERIFIABLE):
            for _pair, date, _hour in classes.hours_in_class(verdict, name):
                bucket = counts.setdefault(date[:4], {cc.CLASS_PASS: 0,
                                                      cc.CLASS_BLOCKED: 0,
                                                      cc.CLASS_UNVERIFIABLE: 0})
                bucket[verdict] += 1
    by_year: dict[str, Any] = {}
    for year in sorted(counts):
        bucket = counts[year]
        sampled = sum(bucket.values())
        verifiable = bucket[cc.CLASS_PASS] + bucket[cc.CLASS_BLOCKED]
        unverifiable_share = (bucket[cc.CLASS_UNVERIFIABLE] / sampled
                              if sampled else 1.0)
        era = next(name for name, bound in ERA_BOUNDS
                   if unverifiable_share < bound)
        by_year[year] = {
            "sampled": sampled,
            "pass": bucket[cc.CLASS_PASS],
            "blocked": bucket[cc.CLASS_BLOCKED],
            "unverifiable": bucket[cc.CLASS_UNVERIFIABLE],
            "agreement_rate": _r(bucket[cc.CLASS_PASS] / verifiable
                                 if verifiable else None, 4),
            "unverifiable_share": _r(unverifiable_share, 4),
            "era": era,
        }
    eras: dict[str, list[str]] = {}
    for year, row in by_year.items():
        eras.setdefault(row["era"], []).append(year)
    return {
        "bounds": [{"era": name, "unverifiable_share_below": bound}
                   for name, bound in ERA_BOUNDS],
        "by_year": by_year,
        "years_by_era": {k: sorted(v) for k, v in sorted(eras.items())},
    }


def section_appendix(series: Series, eras: dict[str, Any]) -> dict[str, Any]:
    """The headline memory and volatility statistics, by era, over all history.

    The question the card asks of the 2000s: which properties survive them.
    Each era is a set of whole years, so the split is on a partition of the
    calendar fixed before any statistic was computed -- which is what stops it
    being a search for the split that makes a property look stable.
    """
    stamps = series.stamps()
    ret_year = stamps.year.to_numpy()[series.ret_pos].astype("int64")
    year_to_era = {int(y): row["era"] for y, row in eras["by_year"].items()}
    labels = np.array([year_to_era.get(int(y), "unclassified")
                       for y in ret_year], dtype=object)
    out: dict[str, Any] = {"whole": headline(series)}
    by_era: dict[str, Any] = {}
    for name, _bound in ERA_BOUNDS:
        mask = labels == name
        if int(mask.sum()) < stats.MIN_SAMPLE:
            continue
        by_era[name] = headline(series, mask)
        by_era[name]["years"] = eras["years_by_era"].get(name, [])
    out["by_era"] = by_era
    out["by_year"] = {
        str(year): headline(series, ret_year == year)
        for year in sorted(set(int(y) for y in ret_year))}
    signed: dict[str, Any] = {}
    for name in SIGNED_HEADLINES:
        null = HEADLINE_NULL[name]
        values = {era: row.get(name) for era, row in by_era.items()}
        usable = [v for v in values.values() if v is not None]
        signed[name] = {
            "by_era": values,
            "same_side_across_eras": (
                bool(len({(v - null) > 0 for v in usable}) == 1)
                if len(usable) > 1 else None),
        }
    out["signed"] = signed
    return out


# --------------------------------------------------------------------------- #
# The character table
# --------------------------------------------------------------------------- #

def character_rows(cells: dict[str, dict[str, Any]], register: Register,
                   labels: Sequence[str]) -> list[dict[str, Any]]:
    """One row per pair-horizon: the fingerprint, its size and its stability.

    The card asks for a ranked table of pairs x horizons x stability, and for
    no pair to be dropped or promoted in it. So the ranking is by effect size
    and the stability sits beside it as a label -- never as a filter, and never
    folded into one score a reader would then read as a decision.
    """
    rows: list[dict[str, Any]] = []
    q_values = register.q_lookup()
    for key, cell in cells.items():
        pair, label = key.split("|", 1)
        memory = cell["memory"]
        vr4 = next((row for row in memory["variance_ratio"] if row["q"] == 4),
                   None)
        rho1 = memory["acf"][0] if memory["acf"] else {}
        signs = memory["sign_persistence"]
        stability = cell["stability"]["rolling"].get("vr4") or {}
        split = cell["stability"]["split_half"].get("vr4") or {}
        vr = (vr4 or {}).get("vr")
        q_value = q_values.get(f"variance_ratio|{pair}|{label}|q4")
        fingerprint = "FLAT"
        if vr is not None and q_value is not None and q_value < 0.05:
            fingerprint = "TREND" if vr > 1.0 else "REVERT"
        rows.append({
            "pair": pair,
            "horizon": label,
            "fingerprint": fingerprint,
            "vr4": vr,
            "vr4_effect": _r(abs(vr - 1.0) if vr is not None else None, 5),
            "vr4_z": (vr4 or {}).get("z"),
            "vr4_q_value": q_value,
            "rho1": rho1.get("rho"),
            "p_same": signs.get("p_same"),
            "vol_rho1": (cell["volatility"]["acf_abs"][0]
                         if cell["volatility"]["acf_abs"] else None),
            "vol_half_life": cell["volatility"]["half_life_abs"],
            "excess_kurtosis": cell["returns"]["excess_kurtosis"],
            "sd_bp": cell["returns"]["sd_bp"],
            "median_spread_pips": cell.get("median_spread_pips"),
            "rolling_sign_agreement": stability.get("sign_agreement"),
            "stability": stability.get("label", "UNMEASURED"),
            "split_half_same_side": split.get("same_side"),
            "n": cell["returns"]["n"],
        })
    order = {label: index for index, label in enumerate(labels)}
    rows.sort(key=lambda r: (-(r["vr4_effect"] or 0.0),
                             order.get(r["horizon"], 99), r["pair"]))
    return rows


def rank_stability(cells: dict[str, dict[str, Any]], pairs: Sequence[str],
                   labels: Sequence[str]) -> dict[str, Any]:
    """Does the ranking of pairs survive the split?

    A statistic can be stable in sign for every pair and still be useless for
    choosing between pairs, if the order it puts them in is noise. Spearman
    between the two halves' rankings asks that directly, per horizon and per
    statistic.
    """
    out: dict[str, Any] = {}
    for label in labels:
        per_statistic: dict[str, Any] = {}
        for name in ("sd_bp", "excess_kurtosis", *SIGNED_HEADLINES):
            first: list[float] = []
            second: list[float] = []
            for pair in pairs:
                cell = cells.get(f"{pair}|{label}")
                if cell is None:
                    continue
                a = cell["stability"]["first_half"].get(name)
                b = cell["stability"]["second_half"].get(name)
                if a is None or b is None:
                    continue
                first.append(a)
                second.append(b)
            per_statistic[name] = {
                "pairs": len(first),
                "spearman": _r(stats.spearman(first, second), 4),
            }
        out[label] = per_statistic
    return out


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #

def run(*, params: dict[str, Any], seed: int, loader: Any) -> dict[str, Any]:
    """The T4 experiment. Evidence and hypotheses; no decisions.

    Args:
        params: The ``[experiment.params]`` table.
        seed: Recorded for the hash. Nothing here is random -- every statistic
            is a deterministic pass over stored bars in table order -- and the
            seed is carried so a later card that does randomise cannot do it
            without one.
        loader: A :class:`~research.loader.ResearchLoader` in scoring mode.
    """
    base = pathlib.Path(loader.root).parent.parent
    store = pathlib.Path(loader.root)
    pairs = [str(p) for p in params["pairs"]]
    labels = [str(t) for t in params["timeframes"]]
    start = as_date(str(params["start_date"]))
    end = as_date(str(params["end_date"]))
    history_start = as_date(str(params["history_start"]))
    history_labels = [str(t) for t in params["history_timeframes"]]
    split = as_date(str(params["split_date"]))
    roll = (int(params["roll_start_hour_ny"]), int(params["roll_end_hour_ny"]))
    session_label = str(params["session_timeframe"])
    alpha = float(params["fdr_alpha"])

    classes = cc.load_classes(
        base / str(params.get("crosscheck_classes_path", cc.CLASSES_RELPATH)))
    eras = eras_from_classes(classes)

    register = Register()
    cells: dict[str, dict[str, Any]] = {}
    coverage: list[dict[str, Any]] = []
    sessions: dict[str, Any] = {}
    clocks: dict[str, Any] = {}
    appendix: dict[str, Any] = {}
    density: dict[str, Any] = {}
    crosscheck_tags: dict[str, Any] = {}

    for pair in pairs:
        for label in labels:
            series = load_series(loader, pair, label, start, end)
            if series is None or len(series) < stats.MIN_SAMPLE:
                coverage.append({"pair": pair, "horizon": label,
                                 "readable": False})
                continue
            cell: dict[str, Any] = {
                "returns": section_returns(series, register),
                "memory": section_memory(
                    series, register,
                    acf_lags=int(params["acf_lags"]),
                    vr_horizons=[int(q) for q in params["vr_horizons"]],
                    adf_lags=int(params["adf_lags"]),
                    continuation_horizons=[
                        int(h) for h in params["continuation_horizons"]]),
                "volatility": section_volatility(
                    series, register,
                    vol_acf_lags=int(params["vol_acf_lags"]),
                    vol_window=int(params["vol_window"]),
                    regime_horizon=int(params["regime_horizon"])),
                "stability": section_stability(
                    series, split=split,
                    window_years=int(params["rolling_window_years"]),
                    step_months=int(params["rolling_step_months"])),
                "median_spread_pips": _r(
                    float(np.median(series.spread_pips)), 4),
                "median_ticks_per_bar": _r(
                    float(np.median(series.tick_count)), 1),
            }
            cells[f"{pair}|{label}"] = cell
            coverage.append({
                "pair": pair, "horizon": label, "readable": True,
                "bars": int(series.ts.size), "returns": len(series),
                "dropped_pairs": series.dropped,
                "stub_bars_dropped": series.bars_dropped_stub,
                "spans": len(series.spans),
                "adjacent_share": _r(series.adjacent_share(), 6),
                "first": cell["returns"]["first"],
                "last": cell["returns"]["last"],
            })
            if label == session_label:
                sessions[pair] = section_sessions(series, register, roll=roll)
                clocks[pair] = section_clock(series)
                crosscheck_tags[pair] = _tag_summary(loader, pair, series)
            _LOG.info("%s %s: %d return(s)", pair, label, len(series))

        for label in history_labels:
            series = load_series(loader, pair, label, history_start, end)
            if series is None or len(series) < stats.MIN_SAMPLE:
                continue
            appendix.setdefault(label, {})[pair] = section_appendix(series,
                                                                    eras)
            if label == session_label:
                density[pair] = density_profile(series, register)

    # Benjamini-Hochberg runs before the character table is built, and the
    # order is load-bearing rather than incidental: the table labels a pair's
    # fingerprint by whether its variance ratio survives the correction, and a
    # q-value that has not been computed yet reads as "no evidence" for every
    # pair at every horizon.
    test_summary = register.summarise(alpha)
    breaks = density_breaks(density, float(params["density_break_multiple"]))
    empties = section_empties(
        store, pairs, history_start, end,
        min_empty_hours=int(params["calendar_min_empty_hours"]),
        min_pairs_partial=int(params["calendar_min_pairs_partial"]))

    return {
        "note": ("EDA battery I: per-pair character across five horizons. "
                 "Evidence and hypotheses only -- no strategy, no backtest, "
                 "no P&L, no scorecard, and no pair dropped or promoted. "
                 "Those are the checkpoint's decisions and this card is "
                 "explicit that they are not its."),
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "pairs": len(pairs), "horizons": labels,
                   "history_start": history_start.isoformat(),
                   "history_horizons": history_labels,
                   "split_date": split.isoformat()},
        "method": _method(params, roll),
        "coverage": coverage,
        "cells": cells,
        "sessions": sessions,
        "clock": clocks,
        "crosscheck_tags": crosscheck_tags,
        "eras": eras,
        "appendix": appendix,
        "density": density,
        "density_breaks": breaks,
        "empties": empties,
        "character": character_rows(cells, register, labels),
        "rank_stability": rank_stability(cells, pairs, labels),
        "tests": test_summary,
        "test_register": register.rows(),
        "rulings": _rulings(pairs, classes, eras),
        "loader": {"mode": loader.mode,
                   "sealed_dates_served": loader.access.sealed_dates(),
                   "excluded_dates_withheld": len(loader.access.excluded),
                   "excluded_pairs": loader.access.excluded_pairs()},
        "seed": int(seed),
    }


def _tag_summary(loader: Any, pair: str, series: Series) -> dict[str, Any]:
    """How much of a pair's hourly series the cross-check ever saw.

    The T4 card asks the loader to carry the R7 tag "where a scoring experiment
    could later need it". This is that tag exercised on real bars, and the
    answer is the number a later card has to reckon with: the cross-check
    sampled a few hundred hours per pair out of a hundred and twenty-five
    thousand, so ``UNSAMPLED`` is not a rare state -- it is almost all of them.
    """
    tags = loader.crosscheck_classes(pair, series.stamps())
    counts: dict[str, int] = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    return {"bars": len(tags),
            "by_class": {k: counts[k] for k in sorted(counts)},
            "sampled_share": _r(
                1.0 - counts.get(cc.CLASS_UNSAMPLED, 0) / max(1, len(tags)),
                6)}


def _method(params: dict[str, Any], roll: tuple[int, int]) -> dict[str, Any]:
    """The parameters every statistic here was computed under, in one place."""
    return {
        "return_definition": "log(mid_close_t) - log(mid_close_{t-1})",
        "gap_rule": ("intraday horizons require exactly adjacent bars; the "
                     f"daily horizon accepts 1 to {MAX_DAILY_GAP_DAYS} days "
                     "and drops the Sunday stub bars"),
        "acf_lags": int(params["acf_lags"]),
        "vol_acf_lags": int(params["vol_acf_lags"]),
        "vr_horizons": [int(q) for q in params["vr_horizons"]],
        "adf_lags": int(params["adf_lags"]),
        "adf_critical_1pct": stats.ADF_CRITICAL_VALUES["1%"],
        "continuation_horizons": [int(h) for h in
                                  params["continuation_horizons"]],
        "vol_window_bars": int(params["vol_window"]),
        "regime_horizon_bars": int(params["regime_horizon"]),
        "rolling_window_years": int(params["rolling_window_years"]),
        "rolling_step_months": int(params["rolling_step_months"]),
        "roll_window_ny": [roll[0], roll[1]],
        "session_timeframe": str(params["session_timeframe"]),
        "density_break_multiple": float(params["density_break_multiple"]),
        "fdr_alpha": float(params["fdr_alpha"]),
        "stability_labels": [{"sign_agreement_at_least": bound, "label": name}
                             for bound, name in STABILITY_LABELS],
        "era_bounds": [{"era": name, "unverifiable_share_below": bound}
                       for name, bound in ERA_BOUNDS],
    }


def _rulings(pairs: Sequence[str], classes: cc.CrosscheckClasses,
             eras: dict[str, Any]) -> dict[str, Any]:
    """The rulings this card is shaped by, and where each one bites."""
    return {
        "R1": {
            "statement": "AUDUSD before 2011-01-01 is excluded from research",
            "bites": ("the full-history appendix and the density series run "
                      "on AUDUSD from 2011 and on eleven pairs before it"),
            "windows": summarise_exclusions(pairs),
        },
        "R3": {
            "statement": ("spread comparisons across eras must control for "
                          "ticks per hour"),
            "bites": ("every spread figure is reported inside a tick-count "
                      f"band; the cross-era band is {R3_REFERENCE_BAND}"),
            "bands": [name for name, _lo, _hi in DENSITY_BANDS],
        },
        "R4": {
            "statement": ("tick counts are not a volume or activity proxy "
                          "until a T4 card has characterised the density "
                          "series"),
            "bites": ("section 6 is that characterisation, and it ends in a "
                      "verdict rather than a table"),
        },
        "R7": {
            "statement": ("the cross-check class of an hour is density-aware; "
                          "UNVERIFIABLE means the check could not see it"),
            "bites": ("the appendix era tags come from the by-year agreement "
                      "table, read from the committed classification"),
            "hours_classified": classes.counts.get("classified"),
            "eras": {k: len(v) for k, v in eras["years_by_era"].items()},
        },
        "R8": {
            "statement": ("the static major-holiday list marks hours "
                          "ineligible for execution in every backtest"),
            "bites": ("stated, not applied: R8 is a backtester rule for T7 "
                      "and this card runs no backtest. Section 7 reports how "
                      "many unexplained-empty dates the static list names, "
                      "which is the size of what R8 will remove"),
        },
    }
