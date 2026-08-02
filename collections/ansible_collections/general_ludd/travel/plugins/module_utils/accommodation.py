"""Travel accommodation module — moved from src/general_ludd/travel/accommodation.py.

Classes:
  HotelSearchEngine - search hotels by location/filters
  HotelBooker - book a hotel room, confirm/cancel bookings
  RoomComparator - compare rooms by price/value/occupancy
  AmenityFilter - filter hotels/rooms by required and preferred amenities
"""

from __future__ import annotations

import uuid
from datetime import date

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    HotelBooking,
    HotelSearch,
    RoomType,
)

_HOTEL_DB: dict[str, list[dict]] = {
    "NYC": [
        {
            "hotel_name": "Midtown Grand",
            "address": "100 Broadway, New York, NY",
            "property_id": "NYC-001",
            "rating": 4.5,
            "amenities": ["wifi", "pool", "gym", "restaurant", "parking"],
            "rooms": [
                {
                    "name": "Standard",
                    "beds": "1 Queen",
                    "max_occupancy": 2,
                    "price_per_night": 200.0,
                    "currency": "USD",
                },
                {"name": "Deluxe", "beds": "1 King", "max_occupancy": 2, "price_per_night": 320.0, "currency": "USD"},
                {"name": "Suite", "beds": "2 Queen", "max_occupancy": 4, "price_per_night": 500.0, "currency": "USD"},
            ],
        },
        {
            "hotel_name": "SoHo Boutique",
            "address": "55 Greene St, New York, NY",
            "property_id": "NYC-002",
            "rating": 4.2,
            "amenities": ["wifi", "restaurant", "spa"],
            "rooms": [
                {"name": "Classic", "beds": "1 Queen", "max_occupancy": 2, "price_per_night": 280.0, "currency": "USD"},
                {"name": "Loft", "beds": "1 King", "max_occupancy": 3, "price_per_night": 420.0, "currency": "USD"},
            ],
        },
        {
            "hotel_name": "Brooklyn Lodge",
            "address": "200 Atlantic Ave, Brooklyn, NY",
            "property_id": "NYC-003",
            "rating": 3.8,
            "amenities": ["wifi"],
            "rooms": [
                {"name": "Basic", "beds": "1 Double", "max_occupancy": 2, "price_per_night": 150.0, "currency": "USD"},
            ],
        },
    ],
    "LON": [
        {
            "hotel_name": "Kensington Palace Hotel",
            "address": "10 Kensington High St, London",
            "property_id": "LON-001",
            "rating": 4.4,
            "amenities": ["wifi", "restaurant", "gym", "concierge"],
            "rooms": [
                {
                    "name": "Classic",
                    "beds": "1 Double",
                    "max_occupancy": 2,
                    "price_per_night": 280.0,
                    "currency": "GBP",
                },
                {
                    "name": "Executive",
                    "beds": "1 King",
                    "max_occupancy": 2,
                    "price_per_night": 450.0,
                    "currency": "GBP",
                },
            ],
        },
        {
            "hotel_name": "Shoreditch House",
            "address": "1 Ebor St, London",
            "property_id": "LON-002",
            "rating": 4.3,
            "amenities": ["wifi", "pool", "spa", "restaurant"],
            "rooms": [
                {"name": "Small", "beds": "1 Double", "max_occupancy": 1, "price_per_night": 220.0, "currency": "GBP"},
                {"name": "Medium", "beds": "1 Queen", "max_occupancy": 2, "price_per_night": 360.0, "currency": "GBP"},
            ],
        },
        {
            "hotel_name": "Camden Hostel",
            "address": "25 Camden High St, London",
            "property_id": "LON-003",
            "rating": 3.5,
            "amenities": ["wifi", "laundry"],
            "rooms": [
                {"name": "Dorm", "beds": "1 Bunk", "max_occupancy": 1, "price_per_night": 45.0, "currency": "GBP"},
            ],
        },
    ],
    "PAR": [
        {
            "hotel_name": "Le Marais Grand",
            "address": "30 Rue de Rivoli, Paris",
            "property_id": "PAR-001",
            "rating": 4.6,
            "amenities": ["wifi", "restaurant", "concierge", "spa", "gym"],
            "rooms": [
                {"name": "Chambre", "beds": "1 Queen", "max_occupancy": 2, "price_per_night": 350.0, "currency": "EUR"},
                {"name": "Suite", "beds": "1 King", "max_occupancy": 3, "price_per_night": 580.0, "currency": "EUR"},
            ],
        },
        {
            "hotel_name": "Montmartre View",
            "address": "12 Rue des Abbesses, Paris",
            "property_id": "PAR-002",
            "rating": 4.1,
            "amenities": ["wifi", "parking"],
            "rooms": [
                {
                    "name": "Standard",
                    "beds": "1 Double",
                    "max_occupancy": 2,
                    "price_per_night": 190.0,
                    "currency": "EUR",
                },
            ],
        },
    ],
    "TYO": [
        {
            "hotel_name": "Shinjuku Tower",
            "address": "1-1 Nishi-Shinjuku, Tokyo",
            "property_id": "TYO-001",
            "rating": 4.7,
            "amenities": ["wifi", "pool", "gym", "restaurant", "concierge", "spa"],
            "rooms": [
                {
                    "name": "Standard",
                    "beds": "1 Double",
                    "max_occupancy": 2,
                    "price_per_night": 28000.0,
                    "currency": "JPY",
                },
                {"name": "Deluxe", "beds": "1 King", "max_occupancy": 2, "price_per_night": 45000.0, "currency": "JPY"},
            ],
        },
        {
            "hotel_name": "Asakusa Inn",
            "address": "2-3 Asakusa, Taito, Tokyo",
            "property_id": "TYO-002",
            "rating": 4.0,
            "amenities": ["wifi", "laundry"],
            "rooms": [
                {
                    "name": "Tatami",
                    "beds": "2 Futon",
                    "max_occupancy": 2,
                    "price_per_night": 12000.0,
                    "currency": "JPY",
                },
            ],
        },
    ],
    "LAX": [
        {
            "hotel_name": "Santa Monica Shoreline",
            "address": "100 Ocean Ave, Santa Monica, CA",
            "property_id": "LAX-001",
            "rating": 4.3,
            "amenities": ["wifi", "pool", "gym", "parking"],
            "rooms": [
                {
                    "name": "Ocean View",
                    "beds": "1 King",
                    "max_occupancy": 2,
                    "price_per_night": 340.0,
                    "currency": "USD",
                },
                {"name": "Garden", "beds": "2 Double", "max_occupancy": 4, "price_per_night": 280.0, "currency": "USD"},
            ],
        },
        {
            "hotel_name": "Hollywood Hills Inn",
            "address": "7000 Hollywood Blvd, Los Angeles, CA",
            "property_id": "LAX-002",
            "rating": 3.9,
            "amenities": ["wifi", "parking"],
            "rooms": [
                {
                    "name": "Standard",
                    "beds": "1 Queen",
                    "max_occupancy": 2,
                    "price_per_night": 210.0,
                    "currency": "USD",
                },
            ],
        },
    ],
    "MIA": [
        {
            "hotel_name": "South Beach Resort",
            "address": "100 Ocean Dr, Miami Beach, FL",
            "property_id": "MIA-001",
            "rating": 4.6,
            "amenities": ["wifi", "pool", "spa", "restaurant", "gym"],
            "rooms": [
                {
                    "name": "Oceanfront",
                    "beds": "1 King",
                    "max_occupancy": 2,
                    "price_per_night": 380.0,
                    "currency": "USD",
                },
                {
                    "name": "Poolside",
                    "beds": "2 Queen",
                    "max_occupancy": 4,
                    "price_per_night": 450.0,
                    "currency": "USD",
                },
            ],
        },
        {
            "hotel_name": "Coral Gables Inn",
            "address": "200 Miracle Mile, Coral Gables, FL",
            "property_id": "MIA-002",
            "rating": 4.0,
            "amenities": ["wifi", "parking"],
            "rooms": [
                {
                    "name": "Standard",
                    "beds": "1 Queen",
                    "max_occupancy": 2,
                    "price_per_night": 180.0,
                    "currency": "USD",
                },
            ],
        },
    ],
    "DXB": [
        {
            "hotel_name": "Deira Gold Souk Hotel",
            "address": "Al Rigga Rd, Deira, Dubai",
            "property_id": "DXB-001",
            "rating": 4.4,
            "amenities": ["wifi", "pool", "restaurant", "concierge"],
            "rooms": [
                {"name": "Deluxe", "beds": "1 King", "max_occupancy": 2, "price_per_night": 450.0, "currency": "AED"},
                {"name": "Premium", "beds": "2 Queen", "max_occupancy": 4, "price_per_night": 680.0, "currency": "AED"},
            ],
        },
    ],
}


