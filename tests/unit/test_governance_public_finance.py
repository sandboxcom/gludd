"""Behavioral unit tests for the governance public_finance knowledge module."""

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
    / "public_finance.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_public_finance_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pf() -> ModuleType:
    return _load_module()


class TestModuleExports:
    def test_constants_present(self, pf):
        for attr in ("BUDGET_TYPES", "BUDGET_DATA", "PROCUREMENT_METHODS",
                      "PROCUREMENT_RULES", "DEBT_INSTRUMENTS", "DEBT_DATA",
                      "SOVEREIGN_WEALTH_FUNDS"):
            assert hasattr(pf, attr), f"missing constant {attr}"

    def test_functions_present(self, pf):
        for fn in ("get_budget_info", "get_procurement_rules", "get_debt_info",
                    "get_swf_by_name", "get_swfs_by_country", "get_swfs_by_type",
                    "list_countries_with_budgets", "list_swf_countries",
                    "procurement_by_method", "debt_by_holder", "debt_to_gdp"):
            assert callable(getattr(pf, fn, None)), f"missing function {fn}"


class TestBudgetTypes:
    def test_six_types(self, pf):
        assert len(pf.BUDGET_TYPES) == 6

    def test_required_types(self, pf):
        for t in ("line_item", "programme_based", "performance_based",
                   "zero_based", "participatory", "gender_responsive"):
            assert t in pf.BUDGET_TYPES

    def test_types_unique(self, pf):
        assert len(set(pf.BUDGET_TYPES)) == len(pf.BUDGET_TYPES)


class TestBudgetData:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP", "CA", "AU"])
    def test_major_countries_present(self, pf, country):
        assert country in pf.BUDGET_DATA, f"missing budget data for {country}"

    def test_each_has_required_fields(self, pf):
        for _code, data in pf.BUDGET_DATA.items():
            assert "name" in data
            assert "currency" in data
            assert "fiscal_year" in data
            assert "budget_type" in data
            assert "budget_authority" in data
            assert "budget_process" in data
            assert "revenue_sources" in data
            assert "expenditure_categories" in data
            assert "audit_body" in data

    def test_budget_types_valid(self, pf):
        for code, data in pf.BUDGET_DATA.items():
            assert data["budget_type"] in pf.BUDGET_TYPES, f"{code} invalid budget_type"

    def test_revenue_sources_sum_roughly_one(self, pf):
        for code, data in pf.BUDGET_DATA.items():
            total = sum(data["revenue_sources"].values())
            assert 0.8 <= total <= 1.2, f"{code} revenue total = {total}"


class TestGetBudgetInfo:
    def test_us_budget(self, pf):
        info = pf.get_budget_info("US")
        assert info is not None
        assert info["name"] == "United States"
        assert info["currency"] == "USD"

    def test_gb_fiscal_year_format(self, pf):
        info = pf.get_budget_info("GB")
        assert "April" in info["fiscal_year"]

    def test_de_debt_brake(self, pf):
        info = pf.get_budget_info("DE")
        assert "schuldenbremse" in str(info.get("fiscal_rules", "")).lower()

    def test_unknown_country_returns_none(self, pf):
        assert pf.get_budget_info("XX") is None

    def test_case_insensitive(self, pf):
        assert pf.get_budget_info("us") == pf.get_budget_info("US")


class TestProcurementMethods:
    def test_nine_methods(self, pf):
        assert len(pf.PROCUREMENT_METHODS) == 9

    def test_required_methods(self, pf):
        for m in ("open_tender", "restricted_tender", "direct_award", "request_for_proposals"):
            assert m in pf.PROCUREMENT_METHODS

    def test_unique(self, pf):
        assert len(set(pf.PROCUREMENT_METHODS)) == len(pf.PROCUREMENT_METHODS)


