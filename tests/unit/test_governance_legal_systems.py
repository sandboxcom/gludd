"""Behavioral unit tests for the governance legal_systems knowledge module."""

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
    / "legal_systems.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_legal_systems_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    return _load_module()


class TestModuleExports:
    def test_constants_present(self, ls):
        for attr in ("LEGAL_SYSTEM_TYPES", "COURT_HIERARCHIES", "APPEAL_PROCESSES", "LEGAL_TERMINOLOGY"):
            assert hasattr(ls, attr), f"missing constant {attr}"

    def test_functions_present(self, ls):
        for fn in ("get_legal_system", "get_court_hierarchy", "get_appeal_process",
                    "get_term", "terms_by_category", "list_countries",
                    "court_at_level", "supreme_court"):
            assert callable(getattr(ls, fn, None)), f"missing function {fn}"


class TestLegalSystemTypes:
    def test_five_types(self, ls):
        assert len(ls.LEGAL_SYSTEM_TYPES) == 5

    def test_required_types(self, ls):
        for t in ("common_law", "civil_law", "customary_law", "religious_law", "mixed"):
            assert t in ls.LEGAL_SYSTEM_TYPES

    def test_types_unique(self, ls):
        assert len(set(ls.LEGAL_SYSTEM_TYPES)) == len(ls.LEGAL_SYSTEM_TYPES)


class TestCourtHierarchies:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP", "CA", "AU", "CN", "IN", "SA", "ZA"])
    def test_major_countries_present(self, ls, country):
        assert country in ls.COURT_HIERARCHIES, f"missing {country}"

    def test_each_country_has_fields(self, ls):
        for code, h in ls.COURT_HIERARCHIES.items():
            assert "name" in h
            assert "system_type" in h
            assert "federal" in h
            assert "constitutional_review" in h
            assert "judge_selection" in h
            assert h["system_type"] in ls.LEGAL_SYSTEM_TYPES, f"{code} invalid system_type"

    def test_federal_section_has_levels(self, ls):
        for code, h in ls.COURT_HIERARCHIES.items():
            assert "levels" in h["federal"], f"{code} federal missing levels"

    def test_ids_unique(self, ls):
        codes = list(ls.COURT_HIERARCHIES.keys())
        assert len(codes) == len(set(codes))

    def test_us_common_law(self, ls):
        assert ls.COURT_HIERARCHIES["US"]["system_type"] == "common_law"

    def test_de_civil_law(self, ls):
        assert ls.COURT_HIERARCHIES["DE"]["system_type"] == "civil_law"

    def test_sa_religious_law(self, ls):
        assert ls.COURT_HIERARCHIES["SA"]["system_type"] == "religious_law"

    def test_za_mixed(self, ls):
        assert ls.COURT_HIERARCHIES["ZA"]["system_type"] == "mixed"


class TestGetLegalSystem:
    def test_us_returns_common_law(self, ls):
        result = ls.get_legal_system("US")
        assert result is not None
        assert result["system_type"] == "common_law"
        assert result["country_name"] == "United States"

    def test_unknown_country_returns_none(self, ls):
        assert ls.get_legal_system("XX") is None

    def test_case_insensitive(self, ls):
        assert ls.get_legal_system("us") == ls.get_legal_system("US")

    def test_includes_constitutional_review(self, ls):
        result = ls.get_legal_system("GB")
        assert "parliamentary_sovereignty" in result["constitutional_review"]

    def test_includes_judge_selection(self, ls):
        result = ls.get_legal_system("FR")
        assert len(result["judge_selection"]) > 10


class TestGetCourtHierarchy:
    def test_us_hierarchy(self, ls):
        h = ls.get_court_hierarchy("US")
        assert h is not None
        assert "Supreme Court" in h["federal"]["supreme_court"]

    def test_de_five_supreme_courts(self, ls):
        h = ls.get_court_hierarchy("DE")
        assert h is not None
        assert len(h["federal"]["federal_supreme_courts"]) >= 5

    def test_fr_dual_system(self, ls):
        h = ls.get_court_hierarchy("FR")
        assert h is not None
        assert "administrative" in h["federal"]

    def test_unknown_returns_none(self, ls):
        assert ls.get_court_hierarchy("XX") is None

    def test_case_insensitive(self, ls):
        assert ls.get_court_hierarchy("jp") == ls.get_court_hierarchy("JP")


class TestGetAppealProcess:
    def test_common_law_stages(self, ls):
        ap = ls.get_appeal_process("common_law")
        assert ap is not None
        assert len(ap["stages"]) >= 4
        assert "certiorari" in ap.get("description", "").lower() or any("discretionary" in s for s in ap["stages"])

    def test_civil_law_cassation(self, ls):
        ap = ls.get_appeal_process("civil_law")
        assert ap is not None
        assert "cassation" in ap["description"].lower()

    def test_religious_law_present(self, ls):
        ap = ls.get_appeal_process("religious_law")
        assert ap is not None
        assert "islamic" in ap["description"].lower()

    def test_mixed_present(self, ls):
        ap = ls.get_appeal_process("mixed")
        assert ap is not None
        assert "constitutional" in ap["description"].lower()

    def test_each_has_example_countries(self, ls):
        for st in ls.LEGAL_SYSTEM_TYPES:
            ap = ls.get_appeal_process(st)
            assert ap is not None, f"missing appeal process for {st}"
            assert "example_countries" in ap
            assert len(ap["example_countries"]) > 0


