"""Tests for new daemon API endpoints: MCP catalog, skills catalog, compute endpoint registration."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestMCPCatalogEndpoints:
    @pytest.mark.asyncio
    async def test_mcp_catalog_search(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/mcp/catalog/search", json={"query": "github", "limit": 5})
            assert resp.status_code == 200
            data = resp.json()
            assert "results" in data
            assert isinstance(data["results"], list)

    @pytest.mark.asyncio
    async def test_mcp_catalog_servers_list(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/mcp/catalog/servers")
            assert resp.status_code == 200
            data = resp.json()
            assert "servers" in data
            assert len(data["servers"]) >= 10

    @pytest.mark.asyncio
    async def test_mcp_catalog_server_detail(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/mcp/catalog/servers/github")
            assert resp.status_code == 200
            data = resp.json()
            assert data["server"]["server_name"] == "github"

    @pytest.mark.asyncio
    async def test_mcp_catalog_server_not_found(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/mcp/catalog/servers/nonexistent")
            assert resp.status_code == 404


class TestSkillsCatalogEndpoints:
    @pytest.mark.asyncio
    async def test_skills_catalog_search(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/skills/catalog/search", json={"query": "tdd", "limit": 5})
            assert resp.status_code == 200
            data = resp.json()
            assert "results" in data
            assert any(r["name"] == "tdd-discipline" for r in data["results"])

    @pytest.mark.asyncio
    async def test_skills_catalog_list(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/skills/catalog")
            assert resp.status_code == 200
            data = resp.json()
            assert "skills" in data
            assert len(data["skills"]) >= 10

    @pytest.mark.asyncio
    async def test_skills_catalog_install(self, transport, tmp_path):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/skills/catalog/install", json={"name": "tdd-discipline"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "tdd-discipline"

    @pytest.mark.asyncio
    async def test_skills_catalog_install_not_found(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/skills/catalog/install", json={"name": "nonexistent"})
            assert resp.status_code == 404


class TestComputeEndpointRegistration:
    @pytest.mark.asyncio
    async def test_register_compute_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/compute/endpoints", json={
                "endpoint_id": "test-gpu",
                "url": "http://gpu-server:8000",
                "model": "llama-3",
                "gpu_type": "a100_80",
                "max_concurrent": 8,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["endpoint_id"] == "test-gpu"
            assert data["url"] == "http://gpu-server:8000"

    @pytest.mark.asyncio
    async def test_register_endpoint_missing_fields(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/compute/endpoints", json={})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_unregister_compute_endpoint(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/admin/compute/endpoints", json={
                "endpoint_id": "to-remove",
                "url": "http://localhost:9999",
            })
            resp = await client.delete("/admin/compute/endpoints/to-remove")
            assert resp.status_code == 200
            assert resp.json()["removed"] == "to-remove"

    @pytest.mark.asyncio
    async def test_list_endpoints(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/compute/endpoints")
            assert resp.status_code == 200
            data = resp.json()
            assert "endpoints" in data
