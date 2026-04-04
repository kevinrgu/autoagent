"""Structured experiment logging with ATIF sidecar index."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ExperimentEntry:
    version: str
    parent: Optional[str]
    schema_version: int
    editable_tree_hash: str
    fixed_tree_hash: str
    contract_version: str
    container_image_digest: str
    scores: dict[str, Any]
    cost_usd: float
    tokens_used: int
    duration_sec: float
    trace_id: str
    atif_version: str
    trajectory_uri: str
    delta: dict[str, Any]
    root_cause: str
    meta_reasoning: str
    network_profile: str
    evaluator_digest: str
    timestamp: str
    meta: dict[str, Any] = field(default_factory=dict)


class ExperimentLogger:
    """Append-only JSONL logger for experiment entries."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, entry: ExperimentEntry) -> None:
        """Append an entry to the JSONL log file."""
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def read_all(self) -> list[ExperimentEntry]:
        """Read all entries from the log file.

        Returns:
            List of ExperimentEntry objects, or empty list if file does not exist.
        """
        if not self._path.exists():
            return []
        entries = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    entries.append(ExperimentEntry(**data))
        return entries
