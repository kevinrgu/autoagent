# Program Strategy

> **Stage 1: READ-ONLY.** This file becomes editable after Stage 2 gate passes
> (per-suite non-regression 10 consecutive runs + human approval).

## Current Strategy

- Focus on tool addition over prompt tuning (high-leverage).
- Prefer specialized tools (e.g., openpyxl for Excel) over raw shell.
- Test one change at a time for clear attribution.
- Prioritize tasks with highest failure rate.

## Tool Design Guidelines

- Each tool should do one thing well.
- Include input validation and clear error messages.
- Return structured output, not raw stdout.
- Match model's name-based priors (models pattern-match tool names).

## Agent Architecture Strategy

- Start with single agent + specialized tools.
- Consider `agent.as_tool()` for verification sub-agent when:
  - Many tasks fail silently (agent thinks it succeeded but output is wrong).
  - Verification logic is complex enough to benefit from a separate agent.

## Simplicity Criterion

All else being equal, simpler is better:
- Fewer components
- Less brittle logic
- Cleaner tool interfaces
- Less code for the same outcome
