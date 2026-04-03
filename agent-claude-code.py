"""
Claude Code CLI adapter for Harbor.

Runs the locally-installed `claude` CLI on the HOST machine (not inside Docker).
No API key needed — uses your existing Claude Code OAuth session.

Prerequisites:
  - Claude Code CLI installed: npm install -g @anthropic-ai/claude-code
  - Authenticated locally: claude login (or already logged in via claude.ai)

Run all tasks:
  docker build -f Dockerfile.claude-code -t autoagent-base .
  uv run harbor run -p tasks/ --agent-import-path agent-claude-code:AutoAgent -o jobs --job-name latest > run.log 2>&1
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


# ===========================================================================
# AGENT CONFIG — meta-agent modifies this section
# ===========================================================================

SYSTEM_PROMPT = """You are a highly capable task-completion agent working inside a sandboxed environment.

## Approach
1. Read the task instruction to understand what's required.
2. Explore the working environment — check what files, tools, and libraries are available.
3. Plan your approach, then execute step by step.
4. Write output files to the exact paths specified in the instructions.
5. Verify your output before finishing.

## Key rules
- All paths referenced in the task are relative to your current working directory.
- Use python3 (not python) for running scripts.
- For data analysis: pandas, numpy, openpyxl are available.
- For file manipulation: use standard Python or shell tools.
- Always verify output files exist and contain valid content before finishing.
- If a task involves git repos, use git commands directly.
- If a task involves databases, use sqlite3 CLI or Python sqlite3 module.
- Read error messages carefully and fix issues iteratively.
- Never give up — try multiple approaches if one fails.
"""

MODEL = "sonnet"
MAX_TURNS = 30
ALLOWED_TOOLS: list[str] = []
CLI_EXTRA_FLAGS: list[str] = []


def build_cli_args(workdir: str, instruction_text: str) -> list[str]:
    """Build the claude CLI argument list. Modify to change execution strategy."""
    args = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if SYSTEM_PROMPT:
        args.extend(["--system-prompt", SYSTEM_PROMPT])
    for tool in ALLOWED_TOOLS:
        args.extend(["--allowedTools", tool])
    args.extend(CLI_EXTRA_FLAGS)
    args.append(instruction_text)
    return args


# ===========================================================================
# HARBOR ADAPTER — fixed harness, do not modify
# ===========================================================================

from harbor.agents.base import BaseAgent  # noqa: E402
from harbor.environments.base import BaseEnvironment  # noqa: E402
from harbor.models.agent.context import AgentContext  # noqa: E402


def _parse_claude_json_output(raw: str) -> list[dict]:
    """Parse Claude Code JSON output (newline-delimited JSON objects)."""
    messages = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def _to_atif(messages: list[dict], duration_ms: int) -> dict:
    """Convert Claude Code stream-json output to an ATIF trajectory dict."""
    steps: list[dict] = []
    step_id = 0
    now = datetime.now(timezone.utc).isoformat()

    cost_usd = None
    session_id = "unknown"
    num_turns = 0
    model_name = MODEL
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_tokens = 0

    pending_tools: dict[str, dict] = {}

    def _step(source: str, message: str, **extra: object) -> dict:
        nonlocal step_id
        step_id += 1
        step = {
            "step_id": step_id,
            "timestamp": now,
            "source": source,
            "message": message,
        }
        step.update({k: v for k, v in extra.items() if v is not None})
        return step

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type == "assistant":
            content_blocks = msg.get("message", {}).get("content", [])
            texts = []
            reasoning = None
            for block in content_blocks:
                block_type = block.get("type", "")
                if block_type == "text":
                    texts.append(block.get("text", ""))
                elif block_type == "thinking":
                    reasoning = block.get("thinking", "")
                elif block_type == "tool_use":
                    pending_tools[block.get("id", "")] = {
                        "tool_call_id": block.get("id", ""),
                        "function_name": block.get("name", "unknown"),
                        "arguments": block.get("input", {}),
                    }
            if texts or reasoning:
                steps.append(_step(
                    "agent",
                    "\n".join(texts) or "(thinking)",
                    reasoning_content=reasoning,
                    model_name=msg.get("message", {}).get("model", model_name),
                ))
            if msg.get("message", {}).get("model"):
                model_name = msg["message"]["model"]
            if msg.get("session_id"):
                session_id = msg["session_id"]

        elif msg_type == "user":
            content = msg.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = "\n".join(
                                b.get("text", str(b)) for b in result_content
                            )
                        tc = pending_tools.pop(tool_id, None)
                        if tc:
                            steps.append(_step(
                                "agent",
                                f"Tool: {tc['function_name']}",
                                tool_calls=[tc],
                                observation={"results": [{
                                    "source_call_id": tool_id,
                                    "content": str(result_content),
                                }]},
                            ))

        elif msg_type == "result":
            cost_usd = msg.get("total_cost_usd")
            if msg.get("session_id"):
                session_id = msg["session_id"]
            num_turns = msg.get("num_turns", num_turns)
            result_usage = msg.get("usage", {})
            if result_usage:
                total_input_tokens = result_usage.get("input_tokens", 0)
                total_output_tokens = result_usage.get("output_tokens", 0)
                total_cache_tokens = result_usage.get("cache_read_input_tokens", 0)

    for tc in pending_tools.values():
        steps.append(_step(
            "agent", f"Tool: {tc['function_name']}", tool_calls=[tc],
        ))

    if not steps:
        steps.append(_step("user", "(empty)"))

    return {
        "schema_version": "ATIF-v1.6",
        "session_id": session_id,
        "agent": {"name": "autoagent-claude-code", "version": "0.1.0", "model_name": model_name},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": total_input_tokens,
            "total_completion_tokens": total_output_tokens,
            "total_cached_tokens": total_cache_tokens,
            "total_cost_usd": cost_usd,
            "total_steps": len(steps),
            "extra": {
                "duration_ms": duration_ms,
                "num_turns": num_turns,
            },
        },
    }


async def _sync_dir_to_container(
    local_dir: Path, container_path: str, environment: BaseEnvironment
) -> None:
    """Upload all files from a local directory into the container."""
    for fpath in local_dir.rglob("*"):
        if fpath.is_file():
            rel = fpath.relative_to(local_dir)
            target = f"{container_path}/{rel}"
            parent = str(Path(target).parent)
            await environment.exec(command=f"mkdir -p {parent}")
            await environment.upload_file(source_path=fpath, target_path=target)


async def _sync_container_to_dir(
    container_path: str, local_dir: Path, environment: BaseEnvironment
) -> None:
    """Download files from the container into a local directory."""
    result = await environment.exec(
        command=f"find {container_path} -type f 2>/dev/null || true"
    )
    if not result.stdout or not result.stdout.strip():
        return
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        rel = line.removeprefix(container_path).lstrip("/")
        local_file = local_dir / rel
        local_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            await environment.download_file(
                source_path=line, target_path=local_file
            )
        except Exception:
            pass


class AutoAgent(BaseAgent):
    """Harbor agent adapter. Runs Claude Code CLI on the HOST,
    syncs files to/from the container for verification."""

    SUPPORTS_ATIF = True

    def __init__(self, *args, extra_env: dict[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra_env = dict(extra_env) if extra_env else {}

    @staticmethod
    def name() -> str:
        return "autoagent"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        # 1. Create a temp workdir on the host that mirrors /task in container
        with tempfile.TemporaryDirectory(prefix="autoagent_") as tmpdir:
            workdir = Path(tmpdir)
            task_dir = workdir / "task"
            task_dir.mkdir()
            output_dir = task_dir / "output"
            output_dir.mkdir()

            # Write instruction
            instr_file = task_dir / "instruction.md"
            instr_file.write_text(instruction)

            # Download any pre-existing files from the container's /task/
            await _sync_container_to_dir("/task", task_dir, environment)

            # 2. Rewrite absolute /task/ paths to the temp workdir path
            #    so claude writes files to the correct location
            task_prefix = str(task_dir)
            local_instruction = instruction.replace("/task/", f"{task_prefix}/")

            # Run claude CLI on the HOST, pointed at the temp workdir
            cli_args = build_cli_args(
                str(workdir),
                local_instruction,
            )

            t0 = time.time()
            try:
                result = subprocess.run(
                    cli_args,
                    capture_output=True,
                    text=True,
                    timeout=540,
                    cwd=str(task_dir),
                )
                duration_ms = int((time.time() - t0) * 1000)
                raw_output = result.stdout or ""
                stderr_output = result.stderr or ""
            except subprocess.TimeoutExpired:
                duration_ms = int((time.time() - t0) * 1000)
                raw_output = ""
                stderr_output = "ERROR: claude CLI timed out after 540s"
            except FileNotFoundError:
                duration_ms = int((time.time() - t0) * 1000)
                raw_output = ""
                stderr_output = (
                    "ERROR: claude CLI not found on host. "
                    "Install: npm install -g @anthropic-ai/claude-code"
                )

            # Save raw output for debugging
            if raw_output:
                (self.logs_dir / "claude_raw_output.txt").write_text(raw_output)
            if stderr_output:
                (self.logs_dir / "claude_stderr.txt").write_text(stderr_output)

            # 3. Sync files created by claude back to the container
            await _sync_dir_to_container(task_dir, "/task", environment)

            # 4. Build ATIF trajectory
            messages = _parse_claude_json_output(raw_output)
            atif = _to_atif(messages, duration_ms)

            traj_path = self.logs_dir / "trajectory.json"
            traj_path.write_text(json.dumps(atif, indent=2))

            # 5. Populate Harbor metrics
            try:
                fm = atif.get("final_metrics", {})
                context.cost_usd = fm.get("total_cost_usd")
                context.n_input_tokens = fm.get("total_prompt_tokens", 0)
                context.n_output_tokens = fm.get("total_completion_tokens", 0)
                context.n_cache_tokens = fm.get("total_cached_tokens", 0)
            except Exception:
                pass

            cost = atif.get("final_metrics", {}).get("total_cost_usd") or 0
            turns = atif.get("final_metrics", {}).get("extra", {}).get("num_turns", 0)
            print(
                f"cost_usd={cost:.4f} turns={turns} duration_ms={duration_ms}"
            )


__all__ = ["AutoAgent"]
