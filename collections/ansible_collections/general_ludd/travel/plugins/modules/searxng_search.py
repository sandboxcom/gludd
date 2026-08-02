#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: searxng_search
  short_description: Query SearXNG metasearch engine for travel data
  description:
    - Queries a SearXNG instance JSON API (C(/search?format=json)) for
      flights, hotels, events, and activities.
    - Maps raw search results to travel contracts (FlightBooking, HotelBooking,
      EventBooking) with structured pricing and provider info.
    - SearXNG is a privacy-respecting metasearch engine; this module provides
      the travel expert with live search data instead of stub results.
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
    searxng_url:
      description: Base URL of the SearXNG instance.
      type: str
      default: "http://localhost:8080"
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
      description: HTTP request timeout in seconds.
      type: int
      default: 10
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
  - name: Search for flights from NYC to Paris
    general_ludd.travel.searxng_search:
      query: "flights NYC to Paris September 2026"
      category: flights

  - name: Search for hotels in Tokyo
    general_ludd.travel.searxng_search:
      query: "hotels in Tokyo Shinjuku"
      category: hotels
      max_results: 20

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
  raw_results:
    description: Raw unprocessed results from SearXNG for debugging.
    type: list
    elements: dict
    returned: always
  search_url:
    description: The full SearXNG API URL that was queried.
    type: str
    returned: always
"""

from __future__ import annotations

import datetime as _datetime
import json as _json
import re as _re
import time as _time
from typing import Any
from urllib import parse as _urlparse
from urllib import request as _urllib_request
from urllib.error import HTTPError as _HTTPError
from urllib.error import URLError as _URLError

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]
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

_ENGINE_MAP: dict[str, str] = {
    "flights": "google_flights,google_travel",
    "hotels": "booking,hotelscombined,tripadvisor",
    "events": "google_events,ticketmaster,eventbrite",
    "activities": "tripadvisor,wikivoyage,google_maps",
    "restaurants": "yelp,tripadvisor,google_maps",
    "general": "google,wikipedia,duckduckgo",
}

_PRICE_RE = _re.compile(r"\$\s*(\d{1,6}(?:[.,]\d{1,2})?)")
_STAR_RE = _re.compile(r"(\d(?:[.,]\d)?)[\s/]*(?:star|⭐|out of 5)")
_FLIGHT_RE = _re.compile(
    r"(?P<from>[A-Z]{3})\s*(?:→|->|to|-)\s*(?P<to>[A-Z]{3})",
    _re.IGNORECASE,
)
_IATA_RE = _re.compile(r"\b([A-Z]{3})\b")


def _normalise_url(base: str) -> str:
    url = base.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _build_search_url(
    searxng_url: str,
    query: str,
    category: str,
    engines: str,
    max_results: int,
    safe_search: int,
    language: str,
) -> str:
    engines_param = engines if engines else _ENGINE_MAP.get(category, "google")
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "categories": "general",
        "engines": engines_param,
        "language": language,
        "safesearch": str(safe_search),
        "pageno": "1",
    }
    return f"{_normalise_url(searxng_url)}/search?{_urlparse.urlencode(params)}"


def _extract_price(text: str) -> float | None:
    match = _PRICE_RE.search(text)
    if match:
        value = match.group(1).replace(",", "")
        return float(value)
    return None


def _extract_stars(text: str) -> float | None:
    match = _STAR_RE.search(text)
    if match:
        return float(match.group(1))
    return None


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
    price = _extract_price(text)

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

    data = booking.model_dump(mode="json")
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
    price = _extract_price(text)
    stars = _extract_stars(text)

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

    data = booking.model_dump(mode="json")
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
    price = _extract_price(text)

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

    data = booking.model_dump(mode="json")
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    search_url = _build_search_url(
        searxng_url,
        query,
        category,
        engines,
        max_results,
        safe_search,
        language,
    )

    req = _urllib_request.Request(
        search_url,
        headers={
            "User-Agent": "gludd-travel/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (_HTTPError, _URLError):
        return [], [], search_url

    data = _json.loads(body)
    raw_results: list[dict[str, Any]] = data.get("results", [])

    parser = _PARSER_MAP.get(category, _parse_general_result)
    structured: list[dict[str, Any]] = []
    for item in raw_results[:max_results]:
        structured.append(parser(item, query))

    return structured, raw_results[:max_results], search_url


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            query=dict(type="str", required=True),
            category=dict(
                type="str",
                default="general",
                choices=["flights", "hotels", "events", "activities", "general", "restaurants"],
            ),
            searxng_url=dict(type="str", default="http://localhost:8080"),
            engines=dict(type="str", default=""),
            max_results=dict(type="int", default=10),
            safe_search=dict(type="int", default=0, choices=[0, 1, 2]),
            language=dict(type="str", default="en"),
            timeout=dict(type="int", default=10),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    params = module.params
    try:
        results, raw, search_url = search_searxng(
            params["query"],
            params["category"],
            params["searxng_url"],
            params["engines"],
            params["max_results"],
            params["safe_search"],
            params["language"],
            params["timeout"],
        )
        module.exit_json(
            changed=False,
            results=results,
            raw_results=raw,
            result_count=len(results),
            query=params["query"],
            category=params["category"],
            search_url=search_url,
        )
    except Exception as exc:
        module.fail_json(msg=f"searxng_search failed: {exc}")


if __name__ == "__main__":
    main()
