"""Tests for the general_ludd.governance postal_delivery module."""

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
    "postal_delivery.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("gov_postal_delivery", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pd = _load_module()


class TestPostalCodePatterns:
    def test_patterns_present(self) -> None:
        assert isinstance(pd.POSTAL_CODE_PATTERNS, dict)
        assert len(pd.POSTAL_CODE_PATTERNS) >= 10

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "CA", "DE", "FR", "AU", "JP", "BR", "IN"):
            assert code in pd.POSTAL_CODE_PATTERNS, f"Missing {code}"

    def test_each_entry_has_required_keys(self) -> None:
        for code, entry in pd.POSTAL_CODE_PATTERNS.items():
            assert "pattern" in entry, f"{code} missing pattern"
            assert "example" in entry, f"{code} missing example"


class TestGetPostalCodePattern:
    def test_known_country(self) -> None:
        result = pd.get_postal_code_pattern("US")
        assert result is not None
        assert "pattern" in result

    def test_unknown_country(self) -> None:
        assert pd.get_postal_code_pattern("XX") is None

    def test_resolves_by_name(self) -> None:
        result = pd.get_postal_code_pattern("United Kingdom")
        assert result is not None


class TestCourierTracking:
    def test_courier_tracking_present(self) -> None:
        assert isinstance(pd.COURIER_TRACKING, dict)
        assert len(pd.COURIER_TRACKING) >= 6

    def test_major_couriers(self) -> None:
        for courier in ("usps", "fedex", "ups", "dhl", "royal_mail", "canada_post"):
            assert courier in pd.COURIER_TRACKING, f"Missing {courier}"

    def test_each_entry_has_url_template(self) -> None:
        for courier, entry in pd.COURIER_TRACKING.items():
            assert "url_template" in entry, f"{courier} missing url_template"


class TestGetCourierTrackingUrl:
    def test_known_courier(self) -> None:
        url = pd.get_courier_tracking_url("usps", "9400123456789012345678")
        assert url is not None
        assert "9400123456789012345678" in url

    def test_unknown_courier(self) -> None:
        assert pd.get_courier_tracking_url("nonexistent", "123") is None


class TestCustomsDeclarationFormats:
    def test_formats_present(self) -> None:
        assert isinstance(pd.CUSTOMS_DECLARATION_FORMATS, dict)
        assert len(pd.CUSTOMS_DECLARATION_FORMATS) >= 6

    def test_known_countries(self) -> None:
        for code in ("US", "GB", "DE", "CA", "AU", "JP"):
            assert code in pd.CUSTOMS_DECLARATION_FORMATS, f"Missing {code}"

    def test_eu_supported(self) -> None:
        assert "EU" in pd.CUSTOMS_DECLARATION_FORMATS

    def test_each_entry_has_required_fields(self) -> None:
        for _code, entry in pd.CUSTOMS_DECLARATION_FORMATS.items():
            assert "form_id" in entry
            assert "required_fields" in entry
            assert isinstance(entry["required_fields"], list)


class TestGetCustomsDeclarationFormat:
    def test_known_country(self) -> None:
        result = pd.get_customs_declaration_format("US")
        assert result is not None
        assert "form_id" in result

    def test_unknown_country(self) -> None:
        assert pd.get_customs_declaration_format("XX") is None


class TestAddressFormats:
    def test_formats_present(self) -> None:
        assert isinstance(pd.ADDRESS_FORMATS, dict)
        assert len(pd.ADDRESS_FORMATS) >= 8


class TestNormalizeAddress:
    def test_returns_dict_for_known_country(self) -> None:
        result = pd.normalize_address("US", "1600 Pennsylvania Avenue NW, Washington, DC 20500")
        assert isinstance(result, dict)
        assert "raw" in result
        assert result["country"] == "US"

    def test_unknown_country_passthrough(self) -> None:
        result = pd.normalize_address("XX", "some address")
        assert isinstance(result, dict)
        assert result["country"] == "XX"


class TestValidateAddress:
    def test_valid_address(self) -> None:
        result = pd.validate_address(
            "US",
            {
                "recipient": "Test",
                "street_address": "123 Main",
                "city": "NY",
                "state": "NY",
                "zip_code": "10001",
            },
        )
        assert result["valid"] is True

    def test_missing_field(self) -> None:
        result = pd.validate_address("US", {"recipient": "Test"})
        assert result["valid"] is False
        assert len(result["missing_fields"]) > 0


class TestSearchCountriesByName:
    def test_returns_matches(self) -> None:
        results = pd.search_countries_by_name("united")
        assert len(results) > 0
        codes = {r["code"] for r in results}
        assert "US" in codes or "GB" in codes

    def test_no_matches(self) -> None:
        results = pd.search_countries_by_name("xyznotarealcountry")
        assert len(results) == 0
