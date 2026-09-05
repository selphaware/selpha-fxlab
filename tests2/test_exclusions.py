"""Ruling R1's exclusion windows: refusal, clamping, and staying visible.

The seal and an exclusion look alike and mean opposite things. The seal hides
data that exists and is fine; an exclusion refuses data that exists and is not.
So the tests here care about two properties the seal's tests do not: that a
caller which clamps a range says how much it dropped, and that the drop is
still countable afterwards. An exclusion nobody can count is indistinguishable
from a pull that never happened.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research import exclusions as ex
from research import loader as loader_mod


def test_the_ruling_in_force_is_audusd_before_2011() -> None:
    """The table is the ruling; a test that restated it would prove nothing.

    What is worth pinning is that the table is *non-empty and specific*, since
    an exclusion silently dropped from the tuple would make every refusal below
    vacuously pass.
    """
    assert ex.excluded_pairs() == ("AUDUSD",)
    entry = ex.exclusion_for("AUDUSD")
    assert entry is not None
    assert entry.before == dt.date(2011, 1, 1)
    assert entry.ruling == "R1"


@pytest.mark.parametrize(
    ("pair", "date", "expected"),
    [
        ("AUDUSD", "2010-12-31", True),
        ("AUDUSD", "2011-01-01", False),   # the boundary is inclusive-open
        ("AUDUSD", "2005-01-03", True),
        ("AUDUSD", "2020-06-01", False),
        ("EURUSD", "2005-01-03", False),   # no exclusion on other pairs
    ],
)
def test_membership_of_the_window(pair: str, date: str, expected: bool) -> None:
    """Including both sides of the boundary, which is where off-by-one lives."""
    assert ex.is_excluded(pair, date) is expected


def test_refusal_names_the_reason_and_the_ruling() -> None:
    """The token is what a report and a gate both grep for."""
    with pytest.raises(ex.PairExcluded) as caught:
        ex.assert_not_excluded("AUDUSD", "2009-06-01", "a test")
    message = str(caught.value)
    assert ex.PAIR_EXCLUDED_WINDOW in message
    assert "R1" in message
    assert "a test" in message


def test_an_unexcluded_pair_date_passes_silently() -> None:
    """No exception, no return value, no surprise."""
    assert ex.assert_not_excluded("AUDUSD", "2011-01-01") is None
    assert ex.assert_not_excluded("EURUSD", "2005-01-03") is None


def test_clamp_window_trims_only_the_excluded_head() -> None:
    """A window overlapping the exclusion comes back starting after it."""
    assert ex.clamp_window("AUDUSD", "2005-01-03", "2014-12-31") == (
        dt.date(2011, 1, 1), dt.date(2014, 12, 31))


def test_clamp_window_leaves_a_clean_window_alone() -> None:
    """Both for a pair with no exclusion and for one clear of its window."""
    assert ex.clamp_window("EURUSD", "2005-01-03", "2014-12-31") == (
        dt.date(2005, 1, 3), dt.date(2014, 12, 31))
    assert ex.clamp_window("AUDUSD", "2015-01-01", "2025-02-28") == (
        dt.date(2015, 1, 1), dt.date(2025, 2, 28))


def test_a_fully_excluded_window_is_none_rather_than_empty() -> None:
    """``None`` and an empty range are different answers.

    An empty range invites a caller to carry on and report zeroes for a window
    it was never allowed to look at. ``None`` makes it say so.
    """
    assert ex.clamp_window("AUDUSD", "2007-01-01", "2008-12-31") is None


def test_split_dates_returns_both_halves() -> None:
    """The dropped half is the number ruling R1 makes reports state."""
    permitted, excluded = ex.split_dates(
        "AUDUSD", ["2010-12-30", "2010-12-31", "2011-01-01", "2011-01-02"])
    assert permitted == ["2011-01-01", "2011-01-02"]
    assert excluded == ["2010-12-30", "2010-12-31"]


def test_split_dates_sorts_and_deduplicates() -> None:
    """A caller's iteration order must not reach a hashed result."""
    permitted, _ = ex.split_dates(
        "EURUSD", ["2012-05-02", "2012-05-01", "2012-05-02"])
    assert permitted == ["2012-05-01", "2012-05-02"]


def test_summarise_only_reports_pairs_that_were_asked_about() -> None:
    """A universe without the pair carries no exclusion row."""
    assert ex.summarise(["EURUSD", "GBPUSD"]) == []
    rows = ex.summarise(["EURUSD", "AUDUSD"])
    assert [r["pair"] for r in rows] == ["AUDUSD"]
    assert rows[0]["window"] == "before 2011-01-01"


