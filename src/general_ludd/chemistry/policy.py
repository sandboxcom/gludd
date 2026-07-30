"""Chemistry policy: constraint validation, mutation audit gate, risk tier.

Implements the policy surface referenced throughout
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §9 (safety table) and §4.2
(constraints). The policy is consulted by :mod:`general_ludd.chemistry.api`
before any actionable artifact is produced.

Three responsibilities:

* :meth:`ChemistryPolicy.check_request` — validate request-level constraints
  (deadline, budget, data classification, approval token for mutation tasks).
  Returns a :class:`PolicyDecision` with a unique ``decision_id`` so every
  refusal is auditable.
* :meth:`ChemistryPolicy.check_mutation` — fail closed when the audit/policy
  service is unavailable for a mutation task (spec §9 row "Audit/policy
  service unavailable | Fail closed for mutation and execution-facing
  export").
* :meth:`ChemistryPolicy.classify_risk` — return the worst-case risk tier
  (low / moderate / high / prohibited) across all entities, using the hazard
  registry from :mod:`general_ludd.chemistry.core`. Unknown entities resolve
  to ``moderate`` with a ``missing-current-hazard-evidence`` limitation
  (spec §9), never a silent ``low``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from general_ludd.chemistry.schemas import (
    ChemistryRequest,
    DataClassification,
    TaskKind,
)

# Tasks that produce actionable artifacts (spec §8.1 protocol export, §7.3
# quantum executable jobs, §7.5 process scale-up). These require (a) a
# non-empty approval_token and (b) the audit/policy service to be available.
MUTATION_TASKS: frozenset[TaskKind] = frozenset(
    {
        TaskKind.protocol,
        TaskKind.compute,
        TaskKind.process,
    }
)

_TIER_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}
_RANK_TIER: dict[int, str] = {v: k for k, v in _TIER_RANK.items()}


@dataclass
class PolicyDecision:
    """Typed result of a policy check.

    ``fail_closed`` is ``True`` when the denial is due to an unavailable
    safety service (audit/policy store down) rather than a constraint
    violation — the caller MUST NOT proceed and MUST NOT silently retry.
    """

    decision_id: str
    allowed: bool
    reason: str | None = None
    risk_tier: str = "low"
    fail_closed: bool = False


def _new_decision_id() -> str:
    return f"pol-{uuid.uuid4().hex[:16]}"


class ChemistryPolicy:
    """Validate request constraints, gate mutations, classify risk.

    The policy never silently relaxes a constraint: when a check fails the
    decision carries a human-stable ``reason`` and a unique ``decision_id``
    for audit correlation.
    """

    def check_request(self, request: ChemistryRequest) -> PolicyDecision:
        """Validate request-level constraints (spec §4.2, §9).

        Returns a :class:`PolicyDecision` with ``allowed=False`` and a
        descriptive ``reason`` when any constraint is violated.
        """
        token = (request.approval_token or "").strip()
        constraints = request.constraints

        # Mutation tasks require a non-empty approval token (spec §8.1, §11).
        if request.task in MUTATION_TASKS and not token:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=False,
                reason=(f"task {request.task.value!r} is a mutation task and requires a non-empty approval_token"),
            )

        # A zero/negative deadline is unsatisfiable for mutation tasks.
        if request.task in MUTATION_TASKS and constraints.deadline_s <= 0:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=False,
                reason="deadline_s must be > 0 for mutation tasks",
            )

        # Restricted data requires an approval token regardless of task kind
        # (spec §9: secrets/regulated records are tenant-isolated and gated).
        if constraints.data_classification is DataClassification.restricted and not token:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=False,
                reason=("restricted data_classification requires a non-empty approval_token"),
            )

        return PolicyDecision(
            decision_id=_new_decision_id(),
            allowed=True,
            reason=None,
            risk_tier=self.classify_risk(request),
        )

    def check_mutation(
        self,
        request: ChemistryRequest,
        *,
        audit_available: bool,
    ) -> PolicyDecision:
        """Gate mutation tasks on audit-service availability (spec §9).

        Spec §9 row "Audit/policy service unavailable | Fail closed for
        mutation and execution-facing export." When ``audit_available`` is
        ``False`` and the task is a mutation, the decision is ``allowed=False``
        with ``fail_closed=True``.
        """
        if request.task not in MUTATION_TASKS:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=True,
                reason=None,
                risk_tier=self.classify_risk(request),
            )

        if not audit_available:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=False,
                reason=("audit/policy service unavailable: mutation blocked (fail-closed per spec §9)"),
                fail_closed=True,
            )

        token = (request.approval_token or "").strip()
        if not token:
            return PolicyDecision(
                decision_id=_new_decision_id(),
                allowed=False,
                reason=(f"mutation task {request.task.value!r} requires a non-empty approval_token"),
            )

        return PolicyDecision(
            decision_id=_new_decision_id(),
            allowed=True,
            reason=None,
            risk_tier=self.classify_risk(request),
        )

    def classify_risk(self, request: ChemistryRequest) -> str:
        """Return the worst-case risk tier across all entities (CHEM-008).

        Delegates to :func:`general_ludd.chemistry.core.screen_hazards` per
        entity. Unknown entities resolve to ``moderate`` (never ``low``)
        per spec §9 row "Missing current hazard or incompatibility evidence".
        """
        from general_ludd.chemistry.core import screen_hazards

        worst = 0  # _TIER_RANK["low"]
        for entity in request.entities:
            screen = screen_hazards(entity)
            worst = max(worst, _TIER_RANK.get(screen["risk_tier"], 1))
        return _RANK_TIER[worst]


__all__ = [
    "MUTATION_TASKS",
    "ChemistryPolicy",
    "PolicyDecision",
]
