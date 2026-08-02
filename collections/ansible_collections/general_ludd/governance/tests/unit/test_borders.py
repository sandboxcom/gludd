"""Tests for borders module — border types, crossing requirements, visas."""

from __future__ import annotations

from plugins.module_utils.borders import (
    BORDER_DATA,
    BORDER_TYPES,
    ENTITY_RECOGNITION,
    RECOGNITION_STATUS,
    VISA_REGIME,
    VISA_TYPES,
    get_crossing_requirements,
    get_recognition_status,
    get_visa_requirements,
    lookup_border,
)


class TestBorderTypes:
    def test_expected_types(self):
        expected = {"land", "maritime", "airspace", "customs", "administrative", "contested", "demilitarized"}
        assert expected == BORDER_TYPES


class TestRecognitionStatus:
    def test_expected_statuses(self):
        expected = {"universal", "partial", "disputed", "unrecognised", "de_facto"}
        assert expected == RECOGNITION_STATUS


class TestVisaTypes:
    def test_expected_types(self):
        expected = {"tourist", "business", "transit", "student", "work", "diplomatic", "refugee", "digital_nomad"}
        assert expected == VISA_TYPES


class TestBorderData:
    def test_has_expected_regions(self):
        regions = [
            "US-Canada land border",
            "US-Mexico land border",
            "Schengen internal border",
            "Korean Demilitarized Zone (DMZ)",
            "India-Pakistan Line of Control (Kashmir)",
            "Western Sahara boundary",
            "Bering Strait maritime boundary",
            "Antarctic Treaty area",
            "Northern Cyprus Green Line",
            "Schengen external border",
            "Brazil-Argentina Iguazu crossing",
            "UK-Ireland Common Travel Area",
        ]
        for r in regions:
            assert r in BORDER_DATA, f"Missing region: {r}"

    def test_entries_have_required_keys(self):
        for name, entry in BORDER_DATA.items():
            for key in ("type", "controlling_bodies", "recognition", "crossing_requirements"):
                assert key in entry, f"{name} missing {key}"
            assert entry["type"] in BORDER_TYPES, f"{name} has unknown type {entry['type']}"
            assert entry["recognition"] in RECOGNITION_STATUS, f"{name} has unknown recognition {entry['recognition']}"
            cr = entry["crossing_requirements"]
            assert "documents" in cr
            assert "visa_required" in cr
            assert "visa_type" in cr


class TestLookupBorder:
    def test_exact_match(self):
        result = lookup_border("US-Canada land border")
        assert result is not None
        assert result["type"] == "land"

    def test_case_insensitive(self):
        result = lookup_border("us-canada land border")
        assert result is not None
        assert result["type"] == "land"

    def test_not_found_is_none(self):
        assert lookup_border("nonexistent border") is None

    def test_returns_copy_not_reference(self):
        a = lookup_border("US-Canada land border")
        b = lookup_border("US-Canada land border")
        assert a is not None
        assert b is not None
        assert a is not b


class TestGetCrossingRequirements:
    def test_us_to_canada(self):
        req = get_crossing_requirements("US", "CA")
        assert "documents" in req
        assert "visa_required" in req

    def test_us_to_france(self):
        req = get_crossing_requirements("US", "FR")
        assert req["visa_required"] is False

    def test_to_us_from_non_vwp(self):
        req = get_crossing_requirements("IN", "US")
        assert req["visa_required"] is True

    def test_same_entity_no_docs(self):
        req = get_crossing_requirements("XX", "XX")
        assert req["documents"] == []
        assert req["visa_required"] is False

    def test_unknown_destination_defaults_to_visa_required(self):
        req = get_crossing_requirements("US", "ZZ")
        assert req["visa_required"] is True


class TestGetRecognitionStatus:
    def test_known_entities(self):
        assert get_recognition_status("United States") == "universal"
        assert get_recognition_status("France") == "universal"
        assert get_recognition_status("Kosovo") == "partial"
        assert get_recognition_status("Taiwan") == "partial"

    def test_case_insensitive(self):
        assert get_recognition_status("france") == "universal"

    def test_unknown_entity_is_none(self):
        assert get_recognition_status("Atlantis") is None


class TestGetVisaRequirements:
    def test_us_passport_to_france(self):
        req = get_visa_requirements("US", "FR")
        assert req["visa_required"] is False

    def test_india_passport_to_us(self):
        req = get_visa_requirements("IN", "US")
        assert req["visa_required"] is True

    def test_unknown_destination_has_error(self):
        req = get_visa_requirements("US", "ZZ")
        assert req["visa_required"] is True
        assert "error" in req


class TestVisaRegime:
    def test_france_schengen(self):
        assert VISA_REGIME["FR"]["regime"] == "schengen"

    def test_us_vwp(self):
        assert VISA_REGIME["US"]["regime"] == "vwp"

    def test_brazil_mercosur(self):
        assert VISA_REGIME["BR"]["regime"] == "mercosur"


class TestEntityRecognition:
    def test_de_facto_entities(self):
        assert ENTITY_RECOGNITION["Transnistria"] == "de_facto"
        assert ENTITY_RECOGNITION["Somaliland"] == "de_facto"
        assert ENTITY_RECOGNITION["Nagorno-Karabakh"] == "de_facto"

    def test_unrecognised_entities(self):
        assert ENTITY_RECOGNITION["Northern Cyprus"] == "unrecognised"
