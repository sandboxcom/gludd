#!/usr/bin/env bash
# run_test_background.sh — launch, poll, list, and kill background test runs
#
# USAGE:
#   bash scripts/run_test_background.sh <testfile>       # launch
#   bash scripts/run_test_background.sh status <testfile> # poll
#   bash scripts/run_test_background.sh list               # list all
#   bash scripts/run_test_background.sh kill <testfile>    # kill
#
# DESIGN:
#   Mirrors the gate-background pattern: nohup -> log file -> PID file.
#   Log:   .gate-logs/test-<sanitized>-<timestamp>.log
#   PID:   .gate-logs/.test-<sanitized>.pid
#   The "status" verb checks PID aliveness and tails the log.
#   The "list" verb globs .test-*.pid files and shows each with status.
#   The "kill" verb sends SIGTERM, waits 5s, then SIGKILL if still alive.

set -euo pipefail

LOG_DIR=".gate-logs"
mkdir -p "${LOG_DIR}"

_sanitize() {
    echo "$1" | sed 's/[^a-zA-Z0-9._-]/_/g'
}

_launch() {
    local testfile="$1"
    local sanitized
    sanitized=$(_sanitize "${testfile}")
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local log_file="${LOG_DIR}/test-${sanitized}-${timestamp}.log"
    local pid_file="${LOG_DIR}/.test-${sanitized}.pid"

    # Refuse if already running (stale PID file still has a live process)
    if [ -f "${pid_file}" ]; then
        local old_pid
        old_pid=$(cat "${pid_file}" 2>/dev/null || echo "")
        if [ -n "${old_pid}" ] && kill -0 "${old_pid}" 2>/dev/null; then
            echo "test-bg: already running for '${testfile}' (PID ${old_pid})"
            echo "  make test-bg-status TESTFILE='${testfile}'"
            exit 0
        fi
        rm -f "${pid_file}"
    fi

    echo "test-bg: launching test for '${testfile}'"
    echo "  log: ${log_file}"
    echo "  pid: ${pid_file}"

    nohup make test-specific TESTFILE="${testfile}" > "${log_file}" 2>&1 &
    echo $! > "${pid_file}"

    echo "test-bg: launched (PID $(cat "${pid_file}")); poll with: make test-bg-status TESTFILE='${testfile}'"
}

_status() {
    local testfile="$1"
    local sanitized
    sanitized=$(_sanitize "${testfile}")
    local pid_file="${LOG_DIR}/.test-${sanitized}.pid"

    if [ ! -f "${pid_file}" ]; then
        echo "test-bg-status: no background test for '${testfile}'"
        echo "  (no ${pid_file})"
        exit 1
    fi

    local pid
    pid=$(cat "${pid_file}" 2>/dev/null || echo "")
    if [ -z "${pid}" ]; then
        echo "test-bg-status: empty PID file for '${testfile}' — removing"
        rm -f "${pid_file}"
        exit 1
    fi

    if kill -0 "${pid}" 2>/dev/null; then
        echo "test-bg-status: RUNNING (PID ${pid})"
    else
        echo "test-bg-status: FINISHED (PID ${pid} not alive)"
    fi

    local log_file
    log_file=$(ls -t "${LOG_DIR}/test-${sanitized}"-*.log 2>/dev/null | head -1)
    if [ -n "${log_file}" ]; then
        echo "--- log: $(basename "${log_file}") ---"
        local terminal=""
        if grep -q '=== PASSED ===\| passed in \| PASSED ' "${log_file}" 2>/dev/null; then
            terminal="PASS"
        elif grep -q 'FAILED\|ERRORS\|^FAIL ' "${log_file}" 2>/dev/null; then
            terminal="FAIL"
        fi
        if [ -n "${terminal}" ]; then
            echo "--- RESULT: ${terminal} ---"
        else
            echo "--- (no terminal marker yet) ---"
        fi
        echo "--- last 15 log lines ---"
        tail -15 "${log_file}" 2>/dev/null || echo "(log empty)"
    else
        echo "(no log file found)"
    fi

    if kill -0 "${pid}" 2>/dev/null; then
        exit 0
    fi
    exit 0
}

_list() {
    local count=0
    for pid_file in "${LOG_DIR}"/.test-*.pid; do
        [ -f "${pid_file}" ] || continue
        count=$((count + 1))
        local sanitized
        sanitized=$(basename "${pid_file}" | sed 's/^\.test-//; s/\.pid$//')
        local pid
        pid=$(cat "${pid_file}" 2>/dev/null || echo "?")
        local testfile="${sanitized}"  # approximate — real testfile is in log header
        if kill -0 "${pid}" 2>/dev/null; then
            local log_file
            log_file=$(ls -t "${LOG_DIR}/test-${sanitized}"-*.log 2>/dev/null | head -1)
            local age=""
            if [ -n "${log_file}" ]; then
                age=$(stat -f '%Sm' "${log_file}" 2>/dev/null || stat -c '%y' "${log_file}" 2>/dev/null || echo "")
            fi
            printf "%-30s %-8s %-22s %s\n" "${sanitized}" "RUNNING" "${pid}" "${age}"
        else
            printf "%-30s %-8s %-22s %s\n" "${sanitized}" "FINISHED" "${pid}" ""
        fi
    done
    if [ "${count}" -eq 0 ]; then
        echo "(no background tests found)"
    fi
}

_kill() {
    local testfile="$1"
    local sanitized
    sanitized=$(_sanitize "${testfile}")
    local pid_file="${LOG_DIR}/.test-${sanitized}.pid"

    if [ ! -f "${pid_file}" ]; then
        echo "test-bg-kill: no background test for '${testfile}'"
        exit 0
    fi

    local pid
    pid=$(cat "${pid_file}" 2>/dev/null || echo "")
    if [ -z "${pid}" ]; then
        echo "test-bg-kill: empty PID file — removing"
        rm -f "${pid_file}"
        exit 0
    fi

    if ! kill -0 "${pid}" 2>/dev/null; then
        echo "test-bg-kill: PID ${pid} not alive — removing stale PID file"
        rm -f "${pid_file}"
        exit 0
    fi

    echo "test-bg-kill: sending SIGTERM to PID ${pid}"
    kill -TERM "${pid}" 2>/dev/null || true

    for i in 1 2 3 4 5; do
        sleep 1
        if ! kill -0 "${pid}" 2>/dev/null; then
            echo "test-bg-kill: PID ${pid} terminated after SIGTERM"
            rm -f "${pid_file}"
            exit 0
        fi
    done

    echo "test-bg-kill: SIGTERM did not take — sending SIGKILL to PID ${pid}"
    kill -KILL "${pid}" 2>/dev/null || true
    rm -f "${pid_file}"
    echo "test-bg-kill: PID ${pid} killed"
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------
case "${1:-}" in
    status)
        if [ -z "${2:-}" ]; then echo "Usage: $0 status <testfile>"; exit 1; fi
        _status "$2"
        ;;
    list)
        _list
        ;;
    kill)
        if [ -z "${2:-}" ]; then echo "Usage: $0 kill <testfile>"; exit 1; fi
        _kill "$2"
        ;;
    "")
        echo "Usage: $0 <testfile>          — launch background test"
        echo "       $0 status <testfile>    — check status"
        echo "       $0 list                 — list all background tests"
        echo "       $0 kill <testfile>      — kill a background test"
        exit 1
        ;;
    *)
        _launch "$1"
        ;;
esac
