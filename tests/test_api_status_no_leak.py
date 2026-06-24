"""Regression tests for F6a: /api/status must not leak internal paths/DB info."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

_LEAKED_KEYS = {"db_url", "db_engine", "config_dir", "config_files", "filestore_root"}
_REQUIRED_KEYS = {"version", "filestore_available", "binary_versions", "quality_gate"}


@pytest.fixture
def daemon_app():
    from general_ludd.daemon import create_daemon_app
    return create_daemon_app(tick_interval=0.01)


@pytest.mark.asyncio
async def test_api_status_no_leak(daemon_app):
    async with AsyncClient(
        transport=ASGITransport(app=daemon_app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    leaked = _LEAKED_KEYS & set(body.keys())
    assert not leaked, f"F6a: /api/status must not expose {leaked}"


@pytest.mark.asyncio
async def test_api_status_has_required_keys(daemon_app):
    async with AsyncClient(
        transport=ASGITransport(app=daemon_app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    missing = _REQUIRED_KEYS - set(body.keys())
    assert not missing, f"F6a: /api/status is missing required keys {missing}"
