from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import agent
from codex_cli_backend import CodexCliResult


class DummyEnvironment:
    pass


@pytest.mark.asyncio
async def test_run_task_uses_codex_backend_when_selected(monkeypatch):
    env = DummyEnvironment()
    fake_result = CodexCliResult(final_output="done")
    fake_runner = AsyncMock(return_value=(fake_result, 123))
    monkeypatch.setenv("AUTOAGENT_BACKEND", "codex-cli")
    monkeypatch.setattr(agent, "run_task_with_codex_cli", fake_runner)

    result, duration_ms = await agent.run_task(env, "do something")

    assert result is fake_result
    assert duration_ms == 123
    fake_runner.assert_awaited_once_with(env, "do something", remote_workdir="/task")


@pytest.mark.asyncio
async def test_run_task_uses_openai_runner_when_selected_backend_is_openai(monkeypatch):
    env = DummyEnvironment()
    fake_result = object()
    runner_result = type("RunnerResult", (), {"run": AsyncMock(return_value=fake_result)})
    monkeypatch.setenv("AUTOAGENT_BACKEND", "openai-agents")
    monkeypatch.setattr(agent, "create_agent", lambda environment: "fake-agent")
    monkeypatch.setattr(agent, "Runner", runner_result)

    result, duration_ms = await agent.run_task(env, "do something")

    assert result is fake_result
    assert duration_ms >= 0
    runner_result.run.assert_awaited_once_with("fake-agent", input="do something", max_turns=agent.MAX_TURNS)


def test_selected_backend_auto_prefers_openai_when_api_key_present(monkeypatch):
    monkeypatch.delenv("AUTOAGENT_BACKEND", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert agent.selected_backend() == "openai-agents"


def test_selected_backend_auto_prefers_codex_when_codex_auth_exists(monkeypatch):
    monkeypatch.delenv("AUTOAGENT_BACKEND", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class FakeOAuth:
        def load_auth_state(self):
            return object()

    monkeypatch.setattr(agent, "CodexOAuthManager", lambda: FakeOAuth())
    monkeypatch.setattr(agent.shutil, "which", lambda name: "/usr/local/bin/codex")

    assert agent.selected_backend() == "codex-cli"


def test_to_atif_supports_codex_cli_result():
    result = CodexCliResult(
        final_output="done",
        session_id="sess-1",
        reported_total_tokens=321,
        steps=[{"source": "agent", "message": "done"}],
    )

    atif = agent.to_atif(result, model="codex-cli", duration_ms=55)

    assert atif["session_id"] == "sess-1"
    assert atif["agent"]["model_name"] == "codex-cli"
    assert atif["steps"][0]["message"] == "done"
    assert atif["final_metrics"]["extra"]["backend"] == "codex-cli"
    assert atif["final_metrics"]["extra"]["reported_total_tokens"] == 321
