"""T2a: bulk ingestion of the twelve-pair universe, newest month first.

    python -m research.bulk_ingest --config experiments/T2a-ingestion/config.toml

What this is
------------
A resumable, politely-paced driver that fills ``data/research/`` with every open
hour of Dukascopy tick data for the pairs and range its config names, validated
by the **Phase 1 pipeline** rather than by anything reimplemented here. It owns
three things Phase 1 does not: how the work is ordered, how fast it is allowed
to ask, and what happens when the feed stops answering.

Everything else is delegated. ``fxlab.ingestion.pipeline.ingest`` decodes,
de-duplicates, validates and stores each hour and writes the manifest;
``fxlab.ingestion.bars.build_bars_incremental`` folds the new days into the bar
tables. A bug in validation is a Phase 1 bug and shows up in the Phase 1 gate,
which is where it belongs.

Order
-----
Reverse-chronological by month, all pairs within a month before the previous
month (task card T2a). A run cut short therefore leaves the most recent -- and
most researched -- history complete, rather than a decade of everything ending
somewhere arbitrary.

Rate
----
One global :class:`~research.coverage_probe.Pacer` governs the offered request
rate, and it is the binding constraint: a GET down a warm connection costs
0.09-0.42s, so two workers unpaced would offer several times what the feed
tolerates. Raising the connection count without raising the paced rate would
therefore change nothing measurable. The calibration in :class:`Calibrator`
consequently moves both together -- level *n* means *n* connections and a gap of
``BASE_GAP_SECONDS / n`` -- so that "probe a higher concurrency" is a real
increase in offered load, which is what the card asks to be tested against the
feed's 503 rate. Level 2 reproduces T1's proven-safe settings exactly.

Outages
-------
An hour that exhausts its attempts while the feed is otherwise answering is a
genuine per-hour failure and becomes a manifest gap. An hour that exhausts its
attempts while *nothing* is being answered is not evidence about that hour at
all, so the session parks on an escalating schedule and asks again. Only after
``outage_budget_seconds`` of that does it give up, which is the one condition
CLAUDE.md calls a stop-and-report rather than something to work around.

Hours nobody finished asking about
----------------------------------
When a session stops -- budget spent, stop file, Ctrl-C -- the hours still in
flight are recorded by the pipeline as gaps, because a fetch that raises is
indistinguishable from one that failed. They are not gaps: nobody finished
asking. :func:`strip_aborted` removes them from the shard manifest afterwards,
so the next session finds them unsettled and asks again. Recording them would be
cheaper and would put holes in the coverage report that the feed never had.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import queue
import signal
import sys
import threading
import time
import tomllib
from typing import Any, Callable, Final, Sequence

from fxlab.config import DukascopyConfig, HourRequest, IngestConfig
from fxlab.ingestion.bars import build_bars_incremental
from fxlab.ingestion.manifest import load_manifest, write_manifest
from fxlab.ingestion.pipeline import ingest
from fxlab.ingestion.sessions import is_market_open
from fxlab.ingestion.sources import (
    AVAILABILITY_EMPTY,
    AVAILABILITY_MISSING,
    AVAILABILITY_PRESENT,
    FeedError,
    RawHour,
)
from fxlab.logging_setup import configure_logging
from research import ledger as ledger_mod
from research.coverage_probe import (
    EXIT_FEED_UNREACHABLE,
    RETRYABLE_STATUS,
    Connection,
    EndpointPool,
    FeedUnreachable,
    Pacer,
    ProbeKey,
    SessionStopped,
)
from research.seal import as_date, assert_not_sealed

_LOG: Final[logging.Logger] = logging.getLogger("research.bulk_ingest")

# --------------------------------------------------------------------------- #
# Policy constants, all stated before the run rather than tuned during it
# --------------------------------------------------------------------------- #

#: Bar timeframes the store maintains (SPEC2 pre-reg #6).
TIMEFRAMES: Final[tuple[str, ...]] = ("1m", "5m", "30m", "1h", "4h", "1d")

#: Concurrency floor and the card's hard ceiling.
MIN_LEVEL: Final[int] = 2
MAX_LEVEL: Final[int] = 4

#: Aggregate inter-request gap is ``BASE_GAP_SECONDS / level``. At level 2 this
#: is 0.4s, which is exactly the floor T1 sustained for ten hours.
BASE_GAP_SECONDS: Final[float] = 0.8

#: Ceiling the pacer may widen the gap to under sustained complaint.
PACER_CEILING_SECONDS: Final[float] = 4.0

#: How the steady-state gap responds to a failure and to a success.
#:
#: T1's Pacer separates two failure modes and says why: the **gap** answers rate
#: limiting, and the **cooldown** answers a backend outage, "and no amount of
#: widening the gap shortens it". Its own numbers -- widen by 1.25, decay by
#: 0.98 -- were tuned for a survey against a feed that was throttling. Measured
#: here on 2026-08-20, the feed was doing the other thing: one address answering
#: HTTP 503 to everything in 20ms and the other alternating 200s and 503s. At a
#: 50% failure rate 1.25 x 0.98 pins the gap at the ceiling within a minute, so
#: the client answers a dead backend by offering less load -- which fixes
#: nothing and costs everything -- and then takes 114 consecutive successes to
#: crawl back down.
#:
#: 1.05 and 0.90 keep the same two controls and let the cooldowns do the job T1
#: assigned them. A genuine rate limit still walks the gap up (and the ceiling
#: still stands); a dead backend no longer does, and recovery takes about
#: twenty successes rather than a hundred and fourteen.
PACER_WIDEN_FACTOR: Final[float] = 1.05
PACER_DECAY: Final[float] = 0.90

#: Continuous clean seconds at a level before the next one is probed.
CLEAN_BEFORE_STEP_UP: Final[float] = 3600.0

#: Length of one calibration evaluation window.
EVAL_WINDOW_SECONDS: Final[float] = 600.0

#: A window is clean while its throttled share of requests stays at or below
#: this.
#:
#: Raised from 0.05 to 0.10 on 2026-08-20, before any pair-month past 2025-02
#: had been ingested, and recorded here rather than quietly. 0.05 was set from
#: T1's measurement -- 782 throttles across 58,386 probes, 1.3%, over ten hours
#: -- and on the day this run started the same feed was answering 3-6% throttled
#: at the same level 2, with one of its two addresses serving 503 to everything.
#: An absolute threshold calibrated against the feed's good mood is not a test
#: of whether *our* load is the problem: it just pins the run at the starting
#: level whenever the feed is having a bad day, which is the day you most want
#: the extra connections.
#:
#: The protection that matters is unchanged and is comparative rather than
#: absolute: a stepped-up level is judged against the **measured** rate of the
#: level below it, and two windows above 1.5x that rate send it back and block
#: it for six hours. That test is self-calibrating -- it asks whether adding a
#: connection made things worse, which is the actual question -- and this
#: constant only decides when it is allowed to be asked.
CLEAN_THROTTLE_RATE: Final[float] = 0.10

#: Consecutive windows above tolerance that count as a *sustained* rise.
#:
#: The card asks for one thing in each direction: step up "after a sustained
#: clean hour", back off "on any sustained rise in 503 rate". This constant is
#: what "sustained" means, and it deliberately means the same thing both ways.
#: The first version of this code used it only for backing off, so a single bad
#: window -- one burst, on a feed measured to flap on a one-minute cycle -- reset
#: the whole clean hour while two were needed to conclude anything. Measured on
#: 2026-08-20, level 3 ran four windows at 4.4%, 0.7%, 2.2% and 1.6% and then one
#: at 15.0%, and that single window alone would have vetoed the level-4 probe
#: indefinitely. An isolated burst is not evidence in either direction.
BAD_WINDOWS_BEFORE_BACKOFF: Final[int] = 2

#: A stepped-up level is judged against the level below it: it must not exceed
#: 1.5x that rate, nor exceed it by more than two percentage points.
STEP_UP_RATE_FACTOR: Final[float] = 1.5
STEP_UP_RATE_MARGIN: Final[float] = 0.02

#: After backing off, how long before the same step-up is probed again.
REPROBE_AFTER_BACKOFF: Final[float] = 6 * 3600.0

#: Attempts on one hour before it is either a gap or evidence of an outage.
DEFAULT_MAX_ATTEMPTS: Final[int] = 12

#: How long to wait for a response once the connection is up.
#:
#: Deliberately four times T1's 15s, and measured rather than guessed. On
#: 2026-08-20 the datafeed resolved to two addresses, one answering HTTP 503 to
#: everything in 20ms and the other answering 200 in 14.4-14.8 seconds. A 15s
#: timeout against a feed in that state is the worst of every option: it waits
#: the full 15s, gets nothing, drops the connection and pays a reconnect. T1's
#: own reasoning argues for the opposite -- a response that is merely slow must
#: be waited for, because abandoning it costs more than the wait -- and 15s was
#: simply calibrated against a healthier feed than this one. Sixty seconds still
#: bounds a genuinely hung backend; it just stops calling a slow one hung.
DEFAULT_READ_TIMEOUT: Final[float] = 60.0

#: Failed attempts between progress warnings while the feed is misbehaving.
#: Without this a degraded feed looks identical to a hung client.
FAILURE_LOG_EVERY: Final[int] = 50

#: Consecutive exhausted hours, or seconds without any answer, that mean the
#: feed itself is down rather than one hour being unlucky.
DEAD_FEED_STREAK: Final[int] = 3
HEALTHY_WINDOW_SECONDS: Final[float] = 120.0

#: Escalating park while the feed is down; the last entry repeats. Capped at
#: fifteen minutes so a long outage costs waiting rather than requests.
OUTAGE_PARKS: Final[tuple[float, ...]] = (30.0, 60.0, 120.0, 300.0, 600.0, 900.0)

#: Seconds of continuous outage before the session gives up and reports.
DEFAULT_OUTAGE_BUDGET: Final[float] = 3 * 3600.0

#: Manifest origin recorded for an hour the derived week boundary called shut.
CLOSED_ORIGIN: Final[str] = "derived:market-closed"

#: Per-chunk progress records. Not ``*.json``: the research gate reads every
#: ``*.json`` under ``experiments/`` as an experiment result.
CHUNKS_NAME: Final[str] = "chunks.jsonl"

#: One record per finished driver session: its counters and its calibration.
#: Written here rather than left only in the ledger, because the summary must
#: be able to read it without reading the ledger -- the ledger gains a line
#: every time the summary runs, which would make its own hash irreproducible.
SESSIONS_NAME: Final[str] = "sessions.jsonl"

#: Where the sharded manifests live inside the store.
MANIFEST_DIRNAME: Final[str] = "manifests"

#: Manifest checkpoint interval, in hours. Roughly every twenty seconds at
#: level 2, which is the resolution a cold restart resumes at.
CHECKPOINT_EVERY: Final[int] = 50


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True, slots=True)
class Chunk:
    """One pair-month of work: the unit of ordering, resume and reporting."""

    pair: str
    year: int
    month: int
    first: dt.date
    last: dt.date

    @property
    def key(self) -> str:
        """Stable identity used in the progress file."""
        return f"{self.pair}/{self.year:04d}-{self.month:02d}"

    @property
    def month_label(self) -> str:
        """``YYYY-MM``."""
        return f"{self.year:04d}-{self.month:02d}"

    def dates(self) -> list[str]:
        """Every UTC date in the chunk, as ISO strings."""
        span = (self.last - self.first).days + 1
        return [(self.first + dt.timedelta(days=i)).isoformat()
                for i in range(span)]

    def hours(self) -> tuple[HourRequest, ...]:
        """Every hour of every day, including the ones the market is shut.

        All twenty-four are requested so that the manifest carries one entry per
        hour of the range, closed ones included -- the card's rule. Which of
        them reach the network is :meth:`HourFeed.fetch`'s decision, not this
        one's.
        """
        out: list[HourRequest] = []
        span = (self.last - self.first).days + 1
        for i in range(span):
            day = self.first + dt.timedelta(days=i)
            for hour in range(24):
                out.append(HourRequest(pair=self.pair, day=day, hour=hour))
        return tuple(out)


def month_starts(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    """Every ``(year, month)`` the inclusive range touches, newest first."""
    months: list[tuple[int, int]] = []
    year, month = end.year, end.month
    while (year, month) >= (start.year, start.month):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def plan_chunks(pairs: Sequence[str], start: dt.date, end: dt.date) -> list[Chunk]:
    """Build the reverse-chronological pair-month plan.

    Args:
        pairs: The universe, in the order it should be worked within a month.
        start: First date, inclusive.
        end: Last date, inclusive.

    Returns:
        Chunks newest month first, all pairs of a month before the month before
        it -- so an interrupted run leaves recent history complete.
    """
    chunks: list[Chunk] = []
    for year, month in month_starts(start, end):
        first = max(start, dt.date(year, month, 1))
        if month == 12:
            month_end = dt.date(year, 12, 31)
        else:
            month_end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
        last = min(end, month_end)
        for pair in pairs:
            chunks.append(Chunk(pair=pair, year=year, month=month,
                                first=first, last=last))
    return chunks


# --------------------------------------------------------------------------- #
# The feed
# --------------------------------------------------------------------------- #

class HourFeed:
    """An :class:`~fxlab.ingestion.sources.HourSource` over pooled connections.

    Phase 1's ``DukascopySource`` opens a fresh ``urllib`` connection per hour.
    T1 measured connection setup at 3-12s against 0.09-0.42s for a GET down a
    warm one, so at three quarters of a million hours that difference is the
    whole run. This keeps a small pool of keep-alive connections, chosen through
    the :class:`~research.coverage_probe.EndpointPool` that learns which of the
    host's addresses are actually serving, and hands them out per fetch rather
    than per thread -- the ingest pipeline builds a new thread pool for every
    chunk, and connections must outlive that.

    Hours the derived FX week boundary says are shut never reach the network:
    they are answered locally with the same empty body the feed would have sent,
    and the pipeline records them as ``closed``. The two hours either side of
    each boundary *are* fetched, so the derivation is checked against the feed
    every week rather than trusted.
    """

    name = "dukascopy"

    def __init__(self, config: DukascopyConfig, *, level: int = MIN_LEVEL,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 outage_budget: float = DEFAULT_OUTAGE_BUDGET,
                 read_timeout: float = DEFAULT_READ_TIMEOUT,
                 parks: Sequence[float] = OUTAGE_PARKS,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.config = config
        self.max_attempts = max_attempts
        self.outage_budget = outage_budget
        self.read_timeout = read_timeout
        self.parks = tuple(parks)
        self.should_stop = should_stop or (lambda: False)
        self.pool = EndpointPool(_host_of(config.base_url))

        self._lock = threading.Lock()
        self._idle: queue.LifoQueue[Connection] = queue.LifoQueue()
        self._pacer = self._new_pacer(level)
        self._retired_throttles = 0
        self._retired_parked = 0.0
        self._last_success = time.monotonic()
        self._exhausted_streak = 0
        self._outage_started: float | None = None
        self._outage_round = 0

        self.level = level
        self.aborted: set[tuple[str, str, int]] = set()
        self.requests = 0
        self.closed_skipped = 0
        self.boundary_probes = 0
        self.connections_opened = 0
        self.outages_ridden_out = 0
        self.attempts_spent = 0
        self.failed_attempts = 0
        self.hours_exhausted = 0

    # -- rate ---------------------------------------------------------------

    @staticmethod
    def _new_pacer(level: int) -> Pacer:
        """A pacer offering what ``level`` connections are allowed to offer."""
        return Pacer(floor=BASE_GAP_SECONDS / level,
                     ceiling=PACER_CEILING_SECONDS,
                     factor=PACER_WIDEN_FACTOR, decay=PACER_DECAY)

    @property
    def pacer(self) -> Pacer:
        """The pacer currently governing the offered rate."""
        return self._pacer

    @property
    def throttles(self) -> int:
        """Throttled responses across every pacer this feed has used."""
        return self._retired_throttles + self._pacer.throttles

    @property
    def parked_seconds(self) -> float:
        """Seconds spent in cooldown across every pacer this feed has used."""
        return self._retired_parked + self._pacer.parked

    def set_level(self, level: int) -> None:
        """Install the gap that ``level`` connections are allowed to offer.

        The connections and the endpoint pool survive; only the pacer is
        replaced, and its counters are carried forward so the session's totals
        stay whole across a calibration step.
        """
        level = max(MIN_LEVEL, min(MAX_LEVEL, int(level)))
        if level == self.level:
            return
        with self._lock:
            self._retired_throttles += self._pacer.throttles
            self._retired_parked += self._pacer.parked
            self._pacer = self._new_pacer(level)
            self.level = level
        _LOG.info("concurrency level %d: %d connection(s), %.3fs gap "
                  "(%.2f req/s offered)", level, level,
                  BASE_GAP_SECONDS / level, level / BASE_GAP_SECONDS)

    # -- connections --------------------------------------------------------

    def _acquire(self) -> Connection:
        """Take an idle connection, or open one."""
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self.connections_opened += 1
        connection = Connection(self.config.base_url, self.config.timeout,
                                self.config.user_agent, pool=self.pool)
        connection.read_timeout = self.read_timeout
        return connection

    def _release(self, connection: Connection) -> None:
        """Return a connection to the pool. A dropped one reopens on next use."""
        self._idle.put(connection)

    def close(self) -> None:
        """Close every idle connection."""
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                return

    # -- fetching -----------------------------------------------------------

    def fetch(self, request: HourRequest) -> RawHour:
        """Return the raw payload for one hour.

        Raises:
            SessionStopped: If the session is stopping. The hour is recorded in
                :attr:`aborted` and stripped from the manifest afterwards, so
                that it stays unsettled rather than becoming a fictional gap.
            FeedError: If the hour exhausted its attempts while the feed was
                otherwise answering. That is a real gap.
            FeedUnreachable: On HTTP 403, or after ``outage_budget`` seconds of
                the feed answering nothing at all.
        """
        if self.should_stop():
            with self._lock:
                self.aborted.add(request.key)
            raise SessionStopped(request.label())

        if not _reaches_the_feed(request):
            with self._lock:
                self.closed_skipped += 1
            return RawHour(payload=b"", origin=CLOSED_ORIGIN,
                           availability=AVAILABILITY_EMPTY)

        with self._lock:
            self.requests += 1
            if not is_market_open(request.start):
                self.boundary_probes += 1

        key = ProbeKey(request.pair, request.date_str, request.hour)
        while True:
            raw, detail = self._attempt_series(key)
            if raw is not None:
                return raw
            if self.should_stop():
                with self._lock:
                    self.aborted.add(request.key)
                raise SessionStopped(request.label())
            if self._feed_is_answering():
                raise FeedError(f"{request.label()}: {detail}")
            self._ride_out_outage(request)

    def _attempt_series(self, key: ProbeKey) -> tuple[RawHour | None, str]:
        """One full retry series for one hour. ``(None, why)`` when exhausted."""
        detail = "no attempt was made"
        for _attempt in range(self.max_attempts):
            if self.should_stop():
                return None, "session stopping"
            self._pacer.wait()
            connection = self._acquire()
            try:
                status, body = connection.get(key)
            except Exception as exc:  # noqa: BLE001 - every transport fault retries
                detail = f"{type(exc).__name__}: {exc}"
                self._note_failure(detail)
                self._pacer.penalise()
                continue
            finally:
                self._release(connection)
                with self._lock:
                    self.attempts_spent += 1

            if status == 200:
                self._succeeded()
                availability = (AVAILABILITY_PRESENT if body
                                else AVAILABILITY_EMPTY)
                return RawHour(payload=body,
                               origin=self._origin(connection, key),
                               availability=availability,
                               http_status=200), ""
            if status == 404:
                self._succeeded()
                return RawHour(payload=b"",
                               origin=self._origin(connection, key),
                               availability=AVAILABILITY_MISSING,
                               http_status=404), ""
            if status == 403:
                raise FeedUnreachable(
                    f"{key.label()}: the datafeed returned 403. Phase 1 "
                    "established that the front end rejects VPN and datacenter "
                    "egress addresses outright while www.dukascopy.com keeps "
                    "working; check the public egress IP before suspecting a "
                    "routing fault.")
            detail = (f"HTTP {status} after {len(body)} bytes"
                      if status in RETRYABLE_STATUS else f"HTTP {status}")
            self._note_failure(detail)
            self._pacer.penalise()

        with self._lock:
            self._exhausted_streak += 1
            self.hours_exhausted += 1
        detail = f"{self.max_attempts} attempts exhausted; last: {detail}"
        _LOG.warning("%s: %s", key.label(), detail)
        return None, detail

    def _note_failure(self, detail: str) -> None:
        """Count a failed attempt and say so occasionally.

        A feed that has gone slow and a client that has hung look identical from
        outside. This is the difference, at one line per fifty failures.
        """
        with self._lock:
            self.failed_attempts += 1
            count = self.failed_attempts
        if count % FAILURE_LOG_EVERY == 0:
            _LOG.warning("%d failed attempt(s) so far; last: %s "
                         "(gap %.2fs, level %d)", count, detail,
                         self._pacer.gap, self.level)

    @staticmethod
    def _origin(connection: Connection, key: ProbeKey) -> str:
        """The URL an hour came from, for the manifest's provenance field."""
        return f"https://{connection.host}{connection.path_for(key)}"

    def _succeeded(self) -> None:
        """Record an answer: it ends a burst, a streak and any outage."""
        self._pacer.reward()
        with self._lock:
            self._last_success = time.monotonic()
            self._exhausted_streak = 0
            if self._outage_started is not None:
                self.outages_ridden_out += 1
                _LOG.info("feed answering again after %.0fs",
                          time.monotonic() - self._outage_started)
            self._outage_started = None
            self._outage_round = 0

    def _feed_is_answering(self) -> bool:
        """Whether this hour's failure is about this hour or about the feed."""
        with self._lock:
            quiet = time.monotonic() - self._last_success
            return (self._exhausted_streak < DEAD_FEED_STREAK
                    and quiet < HEALTHY_WINDOW_SECONDS)

    def _ride_out_outage(self, request: HourRequest) -> None:
        """Park on an escalating schedule, then let the caller ask again.

        Raises:
            FeedUnreachable: Once the outage has run past ``outage_budget``.
        """
        now = time.monotonic()
        with self._lock:
            if self._outage_started is None:
                self._outage_started = now
                _LOG.warning("feed stopped answering at %s; parking",
                             request.label())
            started = self._outage_started
            self._outage_round += 1
            park = self.parks[min(self._outage_round - 1,
                                  len(self.parks) - 1)]
        if now - started > self.outage_budget:
            raise FeedUnreachable(
                f"the datafeed has answered nothing for {now - started:.0f}s, "
                f"past the {self.outage_budget:.0f}s budget. CLAUDE.md calls an "
                "unreachable external dependency a stop-and-report, not "
                "something to work around.")
        _LOG.warning("parking %.0fs (outage %.0fs so far)", park, now - started)
        deadline = time.monotonic() + park
        while time.monotonic() < deadline:
            if self.should_stop():
                return
            time.sleep(min(2.0, deadline - time.monotonic()))


