#!/usr/bin/env bash
set -euo pipefail
echo "=== Smoke Test (Level 1) ==="
uv run harbor run -p tasks/smoke/ --agent-import-path agent:AutoAgent -o jobs/smoke
echo "=== Smoke Complete ==="
