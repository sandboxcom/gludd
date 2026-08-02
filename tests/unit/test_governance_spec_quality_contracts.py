"""Unit tests for spec quality audit contracts."""

from __future__ import annotations

import datetime as dt

from general_ludd.governance.spec_quality_contracts import (
    AuditFinding,
    AuditReport,
    AuditRule,
    RuleRegistry,
    SpecAuditor,
)


class TestAuditRule:
    def test_construction_defaults(self) -> None:
        rule = AuditRule(
            rule_id="R001",
            name="Enforcement Present",
            description="Checks that Enforcement field is present",
            category="enforcement_present",
        )
        assert rule.rule_id == "R001"
        assert rule.name == "Enforcement Present"
        assert rule.category == "enforcement_present"
        assert rule.severity == "error"
        assert rule.check_fn == ""
        assert rule.active is True

    def test_custom_severity(self) -> None:
        rule = AuditRule(
            rule_id="R002",
            name="Warning Rule",
            description="A rule with warning severity",
            category="enforcement_present",
            severity="warning",
        )
        assert rule.severity == "warning"

    def test_inactive_rule(self) -> None:
        rule = AuditRule(
            rule_id="R003",
            name="Inactive Rule",
            description="A deactivated rule",
            category="enforcement_present",
            active=False,
        )
        assert rule.active is False

    def test_with_check_fn(self) -> None:
        rule = AuditRule(
            rule_id="R004",
            name="Concrete Enforcement",
            description="Checks enforcement is concrete",
            category="enforcement_concrete",
            check_fn="scripts/check_spec_quality_ratio.py",
        )
        assert rule.check_fn == "scripts/check_spec_quality_ratio.py"

    def test_equality(self) -> None:
        r1 = AuditRule(rule_id="R001", name="A", description="D", category="c")
        r2 = AuditRule(rule_id="R001", name="A", description="D", category="c")
        r3 = AuditRule(rule_id="R002", name="A", description="D", category="c")
        assert r1 == r2
        assert r1 != r3

    def test_hashable(self) -> None:
        rule = AuditRule(rule_id="R001", name="A", description="D", category="c")
        assert hash(rule) == hash(rule)
        used_as_key = {rule: "value"}
        assert used_as_key[rule] == "value"


class TestAuditFinding:
    def test_construction(self) -> None:
        finding = AuditFinding(
            rule_id="R001",
            spec_id="AB001",
            severity="error",
            message="Missing enforcement",
            evidence="**Behavior:** something",
            line=42,
        )
        assert finding.rule_id == "R001"
        assert finding.spec_id == "AB001"
        assert finding.severity == "error"
        assert finding.message == "Missing enforcement"
        assert finding.evidence == "**Behavior:** something"
        assert finding.line == 42

    def test_default_evidence_and_line(self) -> None:
        finding = AuditFinding(
            rule_id="R001",
            spec_id="AB001",
            severity="warning",
            message="Advisory language",
        )
        assert finding.evidence == ""
        assert finding.line == 0

    def test_equality(self) -> None:
        f1 = AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="m")
        f2 = AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="m")
        f3 = AuditFinding(rule_id="R001", spec_id="AB002", severity="error", message="m")
        assert f1 == f2
        assert f1 != f3


