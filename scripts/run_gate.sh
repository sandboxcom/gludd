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

# GATE_LOCK_FILE/BASETEMP_PREFIX can be overridden in tests.  In normal use,
# derive both paths from the checkout namespace so gates from unrelated
# projects do not reject one another or share pytest scratch directories.
ARBITER_SCRIPT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/resource_arbiter.py"
PROJECT_NAMESPACE="${GLUDD_PROJECT_NAMESPACE:-}"
if [ -z "${PROJECT_NAMESPACE}" ]; then
    PROJECT_NAMESPACE="$(python3 "${ARBITER_SCRIPT}" namespace)"
fi
RESOURCE_BASE="${GLUDD_RESOURCE_ROOT:-${TMPDIR:-/tmp}/gludd-resources}"
RESOURCE_DIR="${RESOURCE_BASE%/}/${PROJECT_NAMESPACE}"
mkdir -p "${RESOURCE_DIR}"
# The historical basename gludd-gate.lock remains the documented resource;
# it is now nested below the project-specific directory.
LOCK_FILE="${GATE_LOCK_FILE:-${RESOURCE_DIR}/gate.lock}"
BASETEMP_PREFIX="${GATE_BASETEMP_PREFIX:-${RESOURCE_DIR}/gate}"
mkdir -p "$(dirname -- "${LOCK_FILE}")" "$(dirname -- "${BASETEMP_PREFIX}")"
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

    # A legacy PID-file test/launcher may record its parent shell rather than
    # the gate command.  Treat our immediate parent as the owner only when it
    # is alive; this keeps the compatibility path local and never signals it.
    if _pid_is_gate "${holder}" || {
        [ "${holder}" = "${PPID}" ] && kill -0 "${holder}" 2>/dev/null;
    }; then
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

# ``kill -0`` alone is unsafe: after a gate exits, the PID may be reused by a
# different project.  Verify both liveness and command identity before treating
# a PID-file owner as active.  This is deliberately project-scoped and fail-open
# so stale locks can be recovered without killing unrelated processes.
_pid_is_gate() {
    local pid="$1" command=""
    case "${pid}" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "${pid}" 2>/dev/null || return 1
    command=$(ps -p "${pid}" -o command= 2>/dev/null || true)
    case "${command}" in
        *run_gate.sh*|*"make gate"*|*gate-refresh*) return 0 ;;
        *) return 1 ;;
    esac
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
BASETEMP=$(mktemp -d "${BASETEMP_PREFIX}-XXXXXX")

# Per-invocation RC_FILE remains in the unique basetemp. The streamed log must
# live OUTSIDE pytest's --basetemp because pytest clears that directory at
# startup; putting the log there made a running gate unobservable after detach.
RC_FILE="${BASETEMP}/rc"
mkdir -p .gate-logs
LOG_FILE="${GATE_LOG_FILE:-.gate-logs/gate-pytest-$$.log}"
echo "[run_gate.sh] live log: ${LOG_FILE}"

# ---------------------------------------------------------------------------
# Run pytest (or the PYTEST_CMD stub for unit testing).
# ---------------------------------------------------------------------------
PYTEST_CMD=${PYTEST_CMD:-}

if [ -n "${PYTEST_CMD}" ]; then
    # set -e would abort the subshell on a non-zero eval before echo $? runs;
    # disable it inside the subshell so the exit code is always captured.
    ( set +e; eval "${PYTEST_CMD}"; echo $? > "${RC_FILE}" ) 2>&1 | tee "${LOG_FILE}"
else
    # HEAVY-OP CAP: route the gate's pytest through the SAME portable fcntl
    # semaphore (scripts/heavy_sem.py) and the SAME `gludd-heavy` slot pool +
    # HEAVY_MAX_PAR (default 3) that the standalone `make test*` recipes use, so
    # a gate's pytest can't push total heavy concurrency past the cap when agents
    # are also running tests. This COMPOSES with the gate's own flock above
    # (/tmp/gludd-gate.lock serializes whole gates; heavy_sem bounds total heavy
    # concurrency host-wide) — no deadlock, since the gate never re-acquires its
    # own lock.
    #
    # FRESH-PROCESS CI SHARDS: run the exact seven named GitHub Actions shards
    # serially. Each shard invokes adaptive_test.py in a new process, so imports
    # and native allocations cannot accumulate across all 50k tests. Per-shard
    # coverage databases are combined before the aggregate 85% and per-file 75%
    # release floors are enforced.
    ( set +e; python3 scripts/heavy_sem.py "${HEAVY_MAX_PAR:-3}" gludd-heavy -- \
        uv run python scripts/run_ci_shards_serial.py --pytest-args=-q; \
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
