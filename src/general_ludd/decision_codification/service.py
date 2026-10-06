"""Verified-log analysis and deterministic decision resolution orchestration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

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
)
from general_ludd.decision_codification.runtime import CodifiedDecision, DecisionRuntime
from general_ludd.decision_codification.schema import (
    DECISION_ACTIONS_V1,
    DecisionAbstentionV1,
    DecisionKind,
    DecisionRuleBundleV1,
    EvaluationReportV1,
    NormalizationRefusalV1,
    VerifiedDecisionSourceV1,
    canonical_sha256,
)
from general_ludd.replay.store import ReplayStoreError, VerifiedBundle

MAX_ANALYSIS_BUNDLES = 10_000
MAX_ANALYSIS_EVENTS = 100_000


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
            for event in verified.events:
                normalized = normalize_verified_decision_event(
                    event,
                    source=source,
                    expected_project_id=project_id,
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


AgentFallback = Callable[[DecisionAbstentionV1], str]


class DecisionResolver:
    """Use exact codified rules first and invoke an agent only after abstention."""

    def __init__(self, runtime: DecisionRuntime) -> None:
        """Bind the local deterministic runtime; no model adapter is retained."""
        self._runtime = runtime

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
            return DecisionResolution(
                decision=result.decision,
                source=DecisionResolutionSource.CODIFIED,
                candidate_digest=result.candidate_digest,
                decision_receipt_digest=result.decision_receipt_digest,
                abstention=None,
            )
        decision = fallback(result)
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
        )


__all__ = [
    "MAX_ANALYSIS_BUNDLES",
    "MAX_ANALYSIS_EVENTS",
    "AgentFallback",
    "AnalysisRejectionReason",
    "DecisionAnalysis",
    "DecisionAnalysisError",
    "DecisionCandidate",
    "DecisionLogAnalyzer",
    "DecisionResolution",
    "DecisionResolutionError",
    "DecisionResolutionSource",
    "DecisionResolver",
    "VerifiedBundleReader",
]
