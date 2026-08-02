"""Unit tests for travel events.py module_utils."""

from __future__ import annotations

from datetime import date

from ansible_collections.general_ludd.travel.plugins.module_utils.events import (
    SCHEMA_VERSION,
    book_activity,
    plan_funeral,
    plan_meeting,
    plan_tour,
    plan_wedding,
)


class TestPlanWedding:
    def test_valid_request(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": 80,
                "budget": {"total": 30000.0, "currency": "EUR"},
                "style": "rustic",
            }
        )
        assert result["status"] == "draft"
        assert result["couple"] == ["Alice", "Bob"]
        assert result["estimated_cost"] == 20000.0  # 80 * 250
        assert result["currency"] == "EUR"
        assert "wedding_id" in result
        assert len(result["vendors"]) == 5
        assert result["guest_list_needed"] is True
        assert result["errors"] == []

    def test_missing_couple_errors(self):
        result = plan_wedding(
            {
                "couple": ["Alice"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": 80,
            }
        )
        assert len(result["errors"]) >= 1
        assert any("couple" in e.lower() for e in result["errors"])

    def test_missing_event_date_errors(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "location": "Tuscany",
                "guests": 80,
            }
        )
        assert len(result["errors"]) >= 1
        assert any("event_date" in e for e in result["errors"])

    def test_missing_location_errors(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "guests": 80,
            }
        )
        assert len(result["errors"]) >= 1
        assert any("location" in e for e in result["errors"])

    def test_zero_guests(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": 0,
                "budget": {"total": 10000.0},
            }
        )
        assert result["guests"] == 0
        assert result["estimated_cost"] == 250.0  # 1 * 250 (floor)

    def test_missing_budget_warns(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": 50,
            }
        )
        assert len(result["warnings"]) >= 1

    def test_negative_guests_handled(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": -5,
            }
        )
        assert result["guests"] == 0

    def test_schema_version_included(self):
        result = plan_wedding(
            {
                "couple": ["Alice", "Bob"],
                "event_date": date(2026, 9, 15),
                "location": "Tuscany",
                "guests": 80,
            }
        )
        assert result["schema_version"] == SCHEMA_VERSION


