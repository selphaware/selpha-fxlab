"""The holdout seal: one cutoff date, enforced by absence and by refusal.

Pre-registered decision #2 of ``SPEC2.md``: everything from **2025-03-01**
onward is sealed. The primary enforcement is that sealed data is never
downloaded during Phase 2 -- the seal is absence. This module is the secondary
enforcement, so that it cannot be fetched or read *by accident*:

* :func:`is_sealed` is the single definition of "sealed", used by the loader,
  the experiment runner and the research gate alike;
* :func:`assert_not_sealed` raises :class:`SealBreach`, whose reason token is
  the literal string ``HOLDOUT_SEALED`` that the gate greps for;
* :func:`sealed_dates_in_text` finds sealed dates in a config file, so a
  config asking for the holdout is rejected before anything runs.

Ruling A (chat, 2026-08-19): the on-disk assertion is scoped to
``data/research/``. The Phase 1 live week lives in ``data/live_week/`` and is
2026 data -- inside the seal -- and is reachable only in ``mechanical`` loader
mode, which cannot produce a scorecard. Phase 1's frozen fixtures are also
inside the seal and are deliberately out of scope: they are judge machinery,
not research data.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
from typing import Final

#: The named reason emitted verbatim on stderr and recorded in the ledger.
HOLDOUT_SEALED: Final[str] = "HOLDOUT_SEALED"

#: Everything on or after this date is sealed. Pre-registered, not tunable.
HOLDOUT_CUTOFF: Final[dt.date] = dt.date(2025, 3, 1)

#: The last date research may use. Stated separately because it is the number
#: that appears in task cards, and ``CUTOFF - 1 day`` is a bug waiting to be
#: written down wrongly.
RESEARCH_WINDOW_END: Final[dt.date] = dt.date(2025, 2, 28)

#: Where research data lives, relative to the project root (ruling D3).
RESEARCH_DATA_DIR: Final[str] = "data/research"

#: The only root ``mechanical`` mode may read (ruling A). Exactly one entry.
MECHANICAL_ALLOWLIST: Final[tuple[str, ...]] = ("data/live_week",)

#: ``YYYY-MM-DD`` anywhere in a text file.
_DATE_RE: Final[re.Pattern[str]] = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


class SealBreach(Exception):
    """Raised when anything reaches for data on or after the cutoff.

    Attributes:
        reason: Always ``HOLDOUT_SEALED``; the gate matches on this token.
        detail: What was asked for, for the human reading the traceback.
    """

    reason: Final[str] = HOLDOUT_SEALED

    def __init__(self, detail: str) -> None:
        super().__init__(f"{HOLDOUT_SEALED}: {detail}")
        self.detail = detail


def as_date(value: str | dt.date | dt.datetime) -> dt.date:
    """Coerce a date-ish value to a :class:`datetime.date`.

    Args:
        value: ``YYYY-MM-DD`` string, date, or datetime.

    Returns:
        The corresponding date.

    Raises:
        ValueError: If a string is not ``YYYY-MM-DD``.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def is_sealed(value: str | dt.date | dt.datetime) -> bool:
    """True if ``value`` falls on or after the holdout cutoff."""
    return as_date(value) >= HOLDOUT_CUTOFF


def assert_not_sealed(value: str | dt.date | dt.datetime, context: str = "") -> None:
    """Raise :class:`SealBreach` if ``value`` is sealed.

    Args:
        value: The date being requested.
        context: What asked for it, quoted back in the error.

    Raises:
        SealBreach: If the date is on or after the cutoff.
    """
    date = as_date(value)
    if date >= HOLDOUT_CUTOFF:
        where = f" ({context})" if context else ""
        raise SealBreach(
            f"{date.isoformat()} is on or after the sealed cutoff "
            f"{HOLDOUT_CUTOFF.isoformat()}{where}")


def sealed_dates_in_text(text: str) -> list[str]:
    """Return every sealed ``YYYY-MM-DD`` token in ``text``, in order.

    Used against ingest and experiment configs. A date that is merely mentioned
    in a comment still counts: a config file is not the place to write down the
    holdout range, and a false positive costs a reworded comment.
    """
    found: list[str] = []
    for match in _DATE_RE.finditer(text):
        token = match.group(0)
        try:
            date = dt.date.fromisoformat(token)
        except ValueError:
            continue
        if date >= HOLDOUT_CUTOFF:
            found.append(token)
    return found


def sealed_dates_in_file(path: pathlib.Path) -> list[str]:
    """Return every sealed date token in a text file, or [] if unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return sealed_dates_in_text(text)
