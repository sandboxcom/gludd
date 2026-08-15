"""Dogfood: can a context-free consumer learn everything they need from the
webmcp self-description alone?

A 'context-free consumer' has NO prior knowledge of gludd beyond what the
running daemon tells them.  They need to discover:

  (a) how to authenticate (which header, which format)
  (b) which paths are public vs PSK-protected
  (c) the endpoint inventory (method, path, purpose)
  (d) /api/facts facet names
  (e) request/response shapes for at least the POST endpoints

These tests exercise the live ``GET /api/webmcp`` endpoint (added in this
branch) via the real daemon app via ASGITransport (same pattern as
test_messages_and_facts_api.py).  Assertions follow: test FIRST documents
the gap, then the implementation fills it, and the test passes.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base

PSK = "dogfood-psk-99"
AUTH = {"Authorization": f"Bearer {PSK}"}


async def _make_app(monkeypatch):
    """Minimal real daemon app with PSK enabled, no event-loop running."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setenv("GLUDD_AUTH_PSK", PSK)
    from general_ludd.daemon import create_daemon_app

    app = create_daemon_app(tick_interval=1.0)
    app.state._session_factory = factory
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return engine, client


class TestWebmcpEndpointExists:
    """The daemon exposes GET /api/webmcp — reachable and returns JSON."""

    @pytest.mark.asyncio
    async def test_webmcp_is_public(self, monkeypatch):
        """GET /api/webmcp must be reachable WITHOUT a PSK token.

        A fresh consumer has no token yet; they read the self-description to
        learn how to get one.  If the endpoint itself required auth, they'd be
        stuck in a bootstrap deadlock.
        """
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            assert resp.status_code == 200, (
                f"GET /api/webmcp returned {resp.status_code} (expected 200). "
                "The endpoint must be public so a context-free consumer can "
                "bootstrap without a token."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_webmcp_returns_json(self, monkeypatch):
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            data = resp.json()
            assert isinstance(data, dict), "GET /api/webmcp must return a JSON object"
        finally:
            await client.aclose()
            await engine.dispose()


class TestWebmcpAuth:
    """Gap (a): a context-free consumer must learn HOW to authenticate."""

    @pytest.mark.asyncio
    async def test_auth_section_present(self, monkeypatch):
        """The description must have an 'auth' top-level key."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            data = resp.json()
            assert "auth" in data, (
                "GET /api/webmcp response is missing the 'auth' key.  "
                "A context-free consumer cannot authenticate without it."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_auth_header_name_is_correct(self, monkeypatch):
        """auth.header must be 'Authorization' (not 'X-PSK' or anything else).

        quickstart.md previously documented 'X-PSK' which is WRONG — the
        daemon middleware checks Authorization: Bearer <token>.  This test
        locks in the correct value.
        """
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            auth = resp.json()["auth"]
            header_name = auth.get("header", "")
            assert header_name == "Authorization", (
                f"auth.header is {header_name!r}, expected 'Authorization'. "
                "The middleware checks 'Authorization: Bearer <token>'."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_auth_scheme_is_bearer(self, monkeypatch):
        """auth.scheme must be 'Bearer'."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            auth = resp.json()["auth"]
            assert auth.get("scheme") == "Bearer", (
                f"auth.scheme is {auth.get('scheme')!r}, expected 'Bearer'."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_auth_env_var_named(self, monkeypatch):
        """auth.env_var must name 'GLUDD_AUTH_PSK' so the consumer knows where to get the token."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            auth = resp.json()["auth"]
            assert "GLUDD_AUTH_PSK" in auth.get("env_var", ""), (
                f"auth.env_var is {auth.get('env_var')!r}; "
                "must reference GLUDD_AUTH_PSK so a consumer knows which env var to read."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_auth_public_paths_listed(self, monkeypatch):
        """auth.public_paths must enumerate paths that need NO token.

        Without this, the consumer can't know that /healthz is free while
        /api/facts is protected.
        """
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            auth = resp.json()["auth"]
            pub = auth.get("public_paths", [])
            assert isinstance(pub, list) and len(pub) > 0, (
                "auth.public_paths is empty/missing.  The consumer cannot tell "
                "which paths need PSK authentication."
            )
            assert "/healthz" in pub, (
                f"/healthz not in auth.public_paths ({pub!r}). "
                "/healthz is a documented public liveness endpoint."
            )
        finally:
            await client.aclose()
            await engine.dispose()


class TestWebmcpEndpointInventory:
    """Gap (c): the consumer needs an endpoint inventory (method, path, purpose)."""

    @pytest.mark.asyncio
    async def test_endpoints_key_present(self, monkeypatch):
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            data = resp.json()
            assert "endpoints" in data, (
                "GET /api/webmcp is missing an 'endpoints' key.  "
                "A context-free consumer cannot discover the API surface."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_endpoints_has_required_paths(self, monkeypatch):
        """Core paths that every consumer will need must be documented."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            endpoints = resp.json()["endpoints"]
            paths = {e["path"] for e in endpoints}
            required = {
                "/api/todos",
                "/api/status",
                "/api/facts",
                "/api/messages",
            }
            missing = required - paths
            assert not missing, (
                f"The following required paths are absent from the webmcp "
                f"endpoint inventory: {sorted(missing)}"
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_each_endpoint_has_method_and_purpose(self, monkeypatch):
        """Every endpoint entry must carry at least 'method', 'path', 'purpose'."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            endpoints = resp.json()["endpoints"]
            for ep in endpoints:
                for field in ("method", "path", "purpose"):
                    assert field in ep, (
                        f"Endpoint entry {ep!r} is missing the '{field}' field. "
                        "All entries must have method, path, purpose."
                    )
        finally:
            await client.aclose()
            await engine.dispose()


class TestWebmcpFactsFacets:
    """Gap (b): /api/facts facets must be documented so the consumer knows what to expect."""

    @pytest.mark.asyncio
    async def test_facts_facets_key_present(self, monkeypatch):
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            data = resp.json()
            assert "facts_facets" in data, (
                "GET /api/webmcp is missing a 'facts_facets' key.  "
                "A consumer cannot know what /api/facts returns."
            )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_facts_facets_covers_all_top_level_keys(self, monkeypatch):
        """facts_facets must document every key that /api/facts actually returns."""
        engine, client = await _make_app(monkeypatch)
        try:
            # Get the actual /api/facts response to discover the real facet keys.
            facts_resp = await client.get("/api/facts", headers=AUTH)
            assert facts_resp.status_code == 200, facts_resp.text
            actual_facets = set(facts_resp.json().keys())

            webmcp_resp = await client.get("/api/webmcp")
            documented_facets = set(webmcp_resp.json()["facts_facets"])

            undocumented = actual_facets - documented_facets
            assert not undocumented, (
                f"The following /api/facts facets are returned by the daemon "
                f"but NOT documented in GET /api/webmcp facts_facets: {sorted(undocumented)}"
            )
        finally:
            await client.aclose()
            await engine.dispose()


class TestWebmcpPostShapes:
    """Gap (d): request/response shapes for POST endpoints."""

    @pytest.mark.asyncio
    async def test_post_todos_shape_documented(self, monkeypatch):
        """The webmcp description must document the POST /api/todos request body."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            endpoints = resp.json()["endpoints"]
            post_todos = next(
                (e for e in endpoints if e["path"] == "/api/todos" and e["method"].upper() == "POST"),
                None,
            )
            assert post_todos is not None, (
                "POST /api/todos not found in webmcp endpoint inventory."
            )
            assert "request_body" in post_todos, (
                "POST /api/todos entry is missing 'request_body'.  "
                "A consumer cannot know what fields to send."
            )
            rb = post_todos["request_body"]
            required_fields = {"title"}
            for f in required_fields:
                assert f in rb, (
                    f"POST /api/todos request_body is missing required field '{f}'. "
                    f"Documented body: {rb!r}"
                )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_post_messages_shape_documented(self, monkeypatch):
        """The webmcp description must document the POST /api/messages request body."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/webmcp")
            endpoints = resp.json()["endpoints"]
            post_msgs = next(
                (e for e in endpoints if e["path"] == "/api/messages" and e["method"].upper() == "POST"),
                None,
            )
            assert post_msgs is not None, (
                "POST /api/messages not found in webmcp endpoint inventory."
            )
            assert "request_body" in post_msgs, (
                "POST /api/messages entry is missing 'request_body'."
            )
            rb = post_msgs["request_body"]
            for field in ("sender", "recipient"):
                assert field in rb, (
                    f"POST /api/messages request_body is missing required field '{field}'. "
                    f"Documented body: {rb!r}"
                )
        finally:
            await client.aclose()
            await engine.dispose()


class TestWebmcpRoundTrip:
    """Smoke: the self-description claims are honest — they match the live daemon."""

    @pytest.mark.asyncio
    async def test_documented_public_paths_actually_return_200_without_psk(self, monkeypatch):
        """Every path in auth.public_paths must actually be reachable without auth."""
        engine, client = await _make_app(monkeypatch)
        try:
            webmcp = await client.get("/api/webmcp")
            pub_paths = webmcp.json()["auth"]["public_paths"]
            # Only test GETable paths that exist on the app (skip /docs, /redoc
            # which require additional rendering, and /api/webmcp itself which
            # we already know is public).
            skip = {"/docs", "/redoc", "/openapi.json"}
            for path in pub_paths:
                if path in skip:
                    continue
                resp = await client.get(path)
                assert resp.status_code != 401, (
                    f"Path {path!r} is listed in webmcp auth.public_paths but "
                    f"returned 401 — the self-description is dishonest."
                )
        finally:
            await client.aclose()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_protected_endpoint_needs_psk(self, monkeypatch):
        """Verify that /api/facts is NOT public — confirms auth.public_paths is
        not over-broad and that the consumer must use the Bearer token."""
        engine, client = await _make_app(monkeypatch)
        try:
            resp = await client.get("/api/facts")  # no auth
            assert resp.status_code == 401, (
                f"/api/facts returned {resp.status_code} without a token; "
                "expected 401.  PSK auth may not be enabled."
            )
        finally:
            await client.aclose()
            await engine.dispose()
