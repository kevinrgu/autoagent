#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

export AUTOAGENT_BACKEND="${AUTOAGENT_BACKEND:-auto}"
export PYTHONUNBUFFERED=1

DEFAULT_PROMPT="Read program.md and let's kick off a new experiment!"
PROMPT="${AUTOAGENT_PROMPT:-$DEFAULT_PROMPT}"

exec codex exec --full-auto -C "$REPO_ROOT" "$PROMPT"
