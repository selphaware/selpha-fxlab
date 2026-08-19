"""Where raw hourly bi5 payloads come from: local fixtures or the live feed.

Two implementations behind one protocol, so the pipeline, the validator and
the store are identical offline and online. That matters more than it sounds:
the offline gate is only meaningful if it exercises the same code path the live
pull does, right up to the byte-fetch.

Live-feed facts, all measured (SPEC.md):

* URL is ``{base}/{PAIR}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5`` with a
  **zero-based month** -- January is ``00``, December is ``11``.
* An **empty body with HTTP 200 means the market was closed**, not an error.
  A 404 is a genuinely absent hour. Conflating the two either manufactures
  gaps across every weekend or hides real holes.
* **503 is throttling**, served as an HAProxy page. Back off; do not give up.
* A **persistent** 503 on every request while the marketing site works means
  the egress IP is a VPN/datacenter address being rejected on reputation.
  That is worth saying out loud, because it looks exactly like a routing fault.
"""

from __future__ import annotations

import logging
import pathlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Any, Callable, Final, Protocol

from fxlab.config import DukascopyConfig, HourRequest

_LOG: Final[logging.Logger] = logging.getLogger(__name__)

#: The hour exists and has content.
AVAILABILITY_PRESENT: Final[str] = "present"
#: HTTP 200 with a zero-byte body: the market was closed for this hour.
AVAILABILITY_EMPTY: Final[str] = "empty"
#: HTTP 404, or no such fixture file: the hour is genuinely absent.
AVAILABILITY_MISSING: Final[str] = "missing"

#: HTTP statuses worth retrying. 503 is the throttle; its 5xx neighbours and
#: 429 behave the same way.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})


class FeedError(RuntimeError):
    """Raised when an hour cannot be retrieved after every retry."""


class OfflineError(RuntimeError):
    """Raised when fixture mode attempts to reach the network."""


@dataclass(frozen=True, slots=True)
class RawHour:
    """One raw hourly payload plus the provenance needed to audit it."""

    payload: bytes
    origin: str
    availability: str
    last_modified: str | None = None
    http_status: int | None = None

    @property
    def is_present(self) -> bool:
        """True when there are bytes to decode."""
        return self.availability == AVAILABILITY_PRESENT


class HourSource(Protocol):
    """Anything that can hand back the raw bytes for one (pair, date, hour)."""

    name: str

    def fetch(self, request: HourRequest) -> RawHour:
        """Return the raw payload for ``request``."""
        ...


class FixtureSource:
    """Reads frozen bi5 files from a local directory.

    Filenames follow ``<PAIR>_<YYYY-MM-DD>_<HH>h.bi5``. A zero-byte file means
    the same thing an empty HTTP body means: the market was closed.
    """

    name = "fixture"

    def __init__(self, raw_dir: pathlib.Path) -> None:
        self.raw_dir = pathlib.Path(raw_dir)

    def fetch(self, request: HourRequest) -> RawHour:
        """Read one hour from the fixture directory. Never touches the network."""
        path = self.raw_dir / request.fixture_name
        if not path.is_file():
            return RawHour(payload=b"", origin=str(path),
                           availability=AVAILABILITY_MISSING)
        payload = path.read_bytes()
        availability = AVAILABILITY_PRESENT if payload else AVAILABILITY_EMPTY
        return RawHour(payload=payload, origin=str(path), availability=availability)


def bi5_url(pair: str, day: Any, hour: int,
            base_url: str = DukascopyConfig().base_url) -> str:
    """Build the datafeed URL for one hour.

    Args:
        pair: Pair symbol, upper case.
        day: A ``datetime.date``.
        hour: UTC hour, 0-23.
        base_url: Datafeed root.

    Returns:
        The full URL. **The month is zero-based** -- ``/2026/06/14/`` is
        14 July 2026, confirmed independently of any decoding by the
        ``Last-Modified`` header the datafeed returns.
    """
    root = base_url.rstrip("/")
    return (f"{root}/{pair.upper()}/{day.year:04d}/{day.month - 1:02d}/"
            f"{day.day:02d}/{hour:02d}h_ticks.bi5")


