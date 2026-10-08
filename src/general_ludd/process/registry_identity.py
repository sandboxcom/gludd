"""Lazy PID identity reads for the managed-process registry."""

from __future__ import annotations

from collections.abc import Callable


def read_create_time(pid: int) -> float | None:
    """Return the live process creation time, or ``None`` when unverifiable."""
    try:
        import psutil

        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def identity_matches(
    pid: int,
    expected_create_time: float | None,
    *,
    reader: Callable[[int], float | None] = read_create_time,
    tolerance_seconds: float = 0.5,
) -> bool:
    """Return whether ``pid`` still has the expected process identity."""
    live_create_time = reader(pid)
    if live_create_time is None or expected_create_time is None:
        return False
    return abs(live_create_time - expected_create_time) <= tolerance_seconds
