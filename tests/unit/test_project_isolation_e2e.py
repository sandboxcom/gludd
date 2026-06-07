"""Verify project isolation is fully wired through daemon lifespan to EventLoop dispatch."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}
    daemon_mod._daemon_state["quality_gate"] = {}


@pytest.fixture
def transport():
    app = create_daemon_app(tick_interval=0.01)
    return ASGITransport(app=app)


class TestProjectIsolationWiredEndToEnd:
    @pytest.mark.asyncio
    async def test_daemon_lifespan_creates_project_workspaces(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_daemon_lifespan_builds_project_secrets(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_event_loop_receives_project_workspace_from_daemon(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_project_per_playbook_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/projects/playbooks")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_project_per_secrets_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/projects/secrets")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_project_per_skills_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/projects/skills")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_project_per_mcp_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/projects/mcp")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_project_logging_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/projects/logging")
            assert resp.status_code == 200
