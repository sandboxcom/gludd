"""Unit tests for governance core: PolicyEngine, ComplianceChecker."""

from __future__ import annotations

import pytest

from general_ludd.governance.contracts import (
    AuditTrail,
    Policy,
    Rule,
)
from general_ludd.governance.core import ComplianceChecker, PolicyEngine


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
