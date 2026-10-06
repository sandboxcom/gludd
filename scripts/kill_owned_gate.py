#!/usr/bin/env python3
"""Terminate one checkout-owned full-gate tree without crossing namespaces."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.resource_arbiter import project_namespace, resource_root
else:
    from resource_arbiter import project_namespace, resource_root


GATE_LOCK_MARKER = "gludd-gate-run-v1"
DEFAULT_GRACE_SECONDS = 10.0
DEFAULT_KILL_WAIT_SECONDS = 1.0
DEFAULT_POLL_SECONDS = 0.1
LEGACY_GATE_LOCK_FIELDS = frozenset({"pid", "started_at"})
LEGACY_LOCK_CLOCK_SLOP_SECONDS = 2.0
LEGACY_LOCK_MAX_ACQUIRE_DELAY_SECONDS = 300.0

_MAKE_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[+:?!])?=.*$")
_SAFE_NAMESPACE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    elapsed_seconds: int
    command: str
    pgid: int | None = None
    started_at: str = ""
    cwd: str = ""
    project_namespace: str = ""


@dataclass(frozen=True)
class _TargetProcess:
    record: ProcessRecord
    depth: int


@dataclass(frozen=True)
class TerminationResult:
    """Auditable result of one gate-tree termination attempt."""

    success: bool
    term_pids: tuple[int, ...] = ()
    kill_pids: tuple[int, ...] = ()
    survivor_pids: tuple[int, ...] = ()
    skipped_pids: tuple[int, ...] = ()
    refusal_reason: str | None = None


RecordsReader = Callable[[], list[ProcessRecord]]
SignalSender = Callable[[int, signal.Signals], None]


def _is_owned_adaptive_gate(record: ProcessRecord, project_root: Path) -> bool:
    command = record.command
    full_gate_pytest = (
        " -m pytest tests/" in command
        and "--cov=general_ludd" in command
        and "--cov-fail-under=85" in command
    )
    return (
        ("adaptive_test.py" in command or full_gate_pytest)
        and str(project_root.resolve()) in command
    )


def owned_adaptive_gate_records(
    records: list[ProcessRecord], *, project_root: Path
) -> list[ProcessRecord]:
    """Return legacy adaptive full-gate workers for diagnostic callers only."""
    return [record for record in records if _is_owned_adaptive_gate(record, project_root)]


def _parse_ps_line(line: str) -> ProcessRecord | None:
    fields = line.strip().split(None, 8)
    if len(fields) != 9:
        return None
    try:
        pid = int(fields[0])
        ppid = int(fields[1])
        pgid = int(fields[2])
    except ValueError:
        return None
    return ProcessRecord(
        pid=pid,
        ppid=ppid,
        elapsed_seconds=0,
        command=fields[8],
        pgid=pgid,
        started_at=" ".join(fields[3:8]),
    )


def _normalized_start_identity(value: str) -> str:
    """Normalize only presentation whitespace in a process start timestamp."""
    return " ".join(value.split())


def _records(project_root: Path) -> list[ProcessRecord]:
    output = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,lstart=,command="],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [record for line in output.splitlines() if (record := _parse_ps_line(line))]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_gate_owner_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    make_indexes = [
        index
        for index, token in enumerate(tokens)
        if Path(token).name in {"make", "gmake"}
    ]
    return any(
        any(token in {"gate", "gate-refresh"} for token in tokens[index + 1 :])
        for index in make_indexes
    )


def _is_exact_legacy_gate_command(command: str) -> bool:
    """Accept one direct gate target without accepting lookalike commands."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or Path(tokens[0]).name not in {"make", "gmake"}:
        return False

    targets: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-s", "--silent", "--no-print-directory"}:
            index += 1
            continue
        if token in {"-C", "--directory"}:
            if index + 1 >= len(tokens) or not tokens[index + 1]:
                return False
            index += 2
            continue
        if token.startswith("--directory=") and token != "--directory=":
            index += 1
            continue
        if token.startswith("-C") and len(token) > 2:
            index += 1
            continue
        if token.startswith("-"):
            return False
        if _MAKE_ASSIGNMENT.fullmatch(token):
            index += 1
            continue
        targets.append(token)
        index += 1
    return targets in (["gate"], ["gate-refresh"])


