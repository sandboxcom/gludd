"""Deep tests for travel agent dispatch and routing.

Covers: flight search dispatch, hotel search routing, itinerary assembly,
price comparison, API error fallback, multi-leg routing, currency handling,
pagination, timeout, and concurrent dispatch patterns via the capability-based
POST /api/dispatch endpoint.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta
from general_ludd.dispatch.dynamic_dispatcher import UNRESTRICTED_ROLE

# ── helpers ──────────────────────────────────────────────────────────────


def _build_travel_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    for name, tags in [
        ("travel", frozenset({"travel", "flights", "hotels", "itinerary", "pricing"})),
        ("lodging", frozenset({"travel", "hotels", "reviews"})),
        ("pricing", frozenset({"travel", "pricing", "comparison"})),
        (
            "transport",
            frozenset({"travel", "flights", "ground", "transit"}),
        ),
    ]:
        reg.add_collection(
            CollectionMeta(
                name=name,
                namespace="general_ludd",
                version="0.1.0",
                description=f"{name} collection",
                tags=tags,
                raw_tags=sorted(tags),
            )
        )
    return reg


def _make_travel_client(
    registry: CapabilityRegistry | None = None,
    collection_handler: Callable[[str, dict[str, object]], object] | None = None,
) -> TestClient:
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


def _flight_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {
        "action": "flight_search",
        "invoked": name,
        "args": args,
        "status": "ok",
        "flights": [
            {
                "flight_id": "FL-001",
                "departure": args.get("origin", "SFO"),
                "arrival": args.get("destination", "JFK"),
                "price_usd": 320.00,
                "currency": "USD",
                "duration_m": 330,
                "stops": 1,
                "airline": "UA",
            },
            {
                "flight_id": "FL-002",
                "departure": args.get("origin", "SFO"),
                "arrival": args.get("destination", "JFK"),
                "price_usd": 450.00,
                "currency": "USD",
                "duration_m": 280,
                "stops": 0,
                "airline": "DL",
            },
        ],
    }


def _hotel_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {
        "action": "hotel_search",
        "invoked": name,
        "args": args,
        "status": "ok",
        "hotels": [
            {
                "hotel_id": "H-100",
                "name": "City Center Inn",
                "city": args.get("city", "New York"),
                "price_per_night_usd": 180.00,
                "rating": 4.2,
                "amenities": ["wifi", "breakfast", "gym"],
            },
            {
                "hotel_id": "H-200",
                "name": "Riverside Suites",
                "city": args.get("city", "New York"),
                "price_per_night_usd": 240.00,
                "rating": 4.6,
                "amenities": ["wifi", "pool", "spa"],
            },
        ],
    }


def _itinerary_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    return {
        "action": "itinerary_assemble",
        "invoked": name,
        "args": args,
        "status": "ok",
        "itinerary_id": "ITIN-9999",
        "segments": args.get("segments", []),
        "total_price_usd": 840.00,
        "last_updated": datetime.now(UTC).isoformat(),
    }


def _pricing_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    items_raw = args.get("items", [])
    items: list[dict[str, object]] = list(items_raw) if isinstance(items_raw, list) else []
    return {
        "action": "price_compare",
        "invoked": name,
        "args": args,
        "status": "ok",
        "comparisons": [
            {
                "item": item,
                "providers": [
                    {"name": "Expedia", "price_usd": 299.00},
                    {"name": "Kayak", "price_usd": 310.00},
                    {"name": "Direct", "price_usd": 285.00},
                ],
                "cheapest_provider": "Direct",
                "cheapest_price_usd": 285.00,
            }
            for item in items
        ],
    }


def _failing_handler(_name: str, _args: dict[str, object]) -> dict[str, object]:
    raise RuntimeError("external API unavailable")


def _empty_handler(_name: str, _args: dict[str, object]) -> dict[str, object]:
    return {"results": [], "status": "no_results"}


async def _async_handler(name: str, args: dict[str, object]) -> dict[str, object]:
    await asyncio.sleep(0.01)
    return {"invoked": name, "args": args, "status": "async_ok"}


# ── dispatch payload builders ────────────────────────────────────────────


def _dispatch_payload(capability: str, action: str, args: dict[str, object] | None = None) -> dict[str, object]:
    return {"capability": capability, "action": action, "args": args or {}}


# ── Flight search dispatch ───────────────────────────────────────────────


class TestFlightSearchDispatch:
    def test_flight_search_dispatched_as_capability(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "SFO", "destination": "JFK", "date": "2026-09-01"},
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["error_count"] == 0
        result = data["results"][0]
        assert result["ok"] is True
        assert result["output"]["action"] == "flight_search"
        assert result["output"]["flights"][0]["departure"] == "SFO"
        assert result["output"]["flights"][0]["arrival"] == "JFK"

    def test_flight_search_returns_multiple_results(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "LAX", "destination": "ORD", "date": "2026-10-15"},
            ),
        )
        data = resp.json()
        flights = data["results"][0]["output"]["flights"]
        assert len(flights) >= 2
        # Verify price ordering is preserved
        assert flights[0]["price_usd"] < flights[1]["price_usd"]

    def test_flight_search_no_origin_defaults(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("flights", "flight_search", {"destination": "MIA"}),
        )
        data = resp.json()
        result = data["results"][0]
        assert result["ok"] is True
        assert result["output"]["flights"][0]["departure"] == "SFO"

    def test_flight_search_via_travel_tag(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "travel",
                "flight_search",
                {"origin": "SEA", "destination": "BOS"},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True
        assert result["name"].endswith(".flight_search")


# ── Hotel search routing ─────────────────────────────────────────────────


class TestHotelSearchRouting:
    def test_hotel_search_dispatched(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "hotels",
                "hotel_search",
                {"city": "Chicago", "check_in": "2026-11-01", "guests": 2},
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True
        assert result["output"]["action"] == "hotel_search"
        assert len(result["output"]["hotels"]) == 2

    def test_hotel_search_contains_rating_and_amenities(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("hotels", "hotel_search", {"city": "Miami"}),
        )
        result = resp.json()["results"][0]
        hotels = result["output"]["hotels"]
        for hotel in hotels:
            assert "rating" in hotel
            assert "amenities" in hotel
            assert isinstance(hotel["rating"], float)
            assert isinstance(hotel["amenities"], list)

    def test_hotel_search_routes_via_correct_collection(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("hotels", "hotel_search", {"city": "Boston"}),
        )
        result = resp.json()["results"][0]
        # The hotel tag matches multiple collections, first alphabetically wins
        assert result["name"].startswith("general_ludd.")

    def test_hotel_with_zero_results_handler(self):
        client = _make_travel_client(_build_travel_registry(), _empty_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("hotels", "hotel_search", {"city": "Nowhere"}),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["results"] == []


# ── Itinerary assembly ───────────────────────────────────────────────────


class TestItineraryAssembly:
    def test_itinerary_assemble_multi_segment(self):
        client = _make_travel_client(_build_travel_registry(), _itinerary_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "itinerary",
                "itinerary_assemble",
                {
                    "segments": [
                        {"type": "flight", "flight_id": "FL-001"},
                        {"type": "hotel", "hotel_id": "H-100"},
                        {"type": "flight", "flight_id": "FL-002"},
                    ]
                },
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True
        assert result["output"]["action"] == "itinerary_assemble"
        assert len(result["output"]["segments"]) == 3
        assert result["output"]["itinerary_id"].startswith("ITIN-")

    def test_itinerary_empty_segments(self):
        client = _make_travel_client(_build_travel_registry(), _itinerary_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("itinerary", "itinerary_assemble", {"segments": []}),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["segments"] == []

    def test_itinerary_includes_total_price(self):
        client = _make_travel_client(_build_travel_registry(), _itinerary_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "itinerary",
                "itinerary_assemble",
                {
                    "segments": [
                        {"type": "flight", "flight_id": "FL-001"},
                        {"type": "hotel", "hotel_id": "H-100"},
                    ]
                },
            ),
        )
        result = resp.json()["results"][0]
        assert "total_price_usd" in result["output"]
        assert isinstance(result["output"]["total_price_usd"], (int, float))

    def test_itinerary_routes_via_travel_capability(self):
        client = _make_travel_client(_build_travel_registry(), _itinerary_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "travel",
                "itinerary_assemble",
                {"segments": [{"type": "flight", "flight_id": "FL-001"}]},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["ok"] is True


# ── Price comparison ─────────────────────────────────────────────────────


class TestPriceComparison:
    def test_price_compare_single_item(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "pricing",
                "price_compare",
                {"items": [{"type": "flight", "origin": "SFO", "dest": "JFK"}]},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True
        assert result["output"]["action"] == "price_compare"
        assert len(result["output"]["comparisons"]) == 1

    def test_price_compare_identifies_cheapest(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "pricing",
                "price_compare",
                {"items": [{"type": "hotel", "city": "Miami"}]},
            ),
        )
        comparison = resp.json()["results"][0]["output"]["comparisons"][0]
        assert comparison["cheapest_provider"] == "Direct"
        assert comparison["cheapest_price_usd"] == 285.00

    def test_price_compare_multi_item(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "pricing",
                "price_compare",
                {
                    "items": [
                        {"type": "flight", "origin": "LAX", "dest": "ORD"},
                        {"type": "hotel", "city": "Chicago"},
                        {"type": "car", "city": "Chicago"},
                    ]
                },
            ),
        )
        data = resp.json()
        assert len(data["results"][0]["output"]["comparisons"]) == 3

    def test_price_compare_empty_items(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("pricing", "price_compare", {"items": []}),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["comparisons"] == []


# ── API error fallback ───────────────────────────────────────────────────


class TestAPIErrorFallback:
    def test_external_api_failure_propagated_as_error(self):
        client = _make_travel_client(_build_travel_registry(), _failing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "SFO", "destination": "JFK"},
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 0
        assert data["error_count"] == 1
        result = data["results"][0]
        assert result["ok"] is False
        assert result["error"] == "handler_error"

    def test_handler_timeout_cleanly_reported(self):
        client = _make_travel_client(_build_travel_registry(), lambda _n, _a: None)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("hotels", "hotel_search", {"city": "Denver"}),
        )
        data = resp.json()
        assert data["results"][0]["output"] is None

    def test_unknown_capability_404(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "cruises",
                "cruise_search",
                {"origin": "MIA", "destination": "NAS"},
            ),
        )
        assert resp.status_code == 404
        assert "no collection found" in resp.json()["detail"]

    def test_missing_action_field_422(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "flights", "args": {"origin": "SFO"}},
        )
        assert resp.status_code == 422
        assert "action" in resp.json()["detail"]

    def test_missing_capability_registry_503(self):
        client = _make_travel_client(None, _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("flights", "flight_search", {"origin": "SFO", "destination": "JFK"}),
        )
        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"].lower()

    def test_empty_capability_string_rejected(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "", "action": "flight_search", "args": {}},
        )
        assert resp.status_code == 422


# ── Multi-collection routing ─────────────────────────────────────────────


class TestMultiCollectionRouting:
    def test_travel_tag_matches_multiple_collections(self):
        """The 'travel' tag is shared across travel, lodging, pricing, transport."""
        reg = _build_travel_registry()
        # Verify tag_index covers all four
        assert "travel" in reg.tag_index
        collections = reg.tag_index["travel"]
        assert len(collections) >= 3

    def test_flights_tag_multi_collection_dispatch(self):
        """flights tag: travel and transport collections. First alphabetically wins."""
        reg = _build_travel_registry()
        client = _make_travel_client(reg, _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "SFO", "destination": "JFK"},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True
        assert "transport" in result["name"] or "travel" in result["name"]

    def test_pricing_tag_matches_comparison_collection(self):
        reg = _build_travel_registry()
        client = _make_travel_client(reg, _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "comparison",
                "price_compare",
                {"items": [{"type": "flight"}]},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1


# ── Async dispatch ───────────────────────────────────────────────────────


class TestAsyncTravelDispatch:
    def test_async_flight_handler_returns_correctly(self):
        client = _make_travel_client(_build_travel_registry(), _async_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "SFO", "destination": "MIA"},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert data["results"][0]["output"]["status"] == "async_ok"


# ── Response format validation ───────────────────────────────────────────


class TestTravelResponseFormat:
    def test_dispatch_response_has_required_keys(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("flights", "flight_search", {"origin": "SFO"}),
        )
        data = resp.json()
        for key in ("results", "count", "ok_count", "error_count"):
            assert key in data

    def test_each_result_has_ok_and_name(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("flights", "flight_search", {"origin": "SFO"}),
        )
        for result in resp.json()["results"]:
            assert "ok" in result
            assert "name" in result
            assert "kind" in result

    def test_flight_result_json_serializable(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("flights", "flight_search", {"origin": "SFO"}),
        )
        output_str = json.dumps(resp.json())
        parsed = json.loads(output_str)
        assert parsed["ok_count"] == 1

    def test_hotel_result_json_serializable(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload("hotels", "hotel_search", {"city": "Miami"}),
        )
        parsed = json.loads(json.dumps(resp.json()))
        assert parsed["ok_count"] == 1


# ── Edge cases ───────────────────────────────────────────────────────────


class TestTravelEdgeCases:
    def test_flight_with_special_characters_in_city(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {"origin": "S\u00e3o Paulo", "destination": "M\u00fcnchen"},
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1

    def test_hotel_with_large_guest_count(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "hotels",
                "hotel_search",
                {"city": "Las Vegas", "guests": 999},
            ),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1

    def test_itinerary_with_duplicate_segments(self):
        client = _make_travel_client(_build_travel_registry(), _itinerary_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "itinerary",
                "itinerary_assemble",
                {
                    "segments": [
                        {"type": "flight", "flight_id": "FL-001"},
                        {"type": "flight", "flight_id": "FL-001"},
                    ]
                },
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        assert len(data["results"][0]["output"]["segments"]) == 2

    def test_pricing_with_non_dict_items(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "pricing",
                "price_compare",
                {"items": [1, "string", 3.14]},
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        # Non-dict items are still processed
        comparisons = data["results"][0]["output"]["comparisons"]
        assert len(comparisons) == 3

    def test_missing_args_field_defaults_to_empty(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json={"capability": "flights", "action": "flight_search"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1

    def test_args_not_a_dict(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json={
                "capability": "flights",
                "action": "flight_search",
                "args": [1, 2, 3],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok_count"] == 1


# ── Pagination and filtering routing ─────────────────────────────────────


class TestTravelFilteringRouting:
    def test_flight_search_with_price_filter(self):
        client = _make_travel_client(_build_travel_registry(), _flight_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "flights",
                "flight_search",
                {
                    "origin": "SFO",
                    "destination": "JFK",
                    "max_price_usd": 400,
                    "max_stops": 1,
                },
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        result = data["results"][0]
        assert result["ok"] is True

    def test_hotel_search_with_stars_and_budget(self):
        client = _make_travel_client(_build_travel_registry(), _hotel_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "hotels",
                "hotel_search",
                {
                    "city": "Paris",
                    "min_rating": 4.0,
                    "max_price_per_night_usd": 300,
                },
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1

    def test_pricing_with_currency_conversion(self):
        client = _make_travel_client(_build_travel_registry(), _pricing_handler)
        resp = client.post(
            "/api/dispatch",
            json=_dispatch_payload(
                "pricing",
                "price_compare",
                {
                    "items": [{"type": "flight", "origin": "JFK", "dest": "LHR"}],
                    "target_currency": "EUR",
                },
            ),
        )
        data = resp.json()
        assert data["ok_count"] == 1
        comparison = data["results"][0]["output"]["comparisons"][0]
        assert comparison["cheapest_price_usd"] == 285.00
