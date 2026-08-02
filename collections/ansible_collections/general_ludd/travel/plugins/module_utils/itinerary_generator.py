"""Sample itinerary generator — produces multi-day itineraries using existing contracts.

Generates sample Itinerary objects from the contracts module with realistic
timeline entries, route stops, documents needed, and emergency contacts.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    DocumentNeeded,
    EmergencyContact,
    Itinerary,
    ItineraryStatus,
    RouteStop,
    StopForecast,
    TimelineEntry,
    WeatherForecast,
    WeatherForecastEntry,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.knowledge import (
    iata_to_city,
    iata_to_name,
)

_EMERGENCY_CONTACTS: dict[str, list[tuple[str, str, bool]]] = {
    "USA": [("police", "911", True), ("hospital", "911", True), ("embassy", "+1-202-501-4444", False)],
    "GBR": [("police", "999", True), ("hospital", "999", True), ("embassy", "+44-20-7499-9000", False)],
    "FRA": [("police", "17", True), ("hospital", "15", True), ("embassy", "+33-1-43-12-22-22", False)],
    "JPN": [("police", "110", True), ("hospital", "119", True), ("embassy", "+81-3-3224-5000", False)],
    "DEU": [("police", "110", True), ("hospital", "112", True), ("embassy", "+49-30-8305-0", False)],
    "ESP": [("police", "112", True), ("hospital", "061", True), ("embassy", "+34-91-587-2200", False)],
    "ITA": [("police", "112", True), ("hospital", "118", True), ("embassy", "+39-06-4674-1", False)],
    "AUS": [("police", "000", True), ("hospital", "000", True), ("embassy", "+61-2-6214-5600", False)],
    "ARE": [("police", "999", True), ("hospital", "998", True), ("embassy", "+971-2-414-2200", False)],
    "SGP": [("police", "999", True), ("hospital", "995", True), ("embassy", "+65-6476-9100", False)],
}

_SAMPLE_DOCS: dict[str, list[tuple[str, str, str]]] = {
    "USA": [("passport", "USA", "valid"), ("visa", "USA", "not_required")],
    "GBR": [("passport", "GBR", "valid"), ("visa", "GBR", "not_required")],
    "FRA": [("passport", "FRA", "valid"), ("visa", "FRA", "not_required")],
    "JPN": [("passport", "JPN", "valid"), ("visa", "JPN", "not_required")],
    "DEU": [("passport", "DEU", "valid"), ("visa", "DEU", "not_required")],
    "ESP": [("passport", "ESP", "valid"), ("visa", "ESP", "not_required")],
    "ITA": [("passport", "ITA", "valid"), ("visa", "ITA", "not_required")],
    "CHN": [("passport", "CHN", "valid"), ("visa", "CHN", "required")],
    "IND": [("passport", "IND", "valid"), ("visa", "IND", "required")],
    "EGY": [("passport", "EGY", "valid"), ("visa", "EGY", "required")],
    "AUS": [("passport", "AUS", "valid"), ("visa", "AUS", "not_required")],
    "ARE": [("passport", "ARE", "valid"), ("visa", "ARE", "not_required")],
    "SGP": [("passport", "SGP", "valid"), ("visa", "SGP", "not_required")],
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _make_timeline(
    stops: list[RouteStop],
    start_date: date,
    end_date: date,
    timezone: str = "UTC",
) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    total_days = max(1, (end_date - start_date).days)
    days_per_stop = max(1, total_days // max(1, len(stops)))

    idx = 0
    for si, stop in enumerate(stops):
        for d in range(days_per_stop):
            day = start_date + timedelta(days=si * days_per_stop + d)
            if day > end_date:
                break
            morning_start = datetime(day.year, day.month, day.day, 9, 0)
            morning_end = datetime(day.year, day.month, day.day, 12, 0)
            entries.append(
                TimelineEntry(
                    entry_index=idx,
                    type="sightseeing",
                    start_time=morning_start,
                    end_time=morning_end,
                    timezone=timezone,
                    location=stop.city,
                    details=f"Morning exploration of {stop.city} — day {d + 1}",
                )
            )
            idx += 1

            afternoon_start = datetime(day.year, day.month, day.day, 13, 0)
            afternoon_end = datetime(day.year, day.month, day.day, 17, 0)
            entries.append(
                TimelineEntry(
                    entry_index=idx,
                    type="activity",
                    start_time=afternoon_start,
                    end_time=afternoon_end,
                    timezone=timezone,
                    location=stop.city,
                    details=f"Afternoon activities in {stop.city}",
                )
            )
            idx += 1

            evening_start = datetime(day.year, day.month, day.day, 18, 30)
            evening_end = datetime(day.year, day.month, day.day, 21, 0)
            entries.append(
                TimelineEntry(
                    entry_index=idx,
                    type="dinner",
                    start_time=evening_start,
                    end_time=evening_end,
                    timezone=timezone,
                    location=stop.city,
                    details=f"Dinner in {stop.city}",
                )
            )
            idx += 1
    return entries


def _make_docs(cc: str) -> list[DocumentNeeded]:
    recs = _SAMPLE_DOCS.get(cc, [("passport", cc, "valid")])
    return [DocumentNeeded(type=t, country=c, status=s) for t, c, s in recs]


def _make_emergency(cc: str) -> list[EmergencyContact]:
    recs = _EMERGENCY_CONTACTS.get(cc, [("police", "112", True)])
    return [EmergencyContact(type=t, country=cc, number=n, available_24h=a) for t, n, a in recs]


def _make_weather(start_date: date, end_date: date) -> WeatherForecast:
    total_days = max(1, (end_date - start_date).days)
    entries: list[WeatherForecastEntry] = []
    for d in range(min(total_days, 14)):
        day = start_date + timedelta(days=d)
        entries.append(
            WeatherForecastEntry(
                date=day,
                temp_high_c=22.0 + (d % 7) * 1.5,
                temp_low_c=12.0 + (d % 7) * 1.0,
                conditions="sunny" if d % 3 == 0 else "partly cloudy" if d % 3 == 1 else "cloudy",
                precipitation_mm=0.0 if d % 3 == 0 else 1.5,
                wind_kmh=10.0 + (d % 5) * 3.0,
                uv_index=5.0 + (d % 4) * 1.5,
            )
        )
    return WeatherForecast(
        retrieved_at=datetime.now(),
        source="sample_generator",
        stops=[
            StopForecast(stop_index=0, forecast=entries),
        ],
    )


def generate(
    cities: list[str],
    start_date: date,
    end_date: date,
    *,
    country_codes: list[str] | None = None,
    timezone: str = "UTC",
) -> Itinerary:
    if not cities:
        raise ValueError("must provide at least one city")
    if end_date <= start_date:
        raise ValueError("end_date must be after start_date")

    if country_codes is None:
        country_codes = ["USA"] * len(cities)
    if len(country_codes) < len(cities):
        country_codes = list(country_codes) + ["USA"] * (len(cities) - len(country_codes))

    stops = [
        RouteStop(
            stop_index=i,
            city=city,
            country=cc,
            arrival_mode="start" if i == 0 else "flight",
            dwell_hours=48.0,
            timezone=timezone,
        )
        for i, (city, cc) in enumerate(zip(cities, country_codes, strict=False))
    ]

    timeline = _make_timeline(stops, start_date, end_date, timezone)
    docs = list({d.country: d for d in [d for cc in country_codes for d in _make_docs(cc)]}.values())
    emergency = list({e.country: e for e in [e_ for cc in country_codes for e_ in _make_emergency(cc)]}.values())
    weather = _make_weather(start_date, end_date)

    itinerary_id = _new_id()
    request_id = _new_id()

    return Itinerary(
        itinerary_id=itinerary_id,
        request_id=request_id,
        status=ItineraryStatus.draft,
        timeline=timeline,
        documents_needed=docs,
        emergency_contacts=emergency,
        weather_forecast=weather,
    )


_TEMPLATES: dict[str, list[str]] = {
    "western_europe": ["LHR", "CDG", "FRA", "MAD"],
    "east_asia": ["NRT", "ICN", "PVG", "HKG"],
    "north_america": ["JFK", "LAX", "ORD", "MIA"],
    "southeast_asia": ["BKK", "KUL", "SIN", "MNL"],
    "middle_east": ["DXB", "AUH", "DOH", "IST"],
    "oceania": ["SYD", "MEL", "AKL"],
    "mediterranean": ["BCN", "FCO", "IST", "ATH"],
    "capitals": ["LHR", "CDG", "FRA", "NRT", "SYD"],
}


def generate_from_template(
    template: str,
    start_date: date,
    end_date: date,
    *,
    timezone: str = "UTC",
) -> Itinerary:
    iata_codes = _TEMPLATES.get(template)
    if iata_codes is None:
        raise ValueError(f"unknown template: '{template}'. Available: {list(_TEMPLATES)}")
    cities = [iata_to_city.get(code, code) for code in iata_codes]
    country_codes = [(iata_to_name.get(code, "") and _resolve_country(code)) or "USA" for code in iata_codes]
    return generate(cities, start_date, end_date, country_codes=country_codes, timezone=timezone)


def list_templates() -> list[str]:
    return list(_TEMPLATES.keys())


def _resolve_country(iata_code: str) -> str:
    from ansible_collections.general_ludd.travel.plugins.module_utils.knowledge import (
        iata_to_country,
    )

    return iata_to_country.get(iata_code, "USA")


__all__ = [
    "generate",
    "generate_from_template",
    "list_templates",
]
