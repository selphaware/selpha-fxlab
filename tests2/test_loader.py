"""The loader: two modes, refusal before disk, and an access record."""

from __future__ import annotations

import pathlib

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research import loader as loader_mod
from research.seal import SealBreach


@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway project root with both data roots present."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    (tmp_path / "data" / "live_week").mkdir(parents=True)
    return tmp_path


def test_scoring_loader_refuses_a_sealed_date(base: pathlib.Path) -> None:
    """And refuses it with the named reason."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    with pytest.raises(SealBreach) as caught:
        loader.load_ticks("EURUSD", ["2025-03-01"])
    assert "HOLDOUT_SEALED" in str(caught.value)


def test_refusal_does_not_depend_on_the_data_being_absent(
        base: pathlib.Path) -> None:
    """The date is checked before the filesystem is touched.

    A seal that only works while the data is missing stops working the moment
    somebody downloads it, which is the one moment it matters.
    """
    partition = (base / "data" / "research" / "ticks" / "pair=EURUSD"
                 / "date=2025-06-02")
    partition.mkdir(parents=True)
    pq.write_table(pa.table({"pair": ["EURUSD"]}), partition / "h.parquet")
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    with pytest.raises(SealBreach):
        loader.load_ticks("EURUSD", ["2025-06-02"])


def test_scoring_loader_allows_the_research_window(base: pathlib.Path) -> None:
    """An unsealed date is recorded and served (empty here, but not refused)."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    frame = loader.load_ticks("EURUSD", ["2025-02-28"])
    assert len(frame) == 0
    assert loader.access.dates == {"2025-02-28"}
    assert loader.access.scorable is True


def test_scoring_root_cannot_be_moved_outside_the_research_tree(
        base: pathlib.Path) -> None:
    """Pointing scoring mode at the quarantine is refused by name."""
    with pytest.raises(loader_mod.LoaderRefusal) as caught:
        loader_mod.ResearchLoader(loader_mod.MODE_SCORING,
                                  root="data/live_week", base=base)
    assert caught.value.reason == loader_mod.SCORING_ROOT_NOT_RESEARCH


def test_mechanical_mode_requires_an_allowlisted_root(
        base: pathlib.Path) -> None:
    """Anything outside the allowlist is refused, including a bare default."""
    with pytest.raises(loader_mod.LoaderRefusal) as caught:
        loader_mod.ResearchLoader(loader_mod.MODE_MECHANICAL, base=base)
    assert caught.value.reason == loader_mod.MECHANICAL_ROOT_NOT_ALLOWED

    with pytest.raises(loader_mod.LoaderRefusal):
        loader_mod.ResearchLoader(loader_mod.MODE_MECHANICAL,
                                  root="data/elsewhere", base=base)


def test_mechanical_mode_serves_sealed_dates_but_is_never_scorable(
        base: pathlib.Path) -> None:
    """The quarantine exists for pipeline checks and nothing else."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_MECHANICAL,
                                       root="data/live_week", base=base)
    loader.load_ticks("EURUSD", ["2026-08-10"])
    assert loader.access.scorable is False
    assert loader.access.sealed_dates() == ["2026-08-10"]


def test_unknown_mode_is_refused(base: pathlib.Path) -> None:
    """There are two modes. A third would be a policy change."""
    with pytest.raises(loader_mod.LoaderRefusal) as caught:
        loader_mod.ResearchLoader("whatever", base=base)
    assert caught.value.reason == loader_mod.UNKNOWN_LOADER_MODE


def test_canary_reports_a_refusal(base: pathlib.Path) -> None:
    """The gate's canary must see the refusal it depends on."""
    refused, message = loader_mod.canary(base=base)
    assert refused is True
    assert "HOLDOUT_SEALED" in message


def test_sealed_parquet_under_finds_partitioned_and_row_dates(
        base: pathlib.Path) -> None:
    """Tick partitions are read from the path, bar files from their rows."""
    root = base / "data" / "research"
    partition = root / "ticks" / "pair=EURUSD" / "date=2025-06-02"
    partition.mkdir(parents=True)
    pq.write_table(pa.table({"pair": ["EURUSD"]}), partition / "h.parquet")

    clean = root / "ticks" / "pair=EURUSD" / "date=2024-06-02"
    clean.mkdir(parents=True)
    pq.write_table(pa.table({"pair": ["EURUSD"]}), clean / "h.parquet")

    offenders = loader_mod.sealed_parquet_under(root)
    assert len(offenders) == 1
    assert "date=2025-06-02" in offenders[0]


def test_access_record_serialises_deterministically(base: pathlib.Path) -> None:
    """Sets become sorted lists, so the result hash does not wander."""
    loader = loader_mod.ResearchLoader(loader_mod.MODE_SCORING, base=base)
    loader.load_ticks("EURUSD", ["2024-01-03", "2024-01-02"])
    payload = loader.access.to_dict()
    assert payload["dates"] == ["2024-01-02", "2024-01-03"]
    assert payload["pairs"] == ["EURUSD"]
