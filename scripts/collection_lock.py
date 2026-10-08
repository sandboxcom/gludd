"""Project-scoped serialization for repository-wide pytest collection.

Collection walks the entire test tree and writes shared pytest metadata.  A
namespaced advisory lock prevents concurrent commit hooks and gate refreshes
from doing that work at the same time, while keeping unrelated checkouts
independent.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

if TYPE_CHECKING or __package__:
    from scripts.resource_arbiter import project_root, resource_path
else:  # pragma: no cover - direct script execution
    _resource_arbiter = import_module("resource_arbiter")
    project_root = _resource_arbiter.project_root
    resource_path = _resource_arbiter.resource_path

DEFAULT_COLLECTION_LOCK_TIMEOUT = 900.0
DEFAULT_GATE_REFRESH_LOCK_TIMEOUT = 120.0
GIT_IDENTITY_TIMEOUT = 5.0
MAX_LEASE_RECORD_BYTES = 512
_UNSAFE_NAMESPACE = re.compile(r"[^A-Za-z0-9_.-]+")


class RepositoryIdentityError(RuntimeError):
    """The shared Git repository identity could not be proven."""


@dataclass(frozen=True)
class LeaseRecord:
    """Content-safe owner fields parsed from a bounded lock record."""

    pid: int
    acquired_unix_ns: int | None


@dataclass(frozen=True)
class LeaseInspection:
    """Kernel lock state plus a bounded advisory owner record."""

    state: Literal["available", "held", "unavailable"]
    record: LeaseRecord | None


def _positive_decimal(value: str, *, maximum: int) -> int | None:
    """Parse one canonical positive decimal within an explicit bound."""

    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        return None
    parsed = int(value)
    return parsed if 0 < parsed <= maximum else None


def parse_lease_record(payload: str) -> LeaseRecord | None:
    """Parse only allowlisted fields from one bounded advisory record."""

    try:
        if len(payload.encode("utf-8")) > MAX_LEASE_RECORD_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    fields: dict[str, str] = {}
    for line in payload.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in {"pid", "acquired_unix_ns"}:
            continue
        if key in fields:
            return None
        fields[key] = value
    pid = _positive_decimal(fields.get("pid", ""), maximum=2**31 - 1)
    if pid is None:
        return None
    acquired_value = fields.get("acquired_unix_ns")
    acquired = None
    if acquired_value is not None:
        acquired = _positive_decimal(acquired_value, maximum=2**63 - 1)
        if acquired is None:
            return None
    return LeaseRecord(pid=pid, acquired_unix_ns=acquired)


def _read_lease_record(handle: TextIO) -> LeaseRecord | None:
    """Read no more than the public record bound from an open lease."""

    handle.seek(0)
    return parse_lease_record(handle.read(MAX_LEASE_RECORD_BYTES + 1))


def inspect_lease(path: Path | str) -> LeaseInspection:
    """Observe a lease without creating, unlinking, or trusting its record."""

    lock_path = Path(path).expanduser()
    try:
        identity = lock_path.lstat()
    except FileNotFoundError:
        return LeaseInspection(state="available", record=None)
    except OSError:
        return LeaseInspection(state="unavailable", record=None)
    if not lock_path.is_file() or lock_path.is_symlink():
        return LeaseInspection(state="unavailable", record=None)
    try:
        with lock_path.open("r+", encoding="utf-8") as handle:
            opened = os.fstat(handle.fileno())
            if (identity.st_dev, identity.st_ino) != (opened.st_dev, opened.st_ino):
                return LeaseInspection(state="unavailable", record=None)
            record = _read_lease_record(handle)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return LeaseInspection(state="held", record=record)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return LeaseInspection(state="available", record=record)
    except (OSError, UnicodeError):
        return LeaseInspection(state="unavailable", record=None)


def repository_common_dir(start: Path | str | None = None) -> Path:
    """Return Git's canonical directory shared by all linked worktrees."""

    checkout = project_root(start)
    command = ["git", "-C", str(checkout), "rev-parse", "--git-common-dir"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=GIT_IDENTITY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepositoryIdentityError(
            f"Git common directory inspection failed for {checkout}"
        ) from exc
    output = result.stdout.strip()
    if result.returncode != 0 or not output or "\n" in output:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        raise RepositoryIdentityError(
            f"Git common directory unavailable for {checkout}: {detail}"
        )
    candidate = Path(output).expanduser()
    if not candidate.is_absolute():
        candidate = checkout / candidate
    try:
        common_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise RepositoryIdentityError(
            f"Git common directory cannot be resolved for {checkout}"
        ) from exc
    if not common_dir.is_dir():
        raise RepositoryIdentityError(
            f"Git common directory is not a directory: {common_dir}"
        )
    return common_dir


def repository_namespace(common_dir: Path | str) -> str:
    """Return a path-safe namespace derived only from shared repository state."""

    resolved = Path(common_dir).expanduser().resolve(strict=True)
    label_path = resolved.parent if resolved.name == ".git" else resolved
    label = _UNSAFE_NAMESPACE.sub("-", label_path.name).strip("-._") or "repository"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{label}-git-{digest}"


def repository_resource_lock(
    resource: str = "collection", start: Path | str | None = None
) -> Path:
    """Return one resource lease shared by every worktree of a repository."""

    common_dir = repository_common_dir(start)
    prototype = resource_path(resource, common_dir)
    return prototype.parent.parent / repository_namespace(common_dir) / prototype.name


def default_resource_lock(
    resource: str = "collection", start: Path | str | None = None
) -> Path:
    """Return a stable project-scoped lock path for one resource."""

    if resource == "collection":
        configured = os.environ.get("GLUDD_COLLECTION_LOCK", "").strip()
        if configured:
            return Path(configured).expanduser()
    return repository_resource_lock(resource, start)


def default_collection_lock(start: Path | str | None = None) -> Path:
    """Return the stable lock path for repository-wide collection."""

    return default_resource_lock(start=start)


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
            handle.write(
                f"pid={os.getpid()}\n"
                f"acquired_unix_ns={time.time_ns()}\n"
            )
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
    started = time.monotonic()
    print(
        f"collection lock waiting: resource={resource} path={lock} "
        f"timeout={wait:.3f}s",
        flush=True,
    )
    acquired = False
    try:
        with collection_lock(lock, timeout=wait):
            acquired = True
            waited = time.monotonic() - started
            print(
                f"collection lock acquired: resource={resource} path={lock} "
                f"waited={waited:.3f}s pid={os.getpid()}",
                flush=True,
            )
            return subprocess.run(command, check=False).returncode
    finally:
        if acquired:
            held = time.monotonic() - started
            print(
                f"collection lock released: resource={resource} path={lock} "
                f"elapsed={held:.3f}s pid={os.getpid()}",
                flush=True,
            )


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
    except (RepositoryIdentityError, TimeoutError) as exc:
        print(f"collection lock unavailable: {exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
