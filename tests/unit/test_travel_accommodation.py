"""Unit tests for travel accommodation.py module_utils."""

from __future__ import annotations

from datetime import date

from ansible_collections.general_ludd.travel.plugins.module_utils.accommodation import (
    AmenityFilter,
    HotelBooker,
    HotelSearchEngine,
    RoomComparator,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    HotelSearch,
    RoomType,
)


class TestHotelSearchEngine:
    def test_search_nyc_returns_hotels(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 20), guests=2, rooms=1)
        results = engine.search(hs)
        assert len(results) >= 1
        assert all("hotel_name" in r for r in results)
        assert all("room_name" in r for r in results)

    def test_search_unknown_location_returns_empty(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="MARS", check_in=date(2026, 8, 15), check_out=date(2026, 8, 20), guests=1, rooms=1)
        results = engine.search(hs)
        assert results == []

    def test_search_total_price_correct(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 20), guests=2, rooms=1)
        results = engine.search(hs)
        for r in results:
            assert r["total_price"] == r["price_per_night"] * r["nights"] * r["rooms"]

    def test_search_max_price_filter(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 16), guests=1, rooms=1)
        results = engine.search(hs, max_price=200.0)
        assert all(r["price_per_night"] <= 200.0 for r in results)

    def test_search_min_rating_filter(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 16), guests=1, rooms=1)
        results = engine.search(hs, min_rating=4.5)
        assert all(r["rating"] >= 4.5 for r in results)

    def test_search_hotel_name_filter(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 16), guests=1, rooms=1)
        results = engine.search(hs, hotel_name="SoHo Boutique")
        assert len(results) >= 1
        assert all(r["hotel_name"] == "SoHo Boutique" for r in results)

    def test_search_hotel_name_filter_no_match(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="NYC", check_in=date(2026, 8, 15), check_out=date(2026, 8, 16), guests=1, rooms=1)
        results = engine.search(hs, hotel_name="Nonexistent Hotel")
        assert results == []

    def test_search_nights_one_day(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="LON", check_in=date(2026, 8, 15), check_out=date(2026, 8, 15), guests=1, rooms=1)
        results = engine.search(hs)
        assert all(r["nights"] == 1 for r in results)

    def test_search_multiple_rooms(self):
        engine = HotelSearchEngine()
        hs = HotelSearch(location="LON", check_in=date(2026, 8, 15), check_out=date(2026, 8, 17), guests=2, rooms=3)
        results = engine.search(hs)
        assert all(r["rooms"] == 3 for r in results)

    def test_search_all_locations(self):
        locations = ["NYC", "LON", "PAR", "TYO", "LAX", "MIA", "DXB"]
        engine = HotelSearchEngine()
        for loc in locations:
            hs = HotelSearch(location=loc, check_in=date(2026, 8, 15), check_out=date(2026, 8, 16), guests=1, rooms=1)
            results = engine.search(hs)
            assert len(results) >= 1, f"No results for {loc}"


class TestHotelBooker:
    def test_book_creates_draft_booking(self):
        booker = HotelBooker()
        room = RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=200.0, currency="USD")
        booking = booker.book(
            hotel_name="Test Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 20),
        )
        assert booking.status == BookingStatus.draft
        assert booking.total_price == 1000.0  # 5 nights * 200
        assert booking.confirmation_code.startswith("H")
        assert len(booking.confirmation_code) == 9

    def test_book_single_night(self):
        booker = HotelBooker()
        room = RoomType(name="Basic", beds="1 Double", max_occupancy=1, price_per_night=45.0, currency="GBP")
        booking = booker.book(
            hotel_name="Hostel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 15),
            currency="GBP",
        )
        assert booking.total_price == 45.0
        assert booking.currency == "GBP"

    def test_book_uses_room_currency_when_not_specified(self):
        booker = HotelBooker()
        room = RoomType(name="Chambre", beds="1 Queen", max_occupancy=2, price_per_night=350.0, currency="EUR")
        booking = booker.book(
            hotel_name="Paris Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 17),
        )
        assert booking.currency == "EUR"
        assert booking.total_price == 700.0

    def test_confirm_changes_status(self):
        booker = HotelBooker()
        room = RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=200.0, currency="USD")
        booking = booker.book(
            hotel_name="Test Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 17),
        )
        confirmed = booker.confirm(booking.booking_id)
        assert confirmed.status == BookingStatus.confirmed
        assert confirmed.booking_id == booking.booking_id

    def test_cancel_changes_status(self):
        booker = HotelBooker()
        room = RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=200.0, currency="USD")
        booking = booker.book(
            hotel_name="Test Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 17),
        )
        cancelled = booker.cancel(booking.booking_id)
        assert cancelled.status == BookingStatus.cancelled

    def test_get_booking_returns_none_for_unknown(self):
        booker = HotelBooker()
        assert booker.get_booking("unknown-id") is None

    def test_get_booking_returns_booking(self):
        booker = HotelBooker()
        room = RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=150.0, currency="USD")
        booking = booker.book(
            hotel_name="Test Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 16),
        )
        result = booker.get_booking(booking.booking_id)
        assert result is not None
        assert result.booking_id == booking.booking_id

    def test_book_stores_address_and_property_id(self):
        booker = HotelBooker()
        room = RoomType(name="Deluxe", beds="1 King", max_occupancy=2, price_per_night=320.0, currency="USD")
        booking = booker.book(
            hotel_name="Grand Hotel",
            room=room,
            check_in=date(2026, 8, 15),
            check_out=date(2026, 8, 17),
            address="100 Main St",
            property_id="GH-001",
        )
        assert booking.address == "100 Main St"
        assert booking.property_id == "GH-001"


