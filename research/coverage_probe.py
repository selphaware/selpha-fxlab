"""Coverage probing for task card T1: ask the feed what it has, store nothing.

A probe is one HTTP GET of one hourly ``.bi5`` file, classified and thrown
away. Nothing is written to ``data/``; the only artefacts are the probe records
under ``experiments/T1-coverage/``. That is the whole point of the card: to
learn where Dukascopy coverage starts and where it is holed *before* committing
days of wall clock to bulk ingestion, without ingesting anything.

Four classifications, matching the Phase 1 feed semantics exactly (SPEC.md):

``data``
    HTTP 200, a non-empty body that decodes as LZMA1 alone-format bi5 into a
    whole number of 20-byte records, and at least one record.
``empty``
    HTTP 200 with a zero-byte body. The feed's way of saying the market was
    closed. It is not a gap and it is not an error.
``missing``
    HTTP 404. The hour is genuinely absent from the feed.
``error``
    Every attempt failed, or the body would not decode. Recorded, never
    silently skipped -- a probe that fell over is not evidence of absence.

Network discipline
------------------

Two connections, never more (the card, and HANDOFF.md §3: four in flight
provoked sustained 503s within about two minutes; two completed). Both workers
share one :class:`Pacer`, so the *global* request rate is what backs off. A
per-worker backoff cannot do this: two workers each backing off independently
still present the feed with twice the rate either of them thinks it is using.

Two costs were measured before any of this was tuned, and both were surprising
enough to be worth writing down:

* a GET down a **warm** connection costs 0.09-0.42s, but **opening** one costs
  3-12s and times out outright often enough to matter. Connection setup, not
  transfer, is the expensive operation, so connections are held open and the
  first version's recycle-every-40-requests was removed;
* the 503 is not a rate limit in any smooth sense. It says "No server is
  available to handle this request", arrives in 25ms, and arrives for *every*
  request for tens of seconds at a time -- interleaved with stretches where a
  request every 0.8s succeeds indefinitely. So the response to it is a global
  pause, not a permanently slower rate.

Resumability
------------

Every completed probe is appended to ``probes.jsonl`` immediately and the file
is flushed. A restart reads what is already there and probes only what is
missing, so a session that dies after eleven hours costs eleven minutes, not
eleven hours. The card budgets hours of wall clock; it does not budget doing
them twice.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import http.client
import json
import logging
import pathlib
import queue
import ssl
import threading
import time
import urllib.parse
from typing import Any, Callable, Final, Iterable, Sequence

from fxlab.config import DukascopyConfig
from fxlab.ingestion.bi5 import Bi5DecodeError, decode_bi5
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import is_market_open
from fxlab.ingestion.validation import (deduplicate, spread_ceiling_pips,
                                        spread_stats, validate)
from fxlab.logging_setup import configure_logging

_LOG: Final[logging.Logger] = logging.getLogger("research.coverage_probe")

#: Probe classifications. These strings are the card's vocabulary and are what
#: the analysis in :mod:`research.coverage` counts, so they are constants.
PROBE_DATA: Final[str] = "data"
PROBE_EMPTY: Final[str] = "empty"
PROBE_MISSING: Final[str] = "missing"
PROBE_ERROR: Final[str] = "error"
PROBE_KINDS: Final[tuple[str, ...]] = (PROBE_DATA, PROBE_EMPTY, PROBE_MISSING,
                                       PROBE_ERROR)

#: Probe record files, relative to the experiment directory. Deliberately not
#: ``*.json``: the research gate treats every ``*.json`` under ``experiments/``
#: as an experiment result, and probe checkpoints are evidence, not results.
PROBES_NAME: Final[str] = "probes.jsonl"
QUALITY_NAME: Final[str] = "quality.jsonl"
PROBES_PARQUET: Final[str] = "probes.parquet"

#: HTTP statuses worth retrying, as Phase 1 classifies them.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset(
    {408, 425, 429, 500, 502, 503, 504})

#: The card's cap. Not a tunable: raising it is a card breach, not an
#: optimisation.
MAX_WORKERS: Final[int] = 2

#: Requests served down one connection before it is recycled.
#:
#: Deliberately large, and measured rather than guessed. On this machine a GET
#: down a *warm* connection to the datafeed costs 0.09-0.42s, while opening a
#: new one costs 3-12s and fails outright often enough to matter. Recycling
#: every 40 requests -- the first thing tried here -- made connection setup the
#: dominant cost of the whole survey. The connection is dropped and rebuilt on
#: any fault anyway, so this is a ceiling, not a schedule.
CONNECTION_LIFETIME: Final[int] = 1000

#: How long every worker is parked after the 1st, 2nd, ... consecutive failure
#: within one burst; the last entry repeats.
#:
#: Zero for the first, because an isolated failure is answered by retrying, not
#: by waiting. Capped at 30s because the feed's availability was measured to
#: flap on roughly a one-minute cycle -- sampled once a minute over six
#: minutes it went 503 / good / good / timeouts / half / 503 -- so a park
#: longer than the cycle sleeps through the recovery it is waiting for.
COOLDOWNS: Final[tuple[float, ...]] = (0.0, 0.5, 2.0, 5.0, 10.0, 20.0, 30.0)

#: Consecutive exhausted probes after which a session gives up. An external
#: dependency that is down is a stop-and-report condition (CLAUDE.md
#: §Autonomy), not something to grind against for six hours. With the cooldown
#: schedule above this is roughly half an hour of a total outage.
DEAD_FEED_STREAK: Final[int] = 12

#: Exit code meaning "the feed is unreachable", distinct from a usage error.
EXIT_FEED_UNREACHABLE: Final[int] = 4


class FeedUnreachable(RuntimeError):
    """Raised when the datafeed stops answering for long enough to give up."""


class SessionStopped(RuntimeError):
    """Raised inside a probe when the session's budget expired mid-retry.

    The probe is simply not recorded. Resumability makes that free: the next
    session finds it missing and asks again. Recording it as an ``error`` would
    be cheaper and wrong -- it would say the feed refused when in fact nobody
    finished asking.
    """


# --------------------------------------------------------------------------- #
# Probe records
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True, slots=True)
class ProbeKey:
    """The identity of one probe: which pair, which UTC date, which hour."""

    pair: str
    date: str
    hour: int

    def as_tuple(self) -> tuple[str, str, int]:
        """Hashable identity, used to skip probes already on disk."""
        return (self.pair, self.date, self.hour)

    @property
    def day(self) -> dt.date:
        """The UTC date as a :class:`datetime.date`."""
        return dt.date.fromisoformat(self.date)

    @property
    def hour_start(self) -> dt.datetime:
        """The UTC instant this hour opens."""
        return dt.datetime(self.day.year, self.day.month, self.day.day,
                           self.hour, tzinfo=dt.timezone.utc)

    def label(self) -> str:
        """Human-readable identity for log lines."""
        return f"{self.pair} {self.date}T{self.hour:02d}:00Z"


@dataclasses.dataclass(frozen=True, slots=True)
class ProbeRecord:
    """One classified probe.

    Attributes:
        key: What was asked for.
        kind: One of :data:`PROBE_KINDS`.
        status: The final HTTP status, when there was one.
        compressed_bytes: Size of the served body.
        ticks: Decoded record count, for ``data`` probes only.
        attempts: How many HTTP attempts it took.
        stage: Which sweep asked for it -- ``first``, ``refine`` or ``quality``.
        detail: Why an ``error`` is an error. Empty otherwise.
    """

    key: ProbeKey
    kind: str
    status: int | None
    compressed_bytes: int
    ticks: int
    attempts: int
    stage: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Compact JSON form. Sixty thousand of these live in one file."""
        record: dict[str, Any] = {
            "pair": self.key.pair, "date": self.key.date, "hour": self.key.hour,
            "kind": self.kind, "bytes": self.compressed_bytes,
            "ticks": self.ticks, "attempts": self.attempts, "stage": self.stage,
        }
        if self.status is not None:
            record["status"] = self.status
        if self.detail:
            record["detail"] = self.detail
        return record


