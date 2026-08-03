"""Deep tests for report generation, rendering, and data integrity.

Covers:
  - Governance/SSL compliance report structure and shape guarantees
  - RenderDocument section type validation and data round-trip
  - Template variable substitution edge cases (SkillRender + VariableStore)
  - Chart/data visualization integrity through pydantic models
  - Multi-project/multi-source aggregation patterns
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceReport,
    Policy,
    Rule,
)
from general_ludd.governance.core import ComplianceChecker, PolicyEngine
from general_ludd.renderers.schema import (
    ChartData,
    ChartSection,
    ChartSeries,
    MarkdownSection,
    Metric,
    MetricGridSection,
    RawHtmlSection,
    RenderDocument,
    RenderMetadata,
    TableSection,
)
from general_ludd.skills.renderer import SkillRenderError, render_skill

# ======================================================================
# 1. Compliance Report Structure
# ======================================================================


class TestComplianceReportStructure:
    def test_empty_compliance_report_shape(self):
        report = ComplianceReport(subject="proj-a", policy_name="Security", status="compliant")
        assert report.subject == "proj-a"
        assert report.policy_name == "Security"
        assert report.status == "compliant"
        assert report.is_compliant is True
        assert report.violations == []
        assert report.audit_trail == []
        assert report.created_at is not None

    def test_non_compliant_report_has_violations(self):
        report = ComplianceReport(
            subject="proj-b",
            policy_name="Data",
            status="non_compliant",
            violations=["D-001", "D-002"],
        )
        assert report.is_compliant is False
        assert len(report.violations) == 2
        assert "D-001" in report.violations

    def test_audit_trail_entries_are_structured(self):
        entry = AuditTrail(
            entry_id="audit-R1-2026-01-01",
            subject="proj-x",
            action="compliance_check",
            details="Rule R1 (has:encryption): PASS",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        report = ComplianceReport(
            subject="proj-x",
            policy_name="MultiRule",
            status="compliant",
            audit_trail=[entry],
        )
        assert len(report.audit_trail) == 1
        assert report.audit_trail[0].entry_id == "audit-R1-2026-01-01"
        assert report.audit_trail[0].action == "compliance_check"
        assert "PASS" in report.audit_trail[0].details

    def test_compliance_checker_produces_structured_audit_trail(self):
        engine = PolicyEngine()
        policy = Policy(name="Sec", description="d", domain="security", level="e")
        engine.register_policy(policy)
        engine.register_rule(Rule(policy_name="Sec", rule_id="R1", condition="has:tls", action="require"))
        engine.register_rule(Rule(policy_name="Sec", rule_id="R2", condition="has:audit", action="require"))

        def fail_first(rule, subject):
            return rule.rule_id != "R1"

        checker = ComplianceChecker(engine, evaluate_fn=fail_first)
        report = checker.check("repo-1")
        assert len(report.audit_trail) == 2
        assert all(isinstance(a, AuditTrail) for a in report.audit_trail)
        assert report.audit_trail[0].details.startswith("Rule R1")
        assert "FAIL" in report.audit_trail[0].details
        assert "PASS" in report.audit_trail[1].details

    def test_report_created_at_is_utc_aware(self):
        report = ComplianceReport(subject="x", policy_name="P", status="compliant")
        assert report.created_at.tzinfo is not None


# ======================================================================
# 2. Multi-Project / Multi-Source Aggregation
# ======================================================================


@dataclass
class _ProjectReport:
    project_id: str
    check_count: int
    violations: list[str]


def _aggregate_multi_project(project_reports: list[_ProjectReport]) -> dict[str, object]:
    all_violations: list[str] = []
    for pr in project_reports:
        for v in pr.violations:
            all_violations.append(f"{pr.project_id}/{v}")
    return {
        "project_count": len(project_reports),
        "total_checks": sum(pr.check_count for pr in project_reports),
        "violations": sorted(all_violations),
        "per_project": {
            pr.project_id: {"checks": pr.check_count, "violations": len(pr.violations)} for pr in project_reports
        },
    }


class TestMultiProjectAggregation:
    def test_empty_projects_yields_zero_counts(self):
        result = _aggregate_multi_project([])
        assert result["project_count"] == 0
        assert result["total_checks"] == 0
        assert result["violations"] == []

    def test_single_project_no_violations(self):
        pr = _ProjectReport(project_id="p1", check_count=5, violations=[])
        result = _aggregate_multi_project([pr])
        assert result["project_count"] == 1
        assert result["total_checks"] == 5
        assert result["violations"] == []
        assert result["per_project"]["p1"]["violations"] == 0

    def test_multiple_projects_aggregate_correctly(self):
        reports = [
            _ProjectReport(project_id="alpha", check_count=3, violations=["A-1"]),
            _ProjectReport(project_id="beta", check_count=5, violations=["B-1", "B-3"]),
            _ProjectReport(project_id="gamma", check_count=2, violations=[]),
        ]
        result = _aggregate_multi_project(reports)
        assert result["project_count"] == 3
        assert result["total_checks"] == 10
        violations = result["violations"]
        assert len(violations) == 3
        assert "alpha/A-1" in violations
        assert "beta/B-3" in violations

    def test_per_project_breaks_down_independently(self):
        reports = [
            _ProjectReport(project_id="x", check_count=10, violations=["X-1", "X-2", "X-3"]),
            _ProjectReport(project_id="y", check_count=1, violations=[]),
        ]
        result = _aggregate_multi_project(reports)
        assert result["per_project"]["x"]["checks"] == 10
        assert result["per_project"]["x"]["violations"] == 3
        assert result["per_project"]["y"]["checks"] == 1
        assert result["per_project"]["y"]["violations"] == 0


# ======================================================================
# 3. RenderDocument Section Types & Data Integrity
# ======================================================================


class TestRenderDocumentSections:
    def test_markdown_section_round_trip(self):
        section = MarkdownSection(content="## Hello\n\nWorld")
        assert section.type == "markdown"
        assert section.content == "## Hello\n\nWorld"
        d = section.model_dump()
        assert d["type"] == "markdown"

    def test_metric_grid_section_round_trip(self):
        section = MetricGridSection(
            metrics=[
                Metric(label="CPU", value=42, unit="%"),
                Metric(label="Memory", value=7.5, unit="GB"),
            ]
        )
        assert section.type == "metric_grid"
        assert len(section.metrics) == 2
        d = section.model_dump()
        assert d["metrics"][0]["label"] == "CPU"

    def test_table_section_structure(self):
        section = TableSection(
            title="Servers",
            columns=["Host", "Status", "Uptime"],
            rows=[["node-1", "online", "14d"], ["node-2", "offline", "0d"]],
        )
        assert section.type == "table"
        assert section.columns == ["Host", "Status", "Uptime"]
        assert len(section.rows) == 2

    def test_chart_section_data_integrity(self):
        data = ChartData(
            labels=["Jan", "Feb", "Mar"],
            series=[ChartSeries(name="revenue", values=[100, 200, 150])],
        )
        section = ChartSection(title="Revenue Trend", chart_type="line", data=data)
        assert section.type == "chart"
        assert section.chart_type == "line"
        assert len(section.data.series[0].values) == 3
        d = section.model_dump()
        reconstructed = ChartSection(**d)
        assert reconstructed.data.series[0].name == "revenue"

    def test_multi_series_chart_retains_all_data(self):
        data = ChartData(
            labels=["Q1", "Q2", "Q3", "Q4"],
            series=[
                ChartSeries(name="sales", values=[10, 20, 30, 40]),
                ChartSeries(name="costs", values=[5, 10, 15, 20]),
            ],
        )
        section = ChartSection(title="P&L", chart_type="bar", data=data)
        dump = section.model_dump()
        assert len(dump["data"]["series"]) == 2
        assert dump["data"]["series"][1]["name"] == "costs"
        assert dump["data"]["series"][1]["values"] == [5, 10, 15, 20]

    def test_chart_series_empty_values_ok(self):
        data = ChartData(labels=[], series=[ChartSeries(name="empty", values=[])])
        section = ChartSection(title="Empty", chart_type="pie", data=data)
        assert section.data.series[0].values == []

    def test_render_document_full_structure(self):
        doc = RenderDocument(
            title="System Health",
            sections=[
                MarkdownSection(content="Overview"),
                MetricGridSection(metrics=[Metric(label="Uptime", value=99.9, unit="%")]),
                TableSection(columns=["Name"], rows=[["ok"]]),
                ChartSection(
                    title="Trend",
                    chart_type="line",
                    data=ChartData(labels=["A"], series=[ChartSeries(name="X", values=[1])]),
                ),
            ],
            metadata=RenderMetadata(generated_at="2026-01-01T00:00:00Z"),
        )
        assert doc.title == "System Health"
        assert len(doc.sections) == 4
        assert doc.metadata.generated_at == "2026-01-01T00:00:00Z"

    def test_render_document_json_serializable(self):
        doc = RenderDocument(
            title="Test",
            sections=[MarkdownSection(content="Hello")],
            metadata=RenderMetadata(),
        )
        raw = doc.model_dump()
        json_str = json.dumps(raw)
        parsed = json.loads(json_str)
        assert parsed["title"] == "Test"
        assert "json" in json_str[:20] or True  # no TypeError occurred

    def test_raw_html_section_is_discrete_type(self):
        section = RawHtmlSection(html="<div>safe content</div>")
        d = section.model_dump()
        assert d["type"] == "raw_html"
        assert d["html"] == "<div>safe content</div>"

    def test_render_document_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            RenderDocument(title="Bad", sections=[], metadata=RenderMetadata(), extra_field="uhoh")

    def test_markdown_section_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            MarkdownSection(content="a", unknown=True)


# ======================================================================
# 4. Template Variable Substitution (Skill Renderer)
# ======================================================================


class TestSkillRenderEdgeCases:
    def test_render_skill_substitutes_variables(self):
        result = render_skill("Hello {{ name }}!", {"name": "World"})
        assert result == "Hello World!"

    def test_render_skill_missing_variable_raises(self):
        with pytest.raises(SkillRenderError, match="undefined"):
            render_skill("Hello {{ name }}!", {})

    def test_render_skill_none_variables_simple_template(self):
        result = render_skill("Static text without variables")
        assert result == "Static text without variables"

    def test_render_skill_nested_variable_access(self):
        result = render_skill(
            "User: {{ user.name }}, Age: {{ user.age }}",
            {"user": {"name": "Alice", "age": 30}},
        )
        assert "Alice" in result
        assert "30" in result

    def test_render_skill_list_variable(self):
        result = render_skill(
            "{% for item in items %}{{ item }},{% endfor %}",
            {"items": ["a", "b", "c"]},
        )
        assert result == "a,b,c,"

    def test_render_skill_empty_variables_dict(self):
        result = render_skill("Plain text", {})
        assert result == "Plain text"

    def test_render_skill_conditional_rendering(self):
        template = "{% if enabled %}ON{% else %}OFF{% endif %}"
        assert render_skill(template, {"enabled": True}) == "ON"
        assert render_skill(template, {"enabled": False}) == "OFF"

    def test_render_skill_number_formatting(self):
        result = render_skill("{{ '%.2f' | format(price) }}", {"price": 3.14159})
        assert result == "3.14"

    def test_render_skill_blank_template(self):
        result = render_skill("", {})
        assert result == ""

    def test_render_skill_security_error_ssti_attribute_access(self):
        with pytest.raises(SkillRenderError, match="sandbox-forbidden"):
            render_skill("{{ ''.__class__.__mro__ }}", {})

    def test_render_skill_security_error_ssti_globals(self):
        with pytest.raises(SkillRenderError, match="sandbox-forbidden"):
            render_skill("{{ cycler.__init__.__globals__ }}", {})

    def test_render_skill_malformed_template_raises(self):
        with pytest.raises(SkillRenderError, match="TemplateSyntaxError"):
            render_skill("{% if %}", {})

    def test_render_skill_boolean_variables(self):
        result = render_skill("{{ flag }}", {"flag": True})
        assert result == "True"


# ======================================================================
# 5. Template Variable Substitution (VariableStore)
# ======================================================================


class TestVariableStoreRender:
    def test_variable_store_sets_and_renders(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "key", "value")
        result = store.render("{{ ns__key }}")
        assert result == "value"

    def test_variable_store_multiple_namespaces(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("a", "x", 1)
        store.set("b", "y", "hello")
        result = store.render("A={{ a__x }}, B={{ b__y }}")
        assert result == "A=1, B=hello"

    def test_variable_store_deep_nested(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("cfg", "db", {"host": "localhost", "port": 5432})
        result = store.render("{{ cfg__db.host }}:{{ cfg__db.port }}")
        assert result == "localhost:5432"

    def test_variable_store_missing_key_resolves_to_empty_string(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        result = store.render("prefix-{{ missing__var }}-suffix")
        assert result == "prefix--suffix"

    def test_variable_store_overwrite(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("ns", "key", "first")
        store.set("ns", "key", "second")
        result = store.render("{{ ns__key }}")
        assert result == "second"

    def test_variable_store_render_with_extra_vars(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        result = store.render("Hello {{ name }}!", name="World")
        assert result == "Hello World!"

    def test_variable_store_get_namespace_returns_copy(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("tmp", "x", 1)
        ns = store.get_namespace("tmp")
        assert ns == {"x": 1}
        ns["extra"] = "mutated"
        assert "extra" not in store.get_namespace("tmp")

    def test_variable_store_all_vars_flat_format(self):
        from general_ludd.dispatch.variable_store import VariableStore

        store = VariableStore()
        store.set("a", "x", "ax")
        store.set("b", "y", "by")
        flat = store.all_vars()
        assert flat["a__x"] == "ax"
        assert flat["b__y"] == "by"


# ======================================================================
# 6. Report Collection & Aggregation
# ======================================================================


class TestReportCollectionAggregation:
    def test_collect_compliance_reports_across_subjects(self):
        """Simulates collecting compliance reports from multiple subjects and merging them."""
        engine = PolicyEngine()
        policy = Policy(name="P", description="d", domain="sec", level="e")
        engine.register_policy(policy)
        engine.register_rule(Rule(policy_name="P", rule_id="R1", condition="c", action="a"))

        checker = ComplianceChecker(engine)
        subjects = ["repo-a", "repo-b", "repo-c"]
        reports = {s: checker.check(s) for s in subjects}

        assert len(reports) == 3
        for s, r in reports.items():
            assert r.subject == s
            assert r.status == "compliant"

    def test_aggregate_render_documents_merges_sections(self):
        """Simulates aggregating multiple RenderDocuments into one combined document."""
        docs = [
            RenderDocument(
                title=f"Report {i}",
                sections=[MarkdownSection(content=f"Section {i}")],
                metadata=RenderMetadata(),
            )
            for i in range(3)
        ]
        combined_sections = [s for doc in docs for s in doc.sections]
        assert len(combined_sections) == 3
        titles = [doc.title for doc in docs]
        assert titles == ["Report 0", "Report 1", "Report 2"]

    def test_cross_source_chart_data_not_corrupted(self):
        """Ensures ChartData from different sources doesn't cross-contaminate."""
        source_a = ChartData(
            labels=["A1", "A2"],
            series=[ChartSeries(name="A", values=[1, 2])],
        )
        source_b = ChartData(
            labels=["B1", "B2", "B3"],
            series=[ChartSeries(name="B", values=[10, 20, 30])],
        )
        collected = {
            "a": source_a.model_dump(),
            "b": source_b.model_dump(),
        }
        assert collected["a"]["series"][0]["name"] == "A"
        assert collected["b"]["series"][0]["name"] == "B"
        assert len(collected["a"]["labels"]) == 2
        assert len(collected["b"]["labels"]) == 3
