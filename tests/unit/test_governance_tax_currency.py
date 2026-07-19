"""Behavioral unit tests for the governance tax_currency knowledge module.

Loads ``tax_currency.py`` directly off its filesystem path with importlib
(it lives inside the Ansible collection tree, not on sys.path) and exercises
the data tables and accessor functions. Follows the same pattern as
``test_module_utils_structured.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "governance"
    / "plugins"
    / "module_utils"
    / "tax_currency.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_tax_currency_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tc() -> ModuleType:
    return _load_module()


# ── Module exports / data table presence ───────────────────────────────────


class TestModuleExports:
    def test_data_tables_present(self, tc):
        for attr in (
            "TAX_SYSTEMS",
            "TAX_DATA",
            "CURRENCIES",
            "TAX_AUTHORITIES",
            "TAX_TREATIES",
        ):
            assert hasattr(tc, attr), f"missing data table {attr}"
            assert isinstance(getattr(tc, attr), dict)

    def test_functions_present(self, tc):
        for fn in (
            "get_tax_info",
            "get_currency_info",
            "get_filing_requirements",
            "get_tax_treaty",
            "list_countries",
            "list_currencies",
        ):
            assert callable(getattr(tc, fn, None)), f"missing function {fn}"


# ── TAX_SYSTEMS coverage ───────────────────────────────────────────────────


class TestTaxSystems:
    @pytest.mark.parametrize(
        "tax_type",
        [
            "income_progressive",
            "income_flat",
            "income_regressive",
            "corporate",
            "vat_gst",
            "sales",
            "property",
            "wealth",
            "inheritance",
            "capital_gains",
            "digital_services",
            "carbon",
        ],
    )
    def test_tax_system_defined(self, tc, tax_type):
        assert tax_type in tc.TAX_SYSTEMS, f"missing tax system {tax_type}"
        info = tc.TAX_SYSTEMS[tax_type]
        assert "description" in info and isinstance(info["description"], str)
        assert "structure" in info
        assert len(info["description"]) > 10

    def test_income_subcategories_distinct(self, tc):
        prog = tc.TAX_SYSTEMS["income_progressive"]
        flat = tc.TAX_SYSTEMS["income_flat"]
        reg = tc.TAX_SYSTEMS["income_regressive"]
        assert prog["structure"] != flat["structure"]
        assert flat["structure"] != reg["structure"]

    def test_each_tax_system_has_example_countries(self, tc):
        for tax_type, info in tc.TAX_SYSTEMS.items():
            assert "example_countries" in info, f"{tax_type} missing example_countries"
            assert isinstance(info["example_countries"], list)
            assert len(info["example_countries"]) > 0


# ── TAX_DATA country coverage ──────────────────────────────────────────────


class TestTaxData:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP", "CA", "AU"])
    def test_major_countries_present(self, tc, country):
        assert country in tc.TAX_DATA, f"missing tax data for {country}"
        data = tc.TAX_DATA[country]
        assert "name" in data
        assert "currency" in data
        assert "tax_types" in data and isinstance(data["tax_types"], list)
        assert len(data["tax_types"]) > 0

    def test_country_currency_links_to_currencies_table(self, tc):
        for code, data in tc.TAX_DATA.items():
            cur = data["currency"]
            assert cur in tc.CURRENCIES, (
                f"{code} currency {cur} missing from CURRENCIES table"
            )

    def test_us_has_progressive_brackets(self, tc):
        us = tc.TAX_DATA["US"]
        brackets = us.get("income_brackets")
        assert brackets is not None
        assert isinstance(brackets, list)
        assert len(brackets) >= 3
        for b in brackets:
            assert "rate" in b
            assert "threshold_usd" in b
            assert 0 <= b["rate"] <= 1.0

    def test_country_has_filing_deadline(self, tc):
        for code, data in tc.TAX_DATA.items():
            assert "filing_deadline" in data, f"{code} missing filing_deadline"


# ── CURRENCIES ─────────────────────────────────────────────────────────────


class TestCurrencies:
    @pytest.mark.parametrize(
        "code,name_substr",
        [
            ("USD", "Dollar"),
            ("EUR", "Euro"),
            ("GBP", "Pound"),
            ("JPY", "Yen"),
            ("CAD", "Dollar"),
            ("AUD", "Dollar"),
            ("CNY", "Yuan"),
            ("INR", "Rupee"),
        ],
    )
    def test_major_fiat_currencies(self, tc, code, name_substr):
        assert code in tc.CURRENCIES
        info = tc.CURRENCIES[code]
        assert "name" in info
        assert name_substr in info["name"]
        assert "symbol" in info
        assert "decimal_places" in info
        assert isinstance(info["decimal_places"], int)
        assert 0 <= info["decimal_places"] <= 4

    def test_jpy_has_zero_decimals(self, tc):
        assert tc.CURRENCIES["JPY"]["decimal_places"] == 0

    def test_digital_currencies_present(self, tc):
        digital_found = [
            code for code, info in tc.CURRENCIES.items()
            if info.get("type") == "digital"
        ]
        assert len(digital_found) >= 2, "expected at least 2 digital currencies"
        for sym in ("BTC", "ETH"):
            assert sym in tc.CURRENCIES, f"missing digital currency {sym}"

    def test_exchange_rate_sources_documented(self, tc):
        assert hasattr(tc, "EXCHANGE_RATE_SOURCES")
        assert isinstance(tc.EXCHANGE_RATE_SOURCES, list)
        assert len(tc.EXCHANGE_RATE_SOURCES) >= 2


# ── TAX_AUTHORITIES ────────────────────────────────────────────────────────


class TestTaxAuthorities:
    @pytest.mark.parametrize(
        "key,expected_name_substr",
        [
            ("US", "IRS"),
            ("GB", "HMRC"),
            ("DE", "Bundeszentralamt"),
        ],
    )
    def test_major_authorities(self, tc, key, expected_name_substr):
        assert key in tc.TAX_AUTHORITIES
        auth = tc.TAX_AUTHORITIES[key]
        assert expected_name_substr in auth["name"]
        assert "portal_url" in auth
        assert auth["portal_url"].startswith(("http://", "https://"))
        assert "filing_system" in auth

    def test_all_authorities_have_contact(self, tc):
        for key, auth in tc.TAX_AUTHORITIES.items():
            assert "name" in auth, f"{key} authority missing name"
            assert "portal_url" in auth, f"{key} authority missing portal_url"


# ── Functions ──────────────────────────────────────────────────────────────


class TestGetTaxInfo:
    def test_us_income_progressive(self, tc):
        result = tc.get_tax_info("US", "income_progressive")
        assert result is not None
        assert result["country"] == "US"
        assert "brackets" in result or "details" in result

    def test_uk_vat(self, tc):
        result = tc.get_tax_info("GB", "vat_gst")
        assert result is not None
        assert "rate" in result or "standard_rate" in result

    def test_unknown_country_returns_none(self, tc):
        assert tc.get_tax_info("XX", "income_progressive") is None

    def test_unknown_tax_type_returns_none(self, tc):
        assert tc.get_tax_info("US", "nonexistent_tax") is None

    def test_case_insensitive_country(self, tc):
        upper = tc.get_tax_info("US", "corporate")
        lower = tc.get_tax_info("us", "corporate")
        assert upper is not None
        assert lower is not None
        assert upper == lower


class TestGetCurrencyInfo:
    def test_usd(self, tc):
        info = tc.get_currency_info("USD")
        assert info is not None
        assert info["symbol"] == "$"
        assert info["decimal_places"] == 2

    def test_case_insensitive(self, tc):
        assert tc.get_currency_info("usd") == tc.get_currency_info("USD")

    def test_unknown_code_returns_none(self, tc):
        assert tc.get_currency_info("XYZ") is None


class TestGetFilingRequirements:
    def test_us_individual(self, tc):
        req = tc.get_filing_requirements("US", "individual")
        assert req is not None
        assert isinstance(req, dict)
        assert "deadline" in req or "requirements" in req

    def test_us_corporation(self, tc):
        req = tc.get_filing_requirements("US", "corporation")
        assert req is not None

    def test_gb_individual(self, tc):
        req = tc.get_filing_requirements("GB", "individual")
        assert req is not None

    def test_unknown_country_returns_none(self, tc):
        assert tc.get_filing_requirements("XX", "individual") is None


class TestGetTaxTreaty:
    def test_us_gb_treaty_exists(self, tc):
        treaty = tc.get_tax_treaty("US", "GB")
        assert treaty is not None
        assert "signed_year" in treaty or "year" in treaty
        assert "type" in treaty or "status" in treaty

    def test_treaty_symmetric(self, tc):
        a_to_b = tc.get_tax_treaty("US", "GB")
        b_to_a = tc.get_tax_treaty("GB", "US")
        assert a_to_b is not None
        assert b_to_a is not None
        assert a_to_b == b_to_a

    def test_no_treaty_returns_none(self, tc):
        result = tc.get_tax_treaty("US", "XX")
        assert result is None


class TestListAccessors:
    def test_list_countries(self, tc):
        countries = tc.list_countries()
        assert isinstance(countries, list)
        assert "US" in countries
        assert len(countries) >= 7

    def test_list_currencies(self, tc):
        currencies = tc.list_currencies()
        assert isinstance(currencies, list)
        assert "USD" in currencies
        assert len(currencies) >= 10
