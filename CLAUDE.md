# BIFROST autoagent — Code Context

## Project role
Session O self-improvement loop. Runs overnight training cycles, proposes diffs to bifrost-router and related files, evaluates with local models, commits accepted changes.

## Recent Sessions
See `sessions/` directory for structured session logs.
Most recent: check `sessions/.current-session` for active session, or
`ls sessions/*.md | sort` for chronological list.

## Session discipline
Every Code or Dispatch session should:
1. `/project:session-start <descriptive-name>` at the beginning
2. `/project:session-update "<what you just did>"` at each meaningful step
3. `/project:session-end` before exiting

Session files land in `sessions/YYYY-MM-DD-HHMM-<name>.md` and auto-stage
to L:\\temp\\pk-upload\\ via the morning PK audit.

## Repo conventions
- Use UTF-8 encoding always (avoid em-dash encoding traps — scripting rule #15)
- Label every command with machine name (Bifrost/Hearth/Forge)
- Use `read_text`/`write_text` Python patterns, not PowerShell here-strings
- Docker builds always `--no-cache`
- All commits go to jhpritch-dev/<repo>, not upstream

## Active gates
Session O-SAFETY canary gates (4 gates, import/build/smoke/graph-invoke)
run on every accepted diff before git commit. See session_o_gates.py.
