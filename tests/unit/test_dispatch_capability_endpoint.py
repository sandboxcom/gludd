"""Tests for POST /api/dispatch/capability endpoint wiring."""

from __future__ import annotations

from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta


def _build_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.add_collection(
        CollectionMeta(
            name="agent",
            namespace="general_ludd",
            version="0.1.0",
            description="Agent roles",
            tags=frozenset({"deploy", "plan", "build"}),
            raw_tags=["deploy", "plan", "build"],
        )
    )
    reg.add_collection(
        CollectionMeta(
            name="infra",
            namespace="general_ludd",
            version="0.1.0",
            description="Infra tooling",
            tags=frozenset({"deploy", "monitor"}),
            raw_tags=["deploy", "monitor"],
        )
    )
    return reg


def _make_client(registry: CapabilityRegistry | None = None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from general_ludd.routers.dispatch import register

    app = FastAPI()
    register(app, {}, capability_registry=registry)
    return TestClient(app, raise_server_exceptions=False)


# ── POST /api/dispatch/capability ────────────────────────────────────────


class TestCapabilityEndpointRoute:
    def test_route_by_capability_tag_found(self):
        """A known tag returns ok=True with matching collections."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": "plan"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["capability"] == "plan"
        assert len(data["matches"]) == 1
        assert data["matches"][0]["collection"] == "agent"
        assert data["error"] is None

    def test_route_by_capability_tag_multiple_matches(self):
        """A tag shared by two collections returns both."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": "deploy"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["matches"]) == 2

    def test_route_by_capability_tag_not_found(self):
        """An unknown tag returns ok=False with an error."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": "unknown"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] is not None

    def test_route_by_collection_name_found(self):
        """A known collection name returns ok=True."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"collection": "agent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["matches"]) == 1
        assert data["matches"][0]["collection"] == "agent"

    def test_route_by_collection_name_not_found(self):
        """An unknown collection name returns ok=False."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"collection": "nonexistent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_route_with_payload(self):
        """Payload is forwarded in the result."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post(
            "/api/dispatch/capability",
            json={"capability": "plan", "payload": {"key": "value"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["payload"] == {"key": "value"}

    def test_route_missing_capability_and_collection_422(self):
        """Body with neither capability nor collection returns 422."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={})
        assert resp.status_code == 422

    def test_route_empty_capability_string(self):
        """Empty capability string returns ok=False."""
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False


# ── GET /api/dispatch/capabilities ────────────────────────────────────────


class TestCapabilitiesListEndpoint:
    def test_lists_all_tags_sorted(self):
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.get("/api/dispatch/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["capabilities"]) == data["capabilities"]
        assert "deploy" in data["capabilities"]
        assert "plan" in data["capabilities"]
        assert "build" in data["capabilities"]
        assert "monitor" in data["capabilities"]

    def test_empty_registry_returns_no_capabilities(self):
        reg = CapabilityRegistry()
        client = _make_client(reg)
        resp = client.get("/api/dispatch/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["capabilities"] == []


# ── GET /api/dispatch/capability/registry ─────────────────────────────────


class TestCapabilityRegistryEndpoint:
    def test_returns_full_registry_dict(self):
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.get("/api/dispatch/capability/registry")
        assert resp.status_code == 200
        data = resp.json()
        assert "collections" in data
        assert "tag_index" in data
        assert len(data["collections"]) == 2
        assert "agent" in data["collections"]
        assert "infra" in data["collections"]

    def test_tag_index_maps_correctly(self):
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.get("/api/dispatch/capability/registry")
        data = resp.json()
        assert "deploy" in data["tag_index"]
        assert sorted(data["tag_index"]["deploy"]) == ["agent", "infra"]
        assert data["tag_index"]["plan"] == ["agent"]
        assert data["tag_index"]["monitor"] == ["infra"]


# ── No registry → endpoints not registered ────────────────────────────────


class TestNoRegistry:
    def test_capability_endpoints_404_when_no_registry(self):
        """When capability_registry=None, the endpoints are not registered."""
        client = _make_client(registry=None)
        resp = client.post("/api/dispatch/capability", json={"capability": "deploy"})
        assert resp.status_code == 404
        resp = client.get("/api/dispatch/capabilities")
        assert resp.status_code == 404
        resp = client.get("/api/dispatch/capability/registry")
        assert resp.status_code == 404


# ── Integration: round-trip through registry ─────────────────────────────


class TestRegistryRoundTrip:
    def test_from_dict_to_dict_preserves_data(self):
        reg = _build_registry()
        serialized = reg.to_dict()
        restored = CapabilityRegistry.from_dict(serialized)
        assert restored.to_dict() == serialized
        client = _make_client(restored)
        resp = client.post("/api/dispatch/capability", json={"capability": "plan"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_route_match_includes_namespace_and_score(self):
        reg = _build_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": "build"})
        data = resp.json()
        assert data["matches"][0]["namespace"] == "general_ludd"
        assert data["matches"][0]["score"] == 1.0
