#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: hotel_search
  short_description: Search hotels at a destination with filters for dates, budget, and amenities
  description:
    - Queries the travel accommodation engine for available hotels matching criteria.
    - Supports check-in/check-out dates, guest count, star rating, price range,
      amenities, and proximity preferences.
    - Returns a ranked list of hotel options with pricing, ratings, and amenity details.
  options:
    destination:
      description: City name or location to search hotels near.
      type: str
      required: true
    check_in:
      description: Check-in date (YYYY-MM-DD).
      type: str
      required: true
    check_out:
      description: Check-out date (YYYY-MM-DD).
      type: str
      required: true
    guests:
      description: Number of guests.
      type: int
      default: 1
    rooms:
      description: Number of rooms.
      type: int
      default: 1
    min_stars:
      description: Minimum star rating (1-5).
      type: int
      default: 0
    max_price_per_night:
      description: Maximum price per night per room in USD.
      type: float
      default: 0.0
    amenities:
      description: Required amenities.
      type: list
      elements: str
      default: []
    sort_by:
      description: Sort results by this field.
      type: str
      default: price
      choices: [price, rating, distance, name]
    currency:
      description: Currency code for prices.
      type: str
      default: "USD"
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""

EXAMPLES:
  - name: Search hotels in Paris
    general_ludd.travel.hotel_search:
      destination: "Paris"
      check_in: "2026-09-01"
      check_out: "2026-09-05"
      guests: 2
      min_stars: 3
      amenities: ["wifi", "breakfast"]
    register: results

RETURN:
  hotels:
    description: Ranked list of hotel options.
    type: list
    elements: dict
    returned: always
  search_params:
    description: Echo of search parameters used.
    type: dict
    returned: always
  total_nights:
    description: Number of nights for the stay.
    type: int
    returned: always
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]
from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    HotelBooking,
    HotelCancellationTerms,
    HotelRate,
    HotelSearch,
    Money,
    ProviderInfo,
    RoomType,
)


def _make_hotel_search(
    destination: str,
    check_in_str: str,
    check_out_str: str,
    guests: int,
    rooms: int,
) -> HotelSearch:
    cin = _datetime.date.fromisoformat(check_in_str)
    cout = _datetime.date.fromisoformat(check_out_str)
    return HotelSearch(
        location=destination,
        check_in=cin,
        check_out=cout,
        guests=guests,
        rooms=rooms,
    )


def _hotel_booking_from_stub(
    destination: str,
    name: str,
    stars: int,
    price_per_night: float,
    rating: float,
    amenity_list: list[str],
    distance_km: float,
    currency: str,
    check_in: str,
    check_out: str,
    rooms: int,
) -> HotelBooking:
    cin = _datetime.date.fromisoformat(check_in)
    cout = _datetime.date.fromisoformat(check_out)
    nights = max(1, (cout - cin).days)

    room = RoomType(
        name="Standard",
        beds="Queen" if stars >= 4 else "Double",
        max_occupancy=2,
        price_per_night=price_per_night,
        currency=currency,
    )

    total = price_per_night * nights * rooms
    rate = HotelRate(
        base_per_night=Money(amount=price_per_night, currency=currency),
        total_amount=Money(amount=total, currency=currency),
        cancellation=HotelCancellationTerms(non_refundable=False, policy_text="Free cancellation 24h before check-in"),
    )

    return HotelBooking(
        confirmation_code=f"{name[:6].upper()}-{_datetime.datetime.now().strftime('%Y%m%d')}",
        hotel_name=name,
        address=f"{destination} City Center",
        room=room,
        check_in=cin,
        check_out=cout,
        total_price=total,
        currency=currency,
        status=BookingStatus.draft,
        property_id=name.lower().replace(" ", "-"),
        rate=rate,
        provider=ProviderInfo(
            source="stub",
            offer_id=f"hotel-{name.lower().replace(' ', '-')}",
            retrieved_at=_datetime.datetime.now(),
        ),
    )


def search_hotels(
    destination: str,
    check_in: str,
    check_out: str,
    guests: int,
    rooms: int,
    min_stars: int,
    max_price_per_night: float,
    amenities: list[str],
    sort_by: str,
    currency: str,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    _search = _make_hotel_search(destination, check_in, check_out, guests, rooms)

    fmt = "%Y-%m-%d"
    cin = _datetime.datetime.strptime(check_in, fmt)
    cout = _datetime.datetime.strptime(check_out, fmt)
    nights = max(1, (cout - cin).days)

    raw_hotels = [
        {
            "name": f"{destination} Grand Hotel",
            "stars": 4,
            "price_per_night": 220.0,
            "rating": 4.5,
            "amenities": ["wifi", "pool", "gym", "breakfast"],
            "distance_km": 1.2,
        },
        {
            "name": f"{destination} Budget Inn",
            "stars": 2,
            "price_per_night": 85.0,
            "rating": 3.8,
            "amenities": ["wifi"],
            "distance_km": 3.5,
        },
    ]

    bookings: list[dict[str, Any]] = []
    for h in raw_hotels:
        if min_stars > 0 and h["stars"] < min_stars:
            continue
        if max_price_per_night > 0 and h["price_per_night"] > max_price_per_night:
            continue
        if amenities and not all(a in h["amenities"] for a in amenities):
            continue

        booking = _hotel_booking_from_stub(
            destination=destination,
            name=h["name"],
            stars=h["stars"],
            price_per_night=h["price_per_night"],
            rating=h["rating"],
            amenity_list=h["amenities"],
            distance_km=h["distance_km"],
            currency=currency,
            check_in=check_in,
            check_out=check_out,
            rooms=rooms,
        )
        booking_dict = booking.model_dump(mode="json")
        booking_dict["stars"] = h["stars"]
        booking_dict["rating"] = h["rating"]
        booking_dict["amenities"] = h["amenities"]
        booking_dict["distance_km"] = h["distance_km"]
        bookings.append(booking_dict)

    if sort_by == "price":
        bookings.sort(key=lambda b: b.get("stars", 0))
    elif sort_by == "rating":
        bookings.sort(key=lambda b: b.get("rating", 0), reverse=True)
    elif sort_by == "distance":
        bookings.sort(key=lambda b: b.get("distance_km", 999))
    elif sort_by == "name":
        bookings.sort(key=lambda b: b.get("hotel_name", ""))

    return bookings, nights, _search.model_dump(mode="json")


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            destination=dict(type="str", required=True),
            check_in=dict(type="str", required=True),
            check_out=dict(type="str", required=True),
            guests=dict(type="int", default=1),
            rooms=dict(type="int", default=1),
            min_stars=dict(type="int", default=0),
            max_price_per_night=dict(type="float", default=0.0),
            amenities=dict(type="list", elements="str", default=[]),
            sort_by=dict(type="str", default="price", choices=["price", "rating", "distance", "name"]),
            currency=dict(type="str", default="USD"),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    params = module.params
    try:
        hotels, nights, _search_params = search_hotels(
            params["destination"],
            params["check_in"],
            params["check_out"],
            params["guests"],
            params["rooms"],
            params["min_stars"],
            params["max_price_per_night"],
            params["amenities"],
            params["sort_by"],
            params["currency"],
        )
        module.exit_json(
            changed=False,
            hotels=hotels,
            total_nights=nights,
            search_params={
                "destination": params["destination"],
                "check_in": params["check_in"],
                "check_out": params["check_out"],
                "guests": params["guests"],
                "rooms": params["rooms"],
            },
        )
    except Exception as exc:
        module.fail_json(msg=f"hotel_search failed: {exc}")


if __name__ == "__main__":
    main()
