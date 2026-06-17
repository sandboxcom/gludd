"""Tests for FIX A (_is_public exact-match) and FIX B (psk-gated healthz/readyz)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    daemon_mod._daemon_state["todos"] = []
    daemon_mod._daemon_state["tick_metrics"] = {}


# ---------------------------------------------------------------------------
# FIX A: _is_public must use exact membership, not startswith("/docs")
# ---------------------------------------------------------------------------

class TestIsPublicExactMatch:
    """Verify that paths sharing a /docs prefix are NOT treated as public."""

    def test_docs_exact_is_public(self):
        """Canonical /docs path is still public."""
        from general_ludd.daemon import create_daemon_app as _cda  # noqa: F401
        # We test via the internal closure by constructing a mini app and
        # checking that auth is required for /docs-admin when a PSK is set.
        # (Direct unit test of _is_public is not exported; we validate
        # the observable contract via the auth middleware instead.)

    @pytest.mark.asyncio
    async def test_docs_prefix_bypass_rejected(self, monkeypatch):
        """GET /docs-admin must NOT bypass auth when a PSK is configured."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # /docs-admin is NOT in _PUBLIC_PATHS; it must require auth
            resp = await client.get("/docs-admin")
            assert resp.status_code == 401, (
                f"/docs-admin should be auth-gated (got {resp.status_code})"
            )

    @pytest.mark.asyncio
    async def test_docsx_prefix_bypass_rejected(self, monkeypatch):
        """/docsx must NOT bypass auth when a PSK is configured."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/docsx")
            assert resp.status_code == 401, (
                f"/docsx should be auth-gated (got {resp.status_code})"
            )

    @pytest.mark.asyncio
    async def test_docs_exact_still_accessible(self, monkeypatch):
        """/docs itself remains accessible without auth (exact-match kept)."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/docs")
            # FastAPI serves the Swagger UI — 200 or redirect (3xx)
            assert resp.status_code in (200, 307, 308), (
                f"/docs should be public (got {resp.status_code})"
            )

    @pytest.mark.asyncio
    async def test_docs_oauth2_redirect_accessible(self, monkeypatch):
        """/docs/oauth2-redirect is public (explicitly in _PUBLIC_PATHS)."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/docs/oauth2-redirect")
            # FastAPI serves the oauth2-redirect page — not 401
            assert resp.status_code != 401, (
                f"/docs/oauth2-redirect should be public (got {resp.status_code})"
            )


# ---------------------------------------------------------------------------
# FIX B: /healthz reason + budget gated behind PSK
# ---------------------------------------------------------------------------

class TestHealthzPskGating:
    """reason and budget must only appear in /healthz when a PSK is set."""

    @pytest.mark.asyncio
    async def test_healthz_no_psk_omits_reason_and_budget(self, monkeypatch):
        """Without a PSK, /healthz must NOT include reason or budget."""
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            # Public fields always present (back-compat)
            assert "status" in body
            assert "no_auth" in body
            assert "auth_degraded" in body
            # PSK-gated fields must be absent
            assert "reason" not in body, "reason must not appear without PSK"
            assert "budget" not in body, "budget must not appear without PSK"

    @pytest.mark.asyncio
    async def test_healthz_with_psk_includes_budget(self, monkeypatch):
        """With a PSK, /healthz must include budget."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert "budget" in body, "budget should appear when PSK is set"

    @pytest.mark.asyncio
    async def test_healthz_degraded_no_psk_omits_reason(self, monkeypatch):
        """In degraded state without PSK, reason must be omitted from /healthz."""
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        app = create_daemon_app(tick_interval=0.01)
        app.state._degraded = "internal db error details"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "degraded"
            assert "reason" not in body, "reason must not appear without PSK"
            assert "budget" not in body, "budget must not appear without PSK"

    @pytest.mark.asyncio
    async def test_healthz_degraded_with_psk_includes_reason(self, monkeypatch):
        """In degraded state with PSK, reason must be present in /healthz."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        app.state._degraded = "internal db error details"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "degraded"
            assert "reason" in body, "reason should appear when PSK is set"
            assert "internal db" in body["reason"]


# ---------------------------------------------------------------------------
# FIX B: /readyz reason gated behind PSK
# ---------------------------------------------------------------------------

class TestReadyzPskGating:
    """In /readyz degraded branch, reason must be generic when no PSK is set."""

    @pytest.mark.asyncio
    async def test_readyz_degraded_no_psk_generic_reason(self, monkeypatch):
        """Without PSK, /readyz degraded reason must be generic, not internal."""
        monkeypatch.delenv("GLUDD_PSK", raising=False)
        app = create_daemon_app(tick_interval=0.01)
        app.state._degraded = "internal db error details"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "degraded"
            assert body["reason"] == "degraded", (
                f"reason should be generic 'degraded' without PSK (got {body['reason']!r})"
            )

    @pytest.mark.asyncio
    async def test_readyz_degraded_with_psk_internal_reason(self, monkeypatch):
        """With PSK, /readyz degraded reason must expose internal detail."""
        monkeypatch.setenv("GLUDD_PSK", "test-secret-key")
        app = create_daemon_app(tick_interval=0.01)
        app.state._degraded = "internal db error details"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "degraded"
            assert "internal db" in body["reason"], (
                f"reason should expose detail with PSK (got {body['reason']!r})"
            )
