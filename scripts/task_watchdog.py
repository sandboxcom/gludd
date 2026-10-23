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
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import active_work_status as inventory_owner
    import gludd_env_defaults as gludd_env_defaults
    import kill_owned_gate as cleanup_owner
    import resource_arbiter as resource_owner
    import start_gate_background as background_owner
    from active_work_status import _repository_roots
elif __package__:
    from scripts import active_work_status as inventory_owner
    from scripts import gludd_env_defaults as gludd_env_defaults
    from scripts import kill_owned_gate as cleanup_owner
    from scripts import resource_arbiter as resource_owner
    from scripts import start_gate_background as background_owner
    from scripts.active_work_status import _repository_roots
else:  # pragma: no cover - direct script execution
    import active_work_status as inventory_owner
    import gludd_env_defaults as gludd_env_defaults
    import kill_owned_gate as cleanup_owner
    import resource_arbiter as resource_owner
    import start_gate_background as background_owner
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


def _log(msg: str, *, required: bool = False) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(WATCHDOG_LOG, "a") as f:
            f.write(line + "\n")
            if required:
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        if required:
            raise


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


def _gate_waiter_pid(command: str) -> int | None:
    """Recognize the existing direct waiter's literal binding without evaluation."""
    if "gate-wait" not in command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or Path(tokens[0]).name not in {"make", "gmake"}:
        return None
    bindings: list[int] = []
    goals: list[str] = []
    for token in tokens[1:]:
        if token == "--no-print-directory":
            continue
        if re.fullmatch(r"GATE_EXPECTED_PID=[1-9][0-9]*", token):
            bindings.append(int(token.partition("=")[2]))
        elif token.startswith("GATE_EXPECTED_PID"):
            return None
        elif not cleanup_owner._MAKE_ASSIGNMENT.fullmatch(token):
            goals.append(token)
    return bindings[0] if len(bindings) == 1 and goals == ["gate-wait"] else None


def _native_gate_identity(root: Path) -> background_owner.GateIdentity | None:
    """Read current producer association, never terminal-success authority."""
    paths = background_owner.GatePaths.for_root(root)
    payload = background_owner._read_json(paths.state_file)
    if payload is None:
        return None
    identity = background_owner._identity_from_state(payload)
    command = payload.get("command")
    if (identity is None or payload.get("project_root") != str(paths.project_root)
            or payload.get("project_namespace") != resource_owner.project_namespace(root)
            or identity.process_group_id != identity.pid or identity.session_id != identity.pid
            or not isinstance(command, list) or not all(isinstance(argument, str) for argument in command)
            or not background_owner.is_canonical_gate_command(command, paths.project_root)):
        return None
    return identity


def _native_terminal_gate(identity: background_owner.GateIdentity) -> bool:
    """Retain terminal association; unavailable inspection revokes signal authority."""
    terminal = background_owner._process_is_terminal(identity.pid)
    if terminal is None:
        raise ValueError("native gate terminal identity is unavailable")
    if not terminal:
        return False
    state = background_owner._process_state(identity)
    if state in {"owned", "gone"}:
        return True
    if state == "reused":
        return False
    raise ValueError("native gate terminal identity is unavailable")


