"""Unit tests for governance core: PolicyEngine, ComplianceChecker, CapabilityRouter."""

from __future__ import annotations

import pytest

from general_ludd.governance.contracts import (
    AuditTrail,
    Policy,
    Rule,
)
from general_ludd.governance.core import CapabilityRouter, ComplianceChecker, PolicyEngine


class TestPolicyEngine:
    def test_register_policy(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Data Retention",
            description="Controls retention.",
            domain="data-governance",
            level="enterprise",
        )
        engine.register_policy(policy)
        assert "Data Retention" in engine
        assert engine.get_policy("Data Retention") is policy

    def test_register_duplicate_policy_raises_valueerror(self):
        engine = PolicyEngine()
        policy = Policy(
            name="P",
            description="desc",
            domain="d",
            level="l",
        )
        engine.register_policy(policy)
        with pytest.raises(ValueError, match="already registered"):
            engine.register_policy(policy)

    def test_register_rule_for_existing_policy(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        rule = Rule(
            policy_name="Security",
            rule_id="SEC-001",
            condition="vuln_found",
            action="block",
        )
        engine.register_rule(rule)
        rules = engine.get_rules("Security")
        assert len(rules) == 1
        assert rules[0].rule_id == "SEC-001"

    def test_register_rule_for_missing_policy_raises_keyerror(self):
        engine = PolicyEngine()
        rule = Rule(
            policy_name="Ghost",
            rule_id="G-1",
            condition="c",
            action="a",
        )
        with pytest.raises(KeyError, match="Ghost"):
            engine.register_rule(rule)

    def test_get_policy_missing_returns_none(self):
        engine = PolicyEngine()
        assert engine.get_policy("Nonexistent") is None

    def test_list_policies_empty(self):
        engine = PolicyEngine()
        assert engine.list_policies() == []

    def test_list_policies_with_filters(self):
        engine = PolicyEngine()
        p1 = Policy(
            name="Sec",
            description="d",
            domain="security",
            level="enterprise",
        )
        p2 = Policy(
            name="Data",
            description="d",
            domain="data",
            level="project",
        )
        engine.register_policy(p1)
        engine.register_policy(p2)
        assert len(engine.list_policies(domain="security")) == 1
        assert engine.list_policies(domain="security")[0].name == "Sec"
        assert len(engine.list_policies(level="project")) == 1
        assert engine.list_policies(level="project")[0].name == "Data"

    def test_list_policies_no_match(self):
        engine = PolicyEngine()
        p = Policy(
            name="P",
            description="d",
            domain="d",
            level="l",
        )
        engine.register_policy(p)
        assert engine.list_policies(domain="nonexistent") == []

    def test_len_engine(self):
        engine = PolicyEngine()
        assert len(engine) == 0
        p = Policy(
            name="P",
            description="d",
            domain="d",
            level="l",
        )
        engine.register_policy(p)
        assert len(engine) == 1

    def test_iter_engine(self):
        engine = PolicyEngine()
        p1 = Policy(
            name="P1",
            description="d",
            domain="d",
            level="l",
        )
        p2 = Policy(
            name="P2",
            description="d",
            domain="d",
            level="l",
        )
        engine.register_policy(p1)
        engine.register_policy(p2)
        names = [p.name for p in engine]
        assert names == ["P1", "P2"]

    def test_contains(self):
        engine = PolicyEngine()
        p = Policy(
            name="P",
            description="d",
            domain="d",
            level="l",
        )
        engine.register_policy(p)
        assert "P" in engine
        assert "Q" not in engine


class TestComplianceChecker:
    def test_check_empty_engine(self):
        engine = PolicyEngine()
        checker = ComplianceChecker(engine)
        report = checker.check("repo-1")
        assert report.subject == "repo-1"
        assert report.status == "compliant"

    def test_check_all_rules_met(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has_encryption",
                action="require",
            )
        )

        def always_pass(rule, subject):
            return True

        checker = ComplianceChecker(engine, evaluate_fn=always_pass)
        report = checker.check("repo-1")
        assert report.status == "compliant"
        assert report.violations == []

    def test_check_with_violations(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has_encryption",
                action="require",
            )
        )
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-2",
                condition="has_audit_log",
                action="require",
            )
        )

        def fail_encryption(rule, subject):
            return rule.rule_id != "S-1"

        checker = ComplianceChecker(engine, evaluate_fn=fail_encryption)
        report = checker.check("repo-1")
        assert report.status == "non_compliant"
        assert report.violations == ["S-1"]
        assert report.is_compliant is False

    def test_check_includes_audit_trail(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has_encryption",
                action="require",
            )
        )

        checker = ComplianceChecker(engine)
        report = checker.check("repo-1")
        assert isinstance(report.audit_trail, list)
        assert len(report.audit_trail) == 1
        assert isinstance(report.audit_trail[0], AuditTrail)

    def test_check_multiple_policies(self):
        engine = PolicyEngine()
        p1 = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        p2 = Policy(
            name="Data",
            description="d",
            domain="data",
            level="e",
        )
        engine.register_policy(p1)
        engine.register_policy(p2)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="c",
                action="a",
            )
        )
        engine.register_rule(
            Rule(
                policy_name="Data",
                rule_id="D-1",
                condition="c",
                action="a",
            )
        )

        checker = ComplianceChecker(engine)
        report = checker.check("repo-1")
        assert report.status in ("compliant", "non_compliant")
        assert len(report.audit_trail) == 2

    def test_report_created_at_is_set(self):
        engine = PolicyEngine()
        checker = ComplianceChecker(engine)
        report = checker.check("repo-1")
        assert report.created_at is not None


