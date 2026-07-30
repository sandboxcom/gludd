"""Expert router, evidence store, answer, and tool-discovery service.

Implements the top-five capabilities from docs/specs/FEATURE_AI_ML_EXPERT.md:

  - AIML-001 ``ExpertRouter`` — route a request to the smallest qualified
    role set. Refuses (never silently relaxes) when constraints cannot be
    satisfied or when a mutation task lacks an approval token.
  - AIML-002 / AIML-003 ``EvidenceStore`` — immutable, content-addressed,
    citation-addressable evidence store with tenant isolation. Duplicate
    content produces one artifact and multiple source locators
    (AIML-AT-002).
  - AIML-007 ``answer_question`` — produce a cited, uncertainty-calibrated
    answer. A failing independent verification never yields ``succeeded``
    (AIML-AT-007).
  - AIML-018 ``discover_tools`` — produce a decision record including
    rejected alternatives and a mandatory integration spike (AIML-AT-018).

The router never shells out to roles directly — role composition uses the
typed orchestrator API and records parent/child run IDs (spec §3.2). The
functions here are the typed entry points the ansible roles wrap.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from general_ludd.ai_ml.evidence import EvidenceStore
from general_ludd.ai_ml.schemas import (
    Citation,
    Constraints,
    ExpertRequest,
    ExpertResult,
    ExpertTask,
    ResultStatus,
    RouterDecision,
    ToolCandidate,
    ToolDecisionRecord,
    Uncertainty,
    Verification,
    VerificationStatus,
)

# ---------------------------------------------------------------------------
# AIML-001 — Expert router
# ---------------------------------------------------------------------------

# Read-only tasks: never require an approval token.
_READ_ONLY_TASKS: frozenset[ExpertTask] = frozenset(
    {
        ExpertTask.QUESTION,
        ExpertTask.RESEARCH,
        ExpertTask.EVALUATE,
    }
)

# Mutation tasks: require an approval token (spec §4.1, §11).
_MUTATION_TASKS: frozenset[ExpertTask] = frozenset(
    {
        ExpertTask.TRAIN,
        ExpertTask.DISTILL,
        ExpertTask.DEPLOY,
        ExpertTask.DATASET,
    }
)

# Tasks that require online research; the router refuses when constraints.offline
# is set because the result cannot be grounded (spec §11 retrieval-outage row:
# "fail if freshness is required").
_ONLINE_REQUIRED_TASKS: frozenset[ExpertTask] = frozenset(
    {
        ExpertTask.QUESTION,
        ExpertTask.RESEARCH,
    }
)

# Task -> role mapping (spec §3.2 role table). A pure question routes to the
# smallest qualified set: just ``research_answer``.
_TASK_ROLES: dict[ExpertTask, tuple[str, ...]] = {
    ExpertTask.QUESTION: ("research_answer",),
    ExpertTask.RESEARCH: ("research_refresh", "research_answer"),
    ExpertTask.DATASET: ("dataset_engineer",),
    ExpertTask.TRAIN: ("adapter_train",),
    ExpertTask.DISTILL: ("model_distill",),
    ExpertTask.SPEECH: ("speech_recognize",),
    ExpertTask.VISION: ("vision_understand",),
    ExpertTask.IMAGE: ("image_create",),
    ExpertTask.WORLD_MODEL: ("world_model",),
    ExpertTask.SIMULATE: ("simulate_domain",),
    ExpertTask.EVALUATE: ("evaluate_model",),
    ExpertTask.DEPLOY: ("promote_release",),
}


class ExpertRouter:
    """Route an ExpertRequest to the smallest qualified role set (AIML-001).

    Returns a typed ``RouterDecision`` whose ``refusal_reason`` is set when the
    constraints cannot be satisfied or when a mutation task lacks an approval
    token. The router never silently relaxes a constraint.
    """

    def route(self, request: ExpertRequest) -> RouterDecision:
        refusal = self._refusal_reason(request)
        if refusal is not None:
            return RouterDecision(
                request_id=request.request_id,
                matched_roles=(),
                refusal_reason=refusal,
            )
        roles = _TASK_ROLES.get(request.task, ())
        return RouterDecision(
            request_id=request.request_id,
            matched_roles=roles,
            refusal_reason=None,
        )

    @staticmethod
    def _refusal_reason(request: ExpertRequest) -> str | None:
        constraints: Constraints = request.constraints
        # Schema already enforces deadline_s > 0 and budget_usd >= 0; the
        # router evaluates *compound* constraints the schema cannot express.
        if constraints.offline and request.task in _ONLINE_REQUIRED_TASKS:
            return f"task {request.task.value!r} requires online research but constraints.offline is true"
        if request.task in _MUTATION_TASKS:
            token = request.approval_token
            if token is None or not token.strip():
                return f"task {request.task.value!r} is a mutation task and requires a non-empty approval_token"
        return None


# ---------------------------------------------------------------------------
# AIML-002 / AIML-003 — Evidence store lives in ``general_ludd.ai_ml.evidence``.
# It is re-exported from this module (and from the package __init__) so the
# historical import paths keep working.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AIML-007 — Reasoning / answer
# ---------------------------------------------------------------------------

# Keywords that mark a query as a numerical claim requiring an independent
# verification before the answer may be marked ``succeeded`` (spec §6.3).
_NUMERICAL_CLAIM_KEYWORDS: frozenset[str] = frozenset(
    {
        "recall",
        "precision",
        "ndcg",
        "mrr",
        "accuracy",
        "latency",
        "throughput",
        "wer",
        "cer",
        "f1",
        "auc",
        "perplexity",
        "bleu",
        "meteor",
        "rouge",
        "how many",
        "how much",
        "calculate",
        "compute",
    }
)


def _is_numerical_claim(query: str) -> bool:
    lowered = query.lower()
    return any(kw in lowered for kw in _NUMERICAL_CLAIM_KEYWORDS)


def answer_question(
    request: ExpertRequest,
    store: EvidenceStore,
    *,
    extra_verifications: Iterable[Verification] = (),
) -> ExpertResult:
    """Produce a cited, uncertainty-calibrated answer (AIML-007).

    Grounding rules (spec §6.3, AIML-AT-007):
      - An answer with no supporting evidence is never ``succeeded``.
      - A numerical claim with a failing independent check is never
        ``succeeded`` — it is downgraded to ``degraded`` or ``failed``.
    """
    run_id = f"run-{uuid.uuid4().hex[:16]}"
    evidence = store.list_all(tenant_id=request.tenant_id)

    if not evidence:
        return ExpertResult(
            request_id=request.request_id,
            run_id=run_id,
            status=ResultStatus.REFUSED,
            answer=None,
            uncertainty=Uncertainty(
                score=1.0,
                method="no_evidence",
                limitations=("no evidence available to ground an answer",),
            ),
            verification=tuple(extra_verifications),
            errors=(),
        )

    # Build a cited answer scaffold grounded in the first piece of evidence.
    # (A production implementation would invoke the retrieval service here;
    # this typed entry point is the contract the retrieval backend plugs into.)
    primary = evidence[0]
    answer_text = (
        f"Based on {len(evidence)} source(s), the answer to "
        f"{request.query!r} is grounded in evidence {primary.source_id}."
    )
    citations = (
        Citation(
            source_id=primary.source_id,
            locator=primary.locators[0],
            claim_ids=("grounded_answer",),
        ),
    )

    numerical = _is_numerical_claim(request.query)
    verifications: list[Verification] = list(extra_verifications)
    if numerical and not any(v.check == "independent numerical verification" for v in verifications):
        # Spec §6.3: at least one independent check is required for
        # high-impact numerical answers. When the caller does not supply
        # one, we mark it as ``not_run`` and downgrade the result.
        verifications.append(
            Verification(
                check="independent numerical verification",
                status=VerificationStatus.NOT_RUN,
                artifact_uri=None,
            )
        )

    failed = any(v.status is VerificationStatus.FAIL for v in verifications)
    not_run_required = numerical and any(
        v.status is VerificationStatus.NOT_RUN and v.check == "independent numerical verification"
        for v in verifications
    )

    if failed:
        status = ResultStatus.FAILED
        uncertainty_score = 0.9
        method = "independent_check_failed"
        limitations = ("an independent verification check failed",)
    elif not_run_required:
        status = ResultStatus.DEGRADED
        uncertainty_score = 0.6
        method = "independent_check_not_run"
        limitations = ("required independent numerical check was not run",)
    else:
        status = ResultStatus.SUCCEEDED
        uncertainty_score = 0.3
        method = "evidence_grounded"
        limitations = ()

    return ExpertResult(
        request_id=request.request_id,
        run_id=run_id,
        status=status,
        answer=answer_text,
        citations=citations,
        verification=tuple(verifications),
        uncertainty=Uncertainty(
            score=uncertainty_score,
            method=method,
            limitations=limitations,
        ),
    )


# ---------------------------------------------------------------------------
# AIML-018 — Tool discovery
# ---------------------------------------------------------------------------

# Selection basis (spec §9). Popularity alone is NOT a signal.
_SELECTION_BASIS: tuple[str, ...] = (
    "task_fit",
    "maintenance",
    "security",
    "license",
    "exit_strategy",
)


def discover_tools(
    need: str,
    registry: Iterable[ToolCandidate],
    *,
    min_maintenance_score: float = 0.5,
    min_security_score: float = 0.4,
) -> ToolDecisionRecord:
    """Discover and assess mature tools before custom code is written (AIML-018).

    Emits a decision record with ``selected`` and ``rejected_alternatives``
    (AIML-AT-018). Candidates below the maintenance/security floor are
    rejected with a reason — popularity alone is never a selection signal
    (spec §9).
    """
    if not isinstance(need, str) or not need.strip():
        raise ValueError("need must be a non-empty string")
    if not (0.0 <= min_maintenance_score <= 1.0):
        raise ValueError(f"min_maintenance_score must be in [0.0, 1.0], got {min_maintenance_score}")
    if not (0.0 <= min_security_score <= 1.0):
        raise ValueError(f"min_security_score must be in [0.0, 1.0], got {min_security_score}")

    selected: list[ToolCandidate] = []
    rejected: list[ToolCandidate] = []

    for candidate in registry:
        reason = _reject_reason(
            candidate,
            min_maintenance_score=min_maintenance_score,
            min_security_score=min_security_score,
        )
        if reason is not None:
            rejected.append(
                ToolCandidate(
                    capability_id=candidate.capability_id,
                    name=candidate.name,
                    version=candidate.version,
                    license=candidate.license,
                    maintenance_score=candidate.maintenance_score,
                    security_score=candidate.security_score,
                    task_fit_score=candidate.task_fit_score,
                    has_exit_strategy=candidate.has_exit_strategy,
                    rejection_reason=reason,
                )
            )
        else:
            selected.append(candidate)

    # Rank selected by composite score (task fit weighted highest).
    selected.sort(
        key=lambda c: (
            c.task_fit_score * 0.4
            + c.maintenance_score * 0.3
            + c.security_score * 0.2
            + (0.1 if c.has_exit_strategy else 0.0)
        ),
        reverse=True,
    )

    return ToolDecisionRecord(
        need=need,
        selected=tuple(selected),
        rejected_alternatives=tuple(rejected),
        integration_spike_required=True,
        selection_basis=_SELECTION_BASIS,
    )


def _reject_reason(
    candidate: ToolCandidate,
    *,
    min_maintenance_score: float,
    min_security_score: float,
) -> str | None:
    """Return a rejection reason, or None if the candidate is selectable."""
    if candidate.maintenance_score < min_maintenance_score:
        return f"maintenance_score {candidate.maintenance_score:.2f} < minimum {min_maintenance_score:.2f}"
    if candidate.security_score < min_security_score:
        return f"security_score {candidate.security_score:.2f} < minimum {min_security_score:.2f}"
    if not candidate.has_exit_strategy:
        return "no documented exit strategy"
    return None


__all__ = [
    "EvidenceStore",
    "ExpertRouter",
    "answer_question",
    "discover_tools",
]