def _protected_pids(records: list[cleanup_owner.ProcessRecord], roots: tuple[Path, ...]) -> set[int]:
    """Use native observer metadata conservatively; display absence grants nothing."""
    observed: list[dict[str, str]] = []
    # The existing producer requires this token in every proven owner command.
    # Its empty process projection needs no historical status display inventory.
    if any("stream_command.py" in record.command for record in records):
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
    waiters: dict[Path, list[tuple[cleanup_owner.ProcessRecord, int]]] = {}
    for record in records:
        binding = _gate_waiter_pid(record.command)
        if binding is not None and (cwd := _owned_cwd(record, roots)) is not None:
            waiters.setdefault(cwd, []).append((record, binding))
    for root in roots:
        gate_pids = {pid for pid in (
            _read_gate_pid(str(root / ".gate-background.pid")),
            _read_gate_run_lock_pid(str(root / ".gate-logs/gate-run.lock")),
        ) if pid in by_pid}
        owners.update(gate_pids)
        root_waiters = waiters.get(root.resolve(), [])
        if not root_waiters:
            continue
        identity = _native_gate_identity(root)
        for waiter, binding in root_waiters:
            recorded = identity is not None and identity.pid == binding
            gate = by_pid.get(binding)
            if gate is None:
                if recorded:
                    owners.add(waiter.pid)
                continue
            if binding not in gate_pids and not recorded:
                continue
            if (
                identity is not None and identity.pid == binding
                and (
                    cleanup_owner._normalized_start_identity(gate.started_at)
                    != cleanup_owner._normalized_start_identity(identity.pid_started_at)
                    or gate.pgid != identity.process_group_id
                )
            ):
                continue
            if _owned_cwd(gate, (root,)) is None or not cleanup_owner._is_gate_owner_command(gate.command):
                if identity is not None and identity.pid == binding and _native_terminal_gate(identity):
                    owners.add(waiter.pid)
                continue
            if recorded:
                owners.add(binding)
            # Deny-only protection covers normal terminal drain; it grants no proof
            # or signal authority and retains the native waiter's original bound.
            owners.add(waiter.pid)
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
    reap: Callable[[], int | None] | None = None,
    authority: Callable[[list[cleanup_owner.ProcessRecord]], None] | None = None,
) -> bool:
    """Keep signal authority strict while confirming exit by fresh PID absence."""
    if not roots or not all(root.is_absolute() and root.is_dir() for root in roots):
        raise ValueError("registered checkout inventory is incomplete")
    def snapshot() -> list[cleanup_owner.ProcessRecord]:
        if reap is not None:
            reap()
        return cleanup_owner._records(roots[0])

    records = snapshot()
    current = next((record for record in records if record.pid == expected.pid), None)
    if current is None or not cleanup_owner._same_identity(expected, current):
        raise ValueError("task process start identity changed")
    targets = cleanup_owner._tree_targets(records, current)
    expected_by_pid = {target.record.pid: target.record for target in targets}
    terminated_parents: set[int] = set()
    signals_refused = False
    unverified_child = False

    def validate(fresh: list[cleanup_owner.ProcessRecord]) -> None:
        nonlocal unverified_child
        if authority is not None:
            authority(fresh)
        protected = _protected_pids(fresh, roots)
        live_owner = next((record for record in fresh if record.pid == expected.pid), None)
        if live_owner is not None and any(
            target.record.pid not in expected_by_pid for target in cleanup_owner._tree_targets(fresh, live_owner)
        ):
            unverified_child = True
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

    def reader() -> list[cleanup_owner.ProcessRecord]:
        fresh = snapshot()
        validate(fresh)
        return fresh

    def exit_reader() -> list[cleanup_owner.ProcessRecord]:
        nonlocal signals_refused
        fresh = snapshot()
        try:
            validate(fresh)
        except ValueError:
            signals_refused = True
        # Wait-only presence view: the native waiter normally considers a changed
        # identity gone. Every occupied original PID must instead remain pending.
        # This view NEVER supplies signal authority; reader/sender remain strict.
        return [expected_by_pid.get(record.pid, record) for record in fresh]

    # Validate the complete tree, including children, before the first signal.
    reader()
    if not apply:
        return True

    def sender(pid: int, signum: signal.Signals) -> None:
        if signals_refused:
            raise ValueError("task signal authority was revoked during exit")
        fresh = reader()
        record = next((record for record in fresh if record.pid == pid), None)
        if record is None:
            raise ProcessLookupError(pid)
        os.kill(pid, signum)
        if signum == signal.SIGTERM:
            terminated_parents.add(pid)

    try:
        _term_pids, skipped = cleanup_owner._signal_targets(
            targets, signal.SIGTERM, records_reader=reader, signal_sender=sender,
        )
    except ValueError:
        if not terminated_parents:
            raise
        signals_refused = True
        skipped = []
    survivors = cleanup_owner._wait_for_exit(
        targets, timeout=5, poll_seconds=0.1, records_reader=exit_reader,
        monotonic=time.monotonic, sleep=time.sleep,
    )
    if survivors and not signals_refused:
        try:
            _killed, kill_skipped = cleanup_owner._signal_targets(
                survivors, signal.SIGKILL, records_reader=reader, signal_sender=sender,
            )
            skipped.extend(kill_skipped)
        except ValueError:
            signals_refused = True
        survivors = cleanup_owner._wait_for_exit(
            survivors, timeout=1, poll_seconds=0.1, records_reader=exit_reader,
            monotonic=time.monotonic, sleep=time.sleep,
        )
    # Success requires another complete native snapshot, never missing cwd,
    # changed command/start metadata, or the wait helper's identity classification.
    present = {record.pid for record in snapshot()}
    return (bool(terminated_parents) and not skipped and not survivors
            and not unverified_child and not present.intersection(expected_by_pid))


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