def _reaches_the_feed(request: HourRequest) -> bool:
    """Whether an hour is asked of the network at all.

    Open hours always are. Shut ones do not, with one deliberate exception: the
    hour either side of a week boundary. The FX week tracks 17:00
    ``America/New_York`` and therefore moves between 21:00 and 22:00 UTC with
    daylight saving, and a boundary derived wrongly is the kind of fault that
    fails silently for half of every year. Asking for the shut hour next to each
    boundary costs about 1.7% more requests and turns the derivation into
    something the stored manifest checks every week.
    """
    start = request.start
    if is_market_open(start):
        return True
    hour = dt.timedelta(hours=1)
    return is_market_open(start - hour) or is_market_open(start + hour)


def _host_of(base_url: str) -> str:
    """The host part of the datafeed base URL."""
    import urllib.parse

    return urllib.parse.urlsplit(base_url).netloc


# --------------------------------------------------------------------------- #
# Concurrency calibration
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class Calibrator:
    """Steps the offered rate up while the feed stays clean, and back on protest.

    The rule, fixed before the run and recorded in the report:

    * start at level 2, which is what T1 sustained for ten hours;
    * a window is *clean* while its throttled share stays at or below
      :data:`CLEAN_THROTTLE_RATE`;
    * after :data:`CLEAN_BEFORE_STEP_UP` seconds of unbroken clean windows, step
      up one level, to a ceiling of 4 -- the card's cap;
    * a stepped-up level is judged against the measured rate of the level below
      it. Two consecutive windows above ``1.5x`` that rate, or two above it by
      more than two percentage points, and the level steps back down and is not
      probed again for six hours.

    "Sustained" means the same thing in both directions: an isolated bad window
    neither backs the level off nor resets the clean clock. The feed was measured
    to flap on a one-minute cycle, so a single burst is noise, not evidence.

    Every transition is recorded with the rates that caused it, so the report
    states what was measured rather than what was hoped for.
    """

    level: int = MIN_LEVEL
    ceiling: int = MAX_LEVEL
    window_seconds: float = EVAL_WINDOW_SECONDS
    clock: Callable[[], float] = time.monotonic

    baselines: dict[int, float] = dataclasses.field(default_factory=dict)
    history: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    windows: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    _window_started: float = dataclasses.field(default=0.0)
    _clean_since: float = dataclasses.field(default=0.0)
    _bad_windows: int = dataclasses.field(default=0)
    _blocked_until: dict[int, float] = dataclasses.field(default_factory=dict)
    _requests: int = dataclasses.field(default=0)
    _throttles: int = dataclasses.field(default=0)

    def __post_init__(self) -> None:
        self._window_started = self.clock()
        self._clean_since = self.clock()
        why = ("start; T1's proven-safe level" if self.level == MIN_LEVEL
               else f"resumed at level {self.level}, earned earlier in the run")
        if self.baselines:
            why += (f"; baselines carried forward "
                    f"{ {k: round(v, 4) for k, v in sorted(self.baselines.items())} }")
        self.history.append({"at": 0.0, "level": self.level, "why": why})

    def observe(self, requests: int, throttles: int) -> int:
        """Fold in one chunk's counters and return the level to run next.

        Args:
            requests: Requests issued since the last call.
            throttles: Throttled responses since the last call.

        Returns:
            The concurrency level the next chunk should use.
        """
        self._requests += max(0, requests)
        self._throttles += max(0, throttles)
        now = self.clock()
        if now - self._window_started < self.window_seconds or not self._requests:
            return self.level

        rate = self._throttles / self._requests
        self.windows.append({"level": self.level, "requests": self._requests,
                             "throttles": self._throttles,
                             "rate": round(rate, 5),
                             "seconds": round(now - self._window_started, 1)})
        self._log_window(now, rate)
        self._close_window(now, rate)
        self._requests = 0
        self._throttles = 0
        self._window_started = now
        return self.level

    def _log_window(self, now: float, rate: float) -> None:
        """Say what each window measured and what it is being judged against.

        Without this the calibration is only visible in the session record,
        which exists once the session ends -- so a multi-day run gives no way to
        tell "the level is not stepping up because the feed is complaining" from
        "the level is not stepping up because something is stuck".
        """
        tolerance = self.tolerance_at(self.level)
        _LOG.info("calibration window: level %d, %.3f%% throttled against a "
                  "%.3f%% tolerance, clean for %.0f min of the %.0f needed",
                  self.level, rate * 100, tolerance * 100,
                  (now - self._clean_since) / 60, CLEAN_BEFORE_STEP_UP / 60)

    def tolerance_at(self, level: int) -> float:
        """The throttle rate a window at ``level`` must stay at or below.

        Two tests, and a window must pass both:

        * **comparative** -- no worse than 1.5x the measured rate of the level
          below, or two percentage points above it. This is the one that
          matters, because it asks whether adding a connection made things
          worse, which is the actual question.
        * **absolute** -- no worse than :data:`CLEAN_THROTTLE_RATE` whatever the
          level below was doing. Without this a high baseline licenses a higher
          one: measured on 2026-08-21, a level-3 window at 8.9% throttled passed
          as clean on the absolute cap alone and stepped the run up to four
          connections while one request in eleven was being refused. A feed
          complaining that much is not one to offer more to, however the level
          below happened to be behaving.
        """
        below = self.baselines.get(level - 1)
        if level <= MIN_LEVEL or below is None:
            return CLEAN_THROTTLE_RATE
        comparative = max(below * STEP_UP_RATE_FACTOR,
                          below + STEP_UP_RATE_MARGIN)
        return min(CLEAN_THROTTLE_RATE, comparative)

    def _close_window(self, now: float, rate: float) -> None:
        """Decide what one finished window means for the level."""
        tolerance = self.tolerance_at(self.level)

        if rate > tolerance:
            self._bad_windows += 1
            # An isolated burst breaks neither the clean streak nor the level:
            # "sustained" means the same thing in both directions.
            if self._bad_windows >= BAD_WINDOWS_BEFORE_BACKOFF:
                self._clean_since = now
                self._back_off(now, rate, tolerance)
            return

        self._bad_windows = 0
        seen = self.baselines.get(self.level)
        self.baselines[self.level] = rate if seen is None else (seen + rate) / 2.0
        if (self.level < self.ceiling
                and now - self._clean_since >= CLEAN_BEFORE_STEP_UP
                and now >= self._blocked_until.get(self.level + 1, 0.0)):
            self._step_up(now, rate)

    def _step_up(self, now: float, rate: float) -> None:
        """Raise the level after a sustained clean stretch."""
        self.level += 1
        self._clean_since = now
        self._bad_windows = 0
        self.history.append({
            "at": round(now, 1), "level": self.level,
            "why": (f"clean for {CLEAN_BEFORE_STEP_UP:.0f}s at level "
                    f"{self.level - 1} (throttle rate {rate:.3%})")})
        _LOG.info("calibration: stepping up to level %d after a clean hour "
                  "(throttle rate %.3f%%)", self.level, rate * 100)

    def _back_off(self, now: float, rate: float, tolerance: float) -> None:
        """Return to the last safe level and stop probing this one for a while.

        At the floor there is nowhere to back off to, so what gets blocked is
        the level *above*: a feed complaining at level 2 is not one to offer a
        third connection to. Blocking level 2 there instead -- which the first
        version did -- blocked nothing, because the step-up test only ever asks
        about the level above the current one.
        """
        blocked = self.level if self.level > MIN_LEVEL else self.level + 1
        self.level = max(MIN_LEVEL, self.level - 1)
        self._blocked_until[blocked] = now + REPROBE_AFTER_BACKOFF
        self._clean_since = now
        self._bad_windows = 0
        self.history.append({
            "at": round(now, 1), "level": self.level,
            "why": (f"level {blocked} blocked: {rate:.3%} throttled against a "
                    f"tolerance of {tolerance:.3%} for "
                    f"{BAD_WINDOWS_BEFORE_BACKOFF} consecutive windows")})
        _LOG.warning("calibration: backing off to level %d; level %d ran "
                     "%.3f%% throttled", self.level, blocked, rate * 100)

    def to_dict(self) -> dict[str, Any]:
        """The calibration record for the report."""
        return {
            "final_level": self.level,
            "baselines": {str(k): round(v, 5) for k, v in sorted(self.baselines.items())},
            "transitions": self.history,
            "windows": self.windows,
        }


