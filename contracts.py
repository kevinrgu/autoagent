"""Interface contracts for the harness/adapter boundary.

These Protocol classes define the shape that the editable harness (agent.py)
must satisfy. The fixed adapter (AutoAgent) depends on these abstractions, not
on concrete implementations.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentWorkflow(Protocol):
    """Contract for the editable harness layer.

    Implementors provide tool construction, agent construction, and
    task-execution orchestration.  The fixed adapter calls these three
    entry-points only — nothing else crosses the boundary.
    """

    def create_tools(self, environment: Any) -> list:
        """Return a list of tools configured for *environment*."""
        ...

    def create_agent(self, environment: Any) -> Any:
        """Build and return a configured agent instance."""
        ...

    async def run_task(self, environment: Any, instruction: str) -> tuple[Any, int]:
        """Execute a task and return (result, duration_ms)."""
        ...


@runtime_checkable
class EvaluatorContract(Protocol):
    """Contract for trajectory evaluators.

    Evaluators receive a trajectory dict and an expected-outcome dict and
    return a scalar score in the range [0.0, 1.0].
    """

    def score(self, trajectory: dict, expected: dict) -> float:
        """Score *trajectory* against *expected*. Returns a float in [0, 1]."""
        ...


__all__ = ["AgentWorkflow", "EvaluatorContract"]
