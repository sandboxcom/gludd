"""Travel core module — moved from src/general_ludd/travel/core.py.

Implements the top 6 user-visible travel functions:
  - plan_trip            construct a trip plan from origin/destination/dates
  - search_flights       search flight availability with filters
  - search_hotels        search hotel availability with filters
  - optimize_multi_stop  optimize a multi-stop route for cost/time
  - estimate_budget      produce a budget estimate for a trip
  - validate_travel_docs validate passports/visas against destination requirements
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from ansible_collections.general_ludd.travel.plugins.module_utils.knowledge import (
    _HOTEL_DB,
    _ROUGH_DISTANCES,
    _TRANSPORT_COST_PER_MILE,
    _VISA_REQUIRED,
)

SCHEMA_VERSION = "trv-001/0.1"

INCOMPLETE_INPUT: dict[str, str] = {"state": "incomplete", "reason": "missing required fields"}
NO_RESULTS: list[dict] = []
UNSUPPORTED_MODE = "unsupported_mode"

_SUPPORTED_ROUTES: dict[str, list[str]] = {
    "JFK": ["LHR", "CDG", "NRT", "LAX", "DXB"],
    "SFO": ["NRT", "LHR", "HKG", "SYD", "LAX"],
    "LHR": ["JFK", "DXB", "SIN", "CDG", "FRA"],
    "LAX": ["JFK", "NRT", "SFO", "HNL", "CDG"],
    "NYC": ["LON", "PAR", "TYO", "LAX", "MIA"],
}
_SUPPORTED_ORIGINS = frozenset(_SUPPORTED_ROUTES.keys())

_AIRLINE_DB: list[dict] = [
    {"airline": "AA", "name": "American Airlines"},
    {"airline": "BA", "name": "British Airways"},
    {"airline": "DL", "name": "Delta Air Lines"},
    {"airline": "UA", "name": "United Airlines"},
    {"airline": "JL", "name": "Japan Airlines"},
    {"airline": "CX", "name": "Cathay Pacific"},
]
_AIRLINES = {a["airline"]: a for a in _AIRLINE_DB}

_HOTEL_LOCATIONS = frozenset(_HOTEL_DB.keys())


def _new_id() -> str:
    return str(uuid.uuid4())


def _route_key(origin: str, dest: str) -> tuple[str, str]:
    return (origin.upper(), dest.upper())


def _flight_number(airline: str, dest: str) -> str:
    return f"{airline}{sum(ord(c) for c in dest) % 9000 + 100:04d}"


def _duration_minutes(origin: str, dest: str) -> int:
    dist = _ROUGH_DISTANCES.get(_route_key(origin, dest), 3000)
    return max(60, dist // 9 + 30)


def plan_trip(request: dict) -> dict:
    """Construct a trip plan from a TripRequest-like dict.

    Returns a dict with ``trip_id``, ``state``, ``segments``, and cost estimate.
    """
    errors: list[str] = []
    warnings: list[str] = []

    origin = (request.get("origin") or "").strip().upper()
    destination = (request.get("destination") or "").strip().upper()
    start_date = request.get("start_date")
    end_date = request.get("end_date")

    if not origin or not destination:
        errors.append("origin and destination are required")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        errors.append("start_date and end_date must be date objects")
    elif start_date > end_date:
        errors.append("end_date must not be before start_date")

    budget = request.get("budget") or {}
    budget_total = float(budget.get("total", 0.0))
    if budget_total <= 0:
        warnings.append("budget is zero or missing; cost estimates are unbounded")

    segments: list[dict] = []
    if origin and destination and origin != destination:
        dist = _ROUGH_DISTANCES.get((origin, destination), 3000)
        segments.append(
            {
                "segment_type": "transport",
                "from_location": origin,
                "to_location": destination,
                "mode": "flight",
                "distance_mi": dist,
                "estimated_cost": round(dist * _TRANSPORT_COST_PER_MILE["flight"], 2),
            }
        )
        if start_date and end_date:
            nights = (end_date - start_date).days
            if nights > 0:
                segments.append(
                    {
                        "segment_type": "stay",
                        "from_location": destination,
                        "to_location": destination,
                        "duration_nights": nights,
                        "estimated_cost_per_night": 250.0,
                        "estimated_cost": round(nights * 250.0, 2),
                    }
                )

    total_cost = sum(s.get("estimated_cost", 0.0) for s in segments)

    return {
        "schema_version": SCHEMA_VERSION,
        "trip_id": _new_id(),
        "origin": origin,
        "destination": destination,
        "state": "draft",
        "segments": segments,
        "total_estimated_cost": round(total_cost, 2),
        "errors": errors,
        "warnings": warnings,
    }


def search_flights(
    origin: str,
    destination: str,
    departure_date: date,
    passengers: int,
    *,
    return_date: date | None = None,
    cabin_class: str = "economy",
    max_connections: int = 2,
) -> list[dict]:
    """Search available flights.

    Returns a list of flight result dicts, empty list for no results.
    """
    origin_n = origin.strip().upper()
    dest_n = destination.strip().upper()

    if not origin_n or not dest_n:
        return []

    if origin_n not in _SUPPORTED_ORIGINS or dest_n not in _SUPPORTED_ROUTES.get(origin_n, []):
        return NO_RESULTS

    airlines_to_use = list(_AIRLINE_DB)
    results: list[dict] = []

    if max_connections <= 0:
        airline = airlines_to_use[0]
        results.append(
            {
                "flight_number": _flight_number(airline["airline"], dest_n),
                "airline": airline["airline"],
                "airline_name": airline["name"],
                "departure_airport": origin_n,
                "arrival_airport": dest_n,
                "departure_time": datetime(departure_date.year, departure_date.month, departure_date.day, 10, 0),
                "arrival_time": datetime(departure_date.year, departure_date.month, departure_date.day, 10, 0).replace(
                    hour=(10 + _duration_minutes(origin_n, dest_n) // 60) % 24
                ),
                "cabin_class": cabin_class,
                "stops": 0,
                "price": round(
                    _ROUGH_DISTANCES.get(_route_key(origin_n, dest_n), 3000)
                    * _TRANSPORT_COST_PER_MILE["flight"]
                    * passengers,
                    2,
                ),
                "currency": "USD",
            }
        )
        return results

    for idx, airline in enumerate(airlines_to_use[:3]):
        results.append(
            {
                "flight_number": _flight_number(airline["airline"], dest_n),
                "airline": airline["airline"],
                "airline_name": airline["name"],
                "departure_airport": origin_n,
                "arrival_airport": dest_n,
                "departure_time": datetime(
                    departure_date.year, departure_date.month, departure_date.day, 8 + idx * 2, 30
                ),
                "arrival_time": datetime(departure_date.year, departure_date.month, departure_date.day, 14 + idx, 15),
                "cabin_class": cabin_class,
                "stops": 0 if idx == 0 else 1,
                "price": round(
                    _ROUGH_DISTANCES.get(_route_key(origin_n, dest_n), 3000)
                    * _TRANSPORT_COST_PER_MILE["flight"]
                    * passengers
                    * (1.0 - idx * 0.1),
                    2,
                ),
                "currency": "USD",
            }
        )
    return results


def search_hotels(
    location: str,
    check_in: date,
    check_out: date,
    guests: int,
    *,
    rooms: int = 1,
) -> list[dict]:
    """Search available hotels in a location.

    Returns a list of hotel result dicts, empty list for no results.
    """
    loc = location.strip().upper()
    if not loc:
        return []

    hotels = _HOTEL_DB.get(loc)
    if hotels is None:
        return NO_RESULTS

    nights = max(1, (check_out - check_in).days)
    results: list[dict] = []
    for hotel in hotels:
        results.append(
            {
                "hotel_name": hotel["hotel_name"],
                "price_per_night": hotel["price_per_night"],
                "currency": hotel["currency"],
                "rating": hotel["rating"],
                "nights": nights,
                "rooms": rooms,
                "total_price": round(hotel["price_per_night"] * nights * rooms, 2),
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "guests": guests,
            }
        )
    return results


def optimize_multi_stop(stops: list[dict]) -> dict:
    """Optimize a multi-stop route for cost and travel time.

    Returns a dict with ``route_id``, ``optimized``, ``segments``, ``total_cost``.
    """
    if len(stops) < 2:
        return {
            "route_id": _new_id(),
            "name": "",
            "optimized": False,
            "reason": "fewer than 2 stops; nothing to optimize",
            "segments": [],
            "total_cost": 0.0,
            "warnings": [],
            "validation": [],
        }

    segments: list[dict] = []
    total_cost = 0.0
    warnings: list[str] = []
    validation: list[dict] = []

    valid_modes = frozenset({"start", "flight", "train", "bus", "car"})

    for i in range(len(stops)):
        cur = stops[i]
        if i == 0:
            if cur.get("arrival_mode", "start") != "start":
                validation.append(
                    {
                        "stop_index": i,
                        "mode_error": UNSUPPORTED_MODE,
                        "detail": f"first stop must use 'start' mode, got '{cur.get('arrival_mode')}'",
                    }
                )
            continue

        prev = stops[i - 1]
        mode = cur.get("arrival_mode", "flight")
        if mode not in valid_modes:
            validation.append({"stop_index": i, "mode_error": UNSUPPORTED_MODE, "detail": f"unsupported mode '{mode}'"})
            warnings.append(UNSUPPORTED_MODE)

        from_city: str = prev.get("city", "").upper()
        to_city: str = cur.get("city", "").upper()
        dist_key = (from_city, to_city)
        dist = _ROUGH_DISTANCES.get(dist_key, 1500)

        cost_per = _TRANSPORT_COST_PER_MILE.get(mode, 0.15)
        seg_cost = round(dist * cost_per, 2)
        total_cost += seg_cost

        segments.append(
            {
                "from_stop_index": i - 1,
                "to_stop_index": i,
                "from_city": from_city,
                "to_city": to_city,
                "mode": mode,
                "distance_mi": dist,
                "estimated_cost": seg_cost,
            }
        )

    return {
        "route_id": _new_id(),
        "name": " \u2192 ".join(s.get("city", "?") for s in stops),
        "optimized": len(stops) >= 2,
        "segments": segments,
        "total_cost": round(total_cost, 2),
        "warnings": warnings,
        "validation": validation,
    }


def estimate_budget(
    origin: str,
    destination: str,
    start_date: date,
    end_date: date,
    travelers: int,
    *,
    cabin_class: str = "economy",
    hotel_stars: int = 4,
) -> dict:
    """Produce a budget estimate for a trip.

    Returns a dict with ``currency``, ``line_items``, and ``total``.
    """
    if travelers <= 0:
        return INCOMPLETE_INPUT

    nights = max(1, (end_date - start_date).days)
    dist_key = _route_key(origin, destination)
    dist = _ROUGH_DISTANCES.get(dist_key, 3000)

    flight_cost = round(
        dist * _TRANSPORT_COST_PER_MILE["flight"] * travelers * (2.0 if cabin_class == "business" else 1.0), 2
    )
    hotel_cost = round(250.0 * nights * max(1, travelers // 2 + travelers % 2), 2)
    incidental_cost = round(nights * travelers * 80.0, 2)
    insurance_cost = round(travelers * 45.0, 2)

    total = round(flight_cost + hotel_cost + incidental_cost + insurance_cost, 2)

    return {
        "schema_version": SCHEMA_VERSION,
        "currency": "USD",
        "line_items": [
            {
                "category": "flights",
                "description": f"Round-trip {origin}-{destination} ({cabin_class})",
                "amount": flight_cost,
            },
            {"category": "hotels", "description": f"{nights} nights accommodation", "amount": hotel_cost},
            {"category": "incidentals", "description": "Meals, local transport, activities", "amount": incidental_cost},
            {"category": "insurance", "description": "Travel insurance per traveler", "amount": insurance_cost},
        ],
        "total": total,
        "state": "estimated",
    }


def validate_travel_docs(
    docs: list[dict],
    *,
    destinations: list[str] | None = None,
    visas: list[dict] | None = None,
) -> list[dict] | dict:
    """Validate travel documents against destination requirements.

    Returns a list of validation result dicts, or INCOMPLETE_INPUT if no docs.
    """
    if not docs:
        return INCOMPLETE_INPUT

    destinations = destinations or []
    visas = visas or []
    today = date.today()
    results: list[dict] = []

    for doc in docs:
        doc_type = doc.get("doc_type", "").lower()
        expiry = doc.get("expiry_date")

        if doc_type == "passport":
            if not isinstance(expiry, date) or expiry < today:
                results.append(
                    {
                        "check": "passport_expiry",
                        "status": "fail",
                        "detail": f"passport expired {expiry.isoformat() if isinstance(expiry, date) else 'unknown'}",
                    }
                )
            else:
                blank_pages = doc.get("blank_pages", 0)
                if len(destinations) > blank_pages and blank_pages < len(destinations):
                    results.append(
                        {
                            "check": "passport_blank_pages",
                            "status": "warning",
                            "detail": (
                                f"insufficient blank pages: {blank_pages} available, {len(destinations)} destinations"
                            ),
                        }
                    )
                results.append(
                    {
                        "check": "passport_expiry",
                        "status": "pass",
                        "detail": f"valid until {expiry.isoformat()}",
                    }
                )

        elif doc_type == "visa":
            if not isinstance(expiry, date) or expiry < today:
                results.append(
                    {
                        "check": "visa_validity",
                        "status": "fail",
                        "detail": f"visa expired {expiry.isoformat() if isinstance(expiry, date) else 'unknown'}",
                    }
                )
            else:
                results.append(
                    {
                        "check": "visa_validity",
                        "status": "pass",
                        "detail": f"visa valid until {expiry.isoformat()}",
                    }
                )

    for dest in destinations:
        dest_u = dest.upper()
        if dest_u in _VISA_REQUIRED:
            has_visa = any(v.get("country", "").upper() == dest_u for v in visas)
            if has_visa:
                visa = next(v for v in visas if v.get("country", "").upper() == dest_u)
                visa_expiry = visa.get("expiry")
                if isinstance(visa_expiry, date) and visa_expiry >= today:
                    results.append(
                        {
                            "check": f"visa_required_{dest_u}",
                            "status": "pass",
                            "detail": f"valid visa for {dest_u}",
                        }
                    )
                else:
                    results.append(
                        {
                            "check": f"visa_required_{dest_u}",
                            "status": "fail",
                            "detail": f"visa for {dest_u} is expired",
                        }
                    )
            else:
                results.append(
                    {
                        "check": f"visa_required_{dest_u}",
                        "status": "fail",
                        "detail": f"visa required for {dest_u} but no visa found",
                    }
                )

    return results


__all__ = [
    "INCOMPLETE_INPUT",
    "NO_RESULTS",
    "SCHEMA_VERSION",
    "UNSUPPORTED_MODE",
    "_ROUGH_DISTANCES",
    "_SUPPORTED_ROUTES",
    "_TRANSPORT_COST_PER_MILE",
    "estimate_budget",
    "optimize_multi_stop",
    "plan_trip",
    "search_flights",
    "search_hotels",
    "validate_travel_docs",
]
