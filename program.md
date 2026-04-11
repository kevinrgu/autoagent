# BIFROST Session O -- AutoAgent Directive

## Objective
Improve the BIFROST AUTOPILOT agent pipeline. Hill-climb L3-L5 pass rate on the verification harness.

## Target
C:\Users\jhpri\projects\autoagent\bifrost_cycle.py

## Proposer
bifrost-t2-gemma4 (gemma4:26b) on Forge (http://192.168.2.50:11434)

## Executor
bifrost-t1b (gemma3:12b) on Bifrost (http://192.168.2.33:11434)

## Evaluator
bifrost-t2-gemma4 (gemma4:26b) on Forge (http://192.168.2.50:11434)

## Constraints
- One change per cycle. No rewrites.
- If L5 fails, rewrite the relevant SKILL.md entry before retrying.
- Max 5 cycles per run.
- Write all decisions to decisions.md.

