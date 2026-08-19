"""The research data loader: two modes, one seal, and a record of what it read.

Every read of stored data by research code goes through here, for three
reasons:

1. **The seal.** In ``scoring`` mode a request for any date on or after the
   holdout cutoff is refused before a single file is touched, with the named
   reason ``HOLDOUT_SEALED``. Checking the date first and the filesystem second
   matters: a refusal that depends on the data being absent stops refusing the
   moment somebody downloads it.
2. **The quarantine.** ``mechanical`` mode exists so pipeline checks can run
   against the Phase 1 live week, which is 2026 data and therefore inside the
   seal (pre-reg #2 allows exactly this). It may only point at the allowlist in
   :mod:`research.seal`, and :attr:`AccessRecord.scorable` is ``False`` for it,
   which the experiment runner turns into a hard refusal to emit a scorecard.
3. **The audit trail.** The loader records every pair and date it actually
   served. That record is written into the result file, and the research gate
   asserts a scored result touched nothing sealed. An access log the code
   cannot forget to write beats a rule the code is supposed to remember.
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Any, Final, Iterable, Sequence

import pyarrow.parquet as pq

from fxlab.ingestion.bars import bars_path, offset_alias
from fxlab.ingestion.store import read_ticks
from research.seal import (MECHANICAL_ALLOWLIST, RESEARCH_DATA_DIR, SealBreach,
                           as_date, assert_not_sealed, is_sealed)

_LOG: Final[logging.Logger] = logging.getLogger("research.loader")

#: Reads that may inform a scored result. Refuses the sealed period outright.
MODE_SCORING: Final[str] = "scoring"

#: Pipeline/mechanical checks only. Allowlisted roots, never scorable.
MODE_MECHANICAL: Final[str] = "mechanical"

MODES: Final[tuple[str, ...]] = (MODE_SCORING, MODE_MECHANICAL)

#: Named refusal reasons, emitted verbatim like the Phase 1 tokens.
MECHANICAL_ROOT_NOT_ALLOWED: Final[str] = "MECHANICAL_ROOT_NOT_ALLOWED"
SCORING_ROOT_NOT_RESEARCH: Final[str] = "SCORING_ROOT_NOT_RESEARCH"
UNKNOWN_LOADER_MODE: Final[str] = "UNKNOWN_LOADER_MODE"


class LoaderRefusal(Exception):
    """A loader was constructed in a way the seal does not permit.

    Attributes:
        reason: One of the named tokens above.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclasses.dataclass
