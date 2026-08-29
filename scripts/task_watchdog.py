#!/usr/bin/env python3
"""task_watchdog.py — kills hung opencode tasks that exceed GLUDD_TASK_TIMEOUT_MS.

PROBLEM
-------
opencode dispatched tasks (task/agent/workflow) can run indefinitely, blocking
the entire agent queue. ``.opencode/plugin/enforce-deadline.ts`` detects breaches
but only WARNS — the plugin API has no kill primitive. This script is the
killing layer.

ARCHITECTURE
------------
::

    enforce-deadline.ts  →  /tmp/gludd-task-deadlines.json   (dispatch timestamps)
                        →  /tmp/gludd-task-stale.json        (breached task IDs)
    task_watchdog.py     →  reads both files
                        →  finds descendant processes older than timeout
                        →  SIGTERM → 5s wait → SIGKILL
                        →  /tmp/gludd-task-killed.json       (kill audit log)

Polls every 5 seconds (configurable via ``GLUDD_TASK_WATCHDOG_POLL``).
Every code path is fail-open: an error logs and continues — never crashes.

USAGE
-----
::

    make task-watchdog-start   # launch in background (nohup, PID tracked)
    make task-watchdog-stop    # kill background watchdog
    make task-watchdog-status  # show PID + last log lines
    make watchdog-auto         # also starts this watchdog alongside agent_watchdog
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from scripts.process_cleanup import descendant_processes, snapshot_processes
else:
    # ``make task-watchdog-start`` executes this file by path, where the
    # repository root is not on ``sys.path`` and the package import fails.
    try:
        from scripts.process_cleanup import descendant_processes, snapshot_processes
    except ModuleNotFoundError:  # pragma: no cover - exercised by direct launch
        from process_cleanup import descendant_processes, snapshot_processes

if TYPE_CHECKING:
    from scripts import gludd_env_defaults as gludd_env_defaults
else:
    try:
        from scripts import gludd_env_defaults as gludd_env_defaults
    except ModuleNotFoundError:  # pragma: no cover - direct launch from scripts/
        import gludd_env_defaults

DEADLINES_FILE = os.environ.get(
    "GLUDD_TASK_DEADLINE_STATE", "/tmp/gludd-task-deadlines.json"
)
STALE_FILE = os.environ.get("GLUDD_TASK_STALE_FILE", "/tmp/gludd-task-stale.json")
KILLED_FILE = os.environ.get("GLUDD_TASK_KILLED_FILE", "/tmp/gludd-task-killed.json")
WATCHDOG_PID_FILE = os.environ.get(
    "GLUDD_TASK_WATCHDOG_PID", ".gate-logs/task-watchdog.pid"
)
WATCHDOG_LOG = os.environ.get(
    "GLUDD_TASK_WATCHDOG_LOG", ".gate-logs/task-watchdog.log"
)

TIMEOUT_MS = int(os.environ.get("GLUDD_TASK_TIMEOUT_MS", gludd_env_defaults.TASK_TIMEOUT_MS_DEFAULT))
TIMEOUT_SECS = TIMEOUT_MS / 1000.0
POLL_SECS = int(os.environ.get("GLUDD_TASK_WATCHDOG_POLL", "5"))

GATE_PID_FILE = Path(os.environ.get("GLUDD_WORKSPACE_ROOT", os.getcwd())) / ".gate-background.pid"

# Processes matching these patterns are candidates for killing when they run
# longer than the timeout. These are the commands dispatched subagents execute.
# Conservative: only matches known task-runner commands, not arbitrary long-
# running daemons (opencode itself, the agent_watchdog, etc.).
TASK_PROCESS_PATTERNS = [
    re.compile(r"pytest"),
    re.compile(r"\bmake\b.*\b(?:test|gate|lint|typecheck|collect|ansible|molecule)\b"),
    re.compile(r"python3?.*-m\s+pytest"),
    re.compile(r"ansible-runner"),
    re.compile(r"ansible-playbook"),
    re.compile(r"\bmolecule\b"),
    re.compile(r"\buv\b.*(?:run|pip).*\b(?:pytest|python|test)\b"),
]

# NEVER kill processes matching these patterns (even if old).
EXCLUDE_PATTERNS = [
    re.compile(r"task_watchdog\.py"),
    re.compile(r"agent_watchdog\.py"),
    re.compile(r"gate-background"),
    re.compile(r"watchdog"),
]

_SELF_PID = os.getpid()


class StaleTask(TypedDict):
    """One deadline entry that exceeded its timeout."""

    task_id: str
    start_ms: float
    elapsed_ms: float
    timeout_ms: float


class HungProcess(TypedDict):
    """One verified task-like process that exceeded its timeout."""

    pid: int
    etime_secs: float
    command: str


class PollResult(TypedDict):
    """Counts emitted by one watchdog poll."""

    stale: int
    killed: int


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(WATCHDOG_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# State file readers (fail-open)
# ---------------------------------------------------------------------------

def load_deadlines(path: str = DEADLINES_FILE) -> dict[str, float]:
    """Load {task_id: epoch_ms} from the deadline state file.

    Handles the plugin format (flat dict of task_id → epoch_ms). Returns {} on
    any error (missing file, corrupt JSON, wrong shape) — fail-open.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in data.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def find_stale_tasks(
    deadlines: dict[str, float],
    timeout_ms: float = TIMEOUT_MS,
    now_ms: float | None = None,
) -> list[StaleTask]:
    """Return tasks whose elapsed wall-clock exceeds the timeout.

    Each entry: ``{task_id, start_ms, elapsed_ms, timeout_ms}``.
    """
    if now_ms is None:
        now_ms = time.time() * 1000.0
    stale: list[StaleTask] = []
    for tid, start_ms in deadlines.items():
        if start_ms <= 0:
            continue
        elapsed_ms = now_ms - start_ms
        if elapsed_ms > timeout_ms:
            stale.append({
                "task_id": tid,
                "start_ms": start_ms,
                "elapsed_ms": elapsed_ms,
                "timeout_ms": timeout_ms,
            })
    return stale


