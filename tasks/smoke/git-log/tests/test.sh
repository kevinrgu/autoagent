#!/usr/bin/env bash
set -euo pipefail

OUTPUT_FILE="/task/output/log.txt"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "FAIL: $OUTPUT_FILE does not exist"
    exit 1
fi

LINE_COUNT=$(wc -l < "$OUTPUT_FILE")
if [ "$LINE_COUNT" -ne 3 ]; then
    echo "FAIL: expected 3 lines but got $LINE_COUNT"
    exit 1
fi

if ! grep -q "first" "$OUTPUT_FILE"; then
    echo "FAIL: 'first' not found in log"
    exit 1
fi

if ! grep -q "second" "$OUTPUT_FILE"; then
    echo "FAIL: 'second' not found in log"
    exit 1
fi

if ! grep -q "third" "$OUTPUT_FILE"; then
    echo "FAIL: 'third' not found in log"
    exit 1
fi

echo "PASS"
