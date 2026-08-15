"""Regression tests for F5b: /docs prefix auth bypass.

/docs-admin and /docsecret must NOT be treated as public paths.
Only /docs itself and paths under /docs/ (with trailing slash) are public.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_PSK = "test-psk-docs-bypass"


def _daemon_client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _worker_client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestDaemonDocsPublicPaths:
    """_is_public must only allow /docs and /docs/* — not /docs-admin etc."""

    @pytest.mark.asyncio
    async def test_docs_exact_is_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.daemon import create_daemon_app
            app = create_daemon_app(tick_interval=0.01)
        async with _daemon_client(app) as c:
            resp = await c.get("/docs")
        # No auth header, but /docs is public — should not be 401.
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_docs_subpath_is_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.daemon import create_daemon_app
            app = create_daemon_app(tick_interval=0.01)
        async with _daemon_client(app) as c:
            resp = await c.get("/docs/oauth2-redirect")
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_docs_admin_is_not_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.daemon import create_daemon_app
            app = create_daemon_app(tick_interval=0.01)
        async with _daemon_client(app) as c:
            resp = await c.get("/docs-admin")
        assert resp.status_code == 401, (
            "F5b: /docs-admin must NOT be treated as public — "
            "it starts with /docs but is not /docs or /docs/"
        )

    @pytest.mark.asyncio
    async def test_docsecret_is_not_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.daemon import create_daemon_app
            app = create_daemon_app(tick_interval=0.01)
        async with _daemon_client(app) as c:
            resp = await c.get("/docsecret")
        assert resp.status_code == 401, (
            "F5b: /docsecret must NOT be treated as public"
        )


class TestWorkerDocsPublicPaths:
    """_worker_is_public must only allow /docs and /docs/* — not /docs-admin etc."""

    @pytest.mark.asyncio
    async def test_worker_docs_exact_is_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.worker.app import create_app
            app = create_app(gateway=None)
        async with _worker_client(app) as c:
            resp = await c.get("/docs")
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_worker_docs_subpath_is_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.worker.app import create_app
            app = create_app(gateway=None)
        async with _worker_client(app) as c:
            resp = await c.get("/docs/openapi.json")
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_worker_docs_admin_is_not_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.worker.app import create_app
            app = create_app(gateway=None)
        async with _worker_client(app) as c:
            resp = await c.get("/docs-admin")
        assert resp.status_code == 401, (
            "F5b: worker /docs-admin must NOT be treated as public"
        )

    @pytest.mark.asyncio
    async def test_worker_docsecret_is_not_public(self):
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.worker.app import create_app
            app = create_app(gateway=None)
        async with _worker_client(app) as c:
            resp = await c.get("/docsecret")
        assert resp.status_code == 401, (
            "F5b: worker /docsecret must NOT be treated as public"
        )