class AccessRecord:
    """What a loader was actually asked for, accumulated as it serves reads.

    Attributes:
        mode: The loader mode.
        root: Absolute data root, as a POSIX string.
        scorable: False for mechanical mode; a result built on it may not score.
        pairs: Every pair served.
        dates: Every ``YYYY-MM-DD`` served.
        timeframes: Every bar timeframe served.
        files: Every file opened, project-relative where possible.
    """

    mode: str
    root: str
    scorable: bool
    pairs: set[str] = dataclasses.field(default_factory=set)
    dates: set[str] = dataclasses.field(default_factory=set)
    timeframes: set[str] = dataclasses.field(default_factory=set)
    files: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise deterministically: sets become sorted lists."""
        return {
            "mode": self.mode,
            "root": self.root,
            "scorable": self.scorable,
            "pairs": sorted(self.pairs),
            "dates": sorted(self.dates),
            "timeframes": sorted(self.timeframes),
            "files": sorted(self.files),
        }

    def sealed_dates(self) -> list[str]:
        """Dates served that fall inside the seal."""
        return sorted(d for d in self.dates if is_sealed(d))


def project_root() -> pathlib.Path:
    """The repository root, derived from this file rather than configured."""
    return pathlib.Path(__file__).resolve().parents[1]


class ResearchLoader:
    """Read stored ticks and bars under a declared mode.

    Args:
        mode: ``scoring`` or ``mechanical``.
        root: Data root. Defaults to ``data/research`` for scoring mode; must be
            given, and allowlisted, for mechanical mode.
        base: Project root; injected in tests, derived otherwise.

    Raises:
        LoaderRefusal: For an unknown mode, a scoring root outside the research
            tree, or a mechanical root outside the allowlist.
    """

    def __init__(self, mode: str, root: str | pathlib.Path | None = None,
                 base: pathlib.Path | None = None) -> None:
        self._base = pathlib.Path(base) if base is not None else project_root()
        if mode not in MODES:
            raise LoaderRefusal(UNKNOWN_LOADER_MODE,
                                f"{mode!r}; known modes are {list(MODES)}")
        self.mode = mode
        self.root = self._resolve_root(mode, root)
        self.access = AccessRecord(mode=mode, root=self.root.as_posix(),
                                   scorable=(mode == MODE_SCORING))

    # -- construction -----------------------------------------------------

    def _resolve_root(self, mode: str, root: str | pathlib.Path | None) -> pathlib.Path:
        """Resolve and police the data root for this mode."""
        if mode == MODE_SCORING:
            resolved = (pathlib.Path(root) if root is not None
                        else self._base / RESEARCH_DATA_DIR)
            resolved = self._absolute(resolved)
            allowed = self._absolute(self._base / RESEARCH_DATA_DIR)
            if not _is_within(resolved, allowed):
                raise LoaderRefusal(
                    SCORING_ROOT_NOT_RESEARCH,
                    f"{resolved.as_posix()} is outside {allowed.as_posix()}; "
                    "scoring reads only the research tree")
            return resolved

        if root is None:
            raise LoaderRefusal(
                MECHANICAL_ROOT_NOT_ALLOWED,
                "mechanical mode has no default root; name one of "
                f"{list(MECHANICAL_ALLOWLIST)} explicitly")
        resolved = self._absolute(pathlib.Path(root))
        for entry in MECHANICAL_ALLOWLIST:
            if _is_within(resolved, self._absolute(self._base / entry)):
                return resolved
        raise LoaderRefusal(
            MECHANICAL_ROOT_NOT_ALLOWED,
            f"{resolved.as_posix()} is not under any of "
            f"{[str(e) for e in MECHANICAL_ALLOWLIST]}")

    def _absolute(self, path: pathlib.Path) -> pathlib.Path:
        """Absolute, normalised, without requiring the path to exist."""
        candidate = path if path.is_absolute() else (self._base / path)
        return pathlib.Path(candidate).resolve()

    # -- reads ------------------------------------------------------------

    def check_date(self, date: Any, context: str = "") -> str:
        """Police one date and record it. Returns the ``YYYY-MM-DD`` form.

        Raises:
            SealBreach: In scoring mode, for any date at or after the cutoff.
        """
        text = as_date(date).isoformat()
        if self.mode == MODE_SCORING:
            assert_not_sealed(text, context or f"{self.mode} loader")
        self.access.dates.add(text)
        return text

    def load_ticks(self, pair: str, dates: Sequence[str] | None = None) -> Any:
        """Read stored ticks for one pair.

        Args:
            pair: Pair name, e.g. ``EURUSD``.
            dates: ``YYYY-MM-DD`` partitions to read. ``None`` reads every date
                present under the root -- which in scoring mode is still policed
                date by date, so a stray sealed partition refuses rather than
                loads.

        Returns:
            A time-sorted pandas DataFrame in the Phase 1 tick schema.
        """
        self.access.pairs.add(pair)
        wanted = list(dates) if dates is not None else self._present_dates(pair)
        checked = [self.check_date(d, f"load_ticks({pair})") for d in wanted]
        for date in checked:
            path_dir = self.root / "ticks" / f"pair={pair}" / f"date={date}"
            for path in sorted(path_dir.glob("*.parquet")):
                self.access.files.append(self._rel(path))
        frame = read_ticks(self.root, pair=pair, dates=checked)
        _LOG.debug("loaded %d ticks for %s over %d date(s) in %s mode",
                   len(frame), pair, len(checked), self.mode)
        return frame

    def load_bars(self, pair: str, timeframe: str) -> Any:
        """Read a stored bar table for one pair and timeframe.

        In scoring mode the table's own timestamps are policed after reading,
        so a bar file that silently extends past the cutoff refuses rather than
        being trimmed. Trimming would hide the fact that sealed data reached
        the research tree at all.
        """
        alias = offset_alias(timeframe)
        self.access.pairs.add(pair)
        self.access.timeframes.add(alias)
        path = bars_path(self.root, pair, alias)
        if not path.exists():
            raise FileNotFoundError(f"no bars at {path}")
        self.access.files.append(self._rel(path))
        frame = pq.read_table(path).to_pandas()
        if len(frame):
            stamps = frame["ts"]
            dates = sorted({d.isoformat() for d in stamps.dt.date.unique()})
            for date in dates:
                self.check_date(date, f"load_bars({pair}, {alias})")
        return frame

    # -- helpers ----------------------------------------------------------

    def _present_dates(self, pair: str) -> list[str]:
        """Date partitions present on disk for a pair."""
        base = self.root / "ticks" / f"pair={pair}"
        if not base.is_dir():
            return []
        return sorted(p.name.split("=", 1)[1] for p in base.iterdir()
                      if p.is_dir() and p.name.startswith("date="))

    def _rel(self, path: pathlib.Path) -> str:
        """Project-relative POSIX path where possible, absolute otherwise."""
        try:
            return path.resolve().relative_to(self._base).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def _is_within(path: pathlib.Path, parent: pathlib.Path) -> bool:
    """True if ``path`` is ``parent`` or lies inside it."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def canary(base: pathlib.Path | None = None,
           date: str = "2025-03-01") -> tuple[bool, str]:
    """Ask a scoring loader for a sealed date and report what happened.

    The research gate calls this. It is deliberately a function in the judged
    surface rather than gate-side code, so that the refusal being tested is the
    one research code would actually hit.

    Returns:
        ``(refused, detail)`` -- ``refused`` is True only if the loader raised
        :class:`~research.seal.SealBreach` for the sealed date.
    """
    loader = ResearchLoader(MODE_SCORING, base=base)
    try:
        loader.load_ticks("EURUSD", [date])
    except SealBreach as exc:
        return True, str(exc)
    except Exception as exc:  # noqa: BLE001 - any other failure is not a refusal
        return False, f"raised {type(exc).__name__} instead of SealBreach: {exc}"
    return False, f"served {date} without refusing"