class TestRoomComparator:
    def test_by_price_sorts_ascending(self):
        rooms = [
            RoomType(name="Expensive", beds="1 King", max_occupancy=2, price_per_night=500.0, currency="USD"),
            RoomType(name="Cheap", beds="1 Double", max_occupancy=1, price_per_night=100.0, currency="USD"),
            RoomType(name="Mid", beds="1 Queen", max_occupancy=2, price_per_night=300.0, currency="USD"),
        ]
        result = RoomComparator.by_price(rooms)
        assert result[0].price_per_night == 100.0
        assert result[1].price_per_night == 300.0
        assert result[2].price_per_night == 500.0

    def test_by_value_sorts_by_price_per_occupant(self):
        rooms = [
            RoomType(name="A", beds="1 King", max_occupancy=1, price_per_night=200.0, currency="USD"),
            RoomType(name="B", beds="2 Queen", max_occupancy=4, price_per_night=400.0, currency="USD"),
        ]
        result = RoomComparator.by_value(rooms)
        assert result[0].name == "B"  # 100 per occupant vs 200

    def test_best_value_returns_cheapest_per_occupant(self):
        rooms = [
            RoomType(name="Small", beds="1 Double", max_occupancy=1, price_per_night=100.0, currency="USD"),
            RoomType(name="Large", beds="2 Queen", max_occupancy=4, price_per_night=300.0, currency="USD"),
        ]
        best = RoomComparator.best_value(rooms)
        assert best.name == "Large"  # 75 per occupant vs 100

    def test_best_value_empty_list_raises(self):
        import pytest

        with pytest.raises(ValueError, match="no rooms"):
            RoomComparator.best_value([])

    def test_by_occupancy_sorts_descending(self):
        rooms = [
            RoomType(name="Small", beds="1 Double", max_occupancy=1, price_per_night=100.0, currency="USD"),
            RoomType(name="Suite", beds="2 Queen", max_occupancy=4, price_per_night=500.0, currency="USD"),
            RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=200.0, currency="USD"),
        ]
        result = RoomComparator.by_occupancy(rooms)
        assert result[0].max_occupancy == 4
        assert result[-1].max_occupancy == 1


class TestAmenityFilter:
    def test_filter_hotels_with_required_amenities(self):
        hotels = [
            {"hotel_name": "A", "amenities": ["wifi", "pool", "gym"]},
            {"hotel_name": "B", "amenities": ["wifi"]},
            {"hotel_name": "C", "amenities": ["wifi", "pool", "spa", "gym"]},
        ]
        af = AmenityFilter(required=["pool", "gym"])
        result = af.filter_hotels(hotels)
        assert len(result) == 2
        names = {h["hotel_name"] for h in result}
        assert names == {"A", "C"}

    def test_filter_hotels_no_required_returns_all(self):
        hotels = [{"hotel_name": "A", "amenities": ["wifi"]}]
        af = AmenityFilter(required=None)
        result = af.filter_hotels(hotels)
        assert len(result) == 1

    def test_filter_hotels_empty_required_returns_all(self):
        hotels = [{"hotel_name": "A", "amenities": ["wifi"]}]
        af = AmenityFilter(required=[])
        result = af.filter_hotels(hotels)
        assert len(result) == 1

    def test_filter_hotels_case_insensitive(self):
        hotels = [{"hotel_name": "A", "amenities": ["WiFi", "Pool"]}]
        af = AmenityFilter(required=["wifi", "pool"])
        result = af.filter_hotels(hotels)
        assert len(result) == 1

    def test_filter_hotels_no_match(self):
        hotels = [{"hotel_name": "A", "amenities": ["wifi"]}]
        af = AmenityFilter(required=["pool", "spa"])
        result = af.filter_hotels(hotels)
        assert result == []

    def test_score_hotels_with_preferred_amenities(self):
        hotels = [
            {"hotel_name": "A", "amenities": ["wifi", "pool", "spa"]},
            {"hotel_name": "B", "amenities": ["wifi"]},
        ]
        af = AmenityFilter(preferred=["pool", "spa", "gym"])
        result = af.score_hotels(hotels)
        assert len(result) == 2
        assert result[0]["hotel_name"] == "A"
        assert result[0]["amenity_score"] == 2
        assert result[1]["amenity_score"] == 0

    def test_score_hotels_shows_matched_and_missing(self):
        hotels = [{"hotel_name": "A", "amenities": ["wifi", "pool"]}]
        af = AmenityFilter(preferred=["wifi", "gym", "spa"])
        result = af.score_hotels(hotels)
        assert result[0]["amenity_score"] == 1
        assert "wifi" in result[0]["matched_amenities"]
        assert "gym" in result[0]["missing_amenities"]
        assert "spa" in result[0]["missing_amenities"]
