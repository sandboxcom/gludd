"""Structured JSON output parser for SearXNG travel results.

Parses raw SearXNG JSON API results into validated Pydantic travel
contracts (FlightBooking, HotelBooking, EventBooking).  Falls back to
partial results when JSON is unstructured or missing fields.

Usage in a module
-----------------
    from ansible_collections.general_ludd.travel.plugins.module_utils.output_parser import (
        JsonOutputParser,
    )

    parser = JsonOutputParser()
    flights = parser.parse_flights(raw_results)
    hotels = parser.parse_hotels(raw_results)
"""

from __future__ import annotations

import datetime as _datetime
import json as _json
import re as _re
import time as _time
from typing import Any

from ansible_collections.general_ludd.agent.plugins.module_utils.searxng import (
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

_IATA_RE = _re.compile(r"\b([A-Z]{3})\b")
_STRUCTURED_KEY = _re.compile(
    r"(?:flight|hotel|room|rate|fare|booking|itinerary|reservation)",
    _re.IGNORECASE,
)
_CURRENCY_RE = _re.compile(r"\b(USD|EUR|GBP|JPY|AUD|CAD|CHF|CNY|INR)\b")


def _extract_airport_codes(text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in _IATA_RE.finditer(text.upper()):
        code = match.group(1)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _find_nested_key(obj: dict[str, Any], key: str) -> Any:
    """Find a key anywhere in a nested dict (depth-first, first match)."""
    if key in obj:
        return obj[key]
    for v in obj.values():
        if isinstance(v, dict):
            result = _find_nested_key(v, key)
            if result is not None:
                return result
    return None


def _find_nested_value(obj: Any, keys: list[str]) -> Any:
    """Try each key in order across nested dicts, returning the first match."""
    for k in keys:
        val = _find_nested_key(obj, k) if isinstance(obj, dict) else None
        if val is not None:
            return val
    return None


def _detect_currency(text: str) -> str:
    match = _CURRENCY_RE.search(text)
    return match.group(1) if match else "USD"


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Attempt to parse a string as JSON, returning None on failure."""
    try:
        return _json.loads(text)  # type: ignore[no-any-return]
    except (_json.JSONDecodeError, TypeError):
        pass
    json_match = _re.search(r"\{.*\}", text, _re.DOTALL)
    if json_match:
        try:
            return _json.loads(json_match.group(0))  # type: ignore[no-any-return]
        except (_json.JSONDecodeError, TypeError):
            pass
    return None


class JsonOutputParser:
    """Parse SearXNG JSON results into structured travel contracts.

    Uses pydantic validation so malformed data is caught early.  When a
    result field is missing or unparseable, the parser builds a partial
    contract with sensible defaults rather than raising — callers always
    get a list of contracts back.
    """

    def __init__(self) -> None:
        self._now = _datetime.datetime.now(_datetime.UTC)
        self._today = _datetime.date.today()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_flights(self, raw_results: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
        """Parse raw SearXNG results into FlightBooking dicts.

        Returns a list of dicts — each is a FlightBooking serialised with
        ``model_dump(mode="json")`` plus ``title`` and ``url`` keys.
        """
        results: list[dict[str, Any]] = []
        for item in raw_results:
            parsed = self._parse_single_flight(item)
            if parsed is not None:
                results.append(parsed)
        return results

    def parse_hotels(self, raw_results: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
        """Parse raw SearXNG results into HotelBooking dicts."""
        results: list[dict[str, Any]] = []
        for item in raw_results:
            parsed = self._parse_single_hotel(item)
            if parsed is not None:
                results.append(parsed)
        return results

    def parse_events(self, raw_results: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
        """Parse raw SearXNG results into EventBooking dicts."""
        results: list[dict[str, Any]] = []
        for item in raw_results:
            parsed = self._parse_single_event(item)
            if parsed is not None:
                results.append(parsed)
        return results

    # ------------------------------------------------------------------
    # Single-result parsers
    # ------------------------------------------------------------------

    def _parse_single_flight(self, item: dict[str, Any]) -> dict[str, Any] | None:
        title = str(item.get("title", ""))
        snippet = str(item.get("content", item.get("snippet", "")))
        url = str(item.get("url", ""))
        text = f"{title} {snippet}"

        json_data = _try_parse_json(text)
        structured = json_data is not None and bool(_STRUCTURED_KEY.search(str(json_data)))

        if structured and isinstance(json_data, dict):
            return self._flight_from_json(json_data, title, url)

        return self._flight_from_text(item, text, title, url)

    def _parse_single_hotel(self, item: dict[str, Any]) -> dict[str, Any] | None:
        title = str(item.get("title", ""))
        snippet = str(item.get("content", item.get("snippet", "")))
        url = str(item.get("url", ""))
        text = f"{title} {snippet}"

        json_data = _try_parse_json(text)
        structured = json_data is not None and bool(_STRUCTURED_KEY.search(str(json_data)))

        if structured and isinstance(json_data, dict):
            return self._hotel_from_json(json_data, title, url)

        return self._hotel_from_text(item, text, title, url)

    def _parse_single_event(self, item: dict[str, Any]) -> dict[str, Any] | None:
        title = str(item.get("title", ""))
        snippet = str(item.get("content", item.get("snippet", "")))
        url = str(item.get("url", ""))
        text = f"{title} {snippet}"

        price = extract_price(text)

        booking = EventBooking(
            event_type=EventKind.conference,
            name=title[:200],
            location="TBD",
            venue=str(item.get("engine", "unknown")),
            event_date=self._today,
            total_price=price if price else 0.0,
            currency="USD",
            status=BookingStatus.draft,
            provider=ProviderInfo(
                source="searxng",
                offer_id=url[:100],
                retrieved_at=self._now,
            ),
        )

        data = booking.model_dump(mode="json")
        data["title"] = title
        data["url"] = url
        data["snippet"] = snippet
        return data

    # ------------------------------------------------------------------
    # JSON-structured parsers
    # ------------------------------------------------------------------

    def _flight_from_json(self, data: dict[str, Any], title: str, url: str) -> dict[str, Any]:
        airline = str(_find_nested_value(data, ["airline", "carrier", "operator", "provider"]) or "unknown")
        price_val = _safe_float(
            _find_nested_value(
                data,
                ["price", "total_price", "fare", "amount", "cost", "total"],
            ),
            350.0,
        )

        codes = _extract_airport_codes(str(data))
        origin = codes[0] if len(codes) >= 1 else "???"
        destination = codes[1] if len(codes) >= 2 else "???"

        segment = FlightSegment(
            flight_number=str(_find_nested_value(data, ["flight_number", "flight_no"]) or "SRNX-001"),
            airline=airline,
            departure_airport=origin,
            arrival_airport=destination,
            departure_time=self._now.replace(hour=8, minute=0, second=0, microsecond=0),
            arrival_time=self._now.replace(hour=12, minute=0, second=0, microsecond=0),
            cabin_class="economy",
            duration_minutes=None,
        )

        currency = str(_find_nested_value(data, ["currency", "currency_code"]) or "USD").upper()
        fare = FlightFare(
            base_amount=Money(amount=price_val, currency=currency),
            total_amount=Money(amount=price_val, currency=currency),
            fare_rules=FlightFareRule(refundable=False, changeable=False),
        )

        booking = FlightBooking(
            confirmation_code=f"JSON-{_time.time_ns() % 1000000:06d}",
            airline=airline,
            segments=[segment],
            total_price=price_val,
            currency=currency,
            status=BookingStatus.draft,
            fare=fare,
            provider=ProviderInfo(
                source="searxng",
                offer_id=url[:100],
                retrieved_at=self._now,
            ),
        )

        out = booking.model_dump(mode="json")
        out["title"] = title
        out["url"] = url
        return out

    def _hotel_from_json(self, data: dict[str, Any], title: str, url: str) -> dict[str, Any]:
        hotel_name = str(_find_nested_value(data, ["hotel_name", "name", "hotel", "property"]) or title[:120])
        address = str(_find_nested_value(data, ["address", "location", "street"]) or "")
        price_val = _safe_float(
            _find_nested_value(
                data,
                [
                    "price_per_night",
                    "price",
                    "rate",
                    "amount",
                    "cost",
                    "total_price",
                ],
            ),
            150.0,
        )
        stars = _safe_float(_find_nested_value(data, ["stars", "rating", "star_rating"]), 0.0)
        currency = str(_find_nested_value(data, ["currency", "currency_code"]) or "USD").upper()

        room_name = str(_find_nested_value(data, ["room_type", "room", "room_name"]) or "Standard")
        beds = str(_find_nested_value(data, ["beds", "bed_type", "bed"]) or "Queen")
        occupancy = _safe_int(_find_nested_value(data, ["max_occupancy", "occupancy", "guests"]), 2)

        room = RoomType(
            name=room_name,
            beds=beds,
            max_occupancy=occupancy,
            price_per_night=price_val,
            currency=currency,
        )

        rate = HotelRate(
            base_per_night=Money(amount=price_val, currency=currency),
            total_amount=Money(amount=price_val * 3, currency=currency),
            cancellation=HotelCancellationTerms(
                non_refundable=False,
                policy_text="Free cancellation 24h before check-in",
            ),
        )

        booking = HotelBooking(
            confirmation_code=f"JSON-{_time.time_ns() % 1000000:06d}",
            hotel_name=hotel_name[:120],
            address=address[:200] if address else title[:200],
            room=room,
            check_in=self._today,
            check_out=self._today + _datetime.timedelta(days=3),
            total_price=price_val * 3,
            currency=currency,
            status=BookingStatus.draft,
            property_id=hotel_name.lower().replace(" ", "-")[:50],
            rate=rate,
            provider=ProviderInfo(
                source="searxng",
                offer_id=url[:100],
                retrieved_at=self._now,
            ),
        )

        out = booking.model_dump(mode="json")
        out["title"] = title
        out["url"] = url
        if stars:
            out["stars"] = stars
        return out

    # ------------------------------------------------------------------
    # Text-based fallback parsers
    # ------------------------------------------------------------------

    def _flight_from_text(self, item: dict[str, Any], text: str, title: str, url: str) -> dict[str, Any]:
        codes = _extract_airport_codes(text)
        origin = codes[0] if len(codes) >= 1 else "???"
        destination = codes[1] if len(codes) >= 2 else "???"
        price = extract_price(text)

        segment = FlightSegment(
            flight_number="SRNX-001",
            airline=str(item.get("engine", "unknown")).split(",")[0].strip(),
            departure_airport=origin,
            arrival_airport=destination,
            departure_time=self._now.replace(hour=8, minute=0, second=0, microsecond=0),
            arrival_time=self._now.replace(hour=12, minute=0, second=0, microsecond=0),
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
                retrieved_at=self._now,
            ),
        )

        out = booking.model_dump(mode="json")
        out["title"] = title
        out["url"] = url
        return out

    def _hotel_from_text(self, item: dict[str, Any], text: str, title: str, url: str) -> dict[str, Any]:
        price = extract_price(text)
        stars = extract_stars(text)

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
            address=str(item.get("content", ""))[:200] or title,
            room=room,
            check_in=self._today,
            check_out=self._today + _datetime.timedelta(days=3),
            total_price=rate.total_amount.amount if rate.total_amount else 0.0,
            currency="USD",
            status=BookingStatus.draft,
            property_id=title.lower().replace(" ", "-")[:50],
            rate=rate,
            provider=ProviderInfo(
                source="searxng",
                offer_id=url[:100],
                retrieved_at=self._now,
            ),
        )

        out = booking.model_dump(mode="json")
        out["title"] = title
        out["url"] = url
        if stars:
            out["stars"] = stars
        return out
