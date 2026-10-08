"""Lazy PID identity reads for the managed-process registry."""

from __future__ import annotations


def read_create_time(pid: int) -> float | None:
    """Return the live process creation time, or ``None`` when unverifiable."""
    try:
        import psutil

        return float(psutil.Process(pid).create_time())
    except Exception:
        return None
