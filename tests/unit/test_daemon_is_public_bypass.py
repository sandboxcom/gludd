"""S.4: _is_public /docs prefix bypass regression tests.

startswith("/docs") would match /docs_evil or /docs-evil/admin, treating
non-docs paths as public. The fix uses startswith("/docs/") (trailing
slash + exact match for "/docs" itself) so only genuine docs paths are
public.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_PSK = "test-psk-docs-bypass-s4"


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_docs_exact_is_public():
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.01)
    async with _client(app) as c:
        resp = await c.get("/docs")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_docs_trailing_slash_is_public():
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.01)
    async with _client(app) as c:
        resp = await c.get("/docs/")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_docs_evil_is_not_public():
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.01)
    async with _client(app) as c:
        resp = await c.get("/docs_evil")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_docs_evil_admin_is_not_public():
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.01)
    async with _client(app) as c:
        resp = await c.get("/docs-evil/admin")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_healthz_is_public():
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app
        app = create_daemon_app(tick_interval=0.01)
    async with _client(app) as c:
        resp = await c.get("/healthz")
    assert resp.status_code != 401