class TestProcurementRules:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP"])
    def test_major_countries_present(self, pf, country):
        assert country in pf.PROCUREMENT_RULES, f"missing procurement rules for {country}"

    def test_each_has_required_fields(self, pf):
        for _code, rules in pf.PROCUREMENT_RULES.items():
            assert "legal_framework" in rules
            assert "governing_body" in rules
            assert "thresholds" in rules
            assert "preferred_methods" in rules
            assert "preferences" in rules
            assert "dispute_resolution" in rules
            assert "transparency" in rules

    def test_preferred_methods_valid(self, pf):
        for code, rules in pf.PROCUREMENT_RULES.items():
            for method in rules["preferred_methods"]:
                assert method in pf.PROCUREMENT_METHODS, f"{code} invalid method: {method}"


class TestGetProcurementRules:
    def test_us_far(self, pf):
        rules = pf.get_procurement_rules("US")
        assert rules is not None
        assert "FAR" in rules["legal_framework"] or "Federal" in rules["legal_framework"]

    def test_unknown_returns_none(self, pf):
        assert pf.get_procurement_rules("XX") is None


class TestProcurementByMethod:
    def test_us_open_tender(self, pf):
        result = pf.procurement_by_method("US", "open_tender")
        assert result is not None
        assert result["matched_method"] == "open_tender"

    def test_unknown_method(self, pf):
        assert pf.procurement_by_method("US", "nonexistent_method") is None

    def test_unknown_country(self, pf):
        assert pf.procurement_by_method("XX", "open_tender") is None


class TestDebtInstruments:
    def test_eight_instruments(self, pf):
        assert len(pf.DEBT_INSTRUMENTS) == 8

    def test_each_has_fields(self, pf):
        for _key, inst in pf.DEBT_INSTRUMENTS.items():
            assert "description" in inst
            assert "typical_maturity" in inst
            assert "risk_level" in inst
            assert "issuing_countries" in inst
            assert len(inst["issuing_countries"]) > 0

    def test_sukuk_present(self, pf):
        assert "sukuk" in pf.DEBT_INSTRUMENTS
        assert "sharia" in pf.DEBT_INSTRUMENTS["sukuk"]["description"].lower()


class TestDebtData:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP", "CA", "AU"])
    def test_major_countries_present(self, pf, country):
        assert country in pf.DEBT_DATA, f"missing debt data for {country}"

    def test_each_has_required_fields(self, pf):
        for _code, data in pf.DEBT_DATA.items():
            assert "name" in data
            assert "gross_debt_to_gdp_pct" in data
            assert "net_debt_to_gdp_pct" in data
            assert "main_instruments" in data
            assert "credit_rating" in data
            assert "debt_management_office" in data
            assert "interest_cost_pct_revenue" in data

    def test_jp_highest_debt(self, pf):
        jp = pf.DEBT_DATA["JP"]
        assert jp["gross_debt_to_gdp_pct"] > 200

    def test_main_instruments_valid(self, pf):
        for code, data in pf.DEBT_DATA.items():
            for inst in data["main_instruments"]:
                assert inst in pf.DEBT_INSTRUMENTS, f"{code} invalid instrument: {inst}"


class TestGetDebtInfo:
    def test_us_debt(self, pf):
        info = pf.get_debt_info("US")
        assert info is not None
        assert "treasury_bill" in info["main_instruments"]

    def test_unknown_country_returns_none(self, pf):
        assert pf.get_debt_info("XX") is None

    def test_case_insensitive(self, pf):
        assert pf.get_debt_info("jp") == pf.get_debt_info("JP")


class TestDebtToGdp:
    def test_us_gdp_ratio(self, pf):
        ratio = pf.debt_to_gdp("US")
        assert ratio is not None
        assert ratio > 50

    def test_jp_gdp_ratio(self, pf):
        ratio = pf.debt_to_gdp("JP")
        assert ratio is not None
        assert ratio > 200

    def test_unknown_country(self, pf):
        assert pf.debt_to_gdp("XX") is None


class TestDebtByHolder:
    def test_us_holders(self, pf):
        result = pf.debt_by_holder("US")
        assert result is not None
        assert "Japan" in result["largest_foreign_holders"]
        assert "China" in result["largest_foreign_holders"]

    def test_unknown_country(self, pf):
        assert pf.debt_by_holder("XX") is None


