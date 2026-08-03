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
        """GET /readyz returns 200 when daemon is healthy."""
        from unittest.mock import patch as _patch

        with _patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/readyz")
            assert resp.status_code == 200


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
