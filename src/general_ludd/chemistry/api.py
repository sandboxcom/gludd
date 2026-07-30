"""Chemistry expert API: the top-level request → result orchestrator.

Implements the typed entry point that the ansible collection, CLI, and skill
layer share. The API composes :class:`ChemistryPolicy` (constraint +
mutation gate) and :class:`ChemistryRouter` (task → workflow + risk
classification) and applies the spec §9 safety stops before dispatching.

Workflow (spec §4.2 → §4.3, §9 safety table):

1. **Validate** request constraints via :meth:`ChemistryPolicy.check_request`.
   A refusal returns a :class:`ChemistryResult` with ``status=refused`` and
   the policy ``decision_id`` correlation.
2. **Mutation gate** — for tasks in
   :data:`general_ludd.chemistry.policy.MUTATION_TASKS`, additionally call
   :meth:`ChemistryPolicy.check_mutation`. When the audit service is
   unavailable the result is ``refused`` with ``fail_closed`` semantics
   (spec §9 row "Audit/policy service unavailable").
3. **Route** — classify risk and select the workflow via
   :class:`ChemistryRouter`. Risk classification happens here, *before*
   detailed work (spec §9).
4. **§9 safety stops** — ambiguous identity, missing hazard evidence.
5. **Dispatch** — return a :class:`ChemistryResult`.
"""

from __future__ import annotations

import uuid

from general_ludd.chemistry.policy import MUTATION_TASKS, ChemistryPolicy
from general_ludd.chemistry.router import ChemistryRouter
from general_ludd.chemistry.schemas import (
    ChemistryRequest,
    ChemistryResult,
    ErrorRecord,
    ResultStatus,
    RiskTier,
    SafetyRecord,
)

_TIER_RANK: dict[str, int] = {"low": 0, "moderate": 1, "high": 2, "prohibited": 3}


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:16]}"


def _err(code: str, message: str, *, retryable: bool = False) -> ErrorRecord:
    return ErrorRecord(code=code, retryable=retryable, message=message)


# Entities that are known to have multiple distinct chemical identities and
# therefore require disambiguation before actionable work proceeds (spec §9
# row "Ambiguous chemical identity | Stop actionable work; request
# disambiguation with candidate records"). A caller may also prefix any
# entity with ``"ambiguous:"`` to force the disambiguation path.
_AMBIGUOUS_ENTITIES: frozenset[str] = frozenset(
    {
        # "ether" is ambiguous: diethyl ether vs petroleum ether vs a generic
        # functional group.
        "ether",
        # "citral" is a stereoisomer mixture (geranial + neral).
        "citral",
        # "morphine" has multiple salt forms with different handling.
        "morphine",
    }
)


def _is_ambiguous(entity: str) -> bool:
    lowered = entity.lower().strip()
    return lowered.startswith("ambiguous:") or lowered in _AMBIGUOUS_ENTITIES


class ChemistryExpertAPI:
    """Top-level entry point for chemistry requests.

    Parameters
    ----------
    policy:
        The :class:`ChemistryPolicy` used for constraint validation and risk
        classification. Defaults to a fresh instance.
    router:
        The :class:`ChemistryRouter` used for task → workflow mapping. Defaults
        to a fresh instance wired to ``policy``.
    audit_available:
        Whether the audit/policy service is reachable. When ``False``,
        mutation tasks are refused with ``fail_closed`` semantics (spec §9).
    """

    def __init__(
        self,
        policy: ChemistryPolicy | None = None,
        router: ChemistryRouter | None = None,
        *,
        audit_available: bool = True,
    ) -> None:
        self._policy = policy or ChemistryPolicy()
        self._router = router or ChemistryRouter(self._policy)
        self._audit_available = audit_available

    def handle_request(self, request: ChemistryRequest) -> ChemistryResult:
        run_id = _new_run_id()

        # 1. Policy: constraint validation.
        decision = self._policy.check_request(request)
        if not decision.allowed:
            return ChemistryResult(
                request_id=request.request_id,
                run_id=run_id,
                status=ResultStatus.refused,
                summary=f"request refused by policy: {decision.reason}",
                errors=[
                    _err(
                        "chem.policy_refused",
                        decision.reason or "policy refusal",
                    )
                ],
            )

        # 2. Mutation tasks require the audit service to be available.
        if request.task in MUTATION_TASKS:
            mutation_decision = self._policy.check_mutation(
                request,
                audit_available=self._audit_available,
            )
            if not mutation_decision.allowed:
                code = "chem.audit_fail_closed" if mutation_decision.fail_closed else "chem.mutation_refused"
                return ChemistryResult(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=ResultStatus.refused,
                    summary=f"mutation blocked: {mutation_decision.reason}",
                    errors=[
                        _err(
                            code,
                            mutation_decision.reason or "mutation refused",
                        )
                    ],
                )

        # 3. Route (risk classification happens inside the router).
        route = self._router.route(request)

        # 4. §9 safety stops.

        # 4a. Ambiguous chemical identity → stop actionable work, request
        #     disambiguation with candidate records.
        ambiguous = [e for e in request.entities if _is_ambiguous(e)]
        if ambiguous:
            return ChemistryResult(
                request_id=request.request_id,
                run_id=run_id,
                status=ResultStatus.refused,
                summary=("ambiguous chemical identity: disambiguation required before actionable work"),
                limitations=[
                    (
                        f"disambiguation-required: {a!r} matches multiple "
                        f"candidate records; provide an explicit structure "
                        f"or identifier"
                    )
                    for a in ambiguous
                ],
                errors=[
                    _err(
                        "chem.ambiguous_identity",
                        "identity disambiguation required (spec §9)",
                    )
                ],
            )

        # 4b. Missing current hazard evidence → refuse protocol/scale-up
        #     (research may continue). Spec §9 row "Missing current hazard
        #     or incompatibility evidence | Refuse protocol/scale-up".
        if route.requires_hazard_review:
            missing = [e for e in request.entities if self._missing_hazard_evidence(e)]
            if missing:
                return ChemistryResult(
                    request_id=request.request_id,
                    run_id=run_id,
                    status=ResultStatus.refused,
                    summary=("missing current hazard evidence: actionable output refused; research may continue"),
                    limitations=[
                        "missing-current-hazard-evidence: hazard record "
                        "unavailable for " + ", ".join(repr(m) for m in missing),
                    ],
                    errors=[
                        _err(
                            "chem.missing_hazard_evidence",
                            "hazard record unavailable (spec §9)",
                        )
                    ],
                    safety=SafetyRecord(
                        risk_tier=RiskTier(route.risk_tier),
                    ),
                )

        # 5. Dispatch (the real workflow implementations plug in here; the
        #    typed entry point returns a succeeded scaffold).
        return ChemistryResult(
            request_id=request.request_id,
            run_id=run_id,
            status=ResultStatus.succeeded,
            summary=(f"routed {request.task.value!r} to {route.workflow!r} (risk: {route.risk_tier})"),
            safety=SafetyRecord(risk_tier=RiskTier(route.risk_tier)),
        )

    @staticmethod
    def _missing_hazard_evidence(entity: str) -> bool:
        """Return ``True`` iff no hazard record is available for ``entity``."""
        from general_ludd.chemistry.core import screen_hazards

        screen = screen_hazards(entity)
        return any("missing-current-hazard-evidence" in (lim or "") for lim in screen.get("limitations", []))


__all__ = ["ChemistryExpertAPI"]
