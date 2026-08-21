"""The T2a bulk-ingest driver: order, rate, and what counts as a gap.

The driver does not decode, validate or store anything -- Phase 1 does, and the
Phase 1 gate judges that. What is tested here is what the driver alone decides:
the order the work is done in, which hours reach the network at all, when a
failure is a gap and when it is an outage, how the concurrency calibration steps
and backs off, and that an hour nobody finished asking about does not end up
recorded as a hole in the data.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from fxlab.config import DukascopyConfig, HourRequest
from fxlab.ingestion.manifest import (HourRecord, Manifest, STATUS_GAP,
                                      STATUS_OK, write_manifest)
from fxlab.ingestion.sources import (AVAILABILITY_EMPTY, AVAILABILITY_MISSING,
                                     AVAILABILITY_PRESENT, FeedError)
from research.bulk_ingest import (CLOSED_ORIGIN, MAX_LEVEL, MIN_LEVEL,
                                  Calibrator, Chunk, HourFeed, _reaches_the_feed,
                                  load_params, month_starts, plan_chunks,
                                  read_chunk_log, resume_calibration,
                                  strip_aborted)
from research.coverage_probe import FeedUnreachable, SessionStopped
from research.seal import SealBreach

UNIVERSE = ("EURUSD", "GBPUSD", "USDJPY")


# --------------------------------------------------------------------------- #
# Order
# --------------------------------------------------------------------------- #

def test_months_run_newest_first() -> None:
    assert month_starts(dt.date(2024, 11, 5), dt.date(2025, 2, 3)) == [
        (2025, 2), (2025, 1), (2024, 12), (2024, 11)]


def test_all_pairs_of_a_month_precede_the_month_before_it() -> None:
    # The card's ordering rule: a run cut short leaves the most recent history
    # complete rather than every pair ending somewhere arbitrary.
    chunks = plan_chunks(UNIVERSE, dt.date(2024, 12, 1), dt.date(2025, 1, 31))
    assert [c.key for c in chunks] == [
        "EURUSD/2025-01", "GBPUSD/2025-01", "USDJPY/2025-01",
        "EURUSD/2024-12", "GBPUSD/2024-12", "USDJPY/2024-12"]


def test_the_first_and_last_months_are_clipped_to_the_range() -> None:
    chunks = plan_chunks(("EURUSD",), dt.date(2024, 12, 20), dt.date(2025, 1, 9))
    assert (chunks[0].first, chunks[0].last) == (dt.date(2025, 1, 1),
                                                 dt.date(2025, 1, 9))
    assert (chunks[1].first, chunks[1].last) == (dt.date(2024, 12, 20),
                                                 dt.date(2024, 12, 31))


def test_a_chunk_requests_every_hour_including_the_shut_ones() -> None:
    # One manifest entry per hour of the range, closed ones included. Which of
    # them reach the network is the feed's decision, not the plan's.
    chunk = Chunk("EURUSD", 2025, 1, dt.date(2025, 1, 4), dt.date(2025, 1, 5))
    hours = chunk.hours()
    assert len(hours) == 48
    assert chunk.dates() == ["2025-01-04", "2025-01-05"]


# --------------------------------------------------------------------------- #
# Which hours reach the network
# --------------------------------------------------------------------------- #

def _hour(day: str, hour: int) -> HourRequest:
    return HourRequest("EURUSD", dt.date.fromisoformat(day), hour)


def test_open_hours_are_always_fetched() -> None:
    assert _reaches_the_feed(_hour("2025-01-08", 13))


def test_the_middle_of_the_weekend_is_never_fetched() -> None:
    assert not _reaches_the_feed(_hour("2025-01-11", 12))  # Saturday noon
    assert not _reaches_the_feed(_hour("2025-01-12", 3))   # Sunday morning


@pytest.mark.parametrize("day,hour", [
    ("2025-01-10", 22),  # Friday, winter: the week shuts at 22:00Z
    ("2025-01-12", 21),  # Sunday, winter: the hour before the 22:00Z open
    ("2025-07-11", 21),  # Friday, summer: the week shuts at 21:00Z
    ("2025-07-13", 20),  # Sunday, summer: the hour before the 21:00Z open
])
def test_the_hour_either_side_of_a_boundary_is_fetched(day, hour) -> None:
    # The boundary tracks 17:00 America/New_York and therefore moves with
    # daylight saving. Asking for the shut hour next to it costs about 1.7% more
    # requests and turns a derivation into something the manifest checks weekly.
    request = _hour(day, hour)
    from fxlab.ingestion.sessions import is_market_open

    assert not is_market_open(request.start)
    assert _reaches_the_feed(request)


# --------------------------------------------------------------------------- #
# The feed
# --------------------------------------------------------------------------- #

class FakeConnection:
    """Answers with a scripted sequence of ``(status, body)``."""

    host = "datafeed.example"

    def __init__(self, script) -> None:
        self.script = list(script)
        self.asked = 0

    def path_for(self, key) -> str:
        return f"/{key.pair}/{key.date}/{key.hour:02d}h_ticks.bi5"

    def get(self, key):
        self.asked += 1
        answer = self.script.pop(0) if self.script else self.script_default()
        if isinstance(answer, Exception):
            raise answer
        return answer

    def script_default(self):
        return (503, b"")

    def close(self) -> None:
        pass


def _feed(script, **kwargs) -> HourFeed:
    feed = HourFeed(DukascopyConfig(timeout=1.0), **kwargs)
    connection = FakeConnection(script)
    feed._acquire = lambda: connection          # noqa: SLF001 - test seam
    feed._release = lambda _c: None             # noqa: SLF001 - test seam
    feed.pacer.floor = 0.0
    feed.pacer._gap = 0.0                       # noqa: SLF001 - no waiting here
    feed.pacer.cooldowns = (0.0,)
    return feed


def test_a_shut_hour_is_answered_without_touching_the_network() -> None:
    feed = _feed([RuntimeError("the network must not be reached")])
    raw = feed.fetch(_hour("2025-01-11", 12))
    assert raw.availability == AVAILABILITY_EMPTY
    assert raw.origin == CLOSED_ORIGIN
    assert feed.closed_skipped == 1 and feed.requests == 0


def test_an_empty_body_on_an_open_hour_is_served_as_empty() -> None:
    feed = _feed([(200, b"")])
    raw = feed.fetch(_hour("2025-01-08", 13))
    assert raw.availability == AVAILABILITY_EMPTY and feed.requests == 1


def test_a_body_is_served_as_present_with_its_url() -> None:
    feed = _feed([(200, b"payload")])
    raw = feed.fetch(_hour("2025-01-08", 13))
    assert raw.availability == AVAILABILITY_PRESENT
    assert raw.origin.startswith("https://datafeed.example/EURUSD/")


def test_a_404_is_absence_and_is_not_retried() -> None:
    feed = _feed([(404, b"")])
    raw = feed.fetch(_hour("2025-01-08", 13))
    assert raw.availability == AVAILABILITY_MISSING


def test_a_403_is_reported_as_the_feed_being_unreachable() -> None:
    # Phase 1 established that the front end rejects VPN egress outright while
    # the marketing site keeps working; that is not a routing fault to retry.
    feed = _feed([(403, b"")])
    with pytest.raises(FeedUnreachable):
        feed.fetch(_hour("2025-01-08", 13))


def test_an_hour_that_fails_while_the_feed_answers_is_a_gap() -> None:
    feed = _feed([(503, b"")] * 4, max_attempts=3)
    with pytest.raises(FeedError):
        feed.fetch(_hour("2025-01-08", 13))


def test_an_hour_that_fails_while_nothing_answers_is_an_outage_not_a_gap() -> None:
    # Three exhausted hours in a row is the feed being down. Recording them as
    # gaps would put holes in the coverage report that the feed never had.
    feed = _feed([(503, b"")] * 100, max_attempts=1, outage_budget=0.0,
                 parks=(0.0,))
    for _ in range(2):
        with pytest.raises(FeedError):
            feed.fetch(_hour("2025-01-08", 13))
    with pytest.raises(FeedUnreachable) as caught:
        feed.fetch(_hour("2025-01-08", 13))
    assert "answered nothing" in str(caught.value)


def test_a_stopping_session_abandons_an_hour_rather_than_failing_it() -> None:
    feed = _feed([(200, b"payload")], should_stop=lambda: True)
    request = _hour("2025-01-08", 13)
    with pytest.raises(SessionStopped):
        feed.fetch(request)
    assert request.key in feed.aborted


def test_raising_the_level_shortens_the_gap_and_keeps_the_counters() -> None:
    feed = _feed([])
    feed.pacer.throttles = 7
    feed.pacer.parked = 3.0
    feed.set_level(MAX_LEVEL)
    assert feed.level == MAX_LEVEL
    assert feed.throttles == 7 and feed.parked_seconds == 3.0
    assert feed.pacer.floor < 0.4


def test_a_connection_gets_the_generous_read_timeout() -> None:
    # T1's 15s was calibrated when a warm GET cost 0.09-0.42s. Against a feed
    # answering in 14.4-14.8s -- measured on the first day of this run -- it
    # converts "slow but answering" into "failed", drops the connection and pays
    # a reconnect for nothing.
    feed = HourFeed(DukascopyConfig(timeout=1.0), read_timeout=45.0)
    connection = feed._acquire()                # noqa: SLF001 - test seam
    assert connection.read_timeout == 45.0
    feed.close()


def test_the_level_is_clamped_to_the_cards_ceiling() -> None:
    feed = _feed([])
    feed.set_level(9)
    assert feed.level == MAX_LEVEL
    feed.set_level(0)
    assert feed.level == MIN_LEVEL


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #

class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _run_windows(cal: Calibrator, clock: FakeClock, windows: int,
                 requests: int, throttles: int) -> None:
    for _ in range(windows):
        clock.advance(cal.window_seconds + 1.0)
        cal.observe(requests, throttles)


def test_calibration_starts_where_T1_proved_safe() -> None:
    cal = Calibrator(clock=FakeClock())
    assert cal.level == MIN_LEVEL
    assert cal.history[0]["level"] == MIN_LEVEL


def test_a_clean_hour_steps_the_level_up_one_at_a_time() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 6, 1000, 5)     # an hour, 0.5% throttled
    assert cal.level == MIN_LEVEL + 1
    _run_windows(cal, clock, 6, 1000, 5)
    assert cal.level == MIN_LEVEL + 2 == MAX_LEVEL


def test_the_level_never_passes_the_cards_ceiling() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 40, 1000, 2)
    assert cal.level == MAX_LEVEL


def test_a_sustained_rise_in_throttling_backs_off_to_the_last_safe_level() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 6, 1000, 10)    # clean hour at level 2: 1%
    assert cal.level == 3
    _run_windows(cal, clock, 2, 1000, 200)   # 20% at level 3
    assert cal.level == 2
    assert "against a tolerance" in cal.history[-1]["why"]


def test_one_bad_window_is_not_enough_to_back_off() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 6, 1000, 10)
    assert cal.level == 3
    _run_windows(cal, clock, 1, 1000, 200)
    assert cal.level == 3


def test_one_bad_window_does_not_reset_the_clean_clock() -> None:
    # The feed flaps on a one-minute cycle, so a single burst is noise. Resetting
    # the whole clean hour for one of them vetoes every step-up indefinitely --
    # measured: level 3 ran 4.4%, 0.7%, 2.2%, 1.6% and then one window at 15.0%.
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 3, 1000, 10)     # clean
    _run_windows(cal, clock, 1, 1000, 400)    # one burst
    _run_windows(cal, clock, 3, 1000, 10)     # clean again, past the hour
    assert cal.level == MIN_LEVEL + 1


def test_two_bad_windows_at_the_floor_block_the_level_above() -> None:
    # There is nowhere to back off to at level 2, so what must be blocked is
    # level 3: a feed complaining at the floor is not one to offer more to.
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 2, 1000, 400)
    assert cal.level == MIN_LEVEL
    _run_windows(cal, clock, 8, 1000, 10)
    assert cal.level == MIN_LEVEL


def test_a_level_that_was_backed_off_is_not_probed_again_immediately() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 6, 1000, 10)
    _run_windows(cal, clock, 2, 1000, 200)
    assert cal.level == 2
    _run_windows(cal, clock, 12, 1000, 10)   # two more clean hours at level 2
    assert cal.level == 2


def test_the_calibration_record_carries_the_rates_that_caused_each_move() -> None:
    clock = FakeClock()
    cal = Calibrator(clock=clock)
    _run_windows(cal, clock, 6, 1000, 10)
    record = cal.to_dict()
    assert record["final_level"] == 3
    assert record["baselines"]["2"] == pytest.approx(0.01)
    assert len(record["windows"]) == 6


def test_a_high_baseline_does_not_license_a_higher_one() -> None:
    # Measured on 2026-08-21: a level-3 window running 8.9% throttled passed as
    # clean on the absolute cap alone and stepped the run up to four connections
    # while one request in eleven was being refused. A feed complaining that much
    # is not one to offer more to, whatever the level below happened to do.
    cal = Calibrator(level=3, baselines={2: 0.09}, clock=FakeClock())
    assert cal.tolerance_at(3) == pytest.approx(0.10)   # absolute cap binds
    cal = Calibrator(level=3, baselines={2: 0.01}, clock=FakeClock())
    assert cal.tolerance_at(3) == pytest.approx(0.03)   # comparative binds


def test_the_floor_is_judged_absolutely_because_nothing_is_below_it() -> None:
    cal = Calibrator(clock=FakeClock())
    assert cal.tolerance_at(MIN_LEVEL) == pytest.approx(0.10)


def test_the_calibration_resumes_from_what_the_run_measured(tmp_path) -> None:
    # The calibration is a property of the run, not the session. Reading only the
    # last record loses exactly the baseline the level above it is judged against.
    path = tmp_path / "sessions.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"calibration": {"final_level": 2,
                                        "baselines": {"2": 0.0244}}}),
            json.dumps({"calibration": {"final_level": 3,
                                        "baselines": {"3": 0.0231}}}),
        ]),
        encoding="utf-8")
    level, baselines = resume_calibration(path)
    assert level == 3
    assert baselines == {2: pytest.approx(0.0244), 3: pytest.approx(0.0231)}


def test_a_fresh_run_has_nothing_to_resume(tmp_path) -> None:
    assert resume_calibration(tmp_path / "absent.jsonl") == (None, {})


# --------------------------------------------------------------------------- #
# Hours nobody finished asking about
# --------------------------------------------------------------------------- #

def test_an_abandoned_hour_is_removed_rather_than_recorded_as_a_gap(tmp_path) -> None:
    manifest = Manifest(hours=[
        HourRecord("EURUSD", "2025-01-08", 13, STATUS_OK, written_ticks=10),
        HourRecord("EURUSD", "2025-01-08", 14, STATUS_GAP),
    ])
    manifest.warnings.append({"pair": "EURUSD", "date": "2025-01-08",
                              "hour": 14, "reason": "FETCH_ERROR"})
    write_manifest(tmp_path, manifest)

    removed = strip_aborted(tmp_path, {("EURUSD", "2025-01-08", 14)})
    assert removed == 1

    from fxlab.ingestion.manifest import load_manifest

    reloaded = load_manifest(tmp_path)
    assert [r.key for r in reloaded.hours] == [("EURUSD", "2025-01-08", 13)]
    assert reloaded.warnings == []


def test_stripping_nothing_touches_nothing(tmp_path) -> None:
    assert strip_aborted(tmp_path, set()) == 0


# --------------------------------------------------------------------------- #
# Progress records
# --------------------------------------------------------------------------- #

def test_the_progress_file_survives_a_half_written_final_line(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        json.dumps({"chunk": "EURUSD/2025-01", "hours_ok": 700}) + "\n"
        + json.dumps({"chunk": "GBPUSD/2025-01", "hours_ok": 12}) + "\n"
        + '{"chunk": "USDJPY/2025-01", "hours_o', encoding="utf-8")
    records = read_chunk_log(path)
    assert set(records) == {"EURUSD/2025-01", "GBPUSD/2025-01"}


def test_a_rewritten_chunk_keeps_only_its_latest_record(tmp_path) -> None:
    path = tmp_path / "chunks.jsonl"
    path.write_text(
        json.dumps({"chunk": "EURUSD/2025-01", "hours_gap": 3}) + "\n"
        + json.dumps({"chunk": "EURUSD/2025-01", "hours_gap": 0}) + "\n",
        encoding="utf-8")
    assert read_chunk_log(path)["EURUSD/2025-01"]["hours_gap"] == 0


# --------------------------------------------------------------------------- #
# The seal
# --------------------------------------------------------------------------- #

def _config(tmp_path: pathlib.Path, start: str, end: str) -> pathlib.Path:
    path = tmp_path / "cfg.toml"
    path.write_text(
        "[experiment]\nid='x'\ntaskcard='T2a'\nentry='e:f'\nseed=1\n"
        "mode='scoring'\n\n[experiment.params]\n"
        "pairs=['EURUSD']\n"
        f"start_date='{start}'\nend_date='{end}'\n"
        "out_dir='data/research'\nexperiment_dir='experiments/x'\n",
        encoding="utf-8")
    return path


def test_a_sealed_end_date_is_refused_before_anything_runs(tmp_path) -> None:
    # Enforced in the driver as well as in the gate: the loop must not be able
    # to ask the feed for a sealed hour even if a config slipped past review.
    with pytest.raises(SealBreach):
        load_params(_config(tmp_path, "2015-01-01", "2025-06-01"),
                    pathlib.Path("."))


def test_the_research_window_itself_is_accepted(tmp_path) -> None:
    params = load_params(_config(tmp_path, "2015-01-01", "2025-02-28"),
                         pathlib.Path("."))
    assert params.pairs == ("EURUSD",)
    assert params.end == dt.date(2025, 2, 28)


def test_an_end_before_the_start_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        load_params(_config(tmp_path, "2020-01-01", "2019-01-01"),
                    pathlib.Path("."))