# --------------------------------------------------------------------------- #
# Session bookkeeping
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class SessionStats:
    """Everything one driver session did, for the ledger and the report."""

    started: float = dataclasses.field(default_factory=time.monotonic)
    chunks_done: int = 0
    chunks_skipped: int = 0
    hours_requested: int = 0
    hours_ok: int = 0
    hours_empty: int = 0
    hours_gap: int = 0
    hours_aborted: int = 0
    ticks: int = 0
    duplicates: int = 0
    bar_seconds: float = 0.0
    bar_dates: int = 0
    ingest_seconds: float = 0.0

    def to_dict(self, feed: HourFeed | None = None) -> dict[str, Any]:
        """Flat, JSON-safe summary."""
        elapsed = max(1e-9, time.monotonic() - self.started)
        out: dict[str, Any] = {
            "chunks_done": self.chunks_done,
            "chunks_skipped": self.chunks_skipped,
            "hours_requested": self.hours_requested,
            "hours_ok": self.hours_ok,
            "hours_empty": self.hours_empty,
            "hours_gap": self.hours_gap,
            "hours_aborted": self.hours_aborted,
            "ticks": self.ticks,
            "duplicates_dropped": self.duplicates,
            "bar_seconds": round(self.bar_seconds, 1),
            "bar_dates_built": self.bar_dates,
            "seconds": round(elapsed, 1),
        }
        if feed is not None:
            out.update({
                "requests": feed.requests,
                "attempts": feed.attempts_spent,
                "closed_not_fetched": feed.closed_skipped,
                "boundary_probes": feed.boundary_probes,
                "throttles": feed.throttles,
                "seconds_parked": round(feed.parked_seconds, 1),
                "outages_ridden_out": feed.outages_ridden_out,
                "connections_opened": feed.connections_opened,
                "failed_attempts": feed.failed_attempts,
                "hours_exhausted": feed.hours_exhausted,
                "endpoint_rotations": feed.pool.rotations,
                "level": feed.level,
                "requests_per_second": round(feed.requests / elapsed, 3),
            })
        return out


