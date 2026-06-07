"""Test that daemon status endpoint includes preflight quality gate results."""

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


class TestStatusQualityGate:
    @pytest.mark.asyncio
    async def test_status_includes_quality_gate_field(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "quality_gate" in data
            assert isinstance(data["quality_gate"], dict)

    @pytest.mark.asyncio
    async def test_status_quality_gate_has_overall_status(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            qg = data["quality_gate"]
            assert "overall" in qg
            assert qg["overall"] in ("PASS", "FAIL", "not_run")

    @pytest.mark.asyncio
    async def test_status_quality_gate_has_check_count(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            qg = data["quality_gate"]
            assert "passed_count" in qg
            assert "total_count" in qg

    @pytest.mark.asyncio
    async def test_status_quality_gate_with_known_state(self, transport):
        daemon_mod._daemon_state["quality_gate"] = {
            "overall": "PASS",
            "passed_count": 8,
            "total_count": 8,
            "last_run": "2026-06-07T00:00:00",
            "checks": [
                {"name": "coverage_85pct", "passed": True},
                {"name": "lint_clean", "passed": True},
            ],
        }
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            qg = data["quality_gate"]
            assert qg["overall"] == "PASS"
            assert qg["passed_count"] == 8
            assert qg["total_count"] == 8

    @pytest.mark.asyncio
    async def test_status_quality_gate_default_not_run(self, transport):
        daemon_mod._daemon_state["quality_gate"] = {}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/status")
            data = resp.json()
            qg = data["quality_gate"]
            assert qg["overall"] == "not_run"
            assert qg["passed_count"] == 0
