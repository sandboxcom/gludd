#!/usr/bin/env bash
# run_gate.sh — collision-proof pytest phase for `make gate`
#
# CONCURRENCY SAFETY:
#   1. Exclusive non-blocking lock on /tmp/gludd-gate.lock.
#      Uses GNU flock when available (Linux, macOS+brew util-linux); falls back
#      to an atomic PID-file approach on stock macOS (no util-linux).
#      A second invocation while the first holds the lock is REJECTED immediately
#      with "another gate is already running (PID …)" and exits non-zero WITHOUT
#      touching any basetemp dir — the first run is never disturbed.
#   2. Per-run unique basetemp (mktemp -d /tmp/gludd-gate-XXXXXX).
#      Even if the lock were bypassed, two concurrent runs land in DIFFERENT dirs
#      and can never collide on pytest's popen-gwN worker paths.
#   3. EXIT/INT/TERM trap removes the unique basetemp and releases the lock on
#      ANY exit so a killed/orphaned gate never holds a stale lock or leaks tmp.
#
# TESTABILITY:
#   Set PYTEST_CMD in the environment to override the real pytest invocation:
#     PYTEST_CMD='python3 -c "import sys; sys.exit(0)"' bash scripts/run_gate.sh
#   The test for this script uses that hook so it runs in milliseconds.
#
# USAGE: called by `make gate`; not meant to be invoked directly.

set -euo pipefail

# ---------------------------------------------------------------------------
# SUBAGENT GUARD: refuse full gate launches from subagent contexts.
#
# If either CLAUDE_AGENT_ID or GLUDD_SUBAGENT is set (indicating this shell
# was spawned inside a Claude subagent), refuse to run unless the caller has
# explicitly set GLUDD_GATE_AUTHORIZED=1.  This prevents a dying subagent from
# orphan-reaping and accidentally launching a full gate that would collide with
# the main session's gate.
# ---------------------------------------------------------------------------
if [ -n "${CLAUDE_AGENT_ID:-}" ] || [ -n "${GLUDD_SUBAGENT:-}" ]; then
    if [ "${GLUDD_GATE_AUTHORIZED:-0}" != "1" ]; then
        echo "run_gate.sh: refusing full gate from subagent context (gates must be launched by the main session); set GLUDD_GATE_AUTHORIZED=1 to override" >&2
        exit 2
    fi
fi

# GATE_LOCK_FILE can be overridden in tests to give each test an isolated lock.
LOCK_FILE="${GATE_LOCK_FILE:-/tmp/gludd-gate.lock}"
LOG_FILE=/tmp/gludd-test-gate.txt
RC_FILE=/tmp/gludd-gate-rc
STATUS_FILE=.gate-status
FAILED_FILE=.gate-failed

# Unique per-run basetemp — created AFTER the lock is held (see below) so a
# rejected second invocation never creates (nor has to clean up) a basetemp dir.
# Empty until then; the trap guards on emptiness.
BASETEMP=""

# --- Cleanup trap: remove unique basetemp + release lock on any exit ---
_cleanup() {
    local rc=$?
    [ -n "${BASETEMP}" ] && rm -rf "${BASETEMP}" 2>/dev/null
    true
    # Releasing fd 200 (flock path) is a no-op when the PID-file path was used.
    exec 200>&- 2>/dev/null || true
    exit "${rc}"
}
trap _cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Lock acquisition: two paths depending on whether GNU flock is present.
# ---------------------------------------------------------------------------
_acquire_flock() {
    # fd 200 must already be open on LOCK_FILE before this is called.
    if flock --nonblock 200; then
        # We hold the lock; stamp our PID for diagnostics.
        printf '%s\n' "$$" > "${LOCK_FILE}" 2>/dev/null || true
        return 0
    fi
    local holder
    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
    echo "[run_gate.sh] another gate is already running (PID ${holder}); refusing to start a second" >&2
    exec 200>&- 2>/dev/null || true
    exit 1
}

