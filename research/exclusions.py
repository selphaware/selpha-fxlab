"""Data-quality exclusions: pair windows research may not read.

Ruling R1 of the M2 checkpoint (``SPEC2.md`` §M2 checkpoint rulings) excludes
AUDUSD before 2011-01-01. The feed served those hours; Phase 1 validation
rejected 12,996 of them for crossed quotes across two bounded episodes, and
what survived is a two-thirds-empty year no analysis should quietly average
over. The corruption was not repaired and the ``CROSSED_QUOTE`` rule was not
weakened, so the data that *is* stored for that window is a biased sample of
it -- which is worse than absence, because absence is visible.

This module is to R1 what :mod:`research.seal` is to the holdout: one
definition, used by the loader, the experiment entry points and the reports
alike, so that "excluded" cannot mean two things in two places.

The enforcement is deliberately the same shape as the seal's. An exclusion is
checked **before** the filesystem, it raises a named reason the gate and the
reports can both grep for, and every read that was permitted is recorded --
so a report states the exclusion as a number rather than as a claim.

Unlike the seal, an exclusion is not a secret being kept. It is a defect being
declared, and the difference shows in what callers are expected to do: a caller
that wants the permitted part of a range asks for exactly that part, via
:func:`permitted_dates`, and reports how much it dropped.
"""

from __future__ import annotations

import datetime as dt
from typing import Final, Iterable, Iterator, Sequence

from research.seal import as_date

#: The named reason, emitted verbatim on stderr and recorded in results.
PAIR_EXCLUDED_WINDOW: Final[str] = "PAIR_EXCLUDED_WINDOW"


class PairExcluded(Exception):
    """Raised when research reaches for data inside an exclusion window.

    Attributes:
        reason: Always ``PAIR_EXCLUDED_WINDOW``; reports and the gate match
            on this token.
        detail: Which pair, which date and which ruling, for the human reading
            the traceback.
    """

    reason: Final[str] = PAIR_EXCLUDED_WINDOW

    def __init__(self, detail: str) -> None:
        super().__init__(f"{PAIR_EXCLUDED_WINDOW}: {detail}")
        self.detail = detail


class Exclusion:
    """One pair excluded over one half-open date window.

    Args:
        pair: The pair name.
        before: Dates strictly before this are excluded. ``None`` means the
            window has no lower-bounded end and every date is excluded.
        after: Dates on or after this are excluded. ``None`` means none are.
        ruling: The ruling that made the decision, quoted in refusals.
        why: One sentence a report can print next to the number.

    A window with both bounds ``None`` excludes nothing and is rejected at
    construction: an exclusion that excludes nothing is a comment, and comments
    do not belong in an enforcement table.
    """

    __slots__ = ("pair", "before", "after", "ruling", "why")

    def __init__(self, pair: str, *, before: dt.date | None = None,
                 after: dt.date | None = None, ruling: str, why: str) -> None:
        if before is None and after is None:
            raise ValueError(f"exclusion for {pair} bounds nothing")
        self.pair = pair
        self.before = before
        self.after = after
        self.ruling = ruling
        self.why = why

    def covers(self, date: dt.date) -> bool:
        """True when ``date`` falls inside the excluded window."""
        if self.before is not None and date < self.before:
            return True
        return self.after is not None and date >= self.after

    def describe(self) -> str:
        """The window as a phrase a report can print."""
        if self.before is not None and self.after is not None:
            return (f"before {self.before.isoformat()} and from "
                    f"{self.after.isoformat()}")
        if self.before is not None:
            return f"before {self.before.isoformat()}"
        return f"from {self.after.isoformat() if self.after else '?'}"

    def to_dict(self) -> dict[str, str]:
        """JSON-plain form, for the result document."""
        return {
            "pair": self.pair,
            "before": self.before.isoformat() if self.before else "",
            "after": self.after.isoformat() if self.after else "",
            "ruling": self.ruling,
            "why": self.why,
            "window": self.describe(),
        }


