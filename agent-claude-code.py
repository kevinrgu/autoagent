"""
Claude Code CLI adapter for Harbor.

Spawns the locally-installed `claude` CLI as a subprocess. No API key needed —
Claude Code uses its own OAuth session from the host machine.

Prerequisites:
  - Claude Code CLI installed: npm install -g @anthropic-ai/claude-code
  - Authenticated locally: claude login (or already logged in)

Run all tasks:
  docker build -f Dockerfile.claude-code -t autoagent-base .
  uv run harbor run -p tasks/ --agent-import-path agent-claude-code:AutoAgent -o jobs --job-name latest > run.log 2>&1
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


# ===========================================================================
# AGENT CONFIG — meta-agent modifies this section
# ===========================================================================

SYSTEM_PROMPT = """You are a highly capable task-completion agent. You solve tasks by reading instructions, analyzing the problem, writing and executing code, and producing the required output files.

## Approach
1. Read /task/instruction.md to understand what's required.
2. Explore the working environment — check what files, tools, and libraries are available.
3. Plan your approach, then execute step by step.
4. Write output files to the exact paths specified in the instructions.
5. Verify your output before finishing.

## Key rules
- Use python3 (not python) for running scripts.
- Use Bash to run shell commands, install packages, inspect files.
- For data analysis: pandas, numpy, openpyxl are available.
- For file manipulation: use standard Python or shell tools.
- Always verify output files exist and contain valid content before finishing.
- If a task involves git repos, use git commands directly.
- If a task involves databases, use sqlite3 CLI or Python sqlite3 module.
- If a task involves images, use PIL/Pillow.
- Read error messages carefully and fix issues iteratively.
- Never give up — try multiple approaches if one fails.
"""

MODEL = "sonnet"
MAX_TURNS = 30
PERMISSION_MODE = "bypassPermissions"
ALLOWED_TOOLS = []
CLI_EXTRA_FLAGS: list[str] = []


def build_cli_args(instruction_path: str) -> list[str]:
    """Build the claude CLI argument list. Modify to change execution strategy."""
    args = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--model", MODEL,
        "--max-turns", str(MAX_TURNS),
        "--permission-mode", PERMISSION_MODE,
    ]
    if SYSTEM_PROMPT:
        args.extend(["--system-prompt", SYSTEM_PROMPT])
    for tool in ALLOWED_TOOLS:
        args.extend(["--allowedTools", tool])
    args.extend(CLI_EXTRA_FLAGS)
    # Prompt is a positional argument in claude CLI
    args.append(f"Read the task at {instruction_path} and complete it.")
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
    """Convert Claude Code stream-json output to an ATIF trajectory dict.

    Stream-json format (with --verbose) emits NDJSON with these types:
    - system: init metadata (tools, model, session_id)
    - assistant: content blocks (thinking, text, tool_use) — may arrive
      as multiple messages for the same API response
    - user: tool_result content blocks
    - result: final summary with cost, usage, duration
    - rate_limit_event: rate limit info (ignored)
    """
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

    # Track pending tool_use blocks to pair with their results
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
                    tool_id = block.get("id", "")
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    pending_tools[tool_id] = {
                        "tool_call_id": tool_id,
                        "function_name": tool_name,
                        "arguments": tool_input,
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
            # User messages contain tool_result blocks
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

    # Flush any pending tool calls that never got a result
    for tc in pending_tools.values():
        steps.append(_step(
            "agent",
            f"Tool: {tc['function_name']}",
            tool_calls=[tc],
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


class AutoAgent(BaseAgent):
    """Harbor agent adapter. Execs Claude Code CLI inside the container."""

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
        await environment.exec(command="mkdir -p /task")
        instr_file = self.logs_dir / "instruction.md"
        instr_file.write_text(instruction)
        await environment.upload_file(
            source_path=instr_file, target_path="/task/instruction.md"
        )

        # Upload host's Claude auth config into the container so the CLI
        # can authenticate without an API key.
        host_claude_dir = Path.home() / ".claude"
        if host_claude_dir.exists():
            for auth_file in ("credentials.json", "config.json", ".credentials.json"):
                src = host_claude_dir / auth_file
                if src.exists():
                    await environment.upload_file(
                        source_path=src,
                        target_path=f"/root/.claude/{auth_file}",
                    )

        env = {"IS_SANDBOX": "1"}
        env.update(self._extra_env)

        result = await environment.exec(
            command="cd /app && python agent-claude-code.py",
            env=env,
            timeout_sec=600,
        )
        if result.stdout:
            (self.logs_dir / "agent_stdout.txt").write_text(result.stdout)
        if result.stderr:
            (self.logs_dir / "agent_stderr.txt").write_text(result.stderr)

        traj_path = self.logs_dir / "trajectory.json"
        if traj_path.exists():
            try:
                fm = json.loads(traj_path.read_text()).get("final_metrics", {})
                context.cost_usd = fm.get("total_cost_usd")
                context.n_input_tokens = fm.get("total_prompt_tokens", 0)
                context.n_output_tokens = fm.get("total_completion_tokens", 0)
                context.n_cache_tokens = fm.get("total_cached_tokens", 0)
            except Exception:
                pass


# ===========================================================================
# CONTAINER ENTRYPOINT — fixed harness, do not modify
# ===========================================================================


def _run_in_container():
    """Container entrypoint — spawns claude CLI, captures output, writes ATIF."""
    import subprocess

    instruction_path = "/task/instruction.md"
    cli_args = build_cli_args(instruction_path)

    t0 = time.time()
    try:
        result = subprocess.run(
            cli_args,
            capture_output=True,
            text=True,
            timeout=540,
            cwd="/task",
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
        stderr_output = "ERROR: claude CLI not found. Ensure @anthropic-ai/claude-code is installed."

    messages = _parse_claude_json_output(raw_output)
    atif = _to_atif(messages, duration_ms)

    traj_dir = Path("/logs/agent")
    traj_dir.mkdir(parents=True, exist_ok=True)
    (traj_dir / "trajectory.json").write_text(json.dumps(atif, indent=2))

    if raw_output:
        (traj_dir / "claude_raw_output.txt").write_text(raw_output)
    if stderr_output:
        (traj_dir / "claude_stderr.txt").write_text(stderr_output)

    fm = atif.get("final_metrics", {})
    cost = fm.get("total_cost_usd") or 0
    turns = fm.get("extra", {}).get("num_turns", 0)
    print(f"cost_usd={cost:.4f} turns={turns} duration_ms={duration_ms}")


if __name__ == "__main__":
    _run_in_container()
