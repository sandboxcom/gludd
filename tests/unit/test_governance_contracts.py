"""Unit tests for governance contracts: Policy, Rule, ComplianceReport, AuditTrail."""

from __future__ import annotations

import datetime

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceReport,
    Policy,
    Rule,
)


class TestPolicy:
    def test_policy_creation_with_required_fields(self):
        policy = Policy(
            name="Data Retention",
            description="Controls data retention periods across the org.",
            domain="data-governance",
            level="enterprise",
        )
        assert policy.name == "Data Retention"
        assert policy.description == "Controls data retention periods across the org."
        assert policy.domain == "data-governance"
        assert policy.level == "enterprise"

    def test_policy_default_values(self):
        policy = Policy(
            name="Test Policy",
            description="A test policy.",
            domain="testing",
            level="project",
        )
        assert policy.status == "draft"
        assert policy.effective_date is None
        assert policy.rules == []

    def test_policy_with_optional_fields(self):
        policy = Policy(
            name="Security Standard",
            description="Defines enterprise security standards.",
            domain="security",
            level="enterprise",
            status="active",
            effective_date="2025-01-15",
            rules=["SEC-001", "SEC-002"],
        )
        assert policy.status == "active"
        assert policy.effective_date == "2025-01-15"
        assert policy.rules == ["SEC-001", "SEC-002"]

    def test_policy_equality_by_value(self):
        p1 = Policy(
            name="X",
            description="desc",
            domain="dom",
            level="lvl",
        )
        p2 = Policy(
            name="X",
            description="desc",
            domain="dom",
            level="lvl",
        )
        assert p1 == p2

    def test_policy_inequality_different_name(self):
        p1 = Policy(
            name="X",
            description="desc",
            domain="dom",
            level="lvl",
        )
        p2 = Policy(
            name="Y",
            description="desc",
            domain="dom",
            level="lvl",
        )
        assert p1 != p2

    def test_policy_hashable(self):
        p1 = Policy(
            name="X",
            description="desc",
            domain="dom",
            level="lvl",
        )
        p2 = Policy(
            name="X",
            description="desc",
            domain="dom",
            level="lvl",
        )
        s = {p1, p2}
        assert len(s) == 1


class TestRule:
    def test_rule_creation_with_required_fields(self):
        rule = Rule(
            policy_name="Data Retention",
            rule_id="RET-001",
            condition="retention_period_expired",
            action="archive",
        )
        assert rule.policy_name == "Data Retention"
        assert rule.rule_id == "RET-001"
        assert rule.condition == "retention_period_expired"
        assert rule.action == "archive"

    def test_rule_default_values(self):
        rule = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        assert rule.priority == 0
        assert rule.enforcement == "advisory"

    def test_rule_with_optional_fields(self):
        rule = Rule(
            policy_name="Security Standard",
            rule_id="SEC-001",
            condition="unpatched_vulnerability",
            action="block_deployment",
            priority=10,
            enforcement="mandatory",
        )
        assert rule.priority == 10
        assert rule.enforcement == "mandatory"

    def test_rule_equality_by_value(self):
        r1 = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        r2 = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        assert r1 == r2

    def test_rule_inequality_different_id(self):
        r1 = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        r2 = Rule(
            policy_name="P",
            rule_id="R2",
            condition="c",
            action="a",
        )
        assert r1 != r2

    def test_rule_hashable(self):
        r1 = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        r2 = Rule(
            policy_name="P",
            rule_id="R1",
            condition="c",
            action="a",
        )
        s = {r1, r2}
        assert len(s) == 1


class TestComplianceReport:
    def test_compliance_report_creation(self):
        report = ComplianceReport(
            subject="repository-1",
            policy_name="Data Retention",
            status="compliant",
        )
        assert report.subject == "repository-1"
        assert report.policy_name == "Data Retention"
        assert report.status == "compliant"

    def test_compliance_report_default_values(self):
        report = ComplianceReport(
            subject="s",
            policy_name="p",
            status="unknown",
        )
        assert report.violations == []
        assert report.created_at is not None
        assert isinstance(report.created_at, datetime.datetime)

    def test_compliance_report_with_violations(self):
        report = ComplianceReport(
            subject="repo-2",
            policy_name="Security Standard",
            status="non_compliant",
            violations=["SEC-001", "SEC-002"],
        )
        assert report.violations == ["SEC-001", "SEC-002"]
        assert report.status == "non_compliant"

    def test_compliance_report_is_compliant_property(self):
        compliant = ComplianceReport(
            subject="r",
            policy_name="p",
            status="compliant",
        )
        assert compliant.is_compliant is True

        non_compliant = ComplianceReport(
            subject="r",
            policy_name="p",
            status="non_compliant",
        )
        assert non_compliant.is_compliant is False

    def test_compliance_report_equality(self):
        ts = datetime.datetime(2025, 1, 1, 12, 0, 0)
        r1 = ComplianceReport(
            subject="s",
            policy_name="p",
            status="compliant",
            created_at=ts,
        )
        r2 = ComplianceReport(
            subject="s",
            policy_name="p",
            status="compliant",
            created_at=ts,
        )
        assert r1 == r2


class TestAuditTrail:
    def test_audit_trail_creation(self):
        trail = AuditTrail(
            entry_id="audit-001",
            subject="repo-1",
            action="compliance_check",
            details="Checked Data Retention policy. 3 rules passed.",
            timestamp="2025-06-01T10:00:00Z",
        )
        assert trail.entry_id == "audit-001"
        assert trail.subject == "repo-1"
        assert trail.action == "compliance_check"
        assert trail.details == "Checked Data Retention policy. 3 rules passed."
        assert trail.timestamp == "2025-06-01T10:00:00Z"

    def test_audit_trail_equality(self):
        a1 = AuditTrail(
            entry_id="a1",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        a2 = AuditTrail(
            entry_id="a1",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert a1 == a2

    def test_audit_trail_inequality(self):
        a1 = AuditTrail(
            entry_id="a1",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        a2 = AuditTrail(
            entry_id="a2",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        assert a1 != a2

    def test_audit_trail_hashable(self):
        a1 = AuditTrail(
            entry_id="a1",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        a2 = AuditTrail(
            entry_id="a1",
            subject="s",
            action="act",
            details="det",
            timestamp="2025-01-01T00:00:00Z",
        )
        s = {a1, a2}
        assert len(s) == 1
