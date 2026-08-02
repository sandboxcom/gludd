"""Unit tests for travel knowledge.py module_utils."""

from __future__ import annotations

from ansible_collections.general_ludd.travel.plugins.module_utils.knowledge import (
    city_country,
    city_to_iata,
    country_name,
    country_to_airports,
    country_to_cities,
    iata_is_valid,
    iata_to_city,
    iata_to_country,
    iata_to_name,
    nearest_airport,
    resolve_iata,
)


class TestLookupDicts:
    def test_city_to_iata_returns_expected(self):
        assert city_to_iata["NEW YORK"] == "JFK"
        assert city_to_iata["LONDON"] == "LHR"
        assert city_to_iata["PARIS"] == "CDG"
        assert city_to_iata["TOKYO"] == "NRT"
        assert city_to_iata["DUBAI"] == "DXB"
        assert city_to_iata["SYDNEY"] == "SYD"
        assert city_to_iata["FRANKFURT"] == "FRA"
        assert city_to_iata["SINGAPORE"] == "SIN"

    def test_iata_to_name_returns_expected(self):
        assert "Heathrow" in iata_to_name["LHR"]
        assert "Charles de Gaulle" in iata_to_name["CDG"]
        assert "Narita" in iata_to_name["NRT"]
        assert "Schiphol" in iata_to_name["AMS"]

    def test_iata_to_city_returns_expected(self):
        assert iata_to_city["JFK"] == "New York"
        assert iata_to_city["LAX"] == "Los Angeles"
        assert iata_to_city["SFO"] == "San Francisco"
        assert iata_to_city["MIA"] == "Miami"

    def test_iata_to_country_returns_expected(self):
        assert iata_to_country["JFK"] == "USA"
        assert iata_to_country["LHR"] == "GBR"
        assert iata_to_country["CDG"] == "FRA"
        assert iata_to_country["NRT"] == "JPN"
        assert iata_to_country["DXB"] == "ARE"

    def test_country_to_cities_has_major(self):
        assert "New York" in country_to_cities["USA"]
        assert "London" in country_to_cities["GBR"]
        assert "Paris" in country_to_cities["FRA"]
        assert "Tokyo" in country_to_cities["JPN"]

    def test_country_to_airports_maps_all(self):
        assert len(country_to_airports["USA"]) >= 10
        assert "JFK" in country_to_airports["USA"]
        assert "LHR" in country_to_airports["GBR"]
        assert "CDG" in country_to_airports["FRA"]

    def test_city_country_returns_expected(self):
        assert city_country["NEW YORK"] == "USA"
        assert city_country["LONDON"] == "GBR"
        assert city_country["TOKYO"] == "JPN"
        assert city_country["SYDNEY"] == "AUS"

    def test_country_name_has_all_records(self):
        assert country_name["USA"] == "United States"
        assert country_name["GBR"] == "United Kingdom"
        assert country_name["FRA"] == "France"
        assert country_name["JPN"] == "Japan"
        assert country_name["AUS"] == "Australia"


class TestResolveIata:
    def test_resolve_by_iata_returns_self(self):
        assert resolve_iata("JFK") == "JFK"
        assert resolve_iata("LHR") == "LHR"
        assert resolve_iata("  cdg ") == "CDG"

    def test_resolve_by_city_returns_iata(self):
        assert resolve_iata("New York") == "JFK"
        assert resolve_iata("London") == "LHR"
        assert resolve_iata("tokyo") == "NRT"

    def test_resolve_unknown_returns_none(self):
        assert resolve_iata("XXXXX") is None
        assert resolve_iata("Nowhereville") is None
        assert resolve_iata("") is None

    def test_resolve_iata_not_in_dict_returns_none(self):
        assert resolve_iata("ZZZ") is None


class TestIataIsValid:
    def test_valid_codes(self):
        assert iata_is_valid("JFK") is True
        assert iata_is_valid("LHR") is True
        assert iata_is_valid("nrt") is True

    def test_invalid_codes(self):
        assert iata_is_valid("ZZZ") is False
        assert iata_is_valid("XXXX") is False
        assert iata_is_valid("") is False


class TestNearestAirport:
    def test_exact_match_in_supported(self):
        result = nearest_airport("JFK", frozenset({"JFK", "LHR", "CDG"}))
        assert result == "JFK"

    def test_city_resolves_if_supported(self):
        result = nearest_airport("London", frozenset({"LHR", "CDG"}))
        assert result == "LHR"

    def test_fallback_same_country(self):
        result = nearest_airport("New York", frozenset({"LAX", "SFO"}))
        assert result in ("LAX", "SFO") or result == "JFK"

    def test_no_supported_returns_resolved(self):
        result = nearest_airport("Paris", None)
        assert result == "CDG"

    def test_unknown_returns_none(self):
        assert nearest_airport("Nowhere", frozenset({"JFK"})) is None


class TestRegistrySize:
    def test_at_least_60_airports(self):
        assert len(iata_to_name) >= 60

    def test_at_least_30_countries(self):
        assert len(country_name) >= 30
