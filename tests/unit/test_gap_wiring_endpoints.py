"""Test that non-stub endpoints exist and return expected status."""
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


class TestRealEndpointsExist:
    @pytest.mark.asyncio
    async def test_self_improve_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/admin/self-improve")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dispatch_mode_endpoint_exists(self, transport):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put("/admin/dispatch/mode", json={"mode": "active"})
            assert resp.status_code == 200
