"""The T4 battery: the decisions that would silently corrupt every number.

Four of them, and each has a test here because none of them fails loudly:

* the gap rule, which is the difference between a 5-minute return sample and
  one containing a weekend;
* the Sunday stub bars at the daily horizon, which would put a two-hour
  "day" between every Friday and Monday;
* the roll window, which moves with New York daylight saving and is silently
  wrong for half of every year if it is pinned to a UTC hour;
* the order in which the Benjamini-Hochberg correction runs, which decides
  whether the character table can call a fingerprint at all.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fxlab.ingestion.bars import BAR_SCHEMA, bars_path
from research import character
from research import crosscheck_class as cc
from research import loader as loader_mod


# --------------------------------------------------------------------------- #
# A throwaway store
# --------------------------------------------------------------------------- #

def _write_bars(base: pathlib.Path, pair: str, alias: str,
                stamps: pd.DatetimeIndex, closes: np.ndarray,
                ticks: np.ndarray | None = None,
                spread: np.ndarray | None = None) -> None:
    """Write a minimal bar table in the pinned Phase 1 schema."""
    n = len(stamps)
    ones = np.ones(n, dtype="float64")
    counts = (np.full(n, 1000, dtype="int64") if ticks is None
              else np.asarray(ticks, dtype="int64"))
    spreads = (ones * 1e-4 if spread is None
               else np.asarray(spread, dtype="float64"))
    table = pa.table({
        "pair": pa.array([pair] * n, pa.large_string()),
        "ts": pa.array(stamps.to_pydatetime(), pa.timestamp("us", tz="UTC")),
        **{f"{side}_{field}": pa.array(closes, pa.float64())
           for side in ("bid", "ask", "mid")
           for field in ("open", "high", "low", "close")},
        "tick_count": pa.array(counts, pa.int64()),
        "spread_mean": pa.array(spreads, pa.float64()),
        "spread_max": pa.array(spreads, pa.float64()),
    }, schema=BAR_SCHEMA)
    path = bars_path(base / "data" / "research", pair, alias)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway project root with a research data tree."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    return tmp_path


def _loader(base: pathlib.Path) -> loader_mod.ResearchLoader:
    return loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)


# --------------------------------------------------------------------------- #
# The gap rule
# --------------------------------------------------------------------------- #

def test_an_hourly_series_drops_the_weekend_pair(base: pathlib.Path) -> None:
    """Three hours on Friday, three on Monday: five returns, not six."""
    stamps = pd.DatetimeIndex(
        ["2016-05-06 08:00", "2016-05-06 09:00", "2016-05-06 10:00",
         "2016-05-09 08:00", "2016-05-09 09:00", "2016-05-09 10:00"],
        tz="UTC")
    _write_bars(base, "EURUSD", "1h", stamps,
                np.array([1.10, 1.11, 1.12, 1.13, 1.14, 1.15]))
    series = character.load_series(_loader(base), "EURUSD", "1h",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    assert series is not None
    assert len(series) == 4
    assert series.dropped == 1
    assert series.spans == [(0, 2), (2, 4)]


def test_no_lag_one_pair_crosses_the_weekend(base: pathlib.Path) -> None:
    """The spans are what stop it, and they must land in the right places."""
    stamps = pd.DatetimeIndex(
        ["2016-05-06 08:00", "2016-05-06 09:00",
         "2016-05-09 08:00", "2016-05-09 09:00"], tz="UTC")
    _write_bars(base, "EURUSD", "1h", stamps,
                np.array([1.10, 1.20, 1.30, 1.40]))
    series = character.load_series(_loader(base), "EURUSD", "1h",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    from research import stats

    earlier, later, _positions = stats.lag_pairs(series.returns, series.spans, 1)
    assert earlier.size == 0 and later.size == 0


def test_the_daily_horizon_keeps_friday_to_monday(base: pathlib.Path) -> None:
    """Which the intraday rule would throw away, losing every Monday."""
    stamps = pd.DatetimeIndex(
        ["2016-05-05", "2016-05-06", "2016-05-09", "2016-05-10"], tz="UTC")
    _write_bars(base, "EURUSD", "1D", stamps,
                np.array([1.10, 1.11, 1.12, 1.13]))
    series = character.load_series(_loader(base), "EURUSD", "1d",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    assert len(series) == 3
    assert series.dropped == 0
    assert series.spans == [(0, 3)]


def test_the_daily_horizon_drops_the_sunday_stub(base: pathlib.Path) -> None:
    """A Sunday daily bar is two hours of the weekly open, not a day."""
    stamps = pd.DatetimeIndex(
        ["2016-05-06", "2016-05-08", "2016-05-09"], tz="UTC")  # Fri, Sun, Mon
    _write_bars(base, "EURUSD", "1D", stamps, np.array([1.10, 1.101, 1.12]))
    series = character.load_series(_loader(base), "EURUSD", "1d",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    assert series.bars_dropped_stub == 1
    assert len(series) == 1
    # Friday to Monday, straight through the stub.
    assert series.returns[0] == pytest.approx(np.log(1.12 / 1.10))


def test_the_daily_horizon_drops_a_fortnight_long_hole(
        base: pathlib.Path) -> None:
    """Four days is a long holiday weekend; a fortnight is missing data."""
    stamps = pd.DatetimeIndex(["2016-05-02", "2016-05-20"], tz="UTC")
    _write_bars(base, "EURUSD", "1D", stamps, np.array([1.10, 1.20]))
    series = character.load_series(_loader(base), "EURUSD", "1d",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    assert len(series) == 0
    assert series.dropped == 1


def test_a_covariate_aligns_to_the_closing_bar(base: pathlib.Path) -> None:
    """Every session, spread and density statistic depends on this."""
    stamps = pd.DatetimeIndex(
        ["2016-05-06 08:00", "2016-05-06 09:00", "2016-05-06 10:00"], tz="UTC")
    _write_bars(base, "EURUSD", "1h", stamps, np.array([1.10, 1.11, 1.12]),
                ticks=np.array([100, 200, 300]))
    series = character.load_series(_loader(base), "EURUSD", "1h",
                                   dt.date(2016, 5, 1), dt.date(2016, 5, 31))
    assert list(series.covariate(series.tick_count)) == [200.0, 300.0]


# --------------------------------------------------------------------------- #
# Ruling R1
# --------------------------------------------------------------------------- #

def test_the_excluded_window_is_recorded_not_merely_skipped(
        base: pathlib.Path) -> None:
    """A caller that clamps must say what it gave up, or R1 leaves no trace."""
    stamps = pd.DatetimeIndex(pd.date_range("2011-01-03", periods=5, freq="D",
                                            tz="UTC"))
    _write_bars(base, "AUDUSD", "1D", stamps, np.linspace(1.0, 1.05, 5))
    loader = _loader(base)
    series = character.load_series(loader, "AUDUSD", "1d",
                                   dt.date(2010, 12, 1), dt.date(2011, 1, 10))
    assert series is not None
    assert loader.access.excluded_pairs() == ["AUDUSD"]
    assert len(loader.access.excluded) == 31   # all of December 2010
    assert all(entry.startswith("AUDUSD:2010-12")
               for entry in loader.access.excluded)


# --------------------------------------------------------------------------- #
# The roll window
# --------------------------------------------------------------------------- #

def test_the_roll_window_moves_with_new_york_daylight_saving() -> None:
    """21:00Z in summer, 22:00Z in winter. A hardcoded UTC hour is wrong for
    half of every year and fails silently."""
    summer = pd.DatetimeIndex(["2016-07-06 20:00", "2016-07-06 21:00",
                               "2016-07-06 22:00"], tz="UTC")
    winter = pd.DatetimeIndex(["2016-01-06 20:00", "2016-01-06 21:00",
                               "2016-01-06 22:00"], tz="UTC")
    assert list(character.in_roll_window(summer, 16, 18)) == [True, True, False]
    assert list(character.in_roll_window(winter, 16, 18)) == [False, True, True]


def test_density_bands_are_the_ones_ruling_r3_is_applied_through() -> None:
    """A spread compared across a band boundary is compared across eras."""
    labels = character.density_band(np.array([100, 700, 2000, 5000, 50_000]))
    assert list(labels) == ["<500", "500-1k", "1k-3k", "3k-10k", ">=10k"]


# --------------------------------------------------------------------------- #
# The test register
# --------------------------------------------------------------------------- #

def test_the_register_groups_by_family_and_corrects_within_it() -> None:
    """Which is what the report states next to a claim."""
    register = character.Register()
    for index, p in enumerate([1e-9, 1e-8, 0.4, 0.9]):
        register.add("vr", f"k{index}", {"z": 1.0, "p_value": p})
    for index, p in enumerate([0.5, 0.6]):
        register.add("other", f"k{index}", {"z": 1.0, "p_value": p})
    summary = register.summarise(0.05)
    assert summary["total_tests"] == 6
    assert summary["families"]["vr"]["tests"] == 4
    assert summary["families"]["vr"]["rejected"] == 2
    assert summary["families"]["other"]["rejected"] == 0


def test_q_values_exist_only_after_the_correction_has_run() -> None:
    """The ordering bug this test exists for made every fingerprint FLAT: the
    character table read q-values that summarise() had not written yet."""
    register = character.Register()
    register.add("variance_ratio", "EURUSD|5m|q4", {"z": -8.0, "p_value": 1e-12})
    assert register.q_lookup()["variance_ratio|EURUSD|5m|q4"] is None
    register.summarise(0.05)
    assert register.q_lookup()["variance_ratio|EURUSD|5m|q4"] == pytest.approx(
        1e-12)


# --------------------------------------------------------------------------- #
# The unexplained empty dates
# --------------------------------------------------------------------------- #

def _empty_row(date: str, pairs: dict[str, int]) -> dict:
    return {"date": date, "pairs_empty": sorted(pairs),
            "pairs_empty_deep": [], "hours_by_pair": pairs,
            "max_hours": max(pairs.values()) if pairs else 0}


def test_a_date_with_no_readable_empty_pair_is_the_filters_own_shadow() -> None:
    """236 of T3's 312 dates are this, and calling them data facts would put a
    three-figure count of nothing in front of a reviewer."""
    row = character.classify_empty_date(_empty_row("2008-03-12", {}), {}, 11)
    assert row["class"] == "r1_artefact"
    assert row["kind"] == "bookkeeping artefact"


def test_a_shallow_sunday_is_the_week_edge() -> None:
    """The FX week opens Sunday 17:00 New York; emptiness there is the edge."""
    row = character.classify_empty_date(
        _empty_row("2009-06-14", {"USDJPY": 1}), {}, 11)
    assert row["class"] == "week_boundary"
    assert row["kind"] == "feed artefact"


def test_a_deep_friday_is_not_the_week_edge() -> None:
    """Good Friday is a Friday, and twenty-two empty hours is not an edge."""
    row = character.classify_empty_date(
        _empty_row("2015-04-03", {"EURUSD": 22, "GBPUSD": 22}),
        {"2015-04-03": "Good Friday"}, 12)
    assert row["class"] == "calendar_holiday"


def test_pairs_sharing_a_currency_are_that_currencys_holiday() -> None:
    """One centre shut, the crosses trading."""
    row = character.classify_empty_date(
        _empty_row("2012-05-03", {"USDJPY": 8, "EURJPY": 8, "AUDJPY": 8}),
        {}, 12)
    assert row["class"] == "currency_holiday"
    assert row["shared_currency"] == ["JPY"]


def test_half_the_universe_going_shallowly_quiet_is_the_feed() -> None:
    """A market closure shuts everybody deeply; a feed hiccup does not."""
    pairs = {name: 2 for name in
             ("EURUSD", "GBPUSD", "USDCHF", "USDCAD", "NZDUSD", "EURGBP")}
    row = character.classify_empty_date(_empty_row("2012-05-16", pairs), {}, 12)
    assert row["class"] == "feed_artefact"


def test_anything_else_says_unknown_rather_than_guessing() -> None:
    row = character.classify_empty_date(
        _empty_row("2012-05-16", {"EURUSD": 4}), {}, 12)
    assert row["class"] == "unknown"


# --------------------------------------------------------------------------- #
# Eras and the character table
# --------------------------------------------------------------------------- #

def _classes(entries: dict[str, list[str]]) -> cc.CrosscheckClasses:
    return cc.CrosscheckClasses(hours=entries, window=("2005-01-03",
                                                       "2025-02-28"),
                                rules={}, counts={})


def test_eras_come_from_how_much_the_check_could_see() -> None:
    """Not from how the year's statistics came out, which would make the split
    a search for the boundary that flatters a property."""
    entries = {"EURUSD": {}}
    for hour in range(50):
        entries["EURUSD"][f"2006-01-02 {hour % 24:02d}"] = "U"
    entries["EURUSD"]["2006-02-02 01"] = "P"
    for hour in range(50):
        entries["EURUSD"][f"2020-01-02 {hour % 24:02d}"] = "P"
    eras = character.eras_from_classes(_classes(entries))
    assert eras["by_year"]["2006"]["era"] == "thin"
    assert eras["by_year"]["2020"]["era"] == "corroborated"


def test_a_fingerprint_is_called_only_when_it_survives_the_correction() -> None:
    """A z of -8 on one of sixty cells is not a finding until BH says so."""
    register = character.Register()
    register.add("variance_ratio", "EURUSD|5m|q4",
                 {"z": -8.0, "p_value": 1e-12})
    register.add("variance_ratio", "GBPUSD|5m|q4",
                 {"z": -0.4, "p_value": 0.7})
    register.summarise(0.05)
    cells = {
        "EURUSD|5m": _cell(0.90, -8.0),
        "GBPUSD|5m": _cell(0.99, -0.4),
    }
    rows = character.character_rows(cells, register, ["5m"])
    fingerprints = {row["pair"]: row["fingerprint"] for row in rows}
    assert fingerprints["EURUSD"] == "REVERT"
    assert fingerprints["GBPUSD"] == "FLAT"


def _cell(vr: float, z: float) -> dict:
    """A minimal cell with the fields the character table reads."""
    return {
        "returns": {"n": 1000, "excess_kurtosis": 5.0, "sd_bp": 3.0},
        "memory": {
            "variance_ratio": [{"q": 4, "vr": vr, "z": z, "p_value": 0.0}],
            "acf": [{"lag": 1, "rho": -0.03}],
            "sign_persistence": {"p_same": 0.48},
        },
        "volatility": {"acf_abs": [0.3], "half_life_abs": 20.0},
        "stability": {"rolling": {"vr4": {"sign_agreement": 1.0,
                                          "label": "STABLE"}},
                      "split_half": {"vr4": {"same_side": True}}},
        "median_spread_pips": 0.3,
    }


def test_the_character_table_ranks_by_effect_size_not_by_significance() -> None:
    """A z of -50 on a VR of 0.999 is a large sample, not a large effect, and
    ranking on it would put the least interesting cell at the top."""
    register = character.Register()
    register.add("variance_ratio", "EURUSD|5m|q4", {"z": -50.0,
                                                    "p_value": 0.0})
    register.add("variance_ratio", "GBPUSD|5m|q4", {"z": -3.0,
                                                    "p_value": 1e-3})
    register.summarise(0.05)
    rows = character.character_rows(
        {"EURUSD|5m": _cell(0.999, -50.0), "GBPUSD|5m": _cell(0.80, -3.0)},
        register, ["5m"])
    assert rows[0]["pair"] == "GBPUSD"


# --------------------------------------------------------------------------- #
# Density breaks
# --------------------------------------------------------------------------- #

def test_the_break_threshold_is_derived_from_the_series_it_describes() -> None:
    """Choosing it against the answer is how a break list becomes a list of the
    years somebody expected."""
    profiles = {
        "EURUSD": {"year_over_year": [{"year": "2006", "log_change": 0.1},
                                      {"year": "2007", "log_change": 0.1},
                                      {"year": "2008", "log_change": 2.0}]},
        "GBPUSD": {"year_over_year": [{"year": "2006", "log_change": 0.1},
                                      {"year": "2007", "log_change": -0.1}]},
    }
    breaks = character.density_breaks(profiles, 3.0)
    assert breaks["median_abs_change"] == pytest.approx(0.1)
    assert breaks["threshold"] == pytest.approx(0.3)
    assert [c["year"] for c in breaks["candidates"]] == ["2008"]


def test_no_breaks_when_nothing_moves() -> None:
    profiles = {"EURUSD": {"year_over_year": [{"year": "2006",
                                               "log_change": 0.1},
                                              {"year": "2007",
                                               "log_change": -0.1}]}}
    assert character.density_breaks(profiles, 3.0)["candidates"] == []
