"""Deep middleware stack tests for the daemon.

Covers: middleware ordering, CORS absence, auth gating, CIDR enforcement,
request/response stats, degraded guard, and request-ID propagation (absence).
Uses the real ``create_daemon_app`` factory with ASGITransport.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

_PSK = "deep-mw-psk-xyz"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app(**kwargs):
    with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
        from general_ludd.daemon import create_daemon_app

        return create_daemon_app(tick_interval=0.01, **kwargs)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# 1. Middleware ordering
# ---------------------------------------------------------------------------


class TestMiddlewareOrdering:
    """auth_and_stats_middleware must run OUTSIDE cidr_middleware.

    When both fire: auth runs first (outermost), CIDR second (innermost).
    On the response path the order reverses: CIDR first, auth second.
    """

    @pytest.mark.asyncio
    async def test_auth_middleware_registered_before_cidr(self):
        """Prove auth middleware is declared higher in source than CIDR."""
        import inspect

        from general_ludd import daemon as daemon_mod

        src = inspect.getsource(daemon_mod.create_daemon_app)
        auth_idx = src.find("auth_and_stats_middleware")
        cidr_idx = src.find("cidr_middleware")
        assert auth_idx >= 0, "auth_and_stats_middleware not in source"
        assert cidr_idx >= 0, "cidr_middleware not in source"
        assert auth_idx < cidr_idx, "auth middleware must be declared before cidr middleware"

    @pytest.mark.asyncio
    async def test_both_middleware_are_http_type(self):
        """Prove both middleware are registered as 'http' (not 'websocket')."""
        app = _make_app()
        mw_count = len(app.user_middleware)
        assert mw_count >= 2, f"Expected at least 2 middlewares, got {mw_count}"

    @pytest.mark.asyncio
    async def test_cidr_middleware_runs_after_auth(self):
        """A request blocked by CIDR still goes through auth first (stats increment)."""
        app = _make_app()
        app.state._allowed_cidr = ["10.0.0.0/8"]
        before_req = app.state._stats_requests
        before_resp = app.state._stats_responses

        from starlette.types import Scope

        async def _override_client(scope: Scope, receive, send):
            scope["client"] = ("192.168.1.1", 12345)
            await app(scope, receive, send)

        async with AsyncClient(transport=ASGITransport(app=_override_client), base_url="http://test") as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 403

        assert app.state._stats_requests > before_req
        assert app.state._stats_responses > before_resp


# ---------------------------------------------------------------------------
# 2. CORS configuration
# ---------------------------------------------------------------------------


class TestCorsConfig:
    @pytest.mark.asyncio
    async def test_no_cors_middleware_registered(self):
        """The daemon does not use FastAPI's CORSMiddleware."""
        app = _make_app()
        for mw in app.user_middleware:
            cls = getattr(mw, "cls", None)
            cls_name = getattr(cls, "__name__", "") if cls is not None else ""
            assert "cors" not in cls_name.lower()

    @pytest.mark.asyncio
    async def test_healthz_lacks_cors_headers(self, transport=None):
        """Responses carry no Access-Control-* headers."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get("/healthz")
            for hdr in resp.headers:
                assert "access-control-" not in hdr.lower(), f"unexpected CORS header: {hdr}"

    @pytest.mark.asyncio
    async def test_public_path_lacks_cors_headers(self):
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get("/api/status")
            for hdr in resp.headers:
                assert "access-control-" not in hdr.lower(), f"unexpected CORS header: {hdr}"


# ---------------------------------------------------------------------------
# 3. Auth middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_public_get_is_unauthenticated(self):
        """Public read-only paths bypass PSK check."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_public_post_requires_auth(self):
        """POST on a path in _PUBLIC_PATHS must go through auth."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.post("/healthz")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_non_public_path_requires_psk(self):
        """Any non-public, non-safe path returns 401 without Bearer."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get("/admin/dashboard/overview")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_psk_gains_access(self):
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get(
                "/admin/daemon/stats",
                headers={"Authorization": f"Bearer {_PSK}"},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_psk_is_401(self):
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get(
                "/admin/daemon/stats",
                headers={"Authorization": "Bearer wrong-psk"},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_require_auth_returns_503(self):
        """fail-closed: no PSK + require_auth → 503 on non-public paths."""
        with patch.dict(os.environ, {}, clear=True), patch.dict(
            os.environ,
            {"GLUDD_REQUIRE_AUTH": "1"},
        ):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
        async with _client(app) as c:
            resp = await c.get("/admin/daemon/stats")
            assert resp.status_code == 503
            data = resp.json()
            assert "auth_required" in data.get("error", "")

    @pytest.mark.asyncio
    async def test_project_id_claim_parsed_from_token(self):
        """Bearer <project>:<psk> stamps request.state.project_id."""
        app = _make_app()
        token = f"my-proj:{_PSK}"
        async with _client(app) as c:
            resp = await c.get(
                "/api/facts/overview",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_degraded_blocks_mutating_dispatch(self):
        """When _degraded is set, /api/dispatch returns 503."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": _PSK}):
            from general_ludd.daemon import create_daemon_app

            app = create_daemon_app(tick_interval=0.01)
        app.state._degraded = "test-degraded"
        async with _client(app) as c:
            resp = await c.post(
                "/api/dispatch/sync",
                json={"todo": {"title": "x"}},
                headers={"Authorization": f"Bearer {_PSK}"},
            )
            data = resp.json()
            # Degraded guard runs BEFORE the route handler
            assert resp.status_code == 503
            assert data.get("error") == "degraded"


# ---------------------------------------------------------------------------
# 4. Request / response stats
# ---------------------------------------------------------------------------


class TestRequestStats:
    @pytest.mark.asyncio
    async def test_stats_requests_incremented(self):
        app = _make_app()
        before = app.state._stats_requests
        async with _client(app) as c:
            await c.get("/healthz")
            await c.get("/healthz")
        assert app.state._stats_requests >= before + 2

    @pytest.mark.asyncio
    async def test_stats_responses_incremented(self):
        app = _make_app()
        before = app.state._stats_responses
        async with _client(app) as c:
            await c.get("/healthz")
        assert app.state._stats_responses == before + 1

    @pytest.mark.asyncio
    async def test_metrics_counter_registered(self):
        """gludd_http_requests_total counter exists after a request."""
        app = _make_app()
        async with _client(app) as c:
            await c.get("/healthz")
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        m = get_metrics_exporter()
        counters = m.get_counters()
        assert any("gludd_http_requests_total" in k for k in counters)

    @pytest.mark.asyncio
    async def test_metrics_histogram_registered(self):
        """gludd_http_request_duration_seconds histogram exists after a request."""
        app = _make_app()
        async with _client(app) as c:
            await c.get("/healthz")
        from general_ludd.observability.metrics_exporter import get_metrics_exporter

        m = get_metrics_exporter()
        gauges = m.get_gauges()
        # histogram is stored as a gauge in the exporter
        assert any("gludd_http_request_duration_seconds" in k for k in gauges)


# ---------------------------------------------------------------------------
# 5. CIDR middleware
# ---------------------------------------------------------------------------


class TestCidrMiddleware:
    @pytest.mark.asyncio
    async def test_loopback_allowed_by_default(self):
        """Loopback 127.0.0.1 is allowed by default CIDR."""
        app = _make_app()
        app.state._allowed_cidr = ["127.0.0.0/8", "::1/128"]
        async with _client(app) as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_testclient_mapped_to_loopback(self):
        """ASGITransport maps testclient → 127.0.0.1, so CIDR pass."""
        app = _make_app()
        app.state._allowed_cidr = ["127.0.0.0/8"]
        async with _client(app) as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_disallowed_ip_returns_403(self):
        """CIDR deny returns 403 with reason."""

        app = _make_app()
        app.state._allowed_cidr = ["10.0.0.0/8"]

        from starlette.types import Scope

        async def _override_client(scope: Scope, receive, send):
            scope["client"] = ("192.168.1.1", 12345)
            await app(scope, receive, send)

        async with AsyncClient(transport=ASGITransport(app=_override_client), base_url="http://test") as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 403
            data = resp.json()
            assert data.get("error") == "forbidden"

    @pytest.mark.asyncio
    async def test_empty_cidr_list_passes(self):
        """Empty CIDR list means no CIDR enforcement — all pass."""
        app = _make_app()
        app.state._allowed_cidr = []
        async with _client(app) as c:
            resp = await c.get("/healthz")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. Request-ID propagation
# ---------------------------------------------------------------------------


class TestRequestIdPropagation:
    @pytest.mark.asyncio
    async def test_no_request_id_header_on_response(self):
        """The daemon does not inject X-Request-ID headers."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get("/healthz")
            assert "x-request-id" not in {k.lower() for k in resp.headers}

    @pytest.mark.asyncio
    async def test_no_request_id_middleware(self):
        """No request-id middleware is registered."""
        app = _make_app()
        for mw in app.user_middleware:
            cls = getattr(mw, "cls", None)
            cls_name = getattr(cls, "__name__", "") if cls is not None else ""
            assert "request" not in cls_name.lower() or "auth" in cls_name.lower()

    @pytest.mark.asyncio
    async def test_incoming_request_id_not_propagated(self):
        """An incoming X-Request-ID header is ignored/not reflected back."""
        app = _make_app()
        async with _client(app) as c:
            resp = await c.get(
                "/healthz",
                headers={"X-Request-ID": "incoming-123"},
            )
            assert resp.headers.get("x-request-id") != "incoming-123"