@dataclass(frozen=True)
class WatchdogSpec:
    """Explicit daemon context for the existing acknowledged lifecycle owner."""

    script_name: str = "task_watchdog.py"
    stem: str = "task-watchdog"
    label: str = "TASK-WATCHDOG"
    pid_environment: str = "GLUDD_TASK_WATCHDOG_PID"
    log_environment: str = "GLUDD_TASK_WATCHDOG_LOG"
    environment: tuple[tuple[str, str], ...] = ()
    marker: Callable[[Path], Path] | None = None
    read_pid: Callable[[Path], int | None] | None = None
    drain_supervisor: bool = True
    startup_ready: Callable[[cleanup_owner.ProcessRecord, Path], None] | None = None
    authority: Callable[
        [cleanup_owner.ProcessRecord, Path], Callable[[list[cleanup_owner.ProcessRecord]], None]
    ] | None = None

    def marker_path(self, root: Path) -> Path:
        """Resolve the configured native lease or default checkout PID marker."""
        return self.marker(root) if self.marker else root / f".gate-logs/{self.stem}.pid"

    def marker_pid(self, path: Path) -> int | None:
        """Read the marker through its configured lease or native PID decoder."""
        return self.read_pid(path) if self.read_pid else _read_gate_pid(str(path))


_TASK_SPEC = WatchdogSpec()


def _current_stop_invocation(
    root: Path, records: list[cleanup_owner.ProcessRecord], *, apply: bool, action: str = "--stop",
    spec: WatchdogSpec = _TASK_SPEC,
) -> dict[int, cleanup_owner.ProcessRecord]:
    """Identify only this exact CLI lifecycle action and its connected UV launcher."""
    def native_stop(record: cleanup_owner.ProcessRecord, *, uv: bool = False) -> bool:
        return (_script_role(record, root, spec.script_name, uv=uv)
                and shlex.split(record.command)[4 if uv else 2:]
                == [action, "--validate-only", str(int(not apply))])

    current = next((record for record in records if record.pid == _SELF_PID), None)
    if (current is None or not current.started_at or not native_stop(current)
            or _owned_cwd(current, (root,)) != root):
        return {}
    verified = {current.pid: current}
    parent = next((record for record in records if record.pid == current.ppid), None)
    if (parent is not None and parent.pid == os.getppid() and parent.started_at
            and native_stop(parent, uv=True) and _owned_cwd(parent, (root,)) == root):
        verified[parent.pid] = parent
    return verified


def _confirm_watchdog_absence(
    root: Path, pid: int | None, records: list[cleanup_owner.ProcessRecord],
    stop_invocation: dict[int, cleanup_owner.ProcessRecord], spec: WatchdogSpec = _TASK_SPEC,
) -> None:
    """A complete absent PID census may recover metadata, never authorize signals."""
    if any(record.pid == pid for record in records):
        raise ValueError("watchdog PID is still occupied")
    for record in records:
        stop_record = stop_invocation.get(record.pid)
        if stop_record is not None:
            if not cleanup_owner._same_identity(stop_record, record) or _owned_cwd(record, (root,)) != root:
                raise ValueError("current stop invocation identity changed")
            continue
        if not (_script_role(record, root, spec.script_name)
                or _script_role(record, root, spec.script_name, uv=True)):
            continue
        cwd = cleanup_owner._process_cwd(record.pid)
        if cwd is None or cwd.resolve() == root:
            raise ValueError(
                "a matching watchdog daemon or launcher is live or unlocated: "
                f"pid={record.pid} current_pid={_SELF_PID} ppid={record.ppid} cwd={cwd}"
            )


