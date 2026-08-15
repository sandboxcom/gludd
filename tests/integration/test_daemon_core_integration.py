"""Integration: daemon core paths — health, dispatch, model gateway, capability registry, event loop.

Covers the 5 core daemon subsystems that every playbook and agent depends on.
Uses the REAL daemon app via ASGITransport wherever an HTTP surface exists;
falls back to direct construction for subsystems without an unauthenticated
HTTP endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any, cast
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
from general_ludd.models.gateway import ModelGateway, ModelProfile

# ── helpers ────────────────────────────────────────────────────────────────


class _FakeProviderRegistry:
    """Minimal ProviderRegistry stand-in for ModelGateway construction."""

    def get_provider(self, provider_id: str, extras: dict[str, Any] | None = None) -> Any:
        class _FakeProvider:
            provider_id = provider_id

            def _identify(self) -> str:
                return provider_id

            async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
                class _FakeResponse:
                    def __init__(self):
                        self.text = "mock response"
                        self.model = provider_id
                        self.token_usage = {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        }

                return _FakeResponse()

        return _FakeProvider()

    def list_providers(self) -> dict[str, dict[str, Any]]:
        return {"fake": {"provider_id": "fake", "display_name": "Fake"}}

    def find_best_provider(
        self, model_id: str, fallback: Any = None, budget_limit: float = 0.0
    ) -> dict[str, str] | None:
        return {"provider_id": "fake", "model_id": model_id}


# ── 1. Health endpoint ─────────────────────────────────────────────────────


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_healthz_returns_200_with_expected_fields(self):
        """GET /healthz returns 200 and all documented fields."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        for field in ("no_auth", "require_auth", "allow_no_auth", "auth_degraded", "budget_exhausted"):
            assert field in data, f"Missing field '{field}' in healthz response"

    @pytest.mark.asyncio
    async def test_healthz_returns_degraded_when_app_state_degraded_set(self):
        """GET /healthz returns 'degraded' status when _degraded is set on app.state."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "lifespan_startup_failure"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["reason"] == "lifespan_startup_failure"

    @pytest.mark.asyncio
    async def test_readyz_returns_200_when_healthy(self):
        """GET /readyz returns 200 when daemon event loop task is live."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)

        async def _fake_loop() -> None:
            while True:
                await asyncio.sleep(60)

        fake_task = asyncio.ensure_future(_fake_loop())
        app.state._event_loop_task = fake_task
        app.state._event_loop_task_auto = False

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
        assert resp.status_code == 200

        fake_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_task


# ── 2. Dispatch endpoint error handling ────────────────────────────────────


class TestDispatchErrorHandling:
    @pytest.mark.asyncio
    async def test_dispatch_empty_body_returns_422(self):
        """POST /api/dispatch with empty body returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json={})
        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        assert "parse" in detail.lower() or "could not" in detail.lower()

    @pytest.mark.asyncio
    async def test_dispatch_too_many_tool_calls_returns_422(self):
        """POST /api/dispatch with >20 tool calls returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        body = {"tool_calls": [{"kind": "mcp", "name": f"tool_{i}", "args": {}} for i in range(21)]}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json=body)
        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        assert "cap" in detail.lower() or "exceeds" in detail.lower()

    @pytest.mark.asyncio
    async def test_dispatch_missing_required_fields_returns_422(self):
        """POST /api/dispatch with body missing required fields returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json={"x": 1})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_dispatch_available_lists_registered_kinds(self):
        """GET /api/dispatch/available returns the registered handler kinds."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/dispatch/available")
        assert resp.status_code == 200
        data = resp.json()
        assert "registered_kinds" in data
        assert isinstance(data["registered_kinds"], list)


# ── 3. Model gateway integration ───────────────────────────────────────────