def test_an_exclusion_bounding_nothing_is_rejected() -> None:
    """A row that excludes nothing is a comment, not an enforcement rule."""
    with pytest.raises(ValueError):
        ex.Exclusion("EURUSD", ruling="R0", why="nothing")


# --------------------------------------------------------------------------- #
# Enforcement through the loader
# --------------------------------------------------------------------------- #

@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway project root with the research tree present."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    (tmp_path / "data" / "live_week").mkdir(parents=True)
    return tmp_path


def _write_bars(base: pathlib.Path, pair: str, days: list[str]) -> None:
    """One 1h bar per named day, enough for the loader to read back."""
    path = (base / "data" / "research" / "bars" / "timeframe=1h"
            / f"pair={pair}")
    path.mkdir(parents=True, exist_ok=True)
    stamps = pd.to_datetime([f"{d}T00:00:00Z" for d in days])
    table = pa.table({
        "pair": pa.array([pair] * len(days), type=pa.large_string()),
        "ts": pa.array(stamps.to_pydatetime(),
                       type=pa.timestamp("us", tz="UTC")),
        "mid_close": pa.array([1.0] * len(days), type=pa.float64()),
    })
    pq.write_table(table, path / f"{pair}_1h.parquet")


def test_the_loader_refuses_an_excluded_tick_date(base: pathlib.Path) -> None:
    """And refuses before the filesystem is consulted, like the seal does."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    with pytest.raises(ex.PairExcluded) as caught:
        loader.load_ticks("AUDUSD", ["2009-06-01"])
    assert ex.PAIR_EXCLUDED_WINDOW in str(caught.value)


def test_the_loader_serves_the_same_pair_outside_the_window(
        base: pathlib.Path) -> None:
    """The exclusion is a window, not a ban on the pair."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    frame = loader.load_ticks("AUDUSD", ["2011-01-03"])
    assert len(frame) == 0
    assert loader.access.dates == {"2011-01-03"}


def test_an_unbounded_bar_read_of_an_excluded_pair_refuses(
        base: pathlib.Path) -> None:
    """Reading the whole table is how the exclusion would be walked past."""
    _write_bars(base, "AUDUSD", ["2010-06-01", "2012-06-01"])
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    with pytest.raises(ex.PairExcluded):
        loader.load_bars("AUDUSD", "1h")


def test_a_windowed_bar_read_serves_the_permitted_part(
        base: pathlib.Path) -> None:
    """The caller states the window; the loader polices what that yields."""
    _write_bars(base, "AUDUSD", ["2010-06-01", "2012-06-01"])
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    frame = loader.load_bars("AUDUSD", "1h",
                             start="2011-01-01", end="2014-12-31")
    assert len(frame) == 1
    assert loader.access.dates == {"2012-06-01"}


def test_a_window_that_still_covers_excluded_dates_refuses(
        base: pathlib.Path) -> None:
    """Windowing is not a bypass: what is served is still policed."""
    _write_bars(base, "AUDUSD", ["2010-06-01", "2012-06-01"])
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    with pytest.raises(ex.PairExcluded):
        loader.load_bars("AUDUSD", "1h", start="2010-01-01", end="2012-12-31")


def test_a_clamped_read_records_what_it_dropped(base: pathlib.Path) -> None:
    """The access log is where an exclusion stops looking like absence."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    loader.note_excluded("AUDUSD", ["2010-12-30", "2010-12-31"])
    assert loader.access.excluded == {"AUDUSD:2010-12-30", "AUDUSD:2010-12-31"}
    assert loader.access.excluded_pairs() == ["AUDUSD"]
    assert loader.access.to_dict()["excluded"] == [
        "AUDUSD:2010-12-30", "AUDUSD:2010-12-31"]


def test_the_exclusion_canary_reports_the_refusal(base: pathlib.Path) -> None:
    """A refusal nobody exercises is a refusal nobody knows is still wired."""
    refused, detail = loader_mod.exclusion_canary(base=base)
    assert refused is True
    assert ex.PAIR_EXCLUDED_WINDOW in detail


def test_the_seal_and_the_exclusion_do_not_shadow_each_other(
        base: pathlib.Path) -> None:
    """Each still fires for its own reason on its own dates."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    from research.seal import SealBreach

    with pytest.raises(SealBreach):
        loader.load_ticks("AUDUSD", ["2025-03-01"])
    with pytest.raises(ex.PairExcluded):
        loader.load_ticks("AUDUSD", ["2009-01-05"])