def _new_id() -> str:
    return str(uuid.uuid4())


class HotelSearchEngine:
    def __init__(self) -> None:
        self._db = _HOTEL_DB

    def search(
        self,
        search: HotelSearch,
        *,
        max_price: float | None = None,
        min_rating: float = 0.0,
        hotel_name: str | None = None,
    ) -> list[dict]:
        loc = search.location.strip().upper()
        hotels = self._db.get(loc)
        if hotels is None:
            return []

        nights = max(1, (search.check_out - search.check_in).days)
        results: list[dict] = []

        for hotel in hotels:
            if hotel_name is not None and hotel["hotel_name"] != hotel_name:
                continue
            if hotel["rating"] < min_rating:
                continue
            for room in hotel["rooms"]:
                price = room["price_per_night"]
                if max_price is not None and price > max_price:
                    continue
                results.append(
                    {
                        "hotel_name": hotel["hotel_name"],
                        "address": hotel["address"],
                        "property_id": hotel["property_id"],
                        "rating": hotel["rating"],
                        "amenities": list(hotel["amenities"]),
                        "room_name": room["name"],
                        "beds": room["beds"],
                        "max_occupancy": room["max_occupancy"],
                        "price_per_night": price,
                        "currency": room["currency"],
                        "rooms": search.rooms,
                        "nights": nights,
                        "total_price": round(price * nights * search.rooms, 2),
                        "check_in": search.check_in.isoformat(),
                        "check_out": search.check_out.isoformat(),
                        "guests": search.guests,
                    }
                )
        return results


