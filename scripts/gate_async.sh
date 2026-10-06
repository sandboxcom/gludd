#!/usr/bin/env bash
# gate_async.sh — detached, pollable, non-blocking gate launcher
#
# DESIGN:
#   Acquires an exclusive flock on LOCK_FILE (default /tmp/gludd-gate-async.lock).
#   Refuses immediately (exit 1) if another gate-async is already running.
#   Writes STATUS_FILE immediately as "RUNNING <epoch> <pid>".
#   Runs the complete `make gate` in a child bash process so every preflight,
#   test, smoke, and attestation phase shares one interrupt boundary. `exit`
#   inside an injected GATE_CMD cannot
#   kill this status-writer (the ship_async bug pattern: eval in the same shell
#   lets `exit` bypass the status-writer; a child process cannot).
#   On completion preserves the full gate's terminal receipt; injected commands
#   that do not write one receive "PASS <epoch>" or "FAIL <epoch> rc=<n>".
#   Releases the lock when done.
#
# ENV OVERRIDES (for testing):
#   GATE_CMD        gate command to run (default: complete `make gate`)
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

# An injected command is a test seam, not proof that this launcher owns the
# checkout-wide gate-run lock.  Remember the distinction before applying the
# default so signal cleanup cannot claim an enclosing gate as its descendant.
GATE_CMD_IS_INJECTED=0
if [ "${GATE_CMD+x}" = "x" ]; then
    GATE_CMD_IS_INJECTED=1
fi
GATE_CMD="${GATE_CMD:-make gate gludd_watchdog_owned_gate=1}"
STATUS_FILE="${STATUS_FILE:-.gate-status}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${GLUDD_PROJECT_ROOT:-$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)}"
ARBITER_SCRIPT="${SCRIPT_DIR}/resource_arbiter.py"
GATE_KILL_SCRIPT="${SCRIPT_DIR}/kill_owned_gate.py"
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
LOCK_ACQUIRED=0
RUNNING_PUBLISHED=0
TERMINAL_PUBLISHED=0
GATE_CHILD_PID=""

# A PID in a portable lock file is only an owner claim, not proof that the
# process is still this gate.  PID reuse is common enough that kill -0 alone is
# unsafe: validate the command identity before refusing to reclaim the lock.
_pid_is_gate_async() {
    local pid="$1" command=""
    case "${pid}" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "${pid}" 2>/dev/null || return 1
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    case "${command}" in
        *gate_async.sh*|*"make gate-async"*) return 0 ;;
        *) return 1 ;;
    esac
}

_status_owner_pid() {
    local status=""
    [ -f "${STATUS_FILE}" ] || return 1
    status=$(head -n 1 "${STATUS_FILE}" 2>/dev/null || true)
    case "${status}" in
        RUNNING\ *\ [0-9]*) printf '%s\n' "${status##* }"; return 0 ;;
        *) return 1 ;;
    esac
}

# Replace status atomically.  Readers must never observe a truncated status
# line while a gate transitions from RUNNING to PASS/FAIL.
_write_status() {
    local content="$1" tmp="${STATUS_FILE}.${$}.tmp"
    printf '%s\n' "${content}" > "${tmp}"
    mv -f "${tmp}" "${STATUS_FILE}"
}

_status_is_successful() {
    [ -f "${STATUS_FILE}" ] || return 1
    grep -Eq '^(PASS([[:space:]]|$)|=== GATE: PASSED ===$)' "${STATUS_FILE}" 2>/dev/null
}

_status_is_terminal() {
    [ -f "${STATUS_FILE}" ] || return 1
    grep -Eq \
        '^(PASS([[:space:]]|$)|FAIL([[:space:]]|$)|ABORTED([[:space:]]|$)|=== GATE: (PASSED|FAILED|ABORTED.*) ===$)' \
        "${STATUS_FILE}" 2>/dev/null
}

