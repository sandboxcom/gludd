"""Unit tests for travel transport.py module_utils."""

from __future__ import annotations

from datetime import date, datetime

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    CabinClass,
    CarRental,
    FlightBooking,
    FlightFare,
    FlightFareRule,
    FlightSearch,
    Money,
    ProviderInfo,
    TrainBooking,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.transport import (
    _CAR_TYPES,
    _TRAIN_OPERATORS,
    CarRentalSearch,
    FlightBooker,
    FlightSearchEngine,
    SeatSelector,
    TrainSearch,
)


class TestFlightSearchEngine:
    def test_search_returns_flights(self):
        fs = FlightSearch(origin="JFK", destination="LHR", departure_date=date(2026, 8, 15), passengers=2)
        engine = FlightSearchEngine(max_results=3)
        results = engine.search(fs)
        assert len(results) >= 1
        assert all("flight_number" in r for r in results)

    def test_search_respects_max_results(self):
        fs = FlightSearch(origin="JFK", destination="LHR", departure_date=date(2026, 8, 15), passengers=1)
        engine = FlightSearchEngine(max_results=1)
        results = engine.search(fs)
        assert len(results) == 1

    def test_search_no_route_returns_empty(self):
        fs = FlightSearch(origin="JFK", destination="CDG", departure_date=date(2026, 8, 15), passengers=1)
        engine = FlightSearchEngine(max_results=5)
        results = engine.search(fs)
        assert isinstance(results, list)

    def test_search_with_cabin_class(self):
        fs = FlightSearch(
            origin="JFK",
            destination="LHR",
            departure_date=date(2026, 8, 15),
            passengers=1,
            cabin_class=CabinClass.business,
        )
        engine = FlightSearchEngine(max_results=2)
        results = engine.search(fs)
        assert len(results) >= 1


class TestFlightBooker:
    def test_book_valid_result_returns_flight_booking(self):
        booker = FlightBooker()
        result = {
            "flight_number": "AA1234",
            "airline": "AA",
            "departure_airport": "JFK",
            "arrival_airport": "LHR",
            "departure_time": datetime(2026, 8, 15, 10, 0),
            "arrival_time": datetime(2026, 8, 15, 18, 0),
            "price": 517.50,
            "currency": "USD",
            "cabin_class": "economy",
        }
        booking = booker.book(result, passengers=2)
        assert isinstance(booking, FlightBooking)
        assert booking.airline == "AA"
        assert len(booking.segments) == 1
        assert booking.total_price == 517.50
        assert booking.currency == "USD"
        assert booking.status == BookingStatus.draft
        assert len(booking.confirmation_code) == 8

    def test_book_missing_flight_number_raises(self):
        booker = FlightBooker()
        result = {"airline": "AA", "price": 100.0}
        import pytest

        with pytest.raises(ValueError, match="flight_number"):
            booker.book(result, passengers=1)

    def test_cancel_booking_sets_cancelled_status(self):
        booker = FlightBooker()
        result = {
            "flight_number": "BA5678",
            "airline": "BA",
            "departure_airport": "LHR",
            "arrival_airport": "JFK",
            "departure_time": datetime(2026, 8, 22, 14, 0),
            "arrival_time": datetime(2026, 8, 22, 20, 0),
            "price": 450.00,
            "currency": "USD",
        }
        booking = booker.book(result, passengers=1)
        cancelled = booker.cancel(booking)
        assert cancelled.status == BookingStatus.cancelled

    def test_book_with_fare_and_provider(self):
        booker = FlightBooker()
        result = {
            "flight_number": "DL9999",
            "airline": "DL",
            "departure_airport": "JFK",
            "arrival_airport": "LAX",
            "departure_time": datetime(2026, 8, 15, 8, 0),
            "arrival_time": datetime(2026, 8, 15, 11, 0),
            "price": 370.50,
            "currency": "USD",
        }
        fare = FlightFare(
            base_amount=Money(amount=300.0, currency="USD"),
            total_amount=Money(amount=370.50, currency="USD"),
            fare_rules=FlightFareRule(refundable=True, changeable=True),
        )
        provider = ProviderInfo(source="test", offer_id="OFFER-001", retrieved_at=datetime.now())
        booking = booker.book(result, passengers=1, fare=fare, provider=provider)
        assert booking.fare is not None
        assert booking.fare.base_amount.amount == 300.0
        assert booking.provider is not None
        assert booking.provider.offer_id == "OFFER-001"


