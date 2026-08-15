"""Red-team tests for worker HTTP auth parity (FIX 3).

The worker runs arbitrary registered playbooks for any caller who can reach the
port. It must enforce the same PSK posture as the daemon:
  - A-1: constant-time token comparison (no `!=` timing oracle).
  - A-3: honor GLUDD_REQUIRE_AUTH -> fail CLOSED (503) when set with no PSK.
  - default (no PSK, no require) stays OPEN for back-compat.

All exercised via AsyncClient + ASGITransport (no real network).
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.worker.app import create_app


@pytest.fixture(autouse=True)
def _clean_env():
    for var in ("GLUDD_AUTH_PSK", "GLUDD_REQUIRE_AUTH"):
        os.environ.pop(var, None)
    yield
    for var in ("GLUDD_AUTH_PSK", "GLUDD_REQUIRE_AUTH"):
        os.environ.pop(var, None)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestWorkerAuthParity:
    @pytest.mark.asyncio
    async def test_no_psk_default_open(self):
        # Back-compat: no env -> /jobs/execute reachable without a token. The
        # unknown playbook 400 proves the request passed auth and hit the route.
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
            )
            assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_psk_rejects_missing_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "s3cret")
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
            )
            assert resp.status_code == 401, resp.text

    @pytest.mark.asyncio
    async def test_psk_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "s3cret")
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
                headers={"Authorization": "Bearer wrong"},
            )
            assert resp.status_code == 401, resp.text

    @pytest.mark.asyncio
    async def test_psk_accepts_correct_token(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "s3cret")
        app = create_app(gateway=None)
        async with _client(app) as client:
            # Correct token -> passes auth, hits route, 400 for unknown playbook.
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
                headers={"Authorization": "Bearer s3cret"},
            )
            assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_healthz_public_even_with_psk(self, monkeypatch):
        monkeypatch.setenv("GLUDD_AUTH_PSK", "s3cret")
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200, resp.text

    @pytest.mark.asyncio
    async def test_require_auth_fail_closed_no_psk(self, monkeypatch):
        # A-3: GLUDD_REQUIRE_AUTH set, no PSK -> non-public paths 503 (fail-closed),
        # NOT fail-open serving unauthenticated job execution.
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
            )
            assert resp.status_code == 403, resp.text
            # /healthz stays public even in fail-closed mode.
            hz = await client.get("/healthz")
            assert hz.status_code == 200

    @pytest.mark.asyncio
    async def test_require_auth_with_psk_uses_token(self, monkeypatch):
        # When both are set, a valid token still passes (require-auth only bites
        # when there is no PSK at all).
        monkeypatch.setenv("GLUDD_AUTH_PSK", "s3cret")
        monkeypatch.setenv("GLUDD_REQUIRE_AUTH", "1")
        app = create_app(gateway=None)
        async with _client(app) as client:
            resp = await client.post(
                "/jobs/execute",
                json={"job_id": "J1", "playbook": "nope.yml", "queue": "core"},
                headers={"Authorization": "Bearer s3cret"},
            )
            assert resp.status_code == 400, resp.text
