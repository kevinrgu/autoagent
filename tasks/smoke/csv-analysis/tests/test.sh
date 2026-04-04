#!/usr/bin/env bash
set -euo pipefail

CSV_FILE="/task/output/data.csv"
AVG_FILE="/task/output/average.txt"

if [ ! -f "$CSV_FILE" ]; then
    echo "FAIL: $CSV_FILE does not exist"
    exit 1
fi

if [ ! -f "$AVG_FILE" ]; then
    echo "FAIL: $AVG_FILE does not exist"
    exit 1
fi

# Count data rows (excluding header)
DATA_ROWS=$(tail -n +2 "$CSV_FILE" | grep -c '[^[:space:]]' || true)
if [ "$DATA_ROWS" -ne 3 ]; then
    echo "FAIL: expected 3 data rows but got $DATA_ROWS"
    exit 1
fi

# Check average (integer: (85+92+78)/3 = 85)
AVG_CONTENT=$(cat "$AVG_FILE" | tr -d '[:space:]')
if [ "$AVG_CONTENT" != "85" ]; then
    echo "FAIL: expected average '85' but got '$AVG_CONTENT'"
    exit 1
fi

echo "PASS"
