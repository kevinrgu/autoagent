from __future__ import annotations

import asyncio
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.usage import Usage


@dataclass
class CodexCliResponse:
    usage: Usage = field(default_factory=Usage)


@dataclass
class CodexCliResult:
    final_output: str
    session_id: str = "codex-cli"
    reported_total_tokens: int | None = None
    transcript: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    raw_responses: list[CodexCliResponse] = field(default_factory=lambda: [CodexCliResponse()])
    new_items: list[Any] = field(default_factory=list)
    backend: str = "codex-cli"

    @property
    def last_response_id(self) -> str:
        return self.session_id


def parse_codex_session_id(stderr: str) -> str | None:
    match = re.search(r"session id:\s*([^\n]+)", stderr, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_codex_total_tokens(stderr: str) -> int | None:
    match = re.search(r"tokens used\s*\n\s*([0-9,]+)", stderr, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


async def exec_codex_cli(
    *,
    workdir: Path,
    prompt: str,
    output_path: Path,
    timeout_seconds: int = 900,
) -> subprocess.CompletedProcess[str]:
    args = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--full-auto",
        "-C",
        str(workdir),
        "-o",
        str(output_path),
        prompt,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("codex executable not found on PATH") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeError(f"codex exec timed out after {timeout_seconds} seconds") from exc

    return subprocess.CompletedProcess(
        args,
        process.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def run_task_with_codex_cli(
    environment,
    instruction: str,
    *,
    remote_workdir: str = "/task",
    timeout_seconds: int = 900,
) -> tuple[CodexCliResult, int]:
    import time

    started = time.time()
    workspace_root = Path(tempfile.mkdtemp(prefix="autoagent-codex-"))
    local_workdir = workspace_root / "workspace"
    local_workdir.mkdir(parents=True, exist_ok=True)
    final_output_path = workspace_root / "codex-last-message.txt"

    try:
        if await environment.is_dir(remote_workdir):
            await environment.download_dir(remote_workdir, local_workdir)

        prompt = build_codex_prompt(instruction=instruction, remote_workdir=remote_workdir)
        completed = await exec_codex_cli(
            workdir=local_workdir,
            prompt=prompt,
            output_path=final_output_path,
            timeout_seconds=timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"codex exec failed with exit code {completed.returncode}: {completed.stderr or completed.stdout}")

        await reset_remote_workdir(environment, remote_workdir)
        await environment.upload_dir(local_workdir, remote_workdir)

        final_output = final_output_path.read_text().strip() if final_output_path.exists() else completed.stdout.strip()
        result = CodexCliResult(
            final_output=final_output or completed.stdout.strip() or "(empty)",
            session_id=parse_codex_session_id(completed.stderr) or "codex-cli",
            reported_total_tokens=parse_codex_total_tokens(completed.stderr),
            transcript=(completed.stdout + ("\n" + completed.stderr if completed.stderr else "")).strip(),
            steps=[{"source": "agent", "message": final_output or completed.stdout.strip() or "(empty)"}],
        )
        duration_ms = int((time.time() - started) * 1000)
        return result, duration_ms
    finally:
        shutil.rmtree(workspace_root, ignore_errors=True)


async def reset_remote_workdir(environment, remote_workdir: str) -> None:
    result = await environment.exec(
        f"rm -rf {shlex.quote(remote_workdir)} && mkdir -p {shlex.quote(remote_workdir)}",
        timeout_sec=120,
    )
    if getattr(result, "return_code", 0) != 0:
        raise RuntimeError(
            f"Failed to reset remote workspace {remote_workdir}: {getattr(result, 'stderr', '') or getattr(result, 'stdout', '')}"
        )



def build_codex_prompt(*, instruction: str, remote_workdir: str) -> str:
    return (
        "You are working on a local mirror of a Harbor task workspace.\n"
        f"The files in the current directory correspond to the remote path {remote_workdir}.\n"
        "Solve the task by editing files in this workspace only.\n"
        "Any shell commands you run execute on the local mirror host, not inside the original Harbor container.\n"
        "When you finish, describe exactly what you changed and the final outcome in your last message.\n\n"
        "Task instruction:\n"
        f"{instruction}\n"
    )
