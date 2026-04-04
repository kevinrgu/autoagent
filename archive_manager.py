"""Evolutionary archive with exploit/explore pools."""
import hashlib
import json
import shutil
import tarfile
from pathlib import Path


class ArchiveManager:
    """Manages versioned snapshots of an editable directory in exploit/explore pools.

    Args:
        archive_dir: Directory where tarballs and index.jsonl are stored.
        editable_dir: Directory being snapshotted/restored.
        cap: Total max versions across both pools.
        exploit_cap: Max versions in exploit pool.
        explore_cap: Max versions in explore pool.
        explore_protected_min: Minimum explore entries that survive cap enforcement.
    """

    def __init__(
        self,
        archive_dir: Path,
        editable_dir: Path,
        cap: int = 50,
        exploit_cap: int = 30,
        explore_cap: int = 20,
        explore_protected_min: int = 10,
    ) -> None:
        self.archive_dir = Path(archive_dir)
        self.editable_dir = Path(editable_dir)
        self.cap = cap
        self.exploit_cap = exploit_cap
        self.explore_cap = explore_cap
        self.explore_protected_min = explore_protected_min
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(
        self,
        version: str,
        scores: dict,
        pool: str = "exploit",
        generation: int = 0,
        stale_generations: int = 0,
    ) -> str:
        """Tarball editable_dir, record metadata, return tree_hash.

        Args:
            version: Unique version identifier.
            scores: Dict of suite→score floats.
            pool: "exploit" or "explore".
            generation: Current generation counter.
            stale_generations: How many generations without improvement.

        Returns:
            SHA-256 hex digest of directory contents.
        """
        tree_hash = self._hash_dir(self.editable_dir)
        tarball_path = self.archive_dir / f"{version}.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            tar.add(self.editable_dir, arcname=".")

        entry = {
            "version": version,
            "pool": pool,
            "scores": scores,
            "tree_hash": tree_hash,
            "generation": generation,
            "stale_generations": stale_generations,
        }
        index_path = self.archive_dir / "index.jsonl"
        with index_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

        self._enforce_cap()
        return tree_hash

    def restore(self, version: str) -> None:
        """Extract the tarball for version back into editable_dir.

        Args:
            version: Version identifier to restore.
        """
        tarball_path = self.archive_dir / f"{version}.tar.gz"
        if not tarball_path.exists():
            raise FileNotFoundError(f"No tarball for version {version!r}")

        # Clear editable_dir then extract
        shutil.rmtree(self.editable_dir)
        self.editable_dir.mkdir(parents=True)

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(self.editable_dir)

    def list_versions(self, pool: str | None = None) -> list[str]:
        """Return list of archived version identifiers.

        Args:
            pool: If given, filter to "exploit" or "explore". Otherwise return all.

        Returns:
            List of version strings in insertion order.
        """
        entries = self._read_index()
        if pool is not None:
            entries = [e for e in entries if e["pool"] == pool]
        return [e["version"] for e in entries]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enforce_cap(self) -> None:
        """Remove oldest entries when per-pool or total cap is exceeded.

        For the explore pool, never remove entries below explore_protected_min.
        """
        entries = self._read_index()

        # Enforce per-pool caps first
        exploit_entries = [e for e in entries if e["pool"] == "exploit"]
        explore_entries = [e for e in entries if e["pool"] == "explore"]

        # Trim exploit (oldest first, no protected minimum)
        while len(exploit_entries) > self.exploit_cap:
            removed = exploit_entries.pop(0)
            self._remove_version(removed["version"])

        # Trim explore (respect protected minimum)
        while len(explore_entries) > self.explore_cap and len(explore_entries) > self.explore_protected_min:
            removed = explore_entries.pop(0)
            self._remove_version(removed["version"])

        # Enforce global cap on combined list (oldest first, explore protected)
        combined = exploit_entries + explore_entries
        combined.sort(key=lambda e: entries.index(e) if e in entries else 0)

        while len(combined) > self.cap:
            # Find oldest non-protected entry to remove
            explore_count = sum(1 for e in combined if e["pool"] == "explore")
            removed = None
            for e in combined:
                if e["pool"] == "exploit":
                    removed = e
                    break
                if e["pool"] == "explore" and explore_count > self.explore_protected_min:
                    removed = e
                    break
            if removed is None:
                break
            combined.remove(removed)
            self._remove_version(removed["version"])

        # Rewrite index with surviving entries
        surviving = {e["version"] for e in combined}
        all_entries = self._read_index()
        kept = [e for e in all_entries if e["version"] in surviving]
        self._write_index(kept)

    def _remove_version(self, version: str) -> None:
        """Delete tarball for a version (index rewrite is done by caller)."""
        tarball = self.archive_dir / f"{version}.tar.gz"
        if tarball.exists():
            tarball.unlink()

    def _hash_dir(self, path: Path) -> str:
        """Compute deterministic SHA-256 hash over all files in a directory.

        Args:
            path: Directory to hash.

        Returns:
            64-character hex digest.
        """
        hasher = hashlib.sha256()
        # Sort for determinism
        for file_path in sorted(Path(path).rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(path)
                hasher.update(str(rel).encode())
                hasher.update(file_path.read_bytes())
        return hasher.hexdigest()

    def _read_index(self) -> list[dict]:
        """Read and parse index.jsonl into a list of entry dicts."""
        index_path = self.archive_dir / "index.jsonl"
        if not index_path.exists():
            return []
        lines = index_path.read_text().splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _write_index(self, entries: list[dict]) -> None:
        """Overwrite index.jsonl with given entries."""
        index_path = self.archive_dir / "index.jsonl"
        with index_path.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
