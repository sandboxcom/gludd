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
    make task-watchdog-stop    # plan; TASK_WATCHDOG_VALIDATE_ONLY=0 applies verified cleanup
    make task-watchdog-status  # show PID + last log lines
    make watchdog-auto         # also starts this watchdog alongside agent_watchdog
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import active_work_status as inventory_owner
    import gludd_env_defaults as gludd_env_defaults
    import kill_owned_gate as cleanup_owner
    from active_work_status import _repository_roots
elif __package__:
    from scripts import active_work_status as inventory_owner
    from scripts import gludd_env_defaults as gludd_env_defaults
    from scripts import kill_owned_gate as cleanup_owner
    from scripts.active_work_status import _repository_roots
else:  # pragma: no cover - direct script execution
    import active_work_status as inventory_owner
    import gludd_env_defaults as gludd_env_defaults
    import kill_owned_gate as cleanup_owner
    from active_work_status import _repository_roots

_OWNERSHIP_POLICY = "registered-cwd-start-identity-observer-protection-v1"

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
GATE_RUN_LOCK_FILE = (
    Path(os.environ.get("GLUDD_WORKSPACE_ROOT", os.getcwd()))
    / ".gate-logs"
    / "gate-run.lock"
)

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
    """One diagnostic elapsed/name candidate, without destructive authority."""

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


