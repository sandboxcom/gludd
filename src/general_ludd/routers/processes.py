"""Admin HTTP API over the gludd managed-process registry.

These endpoints expose :mod:`general_ludd.process.registry` — the in-process
registry of OS processes gludd itself started — for inspection and controlled
signalling. Every route lives under ``/admin/*`` so the daemon's PSK middleware
protects it automatically; this module adds NO auth of its own.

Safety is delegated entirely to the registry: it confines operations to PIDs
gludd registered and verifies process identity (anti PID-reuse) before any
signal is delivered. This router never touches an arbitrary system PID — it only
calls :func:`default_registry` helpers.
"""

from __future__ import annotations

import asyncio
import logging
import signal as _signal
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.process.registry import ProcessRegistryError, default_registry

logger = logging.getLogger(__name__)


def _read_proc_locks(pid: int) -> list[dict[str, Any]]:
    """Best-effort parse of ``/proc/locks`` filtered to ``pid``.

    Linux-only; returns ``[]`` on any error or on platforms without
    ``/proc/locks``. Each ``/proc/locks`` line looks like::

        1: POSIX  ADVISORY  WRITE 1234 fd:01:9999 0 EOF

    where field index 4 is the owning PID.
    """
    locks: list[dict[str, Any]] = []
    try:
        with open("/proc/locks", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 8:
                    continue
                try:
                    owner_pid = int(parts[4])
                except ValueError:
                    continue
                if owner_pid != pid:
                    continue
                locks.append(
                    {
                        "type": parts[1],
                        "kind": parts[2],
                        "mode": parts[3],
                        "pid": owner_pid,
                        "region": parts[5],
                        "start": parts[6],
                        "end": parts[7],
                    }
                )
    except Exception:
        return []
    return locks


def _collect_stats(pid: int) -> dict[str, Any]:
    """Build the psutil snapshot for a single managed pid (runs off-loop).

    Every field that may be unavailable on a given platform degrades to ``None``
    (or ``[]`` / ``0`` per the documented shape) instead of raising.
    """
    import psutil

    proc = psutil.Process(pid)

    # A short interval gives a meaningful percentage; harmless inside to_thread.
    try:
        cpu_percent = proc.cpu_percent(interval=0.1)
    except Exception:
        cpu_percent = 0.0

    mem = proc.memory_info()

    io: dict[str, int] | None
    try:
        ioc = proc.io_counters()  # type: ignore[attr-defined]
        io = {
            "read_bytes": ioc.read_bytes,
            "write_bytes": ioc.write_bytes,
            "read_count": ioc.read_count,
            "write_count": ioc.write_count,
        }
    except Exception:
        io = None

    num_fds: int | None
    try:
        num_fds = proc.num_fds()
    except Exception:
        num_fds = None

    ctx: dict[str, int] | None
    try:
        cs = proc.num_ctx_switches()
        ctx = {"voluntary": cs.voluntary, "involuntary": cs.involuntary}
    except Exception:
        ctx = None

    try:
        open_files = len(proc.open_files())
    except Exception:
        open_files = 0

    return {
        "pid": pid,
        "cpu_percent": cpu_percent,
        "memory": {"rss": int(mem.rss), "vms": int(mem.vms)},
        "io": io,
        "num_fds": num_fds,
        "num_threads": proc.num_threads(),
        "num_ctx_switches": ctx,
        "status": proc.status(),
        "open_files": open_files,
        "locks": _read_proc_locks(pid),
    }


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    @app.get("/admin/processes")
    async def list_processes() -> dict[str, Any]:
        reg = default_registry()
        records = reg.list()
        processes = [
            {**r.to_dict(), "alive": reg.is_alive(r.pid)} for r in records
        ]
        return {"processes": processes, "count": len(processes)}

    @app.post("/admin/processes/{pid}/signal")
    async def signal_process(pid: int, body: dict[str, Any]) -> dict[str, Any]:
        reg = default_registry()
        sig = body.get("signal", "SIGTERM")
        group = bool(body.get("group", False))
        try:
            signum = reg.resolve_signal(sig)
            # signal() reads psutil create_time() for the identity guard and then
            # os.kill()s — both blocking syscalls; keep them off the event loop
            # (mirrors process_stats below).
            await asyncio.to_thread(reg.signal, pid, signum, group=group)
        except ProcessRegistryError as exc:
            # The registry's refusal messages are safe to surface — they carry no
            # secrets, only PID/signal/identity facts the caller already knows.
            msg = str(exc)
            if "not a gludd-managed process" in msg:
                status = 404
            elif "identity check failed" in msg or "disappeared" in msg:
                status = 409
            elif "allow-list" in msg:
                status = 400
            else:
                status = 400
            raise HTTPException(status_code=status, detail=msg) from exc
        except Exception as exc:
            logger.warning(
                "unexpected error signalling pid=%s", pid, exc_info=True
            )
            raise HTTPException(
                status_code=500, detail="internal error delivering signal"
            ) from exc
        return {
            "ok": True,
            "pid": pid,
            "signal": _signal.Signals(signum).name,
            "group": group,
        }

    @app.get("/admin/processes/{pid}/stats")
    async def process_stats(pid: int) -> dict[str, Any]:
        reg = default_registry()
        # Confinement: only managed + live (identity-checked) PIDs are inspectable.
        if reg.get(pid) is None or not reg.is_alive(pid):
            raise HTTPException(
                status_code=404,
                detail=f"pid {pid} is not a live gludd-managed process",
            )
        try:
            # All psutil/proc reads are blocking syscalls; keep them off the loop.
            return await asyncio.to_thread(_collect_stats, pid)
        except Exception as exc:
            logger.warning(
                "unexpected error collecting stats for pid=%s", pid, exc_info=True
            )
            raise HTTPException(
                status_code=404,
                detail=f"pid {pid} is no longer available",
            ) from exc
