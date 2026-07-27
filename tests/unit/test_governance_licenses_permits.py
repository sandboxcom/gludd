"""Tests for the general_ludd.governance licenses_permits module."""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_MODULE_PATH = os.path.join(
    _PROJECT_ROOT,
    "collections",
    "ansible_collections",
    "general_ludd",
    "governance",
    "plugins",
    "module_utils",
    "licenses_permits.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gov_licenses_permits", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load_module()


class TestLicenseTypes:
    def test_types_present(self) -> None:
        assert isinstance(lp.LICENSE_TYPES, frozenset)
        assert len(lp.LICENSE_TYPES) >= 20

    def test_required_types_present(self) -> None:
        required = {"driving", "medical_practitioner", "lawyer", "export_controlled", "business_operating"}
        assert required.issubset(lp.LICENSE_TYPES)


class TestLicenseRegistries:
    def test_registries_present(self) -> None:
        assert isinstance(lp.LICENSE_REGISTRIES, dict)
        assert len(lp.LICENSE_REGISTRIES) >= 4

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "CA", "DE", "AU"):
            assert code in lp.LICENSE_REGISTRIES, f"Missing {code}"

    def test_each_country_has_common_licenses(self) -> None:
        for code, registries in lp.LICENSE_REGISTRIES.items():
            assert "driving" in registries, f"{code} missing driving"


class TestGetLicenseInfo:
    def test_known_license(self) -> None:
        result = lp.get_license_info("driving", "US")
        assert result is not None
        assert result["license_type"] == "driving"
        assert result["found"] is True

    def test_unknown_license(self) -> None:
        result = lp.get_license_info("nonexistent", "US")
        assert result is None

    def test_unknown_country(self) -> None:
        result = lp.get_license_info("driving", "XX")
        assert result is None

    def test_license_in_multiple_countries(self) -> None:
        result_us = lp.get_license_info("medical_practitioner", "US")
        result_gb = lp.get_license_info("medical_practitioner", "GB")
        assert result_us is not None
        assert result_gb is not None
        assert result_us["issuing_body"] != result_gb["issuing_body"]


class TestExportLicenseRequirements:
    def test_requirements_present(self) -> None:
        assert isinstance(lp.EXPORT_LICENSE_REQUIREMENTS, dict)
        assert len(lp.EXPORT_LICENSE_REQUIREMENTS) >= 5

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "DE", "CA", "AU"):
            assert code in lp.EXPORT_LICENSE_REQUIREMENTS, f"Missing {code}"

    def test_eu_supported(self) -> None:
        assert "EU" in lp.EXPORT_LICENSE_REQUIREMENTS

    def test_each_entry_has_regime(self) -> None:
        for code, categories in lp.EXPORT_LICENSE_REQUIREMENTS.items():
            assert len(categories) > 0, f"{code} has 0 categories"
            for _cat_name, cat_data in categories.items():
                assert "regime" in cat_data


class TestGetExportLicenseRequirements:
    def test_known_category(self) -> None:
        result = lp.get_export_license_requirements("US", "military_items")
        assert result is not None
        assert result["regime"] == "ITAR (International Traffic in Arms Regulations)"

    def test_unknown_category(self) -> None:
        assert lp.get_export_license_requirements("US", "nonexistent") is None

    def test_unknown_country(self) -> None:
        assert lp.get_export_license_requirements("XX", "military_items") is None


class TestCheckLicenseValidity:
    def test_known_type(self) -> None:
        result = lp.check_license_validity("driving", "California DMV", "D1234567")
        assert result["verifiable"] is True
        assert "check_methods" in result

    def test_unknown_type(self) -> None:
        result = lp.check_license_validity("nonexistent", "foo", "bar")
        assert result["verifiable"] is False


class TestListProfessionsForCountry:
    def test_known_country(self) -> None:
        result = lp.list_professions_for_country("US")
        assert isinstance(result, list)
        assert len(result) >= 3

    def test_unknown_country(self) -> None:
        result = lp.list_professions_for_country("XX")
        assert len(result) == 0


class TestGetRegulatingBody:
    def test_known_license(self) -> None:
        result = lp.get_regulating_body("lawyer", "US")
        assert result is not None
        assert "issuing_body" in result

    def test_unknown_license(self) -> None:
        assert lp.get_regulating_body("nonexistent", "US") is None
