"""Leakage-free offline replay tests for exported decision rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import general_ludd.decision_codification.evaluate as evaluate_module
from general_ludd.decision_codification.evaluate import (
    EvaluationError,
    ReplayDisposition,
    RuleApplication,
    activation_eligible,
    apply_exported_rule,
    chronological_root_task_split,
    evaluate_candidate,
)
from general_ludd.decision_codification.export import train_and_export_tree
from general_ludd.decision_codification.miner import DecisionEvidence
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    DecisionRuleBundleV1,
    LeafEvaluationV1,
    OutcomeCountsV1,
    OutcomeEvidenceV1,
    RedactionSummaryV1,
    VerifiedOutcome,
    canonical_decision_json,
)

pytestmark = pytest.mark.filterwarnings("error")

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64
START = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(
    index: int,
    *,
    wrong_decision: bool = False,
    missing_route: bool = False,
    policy_digest: str = SHA_C,
    outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS,
) -> DecisionEvidence:
    route = "left" if index % 2 == 0 else "right"
    decision = "approve" if route == "left" else "reject"
    if wrong_decision:
        decision = "reject" if decision == "approve" else "approve"
    event_digest = f"sha256:{index + 1:064x}"
    features: dict[str, str | bool] = {"reversible": True}
    if not missing_route:
        features["route_hint"] = route
    envelope = DecisionEnvelopeV1.create(
        schema="gludd.decision-envelope/v1",
        source_run_id=f"run-{index}",
        source_event_digest=event_digest,
        source_bundle_digest=SHA_A,
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_B,
        policy_digest=policy_digest,
        occurred_at=START + timedelta(days=index, minutes=index),
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review",
            "risk_band": "low",
        },
        features=features,
        decision=decision,
        verified_outcome=outcome,
        outcome_evidence=OutcomeEvidenceV1(
            decision_event_digest=event_digest,
            outcome=outcome,
            terminal_event_ids=(
                () if outcome is VerifiedOutcome.UNKNOWN else (f"terminal-{index}",)
            ),
            gate_digests=(() if outcome is VerifiedOutcome.UNKNOWN else (SHA_A,)),
            status_digests=(),
        ),
        redaction=RedactionSummaryV1(count=0, kinds=()),
    )
    return DecisionEvidence(
        envelope=envelope,
        root_task_id=f"root-{index}",
        source_agent_id=f"agent-{index % 2}",
    )


def _bundle() -> DecisionRuleBundleV1:
    return train_and_export_tree(
        [_evidence(index) for index in range(32)],
        training_recipe_digest=SHA_D,
        dependency_lock_digest=SHA_E,
        evaluator_report_digest=SHA_F,
        created_at=START,
        expires_at=START + timedelta(days=90),
        maximum_use_count=10_000,
    )


def test_chronological_split_is_root_task_grouped_and_leakage_free() -> None:
    records = [_evidence(index) for index in range(40)]
    duplicate = DecisionEvidence(
        envelope=_evidence(100).envelope.model_copy(
            update={"occurred_at": records[0].envelope.occurred_at}
        ),
        root_task_id="root-0",
        source_agent_id="agent-0",
    )
    split = chronological_root_task_split([*reversed(records), duplicate])

    train_roots = {item.root_task_id for item in split.training}
    validation_roots = {item.root_task_id for item in split.validation}
    holdout_roots = {item.root_task_id for item in split.holdout}
    assert not train_roots & validation_roots
    assert not train_roots & holdout_roots
    assert not validation_roots & holdout_roots
    assert len(holdout_roots) >= 5
    assert max(item.envelope.occurred_at for item in split.training) < min(
        item.envelope.occurred_at for item in split.validation
    )

    with pytest.raises(EvaluationError, match="five holdout"):
        chronological_root_task_split(records[:20])
    with pytest.raises(EvaluationError, match="non-empty validation"):
        chronological_root_task_split([])


def test_evaluation_is_order_stable_and_zero_false_automation_passes() -> None:
    bundle = _bundle()
    holdout = [_evidence(index) for index in range(40, 50)]
    report = evaluate_candidate(
        bundle,
        holdout,
        created_at=START + timedelta(days=100),
        current_policy_digest=SHA_C,
        corpus_digest=bundle.corpus_digest,
        estimated_tokens_per_call=250,
    )
    reversed_report = evaluate_candidate(
        bundle,
        list(reversed(holdout)),
        created_at=START + timedelta(days=100),
        current_policy_digest=SHA_C,
        corpus_digest=bundle.corpus_digest,
        estimated_tokens_per_call=250,
    )

    assert canonical_decision_json(report) == canonical_decision_json(reversed_report)
    assert report.precision == 1.0
    assert report.false_automation_count == 0
    assert report.abstention_count == 0
    assert report.estimated_agent_calls_avoided == 10
    assert report.estimated_tokens_avoided == 2_500
    assert activation_eligible(report, bundle)


def test_full_split_digest_binds_export_and_holdout_report_end_to_end() -> None:
    split = chronological_root_task_split(
        [_evidence(index) for index in range(48)]
    )
    bundle = train_and_export_tree(
        split.training,
        training_recipe_digest=SHA_D,
        dependency_lock_digest=SHA_E,
        evaluator_report_digest=SHA_F,
        created_at=START,
        expires_at=START + timedelta(days=90),
        maximum_use_count=10_000,
        corpus_digest=split.corpus_digest,
    )
    report = evaluate_candidate(
        bundle,
        split.holdout,
        created_at=START + timedelta(days=100),
        current_policy_digest=SHA_C,
        corpus_digest=split.corpus_digest,
    )

    assert bundle.corpus_digest == split.corpus_digest
    assert report.corpus_digest == split.corpus_digest
    assert activation_eligible(report, bundle)
    assert not activation_eligible(
        report.model_copy(update={"corpus_digest": SHA_A}), bundle
    )


def test_wrong_decision_unknown_feature_and_policy_mismatch_fail_closed() -> None:
    bundle = _bundle()
    holdout = [
        _evidence(40, wrong_decision=True),
        _evidence(41, missing_route=True),
        _evidence(42, policy_digest=SHA_D),
        *[_evidence(index) for index in range(43, 50)],
    ]
    report = evaluate_candidate(
        bundle,
        holdout,
        created_at=START + timedelta(days=100),
        current_policy_digest=SHA_C,
        corpus_digest=bundle.corpus_digest,
    )

    assert report.false_automation_count == 1
    assert report.unknown_feature_count == 1
    assert report.policy_mismatch_count == 1
    assert report.abstention_count == 2
    assert not activation_eligible(report, bundle)


def test_runtime_adapter_returns_closed_failures_without_guessing() -> None:
    bundle = _bundle()
    envelope = _evidence(40).envelope
    matched = apply_exported_rule(
        bundle, envelope, current_policy_digest=SHA_C
    )
    assert matched.disposition is ReplayDisposition.MATCH

    assert apply_exported_rule(
        bundle, envelope, current_policy_digest=SHA_D
    ).disposition is ReplayDisposition.POLICY_MISMATCH
    assert apply_exported_rule(
        bundle,
        _evidence(41, policy_digest=SHA_D).envelope,
        current_policy_digest=SHA_C,
    ).disposition is ReplayDisposition.POLICY_MISMATCH

    scope_miss = envelope.model_copy(update={"project_id": "project-2"})
    assert apply_exported_rule(
        bundle, scope_miss, current_policy_digest=SHA_C
    ).disposition is ReplayDisposition.SCOPE_MISS
    assert apply_exported_rule(
        bundle,
        _evidence(42, missing_route=True).envelope,
        current_policy_digest=SHA_C,
    ).disposition is ReplayDisposition.UNKNOWN_FEATURE

    root = next(node for node in bundle.nodes if node.node_id == bundle.root_id)

    abstain_root = root.model_copy(
        update={
            "feature_id": "route_hint",
            "operator": "eq",
            "value": "never",
            "miss_id": bundle.default_leaf_id,
        }
    )
    abstain_bundle = bundle.model_copy(
        update={
            "nodes": tuple(
                abstain_root if node.node_id == root.node_id else node
                for node in bundle.nodes
            )
        }
    )
    assert apply_exported_rule(
        abstain_bundle, envelope, current_policy_digest=SHA_C
    ).disposition is ReplayDisposition.ABSTAIN

    not_equal_root = abstain_root.model_copy(
        update={"operator": "not_eq", "miss_id": bundle.default_leaf_id}
    )
    not_equal_bundle = bundle.model_copy(
        update={
            "nodes": tuple(
                not_equal_root if node.node_id == root.node_id else node
                for node in bundle.nodes
            )
        }
    )
    assert apply_exported_rule(
        not_equal_bundle, envelope, current_policy_digest=SHA_C
    ).disposition is ReplayDisposition.MATCH

    missing_root = abstain_root.model_copy(update={"miss_id": "missing-leaf"})
    missing_bundle = bundle.model_copy(
        update={
            "nodes": tuple(
                missing_root if node.node_id == root.node_id else node
                for node in bundle.nodes
            )
        }
    )
    assert apply_exported_rule(
        missing_bundle, envelope, current_policy_digest=SHA_C
    ).disposition is ReplayDisposition.CONFLICT

    cycle_root = root.model_copy(update={"match_id": root.node_id})
    cycle_bundle = bundle.model_copy(
        update={
            "nodes": tuple(
                cycle_root if node.node_id == root.node_id else node
                for node in bundle.nodes
            )
        }
    )
    assert apply_exported_rule(
        cycle_bundle, envelope, current_policy_digest=SHA_C
    ).disposition is ReplayDisposition.CONFLICT


def test_evaluation_excludes_unknown_outcomes_and_validates_inputs() -> None:
    bundle = _bundle()
    with pytest.raises(EvaluationError, match="non-negative"):
        evaluate_candidate(
            bundle,
            [_evidence(index) for index in range(40, 45)],
            created_at=START,
            current_policy_digest=SHA_C,
            estimated_tokens_per_call=-1,
        )
    with pytest.raises(EvaluationError, match="five eligible"):
        evaluate_candidate(
            bundle,
            [_evidence(index) for index in range(40, 44)],
            created_at=START,
            current_policy_digest=SHA_C,
        )

    evidence = [
        *[_evidence(index) for index in range(40, 45)],
        _evidence(45, outcome=VerifiedOutcome.UNKNOWN),
    ]
    report = evaluate_candidate(
        bundle,
        evidence,
        created_at=START,
        current_policy_digest=SHA_C,
        corpus_digest=SHA_D,
    )
    assert report.support_count == 5
    assert report.corpus_digest == SHA_D


def test_activation_checks_terminal_outcomes_leaf_evidence_and_bundle_floors() -> None:
    bundle = _bundle()
    records = [_evidence(index) for index in range(40, 50)]
    report = evaluate_candidate(
        bundle,
        records,
        created_at=START,
        current_policy_digest=SHA_C,
        corpus_digest=bundle.corpus_digest,
    )
    assert activation_eligible(report, bundle)
    assert not activation_eligible(report, bundle, minimum_precision=0.98)
    assert not activation_eligible(report, bundle, minimum_precision=float("nan"))

    failed_report = report.model_copy(update={"verified_failure_count": 1})
    assert not activation_eligible(failed_report, bundle)

    unsupported_leaf_report = report.model_copy(
        update={
            "leaf_results": (
                LeafEvaluationV1(leaf_id="leaf-none", support=0, confidence=0.0),
            )
        }
    )
    assert not activation_eligible(unsupported_leaf_report, bundle)

    leaves = list(bundle.leaves)
    decision_index = next(
        index for index, leaf in enumerate(leaves) if not leaf.abstain
    )
    leaves[decision_index] = leaves[decision_index].model_copy(
        update={
            "support": 0,
            "confidence": 0.0,
            "outcome_counts": OutcomeCountsV1(
                success=0, failure=0, reverted=0, unknown=0, unsafe=0
            ),
        }
    )
    weak_bundle = bundle.model_copy(update={"leaves": tuple(leaves)})
    assert not activation_eligible(report, weak_bundle)


def test_evaluation_detects_report_nondeterminism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    records = [_evidence(index) for index in range(40, 45)]
    stable = evaluate_candidate(
        bundle,
        records,
        created_at=START,
        current_policy_digest=SHA_C,
    )
    changed = stable.model_copy(update={"estimated_tokens_avoided": 1})
    reports = iter((stable, changed))
    monkeypatch.setattr(
        evaluate_module,
        "_build_report",
        lambda *_args, **_kwargs: next(reports),
    )
    with pytest.raises(EvaluationError, match="changed across"):
        evaluate_candidate(
            bundle,
            records,
            created_at=START,
            current_policy_digest=SHA_C,
        )


def test_report_conflict_counter_is_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle()
    monkeypatch.setattr(
        evaluate_module,
        "apply_exported_rule",
        lambda *_args, **_kwargs: RuleApplication(ReplayDisposition.CONFLICT),
    )
    report = evaluate_candidate(
        bundle,
        [_evidence(index) for index in range(40, 45)],
        created_at=START,
        current_policy_digest=SHA_C,
        corpus_digest=bundle.corpus_digest,
    )
    assert report.conflict_count == 5
    assert report.exact_match_count == 0
    assert report.precision == 0.0
    assert not activation_eligible(report, bundle)
