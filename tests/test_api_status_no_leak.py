"""Regression tests for F6a: /api/status must not leak internal paths/DB info."""
from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

# Internal host paths that must NEVER appear in the public response.
_LEAKED_KEYS = {"config_dir", "config_files", "filestore_root"}
_REQUIRED_KEYS = {"version", "filestore_available", "binary_versions", "quality_gate"}
# db_url / db_engine may be present but must have any password masked (***),
# never the raw credential. Passwords in a URL look like :<secret>@.

_PASSWORD_PATTERN = re.compile(r":[^:@/]+@")


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
    # db_url / db_engine are allowed but must carry no raw password.
    for k in ("db_url", "db_engine"):
        if k in body:
            val = body[k]
            assert not _PASSWORD_PATTERN.search(val), (
                f"F6a: {k} leaks a password segment: {val!r}"
            )


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