class TestAuditReport:
    def test_empty_report(self) -> None:
        report = AuditReport()
        assert report.total_findings == 0
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.info_count == 0
        assert report.unique_specs_checked == 0
        assert report.unique_rules_fired == 0
        assert report.rules_applied == []
        assert isinstance(report.timestamp, dt.datetime)

    def test_report_with_findings(self) -> None:
        findings = [
            AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="m1"),
            AuditFinding(rule_id="R001", spec_id="AB001", severity="warning", message="m2"),
            AuditFinding(rule_id="R002", spec_id="AB002", severity="error", message="m3"),
            AuditFinding(rule_id="R002", spec_id="AB002", severity="info", message="m4"),
        ]
        report = AuditReport(
            findings=findings,
            rules_applied=["R001", "R002"],
        )
        assert report.total_findings == 4
        assert report.error_count == 2
        assert report.warning_count == 1
        assert report.info_count == 1
        assert report.unique_specs_checked == 2
        assert report.unique_rules_fired == 2
        assert report.has_errors()

    def test_no_errors(self) -> None:
        findings = [
            AuditFinding(rule_id="R001", spec_id="AB001", severity="warning", message="m"),
        ]
        report = AuditReport(findings=findings)
        assert not report.has_errors()

    def test_findings_by_severity(self) -> None:
        f1 = AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="e")
        f2 = AuditFinding(rule_id="R002", spec_id="AB001", severity="warning", message="w")
        report = AuditReport(findings=[f1, f2])
        errors = report.findings_by_severity("error")
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_findings_by_rule(self) -> None:
        f1 = AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="e")
        f2 = AuditFinding(rule_id="R002", spec_id="AB001", severity="error", message="e")
        report = AuditReport(findings=[f1, f2])
        r1_findings = report.findings_by_rule("R001")
        assert len(r1_findings) == 1
        assert r1_findings[0].rule_id == "R001"

    def test_findings_by_spec(self) -> None:
        f1 = AuditFinding(rule_id="R001", spec_id="AB001", severity="error", message="e")
        f2 = AuditFinding(rule_id="R002", spec_id="AB002", severity="error", message="e")
        report = AuditReport(findings=[f1, f2])
        s1 = report.findings_by_spec("AB001")
        assert len(s1) == 1
        assert s1[0].spec_id == "AB001"

    def test_custom_timestamp(self) -> None:
        ts = dt.datetime(2026, 1, 15, 12, 0, 0, tzinfo=dt.UTC)
        report = AuditReport(timestamp=ts)
        assert report.timestamp == ts