class TestGetTerm:
    def test_lookup_by_name(self, ls):
        term = ls.get_term("stare decisis")
        assert term is not None
        assert term["category"] == "common_law_concepts"

    def test_case_insensitive(self, ls):
        assert ls.get_term("Habeas Corpus") is not None

    def test_unknown_term_returns_none(self, ls):
        assert ls.get_term("nonexistent_term") is None

    def test_term_has_definition(self, ls):
        term = ls.get_term("force majeure")
        assert term is not None
        assert len(term["definition"]) > 20

    def test_term_has_related(self, ls):
        term = ls.get_term("stare decisis")
        assert "related_terms" in term
        assert len(term["related_terms"]) > 0


class TestTermsByCategory:
    def test_common_law_concepts(self, ls):
        terms = ls.terms_by_category("common_law_concepts")
        assert len(terms) >= 3
        names = {t["term"] for t in terms}
        assert "stare decisis" in names

    def test_procedure_category(self, ls):
        terms = ls.terms_by_category("procedure")
        assert len(terms) >= 3

    def test_criminal_law(self, ls):
        terms = ls.terms_by_category("criminal_law")
        names = {t["term"] for t in terms}
        assert "mens rea" in names
        assert "actus reus" in names

    def test_empty_for_unknown_category(self, ls):
        assert ls.terms_by_category("nonexistent_cat") == []


class TestCourtAtLevel:
    def test_us_supreme_court(self, ls):
        courts = ls.court_at_level("US", "supreme_court")
        assert len(courts) == 1
        assert "SCOTUS" in courts[0] or "Supreme Court" in courts[0]

    def test_de_federal_supreme_courts(self, ls):
        courts = ls.court_at_level("DE", "federal_supreme_courts")
        assert len(courts) >= 5

    def test_unknown_country(self, ls):
        assert ls.court_at_level("XX", "supreme_court") == []

    def test_unknown_level(self, ls):
        assert ls.court_at_level("US", "nonexistent_level") == []

    def test_case_insensitive_country(self, ls):
        c1 = ls.court_at_level("us", "supreme_court")
        c2 = ls.court_at_level("US", "supreme_court")
        assert c1 == c2


class TestSupremeCourt:
    def test_us_scotus(self, ls):
        sc = ls.supreme_court("US")
        assert sc is not None
        assert "Supreme Court" in sc

    def test_de_constitutional(self, ls):
        sc = ls.supreme_court("DE")
        assert "Bundesverfassungsgericht" in sc

    def test_gb_supreme_court(self, ls):
        sc = ls.supreme_court("GB")
        assert "Supreme Court" in sc

    def test_unknown_country(self, ls):
        assert ls.supreme_court("XX") is None


class TestListCountries:
    def test_returns_list(self, ls):
        countries = ls.list_countries()
        assert isinstance(countries, list)
        assert "US" in countries
        assert len(countries) >= 11

    def test_sorted(self, ls):
        countries = ls.list_countries()
        assert countries == sorted(countries)


class TestLegalTerminologyCompleteness:
    def test_minimum_terms(self, ls):
        assert len(ls.LEGAL_TERMINOLOGY) >= 20

    def test_each_term_has_category(self, ls):
        for key, term in ls.LEGAL_TERMINOLOGY.items():
            assert "category" in term, f"{key} missing category"
            assert "definition" in term, f"{key} missing definition"

    def test_categories_valid(self, ls):
        valid = {"common_law_concepts", "rights_and_remedies", "civil_law_concepts",
                 "procedure", "contract_law", "administrative_law", "criminal_law",
                 "remedies", "legal_profession"}
        for term in ls.LEGAL_TERMINOLOGY.values():
            assert term["category"] in valid, f"unknown category: {term['category']}"


class TestCompatibilityLookups:
    def test_lookup_legal_system_success_and_unknown(self, ls):
        result = ls.lookup_legal_system("us")
        assert result["found"] is True
        assert ls.lookup_legal_system("XX") is None

    def test_lookup_rights_charter_success_and_unknown(self, ls):
        result = ls.lookup_rights_charter("ECHR")
        assert result["found"] is True
        assert result["binding"] is True
        assert ls.lookup_rights_charter("unknown") is None

    def test_search_court_system_success_and_unknown(self, ls):
        result = ls.search_court_system("de")
        assert result["found"] is True
        assert result["country"] == "DE"
        assert ls.search_court_system("XX") is None
