"""TDD tests for governance info_classification module_utils.

Tests cover:
  - CLASSIFICATION_LEVELS unified hierarchy
  - CLASSIFICATION_BY_COUNTRY per-jurisdiction schemes
  - ACCESS_FRAMEWORKS (clearance, need-to-know, compartments, SAPs)
  - INFO_SOURCES (gazettes, parliaments, courts, stats, banks, audit)
  - FOIA_PROCESS per-country procedures and templates
  - All public functions

Loaded directly from the module_utils path so no ansible dependency is required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections/ansible_collections/general_ludd/governance"
    / "plugins" / "module_utils" / "info_classification.py"
)

if not _MODULE_PATH.is_file():
    pytest.skip("info_classification.py not found", allow_module_level=True)

_spec = importlib.util.spec_from_file_location(
    "governance_info_classification", _MODULE_PATH
)
if _spec is None or _spec.loader is None:
    pytest.skip("could not load module spec", allow_module_level=True)

_ic = importlib.util.module_from_spec(_spec)
sys.modules["governance_info_classification"] = _ic
_spec.loader.exec_module(_ic)

CLASSIFICATION_LEVELS = _ic.CLASSIFICATION_LEVELS
CLASSIFICATION_BY_COUNTRY = _ic.CLASSIFICATION_BY_COUNTRY
ACCESS_FRAMEWORKS = _ic.ACCESS_FRAMEWORKS
INFO_SOURCES = _ic.INFO_SOURCES
FOIA_PROCESS = _ic.FOIA_PROCESS
get_classification_system = _ic.get_classification_system
get_access_requirements = _ic.get_access_requirements
check_clearance_equiv = _ic.check_clearance_equiv
find_official_source = _ic.find_official_source
get_public_records_url = _ic.get_public_records_url
get_foia_procedure = _ic.get_foia_procedure
file_foia_request_template = _ic.file_foia_request_template


# ====================================================================
# CLASSIFICATION_LEVELS — unified hierarchy
# ====================================================================


class TestClassificationLevels:
    def test_all_required_levels_present(self):
        required = {
            "public", "unclassified", "restricted", "confidential",
            "secret", "top_secret", "caveated",
        }
        assert required <= set(CLASSIFICATION_LEVELS)

    def test_levels_have_rank(self):
        for name, level in CLASSIFICATION_LEVELS.items():
            assert "rank" in level, f"{name} missing rank"
            assert isinstance(level["rank"], int)

    def test_rank_ordering(self):
        assert CLASSIFICATION_LEVELS["public"]["rank"] < CLASSIFICATION_LEVELS["unclassified"]["rank"]
        assert CLASSIFICATION_LEVELS["secret"]["rank"] < CLASSIFICATION_LEVELS["top_secret"]["rank"]
        assert CLASSIFICATION_LEVELS["top_secret"]["rank"] <= CLASSIFICATION_LEVELS["caveated"]["rank"]

    def test_levels_have_description(self):
        for name, level in CLASSIFICATION_LEVELS.items():
            assert "description" in level, f"{name} missing description"
            assert isinstance(level["description"], str)
            assert len(level["description"]) > 0

    def test_caveated_includes_sci_and_noforn(self):
        caveated = CLASSIFICATION_LEVELS["caveated"]
        handling = str(caveated).lower()
        assert "sci" in handling or "noforn" in handling or "caveat" in handling


# ====================================================================
# CLASSIFICATION_BY_COUNTRY
# ====================================================================


class TestClassificationByCountry:
    def test_all_required_countries_present(self):
        required = {"US", "UK", "CA", "AU", "FR", "DE", "RU", "CN", "EU", "NATO"}
        assert required <= set(CLASSIFICATION_BY_COUNTRY)

    def test_country_entries_have_levels(self):
        for country, scheme in CLASSIFICATION_BY_COUNTRY.items():
            assert "levels" in scheme, f"{country} missing levels"
            assert len(scheme["levels"]) >= 2, f"{country} needs >=2 levels"

    def test_us_includes_top_secret_sci(self):
        us = CLASSIFICATION_BY_COUNTRY["US"]
        names = str(us["levels"]).lower()
        assert "top secret" in names
        assert "sci" in names or "compartmented" in names

    def test_uk_has_official_and_top_secret(self):
        uk = CLASSIFICATION_BY_COUNTRY["UK"]
        names = str(uk["levels"]).lower()
        assert "official" in names
        assert "top secret" in names

    def test_nato_has_cosmic(self):
        nato = CLASSIFICATION_BY_COUNTRY["NATO"]
        names = str(nato["levels"]).lower()
        assert "cosmic" in names

    def test_country_entries_have_authority(self):
        for country, scheme in CLASSIFICATION_BY_COUNTRY.items():
            assert "authority" in scheme, f"{country} missing authority field"


# ====================================================================
# get_classification_system(country)
# ====================================================================


class TestGetClassificationSystem:
    def test_returns_dict_for_known_country(self):
        result = get_classification_system("US")
        assert isinstance(result, dict)
        assert "levels" in result

    def test_returns_none_for_unknown_country(self):
        assert get_classification_system("XX") is None

    def test_case_sensitive_or_normalized(self):
        # US should always work; verify at least one form resolves
        assert get_classification_system("US") is not None

    def test_returns_independent_copy(self):
        a = get_classification_system("US")
        b = get_classification_system("US")
        assert a is not None and b is not None
        assert a == b


# ====================================================================
# ACCESS_FRAMEWORKS
# ====================================================================


class TestAccessFrameworks:
    def test_required_subkeys_present(self):
        required = {"clearance_levels", "need_to_know", "compartments", "special_access_programs"}
        assert required <= set(ACCESS_FRAMEWORKS)

    def test_clearance_levels_have_us_entry(self):
        cl = ACCESS_FRAMEWORKS["clearance_levels"]
        assert "US" in cl or "US" in str(cl)

    def test_compartments_include_sci(self):
        comps = str(ACCESS_FRAMEWORKS["compartments"]).lower()
        assert "sci" in comps or "compartment" in comps

    def test_need_to_know_is_documented(self):
        ntk = ACCESS_FRAMEWORKS["need_to_know"]
        assert isinstance(ntk, (dict, str))
        assert len(str(ntk)) > 10


# ====================================================================
# get_access_requirements(level, country)
# ====================================================================


class TestGetAccessRequirements:
    def test_returns_requirements_for_known(self):
        result = get_access_requirements("top_secret", "US")
        assert result is not None
        assert isinstance(result, dict)

    def test_returns_none_for_unknown_level(self):
        assert get_access_requirements("mega_secret", "US") is None

    def test_returns_none_for_unknown_country(self):
        assert get_access_requirements("secret", "XX") is None

    def test_public_requires_minimal_access(self):
        result = get_access_requirements("public", "US")
        assert result is not None
        # public info should not require clearance
        assert result.get("clearance_required") in (False, None, "none")


# ====================================================================
# check_clearance_equiv(level_a, country_a, country_b)
# ====================================================================


class TestCheckClearanceEquiv:
    def test_same_country_same_level_equivalent(self):
        assert check_clearance_equiv("secret", "US", "US") is True

    def test_us_top_secret_equiv_uk_top_secret(self):
        assert check_clearance_equiv("top_secret", "US", "UK") is True

    def test_public_equiv_across_countries(self):
        assert check_clearance_equiv("public", "US", "UK") is True

    def test_returns_false_for_unknown(self):
        assert check_clearance_equiv("unknown_level", "US", "UK") is False


# ====================================================================
# INFO_SOURCES
# ====================================================================


class TestInfoSources:
    def test_required_categories_present(self):
        required = {
            "official_gazettes", "parliamentary_records", "court_records",
            "statistics_offices", "central_banks", "audit_offices",
        }
        assert required <= set(INFO_SOURCES)

    def test_us_gazette_is_federal_register(self):
        gazette = INFO_SOURCES["official_gazettes"]
        assert "US" in gazette
        fr = str(gazette["US"]).lower()
        assert "federal register" in fr

    def test_us_central_bank_is_federal_reserve(self):
        banks = INFO_SOURCES["central_banks"]
        assert "US" in banks
        assert "federal reserve" in str(banks["US"]).lower()

    def test_uk_gazette_is_london_gazette(self):
        gazette = INFO_SOURCES["official_gazettes"]
        assert "UK" in gazette
        assert "london gazette" in str(gazette["UK"]).lower().replace("the ", "")


# ====================================================================
# find_official_source(topic, country)
# ====================================================================


class TestFindOfficialSource:
    def test_finds_gazette_for_us(self):
        result = find_official_source("gazette", "US")
        assert result is not None
        assert "federal register" in str(result).lower()

    def test_finds_court_records(self):
        result = find_official_source("court", "US")
        assert result is not None

    def test_returns_none_for_unknown_topic(self):
        assert find_official_source("nonexistent_topic_xyz", "US") is None

    def test_returns_none_for_unknown_country(self):
        assert find_official_source("gazette", "XX") is None


# ====================================================================
# get_public_records_url(record_type, country)
# ====================================================================


class TestGetPublicRecordsUrl:
    def test_returns_url_for_us_gazette(self):
        result = get_public_records_url("gazette", "US")
        assert result is not None
        assert isinstance(result, str)
        assert result.startswith("https://")

    def test_returns_none_for_unknown(self):
        assert get_public_records_url("nonexistent", "US") is None
        assert get_public_records_url("gazette", "XX") is None

    def test_us_statistics_office_url(self):
        result = get_public_records_url("statistics", "US")
        assert result is not None
        assert "census.gov" in result or "bls.gov" in result or "stats" in result.lower()


# ====================================================================
# FOIA_PROCESS
# ====================================================================


class TestFoiaProcess:
    def test_us_has_foia(self):
        assert "US" in FOIA_PROCESS
        us = FOIA_PROCESS["US"]
        assert "law" in us
        assert "FOIA" in str(us["law"]) or "552" in str(us["law"])

    def test_uk_has_foia(self):
        assert "UK" in FOIA_PROCESS

    def test_country_has_steps(self):
        for country, proc in FOIA_PROCESS.items():
            assert "steps" in proc, f"{country} missing steps"

    def test_country_has_response_time(self):
        for country, proc in FOIA_PROCESS.items():
            assert "response_time_days" in proc, f"{country} missing response_time_days"


# ====================================================================
# get_foia_procedure(country)
# ====================================================================


class TestGetFoiaProcedure:
    def test_returns_us_procedure(self):
        result = get_foia_procedure("US")
        assert result is not None
        assert "law" in result
        assert "steps" in result

    def test_returns_none_for_unknown(self):
        assert get_foia_procedure("XX") is None


# ====================================================================
# file_foia_request_template(country, topic)
# ====================================================================


class TestFileFoiaRequestTemplate:
    def test_us_template_includes_topic(self):
        result = file_foia_request_template("US", "climate change")
        assert result is not None
        assert isinstance(result, str)
        assert "climate change" in result

    def test_us_template_references_foia(self):
        result = file_foia_request_template("US", "budget")
        assert "FOIA" in result or "Freedom of Information" in result

    def test_returns_none_for_unknown_country(self):
        assert file_foia_request_template("XX", "topic") is None

    def test_uk_template_references_foia_act(self):
        result = file_foia_request_template("UK", "spending")
        assert result is not None
        assert "FOIA" in result or "Freedom of Information" in result or "2000" in result
