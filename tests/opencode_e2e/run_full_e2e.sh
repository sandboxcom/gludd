#!/usr/bin/env bash
# run_full_e2e.sh — 1-hour E2E test of opencode multitask enforcement
# Spawns opencode against a temp copy of _test_project/, monitors dispatch
# behavior, kills after 3600s, and reports PASS/FAIL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TIMEOUT="${1:-3600}"
echo "=== opencode E2E Multitask Test ==="
echo "Timeout: ${TIMEOUT}s"
echo "Start: $(date)"
echo ""

cd "$ROOT"
uv run python tests/opencode_e2e/run_spawner_test.py --timeout "$TIMEOUT" --progress-interval 60

RC=$?
echo ""
echo "End: $(date)"
echo "Exit code: $RC"
exit $RC
