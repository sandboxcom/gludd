#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: searxng_search
  short_description: Search SearXNG natively on the Ansible controller
  description:
    - The paired action plugin calls the official SearXNG Python WSGI app.
    - Native mode opens no listener and transfers no Python runtime to hosts.
    - Remote HTTP is available only through an explicit compatibility mode.
  options:
    query:
      description: Free-text search query for SearXNG.
      type: str
      required: true
    category:
      description: Travel search category to constrain results.
      type: str
      default: general
      choices: [flights, hotels, events, activities, general, restaurants]
    transport:
      description: Search transport; native is the primary local integration.
      type: str
      choices: [native, remote]
      default: native
    remote_url:
      description: Explicit remote compatibility endpoint.
      type: str
    namespace:
      description: Native project resource namespace.
      type: str
      default: gludd-travel
    settings_path:
      description: Optional native SearXNG settings path on the controller.
      type: path
    engines:
      description: Comma-separated list of SearXNG engines to use. Overrides
        the per-category defaults.
      type: str
      default: ""
    max_results:
      description: Maximum number of results to return.
      type: int
      default: 10
    safe_search:
      description: Safe search level.
      type: int
      default: 0
      choices: [0, 1, 2]
    language:
      description: Language code for results.
      type: str
      default: "en"
    timeout:
      description: Remote compatibility timeout in seconds.
      type: int
      default: 10
    structured:
      description:
        - When true, uses JsonOutputParser to parse flight/hotel results
          from structured JSON embedded in SearXNG result text.
        - When false (default), uses the legacy regex-based extract_price/
          extract_stars parsers.
      type: bool
      default: false

EXAMPLES:
  - name: Search for flights from NYC to Paris
    general_ludd.travel.searxng_search:
      query: "flights NYC to Paris September 2026"
      category: flights

  - name: Search for hotels in Tokyo
    general_ludd.travel.searxng_search:
      query: "hotels in Tokyo Shinjuku"
      category: hotels
      max_results: 20

  - name: Search for flights with structured JSON parsing
    general_ludd.travel.searxng_search:
      query: "flights NYC to Paris September 2026"
      category: flights
      structured: true

  - name: Search for hotels with structured JSON parsing
    general_ludd.travel.searxng_search:
      query: "hotels in Tokyo Shinjuku"
      category: hotels
      structured: true

  - name: Search for activities in Barcelona
    general_ludd.travel.searxng_search:
      query: "things to do in Barcelona Spain"
      category: activities

RETURN:
  results:
    description: Structured search results matching travel contracts.
    type: list
    elements: dict
    returned: always
  query:
    description: Echo of the search query used.
    type: str
    returned: always
  category:
    description: The search category applied.
    type: str
    returned: always
  result_count:
    description: Number of results returned.
    type: int
    returned: always
  instance_uri:
    description: Native identity or explicit remote endpoint used.
    type: str
    returned: always
  transport:
    description: Selected native or remote transport.
    type: str
    returned: always
