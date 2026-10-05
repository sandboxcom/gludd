#!/usr/bin/env python3
"""Live-owner lock shared by the full gate and gate-refresh.

The lock is an atomically-created JSON file containing the owning ``make``
process PID. A live owner fails closed; a dead owner is reclaimed. The lock
persists if a gate is killed, so stale-owner recovery is part of acquisition.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


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


def _read_owner(lock_path: Path) -> int | None:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return int(payload["pid"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _publish_lock(lock_path: Path, pid: int) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = lock_path.with_name(
        f".{lock_path.name}.{pid}.{uuid4().hex}.tmp"
    )
    candidate.write_text(
        json.dumps({"pid": pid, "started_at": time.time()}) + "\n",
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
        if _publish_lock(lock_path, pid):
            print(f"gate-run-lock: acquired {lock_path} pid={pid}")
            return 0

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