class TestCapabilityRouter:
    def test_init_empty(self):
        router = CapabilityRouter()
        assert router.has_all(set()) is True
        assert router.has_any(set()) is False

    def test_add_and_has(self):
        router = CapabilityRouter()
        router.add("encryption")
        router.add("audit_log")
        assert router.has_all({"encryption"}) is True
        assert router.has_all({"encryption", "audit_log"}) is True
        assert router.has_any({"encryption"}) is True

    def test_remove(self):
        router = CapabilityRouter({"encryption", "audit_log"})
        router.remove("encryption")
        assert router.has_all({"encryption"}) is False
        assert router.has_all({"audit_log"}) is True

    def test_interpret_condition_has(self):
        router = CapabilityRouter()
        caps = {"encryption", "audit_log"}
        assert router.interpret_condition("has:encryption", caps) is True
        assert router.interpret_condition("has:missing_cap", caps) is False

    def test_interpret_condition_missing(self):
        router = CapabilityRouter()
        caps = {"encryption"}
        assert router.interpret_condition("missing:audit_log", caps) is True
        assert router.interpret_condition("missing:encryption", caps) is False

    def test_interpret_condition_all_of(self):
        router = CapabilityRouter()
        caps = {"encryption", "audit_log", "backup"}
        assert router.interpret_condition("all_of:encryption,audit_log", caps) is True
        assert router.interpret_condition("all_of:encryption,missing_cap", caps) is False

    def test_interpret_condition_any_of(self):
        router = CapabilityRouter()
        caps = {"encryption"}
        assert router.interpret_condition("any_of:encryption,audit_log", caps) is True
        assert router.interpret_condition("any_of:missing_a,missing_b", caps) is False

    def test_interpret_condition_bare_string_defaults_pass(self):
        router = CapabilityRouter()
        assert router.interpret_condition("unknown_format", set()) is True

    def test_interpret_condition_unrecognised_prefix_defaults_pass(self):
        router = CapabilityRouter()
        assert router.interpret_condition("x:something", set()) is True


class TestComplianceCheckerWithCapabilityRouter:
    def test_check_with_capabilities_all_pass(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has:encryption",
                action="require",
            )
        )
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-2",
                condition="all_of:audit_log,backup",
                action="require",
            )
        )

        router = CapabilityRouter({"encryption", "audit_log", "backup"})
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("repo-1", {"encryption", "audit_log", "backup"})
        assert report.status == "compliant"
        assert report.violations == []
        assert report.is_compliant is True

    def test_check_with_capabilities_violations(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has:encryption",
                action="require",
            )
        )
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-2",
                condition="missing:backdoor",
                action="require",
            )
        )

        router = CapabilityRouter()
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("repo-1", set())
        assert report.status == "non_compliant"
        assert "S-1" in report.violations
        assert "S-2" not in report.violations  # missing:backdoor passes when backdoor absent

    def test_check_with_capabilities_missing_capability(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="missing:pii_access",
                action="require",
            )
        )

        router = CapabilityRouter({"pii_access"})
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("repo-1", {"pii_access"})
        assert report.status == "non_compliant"
        assert "S-1" in report.violations

    def test_check_with_capabilities_no_router_raises(self):
        engine = PolicyEngine()
        checker = ComplianceChecker(engine)
        with pytest.raises(RuntimeError, match="CapabilityRouter"):
            checker.check_with_capabilities("repo-1", set())

    def test_check_with_capabilities_includes_audit_trail(self):
        engine = PolicyEngine()
        policy = Policy(
            name="Security",
            description="d",
            domain="security",
            level="e",
        )
        engine.register_policy(policy)
        engine.register_rule(
            Rule(
                policy_name="Security",
                rule_id="S-1",
                condition="has:encryption",
                action="require",
            )
        )

        router = CapabilityRouter({"encryption"})
        checker = ComplianceChecker(engine, capability_router=router)
        report = checker.check_with_capabilities("repo-1", {"encryption"})
        assert len(report.audit_trail) == 1
        assert isinstance(report.audit_trail[0], AuditTrail)
        assert "PASS" in report.audit_trail[0].details
