#!/usr/bin/env python3
"""Stream or quietly capture a command with durable, parent-readable status."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext, suppress
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, BinaryIO, NamedTuple, Protocol, TypedDict

DEFAULT_OBSERVED_ROOT = Path(".gate-logs/observed")
DEFAULT_HEARTBEAT_SECS = 30.0
DEFAULT_STALE_SECS = 90.0
DEFAULT_RETAIN_RUNS = 20
OBSERVER_FAILURE = 125
TIMEOUT_FAILURE = 124
_TERMINAL_STATES = frozenset({"passed", "failed", "timed_out", "interrupted"})
_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


class _ChildCwdOptions(TypedDict, total=False):
    """Only the explicitly supplied child cwd; keep inherited-call kwargs unchanged."""

    cwd: Path | str


class _ProgressAdmission(Protocol):
    """Native item admission bound to one observed command publication."""

    identity: dict[str, object]

    def validate_execution(
        self, command: Sequence[str], *, environment: Mapping[str, str], cwd: Path | str | None,
        quiet_seconds: float, max_seconds: float,
    ) -> None:
        """Bind only this owner's effective child environment and working directory."""
        ...

    def record(self, observed_root: Path, label: str, run_id: str) -> None:
        """Bind only this owner's actual native starting publication."""
        ...

    def preserved_run_ids(self, observed_root: Path, label: str) -> frozenset[str]:
        """Return bounded native references still needed by admission."""
        ...

    def protected_run_ids(
        self, observed_root: Path, label: str,
    ) -> AbstractContextManager[frozenset[str]]:
        """Serialize reference protection with the bounded terminal retention."""
        ...


def _utc_now() -> str:
    """Return a timezone-qualified timestamp suitable for durable JSON."""
    return datetime.now(UTC).isoformat()


def _safe_name(value: str, *, field: str) -> str:
    """Validate a path-component identifier without rewriting caller intent."""
    if not value or value[0] in ".-" or any(char not in _NAME_CHARS for char in value):
        raise ValueError(f"{field} must be a safe non-hidden path component")
    return value


def _new_run_id() -> str:
    """Return a sortable, process-distinct run identifier."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _atomic_write_json(
    path: Path, payload: Mapping[str, object], *, exclusive: bool = False,
) -> None:
    """Fsync JSON beside ``path`` and atomically claim or replace the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _observer_metadata_lock(directory: Path) -> Iterator[None]:
    """Serialize current-pointer publication and pruning within one observed label."""
    from filelock import FileLock

    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".observer.lock"), timeout=2, mode=0o600):
        yield


class _StatusPublisher:
    """Publish both durable per-run state and the atomic current pointer."""

    def __init__(self, root: Path, label: str, run_id: str) -> None:
        self.directory = (root / label).resolve()
        self.run_path = self.directory / f"{run_id}.json"
        self.current_path = self.directory / "current.json"

    def publish(self, payload: Mapping[str, object]) -> None:
        """Write history before advancing the current-run pointer."""
        with _observer_metadata_lock(self.directory):
            _atomic_write_json(self.run_path, payload)
            _atomic_write_json(self.current_path, payload)


def _preserved_run_ids(
    admission: _ProgressAdmission | None, progress_root: Path | None,
    observed_root: Path, label: str,
) -> frozenset[str]:
    """Read the same native references before publication and before retention."""
    if admission is not None:
        return admission.preserved_run_ids(observed_root, label)
    if progress_root is None:
        return frozenset()
    if TYPE_CHECKING:
        from item_progress import preserved_run_ids
    else:
        from scripts.item_progress import preserved_run_ids
    return preserved_run_ids(progress_root, observed_root, label)


def _protected_run_ids(
    admission: _ProgressAdmission | None, progress_root: Path | None,
    observed_root: Path, label: str,
) -> AbstractContextManager[frozenset[str]]:
    """Reuse native progress serialization without holding it during child execution."""
    if admission is not None:
        return admission.protected_run_ids(observed_root, label)
    if progress_root is None:
        return nullcontext(frozenset())
    if TYPE_CHECKING:
        from item_progress import protected_run_ids
    else:
        from scripts.item_progress import protected_run_ids
    return protected_run_ids(progress_root, observed_root, label)


def _prune_terminal_runs(
    directory: Path, *, retain_runs: int, preserve_runs: frozenset[str] = frozenset(),
) -> None:
    """Prune only superseded terminal runs, never current or active evidence."""
    with _observer_metadata_lock(directory):
        _prune_terminal_runs_locked(directory, retain_runs=retain_runs, preserve_runs=preserve_runs)