class ChunkLog:
    """Append-only, flushed-per-record progress file.

    Flushing every line is the point: this is what a cold restart reads to know
    which pair-months it can skip without opening a thousand manifests.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> None:
        """Append one record and flush it."""
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        """Close the file."""
        with contextlib.suppress(OSError):
            self._handle.close()


def read_chunk_log(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    """The last record for each chunk key, tolerating a truncated final line."""
    out: dict[str, dict[str, Any]] = {}
    if not pathlib.Path(path).exists():
        return out
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("chunk"):
            out[str(record["chunk"])] = record
    return out


# --------------------------------------------------------------------------- #
# Store layout
# --------------------------------------------------------------------------- #

def manifest_dir_for(out_dir: pathlib.Path, chunk: Chunk) -> pathlib.Path:
    """Where one pair-month's manifest shard lives."""
    return (pathlib.Path(out_dir) / MANIFEST_DIRNAME / f"pair={chunk.pair}"
            / chunk.month_label)


def strip_aborted(directory: pathlib.Path,
                  aborted: set[tuple[str, str, int]]) -> int:
    """Remove hours nobody finished asking about from a manifest shard.

    A fetch that raises because the session is stopping is indistinguishable, to
    the pipeline, from one that failed -- so it is recorded as a gap. It is not
    one. Removing the record leaves the hour unsettled, which is exactly what
    makes the next session ask again.

    Returns:
        How many records were removed.
    """
    if not aborted:
        return 0
    manifest = load_manifest(directory)
    before = len(manifest.hours)
    manifest.hours = [rec for rec in manifest.hours if rec.key not in aborted]
    removed = before - len(manifest.hours)

    def kept(entry: dict[str, Any]) -> bool:
        key = (str(entry.get("pair")), str(entry.get("date")),
               entry.get("hour"))
        return key not in aborted

    manifest.errors = [e for e in manifest.errors if kept(e)]
    manifest.warnings = [w for w in manifest.warnings if kept(w)]
    if removed:
        write_manifest(directory, manifest)
        _LOG.info("%d unfinished hour(s) left unsettled in %s",
                  removed, directory)
    return removed


