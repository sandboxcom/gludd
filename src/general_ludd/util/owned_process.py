"""Application-owned supervision for blocking operations in killable processes."""

from __future__ import annotations

import contextlib
import logging
import math
import multiprocessing
import os
import re
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Protocol, TypeVar, cast

logger = logging.getLogger(__name__)

_Result = TypeVar("_Result")
_PROCESS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class _OwnedChild(Protocol):
    """Structural process surface shared by every multiprocessing context."""

    @property
    def pid(self) -> int | None:
        """Return the operating-system child identifier when started."""

    def is_alive(self) -> bool:
        """Return whether the child remains live."""

    def join(self, timeout: float | None = None) -> None:
        """Wait for the child, optionally for a bounded interval."""

    def close(self) -> None:
        """Release the local process handle after the child is reaped."""


class OwnedProcessTimeout(TimeoutError):
    """Raised after Gludd stops an owned child at its absolute deadline."""


class OwnedProcessCancelled(RuntimeError):
    """Raised after Gludd stops an owned child following internal cancellation."""


class OwnedProcessTerminationError(RuntimeError):
    """Raised when an owned process cannot be confirmed stopped."""


@dataclass(frozen=True, slots=True)
class OwnedProcessPolicy:
    """Finite timing policy for one supervised blocking operation."""

    timeout_seconds: float
    shutdown_grace_seconds: float = 5.0
    poll_interval_seconds: float = 0.25
    heartbeat_interval_seconds: float = 15.0

    def __post_init__(self) -> None:
        """Reject timing policies that are unbounded or cannot make progress."""
        limits = {
            "timeout_seconds": (self.timeout_seconds, 86_400.0),
            "shutdown_grace_seconds": (self.shutdown_grace_seconds, 60.0),
            "poll_interval_seconds": (self.poll_interval_seconds, 60.0),
            "heartbeat_interval_seconds": (self.heartbeat_interval_seconds, 3_600.0),
        }
        for field_name, (value, maximum) in limits.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0 < float(value) <= maximum
            ):
                raise ValueError(
                    f"{field_name} must be finite, positive, and no more than {maximum:g}"
                )


def _owned_process_entry(
    sender: Connection,
    operation: Callable[..., object],
    args: tuple[object, ...],
) -> None:
    """Become a process-group leader, execute once, and return one payload."""
    with contextlib.suppress(AttributeError, OSError):
        os.setsid()
    try:
        try:
            sender.send(("result", operation(*args)))
        except BaseException as error:
            try:
                sender.send(("error", error))
            except Exception:
                sender.send(("error_type", type(error).__name__))
    finally:
        sender.close()


def _emit(event_sink: Callable[[str], None] | None, event: str) -> None:
    if event_sink is None:
        return
    try:
        event_sink(event)
    except Exception:
        logger.warning("Owned-process event sink failed", exc_info=True)


def _signal_owned_group(process: _OwnedChild, sig: signal.Signals) -> None:
    pid = process.pid
    if pid is None:
        return
    try:
        os.killpg(pid, sig)
        return
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, sig)


def _terminate_and_join(
    process: _OwnedChild,
    grace_seconds: float,
) -> None:
    """Stop the exact process group and prove the direct child was reaped."""
    if not process.is_alive():
        process.join()
        return
    _signal_owned_group(process, signal.SIGTERM)
    process.join(grace_seconds)
    if process.is_alive():
        _signal_owned_group(process, signal.SIGKILL)
        process.join(grace_seconds)
    if process.is_alive():
        raise OwnedProcessTerminationError(
            "owned process could not be confirmed stopped"
        )


def _validate_process_name(process_name: str) -> None:
    if not isinstance(process_name, str) or _PROCESS_NAME.fullmatch(process_name) is None:
        raise ValueError("process_name must be a bounded identifier")


def run_owned_process(
    operation: Callable[..., _Result],
    args: tuple[object, ...],
    *,
    policy: OwnedProcessPolicy,
    process_name: str,
    cancel_requested: Callable[[], bool] | None = None,
    event_sink: Callable[[str], None] | None = None,
) -> _Result:
    """Run once in Gludd's process group with heartbeat, cancel, and deadline.

    The parent never returns from a cancellation or deadline until the child has
    been joined.  A caller can therefore requeue work only after this function
    reports a confirmed terminal outcome.
    """
    _validate_process_name(process_name)
    if not callable(operation):
        raise TypeError("operation must be callable")
    if type(args) is not tuple:
        raise TypeError("args must be a tuple")
    if cancel_requested is not None and not callable(cancel_requested):
        raise TypeError("cancel_requested must be callable")

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_owned_process_entry,
        args=(sender, operation, args),
        name=process_name,
        daemon=False,
    )
    started = False
    try:
        process.start()
        started = True
        sender.close()
        _emit(event_sink, f"OWNED_PROCESS_STARTED name={process_name}")
        started_at = time.monotonic()
        deadline = started_at + float(policy.timeout_seconds)
        next_heartbeat = started_at + float(policy.heartbeat_interval_seconds)

        while process.is_alive():
            if cancel_requested is not None and cancel_requested():
                _terminate_and_join(process, float(policy.shutdown_grace_seconds))
                _emit(event_sink, f"OWNED_PROCESS_CANCELLED name={process_name}")
                raise OwnedProcessCancelled("owned process cancellation requested")

            now = time.monotonic()
            if now >= deadline:
                _terminate_and_join(process, float(policy.shutdown_grace_seconds))
                _emit(event_sink, f"OWNED_PROCESS_TIMED_OUT name={process_name}")
                raise OwnedProcessTimeout("owned process deadline exceeded")
            if now >= next_heartbeat:
                elapsed_seconds = max(0, int(now - started_at))
                _emit(
                    event_sink,
                    f"OWNED_PROCESS_HEARTBEAT name={process_name} elapsed_seconds={elapsed_seconds}",
                )
                next_heartbeat = now + float(policy.heartbeat_interval_seconds)

            process.join(
                min(
                    float(policy.poll_interval_seconds),
                    max(0.0, deadline - now),
                    max(0.0, next_heartbeat - now),
                )
            )

        process.join()
        if not receiver.poll(float(policy.shutdown_grace_seconds)):
            raise RuntimeError("owned process returned no result")
        try:
            payload = receiver.recv()
        except EOFError as exc:
            raise RuntimeError("owned process returned no result") from exc
    finally:
        if started and process.is_alive():
            _terminate_and_join(process, float(policy.shutdown_grace_seconds))
        receiver.close()
        with contextlib.suppress(OSError, ValueError):
            sender.close()
        if started and not process.is_alive():
            process.close()

    if (
        not isinstance(payload, tuple)
        or len(payload) != 2
        or not isinstance(payload[0], str)
    ):
        raise RuntimeError("owned process returned an invalid result")
    status, value = payload
    if status == "result":
        return cast(_Result, value)
    if status == "error" and isinstance(value, BaseException):
        raise value
    if status == "error_type" and isinstance(value, str):
        raise RuntimeError(f"owned process failed with {value}")
    raise RuntimeError("owned process returned an invalid result")


__all__ = (
    "OwnedProcessCancelled",
    "OwnedProcessPolicy",
    "OwnedProcessTerminationError",
    "OwnedProcessTimeout",
    "run_owned_process",
)