"""

from __future__ import annotations

import dataclasses as _dc
import datetime as _datetime
import re as _re
import time as _time
from typing import Any, cast

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.general_ludd.agent.plugins.module_utils.searxng import (
    SearXNGClient,
    extract_price,
    extract_stars,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    BookingStatus,
    EventBooking,
    EventKind,
    FlightBooking,
    FlightFare,
    FlightFareRule,
    FlightSegment,
    HotelBooking,
    HotelCancellationTerms,
    HotelRate,
    Money,
    ProviderInfo,
    RoomType,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.output_parser import (
    JsonOutputParser,
)

_FLIGHT_RE = _re.compile(
    r"(?P<from>[A-Z]{3})\s*(?:→|->|to|-)\s*(?P<to>[A-Z]{3})",
    _re.IGNORECASE,
)
_IATA_RE = _re.compile(r"\b([A-Z]{3})\b")


def _extract_airport_codes(text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in _IATA_RE.finditer(text.upper()):
        code = match.group(1)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _parse_flight_result(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    title = result.get("title", "")
    snippet = result.get("content", result.get("snippet", ""))
    url = result.get("url", "")
    text = f"{title} {snippet}"
    codes = _extract_airport_codes(text)
    origin = codes[0] if len(codes) >= 1 else "???"
    destination = codes[1] if len(codes) >= 2 else "???"
    price = extract_price(text)

    now = _datetime.datetime.now(_datetime.UTC)
    segment = FlightSegment(
        flight_number="SRNX-001",
        airline=result.get("engine", "unknown").split(",")[0].strip(),
        departure_airport=origin,
        arrival_airport=destination,
        departure_time=now.replace(hour=8, minute=0, second=0, microsecond=0),
        arrival_time=now.replace(hour=12, minute=0, second=0, microsecond=0),
        cabin_class="economy",
        duration_minutes=None,
    )

    fare_amount = price if price else 350.0
    fare = FlightFare(
        base_amount=Money(amount=fare_amount, currency="USD"),
        total_amount=Money(amount=fare_amount, currency="USD"),
        fare_rules=FlightFareRule(refundable=False, changeable=False),
    )

    booking = FlightBooking(
        confirmation_code=f"SRNX-{_time.time_ns() % 1000000:06d}",
        airline=segment.airline,
        segments=[segment],
        total_price=fare_amount,
        currency="USD",
        status=BookingStatus.draft,
        fare=fare,
        provider=ProviderInfo(
            source="searxng",
            offer_id=url[:100],
            retrieved_at=now,
        ),
    )

    data = cast(dict[str, Any], booking.model_dump(mode="json"))
    data["title"] = title
    data["url"] = url
    return data


def _parse_hotel_result(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    title = result.get("title", "")
    snippet = result.get("content", result.get("snippet", ""))
    url = result.get("url", "")
    text = f"{title} {snippet}"
    price = extract_price(text)
    stars = extract_stars(text)

    today = _datetime.date.today()
    now_htl = _datetime.datetime.now(_datetime.UTC)
    room = RoomType(
        name="Standard" if (stars or 3) >= 4 else "Classic",
        beds="Queen",
        max_occupancy=2,
        price_per_night=price if price else 150.0,
        currency="USD",
    )

    rate = HotelRate(
        base_per_night=Money(amount=room.price_per_night, currency="USD"),
        total_amount=Money(amount=room.price_per_night * 3, currency="USD"),
        cancellation=HotelCancellationTerms(
            non_refundable=False,
            policy_text="Free cancellation 24h before check-in",
        ),
    )

    booking = HotelBooking(
        confirmation_code=f"SRNX-{_time.time_ns() % 1000000:06d}",
        hotel_name=title[:120],
        address=snippet[:200] if snippet else "",
        room=room,
        check_in=today,
        check_out=today + _datetime.timedelta(days=3),
        total_price=rate.total_amount.amount if rate.total_amount else 0.0,
        currency="USD",
        status=BookingStatus.draft,
        property_id=title.lower().replace(" ", "-")[:50],
        rate=rate,
        provider=ProviderInfo(
            source="searxng",
            offer_id=url[:100],
            retrieved_at=now_htl,
        ),
    )

    data = cast(dict[str, Any], booking.model_dump(mode="json"))
    data["title"] = title
    data["url"] = url
    if stars:
        data["stars"] = stars
    return data


def _parse_event_result(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    title = result.get("title", "")
    snippet = result.get("content", result.get("snippet", ""))
    url = result.get("url", "")
    text = f"{title} {snippet}"
    price = extract_price(text)

    now_evt = _datetime.datetime.now(_datetime.UTC)
    booking = EventBooking(
        event_type=EventKind.conference,
        name=title[:200],
        location="TBD",
        venue=result.get("engine", "unknown"),
        event_date=_datetime.date.today(),
        total_price=price if price else 0.0,
        currency="USD",
        status=BookingStatus.draft,
        provider=ProviderInfo(
            source="searxng",
            offer_id=url[:100],
            retrieved_at=now_evt,
        ),
    )

    data = cast(dict[str, Any], booking.model_dump(mode="json"))
    data["title"] = title
    data["url"] = url
    data["snippet"] = snippet
    return data


def _parse_activity_result(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    title = result.get("title", "")
    snippet = result.get("content", result.get("snippet", ""))
    url = result.get("url", "")

    return {
        "title": title,
        "description": snippet,
        "url": url,
        "source": result.get("engine", "unknown"),
        "category": result.get("category", "general"),
    }


def _parse_general_result(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    title = result.get("title", "")
    snippet = result.get("content", result.get("snippet", ""))
    url = result.get("url", "")
    engines = result.get("engines", result.get("engine", "unknown"))
    score = result.get("score", 0.0)

    return {
        "title": title,
        "snippet": snippet,
        "url": url,
        "source": engines if isinstance(engines, str) else ",".join(engines),
        "score": score,
        "category": result.get("category", "general"),
    }


_TRAVEL_ENGINE_MAP: dict[str, list[str]] = {
    "flights": ["google_flights", "google_travel"],
    "hotels": ["booking", "hotelscombined", "tripadvisor"],
    "events": ["google_events", "ticketmaster", "eventbrite"],
    "activities": ["tripadvisor", "wikivoyage", "google_maps"],
    "restaurants": ["yelp", "tripadvisor", "google_maps"],
    "general": ["google", "wikipedia", "duckduckgo"],
}


_PARSER_MAP: dict[str, Any] = {
    "flights": _parse_flight_result,
    "hotels": _parse_hotel_result,
    "events": _parse_event_result,
    "activities": _parse_activity_result,
    "restaurants": _parse_general_result,
    "general": _parse_general_result,
}


def search_searxng(
    query: str,
    category: str,
    searxng_url: str,
    engines: str,
    max_results: int,
    safe_search: int,
    language: str,
    timeout: int,
    structured: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    engines_list = [e.strip() for e in engines.split(",") if e.strip()] if engines else _TRAVEL_ENGINE_MAP.get(category)

    client = SearXNGClient(base_url=searxng_url, timeout=timeout)
    resp = client.search(
        query=query,
        max_results=max_results,
        categories=["general"],
        engines=engines_list,
        language=language,
        safe_search=safe_search,
    )

    search_url = f"{client.base_url}/search?q={query}"
    raw_results = [_dc.asdict(r) for r in resp.results]

    if structured:
        parser_obj = JsonOutputParser()
        if category == "flights":
            parsed: list[dict[str, Any]] = parser_obj.parse_flights(raw_results[:max_results], query)
        elif category == "hotels":
            parsed = parser_obj.parse_hotels(raw_results[:max_results], query)
        elif category == "events":
            parsed = parser_obj.parse_events(raw_results[:max_results], query)
        else:
            parsed = [_parse_general_result(item, query) for item in raw_results[:max_results]]
        return parsed, raw_results[:max_results], search_url

    parser = _PARSER_MAP.get(category, _parse_general_result)
    structured_results: list[dict[str, Any]] = []
    for item in raw_results[:max_results]:
        structured_results.append(parser(item, query))

    return structured_results, raw_results[:max_results], search_url


def main() -> None:
    """Fail closed when Ansible bypasses the controller action plugin."""
    module = AnsibleModule(
        argument_spec=dict(
            query=dict(type="str", required=True),
            category=dict(
                type="str",
                default="general",
                choices=["flights", "hotels", "events", "activities", "general", "restaurants"],
            ),
            transport=dict(type="str", default="native", choices=["native", "remote"]),
            remote_url=dict(type="str", required=False),
            namespace=dict(type="str", default="gludd-travel"),
            settings_path=dict(type="path", default=""),
            engines=dict(type="str", default=""),
            max_results=dict(type="int", default=10),
            safe_search=dict(type="int", default=0, choices=[0, 1, 2]),
            language=dict(type="str", default="en"),
            timeout=dict(type="int", default=10),
            structured=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
        required_if=[("transport", "remote", ("remote_url",))],
    )
    module.fail_json(msg="searxng_search requires its controller-side action plugin")


if __name__ == "__main__":
    main()