def read_probes(path: pathlib.Path) -> list[dict[str, Any]]:
    """Read a probe checkpoint file, tolerating a truncated final line.

    A process killed mid-write leaves half a line. That is a checkpoint doing
    its job, not corruption, so the half line is dropped and everything before
    it is kept -- which is exactly what makes the file safe to resume from.
    """
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            _LOG.warning("dropping an unparseable probe line in %s", path.name)
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def probe_index(records: Iterable[dict[str, Any]]) -> set[tuple[str, str, int]]:
    """The set of probe identities already recorded."""
    return {(str(r.get("pair")), str(r.get("date")), int(r.get("hour", -1)))
            for r in records}


class ProbeWriter:
    """Append-only, flushed-per-record probe checkpoint.

    Flushing every record is deliberate. Buffering would make the file cheaper
    to write and useless for the one thing it exists for.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()
        self.written = 0

    def write(self, payload: dict[str, Any]) -> None:
        """Append one record and flush it to the operating system."""
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
            self.written += 1

    def close(self) -> None:
        """Close the underlying file."""
        with contextlib.suppress(OSError):
            self._handle.close()


# --------------------------------------------------------------------------- #
# Rate control
# --------------------------------------------------------------------------- #

class Pacer:
    """One global request-rate governor, shared by every worker.

    Two separate controls, because the feed fails in two separate ways and
    conflating them was measurably wrong.

    The **gap** is the steady-state spacing between requests. It creeps up
    gently when the front end complains and decays back down while it does not,
    so the sustainable rate is found rather than guessed.

    The **cooldown** handles the other failure. The datafeed's 503 page says
    "No server is available to handle this request" and arrives in 25ms, and it
    arrives for *everything* for tens of seconds at a stretch, interleaved with
    stretches where every request succeeds in 150ms. That is a backend outage,
    not a rate limit, and no amount of widening the gap shortens it. So a burst
    of failures parks every worker for an exponentially growing pause and the
    first success afterwards resets the burst, which costs a few seconds of
    waiting instead of turning the steady-state rate into a casualty of it.

    Both are global. Two workers each backing off independently still present
    the front end with twice the rate one of them thinks it is using.
    """

    def __init__(self, floor: float = 0.4, ceiling: float = 4.0,
                 factor: float = 1.25, decay: float = 0.98,
                 cooldowns: Sequence[float] = COOLDOWNS) -> None:
        self.floor = floor
        self.ceiling = ceiling
        self.factor = factor
        self.decay = decay
        self.cooldowns = tuple(cooldowns)
        self._gap = floor
        self._next = 0.0
        self._burst = 0
        self._lock = threading.Lock()
        self.throttles = 0
        self.parked = 0.0

    @property
    def gap(self) -> float:
        """The current inter-request gap in seconds."""
        return self._gap

    def wait(self) -> None:
        """Block until this worker's turn to issue a request."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._gap
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalise(self) -> float:
        """Widen the gap and park every worker. Returns the cooldown used.

        The first failure of a burst costs nothing but the ordinary gap. That
        is measured, not generous: an isolated 503 arrives in 25ms and the
        immediate retry usually succeeds, so pausing for it turned a quarter of
        the requests into five-second stalls and cost four fifths of the
        survey's throughput. Only a *second* consecutive failure is evidence of
        an outage, and from there the schedule escalates quickly.
        """
        with self._lock:
            self._burst += 1
            self._gap = min(self._gap * self.factor, self.ceiling)
            index = min(self._burst - 1, len(self.cooldowns) - 1)
            cooldown = self.cooldowns[index]
            self._next = max(self._next, time.monotonic() + cooldown)
            self.throttles += 1
            self.parked += cooldown
            return cooldown

    def reward(self) -> None:
        """Narrow the gap and end the burst after a success."""
        with self._lock:
            self._burst = 0
            self._gap = max(self.floor, self._gap * self.decay)


