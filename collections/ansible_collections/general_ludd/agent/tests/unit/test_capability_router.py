"""
Unit tests for capability_router module_utils.

Tests dispatch, list_capabilities, and register_capability against
in-process CapabilityRouter (delegates to general_ludd.dispatch).
No daemon HTTP transport — routing is purely in-process.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(
    0,
    "collections/ansible_collections/general_ludd/agent/plugins/module_utils",
)
from capability_router import (  # type: ignore[import]
    CapabilityDispatchError,
    DEFAULT_DAEMON_URL,
    DEFAULT_TIMEOUT,
    DISPATCH_AVAILABLE_ENDPOINT,
    DISPATCH_ENDPOINT,
    clear_registry,
    dispatch,
    get_registry,
    list_capabilities,
    register_capability,
)

DAEMON_URL = "http://localhost:8000"
FAKE_PSK = "test-psk-123"


# ---------------------------------------------------------------------------
# Test helpers — lightweight RouteResult / RouteMatch fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeRouteMatch:
    name: str
    score: float = 1.0


@dataclass
class FakeRouteResult:
    ok: bool
    capability: str = ""
    matches: list[Any] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _mock_router(**kwargs: Any) -> MagicMock:
    """Build a mock router with route() and list_capabilities()."""
    router = MagicMock()
    router.route.return_value = kwargs.get("route_result", FakeRouteResult(ok=False))
    router.list_capabilities.return_value = kwargs.get("capabilities", [])
    return router


# ---------------------------------------------------------------------------
# TestDispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatches_capability_via_router(self):
        match = FakeRouteMatch(name="agentic_collection", score=1.0)
        route_result = FakeRouteResult(
            ok=True,
            capability="agentic",
            matches=[match],
        )
        router = _mock_router(route_result=route_result)

        with patch("capability_router._build", return_value=router):
            result = dispatch("agentic", {"task": "build"})

        router.route.assert_called_once_with("agentic", {"task": "build"})
        assert result["ok_count"] == 1
        assert result["count"] == 1
        assert result["error_count"] == 0
        assert result["results"][0]["capability"] == "agentic"
        assert result["results"][0]["collection"] == "agentic_collection"
        assert result["results"][0]["ok"] is True

    def test_dispatch_default_payload_empty_dict(self):
        match = FakeRouteMatch(name="c", score=1.0)
        route_result = FakeRouteResult(ok=True, capability="tag", matches=[match])
        router = _mock_router(route_result=route_result)

        with patch("capability_router._build", return_value=router):
            dispatch("tag", None)

        router.route.assert_called_once_with("tag", {})

    def test_dispatch_empty_capability_raises(self):
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            dispatch("", {})

    def test_dispatch_none_capability_raises(self):
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            dispatch(None, {})  # type: ignore[arg-type]

    def test_dispatch_router_not_available(self):
        with patch("capability_router._build", return_value=None):
            result = dispatch("tag", {})

        assert result == {"results": [], "count": 0, "ok_count": 0, "error_count": 1}

    def test_dispatch_ok_with_multiple_matches(self):
        matches = [
            FakeRouteMatch(name="coll_a", score=0.9),
            FakeRouteMatch(name="coll_b", score=0.5),
        ]
        route_result = FakeRouteResult(ok=True, capability="shared", matches=matches)
        router = _mock_router(route_result=route_result)

        with patch("capability_router._build", return_value=router):
            result = dispatch("shared", {"key": "val"})

        assert result["ok_count"] == 2
        assert result["count"] == 2
        assert result["error_count"] == 0
        names = [r["collection"] for r in result["results"]]
        assert names == ["coll_a", "coll_b"]

    def test_dispatch_not_ok_returns_error(self):
        route_result = FakeRouteResult(
            ok=False,
            capability="nonexistent",
            error="no collection found",
        )
        router = _mock_router(route_result=route_result)

        with patch("capability_router._build", return_value=router):
            result = dispatch("nonexistent", {})

        assert result["ok_count"] == 0
        assert result["count"] == 0
        assert result["error_count"] == 1


# ---------------------------------------------------------------------------
# TestListCapabilities
# ---------------------------------------------------------------------------


class TestListCapabilities:
    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_lists_from_router(self):
        router = _mock_router(capabilities=["agentic", "mcp", "planner"])

        with patch("capability_router._build", return_value=router):
            caps = list_capabilities()

        assert "agentic" in caps
        assert "mcp" in caps
        assert "planner" in caps

    def test_includes_local_registry_entries(self):
        register_capability("local_only")
        router = _mock_router(capabilities=["daemon_cap"])

        with patch("capability_router._build", return_value=router):
            caps = list_capabilities()

        assert "local_only" in caps
        assert "daemon_cap" in caps

    def test_deduplicates_across_sources(self):
        register_capability("shared_cap")
        router = _mock_router(capabilities=["shared_cap", "other_cap"])

        with patch("capability_router._build", return_value=router):
            caps = list_capabilities()

        assert caps.count("shared_cap") == 1
        assert "other_cap" in caps

    def test_router_not_available_local_only(self):
        with patch("capability_router._build", return_value=None):
            register_capability("local_a")
            register_capability("local_b")
            caps = list_capabilities()

        assert caps == ["local_a", "local_b"]

    def test_router_error_graceful_fallback(self):
        router = _mock_router()
        router.list_capabilities.side_effect = RuntimeError("boom")

        with patch("capability_router._build", return_value=router):
            register_capability("safe")
            caps = list_capabilities()

        assert caps == ["safe"]

    def test_returns_sorted_list(self):
        router = _mock_router(capabilities=["zulu", "alpha", "mike"])

        with patch("capability_router._build", return_value=router):
            caps = list_capabilities()

        assert caps == sorted(caps)
        assert caps == ["alpha", "mike", "zulu"]

    def test_handles_empty_router_response(self):
        router = _mock_router(capabilities=[])

        with patch("capability_router._build", return_value=router):
            caps = list_capabilities()

        assert caps == []


# ---------------------------------------------------------------------------
# TestRegisterCapability
# ---------------------------------------------------------------------------


class TestRegisterCapability:
    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_registers_locally(self):
        result = register_capability(
            "my_cap",
            roles=["coder", "operator"],
            model_needs={"min_tokens": 1024},
        )

        assert result["name"] == "my_cap"
        assert result["roles"] == ["coder", "operator"]
        assert result["model_needs"] == {"min_tokens": 1024}
        assert result["registered"] is True

        registry = get_registry()
        assert "my_cap" in registry
        assert registry["my_cap"]["roles"] == ["coder", "operator"]

    def test_default_args(self):
        result = register_capability("bare_cap")

        assert result["roles"] == []
        assert result["model_needs"] == {}

    def test_empty_name_raises(self):
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            register_capability("")

    def test_none_name_raises(self):
        with pytest.raises(CapabilityDispatchError, match="non-empty string"):
            register_capability(None)  # type: ignore[arg-type]

    def test_multiple_registrations_accumulate(self):
        register_capability("a")
        register_capability("b")

        registry = get_registry()
        assert "a" in registry
        assert "b" in registry

    def test_reregister_updates_existing(self):
        register_capability("cap", roles=["coder"])
        register_capability("cap", roles=["operator"])

        registry = get_registry()
        assert len(registry) == 1
        assert registry["cap"]["roles"] == ["operator"]


# ---------------------------------------------------------------------------
# TestRegistryManagement
# ---------------------------------------------------------------------------


class TestRegistryManagement:
    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_get_registry_returns_copy(self):
        register_capability("a")

        r1 = get_registry()
        r2 = get_registry()
        assert r1 is not r2
        assert r1 == r2

    def test_clear_registry_empties_state(self):
        register_capability("a")

        assert len(get_registry()) == 1
        clear_registry()
        assert len(get_registry()) == 0


# ---------------------------------------------------------------------------
# TestCapabilityDispatchError
# ---------------------------------------------------------------------------


class TestCapabilityDispatchError:
    def test_is_exception(self):
        assert issubclass(CapabilityDispatchError, Exception)

    def test_message_preserved(self):
        exc = CapabilityDispatchError("test message")
        assert str(exc) == "test message"

    def test_can_be_caught_as_exception(self):
        with pytest.raises(CapabilityDispatchError):
            raise CapabilityDispatchError("caught")


# ---------------------------------------------------------------------------
# TestDefaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_daemon_url(self):
        assert DEFAULT_DAEMON_URL == "http://localhost:8000"

    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT == 30

    def test_default_endpoints(self):
        assert DISPATCH_ENDPOINT == "/api/dispatch"
        assert DISPATCH_AVAILABLE_ENDPOINT == "/api/dispatch/available"