def stop_owned_watchdog(
    project_root: Path, *, apply: bool, spec: WatchdogSpec = _TASK_SPEC,
) -> bool:
    """Plan/drain one old daemon, or recover its proven-absent pointer, without restart."""
    root = project_root.resolve()
    marker = spec.marker_path(root)
    if not marker.exists() and not marker.is_symlink():
        print(f"{spec.label}-STOP absent writes=false signals=false", flush=True)
        return True
    try:
        if (marker.is_symlink() or not marker.is_file()
                or marker.resolve() != spec.marker_path(root).absolute()
                or root not in {path.resolve() for path in _repository_roots()}):
            raise ValueError("watchdog PID pointer is not checkout-owned")
        recorded_pid = marker.read_text(encoding="utf-8")
        recorded_stat = marker.stat()
        pid = spec.marker_pid(marker)
        if pid is None or pid <= 1:
            raise ValueError("watchdog PID pointer is malformed")

        def unchanged_pointer() -> None:
            current_stat = marker.stat()
            if (marker.is_symlink() or marker.read_text(encoding="utf-8") != recorded_pid
                    or (current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns)
                    != (recorded_stat.st_dev, recorded_stat.st_ino, recorded_stat.st_mtime_ns)):
                raise ValueError("watchdog PID pointer changed during cleanup")

        records = cleanup_owner._records(root)
        daemon = next((record for record in records if record.pid == pid), None)
        if daemon is None:
            stop_invocation = _current_stop_invocation(root, records, apply=apply, spec=spec)
            _confirm_watchdog_absence(root, pid, records, stop_invocation, spec)
            _confirm_watchdog_absence(root, pid, cleanup_owner._records(root), stop_invocation, spec)
            unchanged_pointer()
            if apply:
                marker.unlink()
            print(f"{spec.label}-STOP absent-pointer verified apply={apply} signals=false", flush=True)
            return True
        if (not daemon.started_at or _owned_cwd(daemon, (root,)) != root
                or not _script_role(daemon, root, spec.script_name)):
            raise ValueError("watchdog PID pointer has no verified native daemon")
        if len(shlex.split(daemon.command)) != 2:
            raise ValueError("watchdog daemon command is not the native singleton")
        parent = next((record for record in records if record.pid == daemon.ppid), None)
        supervisor: cleanup_owner.ProcessRecord | None = None
        owner = daemon
        if parent is not None and _script_role(parent, root, spec.script_name, uv=True):
            if (not parent.started_at or _owned_cwd(parent, (root,)) != root
                    or len(shlex.split(parent.command)) != 4):
                raise ValueError("watchdog UV launcher ownership is incomplete")
            if spec.drain_supervisor:
                owner = parent
            else:
                supervisor = parent
        authority = spec.authority(daemon, root) if spec.authority else None
        unchanged_pointer()
        drain = (_terminate_verified(owner, (root,), apply=apply) if authority is None
                 else _terminate_verified(owner, (root,), apply=apply, authority=authority))
        if not drain:
            raise ValueError("watchdog lifecycle did not fully drain")
        if apply and supervisor is not None:
            print(f"{spec.label}-STOP waiting for native UV supervisor pid={supervisor.pid} "
                  "timeout=2s signals=false", flush=True)
            cleanup_owner._wait_for_exit(
                cleanup_owner._tree_targets([supervisor], supervisor), timeout=2, poll_seconds=0.1,
                records_reader=lambda: cleanup_owner._records(root),
                monotonic=time.monotonic, sleep=time.sleep,
            )
            if any(record.pid == supervisor.pid for record in cleanup_owner._records(root)):
                raise ValueError("watchdog UV supervisor has not naturally exited")
        if apply and marker.exists():
            unchanged_pointer()
            marker.unlink()
        print(f"{spec.label}-STOP verified apply={apply} old_tree_drained={apply}", flush=True)
        return True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"{spec.label}-STOP refused: {exc}", flush=True)
        return False


@dataclass(frozen=True)
class WatchdogLaunch:
    """One native startup outcome, retaining the caller's actual child handle."""

    success: bool
    identity: cleanup_owner.ProcessRecord | None = None
    process: subprocess.Popen[bytes] | None = None


def _startup_identity(
    record: cleanup_owner.ProcessRecord, root: Path, spec: WatchdogSpec = _TASK_SPEC,
) -> bool:
    return (bool(record.started_at) and _script_role(record, root, spec.script_name)
            and len(shlex.split(record.command)) == 2 and _owned_cwd(record, (root,)) == root
            and record.pgid == record.pid and os.getpgid(record.pid) == os.getsid(record.pid) == record.pid)