class Connection:
    """A long-lived keep-alive HTTPS connection to the datafeed host.

    Both timeouts are set by one measured fact: **reconnecting is the most
    expensive thing this client can do**. Fifteen consecutive connection
    attempts to this host produced five successes with a median of 9.3s and ten
    failures that each cost their full 20s timeout. So the read timeout is
    deliberately generous rather than tight -- a response that is merely slow
    must be waited for, because abandoning it forces a reconnect that costs
    more than the wait. A GET down a warm connection returns in 0.09-0.42s when
    the feed is healthy, so this is roughly forty times the healthy latency and
    exists only to bound a hung backend.
    """

    #: Read timeout, once the connection is up. Long on purpose; see above.
    read_timeout: Final[float] = 15.0

    def __init__(self, base_url: str, timeout: float, user_agent: str) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        self.host = parsed.netloc
        self.prefix = parsed.path.rstrip("/")
        self.timeout = timeout
        self.headers = {"User-Agent": user_agent, "Accept": "*/*"}
        self._conn: http.client.HTTPSConnection | None = None
        self._served = 0

    def path_for(self, key: ProbeKey) -> str:
        """The request path for one probe. The month is zero-based (SPEC.md)."""
        day = key.day
        return (f"{self.prefix}/{key.pair}/{day.year:04d}/{day.month - 1:02d}/"
                f"{day.day:02d}/{key.hour:02d}h_ticks.bi5")

    def get(self, key: ProbeKey) -> tuple[int, bytes]:
        """Issue one GET and return ``(status, body)``.

        Raises:
            OSError: For any transport fault. The connection is dropped first,
                so the caller only has to decide whether to retry.
        """
        try:
            if self._conn is None or self._served >= CONNECTION_LIFETIME:
                self.close()
                conn = http.client.HTTPSConnection(
                    self.host, 443, timeout=self.timeout,
                    context=ssl.create_default_context())
                conn.connect()
                if conn.sock is not None:
                    conn.sock.settimeout(self.read_timeout)
                self._conn = conn
                self._served = 0
            self._conn.request("GET", self.path_for(key), headers=self.headers)
            response = self._conn.getresponse()
            body = response.read()
            self._served += 1
            return int(response.status), body
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Drop the underlying connection, if any."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
        self._conn = None
        self._served = 0


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #

