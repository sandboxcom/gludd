#!/usr/bin/env bash
# PreToolUse(Bash) gate-concurrency guard — 2026-06-18
#
# Blocks launching a second pytest/gate while one is already running.
# Root cause of the 208-error incident: two concurrent gates triggered pytest's
# keep-last-3 tmp-root rotation, deleting the first gate's worker dirs mid-flight.
#
# DETECTION: two independent signals (either alone fires the block):
#   1. BASETEMP LOCK: /tmp/gludd-gate-basetemp exists AND was modified within
#      STALE_SECS (600s default).  Gate stamps its basetemp on start; when done it
#      remains but goes stale.  A fresh mtime = gate is running.
#   2. PGREP: a python3 process with "pytest" in its args is running.  Fast, reliable.
#      (We can run pgrep inside a hook because hooks run as subprocesses, not as
#      harness tool calls subject to the make-only policy.)
#
# ENV OVERRIDES (for tests / CI):
#   GLUDD_GATE_STALE_SECS           — override the basetemp freshness window (default 600)
#   GLUDD_GATE_BASETEMP             — override the basetemp path (default /tmp/gludd-gate-basetemp)
#   GLUDD_GATE_PYTEST_RUNNING       — "1" = force-inject running state (skip pgrep/basetemp)
#                                     "0" = force-inject NOT-running state (skip pgrep/basetemp)
#
# SEVERITY: BLOCK (deny) — a second concurrent gate is never the right call and the
# failure mode (208 spurious errors, possible test corruption) is hard to diagnose.
#
# FAIL-OPEN: any unexpected error -> exit 0.
# CONTEXT-EFFICIENT: emits nothing when no gate is running (most turns).

BASETEMP="${GLUDD_GATE_BASETEMP:-/tmp/gludd-gate-basetemp}"
STALE_SECS="${GLUDD_GATE_STALE_SECS:-600}"

input="$(cat 2>/dev/null || echo '{}')"

# Step 1: is this a gate/test Bash command?
is_gate="$(printf '%s' "$input" | python3 -c '
import sys, json, re
try:
    d = json.load(sys.stdin)
    cmd = ((d.get("tool_input") or {}).get("command") or "").strip()
    # Match only make targets that actually invoke the FULL pytest suite.
    # Using (\s|$) instead of \b to avoid matching 'test' within 'test-hooks' or 'test-scripts'.
    # BLOCKED list: gate, test (bare), test-unit (bare), test-e2e, test-and-commit, qa, validate.
    #   validate runs the full gate (lint + typecheck + collect + test + ansible syntax).
    # EXEMPT (do not block even when pytest is running):
    #   - test-count: collection-only gate, no test bodies executed.
    #   - collect-check: collection-error check, no test bodies executed.
    #   - test-hooks / test-stop-hooks: pure-bash harness, never launches pytest.
    #   - test-unit TESTFILE=<path>: targeted single-file run, not the full suite.
    pattern = r"^make\s+(gate|test|test-unit|test-e2e|test-and-commit|qa|validate)(\s|$)"
    m = re.match(pattern, cmd)
    if not m:
        print("no")
    elif m.group(1) == "test-unit" and re.search(r"TESTFILE=\S", cmd):
        # Targeted single-file test — does not contend with a full-suite gate.
        print("no")
    else:
        print("yes")
except Exception:
    print("no")
' 2>/dev/null)"

[ "$is_gate" = "yes" ] || exit 0

# Step 2: detect a running pytest.
pytest_running=0

# ENV OVERRIDE: test harness can inject the result directly
if [ "${GLUDD_GATE_PYTEST_RUNNING}" = "1" ]; then
    pytest_running=1
elif [ "${GLUDD_GATE_PYTEST_RUNNING}" = "0" ]; then
    pytest_running=0
else
    # Signal A: basetemp dir exists and is fresh.
    if [ -d "$BASETEMP" ]; then
        age_secs="$(python3 -c "
import os, time
try:
    mt = os.path.getmtime('${BASETEMP}')
    print(int(time.time() - mt))
except Exception:
    print(99999)
" 2>/dev/null)"
        case "$age_secs" in ''|*[!0-9]*) age_secs=99999 ;; esac
        if [ "$age_secs" -lt "$STALE_SECS" ]; then
            pytest_running=1
        fi
    fi

    # Signal B: pgrep for a running pytest process (belt-and-suspenders).
    if [ "$pytest_running" -eq 0 ]; then
        if pgrep -f "pytest" >/dev/null 2>&1; then
            pytest_running=1
        fi
    fi
fi

if [ "$pytest_running" -eq 1 ]; then
    reason="GATE CONCURRENCY VIOLATION: a pytest / gate run appears to already be in progress (basetemp ${BASETEMP} is fresh OR pgrep found a pytest process). Launching a second concurrent pytest triggers keep-last-3 basetemp rotation, which deletes the first gate's worker dirs mid-flight and produces hundreds of spurious FileNotFoundError errors (the 2026-06-15 208-error incident). Wait for the current gate to finish (SubagentStop notification, or check 'make ps-pytest'), then launch this one. This dispatch is BLOCKED."
    python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":sys.argv[1]}}))' "$reason" 2>/dev/null || true
    exit 0
fi

exit 0
