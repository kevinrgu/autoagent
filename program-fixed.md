# Program Fixed Rules (DO NOT MODIFY)

These rules are immutable. The meta-agent must not alter this file.

## Safety Rules

1. Never modify files in the `fixed/` directory (`adapter.py`, `contracts.py`).
2. Never use `importlib`, `ctypes`, `sys.modules`, or `__import__()` directly.
3. Always log results via `ExperimentLogger` to `experiments.jsonl`.
4. Never skip the preflight policy gate before evaluation.
5. Respect per-suite promotion gates — do not bypass thresholds.
6. Do not modify evaluator, promotion, or scoreboard logic.

## Experiment Protocol

1. Read the latest experiments.jsonl and recent task-level results.
2. Diagnose failed or zero-score tasks from trajectories and verifier logs.
3. Group failures by root cause.
4. Choose one general harness improvement.
5. Run preflight check on the diff.
6. Execute smoke test (Level 1) — all 5 must pass.
7. If smoke passes, execute domain suite (Level 2).
8. Log results to experiments.jsonl with full metadata.
9. If improved: snapshot to archive, commit.
10. If regressed: restore previous best, log root cause.

## Model Constraint

Do not change `MODEL` from `gpt-5` without explicit human approval.

## Overfitting Rule

Do not add task-specific hacks. Use this test:
"If this exact task disappeared, would this still be a worthwhile improvement?"

## Keep / Discard Rules

- If `passed` improved → keep.
- If `passed` stayed same and harness is simpler → keep.
- Otherwise → discard. Record root cause in experiments.jsonl.

## Termination

Continue iterating until the human explicitly stops.
Never pause to ask whether to continue.
