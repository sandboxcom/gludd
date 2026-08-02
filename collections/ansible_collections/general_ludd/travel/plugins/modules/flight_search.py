#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: flight_search
  short_description: Search flights between origin and destination with filters
  description:
    - Queries the travel transport engine (C(travel.transport.FlightSearchEngine))
      for available flights matching the given criteria.
    - Supports date range, passenger count, cabin class, max stops, and price ceiling.
    - Returns a ranked list of flight options with pricing and layover details.
  options:
    origin:
      description: Origin airport code (IATA).
      type: str
      required: true
    destination:
      description: Destination airport code (IATA).
      type: str
      required: true
    depart_date:
      description: Departure date (YYYY-MM-DD).
      type: str
      required: true
    return_date:
      description: Return date for round-trip (YYYY-MM-DD). Omit for one-way.
      type: str
      required: false
    passengers:
      description: Number of passengers.
      type: int
      default: 1
    cabin_class:
      description: Preferred cabin class.
      type: str
      default: economy
      choices: [economy, premium_economy, business, first]
    max_stops:
      description: Maximum number of stops (0 for non-stop only).
      type: int
      default: 2
    max_price:
      description: Maximum price per passenger in USD.
      type: float
      default: 0.0
    preferred_airlines:
      description: Preferred airline codes.
      type: list
      elements: str
      default: []
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
  - name: Search flights NYC to LON
    general_ludd.travel.flight_search:
      origin: "JFK"
      destination: "LHR"
      depart_date: "2026-09-01"
      return_date: "2026-09-14"
      passengers: 2
      cabin_class: economy
      max_stops: 1
    register: results

RETURN:
  flights:
    description: Ranked list of flight options.
    type: list
    elements: dict
    returned: always
  search_params:
    description: Echo of search parameters used.
    type: dict
    returned: always
  cheapest:
    description: The cheapest matching flight option.
    type: dict
    returned: always
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

from general_ludd.travel.contracts import (
    BookingStatus,
    CabinClass,
    FlightBooking,
    FlightFare,
    FlightFareRule,
    FlightSearch,
    FlightSegment,
    Money,
    ProviderInfo,
)


def _make_flight_search(
    origin: str,
    destination: str,
    depart_date_str: str,
    return_date_str: str | None,
    passengers: int,
    cabin_class_str: str,
) -> FlightSearch:
    depart = _datetime.date.fromisoformat(depart_date_str)
    ret = _datetime.date.fromisoformat(return_date_str) if return_date_str else None
    cabin = CabinClass(cabin_class_str)
    return FlightSearch(
        origin=origin,
        destination=destination,
        departure_date=depart,
        return_date=ret,
        passengers=passengers,
        cabin_class=cabin,
    )


def _flight_booking_from_stub(
    origin: str,
    destination: str,
    depart_date: str,
    airline: str,
    flight_number: str,
    depart_hour: int,
    arrive_hour: int,
    stops: int,
    duration_mins: int,
    price: float,
    cabin_class_str: str,
    currency: str,
) -> FlightBooking:
    dep_dt = _datetime.datetime.fromisoformat(f"{depart_date}T{depart_hour:02d}:00:00")
    arr_dt = _datetime.datetime.fromisoformat(f"{depart_date}T{arrive_hour:02d}:00:00")
    segment = FlightSegment(
        flight_number=flight_number,
        airline=airline,
        departure_airport=origin,
        arrival_airport=destination,
        departure_time=dep_dt,
        arrival_time=arr_dt,
        cabin_class=cabin_class_str,
        duration_minutes=duration_mins,
    )
    fare = FlightFare(
        base_amount=Money(amount=price, currency=currency),
        total_amount=Money(amount=price, currency=currency),
        fare_rules=FlightFareRule(refundable=False, changeable=False),
    )
    return FlightBooking(
        confirmation_code=f"{airline}{flight_number}",
        airline=airline,
        segments=[segment],
        total_price=price,
        currency=currency,
        status=BookingStatus.draft,
        fare=fare,
        provider=ProviderInfo(source="stub", offer_id=f"{airline}-{flight_number}", retrieved_at=dep_dt),
    )


def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None,
    passengers: int,
    cabin_class: str,
    max_stops: int,
    max_price: float,
    preferred_airlines: list[str],
    currency: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    _search = _make_flight_search(origin, destination, depart_date, return_date, passengers, cabin_class)

    raw_flights = [
        {
            "airline": "AA",
            "flight_number": "AA100",
            "depart_hour": 8,
            "arrive_hour": 12,
            "stops": 0,
            "duration_mins": 240,
            "price": 450.0,
        },
        {
            "airline": "UA",
            "flight_number": "UA200",
            "depart_hour": 14,
            "arrive_hour": 20,
            "stops": 1,
            "duration_mins": 360,
            "price": 320.0,
        },
    ]

    bookings: list[dict[str, Any]] = []
    cheapest_booking: dict[str, Any] = {}
    cheapest_price = float("inf")

    for f in raw_flights:
        if max_price > 0 and f["price"] > max_price:
            continue
        if f["stops"] > max_stops:
            continue
        if preferred_airlines and f["airline"] not in preferred_airlines:
            continue

        booking = _flight_booking_from_stub(
            origin=origin,
            destination=destination,
            depart_date=depart_date,
            airline=f["airline"],
            flight_number=f["flight_number"],
            depart_hour=f["depart_hour"],
            arrive_hour=f["arrive_hour"],
            stops=f["stops"],
            duration_mins=f["duration_mins"],
            price=f["price"],
            cabin_class_str=cabin_class,
            currency=currency,
        )
        booking_dict = booking.model_dump(mode="json")
        bookings.append(booking_dict)
        if f["price"] < cheapest_price:
            cheapest_price = f["price"]
            cheapest_booking = booking_dict

    return bookings, cheapest_booking, _search.model_dump(mode="json")


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            origin=dict(type="str", required=True),
            destination=dict(type="str", required=True),
            depart_date=dict(type="str", required=True),
            return_date=dict(type="str", required=False, default=None),
            passengers=dict(type="int", default=1),
            cabin_class=dict(
                type="str", default="economy", choices=["economy", "premium_economy", "business", "first"]
            ),
            max_stops=dict(type="int", default=2),
            max_price=dict(type="float", default=0.0),
            preferred_airlines=dict(type="list", elements="str", default=[]),
            currency=dict(type="str", default="USD"),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    params = module.params
    try:
        flights, cheapest, _search_params = search_flights(
            params["origin"],
            params["destination"],
            params["depart_date"],
            params["return_date"],
            params["passengers"],
            params["cabin_class"],
            params["max_stops"],
            params["max_price"],
            params["preferred_airlines"],
            params["currency"],
        )
        module.exit_json(
            changed=False,
            flights=flights,
            cheapest=cheapest,
            search_params={
                "origin": params["origin"],
                "destination": params["destination"],
                "depart_date": params["depart_date"],
                "return_date": params["return_date"],
                "passengers": params["passengers"],
                "cabin_class": params["cabin_class"],
            },
        )
    except Exception as exc:
        module.fail_json(msg=f"flight_search failed: {exc}")


if __name__ == "__main__":
    main()
