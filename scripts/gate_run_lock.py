#!/usr/bin/env python3
"""Live-owner lock shared by the full gate and gate-refresh.

The lock is an atomically-created JSON file containing the owning ``make``
process PID. A live owner fails closed; a dead owner is reclaimed. The lock
persists if a gate is killed, so stale-owner recovery is part of acquisition.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from scripts.resource_arbiter import project_namespace, project_root
else:
    from resource_arbiter import project_namespace, project_root


GATE_LOCK_MARKER = "gludd-gate-run-v1"


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_payload(lock_path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_owner(lock_path: Path) -> int | None:
    payload = _read_payload(lock_path)
    value = payload.get("pid") if payload is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _pid_started_at(pid: int) -> str | None:
    """Return the stable ps start token used to reject PID reuse."""
    completed = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        check=False,
        capture_output=True,
        text=True,
    )
    started_at = completed.stdout.strip()
    return started_at or None


def _publish_lock(lock_path: Path, pid: int) -> bool:
    started_at = _pid_started_at(pid)
    if started_at is None:
        raise ProcessLookupError(f"cannot capture gate owner start time for pid={pid}")
    root = project_root(Path.cwd())
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = lock_path.with_name(
        f".{lock_path.name}.{pid}.{uuid4().hex}.tmp"
    )
    candidate.write_text(
        json.dumps(
            {
                "marker": GATE_LOCK_MARKER,
                "state": "active",
                "pid": pid,
                "pid_started_at": started_at,
                "started_at": time.time(),
                "project_root": str(root),
                "project_namespace": project_namespace(root),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.link(candidate, lock_path)
    except FileExistsError:
        return False
    finally:
        candidate.unlink(missing_ok=True)
    return True


def acquire(lock_path: Path, pid: int) -> int:
    for _attempt in range(3):
        try:
            published = _publish_lock(lock_path, pid)
        except ProcessLookupError as exc:
            print(f"gate-run-lock: {exc}", file=sys.stderr)
            return 1
        if published:
            print(f"gate-run-lock: acquired {lock_path} pid={pid}")
            return 0

        payload = _read_payload(lock_path)
        if payload is not None and payload.get("state") == "termination_failed":
            print(
                f"gate-run-lock: prior gate termination failed; "
                f"manual recovery required (lock={lock_path})",
                file=sys.stderr,
            )
            return 1
        owner = _read_owner(lock_path)
        if owner is None:
            print(
                f"gate-run-lock: {lock_path} has unreadable owner; "
                "refusing to race",
                file=sys.stderr,
            )
            return 1
        if _pid_alive(owner):
            print(
                f"gate-run-lock: another gate is already running "
                f"(pid={owner}, lock={lock_path})",
                file=sys.stderr,
            )
            return 1

        with suppress(FileNotFoundError):
            lock_path.unlink()

    print(f"gate-run-lock: could not acquire {lock_path}", file=sys.stderr)
    return 1


def release(lock_path: Path, pid: int) -> int:
    owner = _read_owner(lock_path)
    if owner is None:
        print(f"gate-run-lock: no owned lock at {lock_path}", file=sys.stderr)
        return 1
    if owner != pid:
        print(
            f"gate-run-lock: pid={pid} cannot release owner pid={owner}",
            file=sys.stderr,
        )
        return 1
    try:
        lock_path.unlink()
    except FileNotFoundError:
        print(f"gate-run-lock: lock disappeared: {lock_path}", file=sys.stderr)
        return 1
    print(f"gate-run-lock: released {lock_path} pid={pid}")
    return 0


def assert_inactive(lock_path: Path, requester_pid: int) -> int:
    """Fail closed when a live gate owns the checkout mutation boundary."""
    if not lock_path.exists():
        print(f"gate-run-lock: inactive {lock_path} requester={requester_pid}")
        return 0

    payload = _read_payload(lock_path)
    if payload is not None and payload.get("state") == "termination_failed":
        print(
            f"gate-run-lock: prior gate termination failed; "
            f"repository mutation refused (lock={lock_path})",
            file=sys.stderr,
        )
        return 1
    owner = _read_owner(lock_path)
    if owner is None:
        print(
            f"gate-run-lock: {lock_path} has unreadable owner; "
            "refusing repository mutation",
            file=sys.stderr,
        )
        return 1
    if _pid_alive(owner):
        print(
            f"gate-run-lock: active gate pid={owner} blocks repository mutation "
            f"requester={requester_pid} lock={lock_path}",
            file=sys.stderr,
        )
        return 1

    with suppress(FileNotFoundError):
        lock_path.unlink()
    print(
        f"gate-run-lock: reclaimed stale owner pid={owner} "
        f"for requester={requester_pid} lock={lock_path}"
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in {
        "acquire",
        "release",
        "assert-inactive",
    }:
        print(
            "Usage: gate_run_lock.py "
            "<acquire|release|assert-inactive> <lock-path> <pid>",
            file=sys.stderr,
        )
        return 2
    try:
        pid = int(argv[3])
    except ValueError:
        print(f"gate-run-lock: invalid pid: {argv[3]!r}", file=sys.stderr)
        return 2

    lock_path = Path(argv[2])
    if argv[1] == "acquire":
        return acquire(lock_path, pid)
    if argv[1] == "assert-inactive":
        return assert_inactive(lock_path, pid)
    return release(lock_path, pid)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
