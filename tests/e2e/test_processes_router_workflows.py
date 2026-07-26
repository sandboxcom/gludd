"""E2E coverage for the managed-process admin router.

These tests exercise the real FastAPI route registration and HTTP error mapping
while substituting the process registry so no host process is signalled.
"""

from __future__ import annotations

import builtins
from io import StringIO
from types import SimpleNamespace

import psutil
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.process.registry import ProcessRegistryError
from general_ludd.routers import processes


class _FakeRegistry:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            pid=123,
            to_dict=lambda: {"pid": 123, "command": ["worker"], "origin": "e2e"},
        )
        self.signal_calls: list[tuple[int, int, bool]] = []
        self.resolve_error: Exception | None = None
        self.signal_error: Exception | None = None
        self.alive = True

    def list(self):
        return [self.record]

    def is_alive(self, pid: int) -> bool:
        return self.alive and pid == 123

    def get(self, pid: int):
        return self.record if pid == 123 else None

    def resolve_signal(self, value: str) -> int:
        if self.resolve_error:
            raise self.resolve_error
        return 15

    def signal(self, pid: int, signum: int, *, group: bool = False) -> None:
        if self.signal_error:
            raise self.signal_error
        self.signal_calls.append((pid, signum, group))


def _app(registry: _FakeRegistry) -> FastAPI:
    app = FastAPI()
    processes.default_registry = lambda: registry
    processes.register(app, {})
    return app


@pytest.mark.asyncio
async def test_list_and_signal_success(monkeypatch):
    registry = _FakeRegistry()
    app = _app(registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as client:
        listed = await client.get("/admin/processes")
        assert listed.status_code == 200
        assert listed.json() == {
            "processes": [{"pid": 123, "command": ["worker"], "origin": "e2e", "alive": True}],
            "count": 1,
        }
        signalled = await client.post(
            "/admin/processes/123/signal", json={"signal": "SIGTERM", "group": True}
        )
    assert signalled.status_code == 200
    assert signalled.json()["signal"] == "SIGTERM"
    assert registry.signal_calls == [(123, 15, True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("not a gludd-managed process", 404),
        ("identity check failed", 409),
        ("process disappeared", 409),
        ("signal not in allow-list", 400),
        ("other refusal", 400),
    ],
)
async def test_signal_registry_errors_map_to_http(monkeypatch, message, status):
    registry = _FakeRegistry()
    registry.resolve_error = ProcessRegistryError(message)
    app = _app(registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as client:
        response = await client.post("/admin/processes/123/signal", json={})
    assert response.status_code == status
    assert message in response.json()["detail"]


@pytest.mark.asyncio
async def test_signal_unexpected_error_is_500():
    registry = _FakeRegistry()
    registry.signal_error = RuntimeError("boom")
    app = _app(registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as client:
        response = await client.post("/admin/processes/123/signal", json={})
    assert response.status_code == 500
    assert response.json()["detail"] == "internal error delivering signal"


@pytest.mark.asyncio
async def test_stats_liveness_and_success(monkeypatch):
    registry = _FakeRegistry()
    app = _app(registry)
    stats = {"pid": 123, "cpu_percent": 1.0, "memory": {"rss": 1, "vms": 2}}
    monkeypatch.setattr(processes, "_collect_stats", lambda pid: stats)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as client:
        ok = await client.get("/admin/processes/123/stats")
        registry.alive = False
        missing = await client.get("/admin/processes/123/stats")
        unknown = await client.get("/admin/processes/999/stats")
    assert ok.status_code == 200 and ok.json() == stats
    assert missing.status_code == unknown.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [(psutil.AccessDenied(pid=123), 403), (psutil.NoSuchProcess(pid=123), 404), (RuntimeError("x"), 404)],
)
async def test_stats_collection_errors_are_fail_closed(monkeypatch, error, status):
    registry = _FakeRegistry()
    app = _app(registry)

    def _raise(_pid: int):
        raise error

    monkeypatch.setattr(processes, "_collect_stats", _raise)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e") as client:
        response = await client.get("/admin/processes/123/stats")
    assert response.status_code == status


def test_read_proc_locks_filters_malformed_and_other_pids(monkeypatch):
    payload = """1: POSIX ADVISORY WRITE 123 fd:01:999 0 EOF
2: FLOCK ADVISORY READ 456 fd:02:111 4 8
short line
3: POSIX ADVISORY WRITE nope fd:03:222 0 EOF
"""
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: StringIO(payload))

    locks = processes._read_proc_locks(123)

    assert locks == [
        {
            "type": "POSIX",
            "kind": "ADVISORY",
            "mode": "WRITE",
            "pid": 123,
            "region": "fd:01:999",
            "start": "0",
            "end": "EOF",
        }
    ]


def test_read_proc_locks_fails_closed_when_procfs_unavailable(monkeypatch):
    def _missing(*args, **kwargs):
        raise OSError("procfs unavailable")

    monkeypatch.setattr(builtins, "open", _missing)

    assert processes._read_proc_locks(123) == []


def test_collect_stats_success_and_procfs_lock_integration(monkeypatch):
    class _Process:
        def cpu_percent(self, interval):
            assert interval == 0.1
            return 12.5

        def memory_info(self):
            return SimpleNamespace(rss=100, vms=200)

        def io_counters(self):
            return SimpleNamespace(read_bytes=1, write_bytes=2, read_count=3, write_count=4)

        def num_fds(self):
            return 5

        def num_ctx_switches(self):
            return SimpleNamespace(voluntary=6, involuntary=7)

        def open_files(self):
            return ["a", "b"]

        def num_threads(self):
            return 8

        def status(self):
            return "running"

    monkeypatch.setattr(psutil, "Process", lambda pid: _Process())
    monkeypatch.setattr(processes, "_read_proc_locks", lambda pid: [{"pid": pid}])

    result = processes._collect_stats(123)

    assert result["cpu_percent"] == 12.5
    assert result["memory"] == {"rss": 100, "vms": 200}
    assert result["io"] == {
        "read_bytes": 1,
        "write_bytes": 2,
        "read_count": 3,
        "write_count": 4,
    }
    assert result["num_fds"] == 5
    assert result["num_ctx_switches"] == {"voluntary": 6, "involuntary": 7}
    assert result["open_files"] == 2
    assert result["locks"] == [{"pid": 123}]


def test_collect_stats_degrades_when_optional_psutil_fields_fail(monkeypatch):
    class _Process:
        def cpu_percent(self, interval):
            raise RuntimeError("cpu unavailable")

        def memory_info(self):
            return SimpleNamespace(rss=10, vms=20)

        def io_counters(self):
            raise RuntimeError("io unavailable")

        def num_fds(self):
            raise RuntimeError("fds unavailable")

        def num_ctx_switches(self):
            raise RuntimeError("ctx unavailable")

        def open_files(self):
            raise RuntimeError("files unavailable")

        def num_threads(self):
            return 1

        def status(self):
            return "sleeping"

    monkeypatch.setattr(psutil, "Process", lambda pid: _Process())
    monkeypatch.setattr(processes, "_read_proc_locks", lambda pid: [])

    result = processes._collect_stats(321)

    assert result["cpu_percent"] == 0.0
    assert result["memory"] == {"rss": 10, "vms": 20}
    assert result["io"] is None
    assert result["num_fds"] is None
    assert result["num_ctx_switches"] is None
    assert result["open_files"] == 0
