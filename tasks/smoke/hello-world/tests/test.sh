#!/usr/bin/env bash
set -euo pipefail

OUTPUT_FILE="/task/output/hello.txt"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "FAIL: $OUTPUT_FILE does not exist"
    exit 1
fi

CONTENT=$(cat "$OUTPUT_FILE")
if [ "$CONTENT" != "Hello, World!" ]; then
    echo "FAIL: expected 'Hello, World!' but got '$CONTENT'"
    exit 1
fi

echo "PASS"