def sealed_parquet_under(root: pathlib.Path) -> list[str]:
    """Every Parquet under ``root`` whose partition or contents are sealed.

    Partition dates are checked from the path, which is cheap; bar files carry
    no date partition, so those are opened and their timestamps checked.
    """
    offenders: list[str] = []
    if not root.exists():
        return offenders
    for path in sorted(root.rglob("*.parquet")):
        partition = _partition_date(path)
        if partition is not None:
            if is_sealed(partition):
                offenders.append(f"{path.as_posix()} (partition {partition})")
            continue
        for date in _table_dates(path):
            if is_sealed(date):
                offenders.append(f"{path.as_posix()} (row date {date})")
                break
    return offenders


def _partition_date(path: pathlib.Path) -> str | None:
    """The ``date=`` partition of a stored path, if it has one."""
    for part in path.parts:
        if part.startswith("date="):
            return part.split("=", 1)[1]
    return None


def _table_dates(path: pathlib.Path) -> Iterable[str]:
    """Distinct UTC dates present in a table's ``ts`` column."""
    try:
        table = pq.read_table(path, columns=["ts"])
    except Exception:  # noqa: BLE001 - an unreadable file is not a seal breach
        return []
    if table.num_rows == 0:
        return []
    frame = table.to_pandas()
    return sorted({d.isoformat() for d in frame["ts"].dt.date.unique()})
