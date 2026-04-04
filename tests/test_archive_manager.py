"""Tests for ArchiveManager: evolutionary archive with exploit/explore pools."""
import json
import tempfile
from pathlib import Path

import pytest

from archive_manager import ArchiveManager


@pytest.fixture
def tmp_dirs():
    """Provide temporary archive_dir and editable_dir."""
    with tempfile.TemporaryDirectory() as base:
        archive_dir = Path(base) / "archive"
        editable_dir = Path(base) / "editable"
        archive_dir.mkdir()
        editable_dir.mkdir()
        yield archive_dir, editable_dir


def _seed_editable(editable_dir: Path, content: dict[str, str]) -> None:
    """Write {filename: text} into editable_dir."""
    for name, text in content.items():
        (editable_dir / name).write_text(text)


def _read_index(archive_dir: Path) -> list[dict]:
    index_path = archive_dir / "index.jsonl"
    if not index_path.exists():
        return []
    return [json.loads(line) for line in index_path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# test_snapshot_and_restore
# ---------------------------------------------------------------------------

def test_snapshot_and_restore(tmp_dirs):
    """snapshot v1 → modify editable → restore v1 → original content back."""
    archive_dir, editable_dir = tmp_dirs
    _seed_editable(editable_dir, {"a.txt": "hello", "b.txt": "world"})

    mgr = ArchiveManager(archive_dir, editable_dir)
    tree_hash = mgr.snapshot("v1", scores={"smoke": 1.0})

    # Mutate editable_dir
    (editable_dir / "a.txt").write_text("mutated")
    (editable_dir / "c.txt").write_text("new file")

    mgr.restore("v1")

    assert (editable_dir / "a.txt").read_text() == "hello"
    assert (editable_dir / "b.txt").read_text() == "world"
    assert not (editable_dir / "c.txt").exists()

    # Index should have one entry
    entries = _read_index(archive_dir)
    assert len(entries) == 1
    assert entries[0]["version"] == "v1"
    assert entries[0]["tree_hash"] == tree_hash


# ---------------------------------------------------------------------------
# test_archive_cap
# ---------------------------------------------------------------------------

def test_archive_cap(tmp_dirs):
    """Create 5 snapshots with cap=3 → only 3 remain."""
    archive_dir, editable_dir = tmp_dirs
    mgr = ArchiveManager(archive_dir, editable_dir, cap=3, exploit_cap=3, explore_cap=3)

    for i in range(1, 6):
        _seed_editable(editable_dir, {"f.txt": f"v{i}"})
        mgr.snapshot(f"v{i}", scores={"smoke": 1.0}, pool="exploit")

    versions = mgr.list_versions()
    assert len(versions) == 3


# ---------------------------------------------------------------------------
# test_exploit_explore_pools
# ---------------------------------------------------------------------------

def test_exploit_explore_pools(tmp_dirs):
    """Snapshot to different pools → list_versions filters correctly."""
    archive_dir, editable_dir = tmp_dirs
    mgr = ArchiveManager(archive_dir, editable_dir, cap=50, exploit_cap=30, explore_cap=20)

    _seed_editable(editable_dir, {"x.txt": "exploit1"})
    mgr.snapshot("e1", scores={"smoke": 1.0}, pool="exploit")

    _seed_editable(editable_dir, {"x.txt": "exploit2"})
    mgr.snapshot("e2", scores={"smoke": 0.9}, pool="exploit")

    _seed_editable(editable_dir, {"x.txt": "explore1"})
    mgr.snapshot("x1", scores={"smoke": 0.5}, pool="explore")

    all_versions = mgr.list_versions()
    exploit_versions = mgr.list_versions(pool="exploit")
    explore_versions = mgr.list_versions(pool="explore")

    assert set(all_versions) == {"e1", "e2", "x1"}
    assert set(exploit_versions) == {"e1", "e2"}
    assert set(explore_versions) == {"x1"}


# ---------------------------------------------------------------------------
# test_explore_protected_min
# ---------------------------------------------------------------------------

def test_explore_protected_min(tmp_dirs):
    """With explore_protected_min=2 and explore_cap=2, adding 3 explore entries
    must keep at least 2 explore entries after cap enforcement."""
    archive_dir, editable_dir = tmp_dirs
    mgr = ArchiveManager(
        archive_dir,
        editable_dir,
        cap=50,
        exploit_cap=30,
        explore_cap=2,
        explore_protected_min=2,
    )

    for i in range(1, 4):
        _seed_editable(editable_dir, {"g.txt": f"explore{i}"})
        mgr.snapshot(f"xp{i}", scores={"smoke": 0.5}, pool="explore")

    explore_versions = mgr.list_versions(pool="explore")
    assert len(explore_versions) >= 2


# ---------------------------------------------------------------------------
# test_hash_dir_deterministic
# ---------------------------------------------------------------------------

def test_hash_dir_deterministic(tmp_dirs):
    """Same directory content → same hash regardless of call order."""
    archive_dir, editable_dir = tmp_dirs
    mgr = ArchiveManager(archive_dir, editable_dir)

    _seed_editable(editable_dir, {"a.txt": "foo", "b.txt": "bar"})
    h1 = mgr._hash_dir(editable_dir)
    h2 = mgr._hash_dir(editable_dir)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_hash_dir_changes_on_content_change(tmp_dirs):
    """Different content → different hash."""
    archive_dir, editable_dir = tmp_dirs
    mgr = ArchiveManager(archive_dir, editable_dir)

    _seed_editable(editable_dir, {"a.txt": "foo"})
    h1 = mgr._hash_dir(editable_dir)

    (editable_dir / "a.txt").write_text("bar")
    h2 = mgr._hash_dir(editable_dir)

    assert h1 != h2
