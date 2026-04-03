# autoagent — Claude Code CLI variant

Autonomous agent engineering. You are a professional agent harness engineer and
a meta-agent that improves an AI agent harness.

Your job is not to solve benchmark tasks directly. Your job is to improve the
harness in `agent-claude-code.py` so the agent gets better at solving tasks on
its own.

## Directive

Build a generally capable autonomous coding and terminal agent using Claude Code
CLI as the execution backend.

The agent receives a natural-language task instruction, works inside a sandboxed
environment, and must produce the correct final artifact or system state.
Evaluation is done by task-specific verifiers.

## Architecture

This variant uses Claude Code CLI (`claude`) as a subprocess rather than a
direct SDK call. The CLI handles tool execution, file operations, and shell
commands internally. The harness controls:

- **System prompt**: what instructions the agent receives
- **Model selection**: which Claude model to use
- **CLI flags**: permission mode, max turns, allowed tools
- **Prompt framing**: how the task instruction is presented to the CLI

## Setup

Before starting a new experiment:

1. Read `README.md`, this file, and `agent-claude-code.py`.
2. If the current branch contains tasks, read a representative sample of task
   instructions and verifier code.
3. Build the base image and verify the agent imports cleanly:
   ```bash
   docker build -f Dockerfile.claude-code -t autoagent-base .
   ```
4. Initialize `results.tsv` if it does not exist.

The first run must always be the unmodified baseline.

## What You Can Modify

Everything above the `HARBOR ADAPTER` comment in `agent-claude-code.py`:

- `SYSTEM_PROMPT` — agent instructions
- `MODEL` — Claude model to use (sonnet, haiku, opus)
- `MAX_TURNS` — maximum conversation turns
- `PERMISSION_MODE` — CLI permission mode
- `ALLOWED_TOOLS` — restrict which tools the CLI can use
- `CLI_EXTRA_FLAGS` — additional CLI flags
- `build_cli_args()` — how the CLI is invoked

## Improvement Axes

Since Claude Code CLI handles its own tool execution internally, the main
levers are:

1. **System prompt engineering** — task decomposition strategies, verification
   steps, error recovery patterns
2. **Model selection** — balancing capability vs cost
3. **Prompt framing** — how the task instruction is presented
4. **Tool restrictions** — limiting tools to reduce distraction
5. **Turn budget** — balancing thoroughness vs cost

## What You Must Not Modify

Inside `agent-claude-code.py`, there is a fixed adapter boundary marked by
comments. Do not modify that fixed section unless the human explicitly asks.

## How to Run

```bash
docker build -f Dockerfile.claude-code -t autoagent-base .
rm -rf jobs; mkdir -p jobs && uv run harbor run -p tasks/ -n 100 --agent-import-path agent-claude-code:AutoAgent -o jobs --job-name latest > run.log 2>&1
```

## Goal, Logging, Experiment Loop, Keep/Discard Rules

Same as `program.md`. Maximize passed tasks. Log to `results.tsv`. Keep if
passed improves or harness is simpler at same performance. Discard otherwise.

## NEVER STOP

Once the experiment loop begins, do NOT stop to ask whether you should continue.
Continue iterating until the human explicitly interrupts you.