class TestRuleRegistry:
    def test_add_and_get_rule(self) -> None:
        registry = RuleRegistry()
        rule = AuditRule(rule_id="R001", name="Test", description="D", category="c")
        registry.add_rule(rule)
        retrieved = registry.get_rule("R001")
        assert retrieved is not None
        assert retrieved.rule_id == "R001"

    def test_get_nonexistent_rule(self) -> None:
        registry = RuleRegistry()
        assert registry.get_rule("nonexistent") is None

    def test_add_duplicate_raises(self) -> None:
        registry = RuleRegistry()
        rule = AuditRule(rule_id="R001", name="Test", description="D", category="c")
        registry.add_rule(rule)
        try:
            registry.add_rule(rule)
        except ValueError as e:
            assert "already exists" in str(e)

    def test_remove_rule(self) -> None:
        registry = RuleRegistry()
        rule = AuditRule(rule_id="R001", name="Test", description="D", category="c")
        registry.add_rule(rule)
        assert registry.remove_rule("R001") is True
        assert registry.get_rule("R001") is None
        assert registry.remove_rule("R001") is False

    def test_list_rules_all(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c1"))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="c2"))
        assert len(registry.list_rules()) == 2

    def test_list_rules_filter_category(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="cat_a"))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="cat_b"))
        result = registry.list_rules(category="cat_a")
        assert len(result) == 1
        assert result[0].rule_id == "R001"

    def test_list_rules_filter_severity(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c", severity="error"))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="c", severity="warning"))
        result = registry.list_rules(severity="warning")
        assert len(result) == 1
        assert result[0].rule_id == "R002"

    def test_list_rules_excludes_inactive(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c", active=True))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="c", active=False))
        result = registry.list_rules()
        assert len(result) == 1
        assert result[0].rule_id == "R001"

    def test_list_rules_include_inactive(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c", active=False))
        result = registry.list_rules(active_only=False)
        assert len(result) == 1

    def test_len(self) -> None:
        registry = RuleRegistry()
        assert len(registry) == 0
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c"))
        assert len(registry) == 1

    def test_iter(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c"))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="c"))
        ids = {r.rule_id for r in registry}
        assert ids == {"R001", "R002"}

    def test_contains(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c"))
        assert "R001" in registry
        assert "R002" not in registry


class TestSpecAuditor:
    def _make_entry(self, spec_id: str, body: str) -> dict[str, object]:
        return {"spec_id": spec_id, "body": body}

    def _registry_with_rules(self, *rules: AuditRule) -> RuleRegistry:
        registry = RuleRegistry()
        for rule in rules:
            registry.add_rule(rule)
        return registry

    def test_empty_entries_no_findings(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R001", name="Enforcement Present", description="D", category="enforcement_present"),
        )
        auditor = SpecAuditor(registry)
        report = auditor.audit([])
        assert report.total_findings == 0

    def test_enforcement_present_passes(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R001", name="Enforcement Present", description="D", category="enforcement_present"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** `make lint`")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_enforcement_missing_flags(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R001", name="Enforcement Present", description="D", category="enforcement_present"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Behavior:** something")
        report = auditor.audit([entry])
        assert report.total_findings == 1
        assert report.findings[0].rule_id == "R001"
        assert report.findings[0].severity == "error"

    def test_enforcement_concrete_passes(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R002", name="Concrete Enforcement", description="D", category="enforcement_concrete"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** `make lint`")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_enforcement_not_concrete_flags(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R002", name="Concrete Enforcement", description="D", category="enforcement_concrete"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** manual review only")
        report = auditor.audit([entry])
        assert report.total_findings == 1
        assert "does not reference concrete mechanism" in report.findings[0].message

    def test_body_non_empty_passes(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R003", name="Body Non-Empty", description="D", category="body_non_empty"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "Some content here")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_empty_body_flags(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R003", name="Body Non-Empty", description="D", category="body_non_empty"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "   ")
        report = auditor.audit([entry])
        assert report.total_findings == 1
        assert "empty body" in report.findings[0].message.lower()

    def test_placeholder_enforcement_flags(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(
                rule_id="R004",
                name="No Placeholder Enforcement",
                description="D",
                category="no_placeholder_enforcement",
            ),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** TBD")
        report = auditor.audit([entry])
        assert report.total_findings == 1
        assert "placeholder" in report.findings[0].message

    def test_placeholder_enforcement_passes_clean(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(
                rule_id="R004",
                name="No Placeholder Enforcement",
                description="D",
                category="no_placeholder_enforcement",
            ),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** `make test`")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_behavior_measurable_passes(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R005", name="Behavior Measurable", description="D", category="behavior_measurable"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Behavior:** The tool blocks the request")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_behavior_advisory_flags(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(
                rule_id="R005",
                name="Behavior Measurable",
                description="D",
                category="behavior_measurable",
                severity="warning",
            ),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Behavior:** Advisory suggestion to block")
        report = auditor.audit([entry])
        assert report.total_findings == 1
        assert "advisory" in report.findings[0].message.lower()

    def test_multiple_entries_multiple_findings(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R001", name="Enforcement Present", description="D", category="enforcement_present"),
        )
        auditor = SpecAuditor(registry)
        entries = [
            self._make_entry("AB001", "**Behavior:** x"),
            self._make_entry("AB002", "**Behavior:** y"),
        ]
        report = auditor.audit(entries)
        assert report.total_findings == 2
        assert report.unique_specs_checked == 2

    def test_inactive_rule_not_applied(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(
                rule_id="R001",
                name="Enforcement Present",
                description="D",
                category="enforcement_present",
                active=False,
            ),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Behavior:** x")
        report = auditor.audit([entry])
        assert report.total_findings == 0

    def test_rules_applied_recorded(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R001", name="A", description="D", category="enforcement_present"),
            AuditRule(rule_id="R002", name="B", description="D", category="body_non_empty"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "**Enforcement:** `make lint`")
        report = auditor.audit([entry])
        assert report.rules_applied == ["R001", "R002"]

    def test_spec_auditor_rules_property(self) -> None:
        registry = RuleRegistry()
        registry.add_rule(AuditRule(rule_id="R001", name="A", description="D", category="c"))
        registry.add_rule(AuditRule(rule_id="R002", name="B", description="D", category="c", active=False))
        auditor = SpecAuditor(registry)
        assert len(auditor.rules) == 1
        assert auditor.rules[0].rule_id == "R001"

    def test_unknown_rule_category_noop(self) -> None:
        registry = self._registry_with_rules(
            AuditRule(rule_id="R999", name="Unknown", description="D", category="nonexistent_category"),
        )
        auditor = SpecAuditor(registry)
        entry = self._make_entry("AB001", "anything")
        report = auditor.audit([entry])
        assert report.total_findings == 0
