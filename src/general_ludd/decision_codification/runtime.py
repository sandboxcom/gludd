"""Deterministic, exact decision-rule lookup with pervasive typed abstention."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from general_ludd.decision_codification.artifact_store import (
    ArtifactStoreError,
    DecisionArtifactStore,
)
from general_ludd.decision_codification.rollout import (
    OutcomeFeedback,
    RolloutController,
    RolloutError,
)
from general_ludd.decision_codification.runtime_context import build_rule_context
from general_ludd.decision_codification.runtime_rules import (
    RuleBundleAdapter,
    RulesEngineAdapter,
)
from general_ludd.decision_codification.schema import (
    DECISION_APPLICATION_OUTCOME_SCHEMA_V1,
    DecisionAbstentionV1,
    DecisionApplicationOutcomeV1,
    DecisionContextV1,
    DecisionKind,
    FallbackReason,
    NormalizationRefusalReason,
    NormalizationRefusalV1,
    RolloutStage,
    VerifiedOutcome,
    canonical_sha256,
)
from general_ludd.decision_codification.telemetry import DecisionCodificationTelemetry

SafetyCheck = Callable[[str, DecisionContextV1], bool]


@dataclass(frozen=True, slots=True)
class CodifiedDecision:
    """Content-safe receipt for one deterministic runtime decision."""

    decision: str
    project_id: str
    decision_kind: DecisionKind
    leaf_id: str
    context_id: str
    candidate_digest: str
    rollout_stage: RolloutStage
    side_effect_id: str
    application_id: str
    decision_receipt_digest: str


class DecisionRuntime:
    """Resolve one exact active scope without network, model, or fuzzy calls."""

    def __init__(
        self,
        artifacts: DecisionArtifactStore,
        rollout: RolloutController,
        *,
        safety_check: SafetyCheck | None = None,
        rule_adapter: RuleBundleAdapter | None = None,
        telemetry: DecisionCodificationTelemetry | None = None,
    ) -> None:
        """Bind one verified rollout source and optional safety adapters."""
        # ``artifacts`` is accepted explicitly to make the trust boundary visible;
        # the rollout controller is the sole verified read adapter.
        if artifacts is not rollout.artifacts:
            raise ValueError("runtime and rollout must share one artifact store")
        self._rollout = rollout
        self._safety_check = safety_check or (lambda decision, context: True)
        self._rules = rule_adapter or RulesEngineAdapter()
        self._telemetry = telemetry or DecisionCodificationTelemetry()

    def lookup(
        self,
        *,
        project_id: str,
        decision_kind: DecisionKind,
        normalized: DecisionContextV1 | NormalizationRefusalV1,
        correlation_id: str,
        policy_digest: str,
        now: datetime,
        side_effect_id: str,
    ) -> CodifiedDecision | DecisionAbstentionV1:
        """Return an exact codified decision or one closed abstention reason."""
        if isinstance(normalized, NormalizationRefusalV1):
            return self._abstain(
                decision_kind,
                FallbackReason.NORMALIZATION_REFUSED,
                normalization_reason=normalized.reason,
            )
        context_input = normalized
        try:
            pointer = self._rollout.current(project_id, decision_kind)
        except Exception:
            return self._abstain(
                decision_kind,
                FallbackReason.RUNTIME_ERROR,
                context=context_input,
            )
        if pointer is None:
            reason = (
                FallbackReason.REVOKED
                if self._rollout.inactive_reason(project_id, decision_kind) == "revoked"
                else FallbackReason.NO_ACTIVE_RULE
            )
            return self._abstain(decision_kind, reason)
        try:
            revoked = self._rollout.is_revoked(pointer.candidate_digest)
            drift_held = self._rollout.is_drift_held(pointer.candidate_digest)
        except Exception:
            return self._abstain(
                decision_kind,
                FallbackReason.RUNTIME_ERROR,
                context=context_input,
                candidate_digest=pointer.candidate_digest,
            )
        if revoked:
            return self._abstain(
                decision_kind,
                FallbackReason.REVOKED,
                context=context_input,
                candidate_digest=pointer.candidate_digest,
            )
        if drift_held:
            return self._abstain(
                decision_kind,
                FallbackReason.DRIFT_HOLD,
                context=context_input,
                candidate_digest=pointer.candidate_digest,
            )
        try:
            bundle, receipt = self._rollout.verified_generation(pointer)
        except (ArtifactStoreError, RolloutError):
            try:
                self._rollout.mark_drift_hold(
                    project_id,
                    decision_kind,
                    "integrity_error",
                )
            except Exception:
                return self._abstain(
                    decision_kind,
                    FallbackReason.INTEGRITY_FAILURE,
                    context=context_input,
                    candidate_digest=pointer.candidate_digest,
                )
            return self._abstain(
                decision_kind,
                FallbackReason.INTEGRITY_FAILURE,
                context=context_input,
                candidate_digest=pointer.candidate_digest,
            )

        try:
            if (
                context_input.project_id != project_id
                or context_input.decision_kind is not decision_kind
            ):
                return self._abstain(
                    decision_kind,
                    FallbackReason.SCOPE_MISS,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if now >= bundle.expires_at or now >= receipt.expires_at:
                return self._abstain(
                    decision_kind,
                    FallbackReason.EXPIRED,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if context_input.feature_schema != bundle.feature_schema:
                return self._abstain(
                    decision_kind,
                    FallbackReason.SCOPE_MISS,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if (
                context_input.exact_guards.get("action_vocabulary")
                != f"{bundle.decision_kind.value}.v1"
                or context_input.exact_guards.get("risk_band") != bundle.risk_scope
                or context_input.context_signature
                not in bundle.observed_context_digests
            ):
                return self._abstain(
                    decision_kind,
                    FallbackReason.SCOPE_MISS,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if (
                context_input.policy_digest != policy_digest
                or policy_digest not in bundle.policy_compatibility
            ):
                self._rollout.mark_drift_hold(project_id, decision_kind, "policy_changed")
                return self._abstain(
                    decision_kind,
                    FallbackReason.POLICY_CHANGED,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if not self._rollout.selected_for_execution(pointer, correlation_id):
                return self._abstain(
                    decision_kind,
                    FallbackReason.CANARY_EXCLUDED,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )

            context = build_rule_context(context_input, bundle)
            if context is None:
                return self._abstain(
                    decision_kind,
                    FallbackReason.SCOPE_MISS,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            leaf_ids = self._rules.matching_leaf_ids(bundle, context)
            if len(leaf_ids) > 1:
                self._rollout.mark_drift_hold(
                    project_id, decision_kind, "multiple_leaves"
                )
                return self._abstain(
                    decision_kind,
                    FallbackReason.MULTIPLE_LEAVES,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if not leaf_ids:
                return self._abstain(
                    decision_kind,
                    FallbackReason.NO_LEAF,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            leaf = next(item for item in bundle.leaves if item.leaf_id == leaf_ids[0])
            if leaf.abstain or leaf.decision is None:
                return self._abstain(
                    decision_kind,
                    FallbackReason.NO_LEAF,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            if not self._safety_check(leaf.decision, context_input):
                self._rollout.mark_drift_hold(
                    project_id, decision_kind, "policy_changed"
                )
                return self._abstain(
                    decision_kind,
                    FallbackReason.POLICY_CHANGED,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            application_id = canonical_sha256({
                "candidate_digest": bundle.candidate_digest,
                "context_id": context_input.context_id,
                "correlation_id": correlation_id,
                "side_effect_id": side_effect_id,
            })
            maximum = min(bundle.maximum_use_count, receipt.maximum_use_count)
            if not self._rollout.reserve_use(
                bundle.candidate_digest, application_id, maximum
            ):
                self._rollout.mark_drift_hold(project_id, decision_kind, "use_limit")
                return self._abstain(
                    decision_kind,
                    FallbackReason.EXPIRED,
                    context=context_input,
                    candidate_digest=bundle.candidate_digest,
                )
            decision_receipt_digest = canonical_sha256({
                "input_context_digest": context_input.context_id,
                "rule_bundle_digest": bundle.candidate_digest,
                "leaf_id": leaf.leaf_id,
                "result": leaf.decision,
                "rollout_stage": pointer.stage.value,
                "side_effect_id": side_effect_id,
            })
            self._telemetry.lookup(
                decision_kind.value, "codified", pointer.stage.value
            )
            return CodifiedDecision(
                decision=leaf.decision,
                project_id=project_id,
                decision_kind=decision_kind,
                leaf_id=leaf.leaf_id,
                context_id=context_input.context_id,
                candidate_digest=bundle.candidate_digest,
                rollout_stage=pointer.stage,
                side_effect_id=side_effect_id,
                application_id=application_id,
                decision_receipt_digest=decision_receipt_digest,
            )
        except Exception:
            return self._abstain(
                decision_kind,
                FallbackReason.RUNTIME_ERROR,
                context=context_input,
                candidate_digest=bundle.candidate_digest,
            )

    def record_application_outcome(
        self,
        *,
        project_id: str,
        decision_kind: DecisionKind,
        candidate_digest: str,
        application_id: str,
        rollout_stage: RolloutStage,
        outcome: VerifiedOutcome,
        occurred_at: datetime,
        terminal_event_id: str | None,
        evidence_digest: str | None,
    ) -> OutcomeFeedback:
        """Persist terminal feedback and immediately propagate a drift hold."""
        bundle = self._rollout.artifacts.read_rule_bundle(candidate_digest)
        if bundle.project_id != project_id or bundle.decision_kind is not decision_kind:
            raise RolloutError("application outcome scope does not match candidate")
        record = DecisionApplicationOutcomeV1(
            schema=DECISION_APPLICATION_OUTCOME_SCHEMA_V1,
            project_id=project_id,
            decision_kind=decision_kind,
            candidate_digest=candidate_digest,
            application_id=application_id,
            rollout_stage=rollout_stage,
            outcome=outcome,
            occurred_at=occurred_at,
            terminal_event_id=terminal_event_id,
            evidence_digest=evidence_digest,
        )
        feedback = self._rollout.record_application_outcome(record)
        if feedback.recorded:
            self._telemetry.application(
                decision_kind.value,
                outcome.value,
                rollout_stage.value,
            )
        if feedback.drift_reason is not None:
            self._telemetry.drift(decision_kind.value, feedback.drift_reason)
        return feedback

    def _abstain(
        self,
        decision_kind: DecisionKind,
        reason: FallbackReason,
        *,
        normalization_reason: NormalizationRefusalReason | None = None,
        context: DecisionContextV1 | None = None,
        candidate_digest: str | None = None,
    ) -> DecisionAbstentionV1:
        self._telemetry.lookup(decision_kind.value, "abstained", "none")
        self._telemetry.fallback(decision_kind.value, reason.value)
        return DecisionAbstentionV1(
            reason=reason,
            normalization_reason=normalization_reason,
            context_id=context.context_id if context is not None else None,
            candidate_digest=candidate_digest,
        )


__all__ = [
    "CodifiedDecision",
    "DecisionRuntime",
    "RuleBundleAdapter",
    "RulesEngineAdapter",
    "SafetyCheck",
]
