"""Behavioral unit tests for the governance international_relations knowledge module.

Loads ``international_relations.py`` directly off its filesystem path with
importlib and exercises data tables and accessor functions. Follows the same
pattern as ``test_governance_tax_currency.py``.
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
    / "international_relations.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_intl_relations_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ir() -> ModuleType:
    return _load_module()


# ── Module exports / data table presence ─────────────────────────────────────


class TestModuleExports:
    def test_data_tables_present(self, ir):
        for attr in (
            "DIPLOMATIC_RELATIONS",
            "EMBASSIES",
            "SANCTIONS_REGIMES",
            "SANCTIONS_DATA",
            "TRADE_AGREEMENTS",
            "VISA_WAIVER_PROGRAMS",
        ):
            assert hasattr(ir, attr), f"missing data table {attr}"
            assert isinstance(getattr(ir, attr), dict)

    def test_functions_present(self, ir):
        for fn in (
            "get_diplomatic_relations",
            "get_embassy_info",
            "list_sanctions_regimes",
            "is_sanctioned",
            "get_sanctions_info",
            "get_trade_agreements",
            "list_trade_agreements",
            "get_visa_waiver_members",
            "list_visa_waiver_programs",
        ):
            assert callable(getattr(ir, fn, None)), f"missing function {fn}"


# ── DIPLOMATIC_RELATIONS ────────────────────────────────────────────────────


class TestDiplomaticRelations:
    @pytest.mark.parametrize(
        "country,expected_alliance_substr",
        [
            ("US", "NATO"),
            ("GB", "Five Eyes"),
            ("DE", "EU"),
            ("FR", "NATO"),
            ("JP", "Quad"),
            ("AU", "AUKUS"),
        ],
    )
    def test_major_countries_present(self, ir, country, expected_alliance_substr):
        assert country in ir.DIPLOMATIC_RELATIONS, f"missing diplomatic relations for {country}"
        data = ir.DIPLOMATIC_RELATIONS[country]
        assert "name" in data
        assert "alliances" in data and isinstance(data["alliances"], list)
        alliance_names = " ".join(data["alliances"])
        assert expected_alliance_substr in alliance_names, f"expected {expected_alliance_substr} in alliances"

    def test_all_countries_have_diplomatic_relations_count(self, ir):
        for code, data in ir.DIPLOMATIC_RELATIONS.items():
            assert "diplomatic_relations_count" in data, f"{code} missing diplomatic_relations_count"

    def test_all_countries_have_foreign_policy_posture(self, ir):
        valid_postures = {"superpower", "middle_power", "regional_power", "revisionist_power"}
        for code, data in ir.DIPLOMATIC_RELATIONS.items():
            posture = data.get("foreign_policy_posture")
            assert posture in valid_postures, f"{code} has unknown posture: {posture}"

    def test_unsc_p5_members(self, ir):
        p5 = {"US", "GB", "FR", "CN", "RU"}
        for code in p5:
            assert code in ir.DIPLOMATIC_RELATIONS, f"missing UNSC P5 member {code}"

    def test_us_diplomatic_relations_count_is_large(self, ir):
        assert ir.DIPLOMATIC_RELATIONS["US"]["diplomatic_relations_count"] >= 180


# ── EMBASSIES ────────────────────────────────────────────────────────────────


class TestEmbassies:
    @pytest.mark.parametrize("country", ["US", "GB", "DE", "FR", "JP"])
    def test_major_embassies_present(self, ir, country):
        assert country in ir.EMBASSIES, f"missing embassy info for {country}"
        data = ir.EMBASSIES[country]
        assert "embassies_worldwide" in data
        assert "consulates_worldwide" in data
        assert isinstance(data["embassies_worldwide"], int)

    def test_all_embassies_have_counts(self, ir):
        for code, data in ir.EMBASSIES.items():
            assert data["embassies_worldwide"] > 0, f"{code} has zero embassies reported"
            assert data["consulates_worldwide"] >= 0, f"{code} has negative consulates"

    def test_us_embassy_network_is_largest(self, ir):
        max_emb = max((data["embassies_worldwide"] for data in ir.EMBASSIES.values()), default=0)
        assert ir.EMBASSIES["US"]["embassies_worldwide"] >= max_emb

    def test_all_embassies_have_notable_absence_field(self, ir):
        for code, data in ir.EMBASSIES.items():
            assert "notable_absence" in data, f"{code} missing notable_absence"


# ── SANCTIONS_REGIMES and SANCTIONS_DATA ─────────────────────────────────────


class TestSanctionsRegimes:
    @pytest.mark.parametrize(
        "regime",
        ["un_security_council", "us_ofac", "eu_restrictive_measures", "uk_sanctions", "ofac_sectoral"],
    )
    def test_regime_defined(self, ir, regime):
        assert regime in ir.SANCTIONS_REGIMES, f"missing sanctions regime {regime}"
        info = ir.SANCTIONS_REGIMES[regime]
        assert "name" in info
        assert "administered_by" in info
        assert "measures" in info and isinstance(info["measures"], list)
        assert len(info["measures"]) > 0

    def test_all_regimes_have_type(self, ir):
        for key, data in ir.SANCTIONS_REGIMES.items():
            assert "type" in data, f"{key} missing type"
            assert data["type"] in ("multilateral", "unilateral", "multilateral_regional")

    def test_unsc_regime_is_multilateral(self, ir):
        assert ir.SANCTIONS_REGIMES["un_security_council"]["type"] == "multilateral"

    def test_us_ofac_is_unilateral(self, ir):
        assert ir.SANCTIONS_REGIMES["us_ofac"]["type"] == "unilateral"


class TestSanctionsData:
    @pytest.mark.parametrize(
        "country",
        ["KP", "IR", "RU", "BY", "SY", "CU", "VE", "MM", "AF"],
    )
    def test_sanctioned_countries_present(self, ir, country):
        assert country in ir.SANCTIONS_DATA, f"missing sanctions data for {country}"
        data = ir.SANCTIONS_DATA[country]
        assert "name" in data
        assert "sanctioned_by" in data
        assert "severity" in data
        assert len(data["sanctioned_by"]) >= 1

    def test_north_korea_sanctioned_by_unsc(self, ir):
        assert "un_security_council" in ir.SANCTIONS_DATA["KP"]["sanctioned_by"]

    def test_russia_not_sanctioned_by_unsc(self, ir):
        assert "un_security_council" not in ir.SANCTIONS_DATA["RU"]["sanctioned_by"]

    def test_cuba_only_sanctioned_by_us(self, ir):
        cu = ir.SANCTIONS_DATA["CU"]
        assert len(cu["sanctioned_by"]) == 1
        assert "us_ofac" in cu["sanctioned_by"]

    def test_iran_most_sanctioned(self, ir):
        ir_data = ir.SANCTIONS_DATA["IR"]
        assert len(ir_data["sanctioned_by"]) >= 3


class TestIsSanctioned:
    def test_us_sanctions_cuba(self, ir):
        assert ir.is_sanctioned("CU", "US") is True

    def test_us_sanctions_iran(self, ir):
        assert ir.is_sanctioned("IR", "US") is True

    def test_us_sanctions_russia(self, ir):
        assert ir.is_sanctioned("RU", "US") is True

    def test_france_sanctions_russia(self, ir):
        assert ir.is_sanctioned("RU", "FR") is True

    def test_germany_sanctions_belarus(self, ir):
        assert ir.is_sanctioned("BY", "DE") is True

    def test_uk_sanctions_syria(self, ir):
        assert ir.is_sanctioned("SY", "GB") is True

    def test_japan_does_not_sanction_cuba(self, ir):
        assert ir.is_sanctioned("CU", "JP") is False

    def test_canada_does_not_sanction_cuba(self, ir):
        assert ir.is_sanctioned("CU", "CA") is False

    def test_unknown_target_returns_false(self, ir):
        assert ir.is_sanctioned("XX", "US") is False

    def test_unknown_sender_returns_false(self, ir):
        assert ir.is_sanctioned("KP", "XX") is False

    def test_case_insensitive(self, ir):
        assert ir.is_sanctioned("ru", "us") == ir.is_sanctioned("RU", "US")

    def test_self_sanction_is_false(self, ir):
        assert ir.is_sanctioned("US", "US") is False


class TestGetSanctionsInfo:
    def test_north_korea(self, ir):
        info = ir.get_sanctions_info("KP")
        assert info is not None
        assert "DPRK" in info["name"] or "North Korea" in info["name"]
        assert info["severity"] == "comprehensive"

    def test_russia(self, ir):
        info = ir.get_sanctions_info("RU")
        assert info is not None
        assert "since_year" in info

    def test_unknown_country_returns_none(self, ir):
        assert ir.get_sanctions_info("XX") is None

    def test_case_insensitive(self, ir):
        assert ir.get_sanctions_info("ir") == ir.get_sanctions_info("IR")


# ── TRADE_AGREEMENTS ─────────────────────────────────────────────────────────


class TestTradeAgreements:
    @pytest.mark.parametrize(
        "agreement",
        ["usmca", "eu_single_market", "cptpp", "rcep", "mercosur", "afcfta", "tca"],
    )
    def test_major_agreements_present(self, ir, agreement):
        assert agreement in ir.TRADE_AGREEMENTS, f"missing trade agreement {agreement}"
        data = ir.TRADE_AGREEMENTS[agreement]
        assert "name" in data
        assert "parties" in data
        assert "scope" in data and isinstance(data["scope"], list)
        assert len(data["scope"]) >= 1

    def test_all_agreements_have_effective_year(self, ir):
        for key, data in ir.TRADE_AGREEMENTS.items():
            assert "effective_year" in data, f"{key} missing effective_year"

    def test_usmca_parties(self, ir):
        parties = ir.TRADE_AGREEMENTS["usmca"]["parties"]
        assert "US" in parties
        assert "CA" in parties
        assert "MX" in parties

    def test_eu_single_market_members(self, ir):
        parties = ir.TRADE_AGREEMENTS["eu_single_market"]["parties"]
        assert len(parties) >= 27
        assert "DE" in parties
        assert "FR" in parties

    def test_mercosur_is_customs_union(self, ir):
        assert ir.TRADE_AGREEMENTS["mercosur"]["type"] == "customs_union"

    def test_rcep_is_largest_by_population(self, ir):
        assert len(ir.TRADE_AGREEMENTS["rcep"]["parties"]) >= 10


class TestGetTradeAgreements:
    def test_us_has_usmca(self, ir):
        agreements = ir.get_trade_agreements("US")
        names = [a["name"] for a in agreements]
        assert any("USMCA" in n for n in names) or any("usmca" in str(a).lower() for a in agreements)

    def test_uk_has_tca(self, ir):
        agreements = ir.get_trade_agreements("GB")
        names = [a["name"] for a in agreements]
        assert any("Trade and Cooperation" in n or "TCA" in n for n in names)

    def test_small_country_returns_empty(self, ir):
        assert ir.get_trade_agreements("XX") == []

    def test_case_insensitive(self, ir):
        assert ir.get_trade_agreements("ca") == ir.get_trade_agreements("CA")

    def test_australia_in_cptpp_and_asean_cer(self, ir):
        agreements = ir.get_trade_agreements("AU")
        names = [a["name"] for a in agreements]
        assert any("CPTPP" in n for n in names)
        assert any("Closer Economic" in n or "Australia-New Zealand" in n for n in names)

    def test_japan_in_cptpp_and_rcep(self, ir):
        agreements = ir.get_trade_agreements("JP")
        names = [a["name"] for a in agreements]
        assert any("CPTPP" in n for n in names)
        assert any("RCEP" in n for n in names)


class TestListTradeAgreements:
    def test_returns_sorted_list(self, ir):
        agreements = ir.list_trade_agreements()
        assert isinstance(agreements, list)
        assert len(agreements) >= 5
        assert agreements == sorted(agreements)

    def test_includes_major_agreements(self, ir):
        agreements = ir.list_trade_agreements()
        for a in ("usmca", "cptpp", "rcep", "eu_single_market"):
            assert a in agreements, f"missing agreement {a}"


# ── VISA_WAIVER_PROGRAMS ─────────────────────────────────────────────────────


class TestVisaWaiverPrograms:
    @pytest.mark.parametrize(
        "program",
        [
            "us_visa_waiver",
            "schengen_visa_waiver",
            "uk_visa_waiver",
            "apac_business_travel_card",
            "ecowas_free_movement",
            "gcc_free_movement",
            "canzuk",
        ],
    )
    def test_program_defined(self, ir, program):
        assert program in ir.VISA_WAIVER_PROGRAMS, f"missing visa waiver program {program}"
        data = ir.VISA_WAIVER_PROGRAMS[program]
        assert "name" in data
        assert "type" in data

    def test_us_visa_waiver_has_many_members(self, ir):
        members = ir.VISA_WAIVER_PROGRAMS["us_visa_waiver"]["member_countries"]
        assert len(members) >= 30

    def test_schengen_includes_eu_countries(self, ir):
        members = ir.VISA_WAIVER_PROGRAMS["schengen_visa_waiver"]["member_countries"]
        assert "DE" in members
        assert "FR" in members
        assert "ES" in members

    def test_ecowas_has_15_members(self, ir):
        members = ir.VISA_WAIVER_PROGRAMS["ecowas_free_movement"]["member_countries"]
        assert len(members) == 15

    def test_gcc_has_six_members(self, ir):
        members = ir.VISA_WAIVER_PROGRAMS["gcc_free_movement"]["member_countries"]
        assert len(members) == 6
        assert "SA" in members
        assert "AE" in members

    def test_canzuk_is_proposed_not_implemented(self, ir):
        assert "proposed" in ir.VISA_WAIVER_PROGRAMS["canzuk"]["type"]

    def test_all_programs_have_conditions(self, ir):
        for key, data in ir.VISA_WAIVER_PROGRAMS.items():
            assert "conditions" in data, f"{key} missing conditions"
            assert isinstance(data["conditions"], list)
            assert len(data["conditions"]) >= 1


class TestGetVisaWaiverMembers:
    def test_us_program_members(self, ir):
        members = ir.get_visa_waiver_members("us_visa_waiver")
        assert members is not None
        assert len(members) >= 30
        assert "GB" in members
        assert "JP" in members

    def test_schengen_members(self, ir):
        members = ir.get_visa_waiver_members("schengen_visa_waiver")
        assert members is not None
        assert "DE" in members

    def test_canzuk_members(self, ir):
        members = ir.get_visa_waiver_members("canzuk")
        assert members is not None
        assert "CA" in members
        assert "GB" in members
        assert len(members) == 4

    def test_unknown_program_returns_none(self, ir):
        assert ir.get_visa_waiver_members("nonexistent_program") is None

    def test_case_insensitive(self, ir):
        a = ir.get_visa_waiver_members("US_VISA_WAIVER")
        b = ir.get_visa_waiver_members("us_visa_waiver")
        assert a == b


class TestListVisaWaiverPrograms:
    def test_returns_sorted_list(self, ir):
        programs = ir.list_visa_waiver_programs()
        assert isinstance(programs, list)
        assert len(programs) >= 5
        assert programs == sorted(programs)

    def test_includes_major_programs(self, ir):
        programs = ir.list_visa_waiver_programs()
        for p in ("us_visa_waiver", "schengen_visa_waiver", "uk_visa_waiver"):
            assert p in programs, f"missing program {p}"


class TestLookupCompatibilityAccessors:
    def test_lookup_diplomatic_relations_success_and_unknown(self, ir):
        result = ir.lookup_diplomatic_relations("us")
        assert result["found"] is True
        assert result["country"] == "US"
        assert ir.lookup_diplomatic_relations("XX") is None

    def test_lookup_sanctions_success_and_unknown(self, ir):
        result = ir.lookup_sanctions("IR")
        assert result["found"] is True
        assert ir.lookup_sanctions("XX") is None

    def test_search_alliance_finds_trade_agreement_and_members(self, ir):
        trade_results = ir.search_alliance("USMCA")
        assert any("USMCA" in item["name"] for item in trade_results)

        nato_results = ir.search_alliance("NATO")
        assert any(item.get("member") == "US" for item in nato_results)
        assert all(
            item.get("full_name") == "North Atlantic Treaty Organization"
            for item in nato_results
        )


# ── Functions (accessor round-trip) ──────────────────────────────────────────


class TestGetDiplomaticRelations:
    def test_us_profile(self, ir):
        result = ir.get_diplomatic_relations("US")
        assert result is not None
        assert result["name"] == "United States"
        assert "superpower" in result.get("foreign_policy_posture", "")

    def test_uk_profile(self, ir):
        result = ir.get_diplomatic_relations("GB")
        assert result is not None
        assert "un_member_since" in result

    def test_unknown_country_returns_none(self, ir):
        assert ir.get_diplomatic_relations("XX") is None

    def test_case_insensitive(self, ir):
        assert ir.get_diplomatic_relations("us") == ir.get_diplomatic_relations("US")


class TestGetEmbassyInfo:
    def test_us_info(self, ir):
        info = ir.get_embassy_info("US")
        assert info is not None
        assert info["embassies_worldwide"] >= 150

    def test_uk_info(self, ir):
        info = ir.get_embassy_info("GB")
        assert info is not None
        assert "largest_embassy" in info

    def test_unknown_country_returns_none(self, ir):
        assert ir.get_embassy_info("XX") is None

    def test_case_insensitive(self, ir):
        assert ir.get_embassy_info("jp") == ir.get_embassy_info("JP")
