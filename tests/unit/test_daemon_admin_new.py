"""Unit tests for new admin endpoints: metrics, projects, compute, model registry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


class TestAgentMetricsEndpoints:
    @pytest.mark.asyncio
    async def test_list_agents_empty(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/agents")
            assert resp.status_code == 200
            assert resp.json() == {"agents": []}

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/agents/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_agents_after_registration(self, app, transport):
        ext = daemon_mod._get_or_create_extended_subsystems(app)
        ext["metrics"].register_agent("agent-1", agent_name="TestAgent", project="proj-a")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/agents")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["agents"]) == 1
            assert data["agents"][0]["agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_get_agent_detail(self, app, transport):
        ext = daemon_mod._get_or_create_extended_subsystems(app)
        ext["metrics"].register_agent("agent-1", agent_name="TestAgent")
        ext["metrics"].record_model_call("agent-1", "gpt-4", 100, 50, True)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/agents/agent-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["agent_id"] == "agent-1"
            assert "models_used" in data
            assert "gpt-4" in data["models_used"]


class TestMetricsEndpoints:
    @pytest.mark.asyncio
    async def test_cost_estimate(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/admin/metrics/cost",
                params={
                    "subscription_name": "pro",
                    "subscription_cost_per_month": 100.0,
                    "tokens_per_week": 50000,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["subscription_name"] == "pro"
            assert data["subscription_cost_usd_per_month"] == 100.0

    @pytest.mark.asyncio
    async def test_metrics_report(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/metrics/report")
            assert resp.status_code == 200
            data = resp.json()
            assert "total_agents" in data
            assert "global_model_usage" in data


class TestProjectEndpoints:
    @pytest.mark.asyncio
    async def test_add_project(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/admin/projects",
                json={"name": "proj-a", "weight": 60.0, "description": "Test project"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "proj-a"
            assert data["weight"] == 60.0
            assert data["active"] is True

    @pytest.mark.asyncio
    async def test_list_projects_empty(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/projects")
            assert resp.status_code == 200
            data = resp.json()
            assert "projects" in data
            assert data["total_projects"] == 0

    @pytest.mark.asyncio
    async def test_delete_project(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            add_resp = await client.post(
                "/admin/projects",
                json={"name": "proj-del", "weight": 30.0},
            )
            project_id = add_resp.json()["project_id"]
            del_resp = await client.delete(f"/admin/projects/{project_id}")
            assert del_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_set_project_weight(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            add_resp = await client.post(
                "/admin/projects",
                json={"name": "proj-w", "weight": 30.0},
            )
            project_id = add_resp.json()["project_id"]
            resp = await client.put(
                f"/admin/projects/{project_id}/weight",
                json={"weight": 40.0},
            )
            assert resp.status_code == 200
            assert resp.json()["weight"] == 40.0

    @pytest.mark.asyncio
    async def test_rebalance_projects(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            a_resp = await client.post(
                "/admin/projects",
                json={"name": "proj-ra", "weight": 50.0},
            )
            b_resp = await client.post(
                "/admin/projects",
                json={"name": "proj-rb", "weight": 50.0},
            )
            a_id = a_resp.json()["project_id"]
            b_id = b_resp.json()["project_id"]
            resp = await client.post(
                "/admin/projects/rebalance",
                json={"weights": {a_id: 40.0, b_id: 60.0}},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_add_project_over_100_fails(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/admin/projects",
                json={"name": "proj-a", "weight": 80.0},
            )
            resp = await client.post(
                "/admin/projects",
                json={"name": "proj-b", "weight": 30.0},
            )
            assert resp.status_code == 422


class TestComputeEndpoints:
    @pytest.mark.asyncio
    async def test_utilization_report(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/compute/utilization")
            assert resp.status_code == 200
            data = resp.json()
            assert "overall_utilization_pct" in data
            assert "endpoints" in data

    @pytest.mark.asyncio
    async def test_list_compute_endpoints(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/compute/endpoints")
            assert resp.status_code == 200
            data = resp.json()
            assert "endpoints" in data


class TestModelRegistryEndpoints:
    @pytest.mark.asyncio
    async def test_search_models(self, transport):
        with patch("general_ludd.models.model_registry.ModelRegistry.search") as mock_search:
            mock_search.return_value = [
                MagicMock(
                    model_id="TheBloke/Llama-2-7B-GGUF",
                    author="TheBloke",
                    downloads=50000,
                    tags=["gguf"],
                    pipeline_tag="text-generation",
                    library_name="llama.cpp",
                ),
            ]
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/admin/models/search",
                    json={"query": "llama gguf", "limit": 10},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert "results" in data

    @pytest.mark.asyncio
    async def test_list_downloaded_models(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/models/downloaded")
            assert resp.status_code == 200
            data = resp.json()
            assert "models" in data
