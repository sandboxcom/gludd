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
from pathlib import Path

try:
    from scripts.resource_arbiter import resource_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from resource_arbiter import resource_path


def default_collection_lock() -> Path:
    """Return the stable lock path for the current project checkout."""

    configured = os.environ.get("GLUDD_COLLECTION_LOCK", "").strip()
    return Path(configured).expanduser() if configured else resource_path("collection")


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


def run_locked(command: list[str], *, timeout: float | None = None) -> int:
    """Run ``command`` while holding the current project's collection lock."""

    lock = default_collection_lock()
    wait = float(os.environ.get("GLUDD_COLLECTION_LOCK_TIMEOUT", "900"))
    if timeout is not None:
        wait = timeout
    print(f"collection lock waiting: {lock}", flush=True)
    with collection_lock(lock, timeout=wait):
        print(f"collection lock acquired: {lock}", flush=True)
        return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    """Run a command under the project-scoped collection lock."""

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "--run" or len(args) == 1:
        print("usage: collection_lock.py --run COMMAND [ARGS...]")
        return 2
    try:
        return run_locked(args[1:])
    except TimeoutError as exc:
        print(f"collection lock unavailable: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
