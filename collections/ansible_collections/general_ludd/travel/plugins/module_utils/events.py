"""Travel events module — moved from src/general_ludd/travel/events.py.

Implements five event planners:
  - plan_wedding        plan a wedding with vendors and cost estimation
  - plan_funeral        plan a funeral with service options
  - plan_meeting        plan a meeting with catering and room setup
  - plan_tour           plan a guided tour with daily itinerary
  - book_activity       book a single activity
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

SCHEMA_VERSION = "trv-001/0.1"

_PERSON_COST = 5.0
_COST_PER_GUEST_WEDDING = 250.0
_COST_PER_ATTENDEE_FUNERAL = 120.0
_COST_PER_ATTENDEE_MEETING = 15.0
_CATERING_COST_PER_HEAD = 25.0
_COST_PER_PERSON_TOUR = 180.0
_TOUR_NIGHTLY = 150.0
_DEFAULT_ACTIVITY_COST = 50.0

_ACTIVITY_PRICES: dict[str, float] = {
    "sightseeing": 35.0,
    "scuba_diving": 120.0,
    "hiking": 25.0,
    "cooking_class": 80.0,
    "wine_tasting": 60.0,
    "museum_tour": 20.0,
    "helicopter_tour": 250.0,
    "camel_ride": 45.0,
}

_WEDDING_VENDORS = [
    {"category": "venue", "suggestion": "Local villa or garden"},
    {"category": "catering", "suggestion": "Full-service caterer"},
    {"category": "photography", "suggestion": "Professional photographer"},
    {"category": "florist", "suggestion": "Seasonal floral arrangements"},
    {"category": "music", "suggestion": "Live band or DJ"},
]

_TOUR_INCLUSIONS = [
    "Professional guide",
    "Transportation between stops",
    "Entry fees to main attractions",
    "Daily breakfast",
]

_MEETING_AGENDA_TEMPLATE = [
    {"time": "0:00", "item": "Welcome and introductions"},
    {"time": "0:10", "item": "Agenda review"},
    {"time": "0:15", "item": "Main discussion points"},
    {"time": "0:45", "item": "Decisions and action items"},
    {"time": "0:55", "item": "Wrap-up and next steps"},
]

_SERVICE_OPTIONS = [
    {"type": "burial", "description": "Traditional burial service"},
    {"type": "cremation", "description": "Cremation with memorial"},
    {"type": "memorial", "description": "Memorial service without burial"},
    {"type": "celebration_of_life", "description": "Celebration of life ceremony"},
]

_ACTIVITY_REQUIREMENTS: dict[str, list[str]] = {
    "sightseeing": ["Comfortable shoes", "Camera"],
    "scuba_diving": ["Certification card", "Swimsuit", "Medical clearance"],
    "hiking": ["Hiking boots", "Water bottle", "Sunscreen"],
    "cooking_class": ["Appetite"],
    "wine_tasting": ["Valid ID (age 21+)"],
    "museum_tour": ["Quiet voice"],
    "helicopter_tour": ["Photo ID", "Closed-toe shoes"],
    "camel_ride": ["Long pants", "Sunscreen"],
}


def _new_id() -> str:
    return str(uuid.uuid4())


def plan_wedding(request: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    couple: list[str] = request.get("couple") or []
    event_date = request.get("event_date")
    location: str = (request.get("location") or "").strip()
    guests: int = request.get("guests", 0)
    if not isinstance(guests, int) or guests <= 0:
        guests = 0
    budget = request.get("budget") or {}
    style: str = request.get("style") or "traditional"
    budget_total = float(budget.get("total", 0.0))

    if len(couple) < 2:
        errors.append("couple requires exactly two names")
    if not isinstance(event_date, date):
        errors.append("event_date must be a date object")
    if not location:
        errors.append("location is required")

    if budget_total <= 0:
        warnings.append("budget is zero or missing; cost estimates are unbounded")

    estimated_cost = round(max(guests, 1) * _COST_PER_GUEST_WEDDING, 2)

    return {
        "schema_version": SCHEMA_VERSION,
        "wedding_id": _new_id(),
        "couple": couple,
        "location": location,
        "event_date": event_date.isoformat() if isinstance(event_date, date) else None,
        "guests": max(guests, 0),
        "style": style,
        "status": "draft",
        "estimated_cost": estimated_cost,
        "currency": budget.get("currency", "USD").upper(),
        "vendors": _WEDDING_VENDORS,
        "guest_list_needed": True,
        "errors": errors,
        "warnings": warnings,
    }


def plan_funeral(request: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    deceased_name: str = (request.get("deceased_name") or "").strip()
    event_date = request.get("event_date")
    location: str = (request.get("location") or "").strip()
    service_type: str = (request.get("service_type") or "burial").strip()
    attendees: int = request.get("attendees", 0)
    if not isinstance(attendees, int) or attendees <= 0:
        attendees = 0

    if not deceased_name:
        errors.append("deceased_name is required")
    if not isinstance(event_date, date):
        errors.append("event_date must be a date object")
    if not location:
        errors.append("location is required")

    estimated_cost = round(max(attendees, 1) * _COST_PER_ATTENDEE_FUNERAL, 2)

    return {
        "schema_version": SCHEMA_VERSION,
        "funeral_id": _new_id(),
        "deceased_name": deceased_name,
        "location": location,
        "event_date": event_date.isoformat() if isinstance(event_date, date) else None,
        "service_type": service_type,
        "attendees": max(attendees, 0),
        "status": "draft",
        "estimated_cost": estimated_cost,
        "currency": "USD",
        "service_options": _SERVICE_OPTIONS,
        "errors": errors,
        "warnings": warnings,
    }


def plan_meeting(request: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    name: str = (request.get("name") or "").strip()
    event_date = request.get("event_date")
    location: str = (request.get("location") or "").strip()
    attendees: int = request.get("attendees", 0)
    if not isinstance(attendees, int) or attendees <= 0:
        attendees = 0
    duration_hours: int = request.get("duration_hours", 1)
    if not isinstance(duration_hours, (int, float)) or duration_hours <= 0:
        duration_hours = 1
    catering: bool = request.get("catering", False)

    if not name:
        errors.append("name is required")
    if not isinstance(event_date, date):
        errors.append("event_date must be a date object")
    if not location:
        errors.append("location is required")

    base_cost = round(max(attendees, 1) * _COST_PER_ATTENDEE_MEETING, 2)
    catering_cost = round(max(attendees, 1) * _CATERING_COST_PER_HEAD, 2) if catering else 0.0
    estimated_cost = round(base_cost + catering_cost, 2)

    room_setup = "boardroom" if attendees <= 15 else "theatre" if attendees <= 50 else "banquet"

    return {
        "schema_version": SCHEMA_VERSION,
        "meeting_id": _new_id(),
        "name": name,
        "location": location,
        "event_date": event_date.isoformat() if isinstance(event_date, date) else None,
        "attendees": max(attendees, 0),
        "duration_hours": duration_hours,
        "catering": catering,
        "status": "draft",
        "estimated_cost": estimated_cost,
        "currency": "USD",
        "agenda_template": _MEETING_AGENDA_TEMPLATE,
        "room_setup": room_setup,
        "errors": errors,
        "warnings": warnings,
    }


def plan_tour(request: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    name: str = (request.get("name") or "").strip()
    destination: str = (request.get("destination") or "").strip()
    start_date = request.get("start_date")
    end_date = request.get("end_date")
    group_size: int = request.get("group_size", 0)
    if not isinstance(group_size, int) or group_size <= 0:
        group_size = 0
    budget = request.get("budget") or {}
    budget_total = float(budget.get("total", 0.0))

    if not destination:
        errors.append("destination is required")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        errors.append("start_date and end_date must be date objects")
    elif end_date <= start_date:
        errors.append("end_date must be after start_date")

    if budget_total <= 0:
        warnings.append("budget is zero or missing; cost estimates are unbounded")

    nights = max(1, (end_date - start_date).days) if isinstance(start_date, date) and isinstance(end_date, date) else 0
    estimated_cost = round(max(group_size, 1) * _COST_PER_PERSON_TOUR + nights * _TOUR_NIGHTLY, 2)

    daily_itinerary: list[dict] = []
    if isinstance(start_date, date) and isinstance(end_date, date) and nights > 0:
        for i in range(min(nights, 14)):
            day = start_date + timedelta(days=i)
            daily_itinerary.append(
                {
                    "day": i + 1,
                    "date": day.isoformat(),
                    "morning": f"Arrive at {destination} highlights"
                    if i == 0
                    else f"Explore {destination} day {i + 1}",
                    "afternoon": "Guided tour and free time",
                    "evening": "Group dinner" if i < nights - 1 else "Farewell dinner",
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "tour_id": _new_id(),
        "name": name,
        "destination": destination,
        "start_date": start_date.isoformat() if isinstance(start_date, date) else None,
        "end_date": end_date.isoformat() if isinstance(end_date, date) else None,
        "group_size": max(group_size, 0),
        "nights": nights,
        "status": "draft",
        "estimated_cost": estimated_cost,
        "currency": budget.get("currency", "USD").upper(),
        "daily_itinerary": daily_itinerary,
        "inclusions": _TOUR_INCLUSIONS,
        "errors": errors,
        "warnings": warnings,
    }


def book_activity(request: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    activity_type: str = (request.get("activity_type") or "").strip().lower()
    event_date = request.get("event_date")
    location: str = (request.get("location") or "").strip()
    participants: int = request.get("participants", 0)
    if not isinstance(participants, int) or participants <= 0:
        participants = 0
    duration_hours: int = request.get("duration_hours", 1)
    if not isinstance(duration_hours, (int, float)) or duration_hours <= 0:
        duration_hours = 1

    if not activity_type:
        errors.append("activity_type is required")
    if not isinstance(event_date, date):
        errors.append("event_date must be a date object")
    if participants <= 0:
        errors.append("participants must be positive")
    if not location:
        errors.append("location is required")

    unit_price = _ACTIVITY_PRICES.get(activity_type, _DEFAULT_ACTIVITY_COST)
    total_price = round(unit_price * max(participants, 1) * max(duration_hours, 1), 2)
    requirements = _ACTIVITY_REQUIREMENTS.get(activity_type, ["Check local guidelines"])

    return {
        "schema_version": SCHEMA_VERSION,
        "booking_id": _new_id(),
        "activity_type": activity_type,
        "location": location,
        "event_date": event_date.isoformat() if isinstance(event_date, date) else None,
        "participants": max(participants, 0),
        "duration_hours": duration_hours,
        "status": "draft",
        "total_price": total_price,
        "currency": "USD",
        "requirements": requirements,
        "errors": errors,
        "warnings": warnings,
    }


__all__ = [
    "SCHEMA_VERSION",
    "book_activity",
    "plan_funeral",
    "plan_meeting",
    "plan_tour",
    "plan_wedding",
]
