"""Unit tests for planning/critique.py — plan quality critique layer."""

from __future__ import annotations

from general_ludd.planning.critique import PlanCritique


class TestPlanCritique:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_known_tools_is_frozenset(self) -> None:
        assert isinstance(PlanCritique.KNOWN_TOOLS, frozenset)

    def test_known_tools_includes_bash(self) -> None:
        assert "bash" in PlanCritique.KNOWN_TOOLS

    def test_known_tools_includes_ansible(self) -> None:
        assert "ansible" in PlanCritique.KNOWN_TOOLS

    def test_known_tools_includes_python(self) -> None:
        assert "python" in PlanCritique.KNOWN_TOOLS


class TestCritiquePlanCompleteness:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_valid_plan_passes(self) -> None:
        plan = {
            "title": "Deploy app",
            "description": "Deploy the application to production",
            "steps": [{"name": "build", "description": "Build the docker image"}],
        }
        findings = self.critique._check_completeness(plan)
        assert len(findings) == 0

    def test_missing_title_flagged(self) -> None:
        plan = {"description": "desc", "steps": [{"name": "step1"}]}
        findings = self.critique._check_completeness(plan)
        titles = [f for f in findings if f["field"] == "title"]
        assert len(titles) >= 1
        assert titles[0]["severity"] == "error"

    def test_missing_description_flagged(self) -> None:
        plan = {"title": "Plan", "steps": [{"name": "step1"}]}
        findings = self.critique._check_completeness(plan)
        descs = [f for f in findings if f["field"] == "description"]
        assert len(descs) >= 1
        assert descs[0]["severity"] == "warning"

    def test_empty_steps_flagged(self) -> None:
        plan = {"title": "Plan", "description": "desc", "steps": []}
        findings = self.critique._check_completeness(plan)
        step_findings = [f for f in findings if f["field"] == "steps"]
        assert len(step_findings) >= 1

    def test_missing_steps_flagged(self) -> None:
        plan = {"title": "Plan", "description": "desc"}
        findings = self.critique._check_completeness(plan)
        step_findings = [f for f in findings if f["field"] == "steps"]
        assert len(step_findings) >= 1
        assert step_findings[0]["severity"] == "error"


class TestCritiquePlanConsistency:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_dangling_dependency_flagged(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": [{"name": "build"}],
            "dependencies": {"deploy": ["build"]},
        }
        findings = self.critique._check_consistency(plan)
        dep_findings = [f for f in findings if f["field"] == "dependencies"]
        assert len(dep_findings) >= 1

    def test_valid_dependency_passes(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": [{"name": "build"}, {"name": "deploy"}],
            "dependencies": {"deploy": ["build"]},
        }
        findings = self.critique._check_consistency(plan)
        dep_findings = [f for f in findings if f["field"] == "dependencies"]
        assert len(dep_findings) == 0

    def test_non_list_steps_handled_gracefully(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": "not_a_list",
            "dependencies": {"x": ["y"]},
        }
        findings = self.critique._check_consistency(plan)
        assert isinstance(findings, list)


class TestCritiquePlanAmbiguity:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_vague_description_flagged(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": [
                {"name": "step1", "description": "do it"},
            ],
        }
        findings = self.critique._check_ambiguity(plan)
        assert len(findings) >= 1
        assert "warning" in findings[0]["severity"]

    def test_adequate_description_passes(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": [
                {"name": "step1", "description": "Build the docker image using the provided Dockerfile"},
            ],
        }
        findings = self.critique._check_ambiguity(plan)
        assert len(findings) == 0

    def test_non_list_steps_handled(self) -> None:
        plan = {"title": "Plan", "steps": "not_a_list"}
        findings = self.critique._check_ambiguity(plan)
        assert isinstance(findings, list)

    def test_non_dict_step_skipped(self) -> None:
        plan = {
            "title": "Plan",
            "description": "desc",
            "steps": ["not_a_dict"],
        }
        findings = self.critique._check_ambiguity(plan)
        assert isinstance(findings, list)


class TestCritiquePlanFeasibility:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_unknown_tool_flagged(self) -> None:
        plan = {
            "title": "Plan",
            "steps": [
                {"name": "step1", "tool": "magic_wand"},
            ],
        }
        findings = self.critique._check_feasibility(plan)
        tool_findings = [
            f for f in findings if "unknown tool" in f["message"].lower()
        ]
        assert len(tool_findings) >= 1

    def test_known_tool_passes(self) -> None:
        plan = {
            "title": "Plan",
            "steps": [
                {"name": "step1", "tool": "bash"},
            ],
        }
        findings = self.critique._check_feasibility(plan)
        tool_findings = [
            f for f in findings if "unknown tool" in f["message"].lower()
        ]
        assert len(tool_findings) == 0

    def test_all_known_tools_pass(self) -> None:
        for tool in PlanCritique.KNOWN_TOOLS:
            plan = {
                "title": "Plan",
                "steps": [{"name": "step1", "tool": tool}],
            }
            findings = self.critique._check_feasibility(plan)
            tool_findings = [f for f in findings if "unknown tool" in f["message"].lower()]
            assert len(tool_findings) == 0, f"Tool {tool} was flagged as unknown"

    def test_non_list_steps_handled(self) -> None:
        plan = {"title": "Plan", "steps": None}
        findings = self.critique._check_feasibility(plan)
        assert isinstance(findings, list)


class TestCritiquePlanFull:
    def setup_method(self) -> None:
        self.critique = PlanCritique()

    def test_critique_plan_aggregates_all_checks(self) -> None:
        plan = {
            "title": "Deploy",
            "description": "Deploy to production with canary rollout",
            "steps": [
                {"name": "build", "tool": "docker", "description": "Build the image"},
                {"name": "deploy", "tool": "ansible", "description": "Deploy the built image"},
            ],
            "dependencies": {"deploy": ["build"]},
        }
        findings = self.critique.critique_plan(plan)
        assert isinstance(findings, list)

    def test_empty_plan_triggers_multiple_findings(self) -> None:
        plan: dict = {}
        findings = self.critique.critique_plan(plan)
        assert len(findings) >= 2

    def test_finding_structure(self) -> None:
        plan = {"title": "T", "steps": []}
        findings = self.critique.critique_plan(plan)
        for f in findings:
            assert "severity" in f
            assert "field" in f
            assert "message" in f
            assert f["severity"] in {"error", "warning"}