#: Every exclusion in force. Adding one is a checkpoint decision, exactly like
#: a change to universe membership (pre-reg #9): the loop may enforce this
#: table, never extend it.
EXCLUSIONS: Final[tuple[Exclusion, ...]] = (
    Exclusion(
        "AUDUSD",
        before=dt.date(2011, 1, 1),
        ruling="R1",
        why=("crossed-quote corruption in two bounded episodes, 2007-04 to "
             "2008-09 and 2009-04 to 2010-10, rejected most of four years and "
             "left what survived a biased sample of the window rather than "
             "merely a thin one"),
    ),
)


def exclusion_for(pair: str) -> Exclusion | None:
    """The exclusion in force for ``pair``, or ``None``."""
    for entry in EXCLUSIONS:
        if entry.pair == pair:
            return entry
    return None


def excluded_pairs() -> tuple[str, ...]:
    """Every pair carrying an exclusion, sorted."""
    return tuple(sorted(e.pair for e in EXCLUSIONS))


def is_excluded(pair: str, date: object) -> bool:
    """True when ``pair`` on ``date`` falls inside an exclusion window."""
    entry = exclusion_for(pair)
    return bool(entry and entry.covers(as_date(date)))


def assert_not_excluded(pair: str, date: object, context: str = "") -> None:
    """Raise :class:`PairExcluded` when this pair-date is excluded.

    Args:
        pair: The pair being read.
        date: Anything :func:`research.seal.as_date` accepts.
        context: What was doing the reading, quoted in the message.

    Raises:
        PairExcluded: With reason ``PAIR_EXCLUDED_WINDOW``.
    """
    entry = exclusion_for(pair)
    if entry is None:
        return
    stamp = as_date(date)
    if not entry.covers(stamp):
        return
    where = f" ({context})" if context else ""
    raise PairExcluded(
        f"{pair} {stamp.isoformat()} is inside the exclusion window "
        f"{entry.describe()}{where}. Ruling {entry.ruling}: {entry.why}. Ask "
        "for the permitted part of the range explicitly and report what was "
        "dropped.")


def permitted_dates(pair: str, dates: Iterable[object]) -> list[str]:
    """The subset of ``dates`` this pair may be read over, as ``YYYY-MM-DD``.

    The counterpart to :func:`assert_not_excluded`: a caller that wants what it
    is allowed to have asks for it here, then reports the difference. Returned
    sorted and de-duplicated so a caller's own ordering cannot leak into a
    result hash.
    """
    entry = exclusion_for(pair)
    out = {as_date(d) for d in dates}
    if entry is not None:
        out = {d for d in out if not entry.covers(d)}
    return [d.isoformat() for d in sorted(out)]


def split_dates(pair: str,
                dates: Iterable[object]) -> tuple[list[str], list[str]]:
    """Split ``dates`` into ``(permitted, excluded)``, both sorted.

    Reports need both halves: the second is the number R1 requires them to
    state, and deriving it here keeps that number from being typed by hand.
    """
    entry = exclusion_for(pair)
    stamps = sorted({as_date(d) for d in dates})
    if entry is None:
        return [d.isoformat() for d in stamps], []
    permitted = [d.isoformat() for d in stamps if not entry.covers(d)]
    excluded = [d.isoformat() for d in stamps if entry.covers(d)]
    return permitted, excluded


def clamp_window(pair: str, start: object,
                 end: object) -> tuple[dt.date, dt.date] | None:
    """The permitted sub-window of ``[start, end]``, or ``None`` if empty.

    Only handles the leading-``before`` form, which is the only shape in force;
    a two-sided exclusion would split a range into two windows, and this
    returns ``None`` rather than silently serving one of them.
    """
    first, last = as_date(start), as_date(end)
    entry = exclusion_for(pair)
    if entry is None:
        return (first, last) if first <= last else None
    if entry.after is not None and entry.before is not None:
        return None
    if entry.before is not None:
        first = max(first, entry.before)
    if entry.after is not None:
        last = min(last, entry.after - dt.timedelta(days=1))
    return (first, last) if first <= last else None


def summarise(pairs: Sequence[str]) -> list[dict[str, str]]:
    """Every exclusion touching ``pairs``, for a result document."""
    wanted = set(pairs)
    return [e.to_dict() for e in EXCLUSIONS if e.pair in wanted]


def iter_exclusions() -> Iterator[Exclusion]:
    """Every exclusion in force, in declaration order."""
    yield from EXCLUSIONS
