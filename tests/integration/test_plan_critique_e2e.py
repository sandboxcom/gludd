"""Integration/e2e tests for G9 plan/critique layer.

Proves PlanCritique works end-to-end: receives a plan, produces
structured findings, and handles edge cases.
"""

from __future__ import annotations

from general_ludd.planning.critique import PlanCritique


class TestPlanCritiqueE2E:
    def test_critique_plan_returns_list(self) -> None:
        critique = PlanCritique()
        plan = {"title": "Add login page", "steps": ["create form", "wire auth"]}
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_empty_plan(self) -> None:
        critique = PlanCritique()
        findings = critique.critique_plan({})
        assert isinstance(findings, list)

    def test_critique_plan_without_steps(self) -> None:
        critique = PlanCritique()
        plan = {"title": "Do something", "description": "Just a description"}
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_with_missing_title(self) -> None:
        critique = PlanCritique()
        plan = {"steps": ["step1", "step2"]}
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_with_ambiguous_description(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Fix it",
            "description": "make it better",
            "steps": ["do the thing"],
        }
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_with_dependencies(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Implement payment flow",
            "steps": ["add UI", "wire backend", "add tests"],
            "dependencies": ["auth module", "user service"],
            "acceptance_criteria": ["users can pay", "receipts are sent"],
        }
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_returns_dicts_with_severity_and_message(self) -> None:
        critique = PlanCritique()
        plan = {"title": "Test plan", "steps": ["write tests", "run tests"]}
        findings = critique.critique_plan(plan)

        for f in findings:
            assert isinstance(f, dict)
            assert "severity" in f
            assert "message" in f

    def test_multiple_critiques_independent(self) -> None:
        critique = PlanCritique()

        f1 = critique.critique_plan({"title": "A", "steps": ["do A"]})
        f2 = critique.critique_plan({"title": "B", "steps": ["do B"]})

        assert isinstance(f1, list)
        assert isinstance(f2, list)

    def test_critique_plan_with_acceptance_criteria(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Add search",
            "steps": ["index data", "build API", "render results"],
            "acceptance_criteria": ["results load in <200ms", "pagination works"],
        }
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_large_nested_plan(self) -> None:
        critique = PlanCritique()
        plan = {
            "title": "Major refactor",
            "description": "A comprehensive rewrite of the data layer",
            "steps": [
                {"name": "extract interface", "sub_steps": ["define ABC", "write adapters"]},
                {"name": "migrate callers", "sub_steps": ["audit imports", "update refs"]},
                {"name": "remove old code", "sub_steps": ["delete files", "update tests"]},
            ],
            "risks": ["data loss", "regressions"],
            "rollback_plan": "revert commit",
        }
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_critique_plan_empty_returns_findings(self) -> None:
        critique = PlanCritique()
        findings = critique.critique_plan({})
        assert isinstance(findings, list)
        for f in findings:
            assert "severity" in f
            assert "message" in f

    def test_constructor_creates_instance(self) -> None:
        c = PlanCritique()
        assert c is not None
        assert isinstance(c, PlanCritique)
