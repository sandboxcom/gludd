"""Allowlisted signal admission and delivery for managed processes."""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable

from general_ludd.process.registry_types import ManagedProcess, ProcessRegistryError

logger = logging.getLogger(__name__)

_ALLOWED_SIGNALS: dict[str, int] = {
    name: int(getattr(signal, name))
    for name in (
        "SIGTERM",
        "SIGINT",
        "SIGHUP",
        "SIGQUIT",
        "SIGUSR1",
        "SIGUSR2",
        "SIGKILL",
        "SIGSTOP",
        "SIGCONT",
    )
    if hasattr(signal, name)
}


def resolve_signal(requested: int | str) -> int:
    """Map a signal name or number to an allowed signal number."""
    if isinstance(requested, str):
        name = requested.strip().upper()
        if not name.startswith("SIG"):
            name = "SIG" + name
        if name not in _ALLOWED_SIGNALS:
            raise ProcessRegistryError(
                f"signal {requested!r} is not in the allow-list "
                f"({', '.join(sorted(_ALLOWED_SIGNALS))})"
            )
        return _ALLOWED_SIGNALS[name]
    signum = int(requested)
    if signum not in _ALLOWED_SIGNALS.values():
        raise ProcessRegistryError(f"signal number {signum} is not in the allow-list")
    return signum


def allowed_signals() -> dict[str, int]:
    """Return a copy of the signal allow-list."""
    return dict(_ALLOWED_SIGNALS)


def validate_target(
    record: ManagedProcess,
    identity_ok: Callable[[ManagedProcess], bool],
) -> int | None:
    """Return the admitted process group after an exact identity check."""
    if not identity_ok(record):
        raise ProcessRegistryError(
            f"refusing to signal pid {record.pid}: process is gone or its PID was "
            "reused by a different process (identity check failed)"
        )
    return record.pgid


def deliver_signal(
    pid: int,
    signum: int,
    *,
    group: bool,
    pgid: int | None,
) -> None:
    """Deliver an admitted signal and normalize kernel refusal errors."""
    try:
        if group and pgid is not None:
            os.killpg(pgid, signum)
        else:
            os.kill(pid, signum)
    except ProcessLookupError as exc:
        raise ProcessRegistryError(
            f"process {pid} disappeared before the signal was delivered"
        ) from exc
    except PermissionError as exc:
        raise ProcessRegistryError(f"not permitted to signal process {pid}") from exc
    logger.info(
        "delivered %s to managed pid=%s (group=%s)",
        signal.Signals(signum).name,
        pid,
        group,
    )
