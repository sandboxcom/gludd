"""Unit tests for the managed-process admin router (routers/processes.py).

Mirrors test_healthz_dead_loop.py: drives the real factory-built daemon app via
httpx ASGITransport, without running the lifespan (hermetic — no DB, no network).
The daemon test-harness runs in no-auth mode (no PSK configured), so /admin/*
routes are reachable directly, exactly as test_daemon.py exercises
/admin/log-level.

A real short-lived child process is spawned and registered via
default_registry() for the happy-path list/stats assertions, and torn down in a
fixture. Unmanaged-PID requests assert the 404 confinement behavior.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.process.registry import default_registry


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def managed_child() -> Iterator[int]:
    """Spawn, register, and later tear down a real managed child process."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    reg = default_registry()
    reg.register(proc.pid, command=["sleep-30-test-child"], origin="unit_test")
    try:
        yield proc.pid
    finally:
        reg.deregister(proc.pid)
        proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


def _unmanaged_pid() -> int:
    """A PID that is live (our own) but was never registered."""
    import os

    return os.getpid()


@pytest.mark.asyncio
async def test_list_processes_includes_managed_child(app, managed_child):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/admin/processes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    pids = {p["pid"] for p in data["processes"]}
    assert managed_child in pids
    rec = next(p for p in data["processes"] if p["pid"] == managed_child)
    assert rec["origin"] == "unit_test"
    assert rec["alive"] is True


@pytest.mark.asyncio
async def test_stats_for_managed_child(app, managed_child):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/admin/processes/{managed_child}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pid"] == managed_child
    assert "cpu_percent" in data
    assert set(data["memory"]) == {"rss", "vms"}
    assert isinstance(data["memory"]["rss"], int)
    assert isinstance(data["num_threads"], int)
    assert isinstance(data["open_files"], int)
    assert isinstance(data["locks"], list)
    assert "status" in data


@pytest.mark.asyncio
async def test_signal_unmanaged_pid_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/admin/processes/{_unmanaged_pid()}/signal",
            json={"signal": "SIGTERM", "group": False},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stats_unmanaged_pid_returns_404(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/admin/processes/{_unmanaged_pid()}/stats")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_signal_managed_child_ok(app, managed_child):
    # SIGCONT is in the allow-list and harmless to a running sleeper; proves the
    # signal path returns the canonical name and echoes group.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/admin/processes/{managed_child}/signal",
            json={"signal": "SIGCONT", "group": False},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "ok": True,
        "pid": managed_child,
        "signal": "SIGCONT",
        "group": False,
    }
