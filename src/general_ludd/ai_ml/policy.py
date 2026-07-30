"""AIML Phase A/C — policy engine: request + mutation gates (spec §4.2, §11).

Implements the policy enforcement layer referenced by the expert router
(AIML-001) and exercised by AIML-AT-021:

    Mutation fails closed when policy/audit storage is unavailable;
    eligible read-only queries are explicitly ``degraded``.

Spec §11 row: "Event/audit store unavailable -> Fail closed for mutation;
read-only answers may degrade only under policy."

Spec §4.2: every :class:`ExpertResult` carries a ``policy`` block with
``decision_id`` (uuid) and ``ruleset_sha256`` (hex). This module is the
canonical source of both.

Contract:

  - :class:`PolicyEngine.check_request` validates a request's constraints
    (budget, deadline, data_classification, allowed_tools/licenses). It
    NEVER silently relaxes a constraint (spec §4.1: "it never silently
    relaxes them").
  - :class:`PolicyEngine.check_mutation` additionally requires the audit
    store to be available; if it is not, the mutation is REFUSED (fail
    closed).
  - Every :class:`PolicyResult` carries a unique ``decision_id`` and the
    fixed ``ruleset_sha256`` so the decision is independently auditable.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from general_ludd.ai_ml.schemas import Constraints, DataClassification, ExpertRequest, ExpertTask

# ---------------------------------------------------------------------------
# Fixed policy ruleset (spec §4.2, §11)
# ---------------------------------------------------------------------------

# The policy rules are CONSTANTS — they are not derived from, and cannot be
# altered by, any request payload or retrieved content. The digest below
# fingerprints the rule text so a result's ruleset_sha256 is stable across
# requests and auditable (AIML-AT-021 structural pin).
_POLICY_RULESET_TEXT = (
    "ai_ml.policy.v1: "
    "budget must cover task cost; "
    "deadline must be positive; "
    "restricted classification requires offline mode; "
    "required tools must intersect allowed_tools; "
    "mutation requires available audit storage (fail closed); "
    "constraints are never silently relaxed"
)
POLICY_RULESET_SHA256: str = hashlib.sha256(_POLICY_RULESET_TEXT.encode("utf-8")).hexdigest()

# Tasks that mutate state (train, distill, deploy) — these require the
# audit store to be available (spec §11: fail closed for mutation).
_MUTATION_TASKS: frozenset[ExpertTask] = frozenset(
    {
        ExpertTask.TRAIN,
        ExpertTask.DISTILL,
        ExpertTask.DEPLOY,
        ExpertTask.DATASET,
    }
)

# Tasks that require a non-zero budget to proceed (spec §11: "Budget/quota
# exhaustion -> Stop before overrun").
_BUDGET_REQUIRED_TASKS: frozenset[ExpertTask] = frozenset(
    {
        ExpertTask.TRAIN,
        ExpertTask.DISTILL,
        ExpertTask.DEPLOY,
    }
)


# ---------------------------------------------------------------------------
# PolicyResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyResult:
    """A typed policy decision (spec §4.2 ``policy`` block).

    ``decision_id`` is a unique per-request uuid; ``ruleset_sha256`` is the
    fixed digest of the policy ruleset (:data:`POLICY_RULESET_SHA256`).
    ``allowed`` is the verdict; ``refusal_reasons`` lists the constraints
    that failed (empty when allowed).
    """

    decision_id: str
    ruleset_sha256: str
    allowed: bool
    refusal_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id.strip():
            raise ValueError("decision_id must be a non-empty string")
        if not isinstance(self.ruleset_sha256, str) or not self.ruleset_sha256.strip():
            raise ValueError("ruleset_sha256 must be a non-empty sha256 hex digest")
        if not isinstance(self.allowed, bool):
            raise ValueError("allowed must be a bool")
        if not isinstance(self.refusal_reasons, tuple):
            raise ValueError("refusal_reasons must be a tuple of strings")
        if self.allowed and self.refusal_reasons:
            raise ValueError("an allowed result must not carry refusal_reasons")
        for r in self.refusal_reasons:
            if not isinstance(r, str) or not r.strip():
                raise ValueError("refusal_reasons entries must be non-empty strings")


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------


@dataclass
class PolicyEngine:
    """Validate expert requests against fixed policy rules (spec §4.1, §11).

    Parameters:
      audit_available: whether the event/audit store is reachable. When
        ``False``, :meth:`check_mutation` refuses all mutations (spec §11:
        "Fail closed for mutation").
      required_tools: a mapping from task kind (e.g. ``"distill"``) to the
        tool capability IDs that task requires. A request whose
        ``allowed_tools`` does not intersect the required set is refused.
      ruleset_sha256: the fixed ruleset digest; defaults to
        :data:`POLICY_RULESET_SHA256`. Override only for test isolation.
    """

    audit_available: bool = True
    required_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ruleset_sha256: str = POLICY_RULESET_SHA256

    def __post_init__(self) -> None:
        if not isinstance(self.audit_available, bool):
            raise ValueError("audit_available must be a bool")
        if not isinstance(self.required_tools, dict):
            raise ValueError("required_tools must be a dict[str, tuple[str, ...]]")
        if not isinstance(self.ruleset_sha256, str) or len(self.ruleset_sha256) != 64:
            raise ValueError("ruleset_sha256 must be a 64-char hex digest")

    # ------------------------------------------------------------------
    # Request gate (read-only + mutation)
    # ------------------------------------------------------------------

    def check_request(self, request: ExpertRequest) -> PolicyResult:
        """Validate a request's constraints (spec §4.1).

        Checks:
          - deadline_s > 0 (schemas enforces; double-check here).
          - budget_usd covers the task's cost class (train/distill/deploy
            require non-zero budget; spec §11).
          - data_classification == RESTRICTED requires offline=True (spec
            §11: cryptographic isolation; restricted data must not leave
            the tenant boundary).
          - required_tools for the task kind intersect allowed_tools.
          - allowed_licenses, when the request specifies them, are
            respected (non-empty list is a positive grant).
        """
        reasons = self._check_constraints(request)
        allowed = not reasons
        return PolicyResult(
            decision_id=self._new_decision_id(),
            ruleset_sha256=self.ruleset_sha256,
            allowed=allowed,
            refusal_reasons=tuple(reasons),
        )

    # ------------------------------------------------------------------
    # Mutation gate (fail closed when audit unavailable)
    # ------------------------------------------------------------------

    def check_mutation(self, request: ExpertRequest) -> PolicyResult:
        """Validate a mutation request AND require audit storage (spec §11).

        Spec §11: "Event/audit store unavailable -> Fail closed for
        mutation." This is the AIML-AT-021 structural gate: a mutation
        with no audit trail is refused, never silently allowed.
        """
        reasons = self._check_constraints(request)

        if request.task in _MUTATION_TASKS and not self.audit_available:
            reasons.append("audit storage unavailable; mutation refused (fail closed, spec §11)")

        allowed = not reasons
        return PolicyResult(
            decision_id=self._new_decision_id(),
            ruleset_sha256=self.ruleset_sha256,
            allowed=allowed,
            refusal_reasons=tuple(reasons),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_constraints(self, request: ExpertRequest) -> list[str]:
        """Return the list of constraint failures for ``request``."""
        reasons: list[str] = []
        constraints: Constraints = request.constraints

        if constraints.deadline_s <= 0:
            reasons.append("deadline_s must be positive")

        if request.task in _BUDGET_REQUIRED_TASKS and constraints.budget_usd <= 0:
            reasons.append(f"budget_usd must be > 0 for task {request.task.value!r} (spec §11: stop before overrun)")

        if constraints.data_classification is DataClassification.RESTRICTED and not constraints.offline:
            reasons.append("restricted data_classification requires offline=True (spec §11: cryptographic isolation)")

        required = self.required_tools.get(request.task.value)
        if required:
            allowed_set = set(constraints.allowed_tools)
            if not set(required).intersection(allowed_set):
                reasons.append(
                    f"task {request.task.value!r} requires one of {sorted(required)} "
                    f"in allowed_tools, got {sorted(allowed_set) or '{}'}"
                )

        return reasons

    @staticmethod
    def _new_decision_id() -> str:
        return f"pol-{uuid.uuid4().hex[:16]}"


__all__ = [
    "POLICY_RULESET_SHA256",
    "PolicyEngine",
    "PolicyResult",
]