class DukascopySource:
    """Fetches hours from the live Dukascopy datafeed, politely.

    Retries with exponential backoff on throttling and transport faults; never
    retries a 404, which is information rather than a fault.
    """

    name = "dukascopy"

    def __init__(self, config: DukascopyConfig | None = None,
                 *, opener: Callable[..., Any] | None = None,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.config = config or DukascopyConfig()
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep or time.sleep

    def url_for(self, request: HourRequest) -> str:
        """URL for one requested hour."""
        return bi5_url(request.pair, request.day, request.hour,
                       base_url=self.config.base_url)

    def fetch(self, request: HourRequest) -> RawHour:
        """Fetch one hour, retrying throttles and transport faults.

        Args:
            request: The hour to retrieve.

        Returns:
            The raw payload, or an empty/missing marker.

        Raises:
            FeedError: If every attempt failed.
        """
        url = self.url_for(request)
        delay = self.config.backoff_initial
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return self._attempt(url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    _LOG.info("%s: 404, hour absent from the feed", request.label())
                    return RawHour(payload=b"", origin=url,
                                   availability=AVAILABILITY_MISSING,
                                   http_status=404)
                if exc.code == 403:
                    raise FeedError(
                        f"{request.label()}: {url} returned 403. The datafeed front "
                        "end rejects VPN and datacenter egress addresses outright "
                        "while www.dukascopy.com keeps working -- check the public "
                        "egress IP before suspecting a routing fault."
                    ) from exc
                if exc.code not in RETRYABLE_STATUS:
                    raise FeedError(
                        f"{request.label()}: {url} returned {exc.code}") from exc
                last_error = exc
            except (urllib.error.URLError, IncompleteRead, TimeoutError,
                    ConnectionError, OSError) as exc:
                last_error = exc

            if attempt < self.config.max_retries:
                _LOG.warning("%s: %s -- retrying in %.1fs (attempt %d/%d)",
                             request.label(), last_error, delay,
                             attempt + 1, self.config.max_retries)
                self._sleep(delay)
                delay = min(delay * self.config.backoff_factor,
                            self.config.backoff_max)

        raise FeedError(
            f"{request.label()}: {url} failed after {self.config.max_retries + 1} "
            f"attempts; last error: {last_error}")

    def _attempt(self, url: str) -> RawHour:
        """Perform one HTTP GET, with no retry logic of its own."""
        req = urllib.request.Request(
            url, headers={"User-Agent": self.config.user_agent})
        with self._opener(req, timeout=self.config.timeout) as response:
            payload = response.read()
            status = getattr(response, "status", None)
            headers = getattr(response, "headers", None)
            last_modified = headers.get("Last-Modified") if headers else None
        availability = AVAILABILITY_PRESENT if payload else AVAILABILITY_EMPTY
        return RawHour(payload=payload, origin=url, availability=availability,
                       last_modified=last_modified, http_status=status)


class BlockedSource:
    """A source that refuses to do anything, making fixture mode provable.

    Fixture mode must be offline by construction, not by convention. When the
    configuration says ``fixture`` this stands in for the network client, so an
    accidental live fetch fails loudly here instead of quietly reaching out.
    """

    name = "blocked"

    def __init__(self, why: str) -> None:
        self.why = why

    def fetch(self, request: HourRequest) -> RawHour:
        """Always raise :class:`OfflineError`."""
        raise OfflineError(f"{request.label()}: {self.why}")


def build_source(mode: str, *, raw_dir: pathlib.Path | None = None,
                 config: DukascopyConfig | None = None) -> HourSource:
    """Return the source implementing ``mode``.

    Args:
        mode: Either ``fixture`` or ``live``.
        raw_dir: Fixture directory, required in fixture mode.
        config: Live client tunables.

    Returns:
        A source honouring :class:`HourSource`.

    Raises:
        ValueError: On an unknown mode, or a fixture mode with no directory.
    """
    if mode == "fixture":
        if raw_dir is None:
            raise ValueError("fixture mode requires raw_dir")
        return FixtureSource(raw_dir)
    if mode == "live":
        return DukascopySource(config)
    raise ValueError(f"unknown ingest mode {mode!r}")
