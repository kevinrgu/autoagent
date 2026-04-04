#!/usr/bin/env bash
set -euo pipefail
NETWORK="${EVAL_NETWORK:-none}"
docker run --rm --read-only \
  --network="$NETWORK" \
  --tmpfs /tmp:size=512M \
  --mount type=bind,source="$(pwd)/adapter.py",target=/app/fixed/adapter.py,readonly \
  --mount type=bind,source="$(pwd)/contracts.py",target=/app/fixed/contracts.py,readonly \
  -v "$(pwd)/agent.py:/app/editable/agent.py:rw" \
  -e PYTHONPATH=/app/fixed:/app/editable:/app \
  --security-opt no-new-privileges:true \
  autoagent-base "$@"
