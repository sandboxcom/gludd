"""Tests for the travel core module (general_ludd.travel).

Covers the 6 user-visible functions:
  plan_trip, search_flights, search_hotels, optimize_multi_stop,
  estimate_budget, validate_travel_docs.
"""

from __future__ import annotations

from datetime import date

from general_ludd.travel.core import (
    INCOMPLETE_INPUT,
    NO_RESULTS,
    UNSUPPORTED_MODE,
    estimate_budget,
    optimize_multi_stop,
    plan_trip,
    search_flights,
    search_hotels,
    validate_travel_docs,
)


def _make_trip_request(**overrides):
    defaults = {
        "origin": "NYC",
        "destination": "LON",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 10),
        "travelers": [],
        "budget": {"currency": "USD", "total": 5000.0},
        "preferences": {"max_stops": 2},
    }
    defaults.update(overrides)
    return defaults


class TestPlanTrip:
    def test_plan_trip_returns_structured_result(self):
        req = _make_trip_request()
        result = plan_trip(req)
        assert "trip_id" in result
        assert result["origin"] == "NYC"
        assert result["destination"] == "LON"
        assert "segments" in result
        assert "total_estimated_cost" in result

    def test_plan_trip_warns_on_zero_budget(self):
        req = _make_trip_request(budget={"currency": "USD", "total": 0.0})
        result = plan_trip(req)
        assert "warnings" in result
        assert any("budget" in w.lower() for w in result["warnings"])

    def test_plan_trip_validates_dates(self):
        req = _make_trip_request(end_date=date(2026, 8, 1))
        result = plan_trip(req)
        assert "errors" in result
        assert any("end_date" in e.lower() or "before" in e.lower() for e in result["errors"])

    def test_plan_trip_no_travelers_is_valid(self):
        req = _make_trip_request()
        result = plan_trip(req)
        assert result.get("state") == "draft"


class TestSearchFlights:
    def test_search_flights_returns_list(self):
        results = search_flights(
            origin="JFK",
            destination="LHR",
            departure_date=date(2026, 9, 1),
            passengers=1,
        )
        assert isinstance(results, list)
        assert len(results) > 0
        for flight in results:
            assert "flight_number" in flight
            assert "airline" in flight
            assert flight["departure_airport"] == "JFK"
            assert flight["arrival_airport"] == "LHR"

    def test_search_flights_respects_max_connections(self):
        results = search_flights(
            origin="SFO",
            destination="NRT",
            departure_date=date(2026, 9, 1),
            passengers=2,
            max_connections=0,
        )
        for flight in results:
            assert flight.get("stops", 0) <= 0

    def test_search_flights_empty_for_missing_origin(self):
        results = search_flights(
            origin="",
            destination="LHR",
            departure_date=date(2026, 9, 1),
            passengers=1,
        )
        assert results == []

    def test_search_flights_accepts_cabin_class(self):
        results = search_flights(
            origin="JFK",
            destination="LHR",
            departure_date=date(2026, 9, 1),
            passengers=1,
            cabin_class="business",
        )
        assert len(results) > 0
        assert results[0]["cabin_class"] == "business"

    def test_empty_results_for_unsupported_route(self):
        result = search_flights(
            origin="ZZZ",
            destination="YYY",
            departure_date=date(2026, 9, 1),
            passengers=1,
        )
        assert result == NO_RESULTS


class TestSearchHotels:
    def test_search_hotels_returns_list(self):
        results = search_hotels(
            location="NYC",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
            guests=2,
        )
        assert isinstance(results, list)
        assert len(results) > 0
        for hotel in results:
            assert "hotel_name" in hotel
            assert "price_per_night" in hotel

    def test_search_hotels_accepts_rooms(self):
        results = search_hotels(
            location="NYC",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
            guests=2,
            rooms=2,
        )
        assert len(results) > 0

    def test_search_hotels_empty_for_empty_location(self):
        results = search_hotels(
            location="",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
            guests=1,
        )
        assert results == []

    def test_empty_for_unsupported_location(self):
        result = search_hotels(
            location="ATLANTIS",
            check_in=date(2026, 9, 1),
            check_out=date(2026, 9, 5),
            guests=1,
        )
        assert result == NO_RESULTS


