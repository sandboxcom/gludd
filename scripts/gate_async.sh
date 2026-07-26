#!/usr/bin/env bash
# gate_async.sh — detached, pollable, non-blocking gate launcher
#
# DESIGN:
#   Acquires an exclusive flock on LOCK_FILE (default /tmp/gludd-gate-async.lock).
#   Refuses immediately (exit 1) if another gate-async is already running.
#   Writes STATUS_FILE immediately as "RUNNING <epoch> <pid>".
#   Runs GATE_CMD in a child bash -c process so `exit` inside the cmd cannot
#   kill this status-writer (the ship_async bug pattern: eval in the same shell
#   lets `exit` bypass the status-writer; a child process cannot).
#   On completion writes STATUS_FILE as "PASS <epoch>" or "FAIL <epoch> rc=<n>".
#   Releases the lock when done.
#
# ENV OVERRIDES (for testing):
#   GATE_CMD        gate command to run (default: scripts/run_gate.sh)
#   STATUS_FILE     file to write status into (default: .gate-status)
#   LOCK_FILE       flock lock file path (default: /tmp/gludd-gate-async.lock)
#
# USAGE:
#   bash scripts/gate_async.sh [REF/label]
#   Called by `make gate-async` — not meant to be invoked directly.
#
# SUBAGENT GUARD: pass GLUDD_GATE_AUTHORIZED=1 if running from a subagent context
# (same guard as run_gate.sh).

set -euo pipefail

REF="${1:-}"

GATE_CMD="${GATE_CMD:-scripts/run_gate.sh}"
STATUS_FILE="${STATUS_FILE:-.gate-status}"
ARBITER_SCRIPT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/resource_arbiter.py"
PROJECT_NAMESPACE="${GLUDD_PROJECT_NAMESPACE:-}"
if [ -z "${PROJECT_NAMESPACE}" ]; then
    PROJECT_NAMESPACE="$(python3 "${ARBITER_SCRIPT}" namespace)"
fi
RESOURCE_BASE="${GLUDD_RESOURCE_ROOT:-${TMPDIR:-/tmp}/gludd-resources}"
RESOURCE_DIR="${RESOURCE_BASE%/}/${PROJECT_NAMESPACE}"
mkdir -p "${RESOURCE_DIR}"
# LOCK_FILE remains overrideable for isolated tests; otherwise it is scoped to
# this checkout rather than the historical global /tmp/gludd-gate-async.lock.
LOCK_FILE="${LOCK_FILE:-${RESOURCE_DIR}/async-gate.lock}"
mkdir -p "$(dirname -- "${LOCK_FILE}")"
RC_FILE="${LOCK_FILE}.rc.$$"

# ---------------------------------------------------------------------------
# Lock acquisition (flock-based, same pattern as run_gate.sh)
# ---------------------------------------------------------------------------
_has_gnu_flock() {
    # Probe against a PRIVATE temp path, never the shared /dev/null: flocking a
    # global path lets concurrent callers (xdist workers, or the two processes in
    # the concurrent-refusal test) transiently fail the probe and diverge into the
    # pid-file fallback branch, which holds no kernel lock — breaking mutual
    # exclusion. A private path makes the branch selection deterministic.
    local _probe
    _probe="$(mktemp 2>/dev/null)" || return 1
    flock --nonblock "${_probe}" true 2>/dev/null
    local _rc=$?
    rm -f "${_probe}" 2>/dev/null || true
    return "${_rc}"
}

_acquire_lock() {
    if command -v flock >/dev/null 2>&1 && _has_gnu_flock; then
        exec 200>"${LOCK_FILE}"
        if flock --nonblock 200; then
            printf '%s\n' "$$" > "${LOCK_FILE}" 2>/dev/null || true
            return 0
        fi
        local holder
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
        echo "[gate_async] another gate-async is already running (PID ${holder}); refusing." >&2
        exec 200>&- 2>/dev/null || true
        exit 1
    else
        # PID-file fallback (stock macOS without GNU flock)
        local tmp="${LOCK_FILE}.${$}.tmp"
        printf '%s\n' "$$" > "${tmp}"
        mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
        if [ ! -f "${tmp}" ]; then
            return 0
        fi
        rm -f "${tmp}"
        local holder
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
        if [ -n "${holder}" ] && kill -0 "${holder}" 2>/dev/null; then
            echo "[gate_async] another gate-async is already running (PID ${holder}); refusing." >&2
            exit 1
        fi
        # Stale lock — remove and retry once
        rm -f "${LOCK_FILE}"
        printf '%s\n' "$$" > "${tmp}"
        mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
        if [ ! -f "${tmp}" ]; then
            return 0
        fi
        rm -f "${tmp}"
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
        echo "[gate_async] another gate-async is already running (PID ${holder}); refusing." >&2
        exit 1
    fi
}

_release_lock() {
    exec 200>&- 2>/dev/null || true
    rm -f "${LOCK_FILE}" 2>/dev/null || true
    rm -f "${RC_FILE}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Acquire lock (refuses if already held)
# ---------------------------------------------------------------------------
_acquire_lock

# ---------------------------------------------------------------------------
# Write RUNNING status immediately
# ---------------------------------------------------------------------------
EPOCH=$(date +%s)
printf 'RUNNING %s %s\n' "${EPOCH}" "$$" > "${STATUS_FILE}"
echo "[gate_async] started at epoch=${EPOCH} pid=$$ ref='${REF}' cmd='${GATE_CMD}'"

# ---------------------------------------------------------------------------
# Run the gate command as a child process via `bash -c`.
#
# KEY INVARIANT: running via `bash -c "..."` means any `exit` inside GATE_CMD
# exits the CHILD bash, not this script. This prevents the ship_async bug where
# `eval "exit 0"` in the same shell would skip the status-writer below.
#
# We capture the child's exit code directly from `bash -c` return value.
# ---------------------------------------------------------------------------
EXIT=0
GLUDD_GATE_AUTHORIZED=1 bash -c "${GATE_CMD}" || EXIT=$?

# ---------------------------------------------------------------------------
# Write final status and release lock
# ---------------------------------------------------------------------------
FINISH_EPOCH=$(date +%s)
if [ "${EXIT}" -eq 0 ]; then
    printf 'PASS %s\n' "${FINISH_EPOCH}" > "${STATUS_FILE}"
    echo "[gate_async] PASS at epoch=${FINISH_EPOCH}"
else
    printf 'FAIL %s rc=%s\n' "${FINISH_EPOCH}" "${EXIT}" > "${STATUS_FILE}"
    echo "[gate_async] FAIL rc=${EXIT} at epoch=${FINISH_EPOCH}"
fi

_release_lock
exit "${EXIT}"
