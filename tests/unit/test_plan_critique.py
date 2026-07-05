"""Unit tests for G9: PlanCritique — plan/critique layer."""

from __future__ import annotations

from general_ludd.planning.critique import PlanCritique


class TestPlanCritique:
    def test_detects_missing_steps(self) -> None:
        critique = PlanCritique()
        plan = {"title": "Deploy app"}
        findings = critique.critique_plan(plan)
        assert any(
            f["field"] == "steps" and f["severity"] == "error"
            for f in findings
        )

    def test_detects_empty_title(self) -> None:
        critique = PlanCritique()
        plan = {"steps": [{"name": "setup", "description": "Install dependencies"}]}
        findings = critique.critique_plan(plan)
        assert any(
            f["field"] == "title" and f["severity"] == "error"
            for f in findings
        )

    def test_detects_missing_dependencies(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Build feature",
            "description": "Build a new feature end to end.",
            "steps": [
                {"name": "step1", "description": "First step", "tool": "bash", "resource": ""},
                {"name": "step2", "description": "Second step", "tool": "bash", "resource": ""},
            ],
            "dependencies": {"step3": ["step1"]},
        }
        findings = critique.critique_plan(plan)
        assert any(
            f["field"] == "dependencies" and f["severity"] == "error"
            for f in findings
        )
        assert "step3" in str(findings)

    def test_detects_vague_description(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Do thing",
            "description": "A test plan.",
            "steps": [
                {"name": "quick", "description": "Do.", "tool": "bash", "resource": ""},
            ],
        }
        findings = critique.critique_plan(plan)
        assert any(
            f["field"] == "steps.quick.description" and f["severity"] == "warning"
            for f in findings
        )

    def test_no_findings_on_good_plan(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Full plan",
            "description": "A well-described plan with clear steps.",
            "steps": [
                {
                    "name": "setup_env",
                    "description": "Install all required Python dependencies.",
                    "tool": "bash",
                    "resource": "",
                },
                {
                    "name": "run_tests",
                    "description": "Execute the full test suite with coverage.",
                    "tool": "bash",
                    "resource": "",
                },
            ],
            "dependencies": {"run_tests": ["setup_env"]},
        }
        findings = critique.critique_plan(plan)
        assert findings == []

    def test_severity_levels(self) -> None:
        critique = PlanCritique()
        plan = {}  # empty plan triggers multiple issues
        findings = critique.critique_plan(plan)
        severities = {f["severity"] for f in findings}
        assert "error" in severities
        for f in findings:
            assert f["severity"] in {"error", "warning", "info"}
            assert "field" in f
            assert "message" in f