_publish_non_success_terminal() {
    local content="$1"
    # A signal can arrive after PASS was atomically renamed but before the
    # main path records that publication in memory.  Disk is authoritative:
    # cleanup must never turn already-successful evidence red.
    if _status_is_terminal; then
        TERMINAL_PUBLISHED=1
        return 0
    fi
    _write_status "${content}"
    TERMINAL_PUBLISHED=1
}

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
    if [ "${GLUDD_GATE_ASYNC_FORCE_PIDFILE:-0}" != "1" ] \
        && command -v flock >/dev/null 2>&1 && _has_gnu_flock; then
        exec 200>"${LOCK_FILE}"
        if flock --nonblock 200; then
            printf '%s\n' "$$" > "${LOCK_FILE}" 2>/dev/null || true
            LOCK_ACQUIRED=1
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
            LOCK_ACQUIRED=1
            return 0
        fi
        rm -f "${tmp}"
        local holder
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "")
        if _pid_is_gate_async "${holder}"; then
            local status_owner
            status_owner=$(_status_owner_pid || true)
            if [ -n "${status_owner}" ] && [ "${status_owner}" != "${holder}" ]; then
                echo "[gate_async] live owner/status mismatch (lock PID ${holder}, status PID ${status_owner}); refusing." >&2
            else
                echo "[gate_async] another gate-async is already running (PID ${holder}); refusing." >&2
            fi
            exit 1
        fi
        # Stale or unrelated lock owner — remove and retry once.  The command
        # identity check above prevents reclaiming a live gate and prevents a
        # reused PID from blocking this project indefinitely.
        rm -f "${LOCK_FILE}"
        printf '%s\n' "$$" > "${tmp}"
        mv -n "${tmp}" "${LOCK_FILE}" 2>/dev/null || true
        if [ ! -f "${tmp}" ]; then
            LOCK_ACQUIRED=1
            return 0
        fi
        rm -f "${tmp}"
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || echo "unknown")
        echo "[gate_async] another gate-async is already running (PID ${holder}); refusing." >&2
        exit 1
    fi
}

_release_lock() {
    local holder=""
    if [ "${LOCK_ACQUIRED}" -eq 1 ]; then
        holder=$(cat "${LOCK_FILE}" 2>/dev/null || true)
        # Remove only our own ownership record.  This prevents late cleanup
        # from unlinking a replacement owner's lock after a status race.
        if [ "${holder}" = "$$" ]; then
            rm -f "${LOCK_FILE}" 2>/dev/null || true
        fi
        exec 200>&- 2>/dev/null || true
        LOCK_ACQUIRED=0
    fi
    rm -f "${RC_FILE}" 2>/dev/null || true
    rm -f "${STATUS_FILE}.${$}.tmp" 2>/dev/null || true
}

_terminate_owned_gate() {
    local cleanup_rc=0
    # Only the default whole-gate command can own this checkout's gate-run
    # lock.  Tests inject bounded stub commands while already running beneath a
    # real gate; asking the checkout-wide terminator to clean those stubs would
    # inspect the enclosing gate and falsely report (or attempt) foreign work.
    if [ "${GATE_CMD_IS_INJECTED}" -eq 0 ]; then
        # The whole-gate lock proves the descendant tree before any signal.
        # This maintained terminator performs bounded TERM/KILL escalation and
        # retains fail-closed ownership evidence if a survivor cannot stop.
        APPLY=1 GLUDD_PROJECT_ROOT="${PROJECT_ROOT}" \
            python3 "${GATE_KILL_SCRIPT}" || cleanup_rc=$?
    fi

    # A signal can land in the short admission window before `make gate`
    # publishes gate-run.lock.  An injected command owns only this direct child.
    # In both cases the PID is exact ownership; TERM it without guessing at
    # unrelated processes.
    if [ -n "${GATE_CHILD_PID}" ] && kill -0 "${GATE_CHILD_PID}" 2>/dev/null; then
        echo "[gate-kill] signal=SIGTERM direct-child pid=${GATE_CHILD_PID}"
        kill -TERM "${GATE_CHILD_PID}" 2>/dev/null || true
    fi
    return "${cleanup_rc}"
}