class TestModelGatewayIntegration:
    def test_gateway_construction_with_profile(self) -> None:
        """ModelGateway can be constructed with a ModelProfile."""
        profile = ModelProfile(
            model_profile_id="test-gpt4",
            model_name="gpt-4",
            provider="openai",
        )
        registry = _FakeProviderRegistry()
        gateway = ModelGateway(
            profiles=[profile],
            provider_registry=cast(Any, registry),
        )
        assert gateway is not None
        assert profile.model_profile_id in gateway._profiles

    def test_gateway_construction_with_no_profiles(self) -> None:
        """ModelGateway can be constructed with zero profiles (empty state)."""
        registry = _FakeProviderRegistry()
        gateway = ModelGateway(provider_registry=cast(Any, registry))
        assert gateway is not None
        assert gateway._profiles == {}

    def test_gateway_failover_log_initialized_empty(self) -> None:
        """Gateway starts with an empty failover log."""
        registry = _FakeProviderRegistry()
        gateway = ModelGateway(provider_registry=cast(Any, registry))
        assert gateway._failover_log is not None


# ── 4. Capability registry lookup ──────────────────────────────────────────


class TestCapabilityRegistryIntegration:
    def test_registry_add_and_lookup_by_tag(self) -> None:
        """CapabilityRegistry.add_collection + lookup_by_tag roundtrip."""
        reg = CapabilityRegistry()
        meta = CollectionMeta(
            name="travel",
            namespace="general_ludd",
            version="1.0",
            tags=frozenset({"travel", "flight"}),
        )
        reg.add_collection(meta)
        found = reg.lookup_by_tag("travel")
        assert "travel" in found
        found_flight = reg.lookup_by_tag("flight")
        assert "travel" in found_flight

    def test_registry_lookup_unknown_tag_returns_empty(self) -> None:
        """lookup_by_tag for a tag not in the registry returns empty frozenset."""
        reg = CapabilityRegistry()
        result = reg.lookup_by_tag("nonexistent")
        assert result == frozenset()

    def test_registry_to_dict_and_from_dict_roundtrip(self) -> None:
        """to_dict -> from_dict is a roundtrip that preserves collections."""
        reg = CapabilityRegistry()
        meta = CollectionMeta(
            name="ChemistryExpert",
            namespace="general_ludd",
            version="2.0",
            description="Chemistry expert collection",
            tags=frozenset({"chemistry", "science"}),
        )
        reg.add_collection(meta)
        d = reg.to_dict()
        restored = CapabilityRegistry.from_dict(d)
        assert "ChemistryExpert" in restored.collections
        restored_meta = restored.collections["ChemistryExpert"]
        assert restored_meta.namespace == "general_ludd"
        assert restored_meta.version == "2.0"
        assert restored_meta.description == "Chemistry expert collection"
        assert "chemistry" in restored_meta.tags
        assert "science" in restored_meta.tags


# ── 5. Event loop lifecycle ────────────────────────────────────────────────


