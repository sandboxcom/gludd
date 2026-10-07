"""PID/start-time verified singleton ownership for the watchdog daemon."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass
class WatchdogLease:
    """An owned watchdog lock; release only removes our own lock record."""

    path: Path
    fd: int
    token: str


def watchdog_lock_path(runtime: Any, workspace: Path | str | None = None) -> Path:
    """Return the project-namespaced singleton lock path."""
    root = runtime.Path(workspace) if workspace is not None else runtime._WORKSPACE
    return cast(Path, runtime.resource_path(runtime.WATCHDOG_LOCK_RESOURCE, root))


def _process_start_time(runtime: Any, pid: int) -> str | None:
    """Read a process start token where the host exposes one.

    Linux exposes a monotonic start tick in ``/proc``.  macOS and other hosts
    fall back to ``ps``'s start-date string.  A missing token is acceptable:
    liveness is still checked with ``kill(pid, 0)`` and stale owners recover
    once their process exits.
    """
    try:
        stat_path = runtime.Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            fields = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            if len(fields) > 19:
                return str(fields[19])
    except (OSError, ValueError, IndexError):
        pass
    try:
        result = runtime.subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=2
        )
        value = result.stdout.strip()
        return value or None
    except (OSError, runtime.subprocess.SubprocessError):
        return None


def _version_key(runtime: Any, version: str) -> tuple[tuple[int, ...], str]:
    """Compare semantic-ish watchdog versions without requiring packaging."""
    value = str(version).strip()
    numbers = tuple(int(part) for part in runtime.re.findall("\\d+", value))
    while numbers and numbers[-1] == 0:
        numbers = numbers[:-1]
    suffix = runtime.re.sub("[0-9.]+", "", value).lower()
    return (numbers, suffix)


def _owner_is_alive(runtime: Any, owner: dict[str, object]) -> bool:
    raw_pid = owner.get("pid", 0)
    if not isinstance(raw_pid, (int, float, str)):
        return False
    try:
        pid = int(raw_pid)
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        runtime.os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    recorded = owner.get("pid_start_time")
    current = runtime._process_start_time(pid)
    return not (recorded and current and (str(recorded) != str(current)))


def _read_lock_owner(runtime: Any, path: Path) -> dict[str, object] | None:
    try:
        value: object = runtime.json.loads(path.read_text(encoding="utf-8"))
        return cast(dict[str, object] | None, runtime._as_record(value))
    except (OSError, runtime.json.JSONDecodeError, TypeError):
        return None


def _unlink_if_token_matches(runtime: Any, path: Path, token: str | None) -> None:
    """Remove a lock only if it still refers to the owner we inspected."""
    current = runtime._read_lock_owner(path)
    if token is not None and current is not None and (current.get("token") != token):
        return
    with runtime.suppress(FileNotFoundError):
        path.unlink()


def acquire_watchdog_lock(
    runtime: Any, *, lock_path: Path | str | None = None, version: str | None = None, pid: int | None = None
) -> WatchdogLease | None:
    """Acquire the singleton watchdog lease, recovering stale owners.

    A live owner with the same or newer version wins.  A newer caller sends a
    polite ``SIGTERM`` to an older live owner, then replaces its record.  The
    token check in :func:`release_watchdog_lock` prevents an old process from
    deleting the replacement lock during shutdown.
    """
    path = runtime.Path(lock_path) if lock_path is not None else runtime.watchdog_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    owner_pid = int(pid if pid is not None else runtime.os.getpid())
    owner_version = str(version if version is not None else runtime.WATCHDOG_VERSION)
    token = runtime.uuid.uuid4().hex
    metadata = {
        "pid": owner_pid,
        "pid_start_time": runtime._process_start_time(owner_pid),
        "started_at": runtime.time.time(),
        "version": owner_version,
        "namespace": runtime.project_namespace(runtime._WORKSPACE),
        "token": token,
    }
    for _ in range(3):
        try:
            fd = runtime.os.open(path, runtime.os.O_CREAT | runtime.os.O_EXCL | runtime.os.O_WRONLY, 384)
        except FileExistsError:
            previous = runtime._read_lock_owner(path)
            if previous is None:
                runtime._unlink_if_token_matches(path, None)
                continue
            if runtime._owner_is_alive(previous):
                old_version = str(previous.get("version", "0"))
                if runtime._version_key(owner_version) <= runtime._version_key(old_version):
                    return None
                with runtime.suppress(KeyError, TypeError, ValueError, ProcessLookupError, PermissionError, OSError):
                    previous_pid = runtime._as_int(previous.get("pid"), -1)
                    if previous_pid > 0:
                        runtime.os.kill(previous_pid, runtime.signal.SIGTERM)
                runtime._unlink_if_token_matches(path, str(previous.get("token", "")))
                continue
            runtime._unlink_if_token_matches(path, str(previous.get("token", "")))
            continue
        else:
            try:
                runtime.os.write(fd, runtime.json.dumps(metadata).encode("utf-8"))
                runtime.os.fsync(fd)
            except Exception:
                runtime.os.close(fd)
                runtime._unlink_if_token_matches(path, token)
                raise
            return cast(WatchdogLease, runtime.WatchdogLease(path=path, fd=fd, token=token))
    return None


def release_watchdog_lock(runtime: Any, lease: WatchdogLease | None) -> None:
    """Release a lease without touching a newer owner's lock record."""
    if lease is None:
        return
    try:
        owner = runtime._read_lock_owner(lease.path)
        if owner is not None and owner.get("token") == lease.token:
            runtime._unlink_if_token_matches(lease.path, lease.token)
    finally:
        with runtime.suppress(OSError):
            runtime.os.close(lease.fd)


def stop_watchdog(runtime: Any, *, lock_path: Path | str | None = None) -> bool:
    """Request shutdown of this project's watchdog without global ``pkill``.

    A live owner's lock is intentionally left in place for its ``finally``
    block to release.  Dead or malformed records are removed immediately.
    """
    path = runtime.Path(lock_path) if lock_path is not None else runtime.watchdog_lock_path()
    owner = runtime._read_lock_owner(path)
    if owner is None:
        runtime._unlink_if_token_matches(path, None)
        return False
    if not runtime._owner_is_alive(owner):
        runtime._unlink_if_token_matches(path, str(owner.get("token", "")))
        return False
    owner_pid = runtime._as_int(owner.get("pid"), -1)
    if owner_pid <= 0:
        return False
    with runtime.suppress(KeyError, TypeError, ValueError, ProcessLookupError, PermissionError, OSError):
        runtime.os.kill(owner_pid, runtime.signal.SIGTERM)
        return True
    return False
