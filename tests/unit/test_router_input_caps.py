"""DoS-hardening regression tests: admin endpoints must reject oversized
client-supplied collections early with HTTP 413 (before the unbounded
iterate/store/split operation).

Covers:
  * POST /admin/tui-log            — entries list cap (1000)
  * POST /admin/projects/rebalance — weights dict cap (500)
  * POST /admin/models/call        — max_tokens ceiling (1_000_000)
  * POST /admin/worktree/scan      — watch_paths split cap (100)
  * POST /admin/log-audit          — log_entries list cap (10_000)
"""

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
def app(monkeypatch):
    monkeypatch.setenv("GLUDD_ALLOW_NO_AUTH", "1")
    return create_daemon_app(tick_interval=0.01)


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


@pytest.mark.asyncio
async def test_tui_log_rejects_oversized_entries(transport):
    oversized = [{"event": "x"} for _ in range(1001)]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/admin/tui-log", json={"entries": oversized})
    assert resp.status_code == 413
    assert "entries" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_rebalance_rejects_oversized_weights(transport):
    oversized = {f"proj-{i:05d}": 1.0 for i in range(501)}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/projects/rebalance", json={"weights": oversized}
        )
    assert resp.status_code == 413
    assert "weights" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_models_call_rejects_oversized_max_tokens(transport):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/models/call",
            json={"prompt": "hello", "max_tokens": 1_000_001},
        )
    assert resp.status_code == 413
    assert "max_tokens" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_worktree_scan_rejects_oversized_watch_paths(transport):
    oversized = ",".join(f"/p{i}" for i in range(101))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/worktree/scan", params={"watch_paths": oversized}
        )
    assert resp.status_code == 413
    assert "watch_paths" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_log_audit_rejects_oversized_log_entries(transport):
    oversized = ["line"] * 10_001
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/log-audit", json={"log_entries": oversized}
        )
    assert resp.status_code == 413
    assert "log_entries" in resp.json()["detail"]
