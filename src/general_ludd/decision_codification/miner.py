"""Conservative evidence assessment for offline decision mining."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    VerifiedOutcome,
    canonical_sha256,
)
from general_ludd.decision_codification.similarity import group_similar_envelopes

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_EVIDENCE_RECORDS = 100_000


class MiningRejectionReason(StrEnum):
    """Closed, content-free reasons that evidence cannot become a candidate."""

    EMPTY = "empty"
    MIXED_SCOPE = "mixed_scope"
    ROOT_TASK_CONFLICT = "root_task_conflict"
    EXACT_CONTEXT_CONFLICT = "exact_context_conflict"
    MINIMUM_SUPPORT = "minimum_support"
    ROOT_TASK_DIVERSITY = "root_task_diversity"
    UTC_DAY_DIVERSITY = "utc_day_diversity"
    SOURCE_AGENT_DIVERSITY = "source_agent_diversity"
    MINIMUM_CONFIDENCE = "minimum_confidence"
    WILSON_FLOOR = "wilson_floor"
    SUCCESS_RATE = "success_rate"
    UNSAFE_OUTCOME = "unsafe_outcome"
    RISK_SCOPE = "risk_scope"
    IRREVERSIBLE_ACTION = "irreversible_action"


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """A normalized envelope plus grouping metadata not used as model features."""

    envelope: DecisionEnvelopeV1
    root_task_id: str
    source_agent_id: str | None = None

    def __post_init__(self) -> None:
        """Reject metadata that cannot be persisted as bounded identifiers."""
        if _IDENTIFIER.fullmatch(self.root_task_id) is None:
            raise ValueError("root_task_id must be a bounded identifier")
        if (
            self.source_agent_id is not None
            and _IDENTIFIER.fullmatch(self.source_agent_id) is None
        ):
            raise ValueError("source_agent_id must be a bounded identifier")


@dataclass(frozen=True, slots=True)
class MiningFloors:
    """Conservative v1 support, quality, and diversity thresholds."""

    minimum_support: int = 16
    minimum_root_tasks: int = 8
    minimum_utc_days: int = 3
    minimum_source_agents: int = 2
    minimum_confidence: float = 0.95
    minimum_wilson_lower_bound: float = 0.80
    minimum_success_rate: float = 0.99

    def __post_init__(self) -> None:
        """Reject unsafe or nonsensical mining thresholds."""
        counts = (
            self.minimum_support,
            self.minimum_root_tasks,
            self.minimum_utc_days,
            self.minimum_source_agents,
        )
        if min(counts) < 1:
            raise ValueError("count floors must be positive")
        rates = (
            self.minimum_confidence,
            self.minimum_wilson_lower_bound,
            self.minimum_success_rate,
        )
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in rates):
            raise ValueError("rate floors must be finite values in [0, 1]")
        safe_counts = (16, 8, 3, 2)
        safe_rates = (0.95, 0.80, 0.99)
        if any(value < safe for value, safe in zip(counts, safe_counts, strict=True)):
            raise ValueError("v1 count safety floors cannot be lowered")
        if any(value < safe for value, safe in zip(rates, safe_rates, strict=True)):
            raise ValueError("v1 rate safety floors cannot be lowered")


@dataclass(frozen=True, slots=True)
class MiningAssessment:
    """Content-free statistics and the closed reasons for rejection."""

    accepted: bool
    reasons: tuple[MiningRejectionReason, ...]
    support: int
    root_task_count: int
    utc_day_count: int
    source_agent_count: int
    majority_decision: str | None
    confidence: float
    wilson_lower_bound: float
    success_rate: float


@dataclass(frozen=True, slots=True)
class CandidateGroup:
    """One similarity cluster that satisfies every configured mining floor."""

    cluster_digest: str
    records: tuple[DecisionEvidence, ...]
    assessment: MiningAssessment


def wilson_lower_bound(successes: int, total: int, *, z: float = 1.959963984540054) -> float:
    """Return the Wilson score lower bound for a binomial proportion."""
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("Wilson counts must satisfy 0 <= successes <= total")
    if total == 0:
        return 0.0
    proportion = successes / total
    squared = z * z
    denominator = 1.0 + squared / total
    centre = proportion + squared / (2.0 * total)
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + squared / (4.0 * total)) / total
    )
    return (centre - margin) / denominator


def _record_order(record: DecisionEvidence) -> tuple[object, ...]:
    return (
        record.envelope.occurred_at,
        record.root_task_id,
        record.envelope.envelope_id,
    )


def canonicalize_evidence(
    records: Iterable[DecisionEvidence],
) -> tuple[DecisionEvidence, ...]:
    """Count each root task once and return canonical chronological evidence."""
    by_root: dict[str, list[DecisionEvidence]] = defaultdict(list)
    for index, record in enumerate(records, start=1):
        if index > MAX_EVIDENCE_RECORDS:
            raise ValueError("decision evidence exceeds the hard evidence record limit")
        by_root[record.root_task_id].append(record)
    selected = [min(group, key=_record_order) for group in by_root.values()]
    return tuple(sorted(selected, key=_record_order))


def canonical_corpus_digest(records: Iterable[DecisionEvidence]) -> str:
    """Bind canonical evidence membership, ordering, and non-feature metadata."""
    canonical = canonicalize_evidence(records)
    return canonical_sha256({
        "schema": "gludd.decision-corpus/v1",
        "members": [
            {
                "envelope_id": record.envelope.envelope_id,
                "root_task_id": record.root_task_id,
                "source_agent_id": record.source_agent_id,
            }
            for record in canonical
        ],
    })


def _scope_key(record: DecisionEvidence) -> tuple[object, ...]:
    envelope = record.envelope
    return (
        envelope.project_id,
        envelope.decision_kind,
        envelope.feature_schema,
        envelope.policy_digest,
        tuple(envelope.exact_guards.items()),
    )


def _context_digest(record: DecisionEvidence) -> str:
    envelope = record.envelope
    return canonical_sha256({
        "exact_guards": envelope.exact_guards,
        "features": envelope.features,
    })


def _root_conflict(records: Sequence[DecisionEvidence]) -> bool:
    signatures: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        signatures[record.root_task_id].add(
            (record.envelope.envelope_id, record.envelope.decision)
        )
    return any(
        len({decision for _, decision in values}) > 1
        for values in signatures.values()
    )


def assess_evidence(
    records: Iterable[DecisionEvidence],
    *,
    floors: MiningFloors | None = None,
) -> MiningAssessment:
    """Apply all default-safe candidate floors to independent root tasks."""
    policy = floors or MiningFloors()
    original = tuple(records)
    canonical = canonicalize_evidence(original)
    reasons: list[MiningRejectionReason] = []
    if not canonical:
        reasons.append(MiningRejectionReason.EMPTY)

    if len({_scope_key(record) for record in canonical}) > 1:
        reasons.append(MiningRejectionReason.MIXED_SCOPE)
    if _root_conflict(original):
        reasons.append(MiningRejectionReason.ROOT_TASK_CONFLICT)

    context_decisions: dict[str, set[str]] = defaultdict(set)
    for record in canonical:
        context_decisions[_context_digest(record)].add(record.envelope.decision)
    if any(len(decisions) > 1 for decisions in context_decisions.values()):
        reasons.append(MiningRejectionReason.EXACT_CONTEXT_CONFLICT)

    support = len(canonical)
    counts = Counter(record.envelope.decision for record in canonical)
    majority_decision: str | None = None
    majority_count = 0
    if counts:
        majority_decision, majority_count = min(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    confidence = majority_count / support if support else 0.0
    wilson = wilson_lower_bound(majority_count, support)
    successes = sum(
        record.envelope.verified_outcome is VerifiedOutcome.SUCCESS
        for record in canonical
    )
    success_rate = successes / support if support else 0.0
    root_count = len({record.root_task_id for record in canonical})
    utc_days = len({record.envelope.occurred_at.date() for record in canonical})
    agents = {
        record.source_agent_id
        for record in canonical
        if record.source_agent_id is not None
    }

    if support < policy.minimum_support:
        reasons.append(MiningRejectionReason.MINIMUM_SUPPORT)
    if root_count < policy.minimum_root_tasks:
        reasons.append(MiningRejectionReason.ROOT_TASK_DIVERSITY)
    if utc_days < policy.minimum_utc_days:
        reasons.append(MiningRejectionReason.UTC_DAY_DIVERSITY)
    if agents and len(agents) < policy.minimum_source_agents:
        reasons.append(MiningRejectionReason.SOURCE_AGENT_DIVERSITY)
    if confidence < policy.minimum_confidence:
        reasons.append(MiningRejectionReason.MINIMUM_CONFIDENCE)
    if wilson < policy.minimum_wilson_lower_bound:
        reasons.append(MiningRejectionReason.WILSON_FLOOR)
    if success_rate < policy.minimum_success_rate:
        reasons.append(MiningRejectionReason.SUCCESS_RATE)
    if any(
        record.envelope.verified_outcome is VerifiedOutcome.UNSAFE
        for record in canonical
    ):
        reasons.append(MiningRejectionReason.UNSAFE_OUTCOME)
    if any(
        record.envelope.exact_guards.get("risk_band") not in {"low", "medium"}
        for record in canonical
    ):
        reasons.append(MiningRejectionReason.RISK_SCOPE)
    if any(record.envelope.features.get("reversible") is not True for record in canonical):
        reasons.append(MiningRejectionReason.IRREVERSIBLE_ACTION)

    unique_reasons = tuple(dict.fromkeys(reasons))
    return MiningAssessment(
        accepted=not unique_reasons,
        reasons=unique_reasons,
        support=support,
        root_task_count=root_count,
        utc_day_count=utc_days,
        source_agent_count=len(agents),
        majority_decision=majority_decision,
        confidence=confidence,
        wilson_lower_bound=wilson,
        success_rate=success_rate,
    )


def mine_candidate_groups(
    records: Iterable[DecisionEvidence],
    *,
    floors: MiningFloors | None = None,
    score_cutoff: float = 92.0,
    max_unique_signatures: int = 5_000,
) -> tuple[CandidateGroup, ...]:
    """Return only complete-link groups that satisfy every safety floor."""
    policy = floors or MiningFloors()
    canonical = canonicalize_evidence(records)
    by_id = {record.envelope.envelope_id: record for record in canonical}
    clusters = group_similar_envelopes(
        (record.envelope for record in canonical),
        score_cutoff=score_cutoff,
        max_unique_signatures=max_unique_signatures,
    )
    candidates: list[CandidateGroup] = []
    for cluster in clusters:
        members = tuple(by_id[member_id] for member_id in cluster.member_ids)
        assessment = assess_evidence(members, floors=policy)
        if assessment.accepted:
            candidates.append(
                CandidateGroup(
                    cluster_digest=cluster.cluster_digest,
                    records=members,
                    assessment=assessment,
                )
            )
    return tuple(sorted(candidates, key=lambda item: item.cluster_digest))


__all__ = [
    "MAX_EVIDENCE_RECORDS",
    "CandidateGroup",
    "DecisionEvidence",
    "MiningAssessment",
    "MiningFloors",
    "MiningRejectionReason",
    "assess_evidence",
    "canonical_corpus_digest",
    "canonicalize_evidence",
    "mine_candidate_groups",
    "wilson_lower_bound",
]
