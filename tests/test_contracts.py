"""Tests for contracts.py — interface contract definitions."""

from __future__ import annotations

import importlib
import inspect


def test_contracts_module_exists() -> None:
    """contracts module must be importable."""
    contracts = importlib.import_module("contracts")
    assert contracts is not None


def test_agent_workflow_protocol_exists() -> None:
    """contracts module must expose AgentWorkflow."""
    contracts = importlib.import_module("contracts")
    assert hasattr(contracts, "AgentWorkflow"), "AgentWorkflow not found in contracts"


def test_evaluator_contract_protocol_exists() -> None:
    """contracts module must expose EvaluatorContract."""
    contracts = importlib.import_module("contracts")
    assert hasattr(contracts, "EvaluatorContract"), "EvaluatorContract not found in contracts"


def test_agent_workflow_has_required_methods() -> None:
    """AgentWorkflow Protocol must declare run_task, create_tools, create_agent."""
    contracts = importlib.import_module("contracts")
    AgentWorkflow = contracts.AgentWorkflow

    required = {"run_task", "create_tools", "create_agent"}
    # Protocol methods appear as annotations or as actual members
    members = set(dir(AgentWorkflow))
    assert required <= members, f"Missing methods: {required - members}"


def test_evaluator_contract_has_score_method() -> None:
    """EvaluatorContract Protocol must declare score."""
    contracts = importlib.import_module("contracts")
    EvaluatorContract = contracts.EvaluatorContract

    assert "score" in dir(EvaluatorContract), "score method not found in EvaluatorContract"


def test_agent_py_functions_are_callable() -> None:
    """agent.py must define create_tools, create_agent, run_task as top-level functions.

    Verified via AST to avoid importing heavy runtime dependencies (harbor, openai-agents).
    """
    import ast
    from pathlib import Path  # noqa: PLC0415

    source = (Path(__file__).parent.parent / "agent.py").read_text()
    tree = ast.parse(source)

    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and isinstance(node.col_offset, int)
        and node.col_offset == 0  # module-level only
    }

    for name in ("create_tools", "create_agent", "run_task"):
        assert name in top_level_funcs, f"{name} not found as a top-level function in agent.py"


def test_run_task_is_coroutine_function() -> None:
    """run_task in agent.py must be declared as async to satisfy the async contract.

    Verified via AST to avoid importing heavy runtime dependencies.
    """
    import ast
    from pathlib import Path  # noqa: PLC0415

    source = (Path(__file__).parent.parent / "agent.py").read_text()
    tree = ast.parse(source)

    async_top_level = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.col_offset == 0
    }

    assert "run_task" in async_top_level, "run_task must be an async def in agent.py"