class TestOptimizeMultiStop:
    def test_optimize_returns_route_dict(self):
        stops = [
            {"city": "NYC", "country": "USA", "arrival_mode": "start"},
            {"city": "LON", "country": "GBR", "arrival_mode": "flight"},
            {"city": "PAR", "country": "FRA", "arrival_mode": "train"},
        ]
        result = optimize_multi_stop(stops)
        assert "route_id" in result
        assert result["optimized"] is True
        assert "segments" in result
        assert result["total_cost"] >= 0.0

    def test_single_stop_returns_unoptimized(self):
        stops = [{"city": "NYC", "country": "USA", "arrival_mode": "start"}]
        result = optimize_multi_stop(stops)
        assert result["optimized"] is False
        assert "reason" in result

    def test_optimize_detects_unsupported_mode(self):
        stops = [
            {"city": "NYC", "country": "USA", "arrival_mode": "start"},
            {"city": "LON", "country": "GBR", "arrival_mode": "submarine"},
        ]
        result = optimize_multi_stop(stops)
        assert UNSUPPORTED_MODE in result.get("warnings", []) or any(
            UNSUPPORTED_MODE in v.get("mode_error", "") for v in result.get("validation", [])
        )


class TestEstimateBudget:
    def test_estimate_returns_structured_dict(self):
        estimate = estimate_budget(
            origin="NYC",
            destination="LON",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            travelers=2,
        )
        assert "currency" in estimate
        assert estimate["currency"] == "USD"
        assert "line_items" in estimate
        assert len(estimate["line_items"]) > 0
        assert "total" in estimate
        assert estimate["total"] > 0

    def test_estimate_includes_flights_hotels_incidentals(self):
        estimate = estimate_budget(
            origin="NYC",
            destination="LON",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            travelers=1,
        )
        categories = {item["category"] for item in estimate["line_items"]}
        assert "flights" in categories
        assert "hotels" in categories
        assert "incidentals" in categories

    def test_estimate_zero_travelers_returns_incomplete(self):
        result = estimate_budget(
            origin="NYC",
            destination="LON",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 10),
            travelers=0,
        )
        assert result == INCOMPLETE_INPUT


class TestValidateTravelDocs:
    def test_valid_passport_passes(self):
        doc = {
            "doc_type": "passport",
            "doc_number": "P123456",
            "issuing_country": "USA",
            "expiry_date": date(2028, 1, 1),
            "holder_name": "Jane Doe",
        }
        results = validate_travel_docs([doc])
        assert len(results) > 0
        assert results[0]["status"] == "pass"

    def test_expired_document_fails(self):
        doc = {
            "doc_type": "passport",
            "doc_number": "P999",
            "issuing_country": "USA",
            "expiry_date": date(2024, 1, 1),
            "holder_name": "Old Doc",
        }
        results = validate_travel_docs([doc])
        assert results[0]["status"] == "fail"
        assert "expired" in results[0]["detail"].lower()

    def test_insufficient_blank_pages_warns(self):
        doc = {
            "doc_type": "passport",
            "doc_number": "P456",
            "issuing_country": "USA",
            "expiry_date": date(2028, 1, 1),
            "holder_name": "Jane",
            "blank_pages": 1,
        }
        results = validate_travel_docs([doc], destinations=["AUS", "JPN", "SGP"])
        assert any(r["status"] in ("warning", "fail") for r in results)

    def test_missing_visa_for_restricted_country(self):
        doc = {
            "doc_type": "passport",
            "doc_number": "P789",
            "issuing_country": "USA",
            "expiry_date": date(2028, 1, 1),
            "holder_name": "Bob",
        }
        visas = []
        results = validate_travel_docs([doc], destinations=["CHN"], visas=visas)
        assert any("visa" in r["detail"].lower() for r in results)

    def test_present_visa_satisfies_requirement(self):
        doc = {
            "doc_type": "passport",
            "doc_number": "P000",
            "issuing_country": "USA",
            "expiry_date": date(2028, 1, 1),
            "holder_name": "Alice",
        }
        visas = [{"country": "CHN", "type": "tourist", "expiry": date(2028, 6, 1)}]
        results = validate_travel_docs([doc], destinations=["CHN"], visas=visas)
        visa_results = [r for r in results if "visa" in r["check"].lower()]
        assert len(visa_results) > 0
        assert visa_results[0]["status"] == "pass"

    def test_empty_docs_list_returns_incomplete(self):
        result = validate_travel_docs([])
        assert result == INCOMPLETE_INPUT
