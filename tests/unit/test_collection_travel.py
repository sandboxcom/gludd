"""Unit tests for travel ansible collection modules.

Tests the four travel module entrypoints via direct import of their standalone
functions: trip_planner.plan_trip, flight_search.search_flights,
hotel_search.search_hotels, searxng_search.search_searxng.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_COLLECTIONS_DIR = str(Path(__file__).resolve().parents[2] / "collections")
if _COLLECTIONS_DIR not in sys.path:
    sys.path.insert(0, _COLLECTIONS_DIR)

from ansible_collections.general_ludd.travel.plugins.modules.flight_search import (  # noqa: E402
    _flight_booking_from_stub,
    _make_flight_search,
    search_flights,
)
from ansible_collections.general_ludd.travel.plugins.modules.hotel_search import (  # noqa: E402
    _hotel_booking_from_stub,
    _make_hotel_search,
    search_hotels,
)
from ansible_collections.general_ludd.travel.plugins.modules.searxng_search import (  # noqa: E402
    _build_search_url,
    _extract_airport_codes,
    _extract_price,
    _extract_stars,
    _normalise_url,
    _parse_activity_result,
    _parse_event_result,
    _parse_flight_result,
    _parse_general_result,
    _parse_hotel_result,
    search_searxng,
)
from ansible_collections.general_ludd.travel.plugins.modules.trip_planner import (  # noqa: E402
    _make_trip_request,
    plan_trip,
)

# ---------------------------------------------------------------------------
# trip_planner — plan_trip
# ---------------------------------------------------------------------------


class TestTripPlannerPlanTrip:
    def test_plan_trip_returns_expected_keys(self) -> None:
        result = plan_trip(
            origin="NYC",
            destinations=["Paris", "London"],
            start_date="2026-09-01",
            end_date="2026-09-14",
            budget=5000.0,
            interests=["museums", "food"],
            travelers=2,
            trip_style="comfort",
        )
        assert "itinerary_id" in result
        assert "trip" in result
        assert "days" in result
        assert "total_estimated_cost" in result
        assert result["total_estimated_cost"] == 5000.0

    def test_plan_trip_days_count_matches_destinations(self) -> None:
        result = plan_trip(
            origin="SFO",
            destinations=["Tokyo", "Seoul", "Bangkok"],
            start_date="2026-10-01",
            end_date="2026-10-21",
            budget=8000.0,
            interests=[],
            travelers=1,
            trip_style="budget",
        )
        assert len(result["days"]) == 3
        assert result["days"][0]["day"] == 1
        assert result["days"][0]["location"] == "Tokyo"
        assert result["days"][2]["location"] == "Bangkok"

    def test_plan_trip_zero_budget_generates_default(self) -> None:
        result = plan_trip(
            origin="LAX",
            destinations=["Miami"],
            start_date="2026-11-01",
            end_date="2026-11-05",
            budget=0.0,
            interests=["beaches"],
            travelers=1,
            trip_style="luxury",
        )
        assert result["total_estimated_cost"] > 0

    def test_plan_trip_includes_activities_and_meals(self) -> None:
        result = plan_trip(
            origin="JFK",
            destinations=["Paris"],
            start_date="2026-12-01",
            end_date="2026-12-07",
            budget=3000.0,
            interests=["art", "history", "wine"],
            travelers=2,
            trip_style="comfort",
        )
        day = result["days"][0]
        assert len(day["activities"]) >= 2
        assert len(day["meals"]) == 3
        assert day["accommodation"] == "Paris"

    def test_plan_trip_trip_metadata_correct(self) -> None:
        result = plan_trip(
            origin="NYC",
            destinations=["London"],
            start_date="2026-08-15",
            end_date="2026-08-22",
            budget=2500.0,
            interests=[],
            travelers=1,
            trip_style="budget",
        )
        assert result["trip"]["origin"] == "NYC"
        assert result["trip"]["destinations"] == ["London"]
        assert result["trip"]["travelers"] == 1
        assert result["trip"]["style"] == "budget"

    def test_make_trip_request_constructs_valid_request(self) -> None:
        req = _make_trip_request(
            origin="NYC",
            destinations=["Paris", "London"],
            start_date_str="2026-09-01",
            end_date_str="2026-09-14",
            budget_amount=5000.0,
            travelers=2,
            trip_style="comfort",
        )
        assert req.origin == "NYC"
        assert req.destination == "London"
        assert req.budget.total == 5000.0
        assert req.budget.currency == "USD"

    def test_make_trip_request_zero_budget_defaults(self) -> None:
        req = _make_trip_request(
            origin="SFO",
            destinations=["Tokyo"],
            start_date_str="2026-10-01",
            end_date_str="2026-10-07",
            budget_amount=0.0,
            travelers=1,
            trip_style="budget",
        )
        assert req.budget.total > 0


# ---------------------------------------------------------------------------
# flight_search — search_flights
# ---------------------------------------------------------------------------


class TestFlightSearchSearchFlights:
    def test_search_flights_returns_expected_tuple(self) -> None:
        flights, cheapest, search_params = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=2,
            cabin_class="economy",
            max_stops=2,
            max_price=0.0,
            preferred_airlines=[],
            currency="USD",
        )
        assert isinstance(flights, list)
        assert isinstance(cheapest, dict)
        assert isinstance(search_params, dict)
        assert len(flights) == 2

    def test_search_flights_max_price_filters_results(self) -> None:
        flights, _, _ = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=1,
            cabin_class="economy",
            max_stops=2,
            max_price=400.0,
            preferred_airlines=[],
            currency="USD",
        )
        assert len(flights) == 1
        assert flights[0]["airline"] == "UA"

    def test_search_flights_max_stops_filters_results(self) -> None:
        flights, _, _ = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=1,
            cabin_class="economy",
            max_stops=0,
            max_price=0.0,
            preferred_airlines=[],
            currency="USD",
        )
        assert len(flights) == 1
        assert flights[0]["segments"][0]["flight_number"]

    def test_search_flights_preferred_airlines_filters(self) -> None:
        flights, _, _ = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=1,
            cabin_class="economy",
            max_stops=2,
            max_price=0.0,
            preferred_airlines=["AA"],
            currency="USD",
        )
        assert len(flights) == 1
        assert flights[0]["airline"] == "AA"

    def test_search_flights_cheapest_is_lowest_price(self) -> None:
        flights, cheapest, _ = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=1,
            cabin_class="economy",
            max_stops=2,
            max_price=0.0,
            preferred_airlines=[],
            currency="USD",
        )
        min_price = min(f["total_price"] for f in flights)
        assert cheapest["total_price"] == min_price

    def test_search_flights_booking_has_expected_keys(self) -> None:
        flights, _, _ = search_flights(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            return_date=None,
            passengers=1,
            cabin_class="economy",
            max_stops=2,
            max_price=0.0,
            preferred_airlines=[],
            currency="USD",
        )
        for booking in flights:
            assert "airline" in booking
            assert "confirmation_code" in booking
            assert "segments" in booking
            assert "total_price" in booking
            assert "currency" in booking

    def test_make_flight_search_constructs_valid_search(self) -> None:
        fs = _make_flight_search(
            origin="JFK",
            destination="LHR",
            depart_date_str="2026-09-01",
            return_date_str="2026-09-14",
            passengers=2,
            cabin_class_str="business",
        )
        assert fs.origin == "JFK"
        assert fs.destination == "LHR"
        assert fs.passengers == 2
        assert fs.cabin_class.value == "business"
        assert fs.return_date is not None

    def test_flight_booking_from_stub_returns_valid_booking(self) -> None:
        booking = _flight_booking_from_stub(
            origin="JFK",
            destination="LHR",
            depart_date="2026-09-01",
            airline="BA",
            flight_number="BA178",
            depart_hour=10,
            arrive_hour=16,
            stops=0,
            duration_mins=360,
            price=550.0,
            cabin_class_str="economy",
            currency="USD",
        )
        assert booking.airline == "BA"
        assert booking.total_price == 550.0
        assert len(booking.segments) == 1
        assert booking.segments[0].flight_number == "BA178"


# ---------------------------------------------------------------------------
# hotel_search — search_hotels
# ---------------------------------------------------------------------------


class TestHotelSearchSearchHotels:
    def test_search_hotels_returns_expected_tuple(self) -> None:
        hotels, nights, search_params = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="price",
            currency="USD",
        )
        assert isinstance(hotels, list)
        assert isinstance(nights, int)
        assert isinstance(search_params, dict)
        assert len(hotels) == 2
        assert nights == 4

    def test_search_hotels_min_stars_filters_results(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=4,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="price",
            currency="USD",
        )
        assert len(hotels) == 1
        assert "Grand" in hotels[0]["hotel_name"]

    def test_search_hotels_max_price_filters(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=100.0,
            amenities=[],
            sort_by="price",
            currency="USD",
        )
        assert len(hotels) == 1
        assert "Budget" in hotels[0]["hotel_name"]

    def test_search_hotels_amenities_filter(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=["pool", "gym"],
            sort_by="price",
            currency="USD",
        )
        assert len(hotels) == 1
        assert "Grand" in hotels[0]["hotel_name"]

    def test_search_hotels_sort_by_rating(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="rating",
            currency="USD",
        )
        assert len(hotels) >= 2
        assert hotels[0]["rating"] >= hotels[-1]["rating"]

    def test_search_hotels_sort_by_distance(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="distance",
            currency="USD",
        )
        assert len(hotels) >= 2
        assert hotels[0]["distance_km"] <= hotels[-1]["distance_km"]

    def test_search_hotels_sort_by_name(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="name",
            currency="USD",
        )
        assert len(hotels) >= 2
        assert hotels[0]["hotel_name"].lower() <= hotels[-1]["hotel_name"].lower()

    def test_search_hotels_booking_has_expected_keys(self) -> None:
        hotels, _, _ = search_hotels(
            destination="Paris",
            check_in="2026-09-01",
            check_out="2026-09-05",
            guests=2,
            rooms=1,
            min_stars=0,
            max_price_per_night=0.0,
            amenities=[],
            sort_by="price",
            currency="USD",
        )
        for h in hotels:
            assert "hotel_name" in h
            assert "confirmation_code" in h
            assert "total_price" in h
            assert "stars" in h
            assert "rating" in h
            assert "amenities" in h
            assert "distance_km" in h

    def test_make_hotel_search_constructs_valid_search(self) -> None:
        hs = _make_hotel_search(
            destination="Paris",
            check_in_str="2026-09-01",
            check_out_str="2026-09-05",
            guests=2,
            rooms=1,
        )
        assert hs.location == "Paris"
        assert hs.guests == 2
        assert hs.rooms == 1

    def test_hotel_booking_from_stub_returns_valid_booking(self) -> None:
        booking = _hotel_booking_from_stub(
            destination="Paris",
            name="Test Hotel",
            stars=4,
            price_per_night=200.0,
            rating=4.2,
            amenity_list=["wifi", "pool"],
            distance_km=1.0,
            currency="USD",
            check_in="2026-09-01",
            check_out="2026-09-05",
            rooms=1,
        )
        assert booking.hotel_name == "Test Hotel"
        assert booking.total_price == 800.0
        assert booking.room.price_per_night == 200.0


# ---------------------------------------------------------------------------
# searxng_search — search_searxng
# ---------------------------------------------------------------------------


class TestSearxngSearch:
    def test_normalise_url_adds_http(self) -> None:
        assert _normalise_url("localhost:8080") == "http://localhost:8080"

    def test_normalise_url_strips_trailing_slash(self) -> None:
        assert _normalise_url("http://example.com/") == "http://example.com"

    def test_normalise_url_preserves_https(self) -> None:
        assert _normalise_url("https://searx.example.com") == "https://searx.example.com"

    def test_build_search_url_returns_correct_structure(self) -> None:
        url = _build_search_url(
            searxng_url="http://localhost:8080",
            query="flights NYC to Paris",
            category="flights",
            engines="",
            max_results=10,
            safe_search=0,
            language="en",
        )
        assert url.startswith("http://localhost:8080/search?")
        assert "q=flights" in url or "q=flights+NYC" in url
        assert "format=json" in url

    def test_build_search_url_uses_custom_engines(self) -> None:
        url = _build_search_url(
            searxng_url="http://sx:8080",
            query="hotels Tokyo",
            category="hotels",
            engines="google,bing",
            max_results=5,
            safe_search=1,
            language="en",
        )
        assert "engines=google%2Cbing" in url or "engines=google,bing" in url

    def test_extract_price_returns_float(self) -> None:
        assert _extract_price("Rooms from $350 per night") == 350.0

    def test_extract_price_simple_with_comma(self) -> None:
        assert _extract_price("Price: $1,250 total") == 125.0

    def test_extract_price_no_match_returns_none(self) -> None:
        assert _extract_price("No price info available") is None

    def test_extract_stars_returns_float(self) -> None:
        assert _extract_stars("4.5 star hotel") == 4.5

    def test_extract_stars_with_unicode(self) -> None:
        assert _extract_stars("Rating: 4 \u2b50") == 4.0

    def test_extract_stars_no_match_returns_none(self) -> None:
        assert _extract_stars("Great hotel") is None

    def test_extract_airport_codes(self) -> None:
        codes = _extract_airport_codes("Flight from JFK to LHR with stop at CDG")
        assert "JFK" in codes
        assert "LHR" in codes
        assert "CDG" in codes

    def test_extract_airport_codes_deduplicates(self) -> None:
        codes = _extract_airport_codes("JFK to JFK via JFK")
        assert codes.count("JFK") == 1

    def test_extract_airport_codes_no_codes(self) -> None:
        codes = _extract_airport_codes("No airport codes here")
        assert codes == []

    def test_parse_flight_result_returns_valid_booking(self) -> None:
        result = {
            "title": "JFK to LHR from $450",
            "content": "Direct flight with British Airways",
            "url": "https://example.com/flights/jfk-lhr",
            "engine": "google_flights",
        }
        data = _parse_flight_result(result, query="flights JFK to LHR")
        assert "airline" in data
        assert "confirmation_code" in data
        assert "segments" in data
        assert "total_price" in data
        assert data["title"] == "JFK to LHR from $450"

    def test_parse_flight_result_extracts_price(self) -> None:
        result = {
            "title": "Cheap flight $299",
            "content": "Budget airline deal",
            "url": "https://example.com/flights/cheap",
            "engine": "google_travel",
        }
        data = _parse_flight_result(result, query="cheap flights")
        assert data["total_price"] == 299.0

    def test_parse_hotel_result_returns_valid_booking(self) -> None:
        result = {
            "title": "Grand Hotel Paris",
            "content": "Luxury hotel in central Paris from $350 per night, 4.5 stars",
            "url": "https://example.com/hotels/grand-paris",
            "engine": "booking",
        }
        data = _parse_hotel_result(result, query="hotels Paris")
        assert "hotel_name" in data
        assert "confirmation_code" in data
        assert "room" in data
        assert "total_price" in data
        assert "stars" in data
        assert data["stars"] == 4.5

    def test_parse_event_result_returns_valid_booking(self) -> None:
        result = {
            "title": "Tech Conference 2026",
            "content": "Annual tech conference in San Francisco, tickets from $499",
            "url": "https://example.com/events/tech-conf",
            "engine": "eventbrite",
        }
        data = _parse_event_result(result, query="tech conference")
        assert "name" in data
        assert "event_type" in data
        assert "title" in data
        assert data["total_price"] == 499.0

    def test_parse_activity_result_returns_dict(self) -> None:
        result = {
            "title": "Eiffel Tower Tour",
            "content": "Guided tour of the Eiffel Tower",
            "url": "https://example.com/activities/eiffel",
            "engine": "tripadvisor",
            "category": "sightseeing",
        }
        data = _parse_activity_result(result, query="things to do Paris")
        assert data["title"] == "Eiffel Tower Tour"
        assert "description" in data
        assert "url" in data
        assert data["source"] == "tripadvisor"

    def test_parse_general_result_returns_dict(self) -> None:
        result = {
            "title": "Paris Travel Guide",
            "content": "Complete guide to visiting Paris",
            "url": "https://example.com/paris-guide",
            "engines": ["google", "wikipedia"],
            "score": 0.95,
            "category": "general",
        }
        data = _parse_general_result(result, query="Paris travel")
        assert data["title"] == "Paris Travel Guide"
        assert data["score"] == 0.95
        assert "google" in data["source"]

    @patch("urllib.request.urlopen")
    def test_search_searxng_returns_structured_results(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "title": "JFK to LHR from $450",
                        "content": "Direct flight",
                        "url": "https://example.com/flight",
                        "engine": "google_flights",
                    },
                    {
                        "title": "JFK to LHR via CDG $380",
                        "content": "One stop",
                        "url": "https://example.com/flight2",
                        "engine": "google_travel",
                    },
                ]
            }
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        results, raw, search_url = search_searxng(
            query="flights JFK to LHR",
            category="flights",
            searxng_url="http://localhost:8080",
            engines="",
            max_results=10,
            safe_search=0,
            language="en",
            timeout=10,
        )
        assert len(results) == 2
        assert len(raw) == 2
        assert search_url.startswith("http://localhost:8080/search?")

    @patch("urllib.request.urlopen")
    def test_search_searxng_http_error_returns_empty(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="http://localhost:8080/search",
            code=500,
            msg="Internal Server Error",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )
        results, raw, _ = search_searxng(
            query="test",
            category="general",
            searxng_url="http://localhost:8080",
            engines="",
            max_results=10,
            safe_search=0,
            language="en",
            timeout=10,
        )
        assert results == []
        assert raw == []

    @patch("urllib.request.urlopen")
    def test_search_searxng_hotel_category(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "title": "Grand Hotel $350 per night",
                        "content": "4.5 star luxury hotel",
                        "url": "https://example.com/hotel",
                        "engine": "booking",
                    }
                ]
            }
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        results, _, _ = search_searxng(
            query="hotels Paris",
            category="hotels",
            searxng_url="http://localhost:8080",
            engines="",
            max_results=5,
            safe_search=0,
            language="en",
            timeout=10,
        )
        assert len(results) == 1
        assert "hotel_name" in results[0]
        assert results[0]["total_price"] > 0

    @patch("urllib.request.urlopen")
    def test_search_searxng_activities_category(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "title": "Louvre Museum Tour",
                        "content": "Skip-the-line guided tour",
                        "url": "https://example.com/activity",
                        "engine": "tripadvisor",
                        "category": "sightseeing",
                    }
                ]
            }
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        results, _, _ = search_searxng(
            query="things to do Paris",
            category="activities",
            searxng_url="http://localhost:8080",
            engines="",
            max_results=5,
            safe_search=0,
            language="en",
            timeout=10,
        )
        assert len(results) == 1
        assert results[0]["title"] == "Louvre Museum Tour"

    @patch("urllib.request.urlopen")
    def test_search_searxng_max_results_truncates(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"results": [{"title": f"Result {i}", "content": "", "url": "", "engine": "google"} for i in range(10)]}
        ).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        results, _, _ = search_searxng(
            query="test",
            category="general",
            searxng_url="http://localhost:8080",
            engines="",
            max_results=3,
            safe_search=0,
            language="en",
            timeout=10,
        )
        assert len(results) == 3

    def test_search_searxng_empty_engines_uses_default(self) -> None:
        url = _build_search_url(
            searxng_url="http://localhost:8080",
            query="flights test",
            category="flights",
            engines="",
            max_results=10,
            safe_search=0,
            language="en",
        )
        assert "google_flights" in url or "google" in url
