#!/usr/bin/env python3
"""Reap only stale, project-owned collection/gate-refresh lock files.

Lock files are advisory records, so deletion is safe only after checking all
available ownership evidence: exact resource-root placement, age, PID state,
and command identity.  Unknown or fresh locks are always preserved.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.process_cleanup import ProcessInfo, snapshot_processes
    from scripts.resource_arbiter import project_namespace, project_root, resource_root
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from process_cleanup import ProcessInfo, snapshot_processes
    from resource_arbiter import project_namespace, project_root, resource_root

LOCK_NAMES = frozenset({"collection.lock", "gate-refresh.lock"})
DEFAULT_STALE_AFTER = 900.0


@dataclass(frozen=True)
class LockAssessment:
    """Evidence-based decision for one lock file."""

    path: Path
    stale: bool
    reason: str
    pid: int | None
    age_seconds: float


def _owner_pid(path: Path) -> int | None:
    try:
        payload = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not payload.startswith("pid="):
        return None
    value = payload.removeprefix("pid=").splitlines()[0].strip()
    try:
        pid = int(value)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _command_is_project_owner(command: str, root: Path) -> bool:
    """Recognize only collection/gate-refresh commands from this checkout."""

    command_root = str(root.resolve())
    return command_root in command and (
        "scripts/collection_lock.py" in command or "gate-refresh" in command
    )


def assess_lock(
    path: Path,
    *,
    namespace: str,
    project_root: Path,
    process_table: Mapping[int, ProcessInfo],
    now: float | None = None,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> LockAssessment:
    """Assess one expected lock without modifying the filesystem."""

    if path.name not in LOCK_NAMES:
        return LockAssessment(path, False, "unrecognized-lock", None, 0.0)
    try:
        age = max(0.0, (now if now is not None else time.time()) - path.stat().st_mtime)
    except OSError:
        return LockAssessment(path, False, "missing-lock", None, 0.0)
    pid = _owner_pid(path)
    process = process_table.get(pid) if pid is not None else None
    if process is not None and pid == os.getpid():
        return LockAssessment(path, False, "current-process-owner", pid, age)
    if process is not None and _command_is_project_owner(process.command, project_root):
        return LockAssessment(path, False, "live-project-owner", pid, age)
    # A live PID with a different command identity may belong to another
    # checkout.  Never infer staleness from age alone: PID reuse and external
    # worktrees are precisely why command identity is part of this guard.
    if process is not None:
        return LockAssessment(path, False, "live-owner-identity-mismatch", pid, age)
    if age < max(0.0, stale_after):
        return LockAssessment(path, False, "fresh-or-unknown-owner", pid, age)
    reason = "dead-owner" if pid is not None else "invalid-owner"
    return LockAssessment(path, True, reason, pid, age)


def reap_stale_locks(
    lock_root: Path,
    *,
    namespace: str,
    project_root: Path,
    process_table: Mapping[int, ProcessInfo] | None = None,
    now: float | None = None,
    stale_after: float = DEFAULT_STALE_AFTER,
    apply: bool = False,
) -> list[Path]:
    """Assess and optionally unlink stale locks directly below ``lock_root``."""

    expected_root = lock_root.expanduser().resolve()
    if expected_root.name != namespace:
        print(f"KEEP namespace-mismatch root={expected_root} namespace={namespace}")
        return []
    table = process_table if process_table is not None else snapshot_processes()
    removed: list[Path] = []
    for path in sorted(expected_root.glob("*.lock")):
        if path.parent.resolve() != expected_root:
            continue
        decision = assess_lock(
            path,
            namespace=namespace,
            project_root=project_root,
            process_table=table,
            now=now,
            stale_after=stale_after,
        )
        action = "STALE" if decision.stale else "KEEP"
        print(f"{action} {decision.reason} pid={decision.pid or '-'} path={path}")
        if decision.stale and apply:
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
    return removed


def stale_gate_refresh_roots(
    process_table: Mapping[int, ProcessInfo],
    *,
    namespace: str,
    project_root: Path,
    stale_after: float = DEFAULT_STALE_AFTER,
) -> list[ProcessInfo]:
    """Find orphaned gate-refresh roots owned by this checkout only."""

    root = project_root.resolve()
    candidates: list[ProcessInfo] = []
    for process in process_table.values():
        command = process.command
        if process.pid == os.getpid() or process.ppid != 1:
            continue
        if process.elapsed_secs < max(0.0, stale_after):
            continue
        if namespace not in command or "gate-refresh" not in command:
            continue
        if not _command_is_project_owner(command, root):
            continue
        candidates.append(process)
    return sorted(candidates, key=lambda item: item.pid)


def reap_stale_gate_refresh_roots(
    process_table: Mapping[int, ProcessInfo],
    *,
    namespace: str,
    project_root: Path,
    stale_after: float = DEFAULT_STALE_AFTER,
    apply: bool = False,
) -> list[int]:
    """Terminate stale roots after the same identity checks as lock cleanup."""

    killed: list[int] = []
    for process in stale_gate_refresh_roots(
        process_table,
        namespace=namespace,
        project_root=project_root,
        stale_after=stale_after,
    ):
        print(f"STALE gate-refresh-root pid={process.pid} command={process.command}")
        if apply:
            try:
                os.kill(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError, PermissionError):
                continue
            killed.append(process.pid)
    return killed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="remove stale lock files")
    parser.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER)
    args = parser.parse_args(argv)
    root = project_root()
    namespace = project_namespace(root)
    lock_root = resource_root(root)
    table = snapshot_processes()
    reap_stale_locks(
        lock_root,
        namespace=namespace,
        project_root=root,
        process_table=table,
        stale_after=args.stale_after,
        apply=args.apply,
    )
    reap_stale_gate_refresh_roots(
        table,
        namespace=namespace,
        project_root=root,
        stale_after=args.stale_after,
        apply=args.apply,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
