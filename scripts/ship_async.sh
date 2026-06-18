#!/usr/bin/env bash
# ship_async.sh — non-blocking ship pipeline: acquire lock, run gate, ff-only merge.
#
# USAGE:
#   bash scripts/ship_async.sh <SHIP_REF> [TARGET]
#
# ARGS:
#   SHIP_REF  — ref (branch/hash) to ship onto TARGET (required).
#   TARGET    — branch to fast-forward to SHIP_REF (default: master).
#
# ENVIRONMENT OVERRIDES (for tests / CI):
#   GATE_CMD       — override the gate command (default: bash scripts/run_gate.sh)
#   SHIP_LOCK_FILE — override lock file path (default: /tmp/gludd-ship.lock)
#   REPO_DIR       — override the git working directory (default: .)
#   STATUS_FILE    — override the status output file (default: .ship-status)
#
# OUTPUT:
#   Appends a single line to STATUS_FILE:
#     "SHIP PASS <SHIP_REF>"   — gate passed, TARGET fast-forwarded.
#     "SHIP FAIL gate"         — gate failed, TARGET untouched.
#     "SHIP FAIL not-ff"       — gate passed but ff-only merge left TARGET != SHIP_REF.
#
# CONCURRENCY:
#   Exclusive lock prevents two simultaneous ship runs. Second invocation exits
#   non-zero immediately with a clear message to stderr.

set -euo pipefail

SHIP_REF="${1:-}"
TARGET="${2:-master}"

if [ -z "${SHIP_REF}" ]; then
    echo "Usage: bash scripts/ship_async.sh <SHIP_REF> [TARGET]" >&2
    exit 1
fi

LOCK_FILE="${SHIP_LOCK_FILE:-/tmp/gludd-ship.lock}"
STATUS_FILE="${STATUS_FILE:-.ship-status}"
REPO_DIR="${REPO_DIR:-.}"
GATE_CMD="${GATE_CMD:-bash scripts/run_gate.sh}"

# ---------------------------------------------------------------------------
# Cleanup trap: release lock on any exit.
# ---------------------------------------------------------------------------
_ship_cleanup() {
    local rc=$?
    # Release flock fd 201 (no-op if we used the pidfile path).
    exec 201>&- 2>/dev/null || true
    # Remove the pidfile if we own it (check by comparing PID inside).
    local holder
    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
    if [ "${holder}" = "$$" ]; then
        rm -f "${LOCK_FILE}" 2>/dev/null || true
    fi
    exit "${rc}"
}
trap _ship_cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Lock acquisition: mirror run_gate.sh pattern.
# ---------------------------------------------------------------------------
_acquire_ship_flock() {
    if flock --nonblock 201; then
        printf '%s\n' "$$" > "${LOCK_FILE}" 2>/dev/null || true
        return 0
    fi
    local holder
    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
    echo "[ship_async] another ship is already running (PID ${holder}); refusing to start a second" >&2
    exec 201>&- 2>/dev/null || true
    exit 1
}

_acquire_ship_pidfile() {
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
        echo "[ship_async] another ship is already running (PID ${holder}); refusing to start a second" >&2
        exit 1
    fi

    # Stale lock — remove and retry once.
    rm -f "${LOCK_FILE}"
    printf '%s\n' "$$" > "${tmp}"
    mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
    if [ ! -f "${tmp}" ]; then
        return 0
    fi
    rm -f "${tmp}"

    holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
    echo "[ship_async] another ship is already running (PID ${holder}); refusing to start a second" >&2
    exit 1
}

_has_gnu_flock() {
    flock --nonblock /dev/null true 2>/dev/null
}

if command -v flock >/dev/null 2>&1 && _has_gnu_flock; then
    exec 201>"${LOCK_FILE}"
    _acquire_ship_flock
else
    exec 201>/dev/null
    _acquire_ship_pidfile
fi

# ---------------------------------------------------------------------------
# Run gate (or stub).
# ---------------------------------------------------------------------------
echo "[ship_async] running gate: ${GATE_CMD}"
GATE_RC=0
( set +e; export GLUDD_GATE_AUTHORIZED=1; ( eval "${GATE_CMD}" ); echo $? > /tmp/gludd-ship-gate-rc.$$ ) || true
GATE_RC=$(cat /tmp/gludd-ship-gate-rc.$$ 2>/dev/null || echo 1)
rm -f /tmp/gludd-ship-gate-rc.$$

if [ "${GATE_RC}" -ne 0 ]; then
    echo "[ship_async] gate FAILED (exit ${GATE_RC}) — aborting ship, ${TARGET} untouched"
    echo "SHIP FAIL gate" >> "${STATUS_FILE}"
    exit 1
fi

echo "[ship_async] gate PASSED — fast-forwarding ${TARGET} to ${SHIP_REF}"

# ---------------------------------------------------------------------------
# Fast-forward merge: checkout TARGET, merge --ff-only SHIP_REF, verify.
# ---------------------------------------------------------------------------
git -C "${REPO_DIR}" checkout "${TARGET}"
git -C "${REPO_DIR}" merge --ff-only "${SHIP_REF}"

TARGET_SHA=$(git -C "${REPO_DIR}" rev-parse "${TARGET}")
SHIP_SHA=$(git -C "${REPO_DIR}" rev-parse "${SHIP_REF}")

if [ "${TARGET_SHA}" != "${SHIP_SHA}" ]; then
    echo "[ship_async] SHIP FAIL: ${TARGET} (${TARGET_SHA}) != ${SHIP_REF} (${SHIP_SHA}) after ff-only merge" >&2
    echo "SHIP FAIL not-ff" >> "${STATUS_FILE}"
    exit 1
fi

echo "[ship_async] SHIP PASS — ${TARGET} is now at ${SHIP_REF} (${TARGET_SHA})"
echo "SHIP PASS ${SHIP_REF}" >> "${STATUS_FILE}"
