"""Agent-specific lease authority for the existing native watchdog lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, cast

from . import lease as lease_owner

if TYPE_CHECKING:
    import task_watchdog as native
    from kill_owned_gate import ProcessRecord


def _lease_pid(path: Path) -> int | None:
    try:
        owner = json.loads(path.read_text(encoding="utf-8"))
        pid = owner.get("pid") if isinstance(owner, dict) else None
        return pid if type(pid) is int and pid > 1 else None
    except (OSError, UnicodeError, ValueError):
        return None


def _pid_path(root: Path) -> Path:
    return root / ".gate-logs/watchdog.pid"


def _lease_path(runtime: Any, root: Path) -> Path:
    path = Path(runtime.watchdog_lock_path(root))
    return path.parent.resolve() / path.name


def _unique_fields(fields: list[tuple[str, object]]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in fields:
        if key in record:
            raise ValueError("agent singleton lease contains duplicate fields")
        record[key] = value
    return record


def _require_ready(runtime: Any, record: ProcessRecord, root: Path) -> None:
    pointer = _pid_path(root)
    ready = _lease_metadata(runtime, root).get("ready")
    if (type(ready) is not int or ready != 1
            or pointer.parent.is_symlink() or pointer.is_symlink() or not pointer.is_file()
            or pointer.read_text(encoding="utf-8") != str(record.pid)):
        raise ValueError("agent readiness PID is unverified; use the native stop owner for safe handoff")


def _lease_metadata(runtime: Any, root: Path) -> dict[str, object]:
    path = _lease_path(runtime, root)
    if (path.is_symlink() or not path.is_file() or path.resolve() != path.absolute()
            or path.stat().st_size > 8192):
        raise ValueError("agent singleton lease is not a regular bounded namespaced file")
    value: object = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict):
        raise ValueError("agent singleton lease is not a record")
    owner = cast(dict[str, object], value)
    pid = owner.get("pid")
    if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1
            or owner.get("namespace") != runtime.project_namespace(root)
            or owner.get("version") != runtime.WATCHDOG_VERSION
            or not isinstance(owner.get("token"), str) or not owner["token"]
            or not isinstance(owner.get("pid_start_time"), str) or not owner["pid_start_time"]):
        raise ValueError("agent singleton namespace, version, token or native start identity is unverified")
    return dict(owner)


def _lease_authority(
    runtime: Any, record: ProcessRecord, root: Path,
) -> Callable[[list[ProcessRecord]], None]:
    path = _lease_path(runtime, root)
    owner = _lease_metadata(runtime, root)
    content = path.read_bytes()
    original = path.stat()
    if (owner["pid"] != record.pid
            or owner["pid_start_time"] != runtime._process_start_time(record.pid)):
        raise ValueError("agent singleton native start identity is unverified")
    pointer = _pid_path(root)
    pointer_content: bytes | None = None
    if pointer.exists() or pointer.is_symlink():
        if pointer.is_symlink() or not pointer.is_file() or pointer.read_text(encoding="utf-8") != str(record.pid):
            raise ValueError("agent convenience PID pointer is unverified")
        pointer_content = pointer.read_bytes()

    def unchanged(records: list[ProcessRecord]) -> None:
        if not any(current.pid == record.pid for current in records):
            return
        try:
            current = path.stat()
            if (path.is_symlink() or path.read_bytes() != content
                    or (current.st_dev, current.st_ino, current.st_mtime_ns)
                    != (original.st_dev, original.st_ino, original.st_mtime_ns)
                    or runtime._process_start_time(record.pid) != owner["pid_start_time"]):
                raise ValueError("agent singleton lease changed during lifecycle")
            if pointer_content is not None and (
                pointer.is_symlink() or pointer.read_bytes() != pointer_content
            ):
                raise ValueError("agent convenience PID pointer changed during lifecycle")
        except OSError as exc:
            # A missing publication while the PID is still present revokes signals;
            # the shared native exit waiter must continue until actual PID absence.
            raise ValueError("agent singleton publication disappeared before native exit") from exc

    unchanged([record])
    return unchanged


def specification(runtime: Any) -> native.WatchdogSpec:
    """Keep every generic lifecycle decision in the original task owner."""
    if TYPE_CHECKING:
        import task_watchdog as owner
    else:
        from scripts import task_watchdog as owner
    root = Path(runtime._WORKSPACE).resolve()
    return owner.WatchdogSpec(
        script_name="agent_watchdog.py", stem="watchdog", label="AGENT-WATCHDOG",
        pid_environment="GLUDD_AGENT_WATCHDOG_PID", log_environment="GLUDD_AGENT_WATCHDOG_LOG",
        environment=(("GLUDD_WORKSPACE_ROOT", str(root)),),
        marker=lambda selected: _lease_path(runtime, selected),
        read_pid=_lease_pid, drain_supervisor=False,
        startup_ready=lambda record, selected: _require_ready(runtime, record, selected),
        authority=lambda record, selected: _lease_authority(runtime, record, selected),
    )


def dispatch(runtime: Any, argv: list[str]) -> int:
    if TYPE_CHECKING:
        import kill_owned_gate as cleanup
        import task_watchdog as owner
    else:
        from scripts import kill_owned_gate as cleanup
        from scripts import task_watchdog as owner
    parser = argparse.ArgumentParser(description="Plan/start or drain the project-owned agent watchdog")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--start", action="store_true")
    operation.add_argument("--stop", action="store_true")
    parser.add_argument("--validate-only", choices=("0", "1"), default="1")
    arguments = parser.parse_args(argv)
    root = Path(runtime._WORKSPACE).resolve()
    spec = specification(runtime)
    apply = arguments.validate_only == "0"
    if arguments.start:
        return 0 if owner.start_owned_watchdog(root, apply=apply, spec=spec).success else 1
    try:
        # A missing lease never hides an unpointed daemon or its UV supervisor.
        marker = spec.marker_path(root)
        if marker.exists() or marker.is_symlink():
            _lease_metadata(runtime, root)
        else:
            for _attempt in range(2):
                records = cleanup._records(root)
                invocation = owner._current_stop_invocation(root, records, apply=apply, spec=spec)
                owner._confirm_watchdog_absence(root, None, records, invocation, spec)
        return 0 if owner.stop_owned_watchdog(root, apply=apply, spec=spec) else 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"AGENT-WATCHDOG-STOP refused: {exc}", flush=True)
        return 1


def configured_pointer(runtime: Any) -> Path | None:
    configured = os.environ.get("GLUDD_AGENT_WATCHDOG_PID")
    if configured is None:
        return None
    pointer = Path(configured)
    if pointer != _pid_path(Path(runtime._WORKSPACE).resolve()):
        raise ValueError("agent startup PID path is not checkout-owned")
    return pointer


def publish_ready(runtime: Any, lease: Any) -> None:
    """Commit the actual child PID only after checked startup readiness."""
    pointer = configured_pointer(runtime)
    if pointer is None:
        return
    root = Path(runtime._WORKSPACE).resolve()
    log = Path(os.environ.get("GLUDD_AGENT_WATCHDOG_LOG", ""))
    if (log != root / ".gate-logs/watchdog.log"
            or pointer.is_symlink() or pointer.exists() or log.is_symlink()
            or not log.is_file() or not isinstance(lease.token, str)):
        raise ValueError("agent startup publication paths are unverified")
    pid = os.getpid()
    with log.open("a", encoding="utf-8") as output:
        output.write(f"agent_watchdog ready: pid={pid} token={lease.token}\n")
        output.flush()
        os.fsync(output.fileno())
    with pointer.open("x", encoding="utf-8") as marker:
        marker.write(str(pid))
        marker.flush()
        os.fsync(marker.fileno())
    lease_owner.acknowledge_watchdog(runtime, lease)
    print(f"agent_watchdog started: pid={pid} namespace={runtime.project_namespace(root)}", flush=True)


def release_pointer(pointer: Path | None) -> None:
    if pointer is not None and not pointer.is_symlink():
        try:
            if pointer.read_text(encoding="utf-8") == str(os.getpid()):
                pointer.unlink()
        except FileNotFoundError:
            pass


def termination_handler(_signum: int, _frame: FrameType | None) -> None:
    """Let SIGTERM unwind the daemon's existing lease-release finally."""
    raise SystemExit(0)


def install_termination_handler() -> Callable[[int, FrameType | None], object] | int | None:
    return signal.signal(signal.SIGTERM, termination_handler)
