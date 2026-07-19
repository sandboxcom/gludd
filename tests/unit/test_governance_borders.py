"""Tests for the general_ludd.governance borders knowledge module.

Loads borders.py directly from the collection path via importlib so the test
can live in the project-root test suite without requiring collection install.
"""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_BORDERS_PATH = os.path.join(
    _PROJECT_ROOT,
    "collections",
    "ansible_collections",
    "general_ludd",
    "governance",
    "plugins",
    "module_utils",
    "borders.py",
)


def _load_borders():
    spec = importlib.util.spec_from_file_location("governance_borders", _BORDERS_PATH)
    assert spec is not None and spec.loader is not None, "borders.py spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


borders = _load_borders()


# ── Border type taxonomy ─────────────────────────────────────────────────────


class TestBorderTypes:
    def test_border_types_present(self) -> None:
        assert isinstance(borders.BORDER_TYPES, frozenset)
        assert len(borders.BORDER_TYPES) >= 7

    def test_border_types_contains_all_required(self) -> None:
        required = {
            "land",
            "maritime",
            "airspace",
            "customs",
            "administrative",
            "contested",
            "demilitarized",
        }
        assert required.issubset(borders.BORDER_TYPES)

    def test_border_types_immutable(self) -> None:
        import pytest

        with pytest.raises(AttributeError):
            borders.BORDER_TYPES.add("fabricated")  # type: ignore[attr-defined]


# ── Recognition status ────────────────────────────────────────────────────────


class TestRecognitionStatus:
    def test_recognition_status_present(self) -> None:
        assert isinstance(borders.RECOGNITION_STATUS, frozenset)
        assert len(borders.RECOGNITION_STATUS) >= 5

    def test_recognition_status_required_values(self) -> None:
        required = {"universal", "partial", "disputed", "unrecognised", "de_facto"}
        assert required.issubset(borders.RECOGNITION_STATUS)

    def test_recognition_status_immutable(self) -> None:
        import pytest

        with pytest.raises(AttributeError):
            borders.RECOGNITION_STATUS.add("fictional")  # type: ignore[attr-defined]


# ── Visa types ─────────────────────────────────────────────────────────────────


class TestVisaTypes:
    def test_visa_types_present(self) -> None:
        assert isinstance(borders.VISA_TYPES, frozenset)
        assert len(borders.VISA_TYPES) >= 8

    def test_visa_types_contains_required(self) -> None:
        required = {
            "tourist",
            "business",
            "transit",
            "student",
            "work",
            "diplomatic",
            "refugee",
            "digital_nomad",
        }
        assert required.issubset(borders.VISA_TYPES)


# ── Border data integrity ─────────────────────────────────────────────────────


class TestBorderData:
    def test_border_data_is_dict(self) -> None:
        assert isinstance(borders.BORDER_DATA, dict)
        assert len(borders.BORDER_DATA) >= 8

    def test_each_entry_has_required_fields(self) -> None:
        required_keys = {
            "type",
            "controlling_bodies",
            "recognition",
            "crossing_requirements",
        }
        for region, entry in borders.BORDER_DATA.items():
            missing = required_keys - set(entry)
            assert not missing, f"{region} missing keys: {missing}"

    def test_each_type_is_valid(self) -> None:
        for region, entry in borders.BORDER_DATA.items():
            assert entry["type"] in borders.BORDER_TYPES, (
                f"{region} has invalid type {entry['type']!r}"
            )

    def test_each_recognition_is_valid(self) -> None:
        for region, entry in borders.BORDER_DATA.items():
            assert entry["recognition"] in borders.RECOGNITION_STATUS, (
                f"{region} has invalid recognition {entry['recognition']!r}"
            )

    def test_controlling_bodies_is_nonempty_list(self) -> None:
        for region, entry in borders.BORDER_DATA.items():
            bodies = entry["controlling_bodies"]
            assert isinstance(bodies, list), f"{region} controlling_bodies not list"
            assert len(bodies) > 0, f"{region} has no controlling bodies"

    def test_crossing_requirements_is_dict(self) -> None:
        for region, entry in borders.BORDER_DATA.items():
            assert isinstance(entry["crossing_requirements"], dict), (
                f"{region} crossing_requirements not dict"
            )

    def test_has_land_border(self) -> None:
        types = {e["type"] for e in borders.BORDER_DATA.values()}
        assert "land" in types

    def test_has_contested_border(self) -> None:
        types = {e["type"] for e in borders.BORDER_DATA.values()}
        assert "contested" in types

    def test_has_demilitarized_border(self) -> None:
        types = {e["type"] for e in borders.BORDER_DATA.values()}
        assert "demilitarized" in types


# ── lookup_border ─────────────────────────────────────────────────────────────


class TestLookupBorder:
    def test_lookup_existing_region(self) -> None:
        first_region = next(iter(borders.BORDER_DATA))
        result = borders.lookup_border(first_region)
        assert result is not None
        assert "type" in result

    def test_lookup_case_insensitive(self) -> None:
        first_region = next(iter(borders.BORDER_DATA))
        result = borders.lookup_border(first_region.lower())
        assert result is not None

    def test_lookup_unknown_returns_none(self) -> None:
        assert borders.lookup_border("nonexistent-region-xyz") is None


# ── get_crossing_requirements ─────────────────────────────────────────────────


class TestCrossingRequirements:
    def test_returns_dict_for_known_pair(self) -> None:
        result = borders.get_crossing_requirements("US", "CA")
        assert isinstance(result, dict)
        assert "visa_required" in result

    def test_unknown_origin_falls_back(self) -> None:
        result = borders.get_crossing_requirements("atlantis", "US")
        assert isinstance(result, dict)
        assert "error" in result or "note" in result or "visa_required" in result

    def test_result_contains_document_list(self) -> None:
        result = borders.get_crossing_requirements("US", "US")
        assert "documents" in result
        assert isinstance(result["documents"], list)


# ── get_recognition_status ────────────────────────────────────────────────────


class TestGetRecognitionStatus:
    def test_known_entity(self) -> None:
        status = borders.get_recognition_status("Kosovo")
        assert status in borders.RECOGNITION_STATUS

    def test_unknown_entity_returns_none(self) -> None:
        assert borders.get_recognition_status("atlas-shrugged-state") is None

    def test_universal_entity(self) -> None:
        status = borders.get_recognition_status("France")
        assert status == "universal"


# ── get_visa_requirements ─────────────────────────────────────────────────────


class TestGetVisaRequirements:
    def test_returns_dict(self) -> None:
        result = borders.get_visa_requirements("US", "FR")
        assert isinstance(result, dict)

    def test_has_visa_type(self) -> None:
        result = borders.get_visa_requirements("US", "FR")
        assert "visa_type" in result
        assert result["visa_type"] in borders.VISA_TYPES

    def test_schengen_visa_free(self) -> None:
        result = borders.get_visa_requirements("DE", "FR")
        assert result.get("visa_required") is False

    def test_unknown_passport_handled(self) -> None:
        result = borders.get_visa_requirements("atlantis", "US")
        assert isinstance(result, dict)
        assert "error" in result or result.get("visa_required") is not None
