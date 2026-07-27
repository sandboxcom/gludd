"""TDD tests for governance decision_makers module_utils."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_COLLECTION_ROOT = (
    Path(__file__).resolve().parents[2] / "collections" / "ansible_collections" / "general_ludd" / "governance"
)
_PLUGIN_ROOT = _COLLECTION_ROOT / "plugins"

if str(_PLUGIN_ROOT / "module_utils") not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT / "module_utils"))

try:
    dm = importlib.import_module("decision_makers")
except ModuleNotFoundError as exc:
    pytest.skip(f"decision_makers module not available: {exc}", allow_module_level=True)


ROLE_TYPES = dm.ROLE_TYPES
DECISION_MAKER_PROFILES = dm.DECISION_MAKER_PROFILES
DECISION_MAKER_PROFILE_TEMPLATE = dm.DECISION_MAKER_PROFILE_TEMPLATE
INFLUENCE_NETWORKS = dm.INFLUENCE_NETWORKS
BIAS_INDICATORS = dm.BIAS_INDICATORS
lookup_decision_maker = dm.lookup_decision_maker
get_decision_authority = dm.get_decision_authority
get_influence_network = dm.get_influence_network
find_decision_maker = dm.find_decision_maker
assess_proclivity = dm.assess_proclivity
DECISION_MAKERS = dm.DECISION_MAKERS
list_countries = dm.list_countries
lookup_decision_makers = dm.lookup_decision_makers
BRANCHES = dm.BRANCHES


class TestRoleTypes:
    def test_role_types_contains_head_of_state(self):
        assert "head_of_state" in ROLE_TYPES

    def test_role_types_contains_all_required(self):
        required = {
            "head_of_state",
            "minister",
            "legislator",
            "judge",
            "regulator",
            "military_leader",
            "diplomat",
            "bureaucrat",
            "local_official",
        }
        assert required.issubset(set(ROLE_TYPES))

    def test_role_types_count(self):
        assert len(ROLE_TYPES) >= 9


class TestProfileTemplate:
    def test_template_has_required_fields(self):
        required = {
            "name",
            "title",
            "body",
            "jurisdiction",
            "term",
            "appointment_process",
            "decision_authority",
            "known_positions",
            "voting_record_summary",
            "public_statements",
            "campaign_finance",
            "lobbying_connections",
        }
        assert required.issubset(set(DECISION_MAKER_PROFILE_TEMPLATE))

    def test_template_field_types(self):
        assert DECISION_MAKER_PROFILE_TEMPLATE["name"] is str
        assert DECISION_MAKER_PROFILE_TEMPLATE["decision_authority"] is dict
        assert DECISION_MAKER_PROFILE_TEMPLATE["known_positions"] is list


class TestProfilesIntegrity:
    def test_every_profile_has_required_keys(self):
        required = set(DECISION_MAKER_PROFILE_TEMPLATE)
        for p in DECISION_MAKER_PROFILES:
            missing = required - set(p)
            assert not missing, f"profile {p.get('person_id')} missing {missing}"

    def test_every_profile_role_is_valid(self):
        for p in DECISION_MAKER_PROFILES:
            assert p["role"] in ROLE_TYPES, f"invalid role: {p['role']}"

    def test_every_profile_has_person_id(self):
        ids = [p["person_id"] for p in DECISION_MAKER_PROFILES]
        assert len(ids) == len(set(ids)), "duplicate person_ids"
        for pid in ids:
            assert pid and isinstance(pid, str)


class TestLookupDecisionMaker:
    def test_returns_profiles_for_known_body(self):
        results = lookup_decision_maker("us-senate")
        assert len(results) == 1
        assert results[0]["person_id"] == "us-sen-01"

    def test_returns_empty_for_unknown_body(self):
        assert lookup_decision_maker("nope") == []

    def test_filters_by_role(self):
        legislators = lookup_decision_maker("us-house", role="legislator")
        assert len(legislators) == 1
        assert legislators[0]["person_id"] == "us-house-01"

    def test_role_filter_returns_empty_on_mismatch(self):
        judges = lookup_decision_maker("us-senate", role="judge")
        assert judges == []

    def test_invalid_role_returns_empty(self):
        assert lookup_decision_maker("us-senate", role="wizard") == []


class TestGetDecisionAuthority:
    def test_returns_authority_block(self):
        auth = get_decision_authority("us-fed-01")
        assert auth is not None
        assert "powers" in auth
        assert "set_interest_rate" in auth["powers"]

    def test_unknown_person_returns_none(self):
        assert get_decision_authority("ghost") is None

    def test_authority_is_binding_flag(self):
        for p in DECISION_MAKER_PROFILES:
            auth = get_decision_authority(p["person_id"])
            assert auth is not None
            assert isinstance(auth.get("binding"), bool)


class TestGetInfluenceNetwork:
    def test_returns_networks_for_affiliated_person(self):
        nets = get_influence_network("us-sen-01")
        assert nets is not None
        assert len(nets) >= 2
        assert "political_parties" in nets or "think_tanks" in nets

    def test_returns_empty_dict_for_unaffiliated(self):
        nets = get_influence_network("us-fed-01")
        assert nets is not None
        assert nets == {}

    def test_unknown_person_returns_none(self):
        assert get_influence_network("nobody") is None

    def test_network_categories_match_influence_networks_keys(self):
        nets = get_influence_network("us-sen-01") or {}
        for category in nets:
            assert category in INFLUENCE_NETWORKS


class TestFindDecisionMaker:
    def test_finds_by_known_position_topic(self):
        hits = find_decision_maker("taxation")
        pids = {h["person_id"] for h in hits}
        assert "us-sen-01" in pids

    def test_finds_by_authority_scope(self):
        hits = find_decision_maker("monetary policy")
        pids = {h["person_id"] for h in hits}
        assert "us-fed-01" in pids

    def test_jurisdiction_filter_narrows(self):
        hits = find_decision_maker("antitrust", jurisdiction="EU")
        assert all(h["jurisdiction"] == "EU" for h in hits)
        assert any(h["person_id"] == "eu-com-01" for h in hits)

    def test_jurisdiction_filter_excludes(self):
        hits = find_decision_maker("antitrust", jurisdiction="US-Federal")
        pids = {h["person_id"] for h in hits}
        assert "eu-com-01" not in pids

    def test_no_match_returns_empty(self):
        assert find_decision_maker("quantum_gravity") == []

    def test_case_insensitive_topic_match(self):
        hits = find_decision_maker("TAXATION")
        assert any(h["person_id"] == "us-sen-01" for h in hits)


class TestAssessProclivity:
    def test_unknown_person_returns_error(self):
        result = assess_proclivity("ghost", "taxation")
        assert "error" in result
        assert result["person_id"] == "ghost"

    def test_returns_score_in_range(self):
        result = assess_proclivity("us-sen-01", "taxation")
        assert "score" in result
        assert -1.0 <= result["score"] <= 1.0

    def test_returns_lean_label(self):
        result = assess_proclivity("us-sen-01", "taxation")
        assert result["lean"] in {"restrictive", "expansionist", "neutral"}

    def test_returns_signals_list(self):
        result = assess_proclivity("us-sen-01", "taxation")
        assert isinstance(result["signals"], list)
        assert len(result["signals"]) >= 1

    def test_returns_confidence_in_range(self):
        result = assess_proclivity("us-sen-01", "defense_spending")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_no_matching_signals_is_neutral(self):
        result = assess_proclivity("us-sen-01", "quantum_gravity")
        assert result["lean"] == "neutral"
        assert result["score"] == 0.0
        assert result["confidence"] == 0.0

    def test_known_position_drives_lean_expansionist(self):
        result = assess_proclivity("us-sen-01", "taxation")
        assert result["lean"] == "expansionist"

    def test_known_position_drives_lean_restrictive(self):
        result = assess_proclivity("us-sc-01", "civil_rights")
        assert result["lean"] == "restrictive"

    def test_influence_network_signal_included(self):
        result = assess_proclivity("us-sen-01", "defense_spending")
        sources = {s["source"] for s in result["signals"]}
        assert "influence_network" in sources or "known_position" in sources

    def test_voting_record_signal_for_legislator(self):
        result = assess_proclivity("us-house-01", "healthcare")
        sources = {s["source"] for s in result["signals"]}
        assert "voting_record" in sources


class TestBiasIndicators:
    def test_bias_indicators_has_required_keys(self):
        required = {
            "voting_patterns",
            "campaign_donors",
            "board_memberships",
            "statements_on_topic",
        }
        assert required.issubset(set(BIAS_INDICATORS))

    def test_bias_indicators_values_are_descriptive(self):
        for _key, desc in BIAS_INDICATORS.items():
            assert isinstance(desc, str)
            assert len(desc) > 10


class TestLegacyDecisionMakers:
    def test_decision_makers_has_expanded_countries(self):
        assert len(DECISION_MAKERS) >= 15
        required = {"US", "GB", "DE", "FR", "JP", "IN", "AU", "IT", "ES", "MX", "ZA", "KR", "SE", "NL", "BR"}
        assert required.issubset(set(DECISION_MAKERS))

    def test_list_countries_returns_sorted(self):
        countries = list_countries()
        assert isinstance(countries, list)
        assert len(countries) >= 15
        assert countries == sorted(countries)

    def test_lookup_known_country(self):
        result = lookup_decision_makers("US")
        assert result["found"] is True
        assert result["count"] >= 3

    def test_lookup_unknown_country(self):
        result = lookup_decision_makers("ZZ")
        assert result["found"] is False

    def test_lookup_with_branch_filter(self):
        result = lookup_decision_makers("US", branch="executive")
        assert result["found"] is True
        for dm_entry in result["decision_makers"]:
            assert dm_entry["branch"] == "executive"

    def test_new_countries_present(self):
        for country in ("IT", "ES", "MX", "ZA", "KR", "SE", "NL", "BR"):
            result = lookup_decision_makers(country)
            assert result["found"] is True, f"Missing {country}"
            assert result["count"] >= 3, f"{country} has fewer than 3 office holders"

    def test_every_country_has_branches(self):
        for country in DECISION_MAKERS:
            result = lookup_decision_makers(country)
            assert "available_branches" in result
            assert len(result["available_branches"]) >= 2

    def test_branches_set_has_key_types(self):
        expected = {"executive", "head of state", "legislative", "judicial"}
        assert expected.issubset(BRANCHES)
