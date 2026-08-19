"""The ingest manifest: what was asked for, what arrived, and what was wrong.

One entry per requested hour, **including** the empty ones. An hour that failed
or arrived corrupt is recorded as a gap; it is never silently skipped, because
a pipeline whose failures are invisible produces a dataset whose holes are
invisible too.

The manifest is also the resume ledger: an already-ingested hour is not
re-fetched on a later run.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Final

from fxlab.ingestion.validation import ValidationIssue

#: Manifest filename inside the store root.
MANIFEST_NAME: Final[str] = "manifest.json"

#: Bumped whenever the on-disk shape changes incompatibly.
SCHEMA_VERSION: Final[int] = 1

#: The hour was fetched, decoded, validated and stored.
STATUS_OK: Final[str] = "ok"
#: The feed served an empty body inside the trading week: no ticks, not an error.
STATUS_EMPTY: Final[str] = "empty"
#: The feed served an empty body outside the trading week: the market was shut.
STATUS_CLOSED: Final[str] = "closed"
#: The hour could not be fetched, decoded or validated. A hole in the data.
STATUS_GAP: Final[str] = "gap"

#: Statuses that mean "this hour is accounted for and needs no re-fetch".
SETTLED_STATUSES: Final[frozenset[str]] = frozenset({STATUS_OK, STATUS_EMPTY, STATUS_CLOSED})

#: sha256 of a zero-byte body, which is what a closed hour hashes to.
EMPTY_SHA256: Final[str] = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


@dataclass(slots=True)
class HourRecord:
    """The manifest entry for one requested hour."""

    pair: str
    date: str
    hour: int
    status: str
    decoded_ticks: int = 0
    written_ticks: int = 0
    duplicates_dropped: int = 0
    sha256: str = EMPTY_SHA256
    compressed_bytes: int = 0
    decoded_bytes: int = 0
    origin: str | None = None
    path: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    spread_pips: dict[str, float] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, int]:
        """Identity of this hour."""
        return (self.pair, self.date, self.hour)

    @property
    def is_gap(self) -> bool:
        """True when this hour is a hole rather than data."""
        return self.status == STATUS_GAP

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "pair": self.pair,
            "date": self.date,
            "hour": self.hour,
            "status": self.status,
            "decoded_ticks": self.decoded_ticks,
            "written_ticks": self.written_ticks,
            "duplicates_dropped": self.duplicates_dropped,
            "sha256": self.sha256,
            "compressed_bytes": self.compressed_bytes,
            "decoded_bytes": self.decoded_bytes,
            "origin": self.origin,
            "path": self.path,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "spread_pips": self.spread_pips,
            "issues": self.issues,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HourRecord:
        """Rebuild a record written by :meth:`to_dict`."""
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_issues(cls, pair: str, date: str, hour: int,
                    issues: list[ValidationIssue], **kwargs: Any) -> HourRecord:
        """Build a gap record carrying the reasons it is a gap."""
        return cls(pair=pair, date=date, hour=hour, status=STATUS_GAP,
                   issues=[i.to_dict() for i in issues], **kwargs)


@dataclass(slots=True)
class Manifest:
    """The full ingest ledger for one store."""

    hours: list[HourRecord] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    source: str = "dukascopy"
    generated_at: str | None = None
    schema_version: int = SCHEMA_VERSION

    def index(self) -> dict[tuple[str, str, int], HourRecord]:
        """Map hour identity to record."""
        return {rec.key: rec for rec in self.hours}

    def upsert(self, record: HourRecord) -> None:
        """Insert ``record``, replacing any existing entry for the same hour."""
        for i, existing in enumerate(self.hours):
            if existing.key == record.key:
                self.hours[i] = record
                return
        self.hours.append(record)

    def add_issue(self, pair: str, date: str, hour: int, issue: ValidationIssue) -> None:
        """File a validation issue under errors or warnings by severity."""
        entry = {"pair": pair, "date": date, "hour": hour, **issue.to_dict()}
        (self.errors if issue.is_hard else self.warnings).append(entry)

    @property
    def ok(self) -> bool:
        """True when no hard validation error was recorded."""
        return not self.errors

    def gaps(self) -> list[HourRecord]:
        """Every hour recorded as a hole."""
        return [rec for rec in self.hours if rec.is_gap]

    def coverage(self) -> dict[str, Any]:
        """Summarise coverage per pair-day and flag tick-count outliers.

        Daily totals are compared with the trailing median of the same pair's
        previous days; a day more than 4x or less than a quarter of it is
        flagged as a warning. That is a coarse detector on purpose -- it exists
        to catch a truncated download, not to model volume seasonality.
        """
        outliers: list[dict[str, Any]] = []
        by_day: dict[tuple[str, str], dict[str, Any]] = {}
        for rec in self.hours:
            day = by_day.setdefault((rec.pair, rec.date), {
                "pair": rec.pair, "date": rec.date, "hours_requested": 0,
                "hours_ok": 0, "hours_empty": 0, "hours_gap": 0,
                "ticks": 0, "duplicates_dropped": 0,
            })
            day["hours_requested"] += 1
            day["ticks"] += rec.written_ticks
            day["duplicates_dropped"] += rec.duplicates_dropped
            if rec.status == STATUS_OK:
                day["hours_ok"] += 1
            elif rec.status in (STATUS_EMPTY, STATUS_CLOSED):
                day["hours_empty"] += 1
            else:
                day["hours_gap"] += 1

        days = sorted(by_day.values(), key=lambda d: (d["pair"], d["date"]))
        history: dict[str, list[int]] = {}
        for day in days:
            prior = [n for n in history.get(day["pair"], []) if n > 0]
            day["tick_count_outlier"] = False
            if prior and day["ticks"] > 0:
                prior_sorted = sorted(prior)
                mid = len(prior_sorted) // 2
                median = (prior_sorted[mid] if len(prior_sorted) % 2
                          else (prior_sorted[mid - 1] + prior_sorted[mid]) / 2)
                if median > 0 and not (0.25 * median <= day["ticks"] <= 4.0 * median):
                    day["tick_count_outlier"] = True
                    outliers.append({
                        "pair": day["pair"], "date": day["date"], "hour": None,
                        "reason": "TICK_COUNT_OUTLIER", "count": day["ticks"],
                        "detail": (f"{day['pair']} {day['date']}: {day['ticks']:,} ticks "
                                   f"against a trailing median of {median:,.0f}"),
                    })
            history.setdefault(day["pair"], []).append(day["ticks"])
        return {"by_day": days, "outliers": outliers}

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form, matching the documented manifest shape."""
        coverage = self.coverage()
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at or dt.datetime.now(
                dt.timezone.utc).isoformat(),
            "source": self.source,
            "hours": [rec.to_dict() for rec in self.hours],
            "coverage": coverage,
            "validation": {
                "ok": self.ok,
                "errors": self.errors,
                "warnings": [*self.warnings, *coverage["outliers"]],
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        """Rebuild a manifest written by :meth:`to_dict`."""
        validation = data.get("validation") or {}
        return cls(
            hours=[HourRecord.from_dict(h) for h in data.get("hours", [])],
            errors=list(validation.get("errors", [])),
            warnings=list(validation.get("warnings", [])),
            source=str(data.get("source", "dukascopy")),
            generated_at=data.get("generated_at"),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


def manifest_path(out_dir: pathlib.Path) -> pathlib.Path:
    """Return the manifest path inside a store root."""
    return pathlib.Path(out_dir) / MANIFEST_NAME


def load_manifest(out_dir: pathlib.Path) -> Manifest:
    """Load an existing manifest, or return an empty one.

    A manifest that cannot be parsed is treated as absent rather than fatal:
    the worst case is re-fetching hours that were already stored.
    """
    path = manifest_path(out_dir)
    if not path.is_file():
        return Manifest()
    try:
        return Manifest.from_dict(json.loads(path.read_text(encoding="utf8")))
    except (json.JSONDecodeError, TypeError, ValueError):
        return Manifest()


def write_manifest(out_dir: pathlib.Path, manifest: Manifest) -> pathlib.Path:
    """Write the manifest atomically and return its path."""
    path = manifest_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=False),
                   encoding="utf8")
    os.replace(tmp, path)
    return path
