"""Unit tests for the governance contracts knowledge modules.

Tests both the ansible module_utils data module (CONTRACTS, CONTRACT_TYPES,
CONTRACT_STATUSES, and query functions) and the Python src package contracts
(GovernancePolicy, GovernanceRule, ComplianceModel, PolicyRegistry dataclasses).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from general_ludd.governance.contracts import (
    ComplianceModel,
    GovernancePolicy,
    GovernanceRule,
    PolicyRegistry,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "governance"
    / "plugins"
    / "module_utils"
    / "contracts.py"
)

MODULE_NAME = "_contracts_under_test"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load_module()


class TestContractTypes:
    def test_frozenset_contains_core_types(self, mod):
        assert "treaty" in mod.CONTRACT_TYPES
        assert "bilateral_agreement" in mod.CONTRACT_TYPES
        assert "multilateral_convention" in mod.CONTRACT_TYPES
        assert "memorandum_of_understanding" in mod.CONTRACT_TYPES
        assert "protocol" in mod.CONTRACT_TYPES
        assert "amendment" in mod.CONTRACT_TYPES

    def test_frozenset_is_immutable(self, mod):
        with pytest.raises(AttributeError):
            mod.CONTRACT_TYPES.remove("treaty")


class TestContractStatuses:
    def test_frozenset_contains_all_statuses(self, mod):
        assert "in_force" in mod.CONTRACT_STATUSES
        assert "signed" in mod.CONTRACT_STATUSES
        assert "ratified" in mod.CONTRACT_STATUSES
        assert "provisionally_applied" in mod.CONTRACT_STATUSES
        assert "dormant" in mod.CONTRACT_STATUSES
        assert "superseded" in mod.CONTRACT_STATUSES
        assert "denounced" in mod.CONTRACT_STATUSES
        assert "expired" in mod.CONTRACT_STATUSES

    def test_frozenset_is_immutable(self, mod):
        with pytest.raises(AttributeError):
            mod.CONTRACT_STATUSES.remove("in_force")


class TestContractsRegistry:
    def test_registry_is_non_empty(self, mod):
        assert isinstance(mod.CONTRACTS, dict)
        assert len(mod.CONTRACTS) >= 10

    def test_un_charter_exists(self, mod):
        charter = mod.CONTRACTS.get("UN_CHARTER")
        assert charter is not None
        assert charter["type"] == "multilateral_convention"
        assert charter["status"] == "in_force"
        assert "effective_date" in charter
        assert "United Nations" in charter["name"]

    def test_paris_agreement_exists(self, mod):
        pa = mod.CONTRACTS.get("PARIS_AGREEMENT")
        assert pa is not None
        assert pa["type"] in ("multilateral_convention", "treaty")
        assert len(pa["parties"]) >= 2

    def test_nato_treaty_exists(self, mod):
        nato = mod.CONTRACTS.get("NATO_TREATY")
        assert nato is not None
        assert nato["type"] == "multilateral_convention"
        assert "US" in nato["parties"]

    def test_every_contract_has_required_fields(self, mod):
        required = {"code", "name", "type", "status", "parties"}
        date_fields = {"effective_date", "signature_date", "adoption_date"}
        for code, contract in mod.CONTRACTS.items():
            missing = required - set(contract.keys())
            assert not missing, f"{code}: missing fields {missing}"
            has_date = bool(set(contract.keys()) & date_fields)
            assert has_date, f"{code}: missing at least one date field"

    def test_every_contract_type_is_valid(self, mod):
        for contract in mod.CONTRACTS.values():
            assert contract["type"] in mod.CONTRACT_TYPES, f"{contract['code']}: invalid type {contract['type']}"

    def test_every_contract_status_is_valid(self, mod):
        for contract in mod.CONTRACTS.values():
            assert contract["status"] in mod.CONTRACT_STATUSES, (
                f"{contract['code']}: invalid status {contract['status']}"
            )

    def test_contract_parties_are_frozenset(self, mod):
        for contract in mod.CONTRACTS.values():
            assert isinstance(contract["parties"], frozenset), f"{contract['code']}: parties should be frozenset"


class TestGetContract:
    def test_existing_contract(self, mod):
        c = mod.get_contract("UN_CHARTER")
        assert c is not None
        assert c["code"] == "UN_CHARTER"
        assert "United Nations" in c["name"]

    def test_nonexistent_contract(self, mod):
        assert mod.get_contract("NONEXISTENT") is None

    def test_case_insensitive(self, mod):
        c = mod.get_contract("un_charter")
        assert c is not None
        assert c["code"] == "UN_CHARTER"

    def test_returns_dict_copy_not_reference(self, mod):
        c1 = mod.get_contract("UN_CHARTER")
        c2 = mod.get_contract("UN_CHARTER")
        assert c1 is not c2
        assert c1 == c2


class TestContractsByParty:
    def test_us_party_to_nato(self, mod):
        results = mod.contracts_by_party("US")
        codes = [r["code"] for r in results]
        assert "NATO_TREATY" in codes
        assert "UN_CHARTER" in codes

    def test_fr_party_to_some(self, mod):
        results = mod.contracts_by_party("FR")
        assert len(results) > 0
        for r in results:
            assert "FR" in r["parties"]

    def test_unknown_country_returns_empty(self, mod):
        assert mod.contracts_by_party("XX") == []

    def test_case_insensitive_country(self, mod):
        results_upper = mod.contracts_by_party("US")
        results_lower = mod.contracts_by_party("us")
        assert len(results_upper) == len(results_lower)


class TestContractsByType:
    def test_multilateral_convention(self, mod):
        results = mod.contracts_by_type("multilateral_convention")
        assert len(results) > 0
        for r in results:
            assert r["type"] == "multilateral_convention"

    def test_bilateral_agreement(self, mod):
        results = mod.contracts_by_type("bilateral_agreement")
        for r in results:
            assert r["type"] == "bilateral_agreement"

    def test_unknown_type_returns_empty(self, mod):
        assert mod.contracts_by_type("nonexistent_type") == []

    def test_case_insensitive_type(self, mod):
        results = mod.contracts_by_type("TREATY")
        for r in results:
            assert r["type"] == "treaty"


class TestContractsByStatus:
    def test_in_force_contracts(self, mod):
        results = mod.contracts_by_status("in_force")
        assert len(results) > 0
        for r in results:
            assert r["status"] == "in_force"

    def test_superseded_contracts(self, mod):
        results = mod.contracts_by_status("superseded")
        for r in results:
            assert r["status"] == "superseded"

    def test_unknown_status_returns_empty(self, mod):
        assert mod.contracts_by_status("nonexistent_status") == []


class TestListContracts:
    def test_returns_all_by_default(self, mod):
        results = mod.list_contracts()
        assert len(results) >= 10
        for r in results:
            assert "code" in r
            assert "name" in r
            assert "type" in r
            assert "status" in r

    def test_filtered_by_type_and_status(self, mod):
        results = mod.list_contracts(contract_type="multilateral_convention", status="in_force")
        for r in results:
            assert r["type"] == "multilateral_convention"
            assert r["status"] == "in_force"

    def test_filtered_by_party(self, mod):
        results = mod.list_contracts(party="US")
        for r in results:
            assert "US" in r["parties"]
        codes = [r["code"] for r in results]
        assert "UN_CHARTER" in codes

    def test_empty_result_for_impossible_combo(self, mod):
        results = mod.list_contracts(contract_type="treaty", party="XX")
        assert results == []


class TestContractParties:
    def test_un_charter_many_parties(self, mod):
        parties = mod.get_contract_parties("UN_CHARTER")
        assert parties is not None
        assert isinstance(parties, frozenset)
        assert "US" in parties
        assert "FR" in parties

    def test_bilateral_has_two_parties(self, mod):
        for contract in mod.CONTRACTS.values():
            if contract["type"] == "bilateral_agreement":
                parties = mod.get_contract_parties(contract["code"])
                assert len(parties) == 2, f"{contract['code']}: bilateral must have 2 parties"

    def test_unknown_contract_returns_none(self, mod):
        assert mod.get_contract_parties("NONEXISTENT") is None

    def test_case_insensitive(self, mod):
        parties = mod.get_contract_parties("un_charter")
        assert parties is not None
        assert "US" in parties


# ── src/general_ludd/governance/contracts.py tests ─────────────────────────


class TestGovernancePolicy:
    def test_create_policy(self):
        p = GovernancePolicy(
            name="trade_agreement",
            level="international",
            description="International trade agreement policy",
            domain="trade",
        )
        assert p.name == "trade_agreement"
        assert p.level == "international"
        assert p.description == "International trade agreement policy"
        assert p.domain == "trade"
        assert p.status == "draft"
        assert p.effective_date is None

    def test_create_policy_full(self):
        p = GovernancePolicy(
            name="immigration_act",
            level="federal",
            description="Immigration control act",
            domain="border",
            status="active",
            effective_date="2024-01-01",
        )
        assert p.status == "active"
        assert p.effective_date == "2024-01-01"

    def test_policy_equality(self):
        p1 = GovernancePolicy(name="p1", level="federal", description="desc", domain="trade")
        p2 = GovernancePolicy(name="p1", level="federal", description="desc", domain="trade")
        assert p1 == p2

    def test_policy_inequality(self):
        p1 = GovernancePolicy(name="p1", level="federal", description="desc", domain="trade")
        p2 = GovernancePolicy(name="p2", level="federal", description="desc", domain="trade")
        assert p1 != p2


class TestGovernanceRule:
    def test_create_rule(self):
        r = GovernanceRule(
            policy_name="trade_agreement",
            rule_id="R001",
            condition="goods_cross_border",
            action="permit",
        )
        assert r.policy_name == "trade_agreement"
        assert r.rule_id == "R001"
        assert r.condition == "goods_cross_border"
        assert r.action == "permit"
        assert r.priority == 0
        assert r.enforcement == "advisory"

    def test_create_rule_full(self):
        r = GovernanceRule(
            policy_name="immigration_act",
            rule_id="R001",
            condition="visa_required",
            action="deny",
            priority=10,
            enforcement="mandatory",
        )
        assert r.action == "deny"
        assert r.priority == 10
        assert r.enforcement == "mandatory"

    def test_rule_action_values(self):
        valid_actions = {"permit", "deny", "require", "recommend"}
        r = GovernanceRule(policy_name="p", rule_id="R1", condition="c", action="permit")
        assert r.action in valid_actions

    def test_rule_enforcement_values(self):
        valid_enforcement = {"advisory", "mandatory", "automatic"}
        r = GovernanceRule(policy_name="p", rule_id="R1", condition="c", action="permit")
        assert r.enforcement in valid_enforcement

    def test_rule_equality(self):
        r1 = GovernanceRule(policy_name="p1", rule_id="R001", condition="c", action="permit")
        r2 = GovernanceRule(policy_name="p1", rule_id="R001", condition="c", action="permit")
        assert r1 == r2

    def test_rule_repr(self):
        r = GovernanceRule(policy_name="trade", rule_id="R1", condition="import", action="permit")
        assert "trade" in repr(r)
        assert "R1" in repr(r)


class TestComplianceModel:
    def test_create_compliance(self):
        cm = ComplianceModel(
            subject="US",
            policy_name="trade_agreement",
            compliance_status="compliant",
            requirements_met=["tariff_schedule", "import_controls"],
            requirements_unmet=[],
            audit_trail=["2024-01-01: initial assessment"],
        )
        assert cm.subject == "US"
        assert cm.policy_name == "trade_agreement"
        assert cm.compliance_status == "compliant"
        assert len(cm.requirements_met) == 2
        assert cm.requirements_unmet == []
        assert cm.last_reviewed is None

    def test_non_compliant(self):
        cm = ComplianceModel(
            subject="XX",
            policy_name="immigration_act",
            compliance_status="non_compliant",
            requirements_met=[],
            requirements_unmet=["entry_exit_system", "visa_waiver"],
            audit_trail=["2024-06-01: non-compliance found"],
        )
        assert cm.compliance_status == "non_compliant"
        assert len(cm.requirements_unmet) == 2

    def test_partial_compliance(self):
        cm = ComplianceModel(
            subject="FR",
            policy_name="trade_agreement",
            compliance_status="partial",
            requirements_met=["tariff_schedule"],
            requirements_unmet=["import_controls"],
            audit_trail=[],
        )
        assert cm.compliance_status == "partial"

    def test_compliance_status_values(self):
        valid_statuses = {"compliant", "non_compliant", "partial", "unknown"}
        cm = ComplianceModel(
            subject="US",
            policy_name="p",
            compliance_status="unknown",
            requirements_met=[],
            requirements_unmet=[],
            audit_trail=[],
        )
        assert cm.compliance_status in valid_statuses

    def test_compliance_equality(self):
        cm1 = ComplianceModel(
            subject="US",
            policy_name="p",
            compliance_status="compliant",
            requirements_met=[],
            requirements_unmet=[],
            audit_trail=[],
        )
        cm2 = ComplianceModel(
            subject="US",
            policy_name="p",
            compliance_status="compliant",
            requirements_met=[],
            requirements_unmet=[],
            audit_trail=[],
        )
        assert cm1 == cm2


class TestPolicyRegistry:
    def test_empty_registry(self):
        reg = PolicyRegistry()
        assert len(reg) == 0

    def test_add_and_get_policy(self):
        reg = PolicyRegistry()
        p = GovernancePolicy(name="trade", level="international", description="Trade policy", domain="trade")
        reg.add_policy(p)
        assert len(reg) == 1
        assert reg.get_policy("trade") is p

    def test_get_nonexistent_policy(self):
        reg = PolicyRegistry()
        assert reg.get_policy("nonexistent") is None

    def test_remove_policy(self):
        reg = PolicyRegistry()
        p = GovernancePolicy(name="trade", level="international", description="desc", domain="trade")
        reg.add_policy(p)
        assert reg.remove_policy("trade") is True
        assert len(reg) == 0

    def test_remove_nonexistent(self):
        reg = PolicyRegistry()
        assert reg.remove_policy("nonexistent") is False

    def test_add_duplicate_raises(self):
        reg = PolicyRegistry()
        p = GovernancePolicy(name="trade", level="international", description="desc", domain="trade")
        reg.add_policy(p)
        with pytest.raises(ValueError, match="already exists"):
            reg.add_policy(p)

    def test_list_policies(self):
        reg = PolicyRegistry()
        p1 = GovernancePolicy(name="p1", level="federal", description="d1", domain="trade")
        p2 = GovernancePolicy(name="p2", level="state", description="d2", domain="border")
        reg.add_policy(p1)
        reg.add_policy(p2)
        names = [p.name for p in reg.list_policies()]
        assert "p1" in names
        assert "p2" in names

    def test_list_policies_by_domain(self):
        reg = PolicyRegistry()
        p1 = GovernancePolicy(name="p1", level="federal", description="d1", domain="trade")
        p2 = GovernancePolicy(name="p2", level="state", description="d2", domain="border")
        reg.add_policy(p1)
        reg.add_policy(p2)
        trade_policies = reg.list_policies(domain="trade")
        assert len(trade_policies) == 1
        assert trade_policies[0].name == "p1"

    def test_list_policies_by_level(self):
        reg = PolicyRegistry()
        p1 = GovernancePolicy(name="p1", level="federal", description="d1", domain="trade")
        p2 = GovernancePolicy(name="p2", level="state", description="d2", domain="border")
        reg.add_policy(p1)
        reg.add_policy(p2)
        federal = reg.list_policies(level="federal")
        assert len(federal) == 1
        assert federal[0].name == "p1"

    def test_add_rule_to_policy(self):
        reg = PolicyRegistry()
        p = GovernancePolicy(name="trade", level="international", description="desc", domain="trade")
        reg.add_policy(p)
        r = GovernanceRule(policy_name="trade", rule_id="R1", condition="import", action="permit")
        reg.add_rule(r)
        rules = reg.get_rules("trade")
        assert len(rules) == 1
        assert rules[0].rule_id == "R1"

    def test_add_rule_to_nonexistent_policy(self):
        reg = PolicyRegistry()
        r = GovernanceRule(policy_name="nonexistent", rule_id="R1", condition="c", action="permit")
        with pytest.raises(KeyError, match="nonexistent"):
            reg.add_rule(r)

    def test_get_rules_nonexistent_policy(self):
        reg = PolicyRegistry()
        assert reg.get_rules("nonexistent") == []

    def test_iter_registry(self):
        reg = PolicyRegistry()
        p1 = GovernancePolicy(name="p1", level="federal", description="d1", domain="trade")
        p2 = GovernancePolicy(name="p2", level="state", description="d2", domain="border")
        reg.add_policy(p1)
        reg.add_policy(p2)
        names = {p.name for p in reg}
        assert names == {"p1", "p2"}

    def test_contains(self):
        reg = PolicyRegistry()
        p = GovernancePolicy(name="trade", level="international", description="desc", domain="trade")
        reg.add_policy(p)
        assert "trade" in reg
        assert "nonexistent" not in reg