# --------------------------------------------------------------------------- #
# The driver
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True, slots=True)
class Params:
    """The bulk-ingest half of an experiment config."""

    pairs: tuple[str, ...]
    start: dt.date
    end: dt.date
    out_dir: pathlib.Path
    experiment_dir: pathlib.Path
    timeframes: tuple[str, ...]
    max_attempts: int
    timeout: float
    read_timeout: float
    outage_budget: float
    build_bars: bool


def load_params(config_path: pathlib.Path, base: pathlib.Path) -> Params:
    """Read the ``[experiment.params]`` table this driver needs.

    Raises:
        ValueError: If a required key is missing or a date is sealed.
    """
    document = tomllib.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    block = (document.get("experiment") or {}).get("params") or {}
    for key in ("pairs", "start_date", "end_date", "out_dir", "experiment_dir"):
        if key not in block:
            raise ValueError(f"{config_path}: [experiment.params] needs {key!r}")

    start = as_date(str(block["start_date"]))
    end = as_date(str(block["end_date"]))
    if end < start:
        raise ValueError(f"{config_path}: end_date precedes start_date")
    # Enforced here as well as by the gate: the driver must not be able to ask
    # the feed for a sealed hour even if a config slipped past review.
    assert_not_sealed(start, "bulk ingest start_date")
    assert_not_sealed(end, "bulk ingest end_date")

    return Params(
        pairs=tuple(str(p) for p in block["pairs"]),
        start=start, end=end,
        out_dir=_under(base, str(block["out_dir"])),
        experiment_dir=_under(base, str(block["experiment_dir"])),
        timeframes=tuple(str(t) for t in block.get("timeframes", TIMEFRAMES)),
        max_attempts=int(block.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
        timeout=float(block.get("timeout_seconds", 20.0)),
        read_timeout=float(block.get("read_timeout_seconds",
                                     DEFAULT_READ_TIMEOUT)),
        outage_budget=float(block.get("outage_budget_seconds",
                                      DEFAULT_OUTAGE_BUDGET)),
        build_bars=bool(block.get("build_bars", True)),
    )


def _under(base: pathlib.Path, value: str) -> pathlib.Path:
    """Resolve a project-relative path against the project root."""
    path = pathlib.Path(value)
    return path if path.is_absolute() else (base / path)


class Driver:
    """Works the plan, one pair-month at a time."""

    def __init__(self, params: Params, *, base: pathlib.Path,
                 level: int = MIN_LEVEL, retry_gaps: bool = False,
                 deadline: float | None = None,
                 stop_file: pathlib.Path | None = None,
                 baselines: dict[int, float] | None = None) -> None:
        self.params = params
        self.base = base
        self.retry_gaps = retry_gaps
        self.deadline = deadline
        self.stop_file = stop_file
        self._stopping = threading.Event()

        self.calibrator = Calibrator(level=level,
                                     baselines=dict(baselines or {}))
        self.feed = HourFeed(
            DukascopyConfig(max_concurrency=MAX_LEVEL, timeout=params.timeout),
            level=level, max_attempts=params.max_attempts,
            outage_budget=params.outage_budget,
            read_timeout=params.read_timeout, should_stop=self.should_stop)
        self.log = ChunkLog(params.experiment_dir / CHUNKS_NAME)
        self.stats = SessionStats()
        self.gap_chunks: set[str] = set()

    # -- stopping -----------------------------------------------------------

    def request_stop(self, why: str = "") -> None:
        """Ask the session to wind down cleanly."""
        if not self._stopping.is_set():
            _LOG.warning("stopping: %s", why or "requested")
        self._stopping.set()

    def should_stop(self) -> bool:
        """Whether the session is winding down."""
        if self._stopping.is_set():
            return True
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.request_stop("wall-clock budget spent")
            return True
        if self.stop_file is not None and self.stop_file.exists():
            self.request_stop(f"stop file {self.stop_file} present")
            return True
        return False

    # -- work ---------------------------------------------------------------

    def run(self, chunks: Sequence[Chunk]) -> SessionStats:
        """Work the plan until it is done or the session stops."""
        done = read_chunk_log(self.log.path)
        for chunk in chunks:
            if self.should_stop():
                break
            previous = done.get(chunk.key)
            if self._can_skip(previous):
                self.stats.chunks_skipped += 1
                if int((previous or {}).get("hours_gap", 0)):
                    self.gap_chunks.add(chunk.key)
                continue
            self.run_chunk(chunk)
        return self.stats

    def _can_skip(self, previous: dict[str, Any] | None) -> bool:
        """Whether a chunk recorded as complete can be passed over."""
        if previous is None or not previous.get("complete"):
            return False
        if self.retry_gaps and int(previous.get("hours_gap", 0)):
            return False
        return True

    def run_chunk(self, chunk: Chunk) -> dict[str, Any]:
        """Ingest one pair-month, build its bars, and record what happened."""
        self.feed.set_level(self.calibrator.level)
        directory = manifest_dir_for(self.params.out_dir, chunk)
        requests_before = self.feed.requests
        throttles_before = self.feed.throttles
        self.feed.aborted = set()

        config = IngestConfig(
            mode="live", out_dir=self.params.out_dir, hours=chunk.hours(),
            manifest_dir=directory, resume=True, fail_on_gap=False,
            checkpoint_every=CHECKPOINT_EVERY, bar_timeframes=(),
            dukascopy=dataclasses.replace(self.feed.config,
                                          max_concurrency=self.feed.level))

        clock = time.perf_counter()
        report = ingest(config, source=self.feed)
        ingest_seconds = time.perf_counter() - clock
        aborted = set(self.feed.aborted)
        removed = strip_aborted(directory, aborted)

        bars: list[dict[str, Any]] = []
        bar_seconds = 0.0
        if self.params.build_bars and report.hours_ok:
            for update in build_bars_incremental(
                    self.params.out_dir, chunk.pair, self.params.timeframes,
                    dates=chunk.dates()):
                bars.append({"timeframe": update.timeframe,
                             "dates": update.dates_built,
                             "rows": update.rows_written,
                             "total": update.rows_total,
                             "seconds": round(update.seconds, 2)})
                bar_seconds += update.seconds
                self.stats.bar_dates += update.dates_built

        issued = self.feed.requests - requests_before
        record = {
            "chunk": chunk.key,
            "pair": chunk.pair,
            "month": chunk.month_label,
            "complete": not aborted,
            "hours_requested": report.hours_requested,
            "hours_ok": report.hours_ok,
            "hours_empty": report.hours_empty,
            "hours_gap": report.hours_gap,
            "hours_skipped": report.hours_skipped,
            "hours_unsettled": removed,
            "ticks": report.ticks_written,
            "duplicates_dropped": report.duplicates_dropped,
            "requests": issued,
            "throttles": self.feed.throttles - throttles_before,
            "level": self.feed.level,
            "ingest_seconds": round(ingest_seconds, 1),
            "bar_seconds": round(bar_seconds, 2),
            "bars": bars,
            "errors": len(report.manifest.errors),
            "at": ledger_mod.now_iso(),
        }
        self.log.write(record)

        self.stats.chunks_done += 1
        self.stats.hours_requested += report.hours_requested
        self.stats.hours_ok += report.hours_ok
        self.stats.hours_empty += report.hours_empty
        self.stats.hours_gap += report.hours_gap
        self.stats.hours_aborted += removed
        self.stats.ticks += report.ticks_written
        self.stats.duplicates += report.duplicates_dropped
        self.stats.bar_seconds += bar_seconds
        self.stats.ingest_seconds += ingest_seconds
        if report.hours_gap:
            self.gap_chunks.add(chunk.key)

        self.calibrator.observe(issued, record["throttles"])
        _LOG.info("%s: %s", chunk.key, report.summary_line())
        return record

    def close(self) -> None:
        """Release the connections and close the progress file."""
        self.feed.close()
        self.log.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def project_root() -> pathlib.Path:
    """Repository root, derived from this file."""
    return pathlib.Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.bulk_ingest",
        description="Bulk-ingest the research window, newest month first.")
    parser.add_argument("--config", required=True, type=pathlib.Path,
                        help="the T2a experiment config")
    parser.add_argument("--max-seconds", type=float, default=None,
                        help="wall-clock budget for this session")
    parser.add_argument("--max-chunks", type=int, default=None,
                        help="stop after this many pair-months")
    parser.add_argument("--pairs", type=str, default=None,
                        help="comma-separated subset of the configured pairs")
    parser.add_argument("--level", type=int, default=None,
                        help=("starting concurrency level "
                              f"({MIN_LEVEL}..{MAX_LEVEL}); defaults to the "
                              "level the run last earned, or "
                              f"{MIN_LEVEL} on a fresh run"))
    parser.add_argument("--retry-gaps", action="store_true",
                        help="re-work pair-months that recorded a gap")
    parser.add_argument("--stop-file", type=pathlib.Path, default=None,
                        help="stop cleanly once this file exists")
    parser.add_argument("--no-bars", action="store_true",
                        help="store ticks only; leave the bar tables alone")
    parser.add_argument("--verbose", action="store_true",
                        help="log every ingested hour, not just every chunk")
    parser.add_argument("--base", type=pathlib.Path, default=None,
                        help="project root; derived when omitted")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one bulk-ingest session and return a process exit code."""
    args = parse_args(argv)
    configure_logging()
    if not args.verbose:
        # Three quarters of a million INFO lines is not a log, it is a second
        # copy of the manifest. Chunk-level progress stays; per-hour does not.
        logging.getLogger("fxlab.ingestion.pipeline").setLevel(logging.WARNING)
    base = pathlib.Path(args.base).resolve() if args.base else project_root()

    try:
        params = load_params(args.config, base)
    except Exception as exc:  # noqa: BLE001 - a usage error, reported plainly
        print(f"BAD_BULK_CONFIG: {exc}", file=sys.stderr)
        return 2
    if args.no_bars:
        params = dataclasses.replace(params, build_bars=False)

    pairs = params.pairs
    if args.pairs:
        wanted = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
        unknown = [p for p in wanted if p not in params.pairs]
        if unknown:
            print(f"BAD_BULK_CONFIG: {unknown} are not in the configured "
                  f"universe {list(params.pairs)}", file=sys.stderr)
            return 2
        pairs = tuple(p for p in params.pairs if p in wanted)

    chunks = plan_chunks(pairs, params.start, params.end)
    if args.max_chunks is not None:
        chunks = chunks[:max(0, args.max_chunks)]

    deadline = (time.monotonic() + args.max_seconds
                if args.max_seconds else None)
    earned, baselines = resume_calibration(params.experiment_dir / SESSIONS_NAME)
    level = args.level if args.level is not None else (earned or MIN_LEVEL)
    if args.level is None and earned:
        _LOG.info("resuming at level %d with baselines %s, earned earlier in "
                  "the run", level, {k: round(v, 4) for k, v in
                                     sorted(baselines.items())})
    driver = Driver(params, base=base, level=level, baselines=baselines,
                    retry_gaps=args.retry_gaps, deadline=deadline,
                    stop_file=args.stop_file)

    def on_signal(_signum: int, _frame: Any) -> None:
        driver.request_stop("interrupt received")

    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, on_signal)
    with contextlib.suppress(AttributeError, ValueError):
        signal.signal(signal.SIGTERM, on_signal)

    experiment_id = str(_experiment_id(args.config)) + "-run"
    note = (f"bulk ingest session, {len(chunks)} pair-month(s) planned, "
            f"{params.start.isoformat()}..{params.end.isoformat()}, "
            f"level {level}; resumable, checkpointed per "
            f"{CHECKPOINT_EVERY} hours")
    ledger_mod.append_start(
        base, experiment_id=experiment_id, taskcard=_taskcard(args.config),
        config_sha256=_config_sha(args.config), seed=_seed(args.config),
        mode="scoring", rerun_class=ledger_mod.RERUN_FULL, subset_sha256=None,
        note=note)

    status = "ok"
    exit_code = 0
    try:
        driver.run(chunks)
    except FeedUnreachable as exc:
        _LOG.error("%s", exc)
        print("FEED_UNREACHABLE", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        status = "failed:FEED_UNREACHABLE"
        exit_code = EXIT_FEED_UNREACHABLE
    except KeyboardInterrupt:
        status = "stopped"
        driver.request_stop("KeyboardInterrupt")
    finally:
        summary = driver.stats.to_dict(driver.feed)
        summary["calibration"] = driver.calibrator.to_dict()
        summary["status"] = status
        summary["ended_at"] = ledger_mod.now_iso()
        summary["chunks_planned"] = len(chunks)
        _append_session(params.experiment_dir / SESSIONS_NAME, summary)
        driver.close()
        ledger_mod.append_end(
            base, experiment_id=experiment_id,
            status=f"{status} {json.dumps(summary, sort_keys=True)}",
            result_files=[], result_hash=None, scored=False)
        _LOG.info("session complete: %s",
                  json.dumps({k: v for k, v in summary.items()
                              if k != "calibration"}, sort_keys=True))
    return exit_code


def resume_calibration(path: pathlib.Path) -> tuple[int | None, dict[int, float]]:
    """The level and baselines the run had earned, from the last session record.

    The calibration is a property of the **run**, not of the session. Starting
    every session from level 2 would spend an hour of every restart re-earning
    what the run already measured; starting at the earned level but discarding
    the baselines is worse, because the level above it then falls back to the
    absolute cap and can be stepped into while the feed is visibly complaining.
    Measured on 2026-08-21: a session resumed that way stepped to four
    connections off a window running 8.9% throttled.

    Returns:
        ``(level, baselines)``; ``(None, {})`` when there is nothing to resume.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return None, {}
    level: int | None = None
    baselines: dict[int, float] = {}
    # Every session, oldest first, so the newest measurement of each level wins
    # and a level measured only on the first day is still carried. Reading the
    # last record alone loses exactly the baseline the level above it needs.
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        calibration = record.get("calibration") or {}
        if not calibration:
            continue
        if calibration.get("final_level"):
            level = int(calibration["final_level"])
        for key, value in (calibration.get("baselines") or {}).items():
            try:
                baselines[int(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return level, baselines


def _append_session(path: pathlib.Path, summary: dict[str, Any]) -> None:
    """Append one session record, flushed, next to the progress file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(summary, sort_keys=True,
                                separators=(",", ":")) + "\n")
        handle.flush()


def _experiment_id(config_path: pathlib.Path) -> str:
    """The experiment id declared in a config."""
    document = tomllib.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    return str((document.get("experiment") or {}).get("id", "bulk-ingest"))


def _taskcard(config_path: pathlib.Path) -> str:
    """The task card declared in a config."""
    document = tomllib.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    return str((document.get("experiment") or {}).get("taskcard", ""))


def _seed(config_path: pathlib.Path) -> int:
    """The seed declared in a config."""
    document = tomllib.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    return int((document.get("experiment") or {}).get("seed", 0))


def _config_sha(config_path: pathlib.Path) -> str:
    """sha256 of a config file's exact bytes."""
    import hashlib

    return hashlib.sha256(pathlib.Path(config_path).read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