_acquire_pidfile() {
    # Atomic create-or-fail: write our PID to a tmp file, then try to rename it
    # into place. On POSIX local filesystems rename(2) is atomic.
    # NOTE: BSD mv -n (macOS) returns 0 even when it refuses to overwrite, unlike
    # GNU mv -n which returns 1. We detect the no-op by checking whether tmp
    # still exists after the mv (if it does, the rename was skipped).
    local tmp="${LOCK_FILE}.${$}.tmp"
    printf '%s\n' "$$" > "${tmp}"
    mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
    if [ ! -f "${tmp}" ]; then
        # tmp was renamed into LOCK_FILE — we won the race.
        return 0
    fi
    rm -f "${tmp}"

    # Something else owns the lockfile.
    local holder
    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")

    if [ -n "${holder}" ] && kill -0 "${holder}" 2>/dev/null; then
        echo "[run_gate.sh] another gate is already running (PID ${holder}); refusing to start a second" >&2
        exit 1
    fi

    # Stale lock (process is dead) — remove and retry once.
    rm -f "${LOCK_FILE}"
    printf '%s\n' "$$" > "${tmp}"
    mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
    if [ ! -f "${tmp}" ]; then
        return 0
    fi
    rm -f "${tmp}"

    # Still lost — another live gate took it between our retries.
    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
    echo "[run_gate.sh] another gate is already running (PID ${holder}); refusing to start a second" >&2
    exit 1
}

# Detect GNU flock (util-linux) vs BSD flock (macOS /usr/bin/flock).
# BSD flock does not support --nonblock; only GNU flock does.
# We test by probing --nonblock on /dev/null rather than trusting uname alone,
# since a macOS user may have installed GNU flock via brew.
_has_gnu_flock() {
    flock --nonblock /dev/null true 2>/dev/null
}

if command -v flock >/dev/null 2>&1 && _has_gnu_flock; then
    # GNU flock available — use kernel-level exclusive lock on fd 200.
    exec 200>"${LOCK_FILE}"
    _acquire_flock
else
    # No GNU flock (stock macOS) — close the unused fd and use PID-file approach.
    exec 200>/dev/null
    _acquire_pidfile
fi

# Lock is now held — safe to create the unique per-run basetemp. Doing this
# AFTER lock acquisition guarantees a rejected second invocation leaves no
# basetemp dir behind at all (the rejection paths above exit before this line).
BASETEMP=$(mktemp -d /tmp/gludd-gate-XXXXXX)

# ---------------------------------------------------------------------------
# Run pytest (or the PYTEST_CMD stub for unit testing).
# ---------------------------------------------------------------------------
PYTEST_CMD=${PYTEST_CMD:-}

# Compute memory-bounded worker count via scripts/gate_worker_count.py.
# Falls back to 1 if the script is missing or errors.
XDIST_WORKERS=$(python3 "$(dirname "$0")/gate_worker_count.py" 2>/dev/null || echo "1")

if [ -n "${PYTEST_CMD}" ]; then
    # set -e would abort the subshell on a non-zero eval before echo $? runs;
    # disable it inside the subshell so the exit code is always captured.
    ( set +e; eval "${PYTEST_CMD}"; echo $? > "${RC_FILE}" ) 2>&1 | tee "${LOG_FILE}"
else
    ( set +e; uv run python -m pytest tests/ \
        -n "${XDIST_WORKERS}" --dist loadgroup -q \
        --basetemp="${BASETEMP}"; \
      echo $? > "${RC_FILE}" ) 2>&1 | tee "${LOG_FILE}"
fi

# The pipe above exits with tee's code (always 0); read pytest's real exit.
EXIT=$(cat "${RC_FILE}" 2>/dev/null || echo 1)

if [ "${EXIT}" -eq 0 ]; then
    echo "PASS 0" >> "${STATUS_FILE}"
else
    echo "FAIL non-zero-exit" >> "${STATUS_FILE}"
    touch "${FAILED_FILE}"
fi

exit "${EXIT}"
