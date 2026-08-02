"""Unit tests for travel itinerary_generator.py module_utils."""

from __future__ import annotations

from datetime import date

import pytest
from ansible_collections.general_ludd.travel.plugins.module_utils.contracts import (
    DocumentNeeded,
    EmergencyContact,
    Itinerary,
    ItineraryStatus,
    WeatherForecast,
)
from ansible_collections.general_ludd.travel.plugins.module_utils.itinerary_generator import (
    generate,
    generate_from_template,
    list_templates,
)


class TestGenerate:
    def test_single_city_creates_itinerary(self):
        itin = generate(
            cities=["Paris"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 4),
        )
        assert isinstance(itin, Itinerary)
        assert itin.status == ItineraryStatus.draft
        assert len(itin.timeline) > 0

    def test_multi_city_creates_itinerary(self):
        itin = generate(
            cities=["New York", "London", "Paris"],
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 10),
        )
        assert isinstance(itin, Itinerary)
        assert itin.status == ItineraryStatus.draft
        assert len(itin.timeline) >= 9

    def test_timeline_entries_have_ordered_indices(self):
        itin = generate(
            cities=["Tokyo"],
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 4),
        )
        indices = [e.entry_index for e in itin.timeline]
        assert indices == list(range(len(indices)))

    def test_timeline_entries_have_reasonable_times(self):
        itin = generate(
            cities=["London"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )
        for e in itin.timeline:
            assert e.start_time < e.end_time
            assert e.start_time.date() >= date(2026, 9, 1)
            assert e.end_time.date() <= date(2026, 9, 2)

    def test_empty_cities_raises(self):
        with pytest.raises(ValueError, match="city"):
            generate(
                cities=[],
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 5),
            )

    def test_end_before_start_raises(self):
        with pytest.raises(ValueError, match="after start"):
            generate(
                cities=["Paris"],
                start_date=date(2026, 9, 5),
                end_date=date(2026, 9, 1),
            )

    def test_documents_needed_populated(self):
        itin = generate(
            cities=["London"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )
        assert len(itin.documents_needed) > 0
        for doc in itin.documents_needed:
            assert isinstance(doc, DocumentNeeded)
            assert len(doc.type) > 0

    def test_emergency_contacts_populated(self):
        itin = generate(
            cities=["Paris", "Tokyo"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 7),
        )
        assert len(itin.emergency_contacts) > 0
        for ec in itin.emergency_contacts:
            assert isinstance(ec, EmergencyContact)
            assert len(ec.number) > 0

    def test_weather_forecast_populated(self):
        itin = generate(
            cities=["Sydney"],
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 8),
        )
        assert isinstance(itin.weather_forecast, WeatherForecast)
        assert len(itin.weather_forecast.stops) > 0

    def test_country_codes_override(self):
        itin = generate(
            cities=["New York", "London"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 5),
            country_codes=["USA", "GBR"],
        )
        assert any(doc.country == "GBR" for doc in itin.documents_needed)

    def test_timezone_defaults_to_utc(self):
        itin = generate(
            cities=["Berlin"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
        )
        for e in itin.timeline:
            assert e.timezone == "UTC"

    def test_timezone_custom(self):
        itin = generate(
            cities=["Tokyo"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
            timezone="Asia/Tokyo",
        )
        for e in itin.timeline:
            assert e.timezone == "Asia/Tokyo"

    def test_documents_deduplicated_across_countries(self):
        itin = generate(
            cities=["New York", "Los Angeles"],
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 5),
            country_codes=["USA", "USA"],
        )
        usa_docs = [d for d in itin.documents_needed if d.country == "USA"]
        assert len(usa_docs) <= 2

    def test_itinerary_ids_are_unique(self):
        a = generate(cities=["Paris"], start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        b = generate(cities=["Paris"], start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        assert a.itinerary_id != b.itinerary_id
        assert a.request_id != b.request_id


class TestGenerateFromTemplate:
    def test_western_europe_template(self):
        itin = generate_from_template(
            "western_europe",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 15),
        )
        assert isinstance(itin, Itinerary)
        assert len(itin.timeline) > 0

    def test_east_asia_template(self):
        itin = generate_from_template(
            "east_asia",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 14),
        )
        assert len(itin.timeline) > 0

    def test_north_america_template(self):
        itin = generate_from_template(
            "north_america",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 12),
        )
        assert isinstance(itin, Itinerary)

    def test_southeast_asia_template(self):
        itin = generate_from_template(
            "southeast_asia",
            start_date=date(2027, 1, 5),
            end_date=date(2027, 1, 18),
        )
        assert len(itin.timeline) > 0

    def test_middle_east_template(self):
        itin = generate_from_template(
            "middle_east",
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 10),
        )
        assert isinstance(itin, Itinerary)

    def test_oceania_template(self):
        itin = generate_from_template(
            "oceania",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 10),
        )
        assert isinstance(itin, Itinerary)

    def test_mediterranean_template(self):
        itin = generate_from_template(
            "mediterranean",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 12),
        )
        assert len(itin.timeline) > 0

    def test_capitals_template(self):
        itin = generate_from_template(
            "capitals",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 16),
        )
        assert isinstance(itin, Itinerary)

    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="unknown template"):
            generate_from_template(
                "mars_mission",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 5),
            )


class TestListTemplates:
    def test_returns_all_templates(self):
        templates = list_templates()
        assert "western_europe" in templates
        assert "east_asia" in templates
        assert "north_america" in templates
        assert "southeast_asia" in templates
        assert "middle_east" in templates
        assert "oceania" in templates
        assert "mediterranean" in templates
        assert "capitals" in templates
        assert len(templates) == 8