def load_stale_ids(path: str = STALE_FILE) -> set[str]:
    """Read the stale-file written by the plugin (breached task IDs).

    The plugin appends entries as a JSON list of ``{task_id, stale_at}`` dicts.
    Returns a set of task_id strings. Fail-open on any error.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return {str(e.get("task_id", "")) for e in data if isinstance(e, dict)}
        if isinstance(data, dict):
            return {str(k) for k in data}
        return set()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


# ---------------------------------------------------------------------------
# Process scanning
# ---------------------------------------------------------------------------

def _parse_etime(etime: str) -> float:
    """Parse ``ps`` ELAPSED column to seconds. Handles DD-HH:MM:SS, HH:MM:SS, MM:SS."""
    etime = etime.strip()
    try:
        if "-" in etime:
            day_part, time_part = etime.split("-", 1)
            days = int(day_part)
        else:
            days = 0
            time_part = etime
        parts = time_part.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            return float(etime)
        return days * 86400 + h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return 0.0


def _read_gate_pid(gate_pid_file: str = str(GATE_PID_FILE)) -> int | None:
    try:
        return int(Path(gate_pid_file).read_text().strip())
    except Exception:
        return None


def _descendant_pids(lines: list[str], root_pid: int) -> set[int]:
    """Return the gate process and every descendant represented in ``ps``."""
    parents: dict[int, int] = {}
    for line in lines:
        parts = line.strip().split(None, 3)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        parents[pid] = ppid

    excluded = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if pid not in excluded and ppid in excluded:
                excluded.add(pid)
                changed = True
    return excluded


def find_hung_processes(
    timeout_secs: float = TIMEOUT_SECS,
    gate_pid_file: str = str(GATE_PID_FILE),
) -> list[HungProcess]:
    """Scan ``ps`` for processes older than timeout matching task patterns.

    Returns ``[{pid, etime_secs, command}, ...]``. Excludes:
    - The watchdog itself (``_SELF_PID``)
    - The gate background process (has its own killer via ``agent_watchdog``)
    - Processes matching ``EXCLUDE_PATTERNS`` (watchdogs, daemons)

    Only processes matching ``TASK_PROCESS_PATTERNS`` are candidates — this is
    conservative so we never kill unrelated long-running system processes.
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,etime,command"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    gate_pid = _read_gate_pid(gate_pid_file)
    hung: list[HungProcess] = []
    lines = result.stdout.splitlines()[1:]  # skip header
    gate_tree = _descendant_pids(lines, gate_pid) if gate_pid is not None else set()

    for line in lines:
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        etime_str = parts[2]
        command = parts[3]

        elapsed = _parse_etime(etime_str)
        if elapsed < timeout_secs:
            continue
        if pid == _SELF_PID:
            continue
        if pid in gate_tree:
            continue
        if any(pat.search(command) for pat in EXCLUDE_PATTERNS):
            continue
        if not any(pat.search(command) for pat in TASK_PROCESS_PATTERNS):
            continue

        hung.append({
            "pid": pid,
            "etime_secs": elapsed,
            "command": command,
        })

    return hung


# ---------------------------------------------------------------------------
# Kill
# ---------------------------------------------------------------------------