class TestSovereignWealthFunds:
    def test_minimum_funds(self, pf):
        assert len(pf.SOVEREIGN_WEALTH_FUNDS) >= 10

    def test_each_fund_has_required_fields(self, pf):
        for fund in pf.SOVEREIGN_WEALTH_FUNDS:
            assert "name" in fund
            assert "country" in fund
            assert "founded" in fund
            assert "assets_usd_bn" in fund
            assert "type" in fund
            assert "funding_source" in fund
            assert "mandate" in fund
            assert "governance" in fund

    def test_norway_fund_present(self, pf):
        names = {f["name"] for f in pf.SOVEREIGN_WEALTH_FUNDS}
        assert "Government Pension Fund Global (GPFG)" in names

    def test_all_funds_positive_assets(self, pf):
        for fund in pf.SOVEREIGN_WEALTH_FUNDS:
            assert fund["assets_usd_bn"] > 0, f"{fund['name']} has non-positive assets"


class TestGetSwfByName:
    def test_by_full_name(self, pf):
        fund = pf.get_swf_by_name("Government Pension Fund Global (GPFG)")
        assert fund is not None
        assert fund["country"] == "NO"

    def test_by_short_name(self, pf):
        fund = pf.get_swf_by_name("Oljefondet (Oil Fund)")
        assert fund is not None

    def test_case_insensitive(self, pf):
        assert pf.get_swf_by_name("gic") is not None

    def test_unknown(self, pf):
        assert pf.get_swf_by_name("Nonexistent Fund") is None


class TestGetSwfsByCountry:
    def test_singapore_has_two(self, pf):
        funds = pf.get_swfs_by_country("SG")
        assert len(funds) >= 2

    def test_norway_has_one(self, pf):
        funds = pf.get_swfs_by_country("NO")
        assert len(funds) == 1

    def test_no_funds_country(self, pf):
        assert pf.get_swfs_by_country("XX") == []

    def test_case_insensitive(self, pf):
        assert pf.get_swfs_by_country("sg") == pf.get_swfs_by_country("SG")


class TestGetSwfsByType:
    def test_swf_type(self, pf):
        funds = pf.get_swfs_by_type("sovereign_wealth_fund")
        assert len(funds) >= 5
        for f in funds:
            assert f["type"] == "sovereign_wealth_fund"

    def test_pension_type(self, pf):
        funds = pf.get_swfs_by_type("sovereign_pension_fund")
        assert len(funds) >= 2

    def test_strategic_holding(self, pf):
        funds = pf.get_swfs_by_type("strategic_holding_company")
        assert len(funds) >= 1
        assert "Temasek" in str(funds)

    def test_unknown_type(self, pf):
        assert pf.get_swfs_by_type("nonexistent") == []


class TestListCountries:
    def test_budget_countries(self, pf):
        countries = pf.list_countries_with_budgets()
        assert isinstance(countries, list)
        assert "US" in countries
        assert len(countries) >= 7

    def test_swf_countries(self, pf):
        countries = pf.list_swf_countries()
        assert isinstance(countries, list)
        assert "NO" in countries
        assert "SG" in countries
        assert len(countries) >= 5

    def test_both_sorted(self, pf):
        for fn in (pf.list_countries_with_budgets, pf.list_swf_countries):
            result = fn()
            assert result == sorted(result)


class TestCompatibilityLookups:
    def test_lookup_budget_success_and_unknown(self, pf):
        result = pf.lookup_budget("us")
        assert result["found"] is True
        assert result["country"] == "US"
        assert pf.lookup_budget("XX") is None

    def test_lookup_sovereign_debt_success_and_unknown(self, pf):
        result = pf.lookup_sovereign_debt("jp")
        assert result["found"] is True
        assert result["country"] == "JP"
        assert pf.lookup_sovereign_debt("XX") is None

    def test_lookup_pension_system_success_and_unknown(self, pf):
        result = pf.lookup_pension_system("gb")
        assert result["found"] is True
        assert result["country"] == "GB"
        assert pf.lookup_pension_system("XX") is None
