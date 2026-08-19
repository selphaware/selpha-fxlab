"""The ledger: append-only, start before result, malformed lines visible."""

from __future__ import annotations

import json
import pathlib

import pytest

from research import ledger


def test_start_record_carries_every_field_the_gate_needs(
        tmp_path: pathlib.Path) -> None:
    """Missing any of these makes an entry unauditable."""
    record = ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                                 config_sha256="abc", seed=7, mode="scoring")
    for field in ledger.START_FIELDS:
        assert field in record
    assert record["record"] == "start"


def test_records_append_and_read_back_in_order(tmp_path: pathlib.Path) -> None:
    """Order in the file is the order of events."""
    ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                        config_sha256="abc", seed=7, mode="scoring")
    ledger.append_end(tmp_path, experiment_id="E1", status="ok",
                      result_files=["experiments/E1/result.json"],
                      result_hash="deadbeef", scored=False)
    records = ledger.read(tmp_path)
    assert [r["record"] for r in records] == ["start", "end"]
    assert ledger.starts_for(records, "E1")
    assert ledger.ends_for(records, "E1")


def test_a_subset_rerun_class_demands_its_hash_up_front(
        tmp_path: pathlib.Path) -> None:
    """Ruling D5: the subset is fixed before results exist, or not at all."""
    with pytest.raises(ValueError):
        ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                            config_sha256="abc", seed=7, mode="scoring",
                            rerun_class=ledger.RERUN_SUBSET)
    ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                        config_sha256="abc", seed=7, mode="scoring",
                        rerun_class=ledger.RERUN_SUBSET, subset_sha256="f00d")


def test_unknown_rerun_class_is_refused(tmp_path: pathlib.Path) -> None:
    """Two classes exist; a third is a policy change."""
    with pytest.raises(ValueError):
        ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                            config_sha256="abc", seed=7, mode="scoring",
                            rerun_class="whenever")


def test_malformed_lines_are_reported_not_skipped(
        tmp_path: pathlib.Path) -> None:
    """A corrupt ledger must not read as a short one."""
    ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                        config_sha256="abc", seed=7, mode="scoring")
    path = ledger.ledger_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    records = ledger.read(tmp_path)
    assert records[-1]["record"] == "malformed"


def test_trial_count_counts_starts_including_abandoned_ones(
        tmp_path: pathlib.Path) -> None:
    """Pre-reg #10 counts what was tried, not what produced a result."""
    for index in range(3):
        ledger.append_start(tmp_path, experiment_id=f"E{index}", taskcard="T4",
                            config_sha256="abc", seed=index, mode="scoring")
    ledger.append_start(tmp_path, experiment_id="X", taskcard="T5",
                        config_sha256="abc", seed=9, mode="scoring")
    records = ledger.read(tmp_path)
    assert ledger.trial_count(records) == 4
    assert ledger.trial_count(records, taskcard="T4") == 3


def test_records_are_written_as_one_json_object_per_line(
        tmp_path: pathlib.Path) -> None:
    """JSON Lines, so an append cannot corrupt what is already there."""
    ledger.append_start(tmp_path, experiment_id="E1", taskcard="T1",
                        config_sha256="abc", seed=7, mode="scoring")
    text = ledger.ledger_path(tmp_path).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text.strip())["experiment_id"] == "E1"


def test_code_commit_reports_dirtiness_rather_than_refusing(
        tmp_path: pathlib.Path) -> None:
    """Outside a repository the commit is unknown and the tree counts as dirty."""
    commit, dirty = ledger.code_commit(tmp_path)
    assert isinstance(commit, str)
    assert isinstance(dirty, bool)
