"""Unit tests for G9: PlanCritique — plan/critique layer."""

from general_ludd.planning.critique import PlanCritique


class TestPlanCritique:
    def test_constructor(self) -> None:
        critique = PlanCritique()
        assert critique is not None

    def test_critique_plan_returns_list(self) -> None:
        critique = PlanCritique()
        plan = {"title": "Add login", "steps": ["create form", "add auth"]}
        findings = critique.critique_plan(plan)
        assert isinstance(findings, list)
        assert findings == []

    def test_critique_plan_empty_plan(self) -> None:
        critique = PlanCritique()
        findings = critique.critique_plan({})
        assert findings == []
