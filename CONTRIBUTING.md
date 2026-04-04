# Contributing to AutoAgent

## Adding a New Backend Adapter

1. Copy `agent.py` as `agent-<backend>.py`.
2. Implement the `AgentWorkflow` protocol from `contracts.py`:
   - `create_tools(environment) -> list`
   - `create_agent(environment) -> agent`
   - `async run_task(environment, instruction) -> tuple[result, duration_ms]`
3. Import `AutoAgent` from `adapter.py` — do not modify it.
4. Run smoke tests: `bash scripts/run_smoke.sh`
5. Log results using `ExperimentLogger` from `experiment_log.py`.

See `agent-claude.py` for a Claude SDK reference implementation.

## Adding New Tasks

1. Create `tasks/<category>/<name>/` with:
   - `task.toml` — task metadata (name, description, timeout_sec)
   - `instruction.md` — what the agent should do
   - `tests/test.sh` — verification script (exit 0 = pass, exit 1 = fail)
2. Follow existing patterns in `tasks/smoke/`.
3. Test scripts should use `set -euo pipefail`.

## Evaluation Levels

| Level | Location | Purpose |
|-------|----------|---------|
| Smoke | `tasks/smoke/` | Basic sanity (< 1 min) |
| Domain | `tasks/<domain>/` | Domain-specific suite |
| Cross-domain | External benchmarks | Generalization test |

## Reporting Benchmark Results

Use `experiments.jsonl` format via `ExperimentLogger`:

```python
from experiment_log import ExperimentLogger, ExperimentEntry

logger = ExperimentLogger("experiments.jsonl")
logger.append(ExperimentEntry(
    version="v1",
    scores={"smoke": 1.0, "spreadsheet": 0.85},
    trace_id="trace-001",
    trajectory_uri="jobs/v1/trajectory.json",
    # ... other fields
))
```

## Project Structure

```
autoagent/
├── agent.py              # Editable harness (meta-agent modifies this)
├── adapter.py            # Fixed Harbor adapter (read-only)
├── contracts.py          # Interface protocols (read-only)
├── preflight.py          # Mutation validation gate
├── experiment_log.py     # ATIF sidecar experiment logger
├── archive_manager.py    # Evolutionary archive (exploit/explore)
├── promotion.py          # Promotion gates and migration rules
├── program.md            # Original meta-agent directive
├── program-fixed.md      # Immutable safety rules
├── program-strategy.md   # Editable strategy (Stage 2)
├── Dockerfile.base       # Container base image
├── scripts/
│   ├── run_eval.sh       # Docker eval runner (read-only + network isolation)
│   └── run_smoke.sh      # Smoke test runner
├── tasks/smoke/          # Level 1 smoke tests (5 tasks)
└── tests/                # Unit tests
```

## Safety Boundary

Files in the **fixed boundary** must not be modified by the meta-agent:
- `adapter.py`, `contracts.py` — enforced via Docker read-only mount
- Evaluator logic, promotion gates — enforced via preflight policy gate

The `preflight.py` gate automatically rejects diffs that touch fixed files or use forbidden imports.