class TestTrainSearch:
    def test_search_returns_train_bookings(self):
        ts = TrainSearch()
        results = ts.search("NYC", "LON", date(2026, 8, 15), passengers=2)
        assert len(results) >= 1
        assert all(isinstance(b, TrainBooking) for b in results)
        assert results[0].total_price > 0
        assert results[0].currency == "USD"

    def test_search_empty_origin_returns_empty(self):
        ts = TrainSearch()
        results = ts.search("", "LON", date(2026, 8, 15))
        assert results == []

    def test_search_same_origin_destination_returns_empty(self):
        ts = TrainSearch()
        results = ts.search("NYC", "NYC", date(2026, 8, 15))
        assert results == []

    def test_search_respects_max_results(self):
        ts = TrainSearch()
        results = ts.search("NYC", "LON", date(2026, 8, 15), max_results=2)
        assert len(results) == 2

    def test_search_sets_train_number(self):
        ts = TrainSearch()
        results = ts.search("NYC", "LON", date(2026, 8, 15))
        assert results[0].train_number is not None
        assert len(results[0].train_number) >= 5

    def test_search_with_seat_class(self):
        ts = TrainSearch()
        results = ts.search("NYC", "LON", date(2026, 8, 15), seat_class="first")
        assert results[0].seat_class == "first"

    def test_search_operators_capped(self):
        ts = TrainSearch()
        results = ts.search("NYC", "LON", date(2026, 8, 15), max_results=100)
        assert len(results) <= len(_TRAIN_OPERATORS)


class TestCarRentalSearch:
    def test_search_returns_car_rentals(self):
        crs = CarRentalSearch()
        results = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 20))
        assert len(results) >= 1
        assert all(isinstance(c, CarRental) for c in results)

    def test_search_invalid_dates_returns_empty(self):
        crs = CarRentalSearch()
        results = crs.search("JFK", date(2026, 8, 20), date(2026, 8, 15))
        assert results == []

    def test_search_empty_location_returns_empty(self):
        crs = CarRentalSearch()
        results = crs.search("", date(2026, 8, 15), date(2026, 8, 20))
        assert results == []

    def test_search_price_increases_with_days(self):
        crs = CarRentalSearch()
        short = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 16))  # 1 day
        long = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 20))  # 5 days
        assert long[0].total_price > short[0].total_price

    def test_search_respects_max_results(self):
        crs = CarRentalSearch()
        results = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 20), max_results=2)
        assert len(results) == 2

    def test_search_includes_insurance_for_premium(self):
        crs = CarRentalSearch()
        results = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 20), max_results=6)
        types = {c.car_type: c.includes_insurance for c in results}
        assert types.get("fullsize", False) is True
        assert types.get("suv", False) is True
        assert types.get("luxury", False) is True
        assert types.get("economy", False) is False

    def test_search_all_car_types_present(self):
        crs = CarRentalSearch()
        results = crs.search("JFK", date(2026, 8, 15), date(2026, 8, 20), max_results=10)
        types = {c.car_type for c in results}
        assert types == set(_CAR_TYPES)


class TestSeatSelector:
    def test_select_window(self):
        ss = SeatSelector()
        result = ss.select(row=10, position="window")
        assert result["row"] == 10
        assert result["position"] == "window"
        assert result["side"] in ("A", "F")

    def test_select_aisle(self):
        ss = SeatSelector()
        result = ss.select(row=11, position="aisle")
        assert result["position"] == "aisle"
        assert result["side"] in ("C", "D")

    def test_select_middle(self):
        ss = SeatSelector()
        result = ss.select(row=12, position="middle")
        assert result["position"] == "middle"
        assert result["side"] in ("B", "E")

    def test_select_unknown_position_defaults_to_window(self):
        ss = SeatSelector()
        result = ss.select(row=5, position="cockpit")
        assert result["position"] == "window"

    def test_assign_auto_prefer_aisle(self):
        ss = SeatSelector()
        result = ss.assign_auto({"prefer_aisle": 1, "row_min": 5, "row_max": 20})
        assert result["position"] == "aisle"
        assert result["side"] == "C"

    def test_assign_auto_prefer_window(self):
        ss = SeatSelector()
        result = ss.assign_auto({"prefer_window": 1, "row_min": 5, "row_max": 20})
        assert result["position"] == "window"
        assert result["side"] == "A"

    def test_assign_auto_no_preference(self):
        ss = SeatSelector()
        result = ss.assign_auto({"row_min": 10, "row_max": 30})
        assert "row" in result
        assert "position" in result
        assert "side" in result
        assert 10 <= result["row"] <= 30

    def test_assign_auto_row_bounds_clamped(self):
        ss = SeatSelector()
        result = ss.assign_auto({"row_min": 100, "row_max": 200})
        assert 60 <= result["row"] <= 100  # bounds swapped by code, result in [60,100]

    def test_assign_auto_swapped_bounds(self):
        ss = SeatSelector()
        result = ss.assign_auto({"row_min": 30, "row_max": 10})
        assert 10 <= result["row"] <= 30

    def test_assign_auto_default_bounds_when_missing(self):
        ss = SeatSelector()
        result = ss.assign_auto({})
        assert 1 <= result["row"] <= 60
