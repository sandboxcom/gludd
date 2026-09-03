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
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, BinaryIO

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


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Fsync JSON beside ``path`` and atomically replace the visible file."""
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
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


class _StatusPublisher:
    """Publish both durable per-run state and the atomic current pointer."""

    def __init__(self, root: Path, label: str, run_id: str) -> None:
        self.directory = (root / label).resolve()
        self.run_path = self.directory / f"{run_id}.json"
        self.current_path = self.directory / "current.json"

    def publish(self, payload: Mapping[str, object]) -> None:
        """Write history before advancing the current-run pointer."""
        _atomic_write_json(self.run_path, payload)
        _atomic_write_json(self.current_path, payload)


def _prune_terminal_runs(directory: Path, *, retain_runs: int) -> None:
    """Prune only superseded terminal runs, never current or active evidence."""
    if retain_runs < 1:
        raise ValueError("retain_runs must be at least 1")
    terminal: list[tuple[datetime, str, Path, dict[str, Any]]] = []
    for status_path in directory.glob("*.json"):
        if status_path.name == "current.json" or status_path.is_symlink():
            continue
        try:
            value = json.loads(status_path.read_text(encoding="utf-8"))
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
    for _started_at, _name, status_path, value in terminal[retain_runs:]:
        run_id = value.get("run_id")
        if not isinstance(run_id, str) or status_path.name != f"{run_id}.json":
            continue
        for field, suffix in (("log_path", ".log"), ("trace_path", ".pytest.jsonl")):
            raw_path = value.get(field)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            artifact = Path(raw_path).resolve()
            if artifact.parent == resolved_directory and artifact.name == f"{run_id}{suffix}":
                artifact.unlink(missing_ok=True)
        status_path.unlink()


def _terminate_process_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Terminate only the observed child's session, escalating after a grace."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    process.wait(timeout=max(grace_seconds, 1.0))


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
        if not _publish_or_fail(publisher, status):
            return OBSERVER_FAILURE
        print(
            f"[observed {effective_label}] start run_id={effective_run_id} "
            f"log={resolved_log}",
            flush=True,
        )

    try:
        resolved_log.parent.mkdir(parents=True, exist_ok=True)
        log_file = resolved_log.open("wb")
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

    environment = os.environ.copy()
    if trace_path is not None and effective_run_id is not None:
        environment["GLUDD_XDIST_TRACE_LOG"] = str(trace_path)
        environment["GLUDD_XDIST_TRACE_RUN_ID"] = effective_run_id
        environment["GLUDD_XDIST_TRACE_TRUNCATE"] = "1"

    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,
            env=environment,
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

    if status is not None:
        status.update(
            {"state": "running", "child_pid": process.pid, "updated_at": _utc_now()}
        )
        if not _publish_or_fail(publisher, status):
            _terminate_process_group(process, termination_grace_seconds)
            log_file.close()
            return OBSERVER_FAILURE

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
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if selector.get_map():
                ready = selector.select(timeout=0.05)
            else:
                time.sleep(0.05)
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
            _prune_terminal_runs(publisher.directory, retain_runs=retain_runs)
        except OSError as exc:
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
    path = directory / (f"{safe_run_id}.json" if safe_run_id else "current.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"observed status is not a JSON object: {path}")
    if safe_run_id is not None and (
        value.get("schema_version") != 1
        or value.get("kind") != "observed_command"
        or value.get("label") != safe_label
        or value.get("run_id") != safe_run_id
    ):
        raise ValueError(f"retained observed status identity mismatch: {path}")
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


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse compatible run mode plus bounded status/tail reader modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
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
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
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
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"observed-command: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