def _prune_terminal_runs_locked(
    directory: Path, *, retain_runs: int, preserve_runs: frozenset[str],
) -> None:
    """Prune against one stable current pointer while the label metadata lock is held."""
    if retain_runs < 1:
        raise ValueError("retain_runs must be at least 1")
    terminal: list[tuple[datetime, str, Path, dict[str, Any]]] = []
    for status_path in directory.glob("*.json"):
        if status_path.name == "current.json" or status_path.is_symlink():
            continue
        try:
            value = json.loads(status_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(value, dict) or value.get("state") not in _TERMINAL_STATES:
            continue
        run_id = value.get("run_id")
        started_at = value.get("started_at")
        if (
            value.get("schema_version") != 1
            or value.get("kind") != "observed_command"
            or value.get("label") != directory.name
            or not isinstance(run_id, str)
            or status_path.name != f"{run_id}.json"
            or not isinstance(started_at, str)
        ):
            continue
        try:
            _safe_name(run_id, field="run_id")
            parsed_started_at = datetime.fromisoformat(started_at)
        except ValueError:
            continue
        if parsed_started_at.tzinfo is None:
            continue
        terminal.append((parsed_started_at, status_path.name, status_path, value))
    terminal.sort(reverse=True)
    resolved_directory = directory.resolve()
    current_run_id: str | None = None
    try:
        current = json.loads((directory / "current.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        current = None
    if (isinstance(current, dict) and current.get("schema_version") == 1
            and current.get("kind") == "observed_command" and current.get("label") == directory.name
            and isinstance(current.get("run_id"), str)):
        current_run_id = _safe_name(current["run_id"], field="run_id")
    for _started_at, _name, status_path, value in terminal[retain_runs:]:
        run_id = value.get("run_id")
        if (run_id in preserve_runs or run_id == current_run_id
                or ("progress_identity" in value and value.get("state") != "passed")):
            # A capacity pointer is not cleanup authority for native failure evidence.
            continue
        if not isinstance(run_id, str) or status_path.name != f"{run_id}.json":
            continue
        for field, suffix in (("log_path", ".log"), ("trace_path", ".pytest.jsonl")):
            raw_path = value.get(field)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            artifact = Path(raw_path).resolve()
            if artifact.parent == resolved_directory and artifact.name == f"{run_id}{suffix}":
                artifact.unlink(missing_ok=True)
        status_path.unlink(missing_ok=True)


def _terminate_process_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Terminate only the observed child's session, escalating after a grace."""
    import psutil

    owned: list[psutil.Process] = []
    for candidate in psutil.process_iter():
        with suppress(ProcessLookupError, psutil.NoSuchProcess):
            if (
                candidate.pid != process.pid
                and os.getpgid(candidate.pid) == process.pid
                and os.getsid(candidate.pid) == process.pid
            ):
                owned.append(candidate)
    deadline = time.monotonic() + grace_seconds
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace_seconds)
    # The leader exiting is not proof that its owned descendants have exited.
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    process.wait(timeout=max(grace_seconds, 1.0))
    psutil.wait_procs(owned, timeout=max(grace_seconds, 1.0))


def _write_chunk(chunk: bytes, log_file: BinaryIO, *, quiet: bool) -> None:
    """Flush one child-output chunk to its durable log and optional terminal."""
    log_file.write(chunk)
    log_file.flush()
    if not quiet:
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()


def _base_status(
    *,
    label: str,
    run_id: str,
    started_at: str,
    log_path: Path,
    trace_path: Path | None,
) -> dict[str, object]:
    """Build the stable observed-command status schema."""
    return {
        "schema_version": 1,
        "kind": "observed_command",
        "label": label,
        "run_id": run_id,
        "state": "starting",
        "owner_pid": os.getpid(),
        "child_pid": None,
        "started_at": started_at,
        "updated_at": started_at,
        "heartbeat_seq": 0,
        "elapsed_seconds": 0.0,
        "last_output_at": started_at,
        "quiet_seconds": 0.0,
        "bytes_written": 0,
        "lines_written": 0,
        "exit_code": None,
        "termination_reason": None,
        "log_path": str(log_path),
        "trace_path": str(trace_path) if trace_path is not None else None,
    }


def _publish_or_fail(
    publisher: _StatusPublisher | None, status: Mapping[str, object]
) -> bool:
    """Publish status, reporting an observer-owned I/O failure to stderr."""
    if publisher is None:
        return True
    try:
        publisher.publish(status)
    except OSError as exc:
        print(f"observed-command: status write failed: {exc}", file=sys.stderr, flush=True)
        return False
    return True


def stream_command(
    command: Sequence[str],
    log_path: Path,
    *,
    observed_root: Path | None = None,
    label: str | None = None,
    run_id: str | None = None,
    heartbeat_seconds: float = 0.0,
    quiet_seconds: float = 0.0,
    max_seconds: float = 0.0,
    quiet: bool = False,
    pytest_trace: bool = False,
    retain_runs: int = DEFAULT_RETAIN_RUNS,
    termination_grace_seconds: float = 0.5,
    progress_admission: _ProgressAdmission | None = None,
    progress_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    startup_metadata: Mapping[str, object] | None = None,
) -> int:
    """Run ``command`` with live output, bounded waits, and atomic status.

    When ``label`` is omitted, this retains the original streaming-log contract.
    Observed mode publishes ``current.json`` plus a run-specific status file and
    returns the child exit code. Observer I/O failures return 125; max-runtime
    and quiet-output timeouts return 124; received signals return 128+signal.
    """
    if not command:
        raise ValueError("command must not be empty")
    if heartbeat_seconds < 0 or quiet_seconds < 0 or max_seconds < 0:
        raise ValueError("observer durations must be non-negative")
    if termination_grace_seconds <= 0:
        raise ValueError("termination grace must be positive")
    if retain_runs < 1:
        raise ValueError("retain_runs must be at least 1")
    if progress_admission is not None and label is None:
        raise ValueError("item admission requires native observed mode")
    child_environment = dict(os.environ if environment is None else environment)
    if progress_admission is not None:
        progress_admission.validate_execution(
            command, environment=child_environment, cwd=cwd,
            quiet_seconds=quiet_seconds, max_seconds=max_seconds,
        )

    publisher: _StatusPublisher | None = None
    status: dict[str, object] | None = None
    trace_path: Path | None = None
    effective_run_id = run_id
    effective_label = label
    started_wall = _utc_now()
    started_mono = time.monotonic()
    resolved_log = log_path.resolve()

    if effective_label is not None:
        if observed_root is None:
            raise ValueError("observed_root is required with label")
        effective_label = _safe_name(effective_label, field="label")
        effective_run_id = _safe_name(
            effective_run_id or _new_run_id(), field="run_id"
        )
        publisher = _StatusPublisher(
            observed_root.resolve(), effective_label, effective_run_id
        )
        if pytest_trace:
            trace_path = publisher.directory / f"{effective_run_id}.pytest.jsonl"
        status = _base_status(
            label=effective_label,
            run_id=effective_run_id,
            started_at=started_wall,
            log_path=resolved_log,
            trace_path=trace_path,
        )
        if startup_metadata is not None:
            status["startup_diagnostic"] = dict(startup_metadata)
        if progress_admission is not None:
            status["progress_identity"] = progress_admission.identity
        try:
            preserved = _preserved_run_ids(
                progress_admission, progress_root, observed_root, effective_label,
            )
            artifacts = (
                publisher.run_path, publisher.directory / f"{effective_run_id}.log",
                publisher.directory / f"{effective_run_id}.pytest.jsonl",
            )
            existing_log = any(
                os.path.lexists(path) and (path.is_symlink() or not path.is_dir())
                for path in (log_path.absolute(), resolved_log)
            )
            if (effective_run_id == "current" or effective_run_id in preserved
                    or any(os.path.lexists(path) for path in artifacts) or existing_log):
                raise FileExistsError(f"observed run identity is already reserved: {effective_run_id}")
            # Link a fully fsynced receipt without replacing an existing name.
            # Concurrent starters can claim the same identity only once.
            _atomic_write_json(publisher.run_path, status, exclusive=True)
        except (OSError, ValueError) as exc:
            print(f"observed-command: starting identity refused: {exc}", file=sys.stderr, flush=True)
            return OBSERVER_FAILURE
        if not _publish_or_fail(publisher, status):
            return OBSERVER_FAILURE
        if progress_admission is not None:
            progress_admission.record(observed_root.resolve(), effective_label, effective_run_id)
        print(
            f"[observed {effective_label}] start run_id={effective_run_id} "
            f"log={resolved_log}",
            flush=True,
        )

    try:
        resolved_log.parent.mkdir(parents=True, exist_ok=True)
        log_file = resolved_log.open("xb" if publisher is not None else "wb")
    except OSError as exc:
        if status is not None:
            status.update(
                {
                    "state": "failed",
                    "updated_at": _utc_now(),
                    "elapsed_seconds": round(time.monotonic() - started_mono, 3),
                    "exit_code": OBSERVER_FAILURE,
                    "termination_reason": "observer-log-open-failed",
                }
            )
            _publish_or_fail(publisher, status)
        print(f"observed-command: log open failed: {exc}", file=sys.stderr, flush=True)
        return OBSERVER_FAILURE

    if trace_path is not None and effective_run_id is not None:
        try:
            with trace_path.open("xb"):
                pass
        except OSError as exc:
            log_file.close()
            assert status is not None
            status.update(
                state="failed", updated_at=_utc_now(), exit_code=OBSERVER_FAILURE,
                elapsed_seconds=round(time.monotonic() - started_mono, 3),
                termination_reason="observer-trace-open-failed",
            )
            _publish_or_fail(publisher, status)
            print(f"observed-command: trace allocation failed: {exc}", file=sys.stderr, flush=True)
            return OBSERVER_FAILURE
        child_environment["GLUDD_XDIST_TRACE_LOG"] = str(trace_path)
        child_environment["GLUDD_XDIST_TRACE_RUN_ID"] = effective_run_id
        child_environment["GLUDD_XDIST_TRACE_TRUNCATE"] = "0"

    child_cwd: _ChildCwdOptions = {"cwd": cwd} if cwd is not None else {}
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env=child_environment,
            **child_cwd,
        )
    except OSError as exc:
        log_file.close()
        if status is not None:
            status.update(
                {
                    "state": "failed",
                    "updated_at": _utc_now(),
                    "elapsed_seconds": round(time.monotonic() - started_mono, 3),
                    "exit_code": OBSERVER_FAILURE,
                    "termination_reason": "observer-child-start-failed",
                }
            )
            _publish_or_fail(publisher, status)
        print(f"observed-command: child start failed: {exc}", file=sys.stderr, flush=True)
        return OBSERVER_FAILURE

    if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
        _terminate_process_group(process, termination_grace_seconds)
        log_file.close()
        raise RuntimeError("streamed command did not expose stdout")

    pending_signal: list[int] = []

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        if not pending_signal:
            pending_signal.append(signum)

    previous_handlers: dict[signal.Signals, Any] = {}
    for watched_signal in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[watched_signal] = signal.getsignal(watched_signal)
        signal.signal(watched_signal, handle_signal)

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    last_output_mono = started_mono
    last_output_wall = started_wall
    bytes_written = 0
    lines_written = 0
    heartbeat_seq = 0
    next_heartbeat = (
        started_mono + heartbeat_seconds if heartbeat_seconds > 0 else float("inf")
    )
    requested_code: int | None = None
    termination_reason: str | None = None
    state: str | None = None

    try:
        # A published running owner must already be able to handle interruption.
        if status is not None:
            status.update(
                {"state": "running", "child_pid": process.pid, "updated_at": _utc_now()}
            )
            if not _publish_or_fail(publisher, status):
                _terminate_process_group(process, termination_grace_seconds)
                process.stdout.close()
                log_file.close()
                return OBSERVER_FAILURE

        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if selector.get_map():
                ready = selector.select(timeout=0.05)
            else:
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.05)
                ready = []
            for key, _mask in ready:
                try:
                    chunk = os.read(key.fd, 64 * 1024)
                except OSError as exc:
                    print(
                        f"observed-command: output read failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    requested_code = OBSERVER_FAILURE
                    termination_reason = "observer-output-read-failed"
                    state = "failed"
                    break
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                try:
                    _write_chunk(chunk, log_file, quiet=quiet)
                except OSError as exc:
                    print(
                        f"observed-command: output write failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    requested_code = OBSERVER_FAILURE
                    termination_reason = "observer-output-write-failed"
                    state = "failed"
                    break
                bytes_written += len(chunk)
                lines_written += chunk.count(b"\n")
                last_output_mono = time.monotonic()
                last_output_wall = _utc_now()

            now = time.monotonic()
            elapsed = now - started_mono
            quiet_for = now - last_output_mono

            if pending_signal:
                signum = pending_signal[0]
                requested_code = 128 + signum
                try:
                    signal_name = signal.Signals(signum).name
                except ValueError:  # pragma: no cover - OS supplied value
                    signal_name = str(signum)
                termination_reason = f"observer-signal:{signal_name}"
                state = "interrupted"
            elif max_seconds > 0 and elapsed >= max_seconds:
                requested_code = TIMEOUT_FAILURE
                termination_reason = "max-runtime-timeout"
                state = "timed_out"
            elif quiet_seconds > 0 and quiet_for >= quiet_seconds:
                requested_code = TIMEOUT_FAILURE
                termination_reason = "quiet-output-timeout"
                state = "timed_out"

            if requested_code is not None:
                _terminate_process_group(process, termination_grace_seconds)
                break

            if now >= next_heartbeat:
                heartbeat_seq += 1
                if status is not None:
                    status.update(
                        {
                            "updated_at": _utc_now(),
                            "heartbeat_seq": heartbeat_seq,
                            "elapsed_seconds": round(elapsed, 3),
                            "last_output_at": last_output_wall,
                            "quiet_seconds": round(quiet_for, 3),
                            "bytes_written": bytes_written,
                            "lines_written": lines_written,
                        }
                    )
                    if not _publish_or_fail(publisher, status):
                        requested_code = OBSERVER_FAILURE
                        termination_reason = "observer-status-write-failed"
                        state = "failed"
                        _terminate_process_group(process, termination_grace_seconds)
                        break
                if effective_label is not None:
                    print(
                        f"[observed {effective_label}] heartbeat "
                        f"elapsed={elapsed:.1f}s quiet={quiet_for:.1f}s "
                        f"lines={lines_written} bytes={bytes_written}",
                        flush=True,
                    )
                next_heartbeat = now + heartbeat_seconds

            if process.poll() is not None and not selector.get_map():
                break
    finally:
        selector.close()
        for watched_signal, previous_handler in previous_handlers.items():
            signal.signal(watched_signal, previous_handler)

    if process.poll() is None:
        _terminate_process_group(process, termination_grace_seconds)
    child_returncode = process.wait()
    process.stdout.close()
    try:
        log_file.flush()
        os.fsync(log_file.fileno())
    except OSError as exc:
        print(f"observed-command: log sync failed: {exc}", file=sys.stderr, flush=True)
        requested_code = OBSERVER_FAILURE
        termination_reason = "observer-log-sync-failed"
        state = "failed"
    finally:
        log_file.close()

    if requested_code is None:
        if child_returncode < 0:
            signum = -child_returncode
            requested_code = 128 + signum
            try:
                signal_name = signal.Signals(signum).name
            except ValueError:  # pragma: no cover - OS supplied value
                signal_name = str(signum)
            termination_reason = f"child-signal:{signal_name}"
            state = "interrupted"
        else:
            requested_code = child_returncode
            state = "passed" if child_returncode == 0 else "failed"

    final_now = time.monotonic()
    if status is not None:
        status.update(
            {
                "state": state,
                "updated_at": _utc_now(),
                "heartbeat_seq": heartbeat_seq,
                "elapsed_seconds": round(final_now - started_mono, 3),
                "last_output_at": last_output_wall,
                "quiet_seconds": round(final_now - last_output_mono, 3),
                "bytes_written": bytes_written,
                "lines_written": lines_written,
                "exit_code": requested_code,
                "termination_reason": termination_reason,
            }
        )
        if not _publish_or_fail(publisher, status):
            return OBSERVER_FAILURE
        assert publisher is not None
        try:
            assert observed_root is not None and effective_label is not None
            with _protected_run_ids(
                progress_admission, progress_root, observed_root, effective_label,
            ) as preserved:
                if progress_admission is None and progress_root is None:
                    _prune_terminal_runs(publisher.directory, retain_runs=retain_runs)
                else:
                    _prune_terminal_runs(
                        publisher.directory, retain_runs=retain_runs,
                        preserve_runs=preserved,
                    )
        except (OSError, ValueError) as exc:
            requested_code = OBSERVER_FAILURE
            state = "failed"
            status.update(
                {
                    "state": state,
                    "updated_at": _utc_now(),
                    "exit_code": requested_code,
                    "termination_reason": "observer-retention-failed",
                }
            )
            if not _publish_or_fail(publisher, status):
                return OBSERVER_FAILURE
            print(
                f"observed-command: retention cleanup failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
        print(
            f"[observed {effective_label}] result state={state} rc={requested_code} "
            f"elapsed={final_now - started_mono:.1f}s",
            flush=True,
        )
    return requested_code


def _load_status(
    root: Path, label: str, *, run_id: str | None = None
) -> dict[str, Any]:
    """Load current status or one immutable retained run, failing closed."""
    safe_label = _safe_name(label, field="label")
    safe_run_id = _safe_name(run_id, field="run_id") if run_id is not None else None
    directory = root.resolve() / safe_label
    current_alias = safe_run_id == "current"
    path = directory / (
        "current.json"
        if safe_run_id is None or current_alias
        else f"{safe_run_id}.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"observed status is not a JSON object: {path}")
    expected_run_id = safe_run_id
    if current_alias:
        payload_run_id = value.get("run_id")
        if not isinstance(payload_run_id, str):
            raise ValueError(f"current observed status has no string run_id: {path}")
        expected_run_id = _safe_name(payload_run_id, field="run_id")
    if expected_run_id is not None and (
        value.get("schema_version") != 1
        or value.get("kind") != "observed_command"
        or value.get("label") != safe_label
        or value.get("run_id") != expected_run_id
    ):
        qualifier = "current" if current_alias else "retained"
        raise ValueError(f"{qualifier} observed status identity mismatch: {path}")
    return value


def _load_current(root: Path, label: str) -> dict[str, Any]:
    """Load one label's atomic current pointer for compatibility."""
    return _load_status(root, label)


def _pid_alive(value: object) -> bool:
    """Return whether an integer PID still names a visible process."""
    if not isinstance(value, int) or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _age_seconds(value: object) -> float:
    """Return wall-clock age for an ISO timestamp, or infinity if invalid."""
    if not isinstance(value, str):
        return float("inf")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return float("inf")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - timestamp).total_seconds())


def observed_status(
    root: Path,
    label: str,
    *,
    stale_seconds: float,
    run_id: str | None = None,
) -> int:
    """Print current JSON with inferred live/stale/orphaned state."""
    status = _load_status(root, label, run_id=run_id)
    recorded_state = str(status.get("state") or "unknown")
    in_progress = recorded_state in {"starting", "running"}
    owner_alive = _pid_alive(status.get("owner_pid")) if in_progress else False
    age = _age_seconds(status.get("updated_at"))
    stale = in_progress and age > stale_seconds
    if in_progress and not owner_alive:
        effective_state = "orphaned"
    elif stale:
        effective_state = "stale"
    else:
        effective_state = recorded_state
    output = dict(status)
    output.update(
        {
            "active": bool(in_progress and owner_alive and not stale),
            "effective_state": effective_state,
            "stale": stale,
            "status_age_seconds": round(age, 3),
        }
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 3 if effective_state in {"orphaned", "stale"} else 0


def observed_tail(
    root: Path, label: str, *, lines: int, run_id: str | None = None
) -> int:
    """Print at most ``lines`` from the current run's durable output log."""
    if lines <= 0 or lines > 1000:
        raise ValueError("tail lines must be between 1 and 1000")
    status = _load_status(root, label, run_id=run_id)
    raw_path = status.get("log_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("observed status does not contain log_path")
    log_path = Path(raw_path).resolve()
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        snapshot = deque(handle, maxlen=lines)
    sys.stdout.writelines(snapshot)
    return 0


# Startup diagnostics reuse this observer. Readers never execute or refresh a run.
_STARTUP_LIMIT = 16 * 1024 * 1024
_STARTUP_COMMAND = ("make", "--no-print-directory", "_pytest-runtime-compatibility-admission")


class _StartupFile(NamedTuple):
    """One bounded byte snapshot, with its original path retained separately."""

    role: str
    path: Path
    data: bytes

    @property
    def digest(self) -> str:
        """Hash the bytes actually parsed/copied, never a later path read."""
        import hashlib

        return hashlib.sha256(self.data).hexdigest()


def _startup_no_links(path: Path, boundary: Path) -> None:
    relative = path.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("startup evidence must not contain symbolic links")


def _startup_read(role: str, path: Path, boundary: Path) -> _StartupFile:
    import stat

    _startup_no_links(path, boundary)
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _STARTUP_LIMIT:
            raise ValueError("startup evidence must be a bounded regular file")
        data = handle.read(_STARTUP_LIMIT + 1)
        if len(data) > _STARTUP_LIMIT or len(data) != info.st_size:
            raise ValueError("startup evidence changed or exceeded its bound")
    _startup_no_links(path, boundary)
    return _StartupFile(role, path, data)


def _startup_fresh(files: Sequence[_StartupFile]) -> None:
    for item in files:
        # Every component is checked again; an absent/changed input never publishes.
        if item.path.resolve() != item.path or item.path.is_symlink():
            raise ValueError("startup evidence changed")
        try:
            current = _startup_read(item.role, item.path, Path(item.path.anchor))
        except OSError as exc:
            raise ValueError("startup evidence changed") from exc
        if current.data != item.data:
            raise ValueError("startup evidence changed")


def _startup_external(repository: Path, path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    canonical = absolute.resolve()
    bases = (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve(), (Path.home() / "tmp").resolve())
    if not any(canonical.is_relative_to(base) and canonical != base for base in bases):
        raise ValueError("startup output must be in an external temporary namespace")
    if canonical.is_relative_to(repository.resolve()):
        raise ValueError("startup output must be outside the checkout")
    # /tmp is the platform-owned alias on macOS; other caller links are refused.
    for parent in (absolute, *absolute.parents):
        if parent.is_symlink() and parent != Path("/tmp"):
            raise ValueError("startup output must not contain symbolic links")
        if (parent / ".git").exists():
            raise ValueError("startup output must not be inside another checkout")
    return canonical


def _startup_identity(label: str, run_id: str, source_sha: str) -> None:
    import re

    _safe_name(label, field="label")
    _safe_name(run_id, field="run_id")
    if run_id == "current":
        raise ValueError("an exact retained run is required, never current")
    if source_sha and re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ValueError("source SHA must be a full lowercase commit identity")


def _startup_inputs(
    repository: Path, root: Path, label: str, run_id: str,
    console_paths: Sequence[Path],
) -> tuple[dict[str, Any], list[_StartupFile]]:
    repository = repository.resolve()
    expected = repository / DEFAULT_OBSERVED_ROOT
    if root.resolve() != expected:
        raise ValueError("startup reader requires the exact checkout producer namespace")
    _startup_no_links(expected, repository)
    status_file = _startup_read("status", expected / label / f"{run_id}.json", repository)
    status = _load_status(expected, label, run_id=run_id)
    if json.loads(status_file.data) != status:
        raise ValueError("startup status changed during identity validation")
    if status.get("state") not in _TERMINAL_STATES:
        raise ValueError("startup readers require a terminal retained run")
    files = [status_file]
    for role, key in (("log", "log_path"), ("trace", "trace_path")):
        value = status.get(key)
        if value is None and role == "trace":
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"startup status lacks {role} path")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("startup evidence path must be absolute")
        for parent in (path, *path.parents):
            if parent.is_symlink() and parent != Path("/tmp"):
                raise ValueError("startup evidence must not contain symbolic links")
        path = path.resolve()
        if not path.is_relative_to(repository / ".gate-logs"):
            raise ValueError("startup evidence path is outside the producer namespace")
        files.append(_startup_read(role, path, repository))
    log = next(item for item in files if item.role == "log")
    if (status.get("bytes_written") != len(log.data)
            or status.get("lines_written") != len(log.data.splitlines())):
        raise ValueError("startup log size/count differs from its terminal status")
    if len(console_paths) > 8 or len(set(console_paths)) != len(console_paths):
        raise ValueError("at most eight distinct external console parts are accepted")
    for index, console in enumerate(console_paths, 1):
        path = _startup_external(repository, console)
        files.append(_startup_read(f"console-{index}", path, Path(path.anchor)))
    _startup_fresh(files)
    return status, files


def _startup_imports(data: bytes) -> list[dict[str, Any]]:
    """Parse three sequential visible import/session/collection segments only."""
    import re

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    stage = ""
    for line in data.decode("utf-8", errors="strict").splitlines():
        if line.startswith("import time: self [us]"):
            if current is not None and stage != "complete":
                raise ValueError("interleaved or truncated import segment")
            current = {"rows": [], "platform": "", "count": 0, "seconds": ""}
            segments.append(current)
            stage = "imports"
        elif line.startswith("import time:"):
            if current is None or stage != "imports":
                raise ValueError("import row outside its visible pre-header segment")
            row = re.fullmatch(r"import time:\s*([0-9]+)\s*\|\s*([0-9]+)\s*\|\s*(\S.*)", line)
            if row is None or int(row[2]) < int(row[1]):
                raise ValueError("malformed importtime self/cumulative columns")
            current["rows"].append((int(row[1]), int(row[2]), row[3].strip()))
        elif "test session starts" in line:
            if current is None or stage != "imports" or not current["rows"]:
                raise ValueError("missing or interleaved session imports")
            stage = "session"
        elif line.startswith("platform "):
            if current is None or stage != "session" or current["platform"]:
                raise ValueError("missing or interleaved platform header")
            current["platform"] = line
        elif re.search(r"=+ .* tests? collected in ", line):
            if current is None or stage != "session" or not current["platform"]:
                raise ValueError("collection summary without its platform/session")
            summary = re.fullmatch(r"=+ ([0-9]+) tests? collected in ([0-9]+\.[0-9]+)s =+", line)
            if summary is None:
                raise ValueError("malformed collection summary")
            current.update(count=int(summary[1]), seconds=summary[2])
            stage = "complete"
    if len(segments) != 3 or stage != "complete":
        raise ValueError("expected exactly three complete cold collection segments")
    if len({segment["platform"] for segment in segments}) != 1:
        raise ValueError("platform differs across cold segments")
    return segments


def _startup_render(
    status: Mapping[str, Any], files: Sequence[_StartupFile], source_sha: str,
) -> str:
    import hashlib

    log = next(item for item in files if item.role == "log")
    tool_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    lines = [
        "# Retained Python startup diagnostic", "",
        "Diagnostic only; never admission or release evidence.", "",
        f"Exact observer: {status['label']} / {status['run_id']}.",
        f"state: {status['state']}; rc: {status.get('exit_code')}; "
        f"observer elapsed: {status.get('elapsed_seconds')} seconds.",
        f"Declared observed source: {source_sha or 'unknown'}; source binding: declared "
        "(historical status may not authenticate this declaration).",
        f"Analyst tool SHA-256: {tool_digest} (analysis code, not observed source).",
        "command argv: " + ("recorded" if status.get("startup_diagnostic") else "absent"),
        "trace: " + ("present" if any(item.role == "trace" for item in files) else "absent"),
        "parent console: " + ("external tool capture; separate from child log"
                             if any(item.role.startswith("console-") for item in files) else "absent"),
        "first-header latency: unknown (no per-line timestamps).", "",
        "File-collection import attribution is unobserved/capture-limited: pytest captures "
        "stderr during successful file collection. Hidden work is unknown, never zero.",
        "Visible self sums cover only validated visible import rows. They are not startup, "
        "CPU, or collection totals. Cumulative rows overlap and are never summed; "
        "cold segments are separate. No subtraction from observer/pytest durations "
        "can establish a missing interval.", "",
    ]
    try:
        segments = _startup_imports(log.data)
    except (ValueError, UnicodeError) as exc:
        lines.append(f"Import analysis incomplete: {exc}")
    else:
        for index, segment in enumerate(segments, 1):
            rows = segment["rows"]
            lines.extend([
                f"## Visible segment {index}", "",
                segment["platform"],
                f"{segment['count']} collected / {segment['seconds']} seconds.",
                f"Visible self: {sum(row[0] for row in rows)} microseconds.",
                "Largest individual cumulative rows (overlapping; not a sum):",
            ])
            for self_us, cumulative_us, name in sorted(rows, key=lambda row: row[1], reverse=True)[:10]:
                lines.append(f"- {name}: self {self_us}, cumulative {cumulative_us} microseconds")
            lines.extend(["", "Largest individual self rows:", ""])
            for self_us, cumulative_us, name in sorted(rows, key=lambda row: row[0], reverse=True)[:10]:
                lines.append(f"- {name}: self {self_us}, cumulative {cumulative_us} microseconds")
            lines.append("")
    lines.extend(["", "Raw input SHA-256 (bytes actually read):", ""])
    lines.extend(f"- {item.role}: {item.digest} ({len(item.data)} bytes)" for item in files)
    return "\n".join(lines) + "\n"


def _startup_replay(archive: Path, label: str, run_id: str) -> str:
    archive = archive.resolve()
    raw = _startup_read("manifest", archive / "manifest.json", archive)
    manifest = json.loads(raw.data)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("kind") != "runtime_startup_archive"
            or manifest.get("label") != label or manifest.get("run_id") != run_id
            or manifest.get("diagnostic_only") is not True):
        raise ValueError("startup archive identity mismatch")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 3 <= len(entries) <= 12:
        raise ValueError("startup archive file list must be bounded")
    files: dict[str, _StartupFile] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("malformed startup archive file")
        role, relative = entry.get("role"), entry.get("path")
        if not isinstance(role, str) or role in files or not isinstance(relative, str):
            raise ValueError("duplicate/malformed startup archive role")
        path = archive / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("startup archive path escape")
        item = _startup_read(role, path, archive)
        if item.digest != entry.get("sha256") or len(item.data) != entry.get("size"):
            raise ValueError("startup archive hash/size changed")
        files[role] = item
    if not {"status", "log", "report"}.issubset(files):
        raise ValueError("startup archive lacks required roles")
    status = _load_status(archive / "status", label, run_id=run_id)
    if json.loads(files["status"].data) != status:
        raise ValueError("startup archive status role mismatch")
    _startup_fresh([raw, *files.values()])
    return files["report"].data.decode("utf-8")


def runtime_startup_report(
    repository: Path, root: Path, label: str, run_id: str, *,
    source_sha: str = "", archive: Path | None = None, console_paths: Sequence[Path] = (),
) -> str:
    """Derive a read-only report from one exact run or checked archived bytes."""
    _startup_identity(label, run_id, source_sha)
    if archive is not None:
        return _startup_replay(_startup_external(repository, archive), label, run_id)
    status, files = _startup_inputs(repository, root, label, run_id, console_paths)
    report = _startup_render(status, files, source_sha)
    _startup_fresh(files)
    return report


def runtime_startup_archive(
    repository: Path, root: Path, label: str, run_id: str, *,
    destination: Path, source_sha: str = "", validate_only: bool = True,
    console_paths: Sequence[Path] = (),
) -> dict[str, object]:
    """Plan/copy immutable raw snapshots; publish an exclusive manifest last."""
    import hashlib

    _startup_identity(label, run_id, source_sha)
    destination = _startup_external(repository, destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("startup archive destination already exists")
    status, files = _startup_inputs(repository, root, label, run_id, console_paths)
    report = _startup_render(status, files, source_sha).encode("utf-8")
    result: dict[str, object] = {
        "validate_only": validate_only, "destination": str(destination),
        "label": label, "run_id": run_id, "diagnostic_only": True,
    }
    if validate_only:
        return result
    _startup_fresh(files)
    destination.mkdir()
    entries = []
    for item in [*files, _StartupFile("report", Path("report.md"), report)]:
        relative = (f"status/{label}/{run_id}.json" if item.role == "status"
                    else "child.log" if item.role == "log"
                    else "report.md" if item.role == "report"
                    else f"{item.role}.raw")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(item.data)
            handle.flush()
            os.fsync(handle.fileno())
        entries.append({
            "role": item.role, "path": relative, "original_path": str(item.path),
            "sha256": item.digest, "size": len(item.data),
        })
    _startup_fresh(files)
    _atomic_write_json(destination / "manifest.json", {
        "schema_version": 1, "kind": "runtime_startup_archive",
        "label": label, "run_id": run_id, "diagnostic_only": True,
        "declared_source": source_sha or None,
        "analyst_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_binding": "declaration only unless recorded by original producer",
        "missing_trace": not any(item.role == "trace" for item in files),
        "missing_console": not any(item.role.startswith("console-") for item in files),
        "files": entries,
    }, exclusive=True)
    return result


def _startup_capacity(repository: Path, environment: Mapping[str, str]) -> None:
    """Use the existing native admission owners, never a parallel scanner."""
    for target, variables in (
        ("ps", ()), ("active-work-status", ()), ("check-system-load", ()),
        ("disk-guard", (
            "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0", "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0",
            "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
        )),
    ):
        completed = subprocess.run(
            ["make", "--no-print-directory", target, *variables],
            cwd=repository, env=dict(environment), check=True, timeout=120,
            capture_output=True, text=True,
        )
        print(completed.stdout, end="", flush=True)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr, flush=True)
        if target == "active-work-status":
            value = json.loads(completed.stdout)
            if not isinstance(value, dict):
                raise ValueError("native startup capacity is malformed")
            resources = value.get("resource_observability", {})
            if not isinstance(resources, dict):
                raise ValueError("native startup capacity is malformed")
            lease = resources.get("collection_lease", {})
            if not isinstance(lease, dict):
                raise ValueError("native startup collection lease is malformed")
            if (resources.get("worker_count") != 0 or resources.get("leased_worker_count") != 0
                    or lease.get("state") != "available"
                    or value.get("gate", {}).get("state") == "RUNNING"):
                raise ValueError("native startup capacity is busy or unknown")
        if target == "check-system-load" and not any(
            line.split() == ["Verdict", ":", verdict]
            for line in completed.stdout.splitlines() for verdict in ("OK", "WARN")
        ):
            raise ValueError("native startup load is critical or unknown")


def _startup_runtime(
    repository: Path, environment: Path | None, python: str, profile_set: str,
) -> dict[str, object]:
    if TYPE_CHECKING:
        from dependency_profiles import ProfileError, inspect_runtime, load_catalog
    else:
        from scripts.dependency_profiles import ProfileError, inspect_runtime, load_catalog
    try:
        return inspect_runtime(
            load_catalog(repository), profile_set=profile_set, environment=environment, python=python,
        )
    except ProfileError as exc:
        raise ValueError(f"startup runtime inspection refused: {exc}") from exc


def _startup_source(repository: Path) -> tuple[str, str]:
    if TYPE_CHECKING:
        from reviewed_head_inputs import _clean_identity
    else:
        from scripts.reviewed_head_inputs import _clean_identity
    return _clean_identity(repository)


def runtime_startup_diagnostic(
    repository: Path, root: Path, label: str, run_id: str, *,
    source_sha: str, environment: Path | None, python: str, profile_set: str,
    log_path: Path, validate_only: bool = True, heartbeat: float = 10,
    retain: int = 20, task: str = "",
) -> dict[str, Any]:
    """Plan one fixed collection diagnostic, or use the existing observer once."""
    _startup_identity(label, run_id, source_sha)
    if not source_sha or python != "3.12.15" or profile_set != "ci":
        raise ValueError("startup diagnostic requires exact source and hosted ci Python 3.12.15")
    if not 0 < heartbeat <= 60 or not 1 <= retain <= 100:
        raise ValueError("startup heartbeat/retention is outside native bounded policy")
    repository = repository.resolve()
    if root.resolve() != repository / DEFAULT_OBSERVED_ROOT:
        raise ValueError("startup observer root must be the native checkout namespace")
    if not log_path.is_absolute():
        log_path = repository / log_path
    log_path = Path(os.path.abspath(log_path))
    if not log_path.is_relative_to(repository / ".gate-logs"):
        raise ValueError("startup log must remain in the native checkout namespace")
    fixed = {
        "GATE_TEST_PROFILE": "release", "GATE_CANDIDATE_SHA": source_sha,
        "GATE_EXACT_SHA_RESUME": "0", "GATE_MAX_FILES_PER_BATCH": "16",
        "GATE_BATCH_WORKERS": "1", "NPROC": "1", "GLUDD_XDIST_WORKERS": "1",
        "PYTEST_ADDOPTS": "--collect-only -rs", "PYTHONPROFILEIMPORTTIME": "1",
        "UV_NO_SYNC": "1", "UV_PYTHON": python, "UV_PYTHON_DOWNLOADS": "never",
        "UV_PYTHON_PREFERENCE": "only-managed",
    }
    result: dict[str, Any] = {
        "validate_only": validate_only, "diagnostic_only": True, "command": list(_STARTUP_COMMAND),
        "quiet_seconds": 60, "max_seconds": 90, "environment": fixed,
        "label": label, "run_id": run_id, "source_sha": source_sha,
        "log_path": str(log_path),
    }
    if validate_only:
        return result
    _startup_no_links(log_path, repository)
    _startup_no_links(root.resolve(), repository)
    directory = root.resolve() / label
    if any(path.exists() or path.is_symlink() for path in (
        log_path, directory / f"{run_id}.json", directory / f"{run_id}.pytest.jsonl",
    )):
        raise ValueError("startup identity already reserved; use report/archive, never rerun")
    before = _startup_source(repository)
    if before[0] != source_sha:
        raise ValueError("startup source does not match the exact clean candidate")
    if TYPE_CHECKING:
        from run_ci_shards_serial import _owned_test_environment
    else:
        from scripts.run_ci_shards_serial import _owned_test_environment
    bootstrap_environment = _owned_test_environment(dict(os.environ))
    _startup_capacity(repository, bootstrap_environment)
    child_environment = {**bootstrap_environment, **fixed}
    inspection = _startup_runtime(repository, environment, python, profile_set)
    if inspection.get("state") != "verified":
        raise ValueError("startup runtime lacks positive current ci inspection")
    metadata = inspection.get("runtime")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("prefix"), str):
        raise ValueError("startup inspection lacks actual runtime prefix")
    child_environment["UV_PROJECT_ENVIRONMENT"] = metadata["prefix"]
    if _startup_source(repository) != before:
        raise ValueError("startup source changed during prerequisite inspection")
    if TYPE_CHECKING:
        from item_progress import command_admission, progress_status
    else:
        from scripts.item_progress import command_admission, progress_status
    context: AbstractContextManager[_ProgressAdmission | None] = nullcontext(None)
    progress_root: Path | None = None
    if progress_status(repository) is not None:
        progress_root = repository
        context = command_admission(
            repository, _STARTUP_COMMAND, environment=child_environment,
            quiet_seconds=60, max_seconds=90, task_id=task or None,
        )
    with context as admission:
        code = stream_command(
            _STARTUP_COMMAND, log_path, observed_root=root, label=label, run_id=run_id,
            heartbeat_seconds=heartbeat, quiet_seconds=60, max_seconds=90, retain_runs=retain,
            environment=child_environment, cwd=repository, progress_admission=admission,
            progress_root=progress_root,
            startup_metadata={
                "source_before": source_sha, "tree_before": before[1],
                "command": list(_STARTUP_COMMAND), "environment": fixed,
                "runtime_inspection": inspection,
                "source_after": "not recorded in observer; checked after return",
            },
        )
    result["exit_code"] = code
    after = _startup_source(repository)
    result["source_after"] = after[0]
    if after != before:
        raise ValueError("startup source changed; diagnostic has no stable source binding")
    return result


def _runtime_startup_cli(action: str) -> int:
    """Literal Make carriers are data; this surface accepts no arbitrary command."""
    def value(name: str, default: str = "") -> str:
        return os.environ.get("GLUDD_RUNTIME_DIAGNOSTIC_" + name, "") or default

    repository = Path.cwd()
    root = Path(value("ROOT", ".gate-logs/observed"))
    if not root.is_absolute():
        root = repository / root
    label, run_id = value("LABEL"), value("RUN_ID")
    source = value("SOURCE_SHA")
    raw_console = json.loads(value("CONSOLE", "[]"))
    if not isinstance(raw_console, list) or any(not isinstance(path, str) for path in raw_console):
        raise ValueError("console must be a JSON list of explicit external capture paths")
    console = [Path(path) for path in raw_console]
    if action == "report":
        report = runtime_startup_report(
            repository, root, label, run_id, source_sha=source, console_paths=console,
            archive=Path(value("ARCHIVE")) if value("ARCHIVE") else None,
        )
        if value("REPORT"):
            output = _startup_external(repository, Path(value("REPORT")))
            with output.open("x", encoding="utf-8") as handle:
                handle.write(report)
        else:
            print(report, end="")
        return 0
    policy = value("VALIDATE_ONLY", "1")
    if policy not in {"0", "1"}:
        raise ValueError("RUNTIME_DIAGNOSTIC_VALIDATE_ONLY must be 0 or 1")
    if action == "archive":
        if not value("ARCHIVE"):
            raise ValueError("an explicit external archive destination is required")
        result = runtime_startup_archive(
            repository, root, label, run_id, source_sha=source, console_paths=console,
            destination=Path(value("ARCHIVE")), validate_only=policy == "1",
        )
    else:
        result = runtime_startup_diagnostic(
            repository, root, label, run_id, source_sha=source,
            environment=Path(value("ENVIRONMENT")) if value("ENVIRONMENT") else None,
            python=value("PYTHON", "3.12.15"), profile_set=value("SET", "ci"),
            log_path=Path(value("LOG", ".gate-logs/runtime-startup.log")),
            validate_only=policy == "1", heartbeat=float(value("HEARTBEAT", "10")),
            retain=int(value("RETAIN", "20")), task=value("TASK"),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    code = result.get("exit_code", 0)
    if not isinstance(code, int):
        raise ValueError("diagnostic returned a non-integer exit code")
    return code


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse compatible run mode plus bounded status/tail reader modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--runtime-startup", choices=("run", "report", "archive"))
    modes.add_argument("--status", action="store_true")
    modes.add_argument("--tail", type=int, metavar="LINES")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_OBSERVED_ROOT)
    parser.add_argument("--label")
    parser.add_argument("--run-id")
    parser.add_argument("--heartbeat-secs", type=float)
    parser.add_argument("--quiet-secs", type=float, default=0.0)
    parser.add_argument("--max-secs", type=float, default=0.0)
    parser.add_argument("--stale-secs", type=float, default=DEFAULT_STALE_SECS)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--pytest-trace", action="store_true")
    parser.add_argument("--retain-runs", type=int, default=DEFAULT_RETAIN_RUNS)
    parser.add_argument("--item-progress", action="store_true")
    parser.add_argument("--item-task", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if args.runtime_startup:
        if args.command:
            parser.error("startup facades do not accept arbitrary commands")
        return args
    if args.status or args.tail is not None:
        if not args.label:
            parser.error("--label is required for status and tail modes")
        if args.command:
            parser.error("status and tail modes do not accept a command")
        return args
    if not args.command:
        parser.error("a command is required after --")
    if not args.label and args.log is None:
        parser.error("--log is required unless --label enables observed mode")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the selected run, status, or bounded-tail operation."""
    args = _parse_args(argv)
    try:
        if args.runtime_startup:
            return _runtime_startup_cli(args.runtime_startup)
        if args.status:
            return observed_status(
                args.root,
                args.label,
                stale_seconds=args.stale_secs,
                run_id=args.run_id,
            )
        if args.tail is not None:
            return observed_tail(
                args.root,
                args.label,
                lines=args.tail,
                run_id=args.run_id,
            )
        label = args.label
        run_id = args.run_id or (_new_run_id() if label else None)
        log_path = args.log
        if log_path is None:
            assert label is not None and run_id is not None
            log_path = args.root / _safe_name(label, field="label") / f"{run_id}.log"
        heartbeat = args.heartbeat_secs
        if heartbeat is None:
            heartbeat = DEFAULT_HEARTBEAT_SECS if label else 0.0
        context: AbstractContextManager[_ProgressAdmission | None] = nullcontext(None)
        progress_root: Path | None = None
        if args.item_progress:
            if TYPE_CHECKING:
                from item_progress import command_admission, progress_status
            else:
                if __package__ in {None, ""}:
                    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from scripts.item_progress import command_admission, progress_status
            # Unattended CI keeps its established owner contract. Interactive
            # work enables scope admission through the native focus command.
            if progress_status(Path.cwd()) is not None:
                progress_root = Path.cwd()
                context = command_admission(
                    Path.cwd(), args.command, environment=os.environ,
                    quiet_seconds=args.quiet_secs, max_seconds=args.max_secs,
                    task_id=args.item_task or None,
                )
        with context as admission:
            return stream_command(
                args.command,
                log_path,
                observed_root=args.root if label else None,
                label=label,
                run_id=run_id,
                heartbeat_seconds=heartbeat,
                quiet_seconds=args.quiet_secs,
                max_seconds=args.max_secs,
                quiet=args.quiet,
                pytest_trace=args.pytest_trace,
                retain_runs=args.retain_runs,
                progress_admission=admission,
                progress_root=progress_root,
            )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"observed-command: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
