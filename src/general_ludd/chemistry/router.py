"""Chemistry router: map task type → workflow, with risk classification first.

Implements CHEM-001 (Expert router) from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §2 and §3.

The router classifies risk *before* detailed workflow generation (spec §9:
"Risk classification occurs before detailed workflow generation and again
after identity resolution") and signals whether ``hazard_review`` must run
before any actionable artifact is returned (spec §3: "High-risk tasks
always compose hazard_review before protocol_draft, quantum_workflow,
molecular_simulation, or process_scaleup can return an actionable artifact").

The router does NOT execute the workflow — it returns a
:class:`WorkflowRoute` describing which capability owns the task and what
safety gates apply. Execution is the responsibility of
:class:`general_ludd.chemistry.api.ChemistryExpertAPI`.
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.chemistry.policy import ChemistryPolicy
from general_ludd.chemistry.schemas import ChemistryRequest, TaskKind

# Task kind → owning capability (spec §3 role table, §14 file plan).
TASK_WORKFLOW: dict[TaskKind, str] = {
    TaskKind.identity: "identity_resolve",
    TaskKind.research: "chemistry_research",
    TaskKind.property: "property_lookup",
    TaskKind.reaction: "reaction_analyze",
    TaskKind.protocol: "protocol_draft",
    TaskKind.stoichiometry: "stoichiometry",
    TaskKind.hazard: "hazard_review",
    TaskKind.inventory: "inventory_check",
    TaskKind.compute: "quantum_workflow",
    TaskKind.spectra: "spectra_analyze",
    TaskKind.analytical: "analytical_validate",
    TaskKind.electrochemistry: "electrochemistry",
    TaskKind.process: "process_scaleup",
}

# Capabilities that produce actionable artifacts and therefore require a
# preceding hazard_review (spec §3).
_HAZARD_GATED_WORKFLOWS: frozenset[str] = frozenset(
    {
        "protocol_draft",
        "quantum_workflow",
        "molecular_simulation",
        "process_scaleup",
    }
)

_TIER_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}


@dataclass
class WorkflowRoute:
    """Result of routing a request to its workflow.

    ``requires_hazard_review`` is ``True`` when spec §3 mandates a
    hazard_review composition before an actionable artifact may be returned.
    """

    request_id: str
    workflow: str
    risk_tier: str
    requires_hazard_review: bool


class ChemistryRouter:
    """Map a :class:`ChemistryRequest` to its workflow + risk classification.

    The router delegates risk classification to
    :class:`ChemistryPolicy.classify_risk` so the policy and router agree on
    a single tier per request.
    """

    def __init__(self, policy: ChemistryPolicy) -> None:
        self._policy = policy

    def route(self, request: ChemistryRequest) -> WorkflowRoute:
        workflow = TASK_WORKFLOW.get(request.task, "")
        if not workflow:
            return WorkflowRoute(
                request_id=request.request_id,
                workflow="",
                risk_tier="low",
                requires_hazard_review=False,
            )

        risk_tier = self._policy.classify_risk(request)
        rank = _TIER_RANK.get(risk_tier, 0)

        # Spec §3: hazard-gated workflows require a hazard_review when the
        # risk is moderate or worse. Any high/prohibited tier also forces
        # a hazard_review regardless of workflow.
        requires_hazard = (
            workflow in _HAZARD_GATED_WORKFLOWS and rank >= _TIER_RANK["moderate"]
        ) or rank >= _TIER_RANK["high"]

        return WorkflowRoute(
            request_id=request.request_id,
            workflow=workflow,
            risk_tier=risk_tier,
            requires_hazard_review=requires_hazard,
        )


__all__ = [
    "TASK_WORKFLOW",
    "ChemistryRouter",
    "WorkflowRoute",
]
