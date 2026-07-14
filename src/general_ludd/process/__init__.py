"""Managed-process tracking for gludd.

gludd starts OS processes from several places (the ansible core runner's
multiprocessing workers, ``asyncio.create_subprocess_exec`` deploy/inference/mcp
calls, the detached daemon). Historically their PIDs were captured only in local
function scope and discarded after use, so an agent could not later enumerate,
signal, or monitor a process gludd had started.

This package provides a single in-process registry of gludd-managed processes so
those capabilities can be built on a safe foundation: signalling is confined to
processes present in the registry AND identity-checked against their recorded
start time, which closes the PID-reuse hole (a recycled PID belonging to an
unrelated process is never signalled).
"""

from __future__ import annotations

from general_ludd.process.registry import (
    ManagedProcess,
    ProcessRegistry,
    default_registry,
    set_default_registry,
)

__all__ = [
    "ManagedProcess",
    "ProcessRegistry",
    "default_registry",
    "set_default_registry",
]
