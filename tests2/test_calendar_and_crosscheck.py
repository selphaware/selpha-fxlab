"""The holiday calendar and the cross-check: the classification rules.

Both modules turn observations into verdicts, and the verdicts are the part
worth pinning. The calendar decides whether a quiet day was a holiday or a data
fault; the cross-check decides whether a price difference blocks the data. Each
of those has an obvious wrong answer that would look fine in a report -- call
every quiet day a holiday, or exempt every disagreement as roll noise -- so the
tests are mostly about the boundary between the two.
"""

from __future__ import annotations

import datetime as dt

import pytest

from research import calendar_build as cal
from research import crosscheck_oanda as cc

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]


def _scan(empty: dict[str, dict[str, int]],
          traded: dict[str, dict[str, int]] | None = None) -> dict:
    """A scan result carrying only what `classify` reads."""
    traded = traded or {}
    present: dict[str, set[str]] = {}
    for table in (empty, traded):
        for date, row in table.items():
            present.setdefault(date, set()).update(row)
    return {"empty": empty, "traded": traded, "pairs_present": present}


# --------------------------------------------------------------------------- #
# The calendar's three-way classification
# --------------------------------------------------------------------------- #

def test_every_pair_quiet_all_day_is_a_full_holiday() -> None:
    """The unanimity rule: a market holiday shuts everybody at once."""
    scan = _scan({"2019-12-25": {p: 14 for p in PAIRS}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert rows["2019-12-25"]["kind"] == cal.FULL


def test_some_pairs_quiet_is_a_partial_holiday() -> None:
    """A currency's own national day, with the crosses still trading."""
    scan = _scan({"2019-01-02": {"USDJPY": 12, "EURUSD": 9, "GBPUSD": 8}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert rows["2019-01-02"]["kind"] == cal.PARTIAL


def test_too_few_pairs_is_unexplained_not_a_holiday() -> None:
    """The card's line: these are data facts for T4, never market closures."""
    scan = _scan({"2019-03-05": {"USDJPY": 12, "EURUSD": 9}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert rows["2019-03-05"]["kind"] == cal.UNEXPLAINED


def test_a_shallow_quiet_spell_is_unexplained_even_if_universal() -> None:
    """Every pair quiet for one hour is a feed hiccup, not a holiday."""
    scan = _scan({"2019-03-05": {p: 2 for p in PAIRS}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert rows["2019-03-05"]["kind"] == cal.UNEXPLAINED


def test_unanimity_is_over_the_pairs_research_may_read() -> None:
    """Ruling R1 shrinks the universe before 2011, and the test must follow.

    Requiring all twelve on a date where the loader refuses one of them would
    make every pre-2011 holiday fail for the reason the pair is excluded.
    """
    scan = _scan({"2009-12-25": {p: 14 for p in PAIRS if p != "AUDUSD"}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert cal.readable_pairs(PAIRS, "2009-12-25") == [
        "EURUSD", "GBPUSD", "USDJPY"]
    assert rows["2009-12-25"]["kind"] == cal.FULL


def test_after_the_exclusion_ends_the_pair_counts_again() -> None:
    """The same shape one year later is not unanimous, because AUDUSD is back."""
    scan = _scan({"2011-12-26": {p: 14 for p in PAIRS if p != "AUDUSD"}})
    rows = cal.classify(scan, PAIRS, min_empty_hours=6,
                        min_pairs_partial=3)["dates"]
    assert rows["2011-12-26"]["kind"] == cal.PARTIAL


# --------------------------------------------------------------------------- #
# The static list, and what the comparison must not conflate
# --------------------------------------------------------------------------- #

def test_thanksgiving_is_the_fourth_thursday() -> None:
    """Checked on a year where November starts on a Thursday, and one where
    it starts on a Friday -- the two cases an off-by-one week gets wrong."""
    assert cal._thanksgiving(2018) == dt.date(2018, 11, 22)
    assert cal._thanksgiving(2019) == dt.date(2019, 11, 28)
    assert cal._thanksgiving(2024) == dt.date(2024, 11, 28)


def test_good_friday_and_easter_monday_bracket_easter() -> None:
    """Derived from the tabulated Sunday, so the table is the only input."""
    holidays = cal.static_holidays([2019])
    assert holidays["2019-04-19"] == "Good Friday"
    assert holidays["2019-04-22"] == "Easter Monday"


def test_a_holiday_the_feed_traded_through_is_not_a_weekend() -> None:
    """The two look identical as a set difference and mean opposite things.

    One says the market was open and quoting on a bank holiday; the other says
    the derived week had already shut and nobody was asked.
    """
    scan = _scan({}, {"2019-04-19": {p: 21 for p in PAIRS}})
    classified = cal.classify(scan, PAIRS, min_empty_hours=6,
                              min_pairs_partial=3)
    static = {"2019-04-19": "Good Friday", "2019-04-20": "invented"}
    out = cal.compare_static(classified, static, scan,
                             dt.date(2019, 1, 1), dt.date(2019, 12, 31))
    assert out["static_traded_through"] == ["2019-04-19"]
    assert out["static_on_a_closed_week"] == ["2019-04-20"]


def test_a_derived_holiday_the_static_list_misses_is_reported() -> None:
    """The whole market stopped and no major-holiday list explains it."""
    scan = _scan({"2019-07-15": {p: 20 for p in PAIRS}})
    classified = cal.classify(scan, PAIRS, min_empty_hours=6,
                              min_pairs_partial=3)
    out = cal.compare_static(classified, {}, scan,
                             dt.date(2019, 1, 1), dt.date(2019, 12, 31))
    assert out["derived_not_static"] == ["2019-07-15"]


def test_the_unexplained_profile_counts_dates_and_hours() -> None:
    """The T4 hand-off: how much, when, and which pairs."""
    scan = _scan({"2019-03-05": {"USDJPY": 12, "EURUSD": 9},
                  "2020-03-05": {"USDJPY": 3}})
    classified = cal.classify(scan, PAIRS, min_empty_hours=6,
                              min_pairs_partial=3)
    profile = cal.unexplained_profile(classified)
    assert profile["dates"] == 2
    assert profile["hours"] == 24
    assert profile["by_year"] == {"2019": 1, "2020": 1}
    assert profile["by_pair"]["USDJPY"] == {"dates": 2, "hours": 15}


def test_the_rendered_calendar_carries_its_own_rule() -> None:
    """A calendar that does not say how it was derived cannot be audited."""
    scan = _scan({"2019-12-25": {p: 14 for p in PAIRS}})
    classified = cal.classify(scan, PAIRS, min_empty_hours=6,
                              min_pairs_partial=3)
    rendered = cal.render_toml({
        "window": {"start": "2019-01-01", "end": "2019-12-31"},
        "rules": {"min_empty_hours": 6, "min_pairs_partial": 3},
        "classified": classified,
        "static": {"2019-12-25": "Christmas Day"},
    })
    assert "min_empty_hours = 6" in rendered
    assert '"2019-12-25" = "Christmas Day"' in rendered
    assert "ruling" in rendered.lower()


# --------------------------------------------------------------------------- #
# The roll window, derived rather than pinned to UTC
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("stamp", "expected"), [
    # Northern summer: 17:00 New York is 21:00 UTC.
    ("2019-06-12T21:00:00+00:00", True),
    ("2019-06-12T20:00:00+00:00", True),    # 16:00 EDT, the window opens
    ("2019-06-12T22:00:00+00:00", False),   # 18:00 EDT, the window has closed
    # Northern winter: the same New York hours are one UTC hour later.
    ("2019-01-14T21:00:00+00:00", True),    # 16:00 EST
    ("2019-01-14T22:00:00+00:00", True),    # 17:00 EST
    ("2019-01-14T20:00:00+00:00", False),   # 15:00 EST, before the window
    ("2019-01-14T23:00:00+00:00", False),   # 18:00 EST, after it
])
def test_the_roll_window_moves_with_daylight_saving(stamp: str,
                                                    expected: bool) -> None:
    """A rule written in UTC is wrong for half of every year."""
    when = dt.datetime.fromisoformat(stamp)
    assert cc.in_roll_window(when, 16, 18) is expected


# --------------------------------------------------------------------------- #
# The comparison and its verdict
# --------------------------------------------------------------------------- #

class _OHLC:
    def __init__(self, o: float, c: float) -> None:
        self.open, self.close = o, c
        self.high = max(o, c)
        self.low = min(o, c)


class _Candle:
    def __init__(self, o: float, c: float, volume: int = 100) -> None:
        self.mid = _OHLC(o, c)
        self.bid = _OHLC(o, c)
        self.ask = _OHLC(o, c)
        self.volume = volume
        self.complete = True


def test_a_difference_is_measured_in_pips_not_price() -> None:
    """0.0001 on EURUSD is one pip; the same figure on a JPY cross is not."""
    stored = {"ticks": 10, "mid_open": 1.1300, "mid_close": 1.1310,
              "spread_mean": 0.0001}
    row = cc.compare_hour("EURUSD", "2019-06-12", 9, stored,
                          _Candle(1.1300, 1.1305), 1.0, (16, 18))
    assert row["close_diff_pips"] == pytest.approx(5.0, abs=1e-6)
    assert row["beyond_threshold"] is True


def test_the_jpy_pip_is_the_second_decimal() -> None:
    """The classic 100x bug, in the one place it would silently pass."""
    stored = {"ticks": 10, "mid_open": 110.00, "mid_close": 110.05,
              "spread_mean": 0.01}
    row = cc.compare_hour("USDJPY", "2019-06-12", 9, stored,
                          _Candle(110.00, 110.00), 1.0, (16, 18))
    assert row["close_diff_pips"] == pytest.approx(5.0, abs=1e-6)


def test_a_small_difference_does_not_flag() -> None:
    """Two venues quoting the same market will not agree exactly."""
    stored = {"ticks": 10, "mid_open": 1.1300, "mid_close": 1.13005,
              "spread_mean": 0.0001}
    row = cc.compare_hour("EURUSD", "2019-06-12", 9, stored,
                          _Candle(1.1300, 1.1300), 1.0, (16, 18))
    assert row["beyond_threshold"] is False


def test_the_roll_hour_is_exempt_however_large_the_difference() -> None:
    """Pre-reg #7 exempts the window; the difference is still recorded."""
    stored = {"ticks": 10, "mid_open": 1.1300, "mid_close": 1.1400,
              "spread_mean": 0.0001}
    row = cc.compare_hour("EURUSD", "2019-06-12", 21, stored,
                          _Candle(1.1300, 1.1300), 1.0, (16, 18))
    assert row["roll_exempt"] is True
    assert row["beyond_threshold"] is False
    assert row["close_diff_pips"] == pytest.approx(100.0, abs=1e-6)


def test_the_worst_of_open_and_close_decides() -> None:
    """A venue that agrees at the close and not at the open still disagrees."""
    stored = {"ticks": 10, "mid_open": 1.1400, "mid_close": 1.1300,
              "spread_mean": 0.0001}
    row = cc.compare_hour("EURUSD", "2019-06-12", 9, stored,
                          _Candle(1.1300, 1.1300), 1.0, (16, 18))
    assert row["abs_worst_pips"] == pytest.approx(100.0, abs=1e-6)
    assert row["beyond_threshold"] is True


# --------------------------------------------------------------------------- #
# Sampling: reproducible, and spread across the year
# --------------------------------------------------------------------------- #

def _year_of_dates(year: int) -> list[str]:
    """Every day of a year, as the eligible-date pool."""
    day = dt.date(year, 1, 1)
    out = []
    while day.year == year:
        out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def test_the_sample_is_reproducible_from_the_seed() -> None:
    """An unseeded sample makes the whole cross-check unreproducible."""
    pool = _year_of_dates(2019)
    first = cc.sample_dates("EURUSD", 2019, 12, pool, seed=7)
    second = cc.sample_dates("EURUSD", 2019, 12, pool, seed=7)
    assert first == second and len(first) == 12


def test_a_different_pair_draws_a_different_sample() -> None:
    """Keying only on the seed would check the same twelve days everywhere."""
    pool = _year_of_dates(2019)
    assert (cc.sample_dates("EURUSD", 2019, 12, pool, seed=7)
            != cc.sample_dates("GBPUSD", 2019, 12, pool, seed=7))


def test_twelve_dates_land_one_in_each_month() -> None:
    """A sample that piled into a quarter would not span the year's regimes."""
    picked = cc.sample_dates("EURUSD", 2019, 12, _year_of_dates(2019), seed=7)
    assert sorted(d[5:7] for d in picked) == [f"{m:02d}" for m in
                                              range(1, 13)]


def test_a_thin_pool_yields_what_it_has_without_repeating() -> None:
    """A pair-year with few eligible dates must not sample one twice."""
    pool = ["2019-03-01", "2019-03-04", "2019-07-02"]
    picked = cc.sample_dates("EURUSD", 2019, 12, pool, seed=7)
    assert sorted(picked) == pool


def test_a_year_with_no_eligible_dates_samples_nothing() -> None:
    """And does not loop forever looking for one."""
    assert cc.sample_dates("EURUSD", 2019, 12, [], seed=7) == []


def test_the_statistics_summarise_an_absolute_sample() -> None:
    """Count, mean, median, p95 and max, on a sample small enough to check."""
    stats = cc._stats([0.0, 0.1, 0.2, 0.3, 10.0])
    assert stats["n"] == 5
    assert stats["median"] == pytest.approx(0.2)
    assert stats["max"] == pytest.approx(10.0)


def test_an_empty_sample_reports_none_rather_than_zero() -> None:
    """Zero difference and no measurement are different claims."""
    assert cc._stats([]) == {"n": 0, "mean": None, "median": None,
                             "p95": None, "max": None}


def test_the_summary_separates_exempt_hours_from_compared_ones() -> None:
    """A roll hour is compared and reported, but never enters the statistics."""
    rows = [{
        "pair": "EURUSD", "date": "2019-06-12", "missing_hours": [],
        "hours": [
            {"pair": "EURUSD", "date": "2019-06-12", "hour": 9,
             "abs_worst_pips": 0.2, "open_diff_pips": 0.2,
             "close_diff_pips": -0.1, "duka_ticks": 5000,
             "roll_exempt": False, "beyond_threshold": False},
            {"pair": "EURUSD", "date": "2019-06-12", "hour": 21,
             "abs_worst_pips": 90.0, "open_diff_pips": 90.0,
             "close_diff_pips": 1.0, "duka_ticks": 400,
             "roll_exempt": True, "beyond_threshold": False},
        ],
    }]
    summary = cc.summarise(rows, 1.0)
    assert summary["hours_compared"] == 2
    assert summary["hours_roll_exempt"] == 1
    assert summary["hours_beyond_threshold"] == 0
    assert summary["by_pair"]["EURUSD"]["max"] == pytest.approx(0.2)
    # The exempt hour contributes to neither the density cut nor the
    # boundary comparison, or a roll spike would move both.
    assert summary["by_density"]["3k-10k"]["n"] == 1
    assert "<500" not in summary["by_density"]
    assert summary["open_vs_close"]["open_abs"]["n"] == 1


def test_the_density_cut_buckets_by_tick_count() -> None:
    """The stratification that explains the cross-check's whole result."""
    assert cc.density_bucket(0) == "<500"
    assert cc.density_bucket(499) == "<500"
    assert cc.density_bucket(500) == "500-1k"
    assert cc.density_bucket(2999) == "1k-3k"
    assert cc.density_bucket(3000) == "3k-10k"
    assert cc.density_bucket(10_000) == ">=10k"
    assert cc.density_bucket(10_000_000) == ">=10k"


# --------------------------------------------------------------------------- #
# Reading the calendar back: pre-reg #5's closing clause
# --------------------------------------------------------------------------- #

def _write_calendar(tmp_path, full, partial=None, *, window=("2005-01-03",
                                                             "2025-02-28")):
    """A committed calendar file to read back."""
    lines = ["[calendar]",
             f'window_start = "{window[0]}"',
             f'window_end = "{window[1]}"',
             "min_empty_hours = 6",
             "min_pairs_partial = 3",
             "[calendar.full]"]
    lines += [f'"{d}" = "{name}"' for d, name in full.items()]
    lines.append("[calendar.partial]")
    for date, pairs in (partial or {}).items():
        lines.append(f'"{date}" = [{", ".join(chr(34) + p + chr(34) for p in pairs)}]')
    path = tmp_path / "calendar.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_an_absent_calendar_raises_rather_than_reading_empty(tmp_path) -> None:
    """"No holidays" and "no calendar" are different claims."""
    with pytest.raises(FileNotFoundError):
        cal.load_calendar(tmp_path / "nothing.toml")


def test_a_full_holiday_shuts_every_pair(tmp_path) -> None:
    """Which is what makes it a full holiday."""
    path = _write_calendar(tmp_path, {"2019-12-25": "Christmas Day"})
    calendar = cal.load_calendar(path)
    assert calendar.is_holiday("2019-12-25") is True
    assert calendar.is_holiday("2019-12-25", "EURUSD") is True


def test_a_partial_holiday_shuts_only_the_pairs_it_names(tmp_path) -> None:
    """Treating it as market-wide would shut pairs that were trading."""
    path = _write_calendar(tmp_path, {},
                           {"2019-01-02": ["USDJPY", "EURJPY"]})
    calendar = cal.load_calendar(path)
    assert calendar.is_holiday("2019-01-02") is False
    assert calendar.is_holiday("2019-01-02", "USDJPY") is True
    assert calendar.is_holiday("2019-01-02", "EURUSD") is False


def test_an_ordinary_day_is_not_a_holiday(tmp_path) -> None:
    """The baseline the other three are measured against."""
    path = _write_calendar(tmp_path, {"2019-12-25": "Christmas Day"})
    assert cal.load_calendar(path).is_holiday("2019-06-12") is False


def test_an_empty_hour_on_a_calendar_date_becomes_closed(tmp_path) -> None:
    """Pre-reg #5's closing clause, as a function."""
    path = _write_calendar(tmp_path, {"2019-12-25": "Christmas Day"})
    calendar = cal.load_calendar(path)
    assert calendar.classify_empty_hour("2019-12-25") == "closed"
    assert calendar.classify_empty_hour("2019-06-12") == "warning"


def test_a_date_outside_the_window_stays_a_warning(tmp_path) -> None:
    """Unexamined is not the same as examined and found ordinary."""
    path = _write_calendar(tmp_path, {"2019-12-25": "Christmas Day"},
                           window=("2020-01-01", "2025-02-28"))
    calendar = cal.load_calendar(path)
    assert calendar.covers("2019-12-25") is False
    assert calendar.classify_empty_hour("2019-12-25") == "warning"
