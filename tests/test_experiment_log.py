"""Tests for structured experiment logging."""

import json
import tempfile
from pathlib import Path

from experiment_log import ExperimentEntry, ExperimentLogger


def _make_entry(**kwargs) -> ExperimentEntry:
    defaults = dict(
        version="v0.1.0",
        parent=None,
        schema_version=1,
        editable_tree_hash="abc123",
        fixed_tree_hash="def456",
        contract_version="1.0",
        container_image_digest="sha256:deadbeef",
        scores={"pass@1": 0.85},
        cost_usd=0.05,
        tokens_used=1500,
        duration_sec=12.3,
        trace_id="trace-001",
        atif_version="0.3.0",
        trajectory_uri="s3://bucket/traj/001.jsonl",
        delta={"agent.py": "+10 -2"},
        root_cause="improved retry logic",
        meta_reasoning="higher pass@1 expected from retry",
        network_profile="none",
        evaluator_digest="sha256:cafebabe",
        timestamp="2026-04-04T00:00:00Z",
    )
    defaults.update(kwargs)
    return ExperimentEntry(**defaults)


def test_entry_has_atif_connection_keys():
    entry = _make_entry()
    assert hasattr(entry, "trace_id")
    assert hasattr(entry, "trajectory_uri")
    assert entry.trace_id == "trace-001"
    assert entry.trajectory_uri == "s3://bucket/traj/001.jsonl"


def test_append_and_read_back():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        logger = ExperimentLogger(path)
        entry = _make_entry()
        logger.append(entry)

        entries = logger.read_all()
        assert len(entries) == 1
        assert entries[0].version == "v0.1.0"
        assert entries[0].scores == {"pass@1": 0.85}
        assert entries[0].trace_id == "trace-001"
    finally:
        path.unlink(missing_ok=True)


def test_read_all_returns_empty_list_for_missing_file():
    logger = ExperimentLogger(Path("/tmp/nonexistent_experiment_log_xyz.jsonl"))
    assert logger.read_all() == []