def _default_project_namespace(root: Path) -> str:
    """Derive the non-overridden resource namespace for a verified cwd."""
    resolved = root.resolve()
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", resolved.name).strip("-._")
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{slug or 'project'}-{digest}"


def _process_cwd(pid: int) -> Path | None:
    """Read one live process cwd on Linux or macOS without guessing."""
    if pid <= 1:
        return None
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except (FileNotFoundError, OSError):
        pass

    for executable in (Path("/usr/sbin/lsof"), Path("/usr/bin/lsof")):
        if not executable.is_file():
            continue
        try:
            completed = subprocess.run(
                [str(executable), "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        names = [line[1:] for line in completed.stdout.splitlines() if line.startswith("n")]
        if len(names) == 1 and names[0]:
            return Path(names[0]).resolve()
    return None


def _process_namespace_override(pid: int) -> tuple[bool, str | None]:
    """Return whether the process environment was readable and its override."""
    environ_path = Path(f"/proc/{pid}/environ")
    try:
        entries = environ_path.read_bytes().split(b"\0")
    except (FileNotFoundError, OSError):
        pass
    else:
        prefix = b"GLUDD_PROJECT_NAMESPACE="
        values = [entry[len(prefix) :] for entry in entries if entry.startswith(prefix)]
        if len(values) > 1:
            return False, None
        if not values:
            return True, None
        try:
            value = values[0].decode("utf-8")
        except UnicodeDecodeError:
            return False, None
        return True, value or None

    try:
        completed = subprocess.run(
            ["/bin/ps", "eww", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    if completed.returncode != 0 or not completed.stdout.strip():
        return False, None
    if re.search(r"(?:^|\s)(?:HOME|PATH)=[^\s]+(?=\s|$)", completed.stdout) is None:
        return False, None
    matches = re.findall(
        r"(?:^|\s)GLUDD_PROJECT_NAMESPACE=([^\s]+)(?=\s|$)",
        completed.stdout,
    )
    if len(matches) > 1:
        return False, None
    return True, matches[0] if matches else None


def _legacy_owner_context(owner: ProcessRecord) -> tuple[Path | None, str | None]:
    try:
        owner_root = Path(owner.cwd).resolve() if owner.cwd else _process_cwd(owner.pid)
    except (OSError, RuntimeError):
        return None, None
    if owner_root is None:
        return None, None
    if owner.project_namespace:
        namespace = owner.project_namespace
    else:
        readable, override = _process_namespace_override(owner.pid)
        if not readable:
            return owner_root, None
        namespace = override or _default_project_namespace(owner_root)
    if (
        namespace in {".", ".."}
        or not namespace
        or _SAFE_NAMESPACE.fullmatch(namespace) is None
    ):
        return owner_root, None
    return owner_root, namespace


def _legacy_started_at_matches(lock_started_at: object, owner_started_at: str) -> bool:
    if (
        isinstance(lock_started_at, bool)
        or not isinstance(lock_started_at, (int, float))
        or not math.isfinite(float(lock_started_at))
        or float(lock_started_at) <= 0.0
        or not owner_started_at
    ):
        return False
    try:
        owner_epoch = time.mktime(
            time.strptime(owner_started_at, "%a %b %d %H:%M:%S %Y")
        )
    except (OverflowError, ValueError):
        return False
    acquire_delay = float(lock_started_at) - owner_epoch
    return (
        -LEGACY_LOCK_CLOCK_SLOP_SECONDS
        <= acquire_delay
        <= LEGACY_LOCK_MAX_ACQUIRE_DELAY_SECONDS
    )


def _same_identity(expected: ProcessRecord, current: ProcessRecord) -> bool:
    return (
        bool(expected.started_at)
        and expected.pid == current.pid
        and expected.started_at == current.started_at
        and expected.command == current.command
    )


def _tree_targets(records: list[ProcessRecord], owner: ProcessRecord) -> list[_TargetProcess]:
    children: dict[int, list[ProcessRecord]] = {}
    for record in records:
        children.setdefault(record.ppid, []).append(record)
    targets: list[_TargetProcess] = []
    pending = [(owner, 0)]
    seen: set[int] = set()
    while pending:
        record, depth = pending.pop()
        if record.pid in seen:
            continue
        seen.add(record.pid)
        targets.append(_TargetProcess(record, depth))
        pending.extend((child, depth + 1) for child in children.get(record.pid, ()))
    return sorted(targets, key=lambda target: (-target.depth, target.record.pid))


def _target_payload(target: _TargetProcess) -> dict[str, object]:
    record = target.record
    return {
        "pid": record.pid,
        "ppid": record.ppid,
        "pgid": record.pgid,
        "started_at": record.started_at,
        "command": record.command,
        "depth": target.depth,
    }


def _targets_from_payload(payload: object) -> list[_TargetProcess] | None:
    if not isinstance(payload, list):
        return None
    targets: list[_TargetProcess] = []
    try:
        for item in payload:
            if not isinstance(item, dict):
                return None
            targets.append(
                _TargetProcess(
                    ProcessRecord(
                        pid=int(item["pid"]),
                        ppid=int(item["ppid"]),
                        elapsed_seconds=0,
                        command=str(item["command"]),
                        pgid=int(item["pgid"]) if item.get("pgid") is not None else None,
                        started_at=str(item["started_at"]),
                    ),
                    int(item["depth"]),
                )
            )
    except (KeyError, TypeError, ValueError):
        return None
    if any(target.record.pid <= 1 or not target.record.started_at for target in targets):
        return None
    return sorted(targets, key=lambda target: (-target.depth, target.record.pid))


def _is_legacy_gate_lock(lock_payload: dict[str, Any]) -> bool:
    return set(lock_payload) == LEGACY_GATE_LOCK_FIELDS


def _validate_legacy_ownership(
    project_root: Path,
    lock_payload: dict[str, Any],
    records: list[ProcessRecord],
) -> tuple[list[_TargetProcess] | None, str | None]:
    owner_pid = lock_payload.get("pid")
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 1:
        return None, "legacy gate owner identity is incomplete"
    owner = next((record for record in records if record.pid == owner_pid), None)
    if owner is None:
        return None, "legacy gate owner is no longer running"
    if not _legacy_started_at_matches(lock_payload.get("started_at"), owner.started_at):
        return None, "legacy gate owner start time mismatch"
    if not _is_exact_legacy_gate_command(owner.command):
        return None, "legacy gate owner command mismatch"

    owner_root, owner_namespace = _legacy_owner_context(owner)
    canonical_root = project_root.resolve()
    if owner_root != canonical_root:
        return None, "legacy gate owner project root mismatch"
    if owner_namespace != project_namespace(canonical_root):
        return None, "legacy gate owner project namespace mismatch"
    return _tree_targets(records, owner), None


def _validate_ownership(
    project_root: Path,
    lock_payload: dict[str, Any],
    records: list[ProcessRecord],
) -> tuple[list[_TargetProcess] | None, str | None]:
    canonical_root = project_root.resolve()
    canonical_namespace = project_namespace(canonical_root)
    if _is_legacy_gate_lock(lock_payload):
        return _validate_legacy_ownership(canonical_root, lock_payload, records)
    if lock_payload.get("marker") != GATE_LOCK_MARKER:
        return None, "gate lock marker mismatch"
    if lock_payload.get("project_root") != str(canonical_root):
        return None, "project root mismatch"
    if lock_payload.get("project_namespace") != canonical_namespace:
        return None, "project namespace mismatch"
    state = lock_payload.get("state")
    if state == "termination_failed":
        targets = _targets_from_payload(lock_payload.get("targets"))
        if not targets:
            return None, "terminal gate lock has no revalidated targets"
        return targets, None
    if state != "active":
        return None, "gate lock state is not active"
    try:
        owner_pid = int(lock_payload["pid"])
        owner_started_at = str(lock_payload["pid_started_at"])
    except (KeyError, TypeError, ValueError):
        return None, "gate owner identity is incomplete"
    owner = next((record for record in records if record.pid == owner_pid), None)
    if owner is None:
        return None, "gate owner is no longer running"
    if (
        not owner_started_at
        or _normalized_start_identity(owner.started_at)
        != _normalized_start_identity(owner_started_at)
    ):
        return None, "gate owner start time mismatch"
    if not _is_gate_owner_command(owner.command):
        return None, "gate owner command mismatch"
    return _tree_targets(records, owner), None


def _current_state(
    expected: ProcessRecord, records_reader: RecordsReader
) -> tuple[str, ProcessRecord | None]:
    current = next(
        (record for record in records_reader() if record.pid == expected.pid), None
    )
    if current is None:
        return "gone", None
    if _same_identity(expected, current):
        return "owned", current
    return "replaced", current


def _command_digest(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:12]


def _signal_targets(
    targets: list[_TargetProcess],
    signum: signal.Signals,
    *,
    records_reader: RecordsReader,
    signal_sender: SignalSender,
) -> tuple[list[int], list[int]]:
    signaled: list[int] = []
    skipped: list[int] = []
    for target in targets:
        expected = target.record
        state, _current = _current_state(expected, records_reader)
        if state == "gone":
            continue
        if state == "replaced":
            skipped.append(expected.pid)
            print(
                f"[gate-kill] identity-mismatch pid={expected.pid} "
                f"expected-start={expected.started_at!r}; refusing signal",
                flush=True,
            )
            continue
        print(
            f"[gate-kill] signal={signum.name} pid={expected.pid} "
            f"depth={target.depth} start={expected.started_at!r} "
            f"command-sha256={_command_digest(expected.command)}",
            flush=True,
        )
        try:
            signal_sender(expected.pid, signum)
        except ProcessLookupError:
            continue
        except PermissionError:
            skipped.append(expected.pid)
            print(
                f"[gate-kill] permission-denied signal={signum.name} pid={expected.pid}",
                flush=True,
            )
            continue
        signaled.append(expected.pid)
    return signaled, skipped


def _owned_survivors(
    targets: list[_TargetProcess], records_reader: RecordsReader
) -> list[_TargetProcess]:
    return [
        target
        for target in targets
        if _current_state(target.record, records_reader)[0] == "owned"
    ]


def _wait_for_exit(
    targets: list[_TargetProcess],
    *,
    timeout: float,
    poll_seconds: float,
    records_reader: RecordsReader,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> list[_TargetProcess]:
    bounded_timeout = max(0.0, timeout)
    bounded_poll = max(0.01, poll_seconds)
    deadline = monotonic() + bounded_timeout
    checks = max(1, math.ceil(bounded_timeout / bounded_poll) + 1)
    for _attempt in range(checks):
        survivors = _owned_survivors(targets, records_reader)
        if not survivors or monotonic() >= deadline:
            return survivors
        sleep(min(bounded_poll, max(0.0, deadline - monotonic())))
    return _owned_survivors(targets, records_reader)


def _lock_still_matches(path: Path, expected: dict[str, Any]) -> bool:
    current = _read_json(path)
    if _is_legacy_gate_lock(expected):
        return bool(
            current
            and _is_legacy_gate_lock(current)
            and current.get("pid") == expected.get("pid")
            and current.get("started_at") == expected.get("started_at")
        )
    return bool(
        current
        and current.get("marker") == expected.get("marker")
        and current.get("pid") == expected.get("pid")
        and current.get("pid_started_at") == expected.get("pid_started_at")
        and current.get("project_namespace") == expected.get("project_namespace")
    )


def _unlink_pid_marker(path: Path, allowed_pids: set[int]) -> None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        pid = int(value)
    except (FileNotFoundError, OSError, ValueError):
        return
    if pid in allowed_pids:
        path.unlink(missing_ok=True)
        print(f"[gate-kill] removed owned marker={path} pid={pid}", flush=True)


def _publish_terminal_status(
    project_root: Path, *, success: bool, survivor_pids: list[int]
) -> None:
    outcome = "terminated" if success else "termination-failed"
    survivors = ",".join(str(pid) for pid in survivor_pids) or "none"
    _write_text_atomic(
        project_root / ".gate-status",
        (
            f"gate-kill {outcome}\n"
            f"survivors {survivors}\n"
            "=== GATE: ABORTED ===\n"
        ),
    )
    (project_root / ".gate-status.next").unlink(missing_ok=True)


def _finalize_lock(
    lock_path: Path,
    lock_payload: dict[str, Any],
    targets: list[_TargetProcess],
    survivor_pids: list[int],
    project_root: Path,
) -> None:
    if not _lock_still_matches(lock_path, lock_payload):
        print(
            f"[gate-kill] ownership lock changed; refusing mutation path={lock_path}",
            flush=True,
        )
        return
    if not survivor_pids:
        lock_path.unlink(missing_ok=True)
        print(f"[gate-kill] released ownership lock={lock_path}", flush=True)
        return
    terminal = dict(lock_payload)
    if _is_legacy_gate_lock(lock_payload):
        owner = next((target.record for target in targets if target.depth == 0), None)
        if owner is None:
            print(
                f"[gate-kill] legacy owner missing; retaining lock={lock_path}",
                flush=True,
            )
            return
        terminal.update(
            {
                "marker": GATE_LOCK_MARKER,
                "pid_started_at": owner.started_at,
                "project_root": str(project_root),
                "project_namespace": project_namespace(project_root),
            }
        )
    terminal.update(
        {
            "state": "termination_failed",
            "failed_at": time.time(),
            "survivor_pids": survivor_pids,
            "targets": [_target_payload(target) for target in targets],
        }
    )
    _write_json_atomic(lock_path, terminal)
    print(
        f"[gate-kill] retained fail-closed lock={lock_path} survivors={survivor_pids}",
        flush=True,
    )


def terminate_owned_gate(
    project_root: Path,
    *,
    apply: bool,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    kill_wait_seconds: float = DEFAULT_KILL_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    records_reader: RecordsReader | None = None,
    signal_sender: SignalSender = os.kill,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> TerminationResult:
    """Terminate an exact owned tree with TERM, bounded wait, then KILL."""
    canonical_root = project_root.resolve()
    lock_path = canonical_root / ".gate-logs" / "gate-run.lock"
    lock_payload = _read_json(lock_path)
    if lock_payload is None:
        print(f"[gate-kill] no readable owned gate lock at {lock_path}", flush=True)
        return TerminationResult(success=not lock_path.exists(), refusal_reason="no gate lock")

    reader = records_reader or (lambda: _records(canonical_root))
    targets, refusal = _validate_ownership(canonical_root, lock_payload, reader())
    if targets is None:
        print(f"[gate-kill] REFUSED: {refusal}", flush=True)
        return TerminationResult(success=False, refusal_reason=refusal)
    print(
        f"[gate-kill] ownership verified namespace={project_namespace(canonical_root)} "
        f"root={canonical_root} targets={len(targets)} apply={apply}",
        flush=True,
    )
    if not apply:
        planned = tuple(target.record.pid for target in targets)
        return TerminationResult(success=True, term_pids=planned)

    lock_schema = "legacy" if _is_legacy_gate_lock(lock_payload) else "marked"
    if lock_schema == "legacy":
        targets, refusal = _validate_ownership(canonical_root, lock_payload, reader())
        if targets is None:
            print(f"[gate-kill] REFUSED: {refusal}", flush=True)
            return TerminationResult(success=False, refusal_reason=refusal)
        if not _lock_still_matches(lock_path, lock_payload):
            refusal = "legacy gate lock changed during validation"
            print(f"[gate-kill] REFUSED: {refusal}", flush=True)
            return TerminationResult(success=False, refusal_reason=refusal)

    term_pids, term_skipped = _signal_targets(
        targets,
        signal.SIGTERM,
        records_reader=reader,
        signal_sender=signal_sender,
    )
    survivors = _wait_for_exit(
        targets,
        timeout=grace_seconds,
        poll_seconds=poll_seconds,
        records_reader=reader,
        monotonic=monotonic,
        sleep=sleep,
    )
    kill_pids: list[int] = []
    kill_skipped: list[int] = []
    if survivors:
        kill_pids, kill_skipped = _signal_targets(
            survivors,
            signal.SIGKILL,
            records_reader=reader,
            signal_sender=signal_sender,
        )
        survivors = _wait_for_exit(
            survivors,
            timeout=kill_wait_seconds,
            poll_seconds=poll_seconds,
            records_reader=reader,
            monotonic=monotonic,
            sleep=sleep,
        )

    survivor_pids = sorted(target.record.pid for target in survivors)
    skipped_pids = sorted(set(term_skipped + kill_skipped))
    success = not survivor_pids
    _publish_terminal_status(
        canonical_root, success=success, survivor_pids=survivor_pids
    )

    target_pids = {target.record.pid for target in targets}
    if success:
        # The primary gate-run lock still blocks a replacement gate while its
        # auxiliary PID markers are removed, closing the PID-reuse race between
        # releasing ownership and cleaning the older run's resource locks.
        _unlink_pid_marker(canonical_root / ".gate-background.pid", target_pids)
        namespaced_root = resource_root(canonical_root)
        _unlink_pid_marker(namespaced_root / "async-gate.lock", target_pids)
        _unlink_pid_marker(namespaced_root / "gate.lock", target_pids)
    _finalize_lock(
        lock_path,
        lock_payload,
        targets,
        survivor_pids,
        canonical_root,
    )

    evidence = {
        "marker": GATE_LOCK_MARKER,
        "lock_schema": lock_schema,
        "project_root": str(canonical_root),
        "project_namespace": project_namespace(canonical_root),
        "recorded_at": time.time(),
        "outcome": "terminated" if success else "termination_failed",
        "term_pids": term_pids,
        "kill_pids": kill_pids,
        "skipped_pids": skipped_pids,
        "survivor_pids": survivor_pids,
        "targets": [_target_payload(target) for target in targets],
    }
    evidence_path = canonical_root / ".gate-logs" / "gate-kill-evidence.json"
    _write_json_atomic(evidence_path, evidence)
    print(
        f"[gate-kill] outcome={evidence['outcome']} evidence={evidence_path} "
        f"term={term_pids} kill={kill_pids} survivors={survivor_pids}",
        flush=True,
    )
    return TerminationResult(
        success=success,
        term_pids=tuple(term_pids),
        kill_pids=tuple(kill_pids),
        survivor_pids=tuple(survivor_pids),
        skipped_pids=tuple(skipped_pids),
    )


def kill_owned_gates(project_root: Path, *, apply: bool) -> int:
    """CLI-compatible wrapper for the exact gate-tree terminator."""
    return 0 if terminate_owned_gate(project_root, apply=apply).success else 1


if __name__ == "__main__":
    root = Path(os.environ.get("GLUDD_PROJECT_ROOT", Path.cwd())).resolve()
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(kill_owned_gates(root, apply=os.environ.get("APPLY") == "1"))
    raise SystemExit(130)
