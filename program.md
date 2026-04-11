# BIFROST Session O -- AutoAgent Directive

## Objective
Improve the BIFROST AUTOPILOT agent pipeline. Hill-climb L3-L5 pass rate
on the verification harness. Self-improve bifrost_cycle.py each overnight run.

## Target
C:\Users\jhpri\projects\autoagent\bifrost_cycle.py

## Proposer
mistral-small3.1:24b (Mistral/EU) on Bifrost (http://192.168.2.33:11434)
Role: Identify one concrete, bounded improvement to the target file.

## Executor
bifrost-t1b / gemma3:12b (Google/US) on Bifrost (http://192.168.2.33:11434)
Role: Implement the proposed change. Output only the modified function/section.

## Evaluator
bifrost-t2-gemma4 / gemma4:26b (Google/US) on Forge (http://192.168.2.50:11434)
Role: Compare original vs new code. Output PASS or FAIL on first line with reasoning.

## Constraints
- One change per cycle. No full file rewrites.
- Max 5 cycles per run.
- Write all decisions to decisions.md.
- Backup target before modifying.
