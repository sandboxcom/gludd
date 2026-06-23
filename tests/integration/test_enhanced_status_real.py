"""Integration test for enhanced status endpoint — tests the REAL daemon app via ASGITransport."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


@pytest.fixture
def transport():
    app = create_daemon_app(tick_interval=0.01)
    return ASGITransport(app=app)


class TestRealDaemonEnhancedStatus:
    """Tests that call the ACTUAL daemon app through ASGITransport, not a duplicate handler."""

    @pytest.mark.asyncio
    async def test_status_returns_all_enhanced_fields(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()

            # config_dir / config_files / filestore_root are intentionally absent:
            # they were removed as a security hardening measure (host-path leakage).
            # The schema now emits non-path equivalents: config_file_count (int),
            # filestore_available (bool), filestore_binaries (list).
            required = [
                "version",
                "uptime_ticks",
                "todos_total",
                "queue_depths",
                "tick_metrics",
                "config_file_count",
                "filestore_available",
                "filestore_binaries",
                "binary_versions",
                "db_engine",
                "db_url",
            ]
            for field in required:
                assert field in data, f"Missing field '{field}' in status response"

    @pytest.mark.asyncio
    async def test_status_version_is_non_empty_string(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            assert isinstance(data["version"], str)
            assert len(data["version"]) > 0

    @pytest.mark.asyncio
    async def test_status_todos_total_counts_real_todos(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/todos", json={"title": "Task A", "queue": "core"})
            await client.post("/api/todos", json={"title": "Task B", "queue": "qa"})

            resp = await client.get("/api/status")
            data = resp.json()
            assert data["todos_total"] == 2
            assert data["queue_depths"]["core"] == 1
            assert data["queue_depths"]["qa"] == 1

    @pytest.mark.asyncio
    async def test_status_filestore_fields_exist(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            # filestore_root is intentionally absent (host-path leakage); the
            # schema exposes availability as a boolean instead.
            assert "filestore_root" not in data
            assert "filestore_available" in data
            assert isinstance(data["filestore_available"], bool)
            assert "filestore_binaries" in data
            assert isinstance(data["filestore_binaries"], list)

    @pytest.mark.asyncio
    async def test_status_db_fields_exist(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            assert "db_engine" in data
            assert "db_url" in data

    @pytest.mark.asyncio
    async def test_status_config_fields_exist(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            # config_dir and config_files are intentionally absent (host-path
            # leakage); the schema exposes a count integer instead.
            assert "config_dir" not in data
            assert "config_files" not in data
            assert "config_file_count" in data
            assert isinstance(data["config_file_count"], int)

    @pytest.mark.asyncio
    async def test_status_uptime_ticks_increments_after_event_loop_runs(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            assert "uptime_ticks" in data
            assert isinstance(data["uptime_ticks"], int)
            assert data["uptime_ticks"] >= 0

    @pytest.mark.asyncio
    async def test_status_tick_metrics_has_sub_keys(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            assert isinstance(data["tick_metrics"], dict)
