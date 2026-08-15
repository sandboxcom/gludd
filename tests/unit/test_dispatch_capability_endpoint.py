"""Tests for POST /api/dispatch/capability endpoint wiring."""

from __future__ import annotations

from collections.abc import Callable

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


# ── Travel collection dispatch routing ────────────────────────────────────


def _travel_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.add_collection(
        CollectionMeta(
            name="travel",
            namespace="general_ludd",
            version="0.1.0",
            description=(
                "Travel planning collection — flight search, hotel search, and trip itinerary planning modules."
            ),
            tags=frozenset({"travel", "flights", "hotels", "itinerary", "planning"}),
            raw_tags=["travel", "flights", "hotels", "itinerary", "planning"],
        )
    )
    return reg


class TestTravelDispatchRouting:
    """Verify the travel collection's galaxy.yml tags route correctly through
    the generic POST /api/dispatch/capability endpoint."""

    def test_route_by_travel_tag(self):
        reg = _travel_registry()
        client = _make_client(reg)
        resp = client.post("/api/dispatch/capability", json={"capability": "travel"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["capability"] == "travel"
        assert len(data["matches"]) == 1
        assert data["matches"][0]["collection"] == "travel"
        assert data["matches"][0]["namespace"] == "general_ludd"
        assert data["matches"][0]["score"] == 1.0

    def test_route_by_flights_tag(self):
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "flights"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["matches"][0]["collection"] == "travel"

    def test_route_by_hotels_tag(self):
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "hotels"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["matches"][0]["collection"] == "travel"

    def test_route_by_itinerary_tag(self):
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "itinerary"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["matches"][0]["collection"] == "travel"

    def test_route_by_planning_tag(self):
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "planning"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["matches"][0]["collection"] == "travel"

    def test_travel_tag_routes_via_collection_lookup(self):
        """Direct collection-lookup also works for travel."""
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"collection": "travel"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["matches"]) == 1
        assert data["matches"][0]["collection"] == "travel"

    def test_list_capabilities_includes_travel_tags(self):
        client = _make_client(_travel_registry())
        resp = client.get("/api/dispatch/capabilities")
        data = resp.json()
        for tag in ("travel", "flights", "hotels", "itinerary", "planning"):
            assert tag in data["capabilities"], f"missing {tag}"

    def test_registry_endpoint_exposes_travel(self):
        client = _make_client(_travel_registry())
        resp = client.get("/api/dispatch/capability/registry")
        data = resp.json()
        assert "travel" in data["collections"]
        assert data["tag_index"]["travel"] == ["travel"]
        assert data["tag_index"]["flights"] == ["travel"]

    def test_route_travel_with_payload(self):
        client = _make_client(_travel_registry())
        payload = {
            "origin": "SFO",
            "destination": "NRT",
            "departure_date": "2026-09-01",
            "return_date": "2026-09-15",
        }
        resp = client.post(
            "/api/dispatch/capability",
            json={"capability": "travel", "payload": payload},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["payload"] == payload

    def test_unknown_travel_related_tag_returns_not_found(self):
        """A tag not in the travel collection returns ok=False."""
        client = _make_client(_travel_registry())
        resp = client.post("/api/dispatch/capability", json={"capability": "vacation"})
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] is not None


# ── POST /api/dispatch capability-based dispatch ──────────────────────────


def _make_cap_dispatch_client(
    registry: CapabilityRegistry | None = None,
    collection_handler: Callable[[str, dict[str, object]], object] | None = None,
):
    """Build a TestClient with both capability_registry and a mock collection_handler."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE
    from general_ludd.routers.dispatch import register

    app = FastAPI()
    register(
        app,
        {},
        capability_registry=registry,
        collection_handler=collection_handler,
        role=UNRESTRICTED_ROLE,
    )
    return TestClient(app, raise_server_exceptions=False)


def _mock_collection_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {"invoked": name, "args": args, "status": "ok"}


class TestCapabilityBasedDispatch:
    def test_dispatch_by_capability_and_action(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "capability": "travel",
                "action": "flight_search",
                "args": {"origin": "SFO", "destination": "NRT"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["error_count"] == 0
        result = data["results"][0]
        assert result["ok"] is True
        assert result["kind"] == "collection"
        assert result["name"] == "general_ludd.travel.flight_search"
        output = result["output"]
        assert output["invoked"] == "general_ludd.travel.flight_search"
        assert output["args"] == {"origin": "SFO", "destination": "NRT"}

    def test_dispatch_by_flights_tag(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "flights", "action": "flight_search", "args": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["name"] == "general_ludd.travel.flight_search"

    def test_dispatch_by_hotels_tag(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "hotels", "action": "hotel_search", "args": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.travel.hotel_search"

    def test_dispatch_by_itinerary_tag(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "capability": "itinerary",
                "action": "trip_planner",
                "args": {
                    "origin": "SFO",
                    "destinations": ["NRT"],
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-15",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["name"] == "general_ludd.travel.trip_planner"

    def test_dispatch_missing_action_returns_422(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post("/api/dispatch", json={"capability": "travel"})
        assert resp.status_code == 422

    def test_dispatch_unknown_capability_returns_404(self):
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "vacation", "action": "search", "args": {}},
        )
        assert resp.status_code == 404

    def test_dispatch_no_registry_returns_503(self):
        client = _make_cap_dispatch_client(registry=None)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "travel", "action": "search"},
        )
        assert resp.status_code == 503

    def test_dispatch_empty_capability_falls_through_to_tool_calls_parse(self):
        """Empty string capability is falsy, falls through to parse_tool_calls (422)."""
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post("/api/dispatch", json={"capability": "", "action": "search"})
        assert resp.status_code == 422

    def test_capability_dispatch_still_allows_kind_based_dispatch(self):
        """When capability is absent, the endpoint handles kind-based dispatch normally."""
        reg = _travel_registry()
        client = _make_cap_dispatch_client(reg, collection_handler=_mock_collection_handler)
        resp = client.post(
            "/api/dispatch",
            json={"kind": "collection", "name": "general_ludd.travel.flight_search", "args": {"origin": "SFO"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["name"] == "general_ludd.travel.flight_search"