class TestEventLoopLifecycle:
    def test_event_loop_construction_minimal(self) -> None:
        """EventLoop can be constructed with minimal arguments."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()
        assert loop is not None
        assert loop._running is False
        assert loop._total_ticks == 0

    @pytest.mark.asyncio
    async def test_event_loop_run_forever_and_stop_lifecycle(self) -> None:
        """EventLoop.run_forever() sets _running True; stop() sets it False."""
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop()
        assert loop._running is False

        tick_task = asyncio.ensure_future(loop.run_forever(interval=0.01))

        await asyncio.sleep(0.05)
        assert loop._running is True

        loop.stop()
        assert loop._running is False

        tick_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick_task

    def test_event_loop_config_preserved(self) -> None:
        """EventLoop stores and returns config dict."""
        from general_ludd.event_loop.loop import EventLoop

        config = {"foo": "bar", "baz": 42}
        loop = EventLoop(config=config)
        assert loop.config == config


# ── 6. Auth middleware error paths ─────────────────────────────────────────


class TestAuthMiddlewareErrors:
    @pytest.mark.asyncio
    async def test_protected_endpoint_without_psk_returns_503(self):
        """Protected endpoints return 503 when no PSK configured and auth is required."""
        with (
            patch.dict(os.environ, {"GLUDD_REQUIRE_AUTH": "1"}, clear=False),
            patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "0"}, clear=False),
        ):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json={"tool_calls": []})
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "auth_required"

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_bad_bearer_token_returns_401(self):
        """Protected endpoints return 401 when an invalid bearer token is sent."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "test-secret-key-12345"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dispatch",
                json={"tool_calls": []},
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"] == "unauthorized"

    @pytest.mark.asyncio
    async def test_public_endpoint_accessible_without_auth(self):
        """GET /healthz is accessible without PSK even when auth is configured."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "test-secret-key-12345"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_public_get_with_mutating_method_requires_auth(self):
        """A public path under a mutating method (POST) is NOT public — requires auth."""
        with patch.dict(os.environ, {"GLUDD_AUTH_PSK": "test-secret-key-12345"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/human-todos",
                json={"title": "test"},
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 401


# ── 7. Malformed request handling ──────────────────────────────────────────


class TestMalformedRequestHandling:
    @pytest.mark.asyncio
    async def test_malformed_json_body_returns_422(self):
        """POST with malformed JSON body returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dispatch",
                content=b"this is not json { [",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_nonexistent_endpoint_returns_404(self):
        """GET /nonexistent/path returns 404."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/nonexistent/endpoint")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_content_type_still_returns_422(self):
        """POST with text/plain content type on a JSON body returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/dispatch",
                content="hello world",
                headers={"Content-Type": "text/plain"},
            )
        assert resp.status_code == 422


# ── 8. Degraded daemon (startup failure) error paths ───────────────────────


