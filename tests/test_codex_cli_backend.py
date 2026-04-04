from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codex_cli_backend import CodexCliResult, parse_codex_session_id, parse_codex_total_tokens, run_task_with_codex_cli


class FakeExecResult:
    def __init__(self, return_code: int = 0, stdout: str = "", stderr: str = ""):
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


class FakeEnvironment:
    def __init__(self, remote_root: Path):
        self.remote_root = remote_root
        self.uploads: list[tuple[Path, str]] = []

    async def is_dir(self, path: str, user=None) -> bool:
        return (self.remote_root / path.lstrip("/")).is_dir()

    async def download_dir(self, source_dir: str, target_dir: Path | str):
        source = self.remote_root / source_dir.lstrip("/")
        shutil.copytree(source, target_dir, dirs_exist_ok=True)

    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        source = Path(source_dir)
        dest = self.remote_root / target_dir.lstrip("/")
        shutil.copytree(source, dest, dirs_exist_ok=True)
        self.uploads.append((source, target_dir))

    async def exec(self, command: str, cwd=None, env=None, timeout_sec=None, user=None):
        target = self.remote_root / "task"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        return FakeExecResult()


class DummyCompleted:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.asyncio
async def test_run_task_with_codex_cli_syncs_workspace_and_returns_result(tmp_path, monkeypatch):
    remote_root = tmp_path / "remote"
    task_dir = remote_root / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "hello.txt").write_text("before")
    (task_dir / "remove-me.txt").write_text("delete")

    env = FakeEnvironment(remote_root)

    async def fake_exec(*, workdir: Path, prompt: str, output_path: Path, timeout_seconds: int) -> DummyCompleted:
        assert workdir.exists()
        assert "Say hello" in prompt
        (workdir / "hello.txt").write_text("after")
        (workdir / "remove-me.txt").unlink()
        output_path.write_text("done")
        return DummyCompleted(stdout="done\n", stderr="session id: sess-123\ntokens used\n1,234\n")

    monkeypatch.setattr("codex_cli_backend.exec_codex_cli", fake_exec)

    result, duration_ms = await run_task_with_codex_cli(env, "Say hello", remote_workdir="/task")

    assert isinstance(result, CodexCliResult)
    assert duration_ms >= 0
    assert result.final_output == "done"
    assert result.session_id == "sess-123"
    assert result.reported_total_tokens == 1234
    assert (task_dir / "hello.txt").read_text() == "after"
    assert not (task_dir / "remove-me.txt").exists()
    assert env.uploads


def test_parse_codex_metadata_from_stderr():
    stderr = "prefix\nsession id: abc-123\ntokens used\n23,960\n"

    assert parse_codex_session_id(stderr) == "abc-123"
    assert parse_codex_total_tokens(stderr) == 23960
