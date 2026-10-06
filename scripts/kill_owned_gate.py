#!/usr/bin/env python3
"""Terminate one checkout-owned full-gate tree without crossing namespaces."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
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


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    elapsed_seconds: int
    command: str
    pgid: int | None = None
    started_at: str = ""


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


def _validate_ownership(
    project_root: Path,
    lock_payload: dict[str, Any],
    records: list[ProcessRecord],
) -> tuple[list[_TargetProcess] | None, str | None]:
    canonical_root = project_root.resolve()
    canonical_namespace = project_namespace(canonical_root)
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
    if not owner_started_at or owner.started_at != owner_started_at:
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
    _finalize_lock(lock_path, lock_payload, targets, survivor_pids)

    evidence = {
        "marker": GATE_LOCK_MARKER,
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
