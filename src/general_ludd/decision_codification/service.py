"""Verified-log analysis and deterministic decision resolution orchestration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from time import monotonic_ns as _monotonic_ns
from typing import Protocol

from pydantic import ConfigDict, TypeAdapter, ValidationError

from general_ludd.decision_codification.capture import (
    DecisionCaptureReceipt,
    DecisionOutcomeRecorder,
)
from general_ludd.decision_codification.evaluate import (
    EvaluationError,
    activation_eligible,
    chronological_root_task_split,
    evaluate_candidate,
)
from general_ludd.decision_codification.export import TreeExportError, train_and_export_tree
from general_ludd.decision_codification.miner import (
    DecisionEvidence,
    MiningFloors,
    mine_candidate_groups,
)
from general_ludd.decision_codification.normalize import (
    normalize_decision_context,
    normalize_verified_decision_event,
    normalize_verified_decision_outcome_event,
)
from general_ludd.decision_codification.observability import (
    DecisionResolutionPath,
    DecisionReuseObservability,
)
from general_ludd.decision_codification.rollout import OutcomeFeedback
from general_ludd.decision_codification.runtime import CodifiedDecision, DecisionRuntime
from general_ludd.decision_codification.schema import (
    DECISION_ACTIONS_V1,
    DecisionAbstentionV1,
    DecisionKind,
    DecisionRuleBundleV1,
    EvaluationReportV1,
    FallbackReason,
    NormalizationRefusalV1,
    OutcomeEvidenceV1,
    RolloutStage,
    VerifiedDecisionSourceV1,
    VerifiedOutcome,
    canonical_sha256,
)
from general_ludd.replay.schema import BoundedIdentifier, EventEnvelopeV1, Sha256Digest
from general_ludd.replay.store import ReplayStoreError, VerifiedBundle

MAX_ANALYSIS_BUNDLES = 10_000
MAX_ANALYSIS_EVENTS = 100_000
_PROJECT_ID_ADAPTER = TypeAdapter(BoundedIdentifier, config=ConfigDict(strict=True))
_POLICY_DIGEST_ADAPTER = TypeAdapter(Sha256Digest, config=ConfigDict(strict=True))
_DECISION_EVENT_TYPES = frozenset(
    {"review.decided", "policy.decided", "budget.decided", "reconcile.decided"}
)
_EMBEDDED_OUTCOME_FIELDS = frozenset({"verified_outcome", "outcome_evidence"})


class VerifiedBundleReader(Protocol):
    """Minimal capability boundary for integrity-checked run-bundle reads."""

    def read_verified(self, run_id: str) -> VerifiedBundle:
        """Return a fully verified, complete bundle or raise."""
        ...


class AnalysisRejectionReason(StrEnum):
    """Closed content-free reasons excluded evidence did not become a candidate."""

    UNVERIFIED_BUNDLE = "unverified_bundle"
    UNSIGNED_BUNDLE = "unsigned_bundle"
    PROJECT_MISMATCH = "project_mismatch"
    MISSING_ROOT_TASK = "missing_root_task"
    NORMALIZATION_REFUSED = "normalization_refused"
    NO_SAFE_GROUP = "no_safe_group"
    SPLIT_REFUSED = "split_refused"
    EXPORT_REFUSED = "export_refused"
    VALIDATION_REFUSED = "validation_refused"
    HOLDOUT_REFUSED = "holdout_refused"
    MISSING_OUTCOME = "missing_outcome"
    CONFLICTING_OUTCOME = "conflicting_outcome"
    ORPHAN_OUTCOME = "orphan_outcome"


class DecisionAnalysisError(ValueError):
    """Raised when a request exceeds a hard bound or is globally invalid."""


@dataclass(frozen=True, slots=True)
class DecisionCandidate:
    """One offline-trained candidate with independent replay evidence."""

    cluster_digest: str
    evidence_count: int
    bundle: DecisionRuleBundleV1
    validation_report: EvaluationReportV1
    holdout_report: EvaluationReportV1


@dataclass(frozen=True, slots=True)
class DecisionAnalysis:
    """Bounded content-free summary plus safe activation candidates."""

    bundles_read: int
    events_seen: int
    events_eligible: int
    candidates: tuple[DecisionCandidate, ...]
    rejection_counts: tuple[tuple[AnalysisRejectionReason, int], ...]


class DecisionLogAnalyzer:
    """Mine signed verified run logs into deterministic rule candidates offline."""

    def __init__(
        self,
        store: VerifiedBundleReader,
        *,
        floors: MiningFloors | None = None,
    ) -> None:
        """Bind the verified read capability and immutable safety floors."""
        self._store = store
        self._floors = floors or MiningFloors()

    def analyze(
        self,
        run_ids: Iterable[str],
        *,
        project_id: str,
        current_policy_digest: str,
        training_recipe_digest: str,
        dependency_lock_digest: str,
        created_at: datetime,
        expires_at: datetime,
        maximum_use_count: int,
        estimated_tokens_per_call: int = 0,
    ) -> DecisionAnalysis:
        """Read verified bundles, mine stable groups, and replay two holdouts."""
        identifiers = tuple(run_ids)
        if not identifiers or len(identifiers) > MAX_ANALYSIS_BUNDLES:
            raise DecisionAnalysisError(
                f"analysis requires 1..{MAX_ANALYSIS_BUNDLES} run IDs"
            )
        if len(set(identifiers)) != len(identifiers):
            raise DecisionAnalysisError("analysis run IDs must be unique")
        if not project_id:
            raise DecisionAnalysisError("analysis requires an exact project ID")
        if expires_at <= created_at:
            raise DecisionAnalysisError("candidate expiry must follow creation")
        if maximum_use_count < 1:
            raise DecisionAnalysisError("maximum_use_count must be positive")
        if estimated_tokens_per_call < 0:
            raise DecisionAnalysisError(
                "estimated_tokens_per_call must be non-negative"
            )

        rejections: Counter[AnalysisRejectionReason] = Counter()
        evidence: list[DecisionEvidence] = []
        bundles_read = 0
        events_seen = 0
        for run_id in identifiers:
            try:
                verified = self._store.read_verified(run_id)
            except ReplayStoreError:
                rejections[AnalysisRejectionReason.UNVERIFIED_BUNDLE] += 1
                continue
            bundles_read += 1
            manifest = verified.manifest
            if manifest.integrity != "signed":
                rejections[AnalysisRejectionReason.UNSIGNED_BUNDLE] += 1
                continue
            if manifest.project_id != project_id:
                rejections[AnalysisRejectionReason.PROJECT_MISMATCH] += 1
                continue
            events_seen += len(verified.events)
            if events_seen > MAX_ANALYSIS_EVENTS:
                raise DecisionAnalysisError(
                    f"analysis exceeds the {MAX_ANALYSIS_EVENTS}-event hard limit"
                )
            source = VerifiedDecisionSourceV1(
                source_run_id=manifest.run_id,
                source_bundle_digest=self._bundle_digest(verified),
                project_id=project_id,
                integrity="signed",
                complete=True,
            )
            linked_outcomes: dict[
                str, tuple[EventEnvelopeV1, OutcomeEvidenceV1]
            ] = {}
            conflicting_outcomes: set[str] = set()
            for outcome_event in verified.events:
                if outcome_event.type != "decision.outcome":
                    continue
                linked = normalize_verified_decision_outcome_event(
                    outcome_event,
                    expected_project_id=project_id,
                )
                if isinstance(linked, NormalizationRefusalV1):
                    rejections[AnalysisRejectionReason.NORMALIZATION_REFUSED] += 1
                    continue
                decision_digest = linked.decision_event_digest
                if (
                    decision_digest in linked_outcomes
                    or decision_digest in conflicting_outcomes
                ):
                    linked_outcomes.pop(decision_digest, None)
                    if decision_digest not in conflicting_outcomes:
                        rejections[
                            AnalysisRejectionReason.CONFLICTING_OUTCOME
                        ] += 1
                    conflicting_outcomes.add(decision_digest)
                    continue
                linked_outcomes[decision_digest] = (outcome_event, linked)

            consumed_outcomes: set[str] = set()
            for event in verified.events:
                if event.type == "decision.outcome":
                    continue
                linked_outcome: OutcomeEvidenceV1 | None = None
                has_embedded_outcome = bool(
                    _EMBEDDED_OUTCOME_FIELDS.intersection(event.payload)
                )
                if event.type in _DECISION_EVENT_TYPES and not has_embedded_outcome:
                    if event.digest in conflicting_outcomes:
                        continue
                    linked_record = linked_outcomes.get(event.digest)
                    if linked_record is None:
                        rejections[AnalysisRejectionReason.MISSING_OUTCOME] += 1
                        continue
                    outcome_event, linked_outcome = linked_record
                    consumed_outcomes.add(event.digest)
                    if (
                        outcome_event.correlation != event.correlation
                        or outcome_event.occurred_at < event.occurred_at
                    ):
                        rejections[
                            AnalysisRejectionReason.CONFLICTING_OUTCOME
                        ] += 1
                        continue
                normalized = normalize_verified_decision_event(
                    event,
                    source=source,
                    expected_project_id=project_id,
                    linked_outcome=linked_outcome,
                )
                if isinstance(normalized, NormalizationRefusalV1):
                    rejections[AnalysisRejectionReason.NORMALIZATION_REFUSED] += 1
                    continue
                root_task_id = event.correlation.task_id or event.correlation.todo_id
                if root_task_id is None:
                    rejections[AnalysisRejectionReason.MISSING_ROOT_TASK] += 1
                    continue
                evidence.append(
                    DecisionEvidence(
                        envelope=normalized,
                        root_task_id=root_task_id,
                        source_agent_id=None,
                    )
                )
            orphaned = set(linked_outcomes) - consumed_outcomes
            if orphaned:
                rejections[AnalysisRejectionReason.ORPHAN_OUTCOME] += len(orphaned)

        groups = mine_candidate_groups(evidence, floors=self._floors)
        if evidence and not groups:
            rejections[AnalysisRejectionReason.NO_SAFE_GROUP] += 1
        candidates: list[DecisionCandidate] = []
        for group in groups:
            try:
                split = chronological_root_task_split(group.records)
            except EvaluationError:
                rejections[AnalysisRejectionReason.SPLIT_REFUSED] += 1
                continue
            try:
                bundle = train_and_export_tree(
                    split.training,
                    training_recipe_digest=training_recipe_digest,
                    dependency_lock_digest=dependency_lock_digest,
                    created_at=created_at,
                    expires_at=expires_at,
                    maximum_use_count=maximum_use_count,
                    floors=self._floors,
                    corpus_digest=split.corpus_digest,
                )
            except (TreeExportError, ValueError):
                rejections[AnalysisRejectionReason.EXPORT_REFUSED] += 1
                continue
            try:
                validation = evaluate_candidate(
                    bundle,
                    split.validation,
                    created_at=created_at,
                    current_policy_digest=current_policy_digest,
                    corpus_digest=split.corpus_digest,
                    estimated_tokens_per_call=estimated_tokens_per_call,
                )
            except EvaluationError:
                rejections[AnalysisRejectionReason.VALIDATION_REFUSED] += 1
                continue
            if not activation_eligible(validation, bundle, floors=self._floors):
                rejections[AnalysisRejectionReason.VALIDATION_REFUSED] += 1
                continue
            try:
                holdout = evaluate_candidate(
                    bundle,
                    split.holdout,
                    created_at=created_at,
                    current_policy_digest=current_policy_digest,
                    corpus_digest=split.corpus_digest,
                    estimated_tokens_per_call=estimated_tokens_per_call,
                )
            except EvaluationError:
                rejections[AnalysisRejectionReason.HOLDOUT_REFUSED] += 1
                continue
            if not activation_eligible(holdout, bundle, floors=self._floors):
                rejections[AnalysisRejectionReason.HOLDOUT_REFUSED] += 1
                continue
            candidates.append(
                DecisionCandidate(
                    cluster_digest=group.cluster_digest,
                    evidence_count=len(group.records),
                    bundle=bundle,
                    validation_report=validation,
                    holdout_report=holdout,
                )
            )
        return DecisionAnalysis(
            bundles_read=bundles_read,
            events_seen=events_seen,
            events_eligible=len(evidence),
            candidates=tuple(
                sorted(candidates, key=lambda candidate: candidate.cluster_digest)
            ),
            rejection_counts=tuple(
                sorted(rejections.items(), key=lambda item: item[0].value)
            ),
        )

    @staticmethod
    def _bundle_digest(bundle: VerifiedBundle) -> str:
        return canonical_sha256({
            "schema": "gludd.verified-decision-source/v1",
            "manifest": bundle.manifest.model_dump(mode="json", by_alias=True),
            "ordered_event_digests": [event.digest for event in bundle.events],
        })


class DecisionResolutionSource(StrEnum):
    """Closed attribution for a live decision result."""

    CODIFIED = "codified"
    AGENT_FALLBACK = "agent_fallback"


class DecisionResolutionError(RuntimeError):
    """Raised when an agent fallback violates the closed action vocabulary."""


@dataclass(frozen=True, slots=True)
class DecisionResolution:
    """One live result attributed to deterministic rules or explicit fallback."""

    decision: str
    source: DecisionResolutionSource
    candidate_digest: str | None
    decision_receipt_digest: str | None
    abstention: DecisionAbstentionV1 | None
    project_id: str | None = None
    decision_kind: DecisionKind | None = None
    rollout_stage: RolloutStage | None = None
    application_id: str | None = None


AgentFallback = Callable[[DecisionAbstentionV1], str]


class DecisionResolver:
    """Use exact codified rules first and invoke an agent only after abstention."""

    def __init__(
        self,
        runtime: DecisionRuntime,
        *,
        observability: DecisionReuseObservability | None = None,
        monotonic_ns: Callable[[], int] = _monotonic_ns,
    ) -> None:
        """Bind the local deterministic runtime; no model adapter is retained."""
        self._runtime = runtime
        self._observability = observability
        self._monotonic_ns = monotonic_ns

    def resolve(
        self,
        *,
        project_id: str,
        expected_project_id: str,
        decision_kind: DecisionKind,
        policy_digest: str,
        features: object,
        correlation_id: str,
        now: datetime,
        side_effect_id: str,
        fallback: AgentFallback,
    ) -> DecisionResolution:
        """Return a local hit, otherwise make exactly one explicit fallback call."""
        started = self._tick() if self._observability is not None else None
        normalized = normalize_decision_context(
            project_id=project_id,
            expected_project_id=expected_project_id,
            decision_kind=decision_kind,
            policy_digest=policy_digest,
            features=features,
        )
        result = self._runtime.lookup(
            project_id=project_id,
            decision_kind=decision_kind,
            normalized=normalized,
            correlation_id=correlation_id,
            policy_digest=policy_digest,
            now=now,
            side_effect_id=side_effect_id,
        )
        if isinstance(result, CodifiedDecision):
            resolution = DecisionResolution(
                decision=result.decision,
                source=DecisionResolutionSource.CODIFIED,
                candidate_digest=result.candidate_digest,
                decision_receipt_digest=result.decision_receipt_digest,
                abstention=None,
                project_id=result.project_id,
                decision_kind=result.decision_kind,
                rollout_stage=result.rollout_stage,
                application_id=result.application_id,
            )
            self._observe(
                started=started,
                project_id=project_id,
                policy_digest=policy_digest,
                decision_kind=decision_kind,
                path=DecisionResolutionPath.EXACT_RULE,
                abstention_reason=None,
                candidate_digest=result.candidate_digest,
                rollout_stage=result.rollout_stage,
            )
            return resolution
        try:
            decision = fallback(result)
        finally:
            self._observe(
                started=started,
                project_id=project_id,
                policy_digest=policy_digest,
                decision_kind=decision_kind,
                path=DecisionResolutionPath.AGENT_FALLBACK,
                abstention_reason=result.reason,
                candidate_digest=result.candidate_digest,
                rollout_stage=None,
            )
        if decision not in DECISION_ACTIONS_V1[decision_kind]:
            raise DecisionResolutionError(
                "agent fallback returned a decision outside the action vocabulary"
            )
        return DecisionResolution(
            decision=decision,
            source=DecisionResolutionSource.AGENT_FALLBACK,
            candidate_digest=result.candidate_digest,
            decision_receipt_digest=None,
            abstention=result,
            project_id=project_id,
            decision_kind=decision_kind,
        )

    def _tick(self) -> int | None:
        try:
            value = self._monotonic_ns()
        except Exception:
            return None
        return value if type(value) is int and value >= 0 else None

    def _observe(
        self,
        *,
        started: int | None,
        project_id: str,
        policy_digest: str,
        decision_kind: DecisionKind,
        path: DecisionResolutionPath,
        abstention_reason: FallbackReason | None,
        candidate_digest: str | None,
        rollout_stage: RolloutStage | None,
    ) -> None:
        observer = self._observability
        if observer is None or started is None:
            return
        finished = self._tick()
        if finished is None or finished < started:
            return
        try:
            observer.record_resolution(
                project_id=project_id,
                policy_digest=policy_digest,
                decision_kind=decision_kind,
                path=path,
                abstention_reason=abstention_reason,
                candidate_digest=candidate_digest,
                rollout_stage=rollout_stage,
                latency_ns=finished - started,
            )
        except Exception:
            return

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
        """Persist terminal feedback for one previously issued exact hit."""
        return self._runtime.record_application_outcome(
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


class DecisionCodificationIntegrationError(ValueError):
    """Raised when an opt-in application binding is incomplete or invalid."""


class DecisionCodificationAdapter:
    """Bind verified replay analysis and exact resolution to one app scope.

    Constructing this adapter is the opt-in.  The binding freezes the expected
    project and policy digest so application callers cannot silently widen the
    analyzer or runtime scope.  The default daemon composition does not create
    an adapter and therefore preserves the existing agent path unchanged.
    """

    def __init__(
        self,
        *,
        bundle_reader: VerifiedBundleReader,
        runtime: DecisionRuntime,
        project_id: str,
        policy_digest: str,
        floors: MiningFloors | None = None,
        decision_recorder: DecisionOutcomeRecorder | None = None,
        observability: DecisionReuseObservability | None = None,
    ) -> None:
        """Validate and bind the existing replay and runtime capabilities."""
        try:
            bound_project_id = _PROJECT_ID_ADAPTER.validate_python(
                project_id,
                strict=True,
            )
        except ValidationError as exc:
            raise DecisionCodificationIntegrationError(
                "decision codification requires a valid project binding"
            ) from exc
        try:
            bound_policy_digest = _POLICY_DIGEST_ADAPTER.validate_python(
                policy_digest,
                strict=True,
            )
        except ValidationError as exc:
            raise DecisionCodificationIntegrationError(
                "decision codification requires a valid policy digest"
            ) from exc
        if not callable(getattr(bundle_reader, "read_verified", None)):
            raise DecisionCodificationIntegrationError(
                "decision codification requires a verified bundle reader"
            )
        if not isinstance(runtime, DecisionRuntime):
            raise DecisionCodificationIntegrationError(
                "decision codification requires a DecisionRuntime"
            )
        if decision_recorder is not None and not isinstance(
            decision_recorder, DecisionOutcomeRecorder
        ):
            raise DecisionCodificationIntegrationError(
                "decision codification recorder is invalid"
            )
        if observability is not None and not isinstance(
            observability, DecisionReuseObservability
        ):
            raise DecisionCodificationIntegrationError(
                "decision codification observability is invalid"
            )

        self._project_id = bound_project_id
        self._policy_digest = bound_policy_digest
        self._analyzer = DecisionLogAnalyzer(bundle_reader, floors=floors)
        self._resolver = DecisionResolver(runtime, observability=observability)
        self._decision_recorder = decision_recorder

    @property
    def project_id(self) -> str:
        """Return the immutable project scope for this application binding."""
        return self._project_id

    @property
    def policy_digest(self) -> str:
        """Return the immutable current-policy scope for this binding."""
        return self._policy_digest

    def analyze(
        self,
        run_ids: Iterable[str],
        *,
        training_recipe_digest: str,
        dependency_lock_digest: str,
        created_at: datetime,
        expires_at: datetime,
        maximum_use_count: int,
        estimated_tokens_per_call: int = 0,
    ) -> DecisionAnalysis:
        """Analyze only verified runs inside the bound application scope."""
        return self._analyzer.analyze(
            run_ids,
            project_id=self._project_id,
            current_policy_digest=self._policy_digest,
            training_recipe_digest=training_recipe_digest,
            dependency_lock_digest=dependency_lock_digest,
            created_at=created_at,
            expires_at=expires_at,
            maximum_use_count=maximum_use_count,
            estimated_tokens_per_call=estimated_tokens_per_call,
        )

    def resolve(
        self,
        *,
        project_id: str,
        decision_kind: DecisionKind,
        features: object,
        correlation_id: str,
        now: datetime,
        side_effect_id: str,
        fallback: AgentFallback,
    ) -> DecisionResolution:
        """Resolve one exact decision or invoke the supplied fallback once."""
        return self._resolver.resolve(
            project_id=project_id,
            expected_project_id=self._project_id,
            decision_kind=decision_kind,
            policy_digest=self._policy_digest,
            features=features,
            correlation_id=correlation_id,
            now=now,
            side_effect_id=side_effect_id,
            fallback=fallback,
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
        """Record bounded feedback without permitting cross-project widening."""
        if project_id != self._project_id:
            raise DecisionCodificationIntegrationError(
                "decision outcome project scope does not match adapter binding"
            )
        return self._resolver.record_application_outcome(
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

    def record_agent_decision_outcome(
        self,
        *,
        project_id: str,
        decision_kind: DecisionKind,
        features: object,
        decision: str,
        capture_id: str,
        root_task_id: str,
        outcome: VerifiedOutcome,
        occurred_at: datetime,
    ) -> DecisionCaptureReceipt | None:
        """Capture one terminal fallback decision when signed capture is configured."""
        if project_id != self._project_id:
            raise DecisionCodificationIntegrationError(
                "agent decision project scope does not match adapter binding"
            )
        if self._decision_recorder is None:
            return None
        return self._decision_recorder.capture(
            capture_id=capture_id,
            root_task_id=root_task_id,
            decision_kind=decision_kind,
            features=features,
            decision=decision,
            outcome=outcome,
            occurred_at=occurred_at,
        )

    def agent_decision_coordination_key(
        self,
        *,
        project_id: str,
        decision_kind: DecisionKind,
        capture_id: str,
        root_task_id: str,
    ) -> str | None:
        """Return an opaque lease key only when signed capture is configured."""
        if project_id != self._project_id:
            raise DecisionCodificationIntegrationError(
                "agent decision project scope does not match adapter binding"
            )
        if self._decision_recorder is None:
            return None
        return self._decision_recorder.coordination_key(
            capture_id=capture_id,
            root_task_id=root_task_id,
            decision_kind=decision_kind,
        )


__all__ = [
    "MAX_ANALYSIS_BUNDLES",
    "MAX_ANALYSIS_EVENTS",
    "AgentFallback",
    "AnalysisRejectionReason",
    "DecisionAnalysis",
    "DecisionAnalysisError",
    "DecisionCandidate",
    "DecisionCodificationAdapter",
    "DecisionCodificationIntegrationError",
    "DecisionLogAnalyzer",
    "DecisionResolution",
    "DecisionResolutionError",
    "DecisionResolutionSource",
    "DecisionResolver",
    "VerifiedBundleReader",
]
