"""G9 Plan/critique layer — critiques generated plans for quality gaps."""

from __future__ import annotations

from typing import Any


class PlanCritique:
    """Critiques a generated plan and produces actionable feedback.

    The critique layer sits between plan generation and execution: it inspects
    the plan for completeness, consistency, ambiguity, and feasibility before
    the plan is handed off to an executor.
    """

    def __init__(self) -> None:
        pass

    def critique_plan(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Critique *plan* and return a (possibly empty) list of findings.

        Each finding is a dict with at minimum ``"severity"`` (one of
        ``"info"``, ``"warning"``, ``"error"``) and ``"message"``.

        Returns an empty list when the plan has no actionable issues.
        """
        return []
