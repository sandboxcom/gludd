"""Project-scoped serialization for repository-wide pytest collection.

Collection walks the entire test tree and writes shared pytest metadata.  A
namespaced advisory lock prevents concurrent commit hooks and gate refreshes
from doing that work at the same time, while keeping unrelated checkouts
independent.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts.resource_arbiter import resource_path
else:  # pragma: no cover - direct script execution
    _resource_arbiter = import_module("resource_arbiter")
    resource_path = _resource_arbiter.resource_path

DEFAULT_COLLECTION_LOCK_TIMEOUT = 900.0
DEFAULT_GATE_REFRESH_LOCK_TIMEOUT = 120.0


def default_resource_lock(resource: str = "collection") -> Path:
    """Return a stable project-scoped lock path for one resource."""

    if resource == "collection":
        configured = os.environ.get("GLUDD_COLLECTION_LOCK", "").strip()
        if configured:
            return Path(configured).expanduser()
    return resource_path(resource)


def default_collection_lock() -> Path:
    """Return the stable lock path for repository-wide collection."""

    return default_resource_lock()


def lock_timeout(resource: str = "collection") -> float:
    """Return the bounded wait for a lock resource.

    Gate refresh is a best-effort status update; it must fail fast enough that
    abandoned waiters cannot accumulate behind a long-running full gate. Direct
    collection callers retain the historical 15-minute default.
    """

    configured = os.environ.get("GLUDD_COLLECTION_LOCK_TIMEOUT", "")
    if resource == "gate-refresh":
        configured = os.environ.get(
            "GLUDD_GATE_REFRESH_LOCK_TIMEOUT",
            configured or str(DEFAULT_GATE_REFRESH_LOCK_TIMEOUT),
        )
    return float(configured or DEFAULT_COLLECTION_LOCK_TIMEOUT)


@contextmanager
def collection_lock(
    path: Path | str | None = None,
    *,
    timeout: float = 900.0,
    poll_interval: float = 0.05,
) -> Iterator[Path]:
    """Acquire an exclusive project collection lock and release it safely.

    ``timeout`` is bounded to avoid a deadlock if an interrupted owner leaves
    an open descriptor behind.  A timeout of zero performs a non-blocking
    attempt and raises ``TimeoutError`` when another owner is active.
    """

    if timeout < 0:
        raise ValueError("collection lock timeout must be non-negative")
    if poll_interval <= 0:
        raise ValueError("collection lock poll interval must be positive")
    lock_path = Path(path) if path is not None else default_collection_lock()
    lock_path = lock_path.expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if timeout == 0 or time.monotonic() - started >= timeout:
                    raise TimeoutError(f"collection lock is busy: {lock_path}") from exc
                time.sleep(min(poll_interval, max(timeout - (time.monotonic() - started), 0.0)))
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            yield lock_path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_locked(
    command: list[str], *, timeout: float | None = None, resource: str = "collection"
) -> int:
    """Run ``command`` while holding one project-scoped resource lock."""

    lock = default_resource_lock(resource)
    wait = lock_timeout(resource)
    if timeout is not None:
        wait = timeout
    print(f"collection lock waiting: {lock}", flush=True)
    with collection_lock(lock, timeout=wait):
        print(f"collection lock acquired: {lock}", flush=True)
        return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """Run a command under the project-scoped collection lock."""

    args = list(sys.argv[1:] if argv is None else argv)
    resource = "collection"
    if args[:1] == ["--resource"]:
        if len(args) < 3:
            print("usage: collection_lock.py --resource RESOURCE --run COMMAND [ARGS...]")
            return 2
        resource = args[1]
        args = args[2:]
    if not args or args[0] != "--run" or len(args) == 1:
        print("usage: collection_lock.py [--resource RESOURCE] --run COMMAND [ARGS...]")
        return 2
    try:
        return run_locked(args[1:], resource=resource)
    except TimeoutError as exc:
        print(f"collection lock unavailable: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
