"""Travel transport module — flight, train, car rental search and booking.
Moved from src/general_ludd/travel/transport.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    CarRental,
    FlightBooking,
    FlightFare,
    FlightSearch,
    FlightSegment,
    ProviderInfo,
    TrainBooking,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.core import (
    _ROUGH_DISTANCES,
    _TRANSPORT_COST_PER_MILE,
    search_flights,
)

_SEAT_SIDES = {
    "window": ("A", "F"),
    "aisle": ("C", "D"),
    "middle": ("B", "E"),
}

_CAR_TYPES = ["economy", "compact", "midsize", "fullsize", "suv", "luxury"]
_CAR_COMPANIES = ["Hertz", "Avis", "Enterprise", "Budget", "National"]

_TRAIN_OPERATORS = ["Eurostar", "Amtrak", "SNCF", "Deutsche Bahn", "JR", "Trenitalia"]
_BUS_OPERATORS = ["Greyhound", "FlixBus", "Megabus", "National Express"]


def _confirm_code() -> str:
    return str(uuid.uuid4())[:8].upper()


def _seat_side(position: str, row: int) -> dict:
    sides = _SEAT_SIDES.get(position, ("A", "F"))
    side = sides[row % len(sides)]
    return {"row": row, "position": position, "side": side}


class FlightSearchEngine:
    def __init__(self, max_results: int = 5) -> None:
        self._max_results = max_results

    def search(self, flight_search: FlightSearch) -> list[dict]:
        results = search_flights(
            origin=flight_search.origin,
            destination=flight_search.destination,
            departure_date=flight_search.departure_date,
            passengers=flight_search.passengers,
            return_date=flight_search.return_date,
            cabin_class=flight_search.cabin_class.value,
        )
        return results[: self._max_results]


class FlightBooker:
    def book(
        self,
        result: dict,
        passengers: int,
        *,
        fare: FlightFare | None = None,
        provider: ProviderInfo | None = None,
    ) -> FlightBooking:
        if "flight_number" not in result:
            raise ValueError("flight result missing required field: flight_number")

        airline_code = result.get("airline", "")
        departure_time = result.get("departure_time", datetime.now())
        arrival_time = result.get("arrival_time", departure_time)

        segment = FlightSegment(
            flight_number=result.get("flight_number", ""),
            airline=airline_code,
            departure_airport=result.get("departure_airport", ""),
            arrival_airport=result.get("arrival_airport", ""),
            departure_time=departure_time,
            arrival_time=arrival_time,
            cabin_class=result.get("cabin_class", "economy"),
            duration_minutes=int((arrival_time - departure_time).total_seconds() / 60)
            if isinstance(departure_time, datetime) and isinstance(arrival_time, datetime)
            else None,
        )

        price = result.get("price", 0.0)
        currency = result.get("currency", "USD")

        return FlightBooking(
            confirmation_code=_confirm_code(),
            airline=airline_code,
            segments=[segment],
            total_price=float(price),
            currency=currency,
            status=BookingStatus.draft,
            fare=fare,
            provider=provider,
            expires_at=datetime.now().replace(hour=23, minute=59, second=59),
        )

    def cancel(self, booking: FlightBooking) -> FlightBooking:
        return booking.model_copy(update={"status": BookingStatus.cancelled})


class TrainSearch:
    def __init__(self) -> None:
        pass

    def search(
        self,
        origin: str,
        destination: str,
        departure_date: date,
        passengers: int = 1,
        *,
        seat_class: str = "standard",
        max_results: int = 5,
    ) -> list[TrainBooking]:
        origin_u = origin.strip().upper()
        dest_u = destination.strip().upper()
        if not origin_u or not dest_u or origin_u == dest_u:
            return []

        dist_key = (origin_u, dest_u)
        dist = _ROUGH_DISTANCES.get(dist_key, 1500)
        cost_per = _TRANSPORT_COST_PER_MILE.get("train", 0.08)

        bookings: list[TrainBooking] = []
        for i in range(min(max_results, len(_TRAIN_OPERATORS))):
            operator = _TRAIN_OPERATORS[i]
            base_minutes = (i * 15) % 60
            dep_hour = (7 + i * 2 + (i * 15) // 60) % 24
            dur_minutes = max(30, dist // 2 + 20)
            dep_time = datetime(departure_date.year, departure_date.month, departure_date.day, dep_hour, base_minutes)
            total_minutes = base_minutes + dur_minutes
            arr_hour = (dep_hour + total_minutes // 60) % 24
            arr_minute = total_minutes % 60
            arr_time = dep_time.replace(hour=arr_hour, minute=arr_minute)

            bookings.append(
                TrainBooking(
                    confirmation_code=_confirm_code(),
                    operator=operator,
                    departure_station=origin_u,
                    arrival_station=dest_u,
                    departure_time=dep_time,
                    arrival_time=arr_time,
                    seat_class=seat_class,
                    total_price=round(dist * cost_per * passengers * (1.0 - i * 0.05), 2),
                    currency="USD",
                    train_number=f"{operator[:2].upper()}{departure_date.day:02d}{i + 1:03d}",
                )
            )
        return bookings


class CarRentalSearch:
    def __init__(self) -> None:
        pass

    def search(
        self,
        location: str,
        pickup_date: date,
        dropoff_date: date,
        *,
        max_results: int = 5,
    ) -> list[CarRental]:
        if dropoff_date < pickup_date:
            return []

        loc = location.strip().upper()
        if not loc:
            return []

        days = max(1, (dropoff_date - pickup_date).days)
        base_daily: dict[str, float] = {
            "economy": 35.0,
            "compact": 45.0,
            "midsize": 55.0,
            "fullsize": 70.0,
            "suv": 90.0,
            "luxury": 150.0,
        }

        results: list[CarRental] = []
        for i in range(min(max_results, len(_CAR_TYPES))):
            car_type = _CAR_TYPES[i]
            daily_rate = base_daily.get(car_type, 50.0)
            company = _CAR_COMPANIES[i % len(_CAR_COMPANIES)]

            results.append(
                CarRental(
                    confirmation_code=_confirm_code(),
                    pickup_location=loc,
                    dropoff_location=loc,
                    pickup_date=pickup_date,
                    dropoff_date=dropoff_date,
                    car_type=car_type,
                    total_price=round(daily_rate * days, 2),
                    currency="USD",
                    rental_company=company,
                    includes_insurance=(car_type in ("fullsize", "suv", "luxury")),
                )
            )
        return results


class SeatSelector:
    def __init__(self) -> None:
        pass

    def select(self, row: int, position: str) -> dict:
        if position not in _SEAT_SIDES:
            position = "window"
        return _seat_side(position, row)

    def assign_auto(self, preferences: dict) -> dict:
        row_min = max(1, int(preferences.get("row_min", 1)))
        row_max = min(60, int(preferences.get("row_max", 60)))
        if row_min > row_max:
            row_min, row_max = row_max, row_min

        row = hash(str(preferences)) % (row_max - row_min + 1) + row_min

        if preferences.get("prefer_aisle"):
            return {"row": row, "position": "aisle", "side": "C"}
        if preferences.get("prefer_window"):
            return {"row": row, "position": "window", "side": "A"}

        positions = list(_SEAT_SIDES.keys())
        pos = positions[abs(hash(str(preferences))) % len(positions)]
        return _seat_side(pos, row)


__all__ = [
    "CarRentalSearch",
    "FlightBooker",
    "FlightSearchEngine",
    "SeatSelector",
    "TrainSearch",
]
