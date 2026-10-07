"""Strict deterministic tree-export tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import general_ludd.decision_codification.export as export_module
from general_ludd.decision_codification.export import (
    OneHotColumn,
    TreeExportError,
    train_and_export_tree,
)
from general_ludd.decision_codification.miner import DecisionEvidence, MiningFloors
from general_ludd.decision_codification.schema import (
    DecisionEnvelopeV1,
    DecisionRuleBundleV1,
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
    index: int, *, outcome: VerifiedOutcome = VerifiedOutcome.SUCCESS
) -> DecisionEvidence:
    route = "left" if index % 2 == 0 else "right"
    decision = "approve" if route == "left" else "reject"
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
        occurred_at=START + timedelta(days=index % 4, minutes=index),
        exact_guards={
            "action_vocabulary": "review.v1",
            "operation_class": "review",
            "risk_band": "low",
        },
        features={"route_hint": route, "reversible": True},
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
        root_task_id=f"root-{index}",
        source_agent_id=f"agent-{index % 2}",
    )


def _export(
    records: list[DecisionEvidence],
    *,
    floors: MiningFloors | None = None,
    corpus_digest: str | None = None,
) -> DecisionRuleBundleV1:
    return train_and_export_tree(
        records,
        training_recipe_digest=SHA_D,
        dependency_lock_digest=SHA_E,
        created_at=START,
        expires_at=START + timedelta(days=90),
        maximum_use_count=10_000,
        floors=floors,
        corpus_digest=corpus_digest,
    )


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


def test_export_is_byte_identical_for_stable_reverse_and_fixed_permutations() -> None:
    records = [_evidence(index) for index in range(32)]
    baseline = _export(records)
    reversed_export = _export(list(reversed(records)))
    permuted_export = _export(records[::2] + records[1::2])

    assert canonical_decision_json(baseline) == canonical_decision_json(reversed_export)
    assert canonical_decision_json(baseline) == canonical_decision_json(permuted_export)
    assert baseline.candidate_digest == reversed_export.candidate_digest
    assert baseline.candidate_digest == permuted_export.candidate_digest


def test_export_accepts_a_valid_full_split_corpus_digest_only() -> None:
    records = [_evidence(index) for index in range(32)]

    assert _export(records, corpus_digest=SHA_A).corpus_digest == SHA_A
    with pytest.raises(TreeExportError, match="corpus_digest"):
        _export(records, corpus_digest="sha256:not-a-digest")


def test_export_is_strict_bounded_and_has_reachable_default_abstention() -> None:
    bundle = _export([_evidence(index) for index in range(32)])

    default = next(
        leaf for leaf in bundle.leaves if leaf.leaf_id == bundle.default_leaf_id
    )
    assert default.abstain
    assert default.decision is None
    assert bundle.root_id == "node-root"
    assert len(bundle.nodes) <= 31
    assert len(bundle.leaves) <= 16
    assert {leaf.decision for leaf in bundle.leaves if not leaf.abstain} == {
        "approve",
        "reject",
    }

    serialized = canonical_decision_json(bundle)
    assert "DecisionTreeClassifier" not in serialized
    assert "pickle" not in serialized
    assert "joblib" not in serialized


def test_export_rejects_insufficient_or_mixed_scope_training_data() -> None:
    with pytest.raises(TreeExportError, match="at least 16"):
        _export([_evidence(index) for index in range(15)])

    records = [_evidence(index) for index in range(32)]
    changed = records[-1].envelope.model_copy(update={"project_id": "project-2"})
    mixed = [
        *records[:-1],
        DecisionEvidence(
            envelope=changed,
            root_task_id=records[-1].root_task_id,
            source_agent_id=records[-1].source_agent_id,
        ),
    ]
    with pytest.raises(TreeExportError, match="single exact scope"):
        _export(mixed)

    mixed_policy = records.copy()
    mixed_policy[-1] = _replace_envelope(mixed_policy[-1], policy_digest=SHA_D)
    with pytest.raises(TreeExportError, match="single exact scope"):
        _export(mixed_policy)


def test_export_rejects_diversity_conflict_and_all_unsafe_leaves() -> None:
    records = [_evidence(index) for index in range(32)]
    one_day = [
        _replace_envelope(record, occurred_at=START + timedelta(minutes=index))
        for index, record in enumerate(records)
    ]
    with pytest.raises(TreeExportError, match="UTC-day diversity"):
        _export(one_day)

    one_agent = [
        DecisionEvidence(record.envelope, record.root_task_id, "one-agent")
        for record in records
    ]
    with pytest.raises(TreeExportError, match="source-agent diversity"):
        _export(one_agent)

    conflict = records.copy()
    conflict[-1] = _replace_envelope(conflict[-1], decision="approve")
    with pytest.raises(TreeExportError, match="conflicting decisions"):
        _export(conflict)

    unsafe = [
        _evidence(index, outcome=VerifiedOutcome.UNSAFE) for index in range(32)
    ]
    with pytest.raises(TreeExportError, match="every learned leaf"):
        _export(unsafe)

    high_diversity = MiningFloors(minimum_root_tasks=33)
    with pytest.raises(TreeExportError, match="root-task diversity"):
        _export(records, floors=high_diversity)


def test_constant_decision_still_exports_through_default_guard() -> None:
    records = [
        _replace_envelope(record, decision="approve")
        for record in [_evidence(index) for index in range(32)]
    ]
    bundle = _export(records)
    assert bundle.nodes == (
        next(node for node in bundle.nodes if node.node_id == "node-root"),
    )
    assert [leaf.decision for leaf in bundle.leaves if not leaf.abstain] == [
        "approve"
    ]


def test_exporter_rejects_unavailable_or_malformed_estimators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        export_module,
        "import_module",
        lambda _name: SimpleNamespace(DecisionTreeClassifier=None),
    )
    with pytest.raises(TreeExportError, match="unavailable"):
        export_module._decision_tree_factory()

    records = tuple(_evidence(index) for index in range(32))
    columns = (OneHotColumn("route_hint", "left"),)
    metadata = export_module._ExportMetadata(
        training_recipe_digest=SHA_D,
        dependency_lock_digest=SHA_E,
        created_at=START,
        expires_at=START + timedelta(days=90),
        maximum_use_count=10_000,
        corpus_digest=SHA_A,
    )

    class FakeClassifier:
        def __init__(self, tree: Any, applied: tuple[int, ...]) -> None:
            self.tree_ = tree
            self.classes_: Sequence[object] = ("approve", "reject")
            self._applied = applied

        def fit(
            self,
            _features: Sequence[Sequence[float]],
            _targets: Sequence[str],
        ) -> object:
            return self

        def apply(
            self, _features: Sequence[Sequence[float]]
        ) -> tuple[int, ...]:
            return self._applied

    too_deep = SimpleNamespace(max_depth=4)
    with pytest.raises(TreeExportError, match="too deep"):
        export_module._export_fitted_tree(
            FakeClassifier(too_deep, ()), records, columns, metadata, MiningFloors()
        )

    half_leaf = SimpleNamespace(
        max_depth=1,
        children_left=(-1, -1),
        children_right=(1, -1),
        threshold=(0.5, -2.0),
        feature=(0, -2),
        value=(((16.0, 16.0),), ((16.0, 0.0),)),
    )
    with pytest.raises(TreeExportError, match="half-leaf"):
        export_module._export_fitted_tree(
            FakeClassifier(half_leaf, (1,) * len(records)),
            records,
            columns,
            metadata,
            MiningFloors(),
        )

    invalid_threshold = SimpleNamespace(
        max_depth=1,
        children_left=(1, -1, -1),
        children_right=(2, -1, -1),
        threshold=(0.25, -2.0, -2.0),
        feature=(0, -2, -2),
        value=(((16.0, 16.0),), ((16.0, 0.0),), ((0.0, 16.0),)),
    )
    with pytest.raises(TreeExportError, match=r"threshold 0\.5"):
        export_module._export_fitted_tree(
            FakeClassifier(invalid_threshold, (1,) * len(records)),
            records,
            columns,
            metadata,
            MiningFloors(),
        )

    invalid_feature = SimpleNamespace(
        max_depth=1,
        children_left=(1, -1, -1),
        children_right=(2, -1, -1),
        threshold=(0.5, -2.0, -2.0),
        feature=(9, -2, -2),
        value=(((16.0, 16.0),), ((16.0, 0.0),), ((0.0, 16.0),)),
    )
    with pytest.raises(TreeExportError, match="unknown feature"):
        export_module._export_fitted_tree(
            FakeClassifier(invalid_feature, (1,) * len(records)),
            records,
            columns,
            metadata,
            MiningFloors(),
        )

    empty_leaf = SimpleNamespace(
        max_depth=0,
        children_left=(-1,),
        children_right=(-1,),
        threshold=(-2.0,),
        feature=(-2,),
        value=(((0.0, 0.0),),),
    )
    with pytest.raises(TreeExportError, match="empty leaf"):
        export_module._export_fitted_tree(
            FakeClassifier(empty_leaf, (0,) * len(records)),
            records,
            columns,
            metadata,
            MiningFloors(),
        )


def test_export_detects_empty_encoding_and_permutation_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_evidence(index) for index in range(32)]
    monkeypatch.setattr(export_module, "_columns", lambda _records: ())
    with pytest.raises(TreeExportError, match="one bounded feature"):
        _export(records)

    monkeypatch.undo()
    stable = _export(records)
    changed = stable.model_copy(update={"maximum_use_count": 9_999})
    exports = iter((stable, changed, stable))
    monkeypatch.setattr(export_module, "_fit", lambda *_args: object())
    monkeypatch.setattr(
        export_module,
        "_export_fitted_tree",
        lambda *_args: next(exports),
    )
    with pytest.raises(TreeExportError, match="changed across"):
        _export(records)
