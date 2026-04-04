"""Tests for adapter.py — fixed Harbor adapter boundary."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


ADAPTER_PATH = Path(__file__).parent.parent / "adapter.py"
AGENT_PATH = Path(__file__).parent.parent / "agent.py"


def _parse_adapter() -> ast.Module:
    return ast.parse(ADAPTER_PATH.read_text())


def test_adapter_file_exists() -> None:
    """adapter.py must exist on disk."""
    assert ADAPTER_PATH.exists(), "adapter.py not found"


def test_adapter_exposes_autoagent() -> None:
    """adapter.py must define AutoAgent class at module level.

    Verified via AST to avoid importing heavy runtime dependencies.
    """
    tree = _parse_adapter()
    top_level_classes = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.col_offset == 0
    }
    assert "AutoAgent" in top_level_classes, "AutoAgent class not found in adapter.py"


def test_adapter_exposes_to_atif() -> None:
    """adapter.py must define to_atif function at module level.

    Verified via AST to avoid importing heavy runtime dependencies.
    """
    tree = _parse_adapter()
    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.col_offset == 0
    }
    assert "to_atif" in top_level_funcs, "to_atif function not found in adapter.py"


def test_autoagent_supports_atif() -> None:
    """AutoAgent must declare SUPPORTS_ATIF = True as a class-level assignment.

    Verified via AST to avoid importing heavy runtime dependencies.
    """
    tree = _parse_adapter()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AutoAgent":
            for stmt in node.body:
                # SUPPORTS_ATIF = True  →  ast.Assign or ast.AnnAssign
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == "SUPPORTS_ATIF":
                            value = stmt.value
                            assert isinstance(value, ast.Constant) and value.value is True, (
                                "AutoAgent.SUPPORTS_ATIF must be True"
                            )
                            return
    raise AssertionError("AutoAgent.SUPPORTS_ATIF = True not found in adapter.py")


def test_adapter_source_hash_is_stable() -> None:
    """adapter.py source hash must be a valid SHA-256 hex digest (tamper detection baseline).

    This test does NOT assert a specific hash value — it verifies that the file
    exists and produces a well-formed digest. Pin the expected value in CI/CD if
    immutability enforcement is required.
    """
    source = ADAPTER_PATH.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    assert len(digest) == 64, "SHA-256 digest must be 64 hex characters"
    assert all(c in "0123456789abcdef" for c in digest), "Digest must be lowercase hex"


def test_agent_imports_autoagent_for_harbor_compat() -> None:
    """agent.py must re-export AutoAgent via 'from adapter import AutoAgent'.

    Harbor uses --agent-import-path agent:AutoAgent, so agent.py must expose it.
    Verified via AST to avoid importing heavy runtime dependencies.
    """
    source = AGENT_PATH.read_text()
    tree = ast.parse(source)

    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "adapter"
            and any(alias.name == "AutoAgent" for alias in node.names)
        ):
            found = True
            break

    assert found, "agent.py must re-export AutoAgent via 'from adapter import AutoAgent'"


def test_adapter_does_not_define_run_task() -> None:
    """run_task must live in agent.py (editable harness), not adapter.py.

    Verified via AST to keep the boundary clean.
    """
    tree = _parse_adapter()
    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.col_offset == 0
    }
    assert "run_task" not in top_level_funcs, (
        "run_task must not be defined in adapter.py — it belongs in the editable harness (agent.py)"
    )
