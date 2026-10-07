#!/usr/bin/env python3
"""Launch one full gate in an identity-tracked, detached POSIX session.

The historical Make recipe used ``nohup`` plus an hour-long shell sleeper.  This
module owns both lifecycle edges instead: the gate is the leader of a new
session, and a short polling watcher exits as soon as that exact process exits.
PID reuse, cross-worktree state, and a replaced current-run record are never
treated as authority to signal a process.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from scripts.resource_arbiter import project_namespace
else:
    if __package__:
        from scripts.resource_arbiter import project_namespace
    else:
        from resource_arbiter import project_namespace


DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_POLL_SECONDS = 0.2
DEFAULT_GRACE_SECONDS = 10.0
WATCH_HEARTBEAT_SECONDS = 30.0
TERMINATION_HEARTBEAT_SECONDS = 1.0
STATE_KIND = "gludd_gate_background"
STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GatePaths:
    """Worktree-local compatibility paths for one background gate."""

    project_root: Path
    log_dir: Path
    pid_file: Path
    state_file: Path
    status_file: Path
    launch_lock: Path

    @classmethod
    def for_root(cls, root: Path | str) -> GatePaths:
        """Derive paths without creating them (important for validation mode)."""
        resolved = Path(root).resolve()
        log_dir = resolved / ".gate-logs"
        return cls(
            project_root=resolved,
            log_dir=log_dir,
            pid_file=resolved / ".gate-background.pid",
            state_file=log_dir / "gate-background-state.json",
            status_file=resolved / ".gate-status",
            launch_lock=log_dir / "gate-background-launch.lock",
        )


@dataclass(frozen=True)
class GateIdentity:
    """Stable process identity; PID alone is intentionally insufficient."""

    run_id: str
    pid: int
    pid_started_at: str
    process_group_id: int
    session_id: int


@dataclass(frozen=True)
class GateLaunch:
    """Result returned to both the CLI and focused behavioral tests."""

    launched: bool
    identity: GateIdentity | None
    log_path: Path | None
    process: subprocess.Popen[bytes] | None = None
    watchdog_process: subprocess.Popen[bytes] | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    """Fsync one replacement before making it visible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    _atomic_write(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if value > 1 else None


def _pid_started_at(pid: int) -> str | None:
    """Return the same stable OS token used by the gate-run lock."""
    if pid <= 1:
        return None
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    started_at = completed.stdout.strip()
    return started_at or None


def _capture_identity(pid: int, run_id: str) -> GateIdentity | None:
    started_at = _pid_started_at(pid)
    if started_at is None:
        return None
    try:
        process_group_id = os.getpgid(pid)
        session_id = os.getsid(pid)
    except (OSError, ProcessLookupError):
        return None
    return GateIdentity(
        run_id=run_id,
        pid=pid,
        pid_started_at=started_at,
        process_group_id=process_group_id,
        session_id=session_id,
    )


def _process_state(identity: GateIdentity) -> str:
    """Return ``owned``, ``gone``, or ``reused`` for an exact identity."""
    started_at = _pid_started_at(identity.pid)
    if started_at is None:
        return "gone"
    if started_at != identity.pid_started_at:
        return "reused"
    try:
        process_group_id = os.getpgid(identity.pid)
        session_id = os.getsid(identity.pid)
    except (OSError, ProcessLookupError):
        return "gone"
    if (
        process_group_id != identity.process_group_id
        or session_id != identity.session_id
    ):
        return "reused"
    return "owned"


def process_matches(identity: GateIdentity | None) -> bool:
    """Return whether the recorded PID still denotes the exact launched gate."""
    return identity is not None and _process_state(identity) == "owned"


def _json_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _identity_from_state(payload: Mapping[str, object]) -> GateIdentity | None:
    run_id_value = payload.get("run_id")
    pid_value = payload.get("pid")
    started_at_value = payload.get("pid_started_at")
    process_group_value = payload.get("process_group_id")
    session_value = payload.get("session_id")
    if (
        not isinstance(run_id_value, str)
        or not isinstance(started_at_value, str)
    ):
        return None
    if (
        payload.get("schema_version") != STATE_SCHEMA_VERSION
        or payload.get("kind") != STATE_KIND
    ):
        return None
    pid = _json_int(pid_value)
    process_group_id = _json_int(process_group_value)
    session_id = _json_int(session_value)
    if pid is None or process_group_id is None or session_id is None:
        return None
    if not run_id_value or pid <= 1 or not started_at_value:
        return None
    return GateIdentity(
        run_id_value,
        pid,
        started_at_value,
        process_group_id,
        session_id,
    )


def _state_is_current(paths: GatePaths, identity: GateIdentity) -> bool:
    payload = _read_json(paths.state_file)
    current = _identity_from_state(payload or {})
    return current == identity


def _merge_state(
    paths: GatePaths, identity: GateIdentity, updates: Mapping[str, object]
) -> bool:
    """Update only the still-current run; an old watcher cannot clobber a new run."""
    payload = _read_json(paths.state_file)
    if payload is None or _identity_from_state(payload) != identity:
        return False
    payload.update(updates)
    payload["updated_at"] = _utc_now()
    _atomic_write_json(paths.state_file, payload)
    return True


def _remove_owned_pid_file(paths: GatePaths, identity: GateIdentity) -> None:
    if _read_pid(paths.pid_file) == identity.pid:
        paths.pid_file.unlink(missing_ok=True)


def _format_seconds(value: float) -> str:
    return f"{value:g}"


def _append_log(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _current_log_path(paths: GatePaths, identity: GateIdentity) -> Path | None:
    """Resolve the exact current run log without allowing receipt path escape."""
    payload = _read_json(paths.state_file)
    if payload is None or _identity_from_state(payload) != identity:
        return None
    raw_path = payload.get("log_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    log_path = Path(raw_path).resolve()
    if (
        log_path.parent != paths.log_dir.resolve()
        or not log_path.name.startswith("gate-")
        or log_path.suffix != ".log"
    ):
        return None
    return log_path


def _signal_session(identity: GateIdentity, signum: signal.Signals) -> bool:
    if _process_state(identity) != "owned":
        return False
    try:
        os.killpg(identity.process_group_id, signum)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_session(
    identity: GateIdentity,
    *,
    grace_seconds: float,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> None:
    """TERM then KILL only the launched session after exact revalidation."""
    print(
        f"[gate-background-watch] phase=terminate-term pid={identity.pid} "
        f"grace={_format_seconds(grace_seconds)}s",
        flush=True,
    )
    _signal_session(identity, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace_seconds)
    next_heartbeat = time.monotonic()
    while _process_state(identity) == "owned" and time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_heartbeat:
            print(
                f"[gate-background-watch] phase=terminate-wait pid={identity.pid} "
                f"remaining={max(0.0, deadline - now):.1f}s",
                flush=True,
            )
            next_heartbeat = now + TERMINATION_HEARTBEAT_SECONDS
        time.sleep(min(max(poll_seconds, 0.001), max(0.0, deadline - now)))
    if _process_state(identity) == "owned":
        print(
            f"[gate-background-watch] phase=terminate-kill pid={identity.pid}",
            flush=True,
        )
        _signal_session(identity, signal.SIGKILL)
        kill_deadline = time.monotonic() + max(
            poll_seconds, min(grace_seconds, 1.0)
        )
        next_heartbeat = time.monotonic()
        while (
            _process_state(identity) == "owned"
            and time.monotonic() < kill_deadline
        ):
            now = time.monotonic()
            if now >= next_heartbeat:
                print(
                    f"[gate-background-watch] phase=kill-wait pid={identity.pid} "
                    f"remaining={max(0.0, kill_deadline - now):.1f}s",
                    flush=True,
                )
                next_heartbeat = now + TERMINATION_HEARTBEAT_SECONDS
            time.sleep(
                min(
                    max(poll_seconds, 0.001),
                    max(0.0, kill_deadline - now),
                )
            )


def _watch_outcome_when_not_owned(identity: GateIdentity) -> str | None:
    state = _process_state(identity)
    if state == "gone":
        return "finished"
    if state == "reused":
        return "superseded"
    return None


def watch_gate(
    identity: GateIdentity,
    paths: GatePaths,
    *,
    timeout_seconds: float,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> str:
    """Watch one exact session until exit or timeout, then terminate and retire."""
    if timeout_seconds <= 0 or poll_seconds <= 0 or grace_seconds <= 0:
        raise ValueError("watcher durations must be positive")
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    next_heartbeat = started_at
    while True:
        outcome = _watch_outcome_when_not_owned(identity)
        if outcome is not None:
            if outcome == "finished":
                _merge_state(
                    paths,
                    identity,
                    {
                        "state": "finished",
                        "finished_at": _utc_now(),
                        "termination_reason": "gate-exited",
                    },
                )
            return outcome
        if not _state_is_current(paths, identity):
            return "superseded"
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        if now >= next_heartbeat:
            print(
                f"[gate-background-watch] heartbeat run_id={identity.run_id} "
                f"pid={identity.pid} elapsed={max(0.0, now - started_at):.1f}s "
                f"remaining={remaining:.1f}s",
                flush=True,
            )
            next_heartbeat = now + WATCH_HEARTBEAT_SECONDS
        time.sleep(min(poll_seconds, remaining))

    # Serialize the final identity check and signal with launcher admission.
    # Without this lock, a replacement run could publish between those events.
    with paths.launch_lock.open("a+b") as launch_lock:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_EX)
        outcome = _watch_outcome_when_not_owned(identity)
        if outcome is not None:
            return outcome
        log_path = _current_log_path(paths, identity)
        if log_path is None:
            return "superseded"

        timeout_text = _format_seconds(timeout_seconds)
        _append_log(
            log_path,
            f"=== GATE: ABORTED (timeout {timeout_text}s) ===\n",
        )
        _terminate_session(
            identity, grace_seconds=grace_seconds, poll_seconds=poll_seconds
        )
        _atomic_write(
            paths.status_file,
            f"GATE_TIMEOUT\n=== GATE: ABORTED (timeout {timeout_text}s) ===\n",
        )
        _remove_owned_pid_file(paths, identity)
        _merge_state(
            paths,
            identity,
            {
                "state": "timed_out",
                "finished_at": _utc_now(),
                "termination_reason": "gate-timeout",
            },
        )
    return "timed_out"


def _existing_identity(paths: GatePaths, pid: int) -> GateIdentity | None:
    payload = _read_json(paths.state_file)
    identity = _identity_from_state(payload or {})
    if identity is not None and identity.pid == pid:
        return identity
    return _capture_identity(pid, f"legacy-{pid}")


def _existing_age(paths: GatePaths) -> float:
    try:
        return max(0.0, time.time() - paths.pid_file.stat().st_mtime)
    except OSError:
        return 0.0


def _new_log_path(paths: GatePaths, run_id: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return paths.log_dir / f"gate-{timestamp}-{run_id[-8:]}.log"


def _state_payload(
    *,
    identity: GateIdentity,
    paths: GatePaths,
    namespace: str,
    log_path: Path,
    command: Sequence[str],
    timeout_seconds: float,
) -> dict[str, object]:
    started_at = _utc_now()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "kind": STATE_KIND,
        "state": "running",
        "run_id": identity.run_id,
        "pid": identity.pid,
        "pid_started_at": identity.pid_started_at,
        "process_group_id": identity.process_group_id,
        "session_id": identity.session_id,
        "watchdog_pid": None,
        "project_root": str(paths.project_root),
        "project_namespace": namespace,
        "log_path": str(log_path),
        "command": list(command),
        "command_text": shlex.join(command),
        "timeout_seconds": timeout_seconds,
        "started_at": started_at,
        "updated_at": started_at,
        "finished_at": None,
        "termination_reason": None,
    }


def _start_watcher(
    identity: GateIdentity,
    paths: GatePaths,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    grace_seconds: float,
    output: BinaryIO,
) -> subprocess.Popen[bytes]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--project-root",
        str(paths.project_root),
        "--watch-run-id",
        identity.run_id,
        "--timeout-seconds",
        str(timeout_seconds),
        "--watcher-poll-seconds",
        str(poll_seconds),
        "--termination-grace-seconds",
        str(grace_seconds),
    ]
    return subprocess.Popen(
        command,
        cwd=paths.project_root,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def launch_gate(
    project_root: Path | str,
    gate_command: Sequence[str],
    *,
    timeout_seconds: float,
    start_watcher: bool = True,
    watcher_poll_seconds: float = DEFAULT_POLL_SECONDS,
    termination_grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> GateLaunch:
    """Atomically admit and launch one worktree-scoped full gate."""
    if not gate_command:
        raise ValueError("gate command must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout seconds must be positive")
    if watcher_poll_seconds <= 0 or termination_grace_seconds <= 0:
        raise ValueError("watcher durations must be positive")

    paths = GatePaths.for_root(project_root)
    paths.log_dir.mkdir(parents=True, exist_ok=True)
    namespace = project_namespace(paths.project_root)
    with paths.launch_lock.open("a+b") as launch_lock:
        fcntl.flock(launch_lock.fileno(), fcntl.LOCK_EX)
        existing_pid = _read_pid(paths.pid_file)
        if existing_pid is not None:
            existing = _existing_identity(paths, existing_pid)
            if existing is not None and process_matches(existing):
                age = _existing_age(paths)
                print(
                    f"[gate-background] gate already running "
                    f"(pid={existing_pid} elapsed={age:.0f}s) - refusing to launch duplicate",
                    flush=True,
                )
                return GateLaunch(False, existing, None)
            paths.pid_file.unlink(missing_ok=True)

        run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        log_path = _new_log_path(paths, run_id)
        environment = os.environ.copy()
        environment["GLUDD_PROJECT_ROOT"] = str(paths.project_root)
        environment["GLUDD_PROJECT_NAMESPACE"] = namespace

        with log_path.open("wb") as log_file:
            header = (
                f"[gate-background] launcher run_id={run_id} namespace={namespace} "
                f"root={paths.project_root} timeout={_format_seconds(timeout_seconds)}s\n"
            ).encode()
            log_file.write(header)
            log_file.flush()
            os.fsync(log_file.fileno())
            process = subprocess.Popen(
                list(gate_command),
                cwd=paths.project_root,
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            identity: GateIdentity | None = None
            for _attempt in range(20):
                identity = _capture_identity(process.pid, run_id)
                if identity is not None:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.005)
            if identity is None:
                process.wait(timeout=5)
                raise RuntimeError("gate exited before its process identity was captured")

            _atomic_write(paths.pid_file, f"{identity.pid}\n")
            _atomic_write_json(
                paths.state_file,
                _state_payload(
                    identity=identity,
                    paths=paths,
                    namespace=namespace,
                    log_path=log_path,
                    command=gate_command,
                    timeout_seconds=timeout_seconds,
                ),
            )

            watchdog: subprocess.Popen[bytes] | None = None
            if start_watcher:
                try:
                    watchdog = _start_watcher(
                        identity,
                        paths,
                        timeout_seconds=timeout_seconds,
                        poll_seconds=watcher_poll_seconds,
                        grace_seconds=termination_grace_seconds,
                        output=log_file,
                    )
                except OSError:
                    _terminate_session(
                        identity,
                        grace_seconds=min(termination_grace_seconds, 0.5),
                        poll_seconds=min(watcher_poll_seconds, 0.05),
                    )
                    process.wait(timeout=5)
                    _remove_owned_pid_file(paths, identity)
                    _merge_state(
                        paths,
                        identity,
                        {
                            "state": "launch_failed",
                            "finished_at": _utc_now(),
                            "termination_reason": "watcher-start-failed",
                        },
                    )
                    raise
                _merge_state(paths, identity, {"watchdog_pid": watchdog.pid})

        print(
            f"GATE-BACKGROUND pid={identity.pid} pgid={identity.process_group_id} "
            f"sid={identity.session_id} namespace={namespace} log={log_path} "
            f"state={paths.state_file}",
            flush=True,
        )
        print("Poll with: make gate-status-check", flush=True)
        return GateLaunch(True, identity, log_path, process, watchdog)


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--make-command", default="make")
    parser.add_argument("--makefile", type=Path)
    parser.add_argument(
        "--timeout-seconds", type=_positive_float, default=DEFAULT_TIMEOUT_SECONDS
    )
    parser.add_argument("--validate-only", choices=("0", "1"), default="0")
    parser.add_argument("--watch-run-id")
    parser.add_argument(
        "--watcher-poll-seconds", type=_positive_float, default=DEFAULT_POLL_SECONDS
    )
    parser.add_argument(
        "--termination-grace-seconds",
        type=_positive_float,
        default=DEFAULT_GRACE_SECONDS,
    )
    return parser


def _watch_from_state(args: argparse.Namespace, paths: GatePaths) -> int:
    payload = _read_json(paths.state_file)
    identity = _identity_from_state(payload or {})
    if identity is None or identity.run_id != args.watch_run_id:
        print(
            f"[gate-background-watch] state identity unavailable run_id={args.watch_run_id}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    outcome = watch_gate(
        identity,
        paths,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.watcher_poll_seconds,
        grace_seconds=args.termination_grace_seconds,
    )
    print(
        f"[gate-background-watch] outcome={outcome} pid={identity.pid} "
        f"run_id={identity.run_id}",
        flush=True,
    )
    return 124 if outcome == "timed_out" else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, launch, or watch one exact gate run."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    paths = GatePaths.for_root(args.project_root)
    if args.watch_run_id:
        try:
            return _watch_from_state(args, paths)
        except (OSError, ValueError) as exc:
            print(f"gate-background-watch: {exc}", file=sys.stderr, flush=True)
            return 2

    makefile = (args.makefile or paths.project_root / "Makefile").resolve()
    try:
        make_command = shlex.split(args.make_command)
    except ValueError as exc:
        print(f"gate-background: invalid make command: {exc}", file=sys.stderr)
        return 2
    if not make_command or not makefile.is_file():
        print("gate-background: make command and Makefile are required", file=sys.stderr)
        return 2
    gate_command = [
        *make_command,
        "-f",
        str(makefile),
        "--no-print-directory",
        "gate",
        "gludd_watchdog_owned_gate=1",
    ]
    namespace = project_namespace(paths.project_root)
    if args.validate_only == "1":
        print(
            f"gate-background: VALIDATE timeout={_format_seconds(args.timeout_seconds)}s "
            f"namespace={namespace} root={paths.project_root} "
            f"command={shlex.join(gate_command)}",
            flush=True,
        )
        return 0
    try:
        launch_gate(
            paths.project_root,
            gate_command,
            timeout_seconds=args.timeout_seconds,
            watcher_poll_seconds=args.watcher_poll_seconds,
            termination_grace_seconds=args.termination_grace_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"gate-background: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
