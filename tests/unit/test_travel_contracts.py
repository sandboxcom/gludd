"""Tests for travel agent contracts (TRV Phase A).

Covers Pydantic contracts in ``src/general_ludd/travel/contracts.py``:
  - Required fields enforced, enums reject invalid tokens.
  - Numeric constraints (negative cost, invalid date ranges) rejected.
  - Currency normalisation (lower → upper).
  - JSON round-trip serialisation preserves data and schema_version.
  - model_validator recomputes total_cost for MultiStopRoute.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from general_ludd.travel.contracts import (
    SCHEMA_VERSION,
    BookingStatus,
    Budget,
    BudgetLineItem,
    BusBooking,
    CabinClass,
    CarRental,
    DocKind,
    EventBooking,
    EventKind,
    FlightBooking,
    FlightFare,
    FlightSearch,
    FlightSegment,
    HotelBooking,
    HotelSearch,
    Itinerary,
    ItineraryStatus,
    LoyaltyProgram,
    Money,
    MultiStopRoute,
    Notification,
    NotificationKind,
    Passport,
    ProviderInfo,
    RoomType,
    RouteStop,
    SegmentKind,
    TimelineEntry,
    TrainBooking,
    Transit,
    TravelDoc,
    Traveler,
    TripPreferences,
    TripRequest,
    TripSegment,
    VisaHeld,
)

UTC = UTC
NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 8, 1)
TOMORROW = date(2026, 8, 2)
NEXT_WEEK = date(2026, 8, 8)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _dict(obj) -> dict:
    return json.loads(obj.model_dump_json())


# ─── Money ─────────────────────────────────────────────────────────────────────


class TestMoney:
    def test_valid_usd(self):
        m = Money(amount=100.0, currency="usd")
        assert m.amount == 100.0
        assert m.currency == "USD"

    def test_currency_uppercased(self):
        m = Money(amount=50.0, currency="eur")
        assert m.currency == "EUR"

    def test_negative_amount_rejected(self):
        with pytest.raises(ValidationError):
            Money(amount=-1.0, currency="USD")

    def test_missing_currency_rejected(self):
        with pytest.raises(ValidationError):
            Money(amount=100.0)

    def test_short_currency_rejected(self):
        with pytest.raises(ValidationError):
            Money(amount=100.0, currency="US")

    def test_json_round_trip(self):
        m = Money(amount=99.99, currency="gbp")
        d = _dict(m)
        assert d["amount"] == 99.99
        assert d["currency"] == "GBP"
        m2 = Money.model_validate(d)
        assert m2 == m


# ─── ProviderInfo ──────────────────────────────────────────────────────────────


class TestProviderInfo:
    def test_valid(self):
        p = ProviderInfo(source="skyscanner", offer_id="abc123", retrieved_at=NOW)
        assert p.source == "skyscanner"
        assert p.offer_id == "abc123"

    def test_empty_source_rejected(self):
        with pytest.raises(ValidationError):
            ProviderInfo(source="", offer_id="abc123", retrieved_at=NOW)


# ─── Traveler & friends ────────────────────────────────────────────────────────


class TestTraveler:
    def test_minimal(self):
        t = Traveler(name="Alice", passport_number="X12345678")
        assert t.name == "Alice"
        assert t.passport_number == "X12345678"
        assert len(t.traveler_id) == 36  # uuid4

    def test_with_passport_sub_model(self):
        pp = Passport(number_masked="****5678", issuing_country="US", expiry_date=TOMORROW)
        t = Traveler(name="Bob", passport_number="P87654321", passport=pp)
        assert t.passport == pp
        assert t.passport.number_masked == "****5678"

    def test_visa_held(self):
        v = VisaHeld(country="FR", type="tourist", expiry=NEXT_WEEK)
        t = Traveler(name="Carol", passport_number="C1111111", visas_held=[v])
        assert len(t.visas_held) == 1
        assert t.visas_held[0].country == "FR"

    def test_loyalty(self):
        lp = LoyaltyProgram(program="MileagePlus", member_id_masked="UA***", tier="Gold")
        t = Traveler(name="Dan", passport_number="D2222222", loyalty_programs=[lp])
        assert t.loyalty_programs[0].tier == "Gold"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            Traveler(name="", passport_number="X12345678")

    def test_json_round_trip(self):
        t = Traveler(name="Eve", passport_number="E3333333", nationality="US")
        d = _dict(t)
        t2 = Traveler.model_validate(d)
        assert t2.name == "Eve"


# ─── Budget ────────────────────────────────────────────────────────────────────


class TestBudget:
    def test_valid(self):
        b = Budget(currency="usd", total=1000.0)
        assert b.total == 1000.0
        assert b.currency == "USD"

    def test_with_line_items(self):
        li = BudgetLineItem(category="flights", description="JFK→CDG", amount=500.0)
        b = Budget(currency="EUR", total=500.0, line_items=[li])
        assert len(b.line_items) == 1
        assert b.line_items[0].category == "flights"

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            Budget(currency="USD", total=-1.0)


# ─── TripRequest ───────────────────────────────────────────────────────────────


class TestTripRequest:
    def test_minimal(self):
        budget = Budget(currency="USD", total=1000.0)
        req = TripRequest(origin="JFK", destination="CDG", start_date=TODAY, end_date=TOMORROW, budget=budget)
        assert req.origin == "JFK"
        assert req.destination == "CDG"
        assert req.schema_version == SCHEMA_VERSION

    def test_end_before_start_rejected(self):
        budget = Budget(currency="USD", total=1000.0)
        with pytest.raises(ValidationError):
            TripRequest(origin="JFK", destination="CDG", start_date=TOMORROW, end_date=TODAY, budget=budget)

    def test_with_travelers(self):
        budget = Budget(currency="USD", total=2000.0)
        traveler = Traveler(name="Frank", passport_number="F4444444")
        req = TripRequest(
            origin="LAX", destination="NRT", start_date=TODAY, end_date=NEXT_WEEK, budget=budget, travelers=[traveler]
        )
        assert len(req.travelers) == 1
        assert req.travelers[0].name == "Frank"

    def test_json_round_trip(self):
        budget = Budget(currency="USD", total=1500.0)
        req = TripRequest(origin="SFO", destination="LHR", start_date=TODAY, end_date=TOMORROW, budget=budget)
        d = _dict(req)
        assert "schema_version" in d
        req2 = TripRequest.model_validate(d)
        assert req2.origin == "SFO"


# ─── TripPreferences ───────────────────────────────────────────────────────────


class TestTripPreferences:
    def test_defaults(self):
        prefs = TripPreferences()
        assert prefs.cabin_class == CabinClass.economy
        assert prefs.max_stops == 2

    def test_invalid_cabin_rejected(self):
        with pytest.raises(ValidationError):
            TripPreferences(cabin_class="royal")


# ─── TripSegment ───────────────────────────────────────────────────────────────


class TestTripSegment:
    def test_valid(self):
        seg = TripSegment(
            segment_type=SegmentKind.transport,
            from_location="JFK",
            to_location="CDG",
            departure=NOW,
            arrival=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            cost=350.0,
            currency="usd",
        )
        assert seg.segment_type == "transport"
        assert seg.currency == "USD"

    def test_arrival_before_departure_rejected(self):
        with pytest.raises(ValidationError):
            TripSegment(
                segment_type=SegmentKind.transport,
                from_location="A",
                to_location="B",
                departure=NOW,
                arrival=datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC),
                cost=100.0,
                currency="usd",
            )

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            TripSegment(
                segment_type=SegmentKind.transport,
                from_location="A",
                to_location="B",
                departure=NOW,
                arrival=NOW,
                cost=-1.0,
                currency="usd",
            )


# ─── FlightSearch ──────────────────────────────────────────────────────────────


class TestFlightSearch:
    def test_valid(self):
        fs = FlightSearch(origin="JFK", destination="CDG", departure_date=TODAY, passengers=2)
        assert fs.origin == "JFK"
        assert fs.passengers == 2
        assert fs.cabin_class == CabinClass.economy

    def test_negative_passengers_rejected(self):
        with pytest.raises(ValidationError):
            FlightSearch(origin="JFK", destination="CDG", departure_date=TODAY, passengers=0)


# ─── FlightSegment ─────────────────────────────────────────────────────────────


class TestFlightSegmentModel:
    def test_valid(self):
        fs = FlightSegment(
            flight_number="UA123",
            airline="United",
            departure_airport="JFK",
            arrival_airport="CDG",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            cabin_class="economy",
        )
        assert fs.flight_number == "UA123"

    def test_arrival_before_departure_rejected(self):
        with pytest.raises(ValidationError):
            FlightSegment(
                flight_number="AA1",
                airline="American",
                departure_airport="LAX",
                arrival_airport="JFK",
                departure_time=NOW,
                arrival_time=datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC),
                cabin_class="business",
            )


# ─── FlightBooking ─────────────────────────────────────────────────────────────


class TestFlightBooking:
    def test_minimal(self):
        seg = FlightSegment(
            flight_number="BA456",
            airline="British Airways",
            departure_airport="LHR",
            arrival_airport="JFK",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            cabin_class="economy",
        )
        fb = FlightBooking(confirmation_code="ABC123", airline="BA", segments=[seg], total_price=500.0, currency="usd")
        assert fb.status == BookingStatus.draft
        assert fb.currency == "USD"

    def test_empty_segments_rejected(self):
        with pytest.raises(ValidationError):
            FlightBooking(confirmation_code="ABC123", airline="BA", segments=[], total_price=500.0, currency="usd")

    def test_invalid_status_rejected(self):
        seg = FlightSegment(
            flight_number="BA456",
            airline="British Airways",
            departure_airport="LHR",
            arrival_airport="JFK",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            cabin_class="economy",
        )
        with pytest.raises(ValidationError):
            FlightBooking(
                confirmation_code="ABC123",
                airline="BA",
                segments=[seg],
                total_price=500.0,
                currency="usd",
                status="bogus",
            )

    def test_json_round_trip(self):
        seg = FlightSegment(
            flight_number="BA456",
            airline="British Airways",
            departure_airport="LHR",
            arrival_airport="JFK",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            cabin_class="economy",
        )
        fb = FlightBooking(confirmation_code="ABC123", airline="BA", segments=[seg], total_price=500.0, currency="usd")
        d = _dict(fb)
        fb2 = FlightBooking.model_validate(d)
        assert fb2.confirmation_code == "ABC123"


# ─── FlightFare ────────────────────────────────────────────────────────────────


class TestFlightFare:
    def test_minimal(self):
        fare = FlightFare(base_amount=Money(amount=400.0, currency="usd"))
        assert fare.base_amount.amount == 400.0
        assert fare.fare_rules.refundable is False

    def test_with_total(self):
        fare = FlightFare(
            base_amount=Money(amount=400.0, currency="usd"),
            total_amount=Money(amount=520.0, currency="usd"),
        )
        assert fare.total_amount.amount == 520.0


# ─── HotelSearch ───────────────────────────────────────────────────────────────


class TestHotelSearch:
    def test_valid(self):
        hs = HotelSearch(location="Paris", check_in=TODAY, check_out=TOMORROW, guests=2, rooms=1)
        assert hs.location == "Paris"

    def test_check_out_before_check_in_rejected(self):
        with pytest.raises(ValidationError):
            HotelSearch(location="Paris", check_in=TOMORROW, check_out=TODAY, guests=2, rooms=1)


# ─── HotelBooking ──────────────────────────────────────────────────────────────


class TestHotelBooking:
    def test_minimal(self):
        room = RoomType(name="Deluxe", beds="1 King", max_occupancy=2, price_per_night=250.0, currency="usd")
        hb = HotelBooking(
            confirmation_code="HOTEL123",
            hotel_name="Grand Hotel",
            address="1 Rue de Rivoli",
            room=room,
            check_in=TODAY,
            check_out=TOMORROW,
            total_price=250.0,
            currency="usd",
        )
        assert hb.status == BookingStatus.draft
        assert hb.currency == "USD"

    def test_check_out_before_check_in_rejected(self):
        room = RoomType(name="Standard", beds="1 Queen", max_occupancy=2, price_per_night=150.0, currency="usd")
        with pytest.raises(ValidationError):
            HotelBooking(
                confirmation_code="HOTEL456",
                hotel_name="Test",
                address="1 Test St",
                room=room,
                check_in=TOMORROW,
                check_out=TODAY,
                total_price=150.0,
                currency="usd",
            )


# ─── CarRental ─────────────────────────────────────────────────────────────────


class TestCarRental:
    def test_valid(self):
        cr = CarRental(
            confirmation_code="CAR123",
            pickup_location="JFK",
            dropoff_location="JFK",
            pickup_date=TODAY,
            dropoff_date=TOMORROW,
            car_type="SUV",
            total_price=200.0,
            currency="usd",
        )
        assert cr.currency == "USD"

    def test_dropoff_before_pickup_rejected(self):
        with pytest.raises(ValidationError):
            CarRental(
                confirmation_code="CAR456",
                pickup_location="JFK",
                dropoff_location="JFK",
                pickup_date=TOMORROW,
                dropoff_date=TODAY,
                car_type="Sedan",
                total_price=100.0,
                currency="usd",
            )


# ─── TrainBooking ──────────────────────────────────────────────────────────────


class TestTrainBooking:
    def test_valid(self):
        tb = TrainBooking(
            confirmation_code="TGV123",
            operator="SNCF",
            departure_station="Paris Gare de Lyon",
            arrival_station="Lyon Part-Dieu",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 1, 14, 0, 0, tzinfo=UTC),
            seat_class="first",
            total_price=120.0,
            currency="eur",
        )
        assert tb.currency == "EUR"

    def test_arrival_before_departure_rejected(self):
        with pytest.raises(ValidationError):
            TrainBooking(
                confirmation_code="TGV456",
                operator="SNCF",
                departure_station="A",
                arrival_station="B",
                departure_time=NOW,
                arrival_time=datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC),
                seat_class="first",
                total_price=100.0,
                currency="eur",
            )


# ─── BusBooking ────────────────────────────────────────────────────────────────


class TestBusBooking:
    def test_valid(self):
        bb = BusBooking(
            confirmation_code="BUS123",
            operator="FlixBus",
            departure_stop="Berlin ZOB",
            arrival_stop="Munich ZOB",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 1, 20, 0, 0, tzinfo=UTC),
            total_price=30.0,
            currency="eur",
        )
        assert bb.currency == "EUR"


# ─── EventBooking ──────────────────────────────────────────────────────────────


class TestEventBooking:
    def test_valid(self):
        eb = EventBooking(
            event_type=EventKind.conference,
            name="PyCon",
            location="Pittsburgh",
            event_date=NEXT_WEEK,
            total_price=500.0,
            currency="usd",
        )
        assert eb.event_type == "conference"
        assert eb.status == BookingStatus.draft

    def test_invalid_event_kind_rejected(self):
        with pytest.raises(ValidationError):
            EventBooking(
                event_type="birthday",
                name="Party",
                event_date=NEXT_WEEK,
                total_price=100.0,
                currency="usd",
            )


# ─── TravelDoc ─────────────────────────────────────────────────────────────────


class TestTravelDoc:
    def test_valid(self):
        td = TravelDoc(
            doc_type=DocKind.passport,
            doc_number="X12345678",
            issuing_country="US",
            expiry_date=NEXT_WEEK,
            holder_name="Alice",
        )
        assert td.doc_type == "passport"

    def test_invalid_doc_kind_rejected(self):
        with pytest.raises(ValidationError):
            TravelDoc(
                doc_type="drivers_license",
                doc_number="D123",
                issuing_country="US",
                expiry_date=NEXT_WEEK,
                holder_name="Alice",
            )


# ─── Notification ──────────────────────────────────────────────────────────────


class TestNotification:
    def test_valid(self):
        n = Notification(
            notification_type=NotificationKind.check_in_reminder,
            title="Check In",
            message="Your flight departs in 24h",
            recipient="alice@example.com",
        )
        assert n.notification_type == "check_in_reminder"
        assert n.sent is False


# ─── MultiStopRoute ────────────────────────────────────────────────────────────


class TestMultiStopRoute:
    def test_total_cost_computed(self):
        seg1 = TripSegment(
            segment_type=SegmentKind.transport,
            from_location="A",
            to_location="B",
            departure=NOW,
            arrival=datetime(2026, 8, 1, 13, 0, 0, tzinfo=UTC),
            cost=100.0,
            currency="usd",
        )
        seg2 = TripSegment(
            segment_type=SegmentKind.transport,
            from_location="B",
            to_location="C",
            departure=datetime(2026, 8, 1, 14, 0, 0, tzinfo=UTC),
            arrival=datetime(2026, 8, 1, 15, 0, 0, tzinfo=UTC),
            cost=200.0,
            currency="usd",
        )
        route = MultiStopRoute(name="A to C", segments=[seg1, seg2])
        assert route.total_cost == 300.0

    def test_empty_segments_rejected(self):
        with pytest.raises(ValidationError):
            MultiStopRoute(name="Empty", segments=[])

    def test_validation_entries_default(self):
        seg = TripSegment(
            segment_type=SegmentKind.stay,
            from_location="X",
            to_location="X",
            departure=NOW,
            arrival=NOW,
            cost=0.0,
            currency="usd",
        )
        route = MultiStopRoute(name="Stay", segments=[seg])
        assert route.validation == []
        assert route.optimized is False


# ─── TimelineEntry ─────────────────────────────────────────────────────────────


class TestTimelineEntry:
    def test_valid(self):
        te = TimelineEntry(
            entry_index=0,
            type="flight",
            start_time=NOW,
            end_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=UTC),
            timezone="America/New_York",
            location="JFK",
        )
        assert te.entry_index == 0
        assert te.type == "flight"


# ─── Itinerary ─────────────────────────────────────────────────────────────────


class TestItinerary:
    def test_minimal(self):
        it = Itinerary()
        assert it.status == ItineraryStatus.draft
        assert it.schema_version == SCHEMA_VERSION
        assert len(it.timeline) == 0

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            Itinerary(status="bogus")

    def test_json_round_trip(self):
        it = Itinerary(status=ItineraryStatus.draft)
        d = _dict(it)
        it2 = Itinerary.model_validate(d)
        assert it2.status == "draft"


# ─── RouteStop ─────────────────────────────────────────────────────────────────


class TestRouteStop:
    def test_minimal(self):
        rs = RouteStop(stop_index=0, city="Paris", country="FR")
        assert rs.city == "Paris"
        assert rs.arrival_mode == "start"
        assert rs.timezone == "UTC"

    def test_negative_dwell_rejected(self):
        with pytest.raises(ValidationError):
            RouteStop(stop_index=0, city="Paris", country="FR", dwell_hours=-1.0)


# ─── Transit ───────────────────────────────────────────────────────────────────


class TestTransit:
    def test_minimal(self):
        t = Transit(from_stop_index=0, to_stop_index=1, mode="air")
        assert t.from_stop_index == 0
        assert t.buffer_minutes == 60
        assert t.connection_warning is None


# ─── Enums ─────────────────────────────────────────────────────────────────────


class TestEnums:
    def test_cabin_class_values(self):
        assert CabinClass.economy == "economy"
        assert CabinClass.business == "business"

    def test_segment_kind_values(self):
        assert SegmentKind.transport == "transport"
        assert SegmentKind.stay == "stay"

    def test_booking_status_values(self):
        assert BookingStatus.draft == "draft"
        assert BookingStatus.confirmed == "confirmed"

    def test_itinerary_status_values(self):
        assert ItineraryStatus.completed == "completed"

    def test_notification_kind_values(self):
        assert NotificationKind.delay == "delay"

    def test_doc_kind_values(self):
        assert DocKind.visa == "visa"

    def test_event_kind_values(self):
        assert EventKind.wedding == "wedding"


# ─── Serialisation round-trips ─────────────────────────────────────────────────


class TestSerialisation:
    def test_money_round_trip(self):
        m = Money(amount=42.0, currency="usd")
        d = _dict(m)
        assert Money.model_validate(d) == m

    def test_trip_request_round_trip(self):
        budget = Budget(currency="USD", total=999.0)
        req = TripRequest(origin="A", destination="B", start_date=TODAY, end_date=TOMORROW, budget=budget)
        d = _dict(req)
        assert TripRequest.model_validate(d).origin == "A"

    def test_flight_booking_round_trip(self):
        seg = FlightSegment(
            flight_number="TK1",
            airline="Turkish",
            departure_airport="IST",
            arrival_airport="JFK",
            departure_time=NOW,
            arrival_time=datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC),
            cabin_class="economy",
        )
        fb = FlightBooking(confirmation_code="TKCNF", airline="TK", segments=[seg], total_price=700.0, currency="usd")
        d = _dict(fb)
        assert FlightBooking.model_validate(d).confirmation_code == "TKCNF"

    def test_hotel_booking_round_trip(self):
        room = RoomType(name="Suite", beds="1 King", max_occupancy=2, price_per_night=500.0, currency="usd")
        hb = HotelBooking(
            confirmation_code="HB1",
            hotel_name="Ritz",
            address="1 Place Vendome",
            room=room,
            check_in=TODAY,
            check_out=TOMORROW,
            total_price=500.0,
            currency="usd",
        )
        d = _dict(hb)
        assert HotelBooking.model_validate(d).hotel_name == "Ritz"

    def test_itinerary_round_trip(self):
        it = Itinerary(status=ItineraryStatus.active)
        d = _dict(it)
        assert Itinerary.model_validate(d).status == "active"