def _startup_admission(
    root: Path, *, apply: bool, spec: WatchdogSpec = _TASK_SPEC,
) -> cleanup_owner.ProcessRecord | None:
    marker = spec.marker_path(root)
    records = cleanup_owner._records(root)
    invocation = _current_stop_invocation(root, records, apply=apply, action="--start", spec=spec)
    existing: cleanup_owner.ProcessRecord | None = None
    pointer_identity: tuple[int, int, int, str] | None = None
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or not marker.is_file() or marker.resolve() != spec.marker_path(root).absolute():
            raise ValueError("startup PID pointer is not checkout-owned")
        pointer_text = marker.read_text(encoding="utf-8")
        pointer_stat = marker.stat()
        pointer_identity = (pointer_stat.st_dev, pointer_stat.st_ino, pointer_stat.st_mtime_ns, pointer_text)
        pid = spec.marker_pid(marker)
        if pid is None or pid <= 1:
            raise ValueError("startup PID pointer is malformed")
        existing = next((record for record in records if record.pid == pid), None)
        if existing is None:
            raise ValueError("stale startup pointer requires the native stop recovery owner")
        if not _startup_identity(existing, root, spec):
            raise ValueError("existing watchdog has no verified detached identity")
        # A long-running singleton is identified natively, independently of log retention.
        invocation[existing.pid] = existing
        if spec.startup_ready:
            spec.startup_ready(existing, root)
        if spec.authority:
            spec.authority(existing, root)(records)
    _confirm_watchdog_absence(root, None, records, invocation, spec)
    fresh = cleanup_owner._records(root)
    _confirm_watchdog_absence(root, None, fresh, invocation, spec)
    if existing is not None:
        current_stat = marker.stat()
        if (marker.is_symlink() or pointer_identity != (
                current_stat.st_dev, current_stat.st_ino, current_stat.st_mtime_ns,
                marker.read_text(encoding="utf-8"))):
            raise ValueError("existing watchdog PID pointer changed during admission")
        current = next((record for record in fresh if record.pid == existing.pid), None)
        if (current is None or not cleanup_owner._same_identity(existing, current)
                or not _startup_identity(current, root, spec)):
            raise ValueError("existing watchdog changed during startup admission")
        if spec.startup_ready:
            spec.startup_ready(current, root)
        if spec.authority:
            spec.authority(existing, root)(fresh)
    return existing


