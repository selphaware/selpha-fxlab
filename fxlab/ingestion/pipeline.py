"""Ingest orchestration: fetch, decode, de-duplicate, validate, store, record.

The pipeline is the only place that knows the order of those steps, and it is
identical in fixture mode and live mode. Every requested hour produces exactly
one manifest entry, whatever happened to it: data, an empty body, or a hole.

Failure policy
--------------
* A hard validation failure (crossed quote, non-positive price, closed-market
  tick, a tick outside its own hour) rejects the hour: nothing is written, the
  reason token goes to stderr verbatim, and the run exits non-zero.
* A payload that will not decode is the same kind of failure.
* An hour the feed does not have is a gap. Whether that fails the run is
  ``ingest.fail_on_gap`` (default true), because on a live pull a silent hole
  is worse than a loud one.
* Duplicates are never a failure. They are dropped and counted.
"""

from __future__ import annotations

import concurrent.futures as futures
import hashlib
import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Final, Iterator

from fxlab.config import HourRequest, IngestConfig
from fxlab.ingestion.bi5 import Bi5DecodeError, decode_bi5
from fxlab.ingestion.manifest import (
    HourRecord,
    Manifest,
    STATUS_CLOSED,
    STATUS_EMPTY,
    STATUS_GAP,
    STATUS_OK,
    SETTLED_STATUSES,
    load_manifest,
    manifest_path,
    write_manifest,
)
from fxlab.ingestion.pairs import pair_spec
from fxlab.ingestion.sessions import is_market_open
from fxlab.ingestion.sources import (
    AVAILABILITY_EMPTY,
    AVAILABILITY_MISSING,
    HourSource,
    RawHour,
    build_source,
)
from fxlab.ingestion.store import hour_file, write_ticks
from fxlab.ingestion.validation import (
    DECODE_ERROR,
    FETCH_ERROR,
    ValidationIssue,
    deduplicate,
    spread_stats,
    validate,
)
from fxlab.logging_setup import emit_reason

_LOG: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    """What one ingest run did."""

    manifest: Manifest
    manifest_file: pathlib.Path
    hours_requested: int = 0
    hours_ok: int = 0
    hours_empty: int = 0
    hours_gap: int = 0
    hours_skipped: int = 0
    ticks_written: int = 0
    duplicates_dropped: int = 0
    bar_files: list[str] = field(default_factory=list)
    bar_dates_built: int = 0
    bar_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """True when no hard validation error was recorded."""
        return self.manifest.ok

    def summary_line(self) -> str:
        """One-line human summary, for the CLI log."""
        return (f"{self.hours_requested} hour(s): {self.hours_ok} ok, "
                f"{self.hours_empty} empty/closed, {self.hours_gap} gap, "
                f"{self.hours_skipped} already stored; "
                f"{self.ticks_written:,} ticks written, "
                f"{self.duplicates_dropped:,} duplicates dropped")


def _fetch_batched(source: HourSource, requests: list[HourRequest],
                   workers: int) -> Iterator[tuple[HourRequest, RawHour | Exception]]:
    """Fetch hours with bounded concurrency, yielding results in request order.

    Args:
        source: The hour source.
        requests: Hours to fetch.
        workers: Maximum simultaneous fetches. The live feed throttles, so this
            is capped by config at 4.

    Yields:
        ``(request, RawHour)`` or ``(request, exception)`` per hour.

    Fetching runs in chunks rather than submitting every hour at once: a
    multi-year pull would otherwise hold every payload in memory before the
    first one is decoded.
    """
    if workers <= 1 or len(requests) == 1:
        for request in requests:
            try:
                yield request, source.fetch(request)
            except Exception as exc:  # noqa: BLE001 - reported per hour
                yield request, exc
        return

    chunk = max(workers * 4, workers)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(requests), chunk):
            window = requests[start:start + chunk]
            submitted = [pool.submit(source.fetch, request) for request in window]
            for request, future in zip(window, submitted):
                try:
                    yield request, future.result()
                except Exception as exc:  # noqa: BLE001 - reported per hour
                    yield request, exc


def _archive(config: IngestConfig, request: HourRequest, raw: RawHour) -> None:
    """Keep the raw payload so a live pull can be replayed offline later."""
    if config.archive_raw_dir is None:
        return
    target = pathlib.Path(config.archive_raw_dir) / request.fixture_name
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(raw.payload)
    os.replace(tmp, target)