_handle_signal() {
    local signal_name="$1" signal_rc="$2" finish_epoch cleanup_rc=0
    # Repeated Ctrl-C/TERM must not interrupt the atomic terminal publication.
    trap '' INT TERM
    trap - EXIT
    set +e
    _terminate_owned_gate || cleanup_rc=$?
    finish_epoch=$(date +%s)
    if _status_is_successful; then
        TERMINAL_PUBLISHED=1
    else
        _write_status \
            "ABORTED ${finish_epoch} signal=${signal_name} rc=${signal_rc} cleanup_rc=${cleanup_rc}
=== GATE: ABORTED ==="
        TERMINAL_PUBLISHED=1
    fi
    echo "[gate_async] ABORTED signal=${signal_name} rc=${signal_rc} cleanup_rc=${cleanup_rc} at epoch=${finish_epoch}"
    _release_lock
    exit "${signal_rc}"
}

_handle_exit() {
    local rc=$? finish_epoch failure_rc
    trap - EXIT
    trap '' INT TERM
    set +e
    if [ "${RUNNING_PUBLISHED}" -eq 1 ] && [ "${TERMINAL_PUBLISHED}" -eq 0 ]; then
        finish_epoch=$(date +%s)
        failure_rc="${rc}"
        # An unexpected early `exit 0` is still incomplete, so fail closed.
        [ "${failure_rc}" -ne 0 ] || failure_rc=1
        _publish_non_success_terminal \
            "FAIL ${finish_epoch} rc=${failure_rc}
=== GATE: FAILED ==="
        rc="${failure_rc}"
    fi
    _release_lock
    exit "${rc}"
}

trap _handle_exit EXIT
trap '_handle_signal INT 130' INT
trap '_handle_signal TERM 143' TERM

# ---------------------------------------------------------------------------
# Acquire lock (refuses if already held)
# ---------------------------------------------------------------------------
_acquire_lock

# ---------------------------------------------------------------------------
# Write RUNNING status immediately
# ---------------------------------------------------------------------------
EPOCH=$(date +%s)
_write_status "RUNNING ${EPOCH} $$"
RUNNING_PUBLISHED=1
echo "[gate_async] started at epoch=${EPOCH} pid=$$ ref='${REF}' cmd='${GATE_CMD}'"

# ---------------------------------------------------------------------------
# Run the gate command as a child process via `bash -c`.
#
# KEY INVARIANT: running via `bash -c "..."` means any `exit` inside GATE_CMD
# exits the CHILD bash, not this script. This prevents the ship_async bug where
# `eval "exit 0"` in the same shell would skip the status-writer below.
#
# We capture the child's exact PID for signal cleanup and preserve the return
# value produced by `bash -c` (including 128-plus-signal values).
# ---------------------------------------------------------------------------
EXIT=0
GLUDD_GATE_AUTHORIZED=1 bash -c "${GATE_CMD}" &
GATE_CHILD_PID=$!
wait "${GATE_CHILD_PID}" || EXIT=$?
GATE_CHILD_PID=""

# ---------------------------------------------------------------------------
# Write final status and release lock
# ---------------------------------------------------------------------------
FINISH_EPOCH=$(date +%s)
if [ "${EXIT}" -eq 0 ]; then
    if ! _status_is_successful; then
        _write_status "PASS ${FINISH_EPOCH}"
    fi
    TERMINAL_PUBLISHED=1
    echo "[gate_async] PASS at epoch=${FINISH_EPOCH}"
else
    _publish_non_success_terminal \
        "FAIL ${FINISH_EPOCH} rc=${EXIT}
=== GATE: FAILED ==="
    echo "[gate_async] FAIL rc=${EXIT} at epoch=${FINISH_EPOCH}"
fi

_release_lock
exit "${EXIT}"