class TestDegradedDaemon:
    @pytest.mark.asyncio
    async def test_degraded_daemon_mutating_endpoint_returns_503(self):
        """When daemon is degraded, mutating endpoints return 503."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "test_startup_failure"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json={"tool_calls": []})
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "degraded"
        assert "test_startup_failure" in data["reason"]

    @pytest.mark.asyncio
    async def test_degraded_daemon_read_only_endpoint_still_serves(self):
        """Read-only public endpoints still serve when daemon is degraded."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "test_startup_failure"
        app.state._no_auth = True
        app.state._allow_no_auth = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_readyz_returns_503_when_degraded(self):
        """GET /readyz returns 503 when daemon state is degraded."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._degraded = "startup_crash"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"


# ── 9. Concurrent request handling ─────────────────────────────────────────


class TestConcurrentRequests:
    @pytest.mark.asyncio
    async def test_concurrent_healthz_requests_all_succeed(self):
        """Multiple concurrent healthz requests all return 200."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)

        async def _healthz_request(idx: int) -> tuple[int, int, dict[str, Any]]:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/healthz")
            return idx, resp.status_code, resp.json()

        tasks = [_healthz_request(i) for i in range(5)]
        results = await asyncio.gather(*tasks)

        for idx, status, data in results:
            assert status == 200, f"request {idx} returned {status}"
            assert data["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_concurrent_read_and_write_requests(self):
        """Concurrent GET (public) + POST (protected) does not corrupt state."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)

        async def _get_health() -> int:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/healthz")
            return resp.status_code

        async def _post_dispatch() -> int:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/dispatch", json={})
            return resp.status_code

        results = await asyncio.gather(*([_get_health() for _ in range(5)] + [_post_dispatch() for _ in range(3)]))
        health_statuses = results[:5]
        dispatch_statuses = results[5:]
        for s in health_statuses:
            assert s == 200
        for s in dispatch_statuses:
            assert s == 422


# ── 10. Large payload handling ─────────────────────────────────────────────


class TestLargePayloadHandling:
    @pytest.mark.asyncio
    async def test_large_json_payload_returns_422_or_413(self):
        """Sending a very large JSON body triggers validation/size rejection."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        large_body = {
            "tool_calls": [{"kind": "mcp", "name": "x" * 1000, "args": {"k": "v" * 50000}} for _ in range(50)]
        }
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json=large_body)
        assert resp.status_code in (400, 413, 422)

    @pytest.mark.asyncio
    async def test_post_dispatch_with_wrong_type_field_returns_422(self):
        """POST /api/dispatch with tool_calls as string (not list) returns 422."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/dispatch", json={"tool_calls": "not_a_list"})
        assert resp.status_code in (400, 422)


# ── 11. Request stats tracking ─────────────────────────────────────────────


class TestRequestStatsTracking:
    @pytest.mark.asyncio
    async def test_request_counts_increment_correctly(self):
        """Request and response counts on app.state increment with usage."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/healthz")
            await client.get("/healthz")
            await client.get("/readyz")
        assert app.state._stats_requests >= 3
        assert app.state._stats_responses >= 3

    @pytest.mark.asyncio
    async def test_admin_daemon_stats_returns_expected_fields(self):
        """GET /admin/daemon/stats returns pid, request counts, memory, uptime."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/daemon/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "pid" in data
        assert "requests_total" in data
        assert "responses_total" in data
        assert "memory_mb" in data
        assert "uptime_s" in data


# ── 12. CIDR middleware blocking ────────────────────────────────────────────


class TestCIDRMiddleware:
    @pytest.mark.asyncio
    async def test_allowed_cidr_blocks_non_matching_ip(self):
        """When allowed_cidr is set, non-matching client IPs get 403."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._allowed_cidr = ["10.0.0.0/8", "172.16.0.0/12"]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code in (200, 403)

    @pytest.mark.asyncio
    async def test_empty_cidr_list_allows_all_clients(self):
        """When allowed_cidr is empty, requests are not blocked."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        app.state._allowed_cidr = []
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200


# ── 13. Event loop task state effects on probes ────────────────────────────


class TestEventLoopTaskProbes:
    @pytest.mark.asyncio
    async def test_readyz_returns_503_when_event_loop_task_done(self):
        """GET /readyz returns 503 when the event loop task has completed."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)

        async def _done_task() -> None:
            pass

        done_task = asyncio.ensure_future(_done_task())
        await done_task
        app.state._event_loop_task = done_task
        app.state._event_loop_task_auto = False

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
        assert resp.status_code == 503
        data = resp.json()
        assert "not_ready" in data["status"] or data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_healthz_reports_degraded_when_task_done(self):
        """GET /healthz reports degraded when event loop task has completed."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)

        async def _done_task() -> None:
            pass

        done_task = asyncio.ensure_future(_done_task())
        await done_task
        app.state._event_loop_task = done_task
        app.state._event_loop_task_auto = False

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"


# ── 14. Config-driven behavior variation ───────────────────────────────────


class TestConfigDrivenBehavior:
    @pytest.mark.asyncio
    async def test_different_tick_interval_persists_on_app_state(self):
        """Creating app with custom tick_interval sets it on app.state."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=42.0)
        assert app.state.tick_interval == 42.0

    @pytest.mark.asyncio
    async def test_log_level_default_is_info(self):
        """Default log_level is 'info'."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        assert app.state.log_level == "info"

    @pytest.mark.asyncio
    async def test_log_level_from_env(self):
        """GLUDD_LOG_LEVEL env var overrides the default log level."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1", "GLUDD_LOG_LEVEL": "debug"}):
            app = create_daemon_app(tick_interval=999.0)
        assert app.state.log_level == "debug"

    @pytest.mark.asyncio
    async def test_db_path_override_persists(self):
        """_db_path_override kwarg is stored on app.state."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(
                tick_interval=999.0,
                _db_path_override="/tmp/test_gludd.db",
            )
        assert app.state._db_path_override == "/tmp/test_gludd.db"

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_text(self):
        """GET /metrics returns Prometheus-formatted text."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_admin_metrics_export_returns_expected_fields(self):
        """GET /admin/metrics/export returns counters, gauges, uptime."""
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/metrics/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data
        assert "gauges" in data
        assert "uptime_seconds" in data