def start_owned_watchdog(
    project_root: Path, *, apply: bool, spec: WatchdogSpec = _TASK_SPEC,
) -> WatchdogLaunch:
    """Admit one detached selected-Python daemon and acknowledge its actual identity."""
    root = project_root.resolve()
    process: subprocess.Popen[bytes] | None = None
    identity: cleanup_owner.ProcessRecord | None = None
    log_identity: tuple[int, int] | None = None
    log = root / f".gate-logs/{spec.stem}.log"
    # Keep the one launch lease through actual success and verified failure drain.
    with ExitStack() as ownership:
        try:
            if os.name != "posix" or not hasattr(os, "getsid"):
                raise ValueError("native detached watchdog startup requires POSIX session support")
            if root not in {path.resolve() for path in _repository_roots()}:
                raise ValueError("startup checkout is not registered")
            script = root / "scripts" / spec.script_name
            directory = root / ".gate-logs"
            log = directory / f"{spec.stem}.log"
            lock = directory / f"{spec.stem}-launch.lock"
            if script.is_symlink() or not script.is_file():
                raise ValueError("startup script is not a regular checkout-owned source")
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError("startup directory is not checkout-owned")
            for path in (log, lock):
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    raise ValueError("startup log or lock is not a regular owned file")
            if not apply:
                existing = _startup_admission(root, apply=False, spec=spec)
                if existing is not None:
                    print(f"{spec.label}-START already-running pid={existing.pid} "
                          "writes=false signals=false", flush=True)
                else:
                    print(f"{spec.label}-START plan writes=false spawn=false signals=false", flush=True)
                return WatchdogLaunch(True, existing)
            # Reuse the native launch-lock/detached-session pattern. FileLock is
            # already owned by the observer and CI control planes and stays portable.
            from filelock import FileLock

            directory.mkdir(parents=True, exist_ok=True)
            ownership.enter_context(FileLock(str(lock), timeout=0, mode=0o600))
            existing = _startup_admission(root, apply=apply, spec=spec)
            if existing is not None:
                print(f"{spec.label}-START already-running pid={existing.pid} spawn=false signals=false", flush=True)
                return WatchdogLaunch(True, existing)
            environment = os.environ.copy()
            environment.update(dict(spec.environment))
            environment.update({
                spec.pid_environment: str(directory / f"{spec.stem}.pid"),
                spec.log_environment: str(log), "PYTHONUNBUFFERED": "1",
            })
            with log.open("ab") as output:
                opened = os.fstat(output.fileno())
                log_identity = (opened.st_dev, opened.st_ino)
                offset = output.tell()
                process = subprocess.Popen(
                    [sys.executable, str(script)], cwd=root, env=environment,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True,
                )
                deadline = time.monotonic() + 5
                for _attempt in range(51):
                    if process.poll() is not None:
                        break
                    current = next((record for record in cleanup_owner._records(root)
                                    if record.pid == process.pid), None)
                    if current is not None:
                        if not _startup_identity(current, root, spec):
                            raise ValueError("new watchdog identity or session is unverified")
                        if identity is not None and not cleanup_owner._same_identity(identity, current):
                            raise ValueError("new watchdog start identity changed")
                        identity = current
                        with log.open("rb") as acknowledgement:
                            acknowledgement.seek(offset)
                            text = acknowledgement.read(8192)
                        marker = spec.marker_path(root)
                        if marker.is_symlink() or log.is_symlink():
                            raise ValueError("startup publication became a symlink")
                        if (spec.marker_pid(marker) == process.pid
                                and f"{Path(spec.script_name).stem} ready: pid={process.pid} ".encode() in text
                                and f"{Path(spec.script_name).stem} started: pid={process.pid} ".encode() in text):
                            marker_stat = marker.stat()
                            fresh = next((record for record in cleanup_owner._records(root)
                                          if record.pid == process.pid), None)
                            current_marker = marker.stat()
                            current_log = log.stat()
                            if (fresh is None or not cleanup_owner._same_identity(identity, fresh)
                                    or not _startup_identity(fresh, root, spec) or process.poll() is not None
                                    or marker.is_symlink() or log.is_symlink()
                                    or spec.marker_pid(marker) != process.pid
                                    or (marker_stat.st_dev, marker_stat.st_ino, marker_stat.st_mtime_ns)
                                    != (current_marker.st_dev, current_marker.st_ino, current_marker.st_mtime_ns)
                                    or log_identity != (current_log.st_dev, current_log.st_ino)):
                                raise ValueError("acknowledged watchdog identity changed")
                            if spec.startup_ready:
                                spec.startup_ready(fresh, root)
                            if spec.authority:
                                spec.authority(fresh, root)([fresh])
                            print(f"{spec.label}-START acknowledged pid={fresh.pid} "
                                  f"start={fresh.started_at!r} pgid={fresh.pid} sid={fresh.pid}", flush=True)
                            return WatchdogLaunch(True, fresh, process)
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                raise RuntimeError("watchdog exited or did not acknowledge startup within five seconds")
        except (OSError, RuntimeError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
            if process is not None and identity is not None and process.poll() is None:
                try:
                    _terminate_verified(identity, (root,), reap=process.poll)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as cleanup_error:
                    print(f"{spec.label}-START cleanup refused: {cleanup_error}", flush=True)
            if process is not None and process.poll() is not None:
                process.wait(timeout=1)
            message = f"{spec.label}-START refused: {exc}"
            if log_identity is not None and not log.is_symlink():
                try:
                    current_log = log.stat()
                    if log_identity == (current_log.st_dev, current_log.st_ino):
                        with log.open("ab") as diagnostic:
                            diagnostic.write((message + "\n").encode())
                            diagnostic.flush()
                except OSError:
                    pass
            print(message, flush=True)
            return WatchdogLaunch(False, identity, process)


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
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--stop", action="store_true")
    operation.add_argument("--start", action="store_true")
    parser.add_argument("--validate-only", choices=("0", "1"), default="1")
    args = parser.parse_args(argv)
    if args.start:
        launch = start_owned_watchdog(Path(__file__).resolve().parents[1], apply=args.validate_only == "0")
        return 0 if launch.success else 1
    if args.stop:
        return 0 if stop_owned_watchdog(Path(__file__).resolve().parents[1], apply=args.validate_only == "0") else 1
    if not _write_pid():
        return 1
    _log(
        f"task_watchdog started: pid={_SELF_PID} timeout={TIMEOUT_SECS:.0f}s "
        f"poll={POLL_SECS}s state={DEADLINES_FILE} ownership_policy={_OWNERSHIP_POLICY}"
    )
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


def _write_pid() -> bool:
    try:
        pointer = Path(WATCHDOG_PID_FILE)
        if pointer.exists() or pointer.is_symlink():
            raise FileExistsError("task watchdog PID pointer already exists")
        _log(f"task_watchdog ready: pid={_SELF_PID} ownership_policy={_OWNERSHIP_POLICY}", required=True)
        with pointer.open("x", encoding="utf-8") as marker:
            marker.write(str(_SELF_PID))
        return True
    except OSError as exc:
        _log(f"task_watchdog PID publication refused: {exc}")
        return False


if __name__ == "__main__":
    raise SystemExit(main())