class HotelBooker:
    def __init__(self) -> None:
        self._bookings: dict[str, HotelBooking] = {}

    def book(
        self,
        hotel_name: str,
        room: RoomType,
        check_in: date,
        check_out: date,
        *,
        address: str = "N/A",
        currency: str = "",
        property_id: str | None = None,
    ) -> HotelBooking:
        nights = max(1, (check_out - check_in).days)
        total = round(room.price_per_night * nights, 2)
        cur = currency if currency else room.currency

        booking = HotelBooking(
            confirmation_code=f"H{_new_id()[:8].upper()}",
            hotel_name=hotel_name,
            address=address,
            room=room,
            check_in=check_in,
            check_out=check_out,
            total_price=total,
            currency=cur,
            status=BookingStatus.draft,
            property_id=property_id,
        )
        self._bookings[booking.booking_id] = booking
        return booking

    def confirm(self, booking_id: str) -> HotelBooking:
        booking = self._bookings[booking_id]
        confirmed = HotelBooking(
            confirmation_code=booking.confirmation_code,
            hotel_name=booking.hotel_name,
            address=booking.address,
            room=booking.room,
            check_in=booking.check_in,
            check_out=booking.check_out,
            total_price=booking.total_price,
            currency=booking.currency,
            status=BookingStatus.confirmed,
            property_id=booking.property_id,
        )
        confirmed.booking_id = booking.booking_id
        self._bookings[booking.booking_id] = confirmed
        return confirmed

    def cancel(self, booking_id: str) -> HotelBooking:
        booking = self._bookings[booking_id]
        cancelled = HotelBooking(
            confirmation_code=booking.confirmation_code,
            hotel_name=booking.hotel_name,
            address=booking.address,
            room=booking.room,
            check_in=booking.check_in,
            check_out=booking.check_out,
            total_price=booking.total_price,
            currency=booking.currency,
            status=BookingStatus.cancelled,
            property_id=booking.property_id,
        )
        cancelled.booking_id = booking.booking_id
        self._bookings[booking.booking_id] = cancelled
        return cancelled

    def get_booking(self, booking_id: str) -> HotelBooking | None:
        return self._bookings.get(booking_id)


class RoomComparator:
    @staticmethod
    def by_price(rooms: list[RoomType]) -> list[RoomType]:
        return sorted(rooms, key=lambda r: r.price_per_night)

    @staticmethod
    def by_value(rooms: list[RoomType]) -> list[RoomType]:
        return sorted(rooms, key=lambda r: r.price_per_night / r.max_occupancy)

    @staticmethod
    def best_value(rooms: list[RoomType]) -> RoomType:
        if not rooms:
            raise ValueError("no rooms to compare")
        return min(rooms, key=lambda r: r.price_per_night / r.max_occupancy)

    @staticmethod
    def by_occupancy(rooms: list[RoomType]) -> list[RoomType]:
        return sorted(rooms, key=lambda r: r.max_occupancy, reverse=True)


class AmenityFilter:
    def __init__(self, required: list[str] | None = None, preferred: list[str] | None = None) -> None:
        self.required = [a.lower() for a in (required or [])]
        self.preferred = [a.lower() for a in (preferred or [])]

    def filter_hotels(self, hotels: list[dict]) -> list[dict]:
        if not self.required:
            return list(hotels)

        result: list[dict] = []
        for hotel in hotels:
            amenities_lower = {a.lower() for a in hotel.get("amenities", [])}
            if all(req in amenities_lower for req in self.required):
                result.append(hotel)
        return result

    def score_hotels(self, hotels: list[dict]) -> list[dict]:
        scored: list[dict] = []
        for hotel in hotels:
            amenities_lower = {a.lower() for a in hotel.get("amenities", [])}
            matched = [p for p in self.preferred if p in amenities_lower]
            missing = [p for p in self.preferred if p not in amenities_lower]
            scored.append(
                {
                    **hotel,
                    "amenity_score": len(matched),
                    "matched_amenities": matched,
                    "missing_amenities": missing,
                }
            )
        return sorted(scored, key=lambda h: h["amenity_score"], reverse=True)


__all__ = [
    "AmenityFilter",
    "HotelBooker",
    "HotelSearchEngine",
    "RoomComparator",
]