class Prober:
    """Classifies one probe at a time down one connection."""

    def __init__(self, pacer: Pacer, config: DukascopyConfig,
                 max_attempts: int,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self.pacer = pacer
        self.config = config
        self.max_attempts = max_attempts
        self.should_stop = should_stop or (lambda: False)
        self.connection = Connection(config.base_url, config.timeout,
                                     config.user_agent)

    def probe(self, key: ProbeKey, stage: str,
              keep_payload: bool = False) -> tuple[ProbeRecord, bytes | None]:
        """Fetch and classify one hour.

        Args:
            key: What to ask for.
            stage: Which sweep this belongs to.
            keep_payload: Return the raw body alongside the record, for the
                quality spot checks that decode it properly.

        Returns:
            ``(record, payload)``; ``payload`` is ``None`` unless asked for.

        Raises:
            FeedUnreachable: On HTTP 403, which the Phase 1 client identifies
                as the datafeed rejecting a VPN or datacenter egress address
                outright rather than as a routing fault.
        """
        last_detail = "no attempt was made"
        status: int | None = None
        for attempt in range(1, self.max_attempts + 1):
            # Checked before every attempt, not only between probes. A probe
            # caught in an outage can sit in cooldowns for minutes, and a
            # session whose wall-clock budget expires mid-retry should stop
            # then rather than overrun it by the length of a retry storm.
            if attempt > 1 and self.should_stop():
                raise SessionStopped(key.label())
            self.pacer.wait()
            try:
                status, body = self.connection.get(key)
            except Exception as exc:  # noqa: BLE001 - every transport fault retries
                last_detail = f"{type(exc).__name__}: {exc}"
                self.pacer.penalise()
                continue

            if status == 404:
                self.pacer.reward()
                return (ProbeRecord(key, PROBE_MISSING, 404, 0, 0, attempt,
                                    stage), None)
            if status == 403:
                raise FeedUnreachable(
                    f"{key.label()}: the datafeed returned 403. Phase 1 "
                    "established that the front end rejects VPN and datacenter "
                    "egress addresses outright while www.dukascopy.com keeps "
                    "working; check the public egress IP before suspecting a "
                    "routing fault.")
            if status in RETRYABLE_STATUS:
                last_detail = f"HTTP {status} after {len(body)} bytes"
                self.pacer.penalise()
                continue
            if status != 200:
                last_detail = f"HTTP {status}"
                self.pacer.penalise()
                continue

            self.pacer.reward()
            if not body:
                return (ProbeRecord(key, PROBE_EMPTY, 200, 0, 0, attempt,
                                    stage), b"")
            try:
                decoded = decode_bi5(body, key.pair, key.hour_start)
            except Bi5DecodeError as exc:
                # A body that will not decode is not evidence of absence, and
                # retrying is worth one attempt in case it was truncated.
                last_detail = f"DECODE_ERROR: {exc}"
                continue
            kind = PROBE_DATA if len(decoded) else PROBE_EMPTY
            return (ProbeRecord(key, kind, 200, len(body), len(decoded),
                                attempt, stage),
                    body if keep_payload else None)

        return (ProbeRecord(key, PROBE_ERROR, status, 0, 0, self.max_attempts,
                            stage, last_detail), None)

    def close(self) -> None:
        """Release the connection."""
        self.connection.close()


@dataclasses.dataclass
class SessionStats:
    """Counters for one harvest session, reported at the end and ledgered."""

    completed: int = 0
    by_kind: dict[str, int] = dataclasses.field(
        default_factory=lambda: {kind: 0 for kind in PROBE_KINDS})
    consecutive_errors: int = 0
    worst_streak: int = 0
    throttles: int = 0
    parked: float = 0.0
    outages: int = 0
    started: float = dataclasses.field(default_factory=time.monotonic)

    def record(self, kind: str) -> None:
        """Fold one classified probe into the counters."""
        self.completed += 1
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        if kind == PROBE_ERROR:
            self.consecutive_errors += 1
            self.worst_streak = max(self.worst_streak, self.consecutive_errors)
        else:
            self.consecutive_errors = 0

    @property
    def elapsed(self) -> float:
        """Seconds since the session began."""
        return time.monotonic() - self.started

    def to_dict(self) -> dict[str, Any]:
        """Summary for the log line and the ledger note."""
        rate = self.completed / self.elapsed if self.elapsed > 0 else 0.0
        return {"completed": self.completed, "by_kind": dict(self.by_kind),
                "seconds": round(self.elapsed, 1),
                "probes_per_second": round(rate, 3),
                "worst_error_streak": self.worst_streak,
                "throttles": self.throttles,
                "seconds_parked": round(self.parked, 1),
                "outages_ridden_out": self.outages}


def run_probes(keys: Sequence[ProbeKey], stage: str, writer: ProbeWriter,
               config: DukascopyConfig, *, max_attempts: int,
               deadline: float | None, pacer: Pacer | None = None,
               stats: SessionStats | None = None) -> SessionStats:
    """Probe every key with :data:`MAX_WORKERS` connections.

    Args:
        keys: What to probe, in the order they should be attempted.
        stage: Recorded on every record.
        writer: Where completed probes are checkpointed.
        config: Phase 1 datafeed tunables.
        max_attempts: HTTP attempts before a probe is recorded as ``error``.
        deadline: :func:`time.monotonic` value after which the session stops
            cleanly, leaving the rest for the next run. ``None`` means run to
            completion.
        pacer: Shared rate governor; created when omitted.
        stats: Counters to accumulate into; created when omitted.

    Returns:
        The session counters.

    Raises:
        FeedUnreachable: If the feed answers 403, or if
            :data:`DEAD_FEED_STREAK` probes in a row exhaust their attempts.
    """
    pacer = pacer or Pacer()
    stats = stats or SessionStats()
    work: queue.Queue[ProbeKey | None] = queue.Queue()
    for key in keys:
        work.put(key)
    for _ in range(MAX_WORKERS):
        work.put(None)

    lock = threading.Lock()
    fatal: list[BaseException] = []
    stop = threading.Event()

    def expired() -> bool:
        """True once the session's wall-clock budget has run out."""
        return deadline is not None and time.monotonic() >= deadline

    def worker() -> None:
        prober = Prober(pacer, config, max_attempts,
                        should_stop=lambda: stop.is_set() or expired())
        try:
            while not stop.is_set():
                key = work.get()
                if key is None:
                    return
                try:
                    record, _ = prober.probe(key, stage)
                except SessionStopped:
                    return
                except FeedUnreachable as exc:
                    with lock:
                        fatal.append(exc)
                    stop.set()
                    return
                writer.write(record.to_dict())
                with lock:
                    stats.record(record.kind)
                    completed = stats.completed
                    streak = stats.consecutive_errors
                if streak >= DEAD_FEED_STREAK:
                    with lock:
                        fatal.append(FeedUnreachable(
                            f"{streak} consecutive probes exhausted every "
                            f"attempt; the datafeed is not answering. "
                            f"Last: {record.key.label()} -- {record.detail}"))
                    stop.set()
                    return
                if completed % 200 == 0:
                    _LOG.info("%s: %d probes, %.2f/s, gap %.2fs, %d throttles, "
                              "%.0fs parked", stage, completed,
                              completed / max(stats.elapsed, 1e-9),
                              pacer.gap, pacer.throttles, pacer.parked)
                if expired():
                    stop.set()
                    return
        finally:
            prober.close()

    threads = [threading.Thread(target=worker, name=f"probe-{i}", daemon=True)
               for i in range(MAX_WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stats.throttles += pacer.throttles
    stats.parked += pacer.parked
    if fatal:
        raise fatal[0]
    return stats


# --------------------------------------------------------------------------- #
# What to probe
# --------------------------------------------------------------------------- #

def trading_days(start: dt.date, end: dt.date, hour: int) -> list[dt.date]:
    """Every date in ``[start, end]`` whose ``hour`` falls in the FX week.

    Derived from :func:`fxlab.ingestion.sessions.is_market_open`, which tracks
    17:00 ``America/New_York``, rather than from a hardcoded Monday-to-Friday
    rule. The two agree at 13:00 UTC, and only one of them stays right when the
    card is reused at a different hour.
    """
    days: list[dt.date] = []
    day = start
    while day <= end:
        stamp = dt.datetime(day.year, day.month, day.day, hour,
                            tzinfo=dt.timezone.utc)
        if is_market_open(stamp):
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def first_pass_keys(pairs: Sequence[str], start: dt.date, end: dt.date,
                    hour: int) -> list[ProbeKey]:
    """One probe per pair per trading day, interleaved across pairs.

    Interleaving matters for a resumable sweep: a session that stops half way
    leaves every pair half done rather than six pairs done and six untouched,
    so even a partial harvest supports the whole survey.
    """
    days = trading_days(start, end, hour)
    keys: list[ProbeKey] = []
    for day in days:
        for pair in pairs:
            keys.append(ProbeKey(pair, day.isoformat(), hour))
    return keys


def _iter_config_pairs(params: dict[str, Any]) -> list[str]:
    """The pair list from an experiment config's params."""
    pairs = params.get("pairs")
    if not pairs:
        raise ValueError("config params must list 'pairs'")
    return [str(p).upper() for p in pairs]


# --------------------------------------------------------------------------- #
# Quality spot checks
# --------------------------------------------------------------------------- #

def quality_targets(records: Iterable[dict[str, Any]], pair: str,
                    count: int) -> list[ProbeKey]:
    """Pick ``count`` ``data`` probes spread evenly across a pair's history.

    Deterministic by construction: the first, the last and evenly spaced
    positions in between, taken from the date-sorted list of probes that
    returned data. A seeded random choice would be reproducible too, but this
    one is also *explicable* -- "earliest, midpoint, latest" is a claim a
    reviewer can check by eye.
    """
    dated = sorted({(str(r["date"]), int(r["hour"])) for r in records
                    if str(r.get("pair")) == pair
                    and str(r.get("kind")) == PROBE_DATA})
    if not dated:
        return []
    if count <= 1:
        chosen = [dated[0]]
    else:
        step = (len(dated) - 1) / (count - 1)
        picked = sorted({int(round(index * step)) for index in range(count)})
        chosen = [dated[i] for i in picked]
    return [ProbeKey(pair, date, hour) for date, hour in chosen]


def quality_check(payload: bytes, key: ProbeKey) -> dict[str, Any]:
    """Decode one hour fully and report what the Phase 1 validator makes of it.

    This is the part of the card that asks whether early history is *usable*
    rather than merely *present*. A 2005 hour that decodes into forty crossed
    quotes is present and worthless, and only a full decode says so.
    """
    decoded = decode_bi5(payload, key.pair, key.hour_start)
    batch = deduplicate(decoded)
    issues = validate(batch)
    stats = spread_stats(batch)
    spec = pair_spec(key.pair)
    bid = batch.bid
    ask = batch.ask
    return {
        "pair": key.pair, "date": key.date, "hour": key.hour,
        "compressed_bytes": len(payload),
        "decoded_ticks": batch.decoded_ticks,
        "ticks": len(batch),
        "duplicates_dropped": batch.duplicates_dropped,
        "min_bid": round(float(bid.min()), 8) if len(batch) else None,
        "min_ask": round(float(ask.min()), 8) if len(batch) else None,
        "crossed_ticks": int((bid > ask).sum()) if len(batch) else 0,
        "non_positive_ticks": (int(((bid <= 0) | (ask <= 0)).sum())
                               if len(batch) else 0),
        "spread_pips": {k: round(float(v), 6) for k, v in stats.items()},
        "spread_ceiling_pips": spread_ceiling_pips(key.pair),
        "pip_size": spec.pip_size,
        "issues": [{"reason": i.reason, "count": i.count} for i in issues],
        "ok": (not [i for i in issues if i.is_hard]
               and stats["p99_9_pips"] <= spread_ceiling_pips(key.pair)),
    }


def run_quality(keys: Sequence[ProbeKey], quality_writer: ProbeWriter,
                config: DukascopyConfig, *, max_attempts: int,
                pacer: Pacer | None = None) -> int:
    """Re-fetch and fully decode the spot-check hours. Single connection.

    Serial on purpose: there are three dozen of these in the whole survey, and
    the decode dominates the fetch. The re-fetch writes only to the quality
    checkpoint, never to ``probes.jsonl`` -- these hours are already classified
    there by the first pass, and a second record for the same identity would be
    a duplicate rather than evidence.
    """
    pacer = pacer or Pacer()
    prober = Prober(pacer, config, max_attempts)
    written = 0
    try:
        for key in keys:
            record, payload = prober.probe(key, "quality", keep_payload=True)
            if record.kind != PROBE_DATA or not payload:
                quality_writer.write({
                    "pair": key.pair, "date": key.date, "hour": key.hour,
                    "ok": False, "ticks": 0,
                    "detail": f"spot check returned {record.kind}: "
                              f"{record.detail}"})
                written += 1
                continue
            quality_writer.write(quality_check(payload, key))
            written += 1
    finally:
        prober.close()
    return written


# --------------------------------------------------------------------------- #
# Parquet mirror
# --------------------------------------------------------------------------- #

def write_parquet(records: Sequence[dict[str, Any]],
                  path: pathlib.Path) -> pathlib.Path:
    """Mirror the probe checkpoint as Parquet, with pinned Arrow types.

    The JSONL is the checkpoint and the audit trail; this is the same rows in
    the columnar form the card asks for. Types are pinned for the same reason
    Phase 1 pins its tick schema: pandas 3 infers ``large_string`` for object
    columns and an inferred schema is a schema nobody agreed to.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([
        ("pair", pa.large_string()),
        ("date", pa.large_string()),
        ("hour", pa.int16()),
        ("kind", pa.large_string()),
        ("status", pa.int32()),
        ("compressed_bytes", pa.int64()),
        ("ticks", pa.int64()),
        ("attempts", pa.int16()),
        ("stage", pa.large_string()),
    ])
    ordered = sorted(records, key=lambda r: (str(r.get("pair")),
                                             str(r.get("date")),
                                             int(r.get("hour", 0))))
    columns = {
        "pair": [str(r.get("pair", "")) for r in ordered],
        "date": [str(r.get("date", "")) for r in ordered],
        "hour": [int(r.get("hour", 0)) for r in ordered],
        "kind": [str(r.get("kind", "")) for r in ordered],
        "status": [r.get("status") for r in ordered],
        "compressed_bytes": [int(r.get("bytes", 0)) for r in ordered],
        "ticks": [int(r.get("ticks", 0)) for r in ordered],
        "attempts": [int(r.get("attempts", 0)) for r in ordered],
        "stage": [str(r.get("stage", "")) for r in ordered],
    }
    table = pa.table(columns, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def load_params(config_path: pathlib.Path) -> dict[str, Any]:
    """Read ``[experiment.params]`` out of an experiment config."""
    from research.experiment import load_config

    return dict(load_config(config_path).params)


def plan(stage: str, params: dict[str, Any],
         records: Sequence[dict[str, Any]]) -> list[ProbeKey]:
    """The probes a stage wants, before anything already done is removed."""
    from research.coverage import refine_targets

    pairs = _iter_config_pairs(params)
    start = dt.date.fromisoformat(str(params["start_date"]))
    end = dt.date.fromisoformat(str(params["end_date"]))
    hour = int(params["probe_hour"])

    if stage == "first":
        return first_pass_keys(pairs, start, end, hour)
    if stage == "retry":
        # Re-ask for everything that exhausted its attempts. An `error` is a
        # probe that fell over, not evidence that the hour is absent, so it is
        # worth asking again once the feed is behaving; the analysis takes the
        # last record for an identity, so a successful re-probe replaces it.
        return [ProbeKey(str(r["pair"]), str(r["date"]), int(r["hour"]))
                for r in sorted(records, key=lambda r: (str(r.get("pair")),
                                                        str(r.get("date")),
                                                        int(r.get("hour", 0))))
                if str(r.get("kind")) == PROBE_ERROR]
    if stage == "refine":
        return refine_targets(records, params)
    if stage == "quality":
        count = int(params.get("quality_probes_per_pair", 3))
        keys: list[ProbeKey] = []
        for pair in pairs:
            keys.extend(quality_targets(records, pair, count))
        return keys
    raise ValueError(f"unknown stage {stage!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m research.coverage_probe",
        description="Probe Dukascopy coverage for task card T1. Stores no ticks.")
    parser.add_argument("--config", required=True, type=pathlib.Path,
                        help="the T1 experiment config TOML")
    parser.add_argument("--stage", default="first",
                        choices=("first", "retry", "refine", "quality"),
                        help="which sweep to run")
    parser.add_argument("--minutes", type=float, default=None,
                        help="wall-clock budget; the session stops cleanly and "
                             "the next run resumes where it stopped")
    parser.add_argument("--limit", type=int, default=None,
                        help="probe at most this many keys, for calibration")
    parser.add_argument("--base", type=pathlib.Path, default=None,
                        help="project root; derived when omitted")
    parser.add_argument("--outage-pause", type=float, default=180.0,
                        help="seconds to wait out a feed outage before "
                             "resuming the same stage")
    parser.add_argument("--max-outages", type=int, default=60,
                        help="outages ridden out before the stage gives up and "
                             "reports the feed unreachable")
    parser.add_argument("--no-ledger", action="store_true",
                        help="skip the ledger records, for calibration runs "
                             "that are not part of the experiment")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one harvest session and return a process exit code."""
    args = parse_args(argv)
    configure_logging()
    base = (pathlib.Path(args.base).resolve() if args.base
            else pathlib.Path(__file__).resolve().parents[1])

    from research import ledger as ledger_mod
    from research.experiment import load_config

    config = load_config(args.config)
    params = dict(config.params)
    directory = args.config.resolve().parent
    probes_path = directory / PROBES_NAME
    quality_path = directory / QUALITY_NAME

    existing = read_probes(probes_path)
    wanted = plan(args.stage, params, existing)
    # Two stages deliberately re-ask for identities that are already in
    # probes.jsonl, so neither can resume against it. The quality stage
    # re-fetches hours the first pass classified, and resumes against its own
    # checkpoint instead. The retry stage exists precisely to ask again, and is
    # self-limiting: it re-plans from the file each session, so an identity
    # that succeeds stops being an error and stops being planned.
    if args.stage == "quality":
        done = probe_index(read_probes(quality_path))
    elif args.stage == "retry":
        done = set()
    else:
        done = probe_index(existing)
    todo = [key for key in wanted if key.as_tuple() not in done]
    if args.limit is not None:
        todo = todo[:args.limit]

    _LOG.info("stage %s: %d probe(s) planned, %d already recorded, %d to do",
              args.stage, len(wanted), len(wanted) - len(todo), len(todo))
    if not todo:
        _LOG.info("nothing to do; stage %s is complete", args.stage)
        write_parquet(existing, directory / PROBES_PARQUET)
        return 0

    feed = DukascopyConfig(
        max_concurrency=MAX_WORKERS,
        max_retries=int(params.get("max_retries", 6)),
        timeout=float(params.get("timeout_seconds", 12.0)))
    deadline = (time.monotonic() + args.minutes * 60.0
                if args.minutes is not None else None)

    probe_id = f"{config.experiment_id}-probe"
    if not args.no_ledger:
        ledger_mod.append_start(
            base, experiment_id=probe_id, taskcard=config.taskcard,
            config_sha256=config.sha256, seed=config.seed, mode=config.mode,
            rerun_class=config.rerun_class,
            note=(f"probe harvest session, stage={args.stage}, "
                  f"{len(todo)} probe(s) queued; resumable, network only, "
                  "stores no ticks"))

    writer = ProbeWriter(probes_path)
    status = "ok"
    stats = SessionStats()
    outages = 0
    try:
        if args.stage == "quality":
            quality_writer = ProbeWriter(quality_path)
            try:
                written = run_quality(todo, quality_writer, feed,
                                      max_attempts=feed.max_retries + 1)
            finally:
                quality_writer.close()
            _LOG.info("quality stage: %d spot check(s) written", written)
            stats.completed = written
        else:
            # One ledger entry covers the whole stage, and the stage rides out
            # the feed's outages rather than exiting on them. The datafeed's
            # availability was measured to flap on a roughly one-minute cycle
            # with multi-minute blackouts; a harvester that exited on each one
            # would need restarting a hundred times overnight, and every
            # restart would be another ledger entry inflating this card's trial
            # count with something that is not a trial.
            while todo:
                try:
                    run_probes(todo, args.stage, writer, feed,
                               max_attempts=feed.max_retries + 1,
                               deadline=deadline, stats=stats)
                except FeedUnreachable as exc:
                    outages += 1
                    if outages > args.max_outages:
                        raise
                    _LOG.warning("outage %d/%d: %s -- pausing %.0fs",
                                 outages, args.max_outages, exc,
                                 args.outage_pause)
                    time.sleep(args.outage_pause)
                    # The streak is what tripped the outage; carrying it across
                    # the pause would make the next single failure trip it
                    # again immediately and turn one outage into sixty.
                    stats.consecutive_errors = 0
                if deadline is not None and time.monotonic() >= deadline:
                    _LOG.info("wall-clock budget spent; the rest resumes next "
                              "run")
                    break
                recorded = probe_index(read_probes(probes_path))
                todo = [key for key in todo if key.as_tuple() not in recorded]
            stats.outages = outages
    except FeedUnreachable as exc:
        status = "failed:FEED_UNREACHABLE"
        stats.outages = outages
        _LOG.error("%s", exc)
        print("FEED_UNREACHABLE", flush=True)
        print(str(exc), flush=True)
        return _finish(base, probe_id, config, writer, directory, probes_path,
                       stats, status, args.no_ledger, EXIT_FEED_UNREACHABLE)
    except KeyboardInterrupt:
        status = "failed:INTERRUPTED"
        stats.outages = outages
        return _finish(base, probe_id, config, writer, directory, probes_path,
                       stats, status, args.no_ledger, 130)

    return _finish(base, probe_id, config, writer, directory, probes_path,
                   stats, status, args.no_ledger, 0)


def _finish(base: pathlib.Path, probe_id: str, config: Any,
            writer: ProbeWriter, directory: pathlib.Path,
            probes_path: pathlib.Path, stats: SessionStats, status: str,
            no_ledger: bool, code: int) -> int:
    """Close the checkpoint, refresh the Parquet mirror and close the ledger."""
    writer.close()
    records = read_probes(probes_path)
    write_parquet(records, directory / PROBES_PARQUET)
    summary = stats.to_dict()
    summary["probes_on_disk"] = len(records)
    _LOG.info("session %s: %s", status, json.dumps(summary, sort_keys=True))
    if not no_ledger:
        from research import ledger as ledger_mod
        ledger_mod.append_end(
            base, experiment_id=probe_id,
            status=f"{status} {json.dumps(summary, sort_keys=True)}",
            result_files=[], result_hash=None, scored=False)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
