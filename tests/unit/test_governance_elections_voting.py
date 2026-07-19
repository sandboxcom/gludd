"""Behavioral unit tests for the governance elections_voting knowledge module.

Loads ``elections_voting.py`` directly off its filesystem path with importlib
and exercises data tables and accessor functions. Follows the same pattern as
``test_governance_tax_currency.py``.
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
    / "elections_voting.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_elections_voting_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev() -> ModuleType:
    return _load_module()


# ── Module exports / data table presence ─────────────────────────────────────


class TestModuleExports:
    def test_data_tables_present(self, ev):
        for attr in ("ELECTION_SYSTEMS", "ELECTION_DATA", "ELECTORAL_BODIES", "POLLING_PROCEDURES"):
            assert hasattr(ev, attr), f"missing data table {attr}"
            assert isinstance(getattr(ev, attr), dict)

    def test_functions_present(self, ev):
        for fn in (
            "get_election_info",
            "list_election_systems",
            "get_electoral_body",
            "get_polling_procedures",
            "get_voter_eligibility",
            "list_countries_with_elections",
        ):
            assert callable(getattr(ev, fn, None)), f"missing function {fn}"


# ── ELECTION_SYSTEMS coverage ────────────────────────────────────────────────


class TestElectionSystems:
    @pytest.mark.parametrize(
        "system_type",
        [
            "fptp",
            "runoff",
            "proportional_representation_list",
            "mixed_member_proportional",
            "parallel",
            "single_transferable_vote",
            "instant_runoff",
            "electoral_college",
            "majority_bonus",
        ],
    )
    def test_system_defined(self, ev, system_type):
        assert system_type in ev.ELECTION_SYSTEMS, f"missing system {system_type}"
        info = ev.ELECTION_SYSTEMS[system_type]
        assert "description" in info and isinstance(info["description"], str)
        assert len(info["description"]) > 10
        assert "ballot_type" in info
        assert "district_type" in info

    def test_each_system_has_example_countries(self, ev):
        for system_type, info in ev.ELECTION_SYSTEMS.items():
            assert "example_countries" in info, f"{system_type} missing example_countries"
            assert isinstance(info["example_countries"], list)
            assert len(info["example_countries"]) > 0

    def test_fptp_and_runoff_are_distinct(self, ev):
        assert ev.ELECTION_SYSTEMS["fptp"]["description"] != ev.ELECTION_SYSTEMS["runoff"]["description"]
        assert ev.ELECTION_SYSTEMS["fptp"]["district_type"] == ev.ELECTION_SYSTEMS["runoff"]["district_type"]

    def test_pr_and_mmp_are_distinct(self, ev):
        pr = ev.ELECTION_SYSTEMS["proportional_representation_list"]
        mmp = ev.ELECTION_SYSTEMS["mixed_member_proportional"]
        assert pr["ballot_type"] != mmp["ballot_type"]


# ── ELECTION_DATA country coverage ───────────────────────────────────────────


class TestElectionData:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP", "CA", "AU", "IN", "BR", "ZA"])
    def test_major_countries_present(self, ev, country):
        assert country in ev.ELECTION_DATA, f"missing election data for {country}"
        data = ev.ELECTION_DATA[country]
        assert "name" in data
        assert "voting_age" in data
        assert isinstance(data["voting_age"], int)
        assert data["voting_age"] >= 16

    def test_each_country_has_system(self, ev):
        for code, data in ev.ELECTION_DATA.items():
            assert "system_for_lower_house" in data, f"{code} missing system_for_lower_house"
            sys_type = data["system_for_lower_house"]
            if sys_type is not None:
                assert sys_type in ev.ELECTION_SYSTEMS, f"{code} lower-house system {sys_type} unknown"

    def test_compulsory_voting_countries(self, ev):
        compulsory = [
            code for code, data in ev.ELECTION_DATA.items()
            if data.get("compulsory_voting")
        ]
        assert len(compulsory) >= 2, f"expected at least 2 compulsory-voting countries, got {len(compulsory)}"
        assert "AU" in compulsory
        assert "BR" in compulsory

    def test_term_length_structure(self, ev):
        for code, data in ev.ELECTION_DATA.items():
            tl = data.get("term_length_years")
            assert tl is not None, f"{code} missing term_length_years"
            assert isinstance(tl, dict)
            assert len(tl) >= 1

    def test_us_presidential_system_is_electoral_college(self, ev):
        assert ev.ELECTION_DATA["US"]["presidential_system"] == "electoral_college"

    def test_australia_uses_irv_and_stv(self, ev):
        au = ev.ELECTION_DATA["AU"]
        assert au["system_for_lower_house"] == "instant_runoff"
        assert au["system_for_upper_house"] == "single_transferable_vote"

    def test_germany_mmp(self, ev):
        de = ev.ELECTION_DATA["DE"]
        assert de["system_for_lower_house"] == "mixed_member_proportional"

    def test_india_largest_electorate(self, ev):
        in_data = ev.ELECTION_DATA["IN"]
        assert in_data.get("registered_voters_approx", 0) > 500_000_000


# ── ELECTORAL_BODIES ─────────────────────────────────────────────────────────


class TestElectoralBodies:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "IN", "AU", "BR", "ZA"])
    def test_major_electoral_bodies_present(self, ev, country):
        assert country in ev.ELECTORAL_BODIES, f"missing electoral body for {country}"
        body = ev.ELECTORAL_BODIES[country]
        assert "name" in body
        assert "independence" in body
        assert "role" in body

    def test_all_bodies_have_portal_url(self, ev):
        for key, body in ev.ELECTORAL_BODIES.items():
            assert "portal_url" in body, f"{key} electoral body missing portal_url"
            assert body["portal_url"].startswith(("http://", "https://")), f"{key} invalid portal_url"

    def test_independence_levels_coverage(self, ev):
        levels = {body["independence"] for body in ev.ELECTORAL_BODIES.values()}
        assert len(levels) >= 3, f"expected at least 3 independence levels, got {len(levels)}"

    def test_in_eci_is_constitutional_body(self, ev):
        assert ev.ELECTORAL_BODIES["IN"]["independence"] == "constitutional_body"


# ── POLLING_PROCEDURES ───────────────────────────────────────────────────────


class TestPollingProcedures:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "IN", "BR", "AU", "ZA"])
    def test_major_polling_procedures_present(self, ev, country):
        assert country in ev.POLLING_PROCEDURES, f"missing polling procedures for {country}"
        proc = ev.POLLING_PROCEDURES[country]
        assert "polling_station_type" in proc
        assert "opening_hours" in proc

    def test_all_procedures_have_counting_method(self, ev):
        for key, proc in ev.POLLING_PROCEDURES.items():
            assert "counting_method" in proc, f"{key} missing counting_method"

    def test_all_procedures_have_id_requirements(self, ev):
        for key, proc in ev.POLLING_PROCEDURES.items():
            assert "identification_required" in proc, f"{key} missing identification_required"

    def test_uk_requires_photo_id(self, ev):
        assert ev.POLLING_PROCEDURES["GB"]["identification_required"] is True

    def test_australia_no_photo_id(self, ev):
        assert ev.POLLING_PROCEDURES["AU"]["identification_required"] is False

    def test_brazil_electronic_machines(self, ev):
        assert "electronic" in ev.POLLING_PROCEDURES["BR"]["counting_method"].lower()

    def test_france_manual_public_count(self, ev):
        assert "manual" in ev.POLLING_PROCEDURES["FR"]["counting_method"].lower()


# ── Functions ────────────────────────────────────────────────────────────────


class TestGetElectionInfo:
    def test_us_profile(self, ev):
        result = ev.get_election_info("US")
        assert result is not None
        assert result["country"] == "US"
        assert result["name"] == "United States"
        assert "voting_age" in result

    def test_uk_profile(self, ev):
        result = ev.get_election_info("GB")
        assert result is not None
        assert result["country"] == "GB"
        assert result["compulsory_voting"] is False

    def test_australia_compulsory(self, ev):
        result = ev.get_election_info("AU")
        assert result is not None
        assert result["compulsory_voting"] is True

    def test_unknown_country_returns_none(self, ev):
        assert ev.get_election_info("XX") is None

    def test_case_insensitive(self, ev):
        upper = ev.get_election_info("US")
        lower = ev.get_election_info("us")
        assert upper is not None
        assert lower is not None
        assert upper == lower


class TestListElectionSystems:
    def test_returns_sorted_list(self, ev):
        systems = ev.list_election_systems()
        assert isinstance(systems, list)
        assert len(systems) >= 5
        assert systems == sorted(systems)

    def test_includes_major_systems(self, ev):
        systems = ev.list_election_systems()
        for s in ("fptp", "runoff", "proportional_representation_list", "mixed_member_proportional"):
            assert s in systems, f"missing system {s}"


class TestGetElectoralBody:
    def test_us_fec(self, ev):
        body = ev.get_electoral_body("US")
        assert body is not None
        assert "FEC" in body["name"] or "Federal" in body["name"]

    def test_in_eci(self, ev):
        body = ev.get_electoral_body("IN")
        assert body is not None
        assert "India" in body["name"] or "Election Commission" in body["name"]

    def test_uk_electoral_commission(self, ev):
        body = ev.get_electoral_body("GB")
        assert body is not None
        assert "Electoral Commission" in body["name"]

    def test_unknown_country_returns_none(self, ev):
        assert ev.get_electoral_body("XX") is None

    def test_case_insensitive(self, ev):
        assert ev.get_electoral_body("us") == ev.get_electoral_body("US")


class TestGetPollingProcedures:
    def test_us_procedures(self, ev):
        proc = ev.get_polling_procedures("US")
        assert proc is not None
        assert "polling_station_type" in proc

    def test_in_procedures(self, ev):
        proc = ev.get_polling_procedures("IN")
        assert proc is not None
        assert "EVMs with VVPAT" in proc.get("counting_method", "") or "electronic" in proc.get("counting_method", "")

    def test_de_procedures(self, ev):
        proc = ev.get_polling_procedures("DE")
        assert proc is not None
        assert "manual" in proc.get("counting_method", "").lower()

    def test_unknown_country_returns_none(self, ev):
        assert ev.get_polling_procedures("XX") is None

    def test_case_insensitive(self, ev):
        assert ev.get_polling_procedures("DE") == ev.get_polling_procedures("de")


class TestGetVoterEligibility:
    def test_us_eligibility(self, ev):
        info = ev.get_voter_eligibility("US")
        assert info is not None
        assert info["voting_age"] == 18
        assert info["compulsory_voting"] is False

    def test_brazil_eligibility(self, ev):
        info = ev.get_voter_eligibility("BR")
        assert info is not None
        assert info["voting_age"] == 16
        assert info["compulsory_voting"] is True

    def test_unknown_country_returns_none(self, ev):
        assert ev.get_voter_eligibility("XX") is None


class TestListCountriesWithElections:
    def test_returns_sorted_list(self, ev):
        countries = ev.list_countries_with_elections()
        assert isinstance(countries, list)
        assert len(countries) >= 8
        assert "US" in countries
        assert countries == sorted(countries)

    def test_includes_all_election_data_keys(self, ev):
        countries = ev.list_countries_with_elections()
        for key in ev.ELECTION_DATA:
            assert key in countries
