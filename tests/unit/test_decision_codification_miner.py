"""Safety-floor tests for deterministic decision mining."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import general_ludd.decision_codification.miner as miner_module
from general_ludd.decision_codification.miner import (
    DecisionEvidence,
    MiningFloors,
    assess_evidence,
    canonical_corpus_digest,
    canonicalize_evidence,
    mine_candidate_groups,
    wilson_lower_bound,
)
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    OutcomeEvidenceV1,
    RedactionSummaryV1,
    VerifiedOutcome,
)

pytestmark = pytest.mark.filterwarnings("error")

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
START = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(
    index: int,
    *,
    decision: str = "approve",
    outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS,
    root_task_id: str | None = None,
    source_agent_id: str | None = None,
    feature: str = "stable",
) -> DecisionEvidence:
    event_digest = f"sha256:{index + 1:064x}"
    envelope = DecisionEnvelopeV1.create(
        schema="gludd.decision-envelope/v1",
        source_run_id=f"run-{index}",
        source_event_digest=event_digest,
        source_bundle_digest=SHA_A,
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_B,
        policy_digest=SHA_C,
        occurred_at=START + timedelta(days=index % 3, minutes=index),
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review",
            "risk_band": "low",
        },
        features={"pattern": feature, "reversible": True},
        decision=decision,
        verified_outcome=outcome,
        outcome_evidence=OutcomeEvidenceV1(
            decision_event_digest=event_digest,
            outcome=outcome,
            terminal_event_ids=(f"terminal-{index}",),
            gate_digests=(SHA_A,),
            status_digests=(),
        ),
        redaction=RedactionSummaryV1(count=0, kinds=()),
    )
    return DecisionEvidence(
        envelope=envelope,
        root_task_id=root_task_id or f"root-{index}",
        source_agent_id=(
            source_agent_id if source_agent_id is not None else f"agent-{index % 2}"
        ),
    )


def _passing_evidence() -> list[DecisionEvidence]:
    return [_evidence(index) for index in range(16)]


def _replace_envelope(
    record: DecisionEvidence, **updates: object
) -> DecisionEvidence:
    payload = record.envelope.model_dump(
        mode="python", by_alias=True, exclude={"envelope_id"}
    )
    payload.update(updates)
    return DecisionEvidence(
        envelope=DecisionEnvelopeV1.create(**payload),
        root_task_id=record.root_task_id,
        source_agent_id=record.source_agent_id,
    )


def test_default_support_confidence_wilson_and_diversity_floors_pass() -> None:
    assessment = assess_evidence(_passing_evidence())

    assert assessment.accepted
    assert assessment.reasons == ()
    assert assessment.support == 16
    assert assessment.root_task_count == 16
    assert assessment.utc_day_count == 3
    assert assessment.source_agent_count == 2
    assert assessment.confidence == 1.0
    assert assessment.success_rate == 1.0
    assert assessment.wilson_lower_bound == pytest.approx(0.806, abs=0.001)
    assert wilson_lower_bound(16, 16) == assessment.wilson_lower_bound


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        (_passing_evidence()[:15], "minimum_support"),
        (
            [
                *[_evidence(index) for index in range(15)],
                _evidence(15, decision="reject", feature="different"),
            ],
            "minimum_confidence",
        ),
        (
            [
                *[_evidence(index) for index in range(15)],
                _evidence(15, outcome=VerifiedOutcome.UNSAFE),
            ],
            "unsafe_outcome",
        ),
        (
            [_evidence(index, source_agent_id="only-agent") for index in range(16)],
            "source_agent_diversity",
        ),
    ],
)
def test_floor_failures_are_content_free(
    records: list[DecisionEvidence], reason: str
) -> None:
    assessment = assess_evidence(records)
    assert not assessment.accepted
    assert reason in assessment.reasons


def test_missing_agent_identity_does_not_invent_a_diversity_failure() -> None:
    records = [
        DecisionEvidence(
            envelope=item.envelope,
            root_task_id=item.root_task_id,
            source_agent_id=None,
        )
        for item in _passing_evidence()
    ]
    assert assess_evidence(records).accepted


def test_root_task_deduplication_and_context_conflict_fail_closed() -> None:
    records = _passing_evidence()
    duplicate = _evidence(100, root_task_id="root-0")
    canonical = canonicalize_evidence([*records, duplicate])
    assert len(canonical) == 16
    assert assess_evidence([*records, duplicate]).support == 16

    conflict = _evidence(101, decision="reject", feature="stable")
    assessment = assess_evidence([*records, conflict])
    assert "exact_context_conflict" in assessment.reasons


def test_evidence_canonicalization_is_hard_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(miner_module, "MAX_EVIDENCE_RECORDS", 2)

    with pytest.raises(ValueError, match="evidence record limit"):
        canonicalize_evidence([_evidence(index) for index in range(3)])


def test_corpus_and_candidate_outputs_are_permutation_stable() -> None:
    records = _passing_evidence()
    assert canonical_corpus_digest(records) == canonical_corpus_digest(
        list(reversed(records))
    )

    changed_metadata = [
        *records[:-1],
        DecisionEvidence(
            envelope=records[-1].envelope,
            root_task_id="different-root",
            source_agent_id=records[-1].source_agent_id,
        ),
    ]
    assert canonical_corpus_digest(records) != canonical_corpus_digest(changed_metadata)

    candidates = mine_candidate_groups(records, floors=MiningFloors())
    reversed_candidates = mine_candidate_groups(
        list(reversed(records)), floors=MiningFloors()
    )
    assert [candidate.cluster_digest for candidate in candidates] == [
        candidate.cluster_digest for candidate in reversed_candidates
    ]
    assert len(candidates) == 1
    assert candidates[0].assessment.accepted


def test_closed_floor_validation_and_empty_assessment() -> None:
    with pytest.raises(ValueError, match="root_task_id"):
        DecisionEvidence(_evidence(0).envelope, "../unsafe")
    with pytest.raises(ValueError, match="source_agent_id"):
        DecisionEvidence(_evidence(0).envelope, "root", "agent with spaces")
    with pytest.raises(ValueError, match="count floors"):
        MiningFloors(minimum_support=0)
    with pytest.raises(ValueError, match="rate floors"):
        MiningFloors(minimum_confidence=float("nan"))
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_support=15)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_root_tasks=7)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_utc_days=2)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_source_agents=1)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_confidence=0.94)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_wilson_lower_bound=0.79)
    with pytest.raises(ValueError, match="cannot be lowered"):
        MiningFloors(minimum_success_rate=0.98)
    with pytest.raises(ValueError, match="Wilson counts"):
        wilson_lower_bound(2, 1)
    assert wilson_lower_bound(0, 0) == 0.0

    empty = assess_evidence([])
    assert not empty.accepted
    assert "empty" in empty.reasons
    assert empty.majority_decision is None
    assert canonical_corpus_digest([]).startswith("sha256:")


def test_wilson_day_scope_risk_and_reversibility_fail_closed() -> None:
    wilson_records = [_evidence(index) for index in range(20)]
    wilson_records[-1] = _evidence(
        19, decision="reject", feature="different-context"
    )
    wilson = assess_evidence(wilson_records)
    assert wilson.confidence == 0.95
    assert "wilson_floor" in wilson.reasons

    one_day = [
        _replace_envelope(record, occurred_at=START + timedelta(minutes=index))
        for index, record in enumerate(_passing_evidence())
    ]
    assert "utc_day_diversity" in assess_evidence(one_day).reasons

    mixed = _passing_evidence()
    mixed[-1] = _replace_envelope(mixed[-1], project_id="project-2")
    assert "mixed_scope" in assess_evidence(mixed).reasons

    high_risk = _passing_evidence()
    high_guards = dict(high_risk[-1].envelope.exact_guards)
    high_guards["risk_band"] = "high"
    high_risk[-1] = _replace_envelope(high_risk[-1], exact_guards=high_guards)
    assert "risk_scope" in assess_evidence(high_risk).reasons

    irreversible = _passing_evidence()
    features = dict(irreversible[-1].envelope.features)
    features["reversible"] = False
    irreversible[-1] = _replace_envelope(irreversible[-1], features=features)
    assert "irreversible_action" in assess_evidence(irreversible).reasons


def test_failure_rate_root_conflict_and_rejected_mining_group() -> None:
    records = _passing_evidence()
    failed = _evidence(15, outcome=VerifiedOutcome.FAILURE)
    assessment = assess_evidence([*records[:-1], failed])
    assert "success_rate" in assessment.reasons

    root_conflict = _evidence(100, decision="reject", root_task_id="root-0")
    assessment = assess_evidence([*records, root_conflict])
    assert "root_task_conflict" in assessment.reasons

    assert mine_candidate_groups(records[:15]) == ()
