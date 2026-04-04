#!/usr/bin/env bash
set -euo pipefail

OUTPUT_FILE="/task/output/count.txt"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "FAIL: $OUTPUT_FILE does not exist"
    exit 1
fi

CONTENT=$(cat "$OUTPUT_FILE" | tr -d '[:space:]')
if [ "$CONTENT" != "9" ]; then
    echo "FAIL: expected '9' but got '$CONTENT'"
    exit 1
fi

echo "PASS"
