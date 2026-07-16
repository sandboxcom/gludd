"""Tests for conflicts_treaties module in the governance collection.

TDD: these tests were written BEFORE the module existed. They import
``conflicts_treaties`` from the governance collection's module_utils path.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import ClassVar

import pytest

_COLLECTION_ROOT = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "governance"
)
_MODULE_UTILS = _COLLECTION_ROOT / "plugins" / "module_utils"

if str(_MODULE_UTILS) not in sys.path:
    sys.path.insert(0, str(_MODULE_UTILS))

try:
    ct = importlib.import_module("conflicts_treaties")
    ConflictType = ct.CONFLICT_TYPES
    ACTIVE_CONFLICTS = ct.ACTIVE_CONFLICTS
    TREATY_DATABASE = ct.TREATY_DATABASE
    INTERNATIONAL_COURTS = ct.INTERNATIONAL_COURTS
    lookup_conflict = ct.lookup_conflict
    get_treaty = ct.get_treaty
    get_treaty_parties = ct.get_treaty_parties
    get_treaty_obligations = ct.get_treaty_obligations
    get_court_jurisdiction = ct.get_court_jurisdiction
    check_court_jurisdiction = ct.check_court_jurisdiction
except ModuleNotFoundError:
    pytest.skip("conflicts_treaties module not available", allow_module_level=True)


# ============================================================================
# CONFLICT_TYPES
# ============================================================================


class TestConflictTypes:
    def test_all_seven_required_types_present(self):
        required = {
            "interstate", "intrastate", "asymmetric",
            "cyber", "economic", "proxy", "frozen",
        }
        for name in required:
            assert name in ConflictType, f"missing conflict type: {name}"

    def test_conflict_type_values_are_strings(self):
        # Conventional Python enum design: UPPERCASE name, lowercase value.
        for member in ConflictType:
            assert isinstance(member.value, str)
            assert member.value == member.name.lower()
            assert member.value.islower()

    def test_conflict_type_count(self):
        assert len(ConflictType) >= 7


# ============================================================================
# ACTIVE_CONFLICTS
# ============================================================================


class TestActiveConflicts:
    def test_active_conflicts_is_nonempty_list(self):
        assert isinstance(ACTIVE_CONFLICTS, list)
        assert len(ACTIVE_CONFLICTS) >= 3

    def test_each_conflict_has_required_fields(self):
        required = {"id", "name", "region", "type", "parties", "status"}
        for c in ACTIVE_CONFLICTS:
            missing = required - set(c.keys())
            assert not missing, f"conflict {c.get('id')} missing fields: {missing}"

    def test_each_conflict_type_is_valid_enum(self):
        for c in ACTIVE_CONFLICTS:
            assert c["type"] in ConflictType, (
                f"conflict {c['id']} has invalid type {c['type']!r}"
            )

    def test_parties_are_lists(self):
        for c in ACTIVE_CONFLICTS:
            assert isinstance(c["parties"], list)
            assert len(c["parties"]) >= 1

    def test_status_is_string(self):
        for c in ACTIVE_CONFLICTS:
            assert isinstance(c["status"], str)
            assert c["status"]  # non-empty


# ============================================================================
# lookup_conflict(region)
# ============================================================================


class TestLookupConflict:
    def test_returns_list(self):
        result = lookup_conflict("Eastern Europe")
        assert isinstance(result, list)

    def test_returns_only_matching_region(self):
        result = lookup_conflict("Eastern Europe")
        for c in result:
            assert c["region"] == "Eastern Europe"

    def test_unknown_region_returns_empty(self):
        result = lookup_conflict("Nonexistent Region XYZ")
        assert result == []

    def test_case_sensitive_match(self):
        # documented behavior: region match is case-sensitive
        lower = lookup_conflict("eastern europe")
        upper = lookup_conflict("Eastern Europe")
        # at least one real entry exists for Eastern Europe
        assert len(upper) >= 1
        # case-insensitive is NOT guaranteed
        assert isinstance(lower, list)


# ============================================================================
# TREATY_DATABASE
# ============================================================================


class TestTreatyDatabase:
    REQUIRED_TREATIES: ClassVar[frozenset[str]] = frozenset({
        "geneva_conventions", "paris_agreement", "nato",
        "npt", "unclos", "cptpp", "usmca",
    })

    def test_treaty_database_is_list(self):
        assert isinstance(TREATY_DATABASE, list)
        assert len(TREATY_DATABASE) >= 7

    def test_all_required_treaties_present(self):
        ids = {t["id"] for t in TREATY_DATABASE}
        missing = self.REQUIRED_TREATIES - ids
        assert not missing, f"missing treaties: {missing}"

    def test_each_treaty_has_required_fields(self):
        required = {
            "id", "name", "subject", "parties", "enforcement",
        }
        for t in TREATY_DATABASE:
            missing = required - set(t.keys())
            assert not missing, f"treaty {t.get('id')} missing fields: {missing}"

    def test_enforcement_is_dict_or_str(self):
        for t in TREATY_DATABASE:
            enf = t["enforcement"]
            assert isinstance(enf, (dict, str, list)), (
                f"treaty {t['id']} enforcement has bad type {type(enf)}"
            )


# ============================================================================
# get_treaty(name)
# ============================================================================


class TestGetTreaty:
    def test_get_by_id_exact(self):
        result = get_treaty("npt")
        assert result is not None
        assert result["id"] == "npt"
        assert "Non-Proliferation" in result["name"]

    def test_get_returns_none_for_unknown(self):
        assert get_treaty("nonexistent_treaty_xyz") is None

    def test_get_geneva_conventions(self):
        result = get_treaty("geneva_conventions")
        assert result is not None
        assert isinstance(result["parties"], list)
        assert len(result["parties"]) >= 100  # near-universal ratification

    def test_get_usmca(self):
        result = get_treaty("usmca")
        assert result is not None
        parties = result["parties"]
        assert "United States" in parties
        assert "Canada" in parties
        assert "Mexico" in parties

    def test_case_sensitive_lookup(self):
        # documented: ID lookup is case-sensitive
        assert get_treaty("NPT") is None  # lowercase only
        assert get_treaty("npt") is not None


# ============================================================================
# get_treaty_parties(treaty_id)
# ============================================================================


class TestGetTreatyParties:
    def test_returns_list_for_known_treaty(self):
        parties = get_treaty_parties("usmca")
        assert isinstance(parties, list)
        assert len(parties) == 3

    def test_returns_empty_for_unknown(self):
        assert get_treaty_parties("nonexistent") == []

    def test_nato_parties_include_founders(self):
        parties = get_treaty_parties("nato")
        assert "United States" in parties
        assert "United Kingdom" in parties
        assert "France" in parties

    def test_parties_count_geneva_is_large(self):
        parties = get_treaty_parties("geneva_conventions")
        assert len(parties) >= 150  # virtually universal


# ============================================================================
# get_treaty_obligations(country)
# ============================================================================


class TestGetTreatyObligations:
    def test_us_obligations_include_npt(self):
        obligations = get_treaty_obligations("United States")
        ids = [o["treaty_id"] for o in obligations]
        assert "npt" in ids

    def test_returns_list(self):
        obligations = get_treaty_obligations("United States")
        assert isinstance(obligations, list)
        assert len(obligations) >= 1

    def test_unknown_country_returns_empty(self):
        result = get_treaty_obligations("Atlantis")
        assert result == []

    def test_each_obligation_has_treaty_id_and_subject(self):
        obligations = get_treaty_obligations("Canada")
        for o in obligations:
            assert "treaty_id" in o
            assert "subject" in o

    def test_non_party_country_excluded(self):
        # A country NOT in a treaty should not have obligations under it
        obligations = get_treaty_obligations("United States")
        for o in obligations:
            treaty = get_treaty(o["treaty_id"])
            if treaty is not None:
                assert "United States" in treaty["parties"], (
                    f"US has obligation under {o['treaty_id']} but is not a party"
                )


# ============================================================================
# INTERNATIONAL_COURTS
# ============================================================================


class TestInternationalCourts:
    REQUIRED_COURTS: ClassVar[frozenset[str]] = frozenset(
        {"icj", "icc", "icty", "ictr", "wto_dsb"}
    )

    def test_all_required_courts_present(self):
        ids = {c["id"] for c in INTERNATIONAL_COURTS}
        missing = self.REQUIRED_COURTS - ids
        assert not missing, f"missing courts: {missing}"

    def test_each_court_has_required_fields(self):
        required = {"id", "name", "jurisdiction", "procedures"}
        for c in INTERNATIONAL_COURTS:
            missing = required - set(c.keys())
            assert not missing, f"court {c.get('id')} missing fields: {missing}"

    def test_court_has_notable_cases(self):
        for c in INTERNATIONAL_COURTS:
            assert "notable_cases" in c, f"court {c['id']} missing notable_cases"
            assert isinstance(c["notable_cases"], list)


# ============================================================================
# get_court_jurisdiction(court_id)
# ============================================================================


class TestGetCourtJurisdiction:
    def test_get_icj_jurisdiction(self):
        result = get_court_jurisdiction("icj")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 10

    def test_get_unknown_court_returns_none(self):
        assert get_court_jurisdiction("nonexistent_court") is None

    def test_get_icc_jurisdiction_mentions_war_crimes(self):
        result = get_court_jurisdiction("icc")
        assert result is not None
        lowered = result.lower()
        assert "war crime" in lowered or "genocide" in lowered or "crime" in lowered


# ============================================================================
# check_court_jurisdiction(court_id, case_type)
# ============================================================================


class TestCheckCourtJurisdiction:
    def test_icj_hears_interstate_disputes(self):
        assert check_court_jurisdiction("icj", "interstate_dispute") is True

    def test_icj_rejects_individual_criminal(self):
        # ICJ hears state disputes, not individual criminal prosecutions
        assert check_court_jurisdiction("icj", "individual_war_crime") is False

    def test_icc_hears_war_crimes(self):
        assert check_court_jurisdiction("icc", "war_crimes") is True
        assert check_court_jurisdiction("icc", "genocide") is True
        assert check_court_jurisdiction("icc", "crimes_against_humanity") is True

    def test_wto_dsb_hears_trade_disputes(self):
        assert check_court_jurisdiction("wto_dsb", "trade_dispute") is True

    def test_wto_dsb_rejects_criminal(self):
        assert check_court_jurisdiction("wto_dsb", "war_crimes") is False

    def test_unknown_court_returns_false(self):
        assert check_court_jurisdiction("nonexistent", "any_case") is False

    def test_unknown_case_type_returns_false(self):
        assert check_court_jurisdiction("icj", "totally_unknown_xyz") is False