def kill_process(pid: int, expected_command: str | None = None) -> bool:
    """Terminate a verified process tree, falling back to a direct PID.

    ``expected_command`` closes the PID-reuse race: a stale task record cannot
    kill a different project's process that inherited the same numeric PID.
    When a process snapshot is available, descendants are terminated first so
    pytest/uv workers cannot outlive their parent.
    """
    table = snapshot_processes()
    root = table.get(pid)
    if expected_command is not None and (root is None or root.command != expected_command):
        _log(f"TASK KILL SKIP: pid={pid} identity changed")
        return False

    candidates = [*descendant_processes(table, pid), root] if root else []
    if candidates:
        signalled = False
        for process in candidates:
            try:
                os.kill(process.pid, signal.SIGTERM)
                signalled = True
                _log(f"TASK KILL: SIGTERM → pid={process.pid}")
            except (ProcessLookupError, PermissionError):
                continue
        time.sleep(5)
        for process in candidates:
            try:
                os.kill(process.pid, signal.SIGKILL)
                _log(f"TASK KILL: SIGKILL → pid={process.pid}")
            except (ProcessLookupError, PermissionError):
                continue
        return signalled

    # Keep fail-open behavior for a process that exited between discovery and
    # cleanup, and for the small unit-test fake that has no ps row.
    try:
        os.kill(pid, signal.SIGTERM)
        _log(f"TASK KILL: SIGTERM → pid={pid}")
    except ProcessLookupError:
        return False
    except PermissionError:
        _log(f"TASK KILL: permission denied pid={pid}")
        return False

    time.sleep(5)

    try:
        os.kill(pid, signal.SIGKILL)
        _log(f"TASK KILL: SIGKILL → pid={pid}")
    except ProcessLookupError:
        pass  # SIGTERM worked — process already gone
    except PermissionError:
        pass

    return True


def record_kill(
    task_id: str,
    pid: int | None,
    elapsed_ms: float,
    reason: str,
    killed_file: str = KILLED_FILE,
) -> None:
    """Append a kill record to the audit log (JSON list)."""
    entry: dict[str, object] = {
        "task_id": task_id,
        "pid": pid,
        "elapsed_ms": round(elapsed_ms, 1),
        "reason": reason,
        "killed_at": time.time(),
    }
    try:
        existing: list[dict[str, object]] = []
        p = Path(killed_file)
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except json.JSONDecodeError:
                data = []
            if isinstance(data, list):
                existing = data
        existing.append(entry)
        p.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=p.parent,
                prefix=f".{p.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(json.dumps(existing, indent=2))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, p)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    except Exception as exc:
        _log(f"KILL RECORD ERROR: {exc}")


# ---------------------------------------------------------------------------
# Single poll cycle
# ---------------------------------------------------------------------------

def run_once(
    deadlines_file: str = DEADLINES_FILE,
    stale_file: str = STALE_FILE,
    killed_file: str = KILLED_FILE,
    timeout_ms: float = TIMEOUT_MS,
) -> PollResult:
    """One poll cycle. Returns ``{stale: N, killed: N}``.

    1. Load deadlines, find stale tasks.
    2. Find hung processes matching task patterns.
    3. Kill them, record kills.
    Fail-open: any error returns ``{stale: 0, killed: 0}``.
    """
    try:
        deadlines = load_deadlines(deadlines_file)
        if not deadlines:
            return {"stale": 0, "killed": 0}

        now_ms = time.time() * 1000.0
        stale = find_stale_tasks(deadlines, timeout_ms=timeout_ms, now_ms=now_ms)

        if not stale:
            return {"stale": 0, "killed": 0}

        _log(f"STALE TASKS: {len(stale)} task(s) over {timeout_ms/1000:.0f}s timeout")

        # Also cross-reference the plugin's stale file (populated by enforce-deadline.ts)
        stale_from_plugin = load_stale_ids(stale_file)
        if stale_from_plugin:
            _log(f"  plugin-flagged stale: {len(stale_from_plugin)} IDs")

        hung = find_hung_processes(timeout_secs=timeout_ms / 1000.0)
        killed = 0

        for proc in hung:
            killed_pid = kill_process(proc["pid"], expected_command=proc.get("command"))
            if killed_pid:
                killed += 1
                # Attribute to the oldest stale task (best-effort mapping)
                task_id = stale[0]["task_id"] if stale else "unknown"
                elapsed_ms = stale[0]["elapsed_ms"] if stale else proc["etime_secs"] * 1000
                record_kill(
                    task_id=task_id,
                    pid=proc["pid"],
                    elapsed_ms=elapsed_ms,
                    reason="task_timeout_exceeded",
                    killed_file=killed_file,
                )
                _log(
                    f"KILLED: pid={proc['pid']} cmd={proc['command'][:80]} "
                    f"elapsed={proc['etime_secs']:.0f}s"
                )

        return {"stale": len(stale), "killed": killed}

    except Exception as exc:
        _log(f"POLL ERROR: {exc}")
        return {"stale": 0, "killed": 0}


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def main() -> None:
    _log(
        f"task_watchdog started: timeout={TIMEOUT_SECS:.0f}s "
        f"poll={POLL_SECS}s state={DEADLINES_FILE}"
    )
    _write_pid()
    try:
        while True:
            run_once()
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        _log("task_watchdog stopped (interrupted)")
    finally:
        with suppress(Exception):
            Path(WATCHDOG_PID_FILE).unlink(missing_ok=True)


def _write_pid() -> None:
    with suppress(Exception):
        Path(WATCHDOG_PID_FILE).write_text(str(_SELF_PID))


if __name__ == "__main__":
    main()
