"""Test that all completion-audit-flagged classes have daemon endpoints wired."""

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


class TestWiredEndpoints:
    @pytest.mark.asyncio
    async def test_gap_analysis_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/gap-analysis")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_log_audit_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/log-audit")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_evidence_check_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/evidence-check", json={"text": "test claim"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_quality_gate_check_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/qualitygate/check")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dispatch_agent_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/dispatch-agent", json={"task": "test", "agent": "default"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dogfood_run_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/dogfood/run", json={"sprint_file": "test.md"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_container_build_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/container/build")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dependency_check_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/dependency/check")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_git_automate_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/git/automate", json={"repo": "."})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_deployment_apply_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/deployment/apply")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_self_improve_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/self-improve")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_scoring_run_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/scoring/run")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_observability_record_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/observability/record")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_review_return_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/review/return")
            assert resp.status_code == 200