class TestPlanFuneral:
    def test_valid_request(self):
        result = plan_funeral(
            {
                "deceased_name": "John Doe",
                "event_date": date(2026, 9, 20),
                "location": "Chicago",
                "service_type": "cremation",
                "attendees": 50,
            }
        )
        assert result["status"] == "draft"
        assert result["deceased_name"] == "John Doe"
        assert result["service_type"] == "cremation"
        assert result["estimated_cost"] == 6000.0  # 50 * 120
        assert len(result["service_options"]) == 4
        assert result["errors"] == []

    def test_missing_deceased_name_errors(self):
        result = plan_funeral(
            {
                "event_date": date(2026, 9, 20),
                "location": "Chicago",
                "attendees": 50,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_date_errors(self):
        result = plan_funeral(
            {
                "deceased_name": "John Doe",
                "location": "Chicago",
                "attendees": 50,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_location_errors(self):
        result = plan_funeral(
            {
                "deceased_name": "John Doe",
                "event_date": date(2026, 9, 20),
                "attendees": 50,
            }
        )
        assert len(result["errors"]) >= 1

    def test_default_service_type(self):
        result = plan_funeral(
            {
                "deceased_name": "Jane Doe",
                "event_date": date(2026, 9, 20),
                "location": "Boston",
                "attendees": 30,
            }
        )
        assert result["service_type"] == "burial"

    def test_currency_always_usd(self):
        result = plan_funeral(
            {
                "deceased_name": "John Doe",
                "event_date": date(2026, 9, 20),
                "location": "Chicago",
                "attendees": 50,
            }
        )
        assert result["currency"] == "USD"


class TestPlanMeeting:
    def test_valid_request(self):
        result = plan_meeting(
            {
                "name": "Q4 Strategy",
                "event_date": date(2026, 10, 1),
                "location": "Board Room",
                "attendees": 12,
                "duration_hours": 2,
            }
        )
        assert result["status"] == "draft"
        assert result["name"] == "Q4 Strategy"
        assert result["attendees"] == 12
        assert result["room_setup"] == "boardroom"
        assert len(result["agenda_template"]) == 5
        assert result["errors"] == []

    def test_room_setup_theatre(self):
        result = plan_meeting(
            {
                "name": "All Hands",
                "event_date": date(2026, 10, 1),
                "location": "Auditorium",
                "attendees": 30,
                "duration_hours": 1,
            }
        )
        assert result["room_setup"] == "theatre"

    def test_room_setup_banquet(self):
        result = plan_meeting(
            {
                "name": "Gala",
                "event_date": date(2026, 10, 1),
                "location": "Ballroom",
                "attendees": 100,
                "duration_hours": 3,
            }
        )
        assert result["room_setup"] == "banquet"

    def test_catering_adds_cost(self):
        no_catering = plan_meeting(
            {
                "name": "Test",
                "event_date": date(2026, 10, 1),
                "location": "Room A",
                "attendees": 20,
            }
        )
        with_catering = plan_meeting(
            {
                "name": "Test",
                "event_date": date(2026, 10, 1),
                "location": "Room A",
                "attendees": 20,
                "catering": True,
            }
        )
        assert with_catering["estimated_cost"] > no_catering["estimated_cost"]

    def test_missing_name_errors(self):
        result = plan_meeting(
            {
                "event_date": date(2026, 10, 1),
                "location": "Room A",
                "attendees": 10,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_date_errors(self):
        result = plan_meeting(
            {
                "name": "Test",
                "location": "Room A",
                "attendees": 10,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_location_errors(self):
        result = plan_meeting(
            {
                "name": "Test",
                "event_date": date(2026, 10, 1),
                "attendees": 10,
            }
        )
        assert len(result["errors"]) >= 1


class TestPlanTour:
    def test_valid_request(self):
        result = plan_tour(
            {
                "name": "European Adventure",
                "destination": "Paris",
                "start_date": date(2026, 9, 1),
                "end_date": date(2026, 9, 7),
                "group_size": 4,
                "budget": {"total": 10000.0, "currency": "EUR"},
            }
        )
        assert result["status"] == "draft"
        assert result["destination"] == "Paris"
        assert result["nights"] == 6
        assert result["group_size"] == 4
        assert len(result["daily_itinerary"]) == 6
        assert len(result["inclusions"]) == 4

    def test_invalid_date_range_errors(self):
        result = plan_tour(
            {
                "name": "Test",
                "destination": "Paris",
                "start_date": date(2026, 9, 7),
                "end_date": date(2026, 9, 1),
                "group_size": 4,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_destination_errors(self):
        result = plan_tour(
            {
                "name": "Test",
                "start_date": date(2026, 9, 1),
                "end_date": date(2026, 9, 7),
                "group_size": 4,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_dates_errors(self):
        result = plan_tour(
            {
                "name": "Test",
                "destination": "Paris",
                "group_size": 4,
            }
        )
        assert len(result["errors"]) >= 1

    def test_estimated_cost_includes_nightly(self):
        result = plan_tour(
            {
                "name": "Test",
                "destination": "Paris",
                "start_date": date(2026, 9, 1),
                "end_date": date(2026, 9, 4),
                "group_size": 2,
                "budget": {"total": 5000.0},
            }
        )
        assert result["estimated_cost"] > 0
        # 2 * 180 + 3 * 150 = 360 + 450 = 810
        assert result["estimated_cost"] == 810.0

    def test_budget_missing_warns(self):
        result = plan_tour(
            {
                "name": "Test",
                "destination": "Paris",
                "start_date": date(2026, 9, 1),
                "end_date": date(2026, 9, 4),
                "group_size": 2,
            }
        )
        assert len(result["warnings"]) >= 1

    def test_daily_itinerary_structure(self):
        result = plan_tour(
            {
                "name": "Test",
                "destination": "Tokyo",
                "start_date": date(2026, 9, 1),
                "end_date": date(2026, 9, 4),
                "group_size": 2,
            }
        )
        for day_entry in result["daily_itinerary"]:
            assert "day" in day_entry
            assert "date" in day_entry
            assert "morning" in day_entry
            assert "afternoon" in day_entry
            assert "evening" in day_entry

    def test_itinerary_capped_at_14_days(self):
        result = plan_tour(
            {
                "name": "Long Tour",
                "destination": "World",
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 2, 1),
                "group_size": 1,
            }
        )
        assert len(result["daily_itinerary"]) <= 14


class TestBookActivity:
    def test_valid_activity(self):
        result = book_activity(
            {
                "activity_type": "scuba_diving",
                "event_date": date(2026, 8, 20),
                "location": "Cozumel",
                "participants": 2,
                "duration_hours": 3,
            }
        )
        assert result["status"] == "draft"
        assert result["activity_type"] == "scuba_diving"
        assert result["total_price"] == 720.0  # 120 * 2 * 3
        assert "booking_id" in result
        assert len(result["requirements"]) >= 1

    def test_unknown_activity_uses_default_price(self):
        result = book_activity(
            {
                "activity_type": "unknown_thing",
                "event_date": date(2026, 8, 20),
                "location": "Somewhere",
                "participants": 2,
                "duration_hours": 2,
            }
        )
        assert result["total_price"] == 200.0  # 50 * 2 * 2

    def test_missing_activity_type_errors(self):
        result = book_activity(
            {
                "event_date": date(2026, 8, 20),
                "location": "Beach",
                "participants": 1,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_date_errors(self):
        result = book_activity(
            {
                "activity_type": "hiking",
                "location": "Beach",
                "participants": 1,
            }
        )
        assert len(result["errors"]) >= 1

    def test_zero_participants_errors(self):
        result = book_activity(
            {
                "activity_type": "hiking",
                "event_date": date(2026, 8, 20),
                "location": "Beach",
                "participants": 0,
            }
        )
        assert len(result["errors"]) >= 1

    def test_missing_location_errors(self):
        result = book_activity(
            {
                "activity_type": "hiking",
                "event_date": date(2026, 8, 20),
                "participants": 1,
            }
        )
        assert len(result["errors"]) >= 1

    def test_all_activity_types_have_prices(self):
        activities = [
            "sightseeing",
            "scuba_diving",
            "hiking",
            "cooking_class",
            "wine_tasting",
            "museum_tour",
            "helicopter_tour",
            "camel_ride",
        ]
        for activity in activities:
            result = book_activity(
                {
                    "activity_type": activity,
                    "event_date": date(2026, 8, 20),
                    "location": "Test",
                    "participants": 1,
                }
            )
            assert result["total_price"] > 0

    def test_schema_version_included(self):
        result = book_activity(
            {
                "activity_type": "hiking",
                "event_date": date(2026, 8, 20),
                "location": "Test",
                "participants": 1,
            }
        )
        assert result["schema_version"] == SCHEMA_VERSION