def _record_gap(manifest: Manifest, request: HourRequest,
                issues: list[ValidationIssue], *, hard: bool,
                **fields: Any) -> HourRecord:
    """Record one hour as a gap and emit its reason tokens."""
    record = HourRecord(pair=request.pair, date=request.date_str, hour=request.hour,
                        status=STATUS_GAP,
                        issues=[i.to_dict() for i in issues], **fields)
    manifest.upsert(record)
    for issue in issues:
        emit_reason(issue.reason, issue.detail)
        if hard:
            manifest.add_issue(request.pair, request.date_str, request.hour, issue)
        else:
            manifest.warnings.append({
                "pair": request.pair, "date": request.date_str,
                "hour": request.hour, **issue.to_dict()})
        _LOG.error("%s rejected: %s", request.label(), issue.detail)
    return record


def _process_hour(config: IngestConfig, request: HourRequest,
                  fetched: RawHour | Exception, manifest: Manifest) -> HourRecord:
    """Turn one fetched payload into a manifest entry, writing ticks if valid."""
    label = request.label()

    if isinstance(fetched, Exception):
        issue = ValidationIssue(FETCH_ERROR, f"{label}: fetch failed: {fetched}")
        return _record_gap(manifest, request, [issue], hard=config.fail_on_gap)

    _archive(config, request, fetched)
    sha256 = hashlib.sha256(fetched.payload).hexdigest()
    common: dict[str, Any] = {
        "sha256": sha256,
        "compressed_bytes": len(fetched.payload),
        "origin": fetched.origin,
    }

    if fetched.availability == AVAILABILITY_MISSING:
        issue = ValidationIssue(
            FETCH_ERROR,
            f"{label}: the feed has no such hour ({fetched.origin}). A 404 is a "
            "genuinely absent hour, unlike an empty body, which means the market "
            "was closed.")
        return _record_gap(manifest, request, [issue],
                           hard=config.fail_on_gap, **common)

    if fetched.availability == AVAILABILITY_EMPTY:
        open_market = is_market_open(request.start)
        status = STATUS_EMPTY if open_market else STATUS_CLOSED
        if open_market:
            _LOG.warning("%s: empty body inside the trading week", label)
            manifest.warnings.append({
                "pair": request.pair, "date": request.date_str,
                "hour": request.hour, "reason": "EMPTY_TRADING_HOUR", "count": 0,
                "detail": f"{label}: the feed served no ticks during an open hour"})
        else:
            _LOG.info("%s: market closed, empty body as expected", label)
        record = HourRecord(pair=request.pair, date=request.date_str,
                            hour=request.hour, status=status, **common)
        manifest.upsert(record)
        return record

    spec = pair_spec(request.pair)
    try:
        decoded = decode_bi5(fetched.payload, request.pair, request.start, spec=spec)
    except Bi5DecodeError as exc:
        issue = ValidationIssue(DECODE_ERROR, f"{label}: {exc}")
        return _record_gap(manifest, request, [issue], hard=True, **common)

    batch = deduplicate(decoded)
    common["decoded_bytes"] = decoded.decoded_bytes
    issues = validate(batch, spec=spec)
    hard = [issue for issue in issues if issue.is_hard]
    soft = [issue for issue in issues if not issue.is_hard]

    if hard:
        record = _record_gap(
            manifest, request, hard, hard=True,
            decoded_ticks=batch.decoded_ticks,
            duplicates_dropped=batch.duplicates_dropped, **common)
        for issue in soft:
            manifest.add_issue(request.pair, request.date_str, request.hour, issue)
        return record

    for issue in soft:
        _LOG.warning("%s: %s", label, issue.detail)
        manifest.add_issue(request.pair, request.date_str, request.hour, issue)

    written = write_ticks(config.out_dir, batch, config.source)
    first_ts = last_ts = None
    if len(batch):
        first_ts = str(batch.ts_us[0].astype("datetime64[us]")) + "+00:00"
        last_ts = str(batch.ts_us[-1].astype("datetime64[us]")) + "+00:00"

    record = HourRecord(
        pair=request.pair, date=request.date_str, hour=request.hour,
        status=STATUS_OK,
        decoded_ticks=batch.decoded_ticks,
        written_ticks=len(batch),
        duplicates_dropped=batch.duplicates_dropped,
        path=str(written.path),
        first_ts=first_ts,
        last_ts=last_ts,
        spread_pips=spread_stats(batch, spec=spec),
        issues=[i.to_dict() for i in soft],
        **common)
    manifest.upsert(record)
    _LOG.info("%s: %d ticks written (%d duplicates dropped)",
              label, len(batch), batch.duplicates_dropped)
    return record


