"""G9 Plan/critique layer — critiques generated plans for quality gaps."""

from __future__ import annotations

from typing import Any


class PlanCritique:
    """Critiques a generated plan and produces actionable feedback.

    The critique layer sits between plan generation and execution: it inspects
    the plan for completeness, consistency, ambiguity, and feasibility before
    the plan is handed off to an executor.
    """

    KNOWN_TOOLS = frozenset({"bash", "python", "ansible", "git", "docker", "curl", "apt"})

    def __init__(self) -> None:
        pass

    def critique_plan(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Critique *plan* and return a (possibly empty) list of findings."""
        findings: list[dict[str, Any]] = []
        findings.extend(self._check_completeness(plan))
        findings.extend(self._check_consistency(plan))
        findings.extend(self._check_ambiguity(plan))
        findings.extend(self._check_feasibility(plan))
        return findings

    def _finding(self, severity: str, field: str, message: str) -> dict[str, Any]:
        return {"severity": severity, "field": field, "message": message}

    def _check_completeness(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if not plan.get("title"):
            findings.append(self._finding("error", "title", "Plan is missing a title."))
        if not plan.get("description"):
            findings.append(self._finding("warning", "description", "Plan has no description — intent is unclear."))
        steps = plan.get("steps", [])
        if not isinstance(steps, list) or not steps:
            findings.append(self._finding("error", "steps", "Plan has no steps defined."))
        return findings

    def _check_consistency(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            return findings
        step_names = {s.get("name") for s in steps if isinstance(s, dict) and s.get("name")}
        dependencies = plan.get("dependencies", {}) or {}
        for dep_name in dependencies:
            if dep_name not in step_names:
                msg = f"Dependency source '{dep_name}' is not a defined step name."
                findings.append(self._finding("error", "dependencies", msg))
        return findings

    def _check_ambiguity(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            return findings
        for step in steps:
            if not isinstance(step, dict):
                continue
            desc = step.get("description", "")
            if isinstance(desc, str) and len(desc.strip()) < 10:
                name = step.get("name", "unnamed")
                msg = f"Step '{name}' has a vague description (< 10 characters)."
                findings.append(
                    self._finding("warning", f"steps.{name}.description", msg)
                )
        return findings

    def _check_feasibility(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            return findings
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = step.get("tool")
            if tool and tool not in self.KNOWN_TOOLS:
                name = step.get("name", "unnamed")
                findings.append(
                    self._finding("warning", f"steps.{name}.tool", f"Step '{name}' references unknown tool '{tool}'.")
                )
            resource = step.get("resource")
            if (resource and resource.startswith(("http://", "https://"))) or resource:
                pass
        return findings
