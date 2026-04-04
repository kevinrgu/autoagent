#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${AUTOAGENT_LOG_DIR:-$REPO_ROOT/meta-agent-logs}"
mkdir -p "$LOG_DIR"

while true; do
  TS="$(date +%Y%m%d-%H%M%S)"
  LOG_FILE="$LOG_DIR/meta-agent-$TS.log"
  echo "[$(date)] starting meta-agent run" | tee -a "$LOG_FILE"
  if "$REPO_ROOT/scripts/run-meta-agent-once.sh" >>"$LOG_FILE" 2>&1; then
    echo "[$(date)] meta-agent exited cleanly" | tee -a "$LOG_FILE"
  else
    CODE=$?
    echo "[$(date)] meta-agent exited with code $CODE" | tee -a "$LOG_FILE"
  fi
  sleep "${AUTOAGENT_RESTART_DELAY_SECONDS:-10}"
done