def _read_gate_run_lock_pid(
    gate_run_lock_file: str = str(GATE_RUN_LOCK_FILE),
) -> int | None:
    """Return the active foreground gate owner recorded by ``gate_run_lock``."""
    try:
        payload = json.loads(Path(gate_run_lock_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return int(payload["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
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
    gate_run_lock_file: str = str(GATE_RUN_LOCK_FILE),
    repository_roots: tuple[Path, ...] = (),
) -> list[HungProcess]:
    """Scan ``ps`` for processes older than timeout matching task patterns.

    Returns ``[{pid, etime_secs, command}, ...]``. Excludes:
    - The watchdog itself (``_SELF_PID``)
    - Active foreground and background gates in every registered worktree,
      which own their process trees
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

    gate_pids = {
        pid
        for pid in (
            _read_gate_pid(gate_pid_file),
            _read_gate_run_lock_pid(gate_run_lock_file),
        )
        if pid is not None
    }
    for root in repository_roots:
        gate_pids.update(
            pid
            for pid in (
                _read_gate_pid(str(root / ".gate-background.pid")),
                _read_gate_run_lock_pid(str(root / ".gate-logs" / "gate-run.lock")),
            )
            if pid is not None
        )
    hung: list[HungProcess] = []
    lines = result.stdout.splitlines()[1:]  # skip header
    gate_tree: set[int] = set()
    for gate_pid in gate_pids:
        gate_tree.update(_descendant_pids(lines, gate_pid))

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

def _native_start_is_stale(record: cleanup_owner.ProcessRecord, *, now_seconds: float, timeout_seconds: float) -> bool:
    """Corroborate elapsed diagnostics with the fresh native start timestamp."""
    try:
        started = time.mktime(time.strptime(record.started_at, "%a %b %d %H:%M:%S %Y"))
    except (OverflowError, ValueError):
        return False
    return now_seconds - started >= timeout_seconds


def _owned_cwd(record: cleanup_owner.ProcessRecord, roots: tuple[Path, ...]) -> Path | None:
    """Require a live cwd equal to a native registered checkout, never a prefix."""
    cwd = cleanup_owner._process_cwd(record.pid)
    if cwd is None:
        return None
    resolved = cwd.resolve()
    return resolved if resolved in {root.resolve() for root in roots} else None


def _script_role(record: cleanup_owner.ProcessRecord, root: Path, name: str, *, uv: bool = False) -> bool:
    """Recognize the exact native Python script, including legacy relative argv."""
    try:
        tokens = shlex.split(record.command)
    except ValueError:
        return False
    if uv:
        if len(tokens) < 4 or Path(tokens[0]).name != "uv" or tokens[1] != "run":
            return False
        tokens = tokens[2:]
    if len(tokens) < 2 or re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(tokens[0]).name) is None:
        return False
    expected = root / "scripts" / name
    if expected.is_symlink() or not expected.is_file():
        return False
    return (root / tokens[1]).resolve() == expected.resolve()


def _protected_pids(records: list[cleanup_owner.ProcessRecord], roots: tuple[Path, ...]) -> set[int]:
    """Use native observer metadata conservatively; display absence grants nothing."""
    paths = inventory_owner._observer_status_paths(roots)
    if len(paths) >= inventory_owner._OBSERVER_STATUS_LIMIT:
        raise ValueError("native observer status inventory reached its display cap")
    output = "\n".join(f"{record.pid} {record.ppid} {record.command}" for record in records)
    observed = inventory_owner._observer_owned_processes_from_output(output, repository_roots=roots)
    if len(observed) >= inventory_owner._OBSERVER_PROCESS_LIMIT:
        raise ValueError("native observer process inventory reached its display cap")
    owners = {int(row["pid"]) for row in observed if row["observer_role"] == "owner"}
    for record in records:
        # This is deny-only discovery. A live native observer omitted by damaged,
        # missing or capped status cannot turn an ordinary candidate into authority.
        if "stream_command.py" not in record.command:
            continue
        cwd = _owned_cwd(record, roots)
        if cwd is not None and _script_role(record, cwd, "stream_command.py") and record.pid not in owners:
            raise ValueError("live native observer is absent from protection inventory")
    by_pid = {record.pid: record for record in records}
    protected: set[int] = set()
    for root in roots:
        owners.update(pid for pid in (
            _read_gate_pid(str(root / ".gate-background.pid")),
            _read_gate_run_lock_pid(str(root / ".gate-logs/gate-run.lock")),
        ) if pid in by_pid)
    for pid in owners:
        owner = by_pid[pid]
        protected.update(target.record.pid for target in cleanup_owner._tree_targets(records, owner))
        parent = owner.ppid
        seen: set[int] = set()
        while parent in by_pid and parent not in seen:
            seen.add(parent)
            ancestor = by_pid[parent]
            try:
                tokens = shlex.split(ancestor.command)
            except ValueError:
                raise ValueError("native observer ancestor command is unreadable") from None
            if tokens and Path(tokens[0]).name in {"make", "gmake", "uv"}:
                protected.add(parent)
            parent = ancestor.ppid
    return protected


def _terminate_verified(
    expected: cleanup_owner.ProcessRecord, roots: tuple[Path, ...], *, apply: bool = True,
) -> bool:
    """Reuse the native tree terminator with fresh ownership before every signal."""
    if not roots or not all(root.is_absolute() and root.is_dir() for root in roots):
        raise ValueError("registered checkout inventory is incomplete")
    records = cleanup_owner._records(roots[0])
    current = next((record for record in records if record.pid == expected.pid), None)
    if current is None or not cleanup_owner._same_identity(expected, current):
        raise ValueError("task process start identity changed")
    targets = cleanup_owner._tree_targets(records, current)
    expected_by_pid = {target.record.pid: target.record for target in targets}
    terminated_parents: set[int] = set()

    def reader() -> list[cleanup_owner.ProcessRecord]:
        fresh = cleanup_owner._records(roots[0])
        protected = _protected_pids(fresh, roots)
        live_owner = next((record for record in fresh if record.pid == expected.pid), None)
        if live_owner is not None and any(
            target.record.pid not in expected_by_pid for target in cleanup_owner._tree_targets(fresh, live_owner)
        ):
            raise ValueError("task tree acquired an unverified child")
        for record in fresh:
            original = expected_by_pid.get(record.pid)
            if original is None:
                continue
            reparented_by_cleanup = (
                original.ppid in terminated_parents
                and all(parent.pid != original.ppid for parent in fresh)
            )
            if (not cleanup_owner._same_identity(original, record)
                    or (original.ppid != record.ppid and not reparented_by_cleanup)
                    or _owned_cwd(record, roots) is None or record.pid in protected):
                raise ValueError("task tree ownership or native protection changed")
        return fresh

    # Validate the complete tree, including children, before the first signal.
    reader()
    if not apply:
        return True

    def sender(pid: int, signum: signal.Signals) -> None:
        # _signal_targets rechecks start/command identity; refresh cwd/protection
        # once more at the actual signal boundary, including escalation.
        fresh = reader()
        record = next((record for record in fresh if record.pid == pid), None)
        if record is None:
            raise ProcessLookupError(pid)
        os.kill(pid, signum)
        if signum == signal.SIGTERM:
            terminated_parents.add(pid)

    term_pids, skipped = cleanup_owner._signal_targets(
        targets, signal.SIGTERM, records_reader=reader, signal_sender=sender,
    )
    survivors = cleanup_owner._wait_for_exit(
        targets, timeout=5, poll_seconds=0.1, records_reader=reader,
        monotonic=time.monotonic, sleep=time.sleep,
    )
    if survivors:
        _killed, kill_skipped = cleanup_owner._signal_targets(
            survivors, signal.SIGKILL, records_reader=reader, signal_sender=sender,
        )
        skipped.extend(kill_skipped)
        survivors = cleanup_owner._wait_for_exit(
            survivors, timeout=1, poll_seconds=0.1, records_reader=reader,
            monotonic=time.monotonic, sleep=time.sleep,
        )
    return bool(term_pids) and not skipped and not survivors


def kill_process(
    pid: int, expected_command: str | None = None, *,
    expected_record: cleanup_owner.ProcessRecord | None = None,
    repository_roots: tuple[Path, ...] | None = None,
) -> bool:
    """Terminate only a fresh, positively owned identity; never fall back to a PID."""
    try:
        roots = _repository_roots() if repository_roots is None else repository_roots
        records = cleanup_owner._records(roots[0]) if roots else []
        record = next((record for record in records if record.pid == pid), None)
        if record is None or (expected_command is not None and record.command != expected_command):
            return False
        expected = expected_record or record
        return _terminate_verified(expected, roots)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        _log(f"TASK KILL SKIP: pid={pid} ownership refused: {exc}")
        return False


def stop_owned_watchdog(project_root: Path, *, apply: bool) -> bool:
    """Plan or drain one exact old daemon and matching UV launcher, without restart."""
    root = project_root.resolve()
    marker = root / ".gate-logs/task-watchdog.pid"
    if not marker.exists() and not marker.is_symlink():
        print("TASK-WATCHDOG-STOP absent writes=false signals=false", flush=True)
        return True
    try:
        if marker.is_symlink() or not marker.is_file() or root not in {
            path.resolve() for path in _repository_roots()
        }:
            raise ValueError("watchdog PID pointer is not checkout-owned")
        recorded_pid = marker.read_text(encoding="utf-8")
        pid = int(recorded_pid.strip())
        records = cleanup_owner._records(root)
        daemon = next((record for record in records if record.pid == pid), None)
        if (daemon is None or not daemon.started_at or _owned_cwd(daemon, (root,)) != root
                or not _script_role(daemon, root, "task_watchdog.py")):
            raise ValueError("watchdog PID pointer has no verified native daemon")
        if len(shlex.split(daemon.command)) != 2:
            raise ValueError("watchdog daemon command is not the native singleton")
        parent = next((record for record in records if record.pid == daemon.ppid), None)
        owner = daemon
        if parent is not None and _script_role(parent, root, "task_watchdog.py", uv=True):
            if (not parent.started_at or _owned_cwd(parent, (root,)) != root
                    or len(shlex.split(parent.command)) != 4):
                raise ValueError("watchdog UV launcher ownership is incomplete")
            owner = parent
        if marker.read_text(encoding="utf-8") != recorded_pid:
            raise ValueError("watchdog PID pointer changed before cleanup")
        if not _terminate_verified(owner, (root,), apply=apply):
            raise ValueError("watchdog lifecycle did not fully drain")
        # The old daemon must be fully gone before the existing start owner
        # can create a replacement and its new PID pointer.
        if apply and marker.exists():
            if marker.read_text(encoding="utf-8") != recorded_pid:
                raise ValueError("watchdog PID pointer was replaced during cleanup")
            marker.unlink()
        print(f"TASK-WATCHDOG-STOP verified apply={apply} old_tree_drained={apply}", flush=True)
        return True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"TASK-WATCHDOG-STOP refused: {exc}", flush=True)
        return False


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

        # The deadline file is host-global, so the destructive scan must honor
        # gate ownership across the repository's complete registered worktree
        # set.  If Git cannot establish that set, fail safe without killing an
        # ambiguously owned process.
        repository_roots = _repository_roots()
        hung = find_hung_processes(
            timeout_secs=timeout_ms / 1000.0,
            repository_roots=repository_roots,
        )
        killed = 0
        records = cleanup_owner._records(repository_roots[0]) if hung and repository_roots else []
        by_pid = {record.pid: record for record in records}
        protected = _protected_pids(records, repository_roots) if hung else set()

        for proc in hung:
            record = by_pid.get(proc["pid"])
            if (record is None or record.command != proc["command"]
                    or not _native_start_is_stale(record, now_seconds=now_ms / 1000, timeout_seconds=timeout_ms / 1000)
                    or _owned_cwd(record, repository_roots) is None or record.pid in protected):
                continue
            killed_pid = kill_process(
                proc["pid"], expected_command=proc["command"], expected_record=record,
                repository_roots=repository_roots,
            )
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

def main(argv: list[str] | None = None) -> int:
    """Run the existing daemon or plan/drain its verified checkout-owned tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--validate-only", choices=("0", "1"), default="1")
    args = parser.parse_args(argv)
    if args.stop:
        return 0 if stop_owned_watchdog(Path(__file__).resolve().parents[1], apply=args.validate_only == "0") else 1
    _log(
        f"task_watchdog started: timeout={TIMEOUT_SECS:.0f}s "
        f"poll={POLL_SECS}s state={DEADLINES_FILE} ownership_policy={_OWNERSHIP_POLICY}"
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
            if _read_gate_pid(WATCHDOG_PID_FILE) == _SELF_PID:
                Path(WATCHDOG_PID_FILE).unlink(missing_ok=True)
    return 0


def _write_pid() -> None:
    with suppress(Exception):
        Path(WATCHDOG_PID_FILE).write_text(str(_SELF_PID))


if __name__ == "__main__":
    raise SystemExit(main())
