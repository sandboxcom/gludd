"""Chronological splitting and deterministic offline rule replay."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from general_ludd.decision_codification.miner import (
    DecisionEvidence,
    MiningFloors,
    canonical_corpus_digest,
    canonicalize_evidence,
    wilson_lower_bound,
)
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    EvaluationReportV1,
    LeafEvaluationV1,
    VerifiedOutcome,
    canonical_decision_json,
)

_RUNTIME_KNOWN_FEATURE = "codification_known"


class EvaluationError(ValueError):
    """Raised when leakage-free deterministic evaluation is impossible."""


class ReplayDisposition(StrEnum):
    """Closed offline replay outcomes used to build bounded counters."""

    MATCH = "match"
    ABSTAIN = "abstain"
    SCOPE_MISS = "scope_miss"
    UNKNOWN_FEATURE = "unknown_feature"
    POLICY_MISMATCH = "policy_mismatch"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class EvidenceSplit:
    """Chronological, mutually exclusive root-task partitions."""

    training: tuple[DecisionEvidence, ...]
    validation: tuple[DecisionEvidence, ...]
    holdout: tuple[DecisionEvidence, ...]
    corpus_digest: str


@dataclass(frozen=True, slots=True)
class RuleApplication:
    """One content-free result from the exported rule adapter."""

    disposition: ReplayDisposition
    leaf_id: str | None = None
    decision: str | None = None


def chronological_root_task_split(
    records: Iterable[DecisionEvidence],
) -> EvidenceSplit:
    """Split oldest 70%, next 15%, and newest 15% without task leakage."""
    independent = canonicalize_evidence(records)
    task_count = len(independent)
    training_end = math.floor(task_count * 0.70)
    validation_end = math.floor(task_count * 0.85)
    if training_end < 1 or validation_end <= training_end:
        raise EvaluationError("chronological split requires a non-empty validation set")
    if task_count - validation_end < 5:
        raise EvaluationError("chronological split requires at least five holdout root tasks")

    training = independent[:training_end]
    validation = independent[training_end:validation_end]
    holdout = independent[validation_end:]
    return EvidenceSplit(
        training=training,
        validation=validation,
        holdout=holdout,
        corpus_digest=canonical_corpus_digest(independent),
    )


def apply_exported_rule(
    bundle: DecisionRuleBundleV1,
    envelope: DecisionEnvelopeV1,
    *,
    current_policy_digest: str,
) -> RuleApplication:
    """Evaluate strict exported nodes without consulting the fitted estimator."""
    if current_policy_digest not in bundle.policy_compatibility:
        return RuleApplication(ReplayDisposition.POLICY_MISMATCH)
    if envelope.policy_digest not in bundle.policy_compatibility:
        return RuleApplication(ReplayDisposition.POLICY_MISMATCH)
    if (
        envelope.project_id != bundle.project_id
        or envelope.decision_kind is not bundle.decision_kind
        or envelope.feature_schema != bundle.feature_schema
        or envelope.exact_guards.get("risk_band") != bundle.risk_scope
    ):
        return RuleApplication(ReplayDisposition.SCOPE_MISS)

    nodes = {node.node_id: node for node in bundle.nodes}
    leaves = {leaf.leaf_id: leaf for leaf in bundle.leaves}
    identifier = bundle.root_id
    visited: set[str] = set()
    while identifier in nodes:
        if identifier in visited:
            return RuleApplication(ReplayDisposition.CONFLICT)
        visited.add(identifier)
        node = nodes[identifier]
        if node.feature_id == _RUNTIME_KNOWN_FEATURE:
            value: object = True
        elif node.feature_id in envelope.features:
            value = envelope.features[node.feature_id]
        else:
            return RuleApplication(ReplayDisposition.UNKNOWN_FEATURE)
        predicate = value == node.value
        if node.operator == "not_eq":
            predicate = not predicate
        identifier = node.match_id if predicate else node.miss_id

    leaf = leaves.get(identifier)
    if leaf is None:
        return RuleApplication(ReplayDisposition.CONFLICT)
    if leaf.abstain:
        return RuleApplication(
            ReplayDisposition.ABSTAIN,
            leaf_id=leaf.leaf_id,
        )
    return RuleApplication(
        ReplayDisposition.MATCH,
        leaf_id=leaf.leaf_id,
        decision=leaf.decision,
    )


def _build_report(
    bundle: DecisionRuleBundleV1,
    records: Sequence[DecisionEvidence],
    *,
    created_at: datetime,
    current_policy_digest: str,
    corpus_digest: str,
    estimated_tokens_per_call: int,
) -> EvaluationReportV1:
    exact_match_count = 0
    abstention_count = 0
    correct_count = 0
    false_automation_count = 0
    conflict_count = 0
    unknown_feature_count = 0
    policy_mismatch_count = 0
    matched_by_leaf: dict[str, list[bool]] = defaultdict(list)

    for record in records:
        application = apply_exported_rule(
            bundle,
            record.envelope,
            current_policy_digest=current_policy_digest,
        )
        if application.disposition is ReplayDisposition.MATCH:
            exact_match_count += 1
            correct = application.decision == record.envelope.decision
            correct_count += int(correct)
            false_automation_count += int(not correct)
            if application.leaf_id is not None:
                matched_by_leaf[application.leaf_id].append(correct)
        else:
            abstention_count += 1
            conflict_count += int(application.disposition is ReplayDisposition.CONFLICT)
            unknown_feature_count += int(
                application.disposition is ReplayDisposition.UNKNOWN_FEATURE
            )
            policy_mismatch_count += int(
                application.disposition is ReplayDisposition.POLICY_MISMATCH
            )

    precision = correct_count / exact_match_count if exact_match_count else 0.0
    agents = {
        record.source_agent_id
        for record in records
        if record.source_agent_id is not None
    }
    leaf_results = tuple(
        LeafEvaluationV1(
            leaf_id=leaf.leaf_id,
            support=len(matched_by_leaf[leaf.leaf_id]),
            confidence=(
                sum(matched_by_leaf[leaf.leaf_id])
                / len(matched_by_leaf[leaf.leaf_id])
                if matched_by_leaf[leaf.leaf_id]
                else 0.0
            ),
        )
        for leaf in bundle.leaves
        if not leaf.abstain
    )
    return EvaluationReportV1.create(
        schema="gludd.decision-evaluation-report/v1",
        candidate_digest=bundle.candidate_digest,
        corpus_digest=corpus_digest,
        project_id=bundle.project_id,
        decision_kind=bundle.decision_kind,
        created_at=created_at,
        support_count=len(records),
        root_task_count=len({record.root_task_id for record in records}),
        utc_day_count=len({record.envelope.occurred_at.date() for record in records}),
        source_agent_count=len(agents),
        exact_match_count=exact_match_count,
        abstention_count=abstention_count,
        precision=precision,
        leaf_results=leaf_results,
        false_automation_count=false_automation_count,
        conflict_count=conflict_count,
        unknown_feature_count=unknown_feature_count,
        policy_mismatch_count=policy_mismatch_count,
        verified_failure_count=sum(
            record.envelope.verified_outcome is VerifiedOutcome.FAILURE
            for record in records
        ),
        rollback_count=sum(
            record.envelope.verified_outcome is VerifiedOutcome.REVERTED
            for record in records
        ),
        safety_violation_count=sum(
            record.envelope.verified_outcome is VerifiedOutcome.UNSAFE
            for record in records
        ),
        estimated_agent_calls_avoided=exact_match_count,
        estimated_tokens_avoided=exact_match_count * estimated_tokens_per_call,
        historical_policy_digest=bundle.policy_compatibility[0],
        current_policy_digest=current_policy_digest,
        deterministic_replay_runs=2,
    )


def evaluate_candidate(
    bundle: DecisionRuleBundleV1,
    records: Iterable[DecisionEvidence],
    *,
    created_at: datetime,
    current_policy_digest: str,
    corpus_digest: str | None = None,
    estimated_tokens_per_call: int = 0,
) -> EvaluationReportV1:
    """Replay a candidate twice and reject any byte-level nondeterminism."""
    if estimated_tokens_per_call < 0:
        raise EvaluationError("estimated_tokens_per_call must be non-negative")
    independent = canonicalize_evidence(records)
    eligible = tuple(
        record
        for record in independent
        if record.envelope.verified_outcome is not VerifiedOutcome.UNKNOWN
    )
    if len(eligible) < 5:
        raise EvaluationError("evaluation requires at least five eligible root tasks")
    digest = corpus_digest or canonical_corpus_digest(eligible)
    first = _build_report(
        bundle,
        eligible,
        created_at=created_at,
        current_policy_digest=current_policy_digest,
        corpus_digest=digest,
        estimated_tokens_per_call=estimated_tokens_per_call,
    )
    second = _build_report(
        bundle,
        tuple(reversed(eligible)),
        created_at=created_at,
        current_policy_digest=current_policy_digest,
        corpus_digest=digest,
        estimated_tokens_per_call=estimated_tokens_per_call,
    )
    if canonical_decision_json(first) != canonical_decision_json(second):
        raise EvaluationError("evaluation report changed across deterministic replay runs")
    return first


def _leaf_meets_floors(leaf: DecisionRuleLeafV1, floors: MiningFloors) -> bool:
    if leaf.abstain:
        return True
    success_rate = (
        leaf.outcome_counts.success / leaf.support if leaf.support else 0.0
    )
    decision_successes = round(leaf.confidence * leaf.support)
    return (
        leaf.support >= floors.minimum_support
        and leaf.confidence >= floors.minimum_confidence
        and wilson_lower_bound(decision_successes, leaf.support)
        >= floors.minimum_wilson_lower_bound
        and success_rate >= floors.minimum_success_rate
        and leaf.outcome_counts.unsafe == 0
    )


def activation_eligible(
    report: EvaluationReportV1,
    bundle: DecisionRuleBundleV1,
    *,
    floors: MiningFloors | None = None,
    minimum_precision: float = 0.99,
) -> bool:
    """Apply the zero-false-automation activation gate."""
    if not math.isfinite(minimum_precision) or not 0.99 <= minimum_precision <= 1.0:
        return False
    policy = floors or MiningFloors()
    terminal_failure_rate = (
        (
            report.verified_failure_count
            + report.rollback_count
            + report.safety_violation_count
        )
        / report.support_count
        if report.support_count
        else 1.0
    )
    evaluated_leaves_are_confident = all(
        result.support > 0 and result.confidence >= policy.minimum_confidence
        for result in report.leaf_results
    )
    return (
        report.candidate_digest == bundle.candidate_digest
        and report.corpus_digest == bundle.corpus_digest
        and report.support_count >= 5
        and report.precision >= minimum_precision
        and report.exact_match_count > 0
        and report.false_automation_count == 0
        and report.conflict_count == 0
        and report.safety_violation_count == 0
        and report.policy_mismatch_count == 0
        and terminal_failure_rate <= 1.0 - policy.minimum_success_rate
        and evaluated_leaves_are_confident
        and all(_leaf_meets_floors(leaf, policy) for leaf in bundle.leaves)
    )


__all__ = [
    "EvaluationError",
    "EvidenceSplit",
    "ReplayDisposition",
    "RuleApplication",
    "activation_eligible",
    "apply_exported_rule",
    "chronological_root_task_split",
    "evaluate_candidate",
]