def _already_stored(config: IngestConfig, manifest: Manifest,
                    request: HourRequest) -> HourRecord | None:
    """Return a carried-forward record when this hour needs no re-fetch."""
    if not config.resume:
        return None
    previous = manifest.index().get(request.key)
    if previous is None or previous.status not in SETTLED_STATUSES:
        return None
    if previous.status == STATUS_OK:
        path = hour_file(config.out_dir, request.pair,
                         request.date_str, request.hour)
        if not path.exists():
            return None
    return previous


def _build_bars(config: IngestConfig, report: IngestReport) -> None:
    """Bring the configured bar tables up to date with the stored ticks.

    Incremental, per SPEC2 prerequisite P0-B: only the days whose stored ticks
    differ from what was last folded in are resampled. Rebuilding a pair's whole
    history on every run is fine for a week and hopeless for a decade of twelve
    pairs, which is the scale this store is being filled to.
    """
    from fxlab.ingestion.bars import build_bars_incremental

    touched: dict[str, set[str]] = {}
    for rec in report.manifest.hours:
        if rec.status == STATUS_OK:
            touched.setdefault(rec.pair, set()).add(rec.date)

    for pair, dates in sorted(touched.items()):
        for update in build_bars_incremental(config.out_dir, pair,
                                             config.bar_timeframes,
                                             dates=sorted(dates)):
            report.bar_files.append(str(update.path))
            report.bar_dates_built += update.dates_built
            report.bar_seconds += update.seconds


def ingest(config: IngestConfig, *, source: HourSource | None = None) -> IngestReport:
    """Run the ingest pipeline described by ``config``.

    Args:
        config: A validated ingest configuration.
        source: Override the hour source; the mode selects one by default.

    Returns:
        An :class:`IngestReport`. The manifest has already been written.
    """
    out_dir = pathlib.Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The manifest need not live in the store root: a bulk pull shards it by
    # pair and month so that checkpointing stays cheap at a million hours.
    manifest_root = (pathlib.Path(config.manifest_dir)
                     if config.manifest_dir is not None else out_dir)
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(manifest_root) if config.resume else Manifest()
    manifest.errors = []
    manifest.warnings = []
    manifest.source = config.source

    if source is None:
        source = build_source(config.mode, raw_dir=config.raw_dir,
                              config=config.dukascopy)
    _LOG.info("ingest mode=%s source=%s hours=%d out_dir=%s",
              config.mode, getattr(source, "name", type(source).__name__),
              len(config.hours), out_dir)

    report = IngestReport(manifest=manifest,
                          manifest_file=manifest_path(manifest_root),
                          hours_requested=len(config.hours))

    pending: list[HourRequest] = []
    for request in config.hours:
        carried = _already_stored(config, manifest, request)
        if carried is None:
            pending.append(request)
            continue
        report.hours_skipped += 1
        if carried.status == STATUS_OK:
            report.hours_ok += 1
            report.ticks_written += carried.written_ticks
        else:
            report.hours_empty += 1
        _LOG.debug("%s: already stored (%s), not re-fetched",
                   request.label(), carried.status)

    workers = min(config.dukascopy.max_concurrency, max(1, len(pending)))
    processed = 0
    for request, fetched in _fetch_batched(source, pending, workers):
        record = _process_hour(config, request, fetched, manifest)
        if record.status == STATUS_OK:
            report.hours_ok += 1
            report.ticks_written += record.written_ticks
        elif record.status in (STATUS_EMPTY, STATUS_CLOSED):
            report.hours_empty += 1
        else:
            report.hours_gap += 1
        report.duplicates_dropped += record.duplicates_dropped

        # Checkpoint the ledger as we go. A multi-day live pull that is
        # interrupted -- and one will be, because the feed throttles and the
        # transport drops -- must not lose the record of what it already has,
        # or the next run re-fetches everything and earns the same throttling.
        processed += 1
        if processed % config.checkpoint_every == 0:
            write_manifest(manifest_root, manifest)
            _LOG.debug("manifest checkpointed after %d hour(s)", processed)

    if config.bar_timeframes:
        _build_bars(config, report)

    write_manifest(manifest_root, manifest)
    _LOG.info("ingest complete: %s", report.summary_line())
    return report
