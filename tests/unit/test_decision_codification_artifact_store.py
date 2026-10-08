"""Tests for create-only authenticated decision artifacts and receipt chains."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

import general_ludd.decision_codification.artifact_store as artifact_store_module
from general_ludd.decision_codification.artifact_store import (
    ArtifactAlreadyExists,
    ArtifactIntegrityError,
    DecisionArtifactStore,
    ReceiptChainError,
)
from general_ludd.decision_codification.schema import (
    ApprovalReceiptV1,
    DecisionRuleBundleV1,
    DecisionRuleLeafV1,
    DecisionRuleNodeV1,
    OutcomeCountsV1,
    ReceiptType,
)
from general_ludd.integrity.store import IntegrityStore

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def _bundle() -> DecisionRuleBundleV1:
    return DecisionRuleBundleV1.create(
        schema="gludd.decision-rule-bundle/v1",
        project_id="project-1",
        decision_kind="review",
        feature_schema=SHA_D,
        policy_compatibility=(SHA_E,),
        risk_scope="low",
        root_id="node-1",
        default_leaf_id="leaf-abstain",
        nodes=(
            DecisionRuleNodeV1(
                node_id="node-1",
                feature_id="work_type",
                operator="eq",
                value="code",
                match_id="leaf-approve",
                miss_id="leaf-abstain",
            ),
        ),
        leaves=(
            DecisionRuleLeafV1(
                leaf_id="leaf-abstain",
                decision=None,
                support=0,
                confidence=0.0,
                outcome_counts=OutcomeCountsV1(
                    success=0, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=True,
            ),
            DecisionRuleLeafV1(
                leaf_id="leaf-approve",
                decision="approve",
                support=20,
                confidence=1.0,
                outcome_counts=OutcomeCountsV1(
                    success=20, failure=0, reverted=0, unknown=0, unsafe=0
                ),
                abstain=False,
            ),
        ),
        corpus_digest=SHA_A,
        training_recipe_digest=SHA_B,
        dependency_lock_digest=SHA_C,
        observed_context_digests=(SHA_F,),
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
        maximum_use_count=100,
    )


def test_observability_receipt_hmac_is_scope_bound_and_verifiable(
    tmp_path: Path,
) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"artifact-integrity-key")

    tag = store.decision_observability_hmac("project-1", SHA_E, SHA_A)

    assert tag.startswith("hmac-sha256:")
    store.verify_decision_observability_hmac("project-1", SHA_E, SHA_A, tag)
    with pytest.raises(ArtifactIntegrityError):
        store.verify_decision_observability_hmac("project-1", SHA_E, SHA_B, tag)


def _receipt(
    bundle: DecisionRuleBundleV1,
    *,
    previous: str | None = None,
    receipt_type: str = "approval",
    state: str = "shadow",
) -> ApprovalReceiptV1:
    return ApprovalReceiptV1.create(
        schema="gludd.decision-approval-receipt/v1",
        receipt_type=receipt_type,
        lifecycle_state=state,
        previous_receipt_digest=previous,
        candidate_digest=bundle.candidate_digest,
        corpus_digest=bundle.corpus_digest,
        evaluator_report_digest=SHA_F,
        feature_schema=bundle.feature_schema,
        policy_digest=SHA_E,
        source_code_digest=SHA_B,
        dependency_lock_digest=bundle.dependency_lock_digest,
        training_recipe_digest=bundle.training_recipe_digest,
        project_id=bundle.project_id,
        decision_kind=bundle.decision_kind,
        approver_identity_hmac="hmac-sha256:" + "1" * 64,
        authorization_evidence_digest=SHA_A,
        created_at=NOW if previous is None else NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=30),
        risk_class=bundle.risk_scope,
        rollout_plan=("shadow", "canary", "active"),
        maximum_use_count=100,
    )


def test_artifacts_are_create_only_hmac_authenticated_and_typed(tmp_path: Path) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"artifact-key-for-tests")
    bundle = _bundle()

    store.create_rule_bundle(bundle)

    assert store.read_rule_bundle(bundle.candidate_digest) == bundle
    with pytest.raises(ArtifactAlreadyExists):
        store.create_rule_bundle(bundle)

    wrong_key = DecisionArtifactStore(str(tmp_path), key=b"different-test-key")
    with pytest.raises(ArtifactIntegrityError):
        wrong_key.read_rule_bundle(bundle.candidate_digest)


def test_receipts_append_as_a_verified_immutable_chain(tmp_path: Path) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"receipt-key-for-tests")
    bundle = _bundle()
    approval = _receipt(bundle)
    promotion = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
    )

    store.append_receipt(approval)
    store.append_receipt(promotion)

    assert store.verify_receipt_chain(promotion.receipt_digest) == (
        approval,
        promotion,
    )
    with pytest.raises(ArtifactAlreadyExists):
        store.append_receipt(promotion)


def test_receipt_chain_rejects_missing_parent_and_binding_changes(tmp_path: Path) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"receipt-key-for-tests")
    bundle = _bundle()
    approval = _receipt(bundle)
    store.append_receipt(approval)

    missing_parent = _receipt(
        bundle,
        previous=SHA_C,
        receipt_type="promotion",
        state="canary",
    )
    with pytest.raises(ReceiptChainError, match="previous receipt"):
        store.append_receipt(missing_parent)

    changed = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
    ).model_copy(update={"project_id": "project-2"})
    with pytest.raises(ReceiptChainError, match="binding"):
        store.append_receipt(changed)


def test_store_requires_a_key_and_identity_hmac_is_domain_scoped(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIntegrityError, match="HMAC key"):
        DecisionArtifactStore(str(tmp_path), key=None)

    store = DecisionArtifactStore(str(tmp_path), key=b"identity-key-for-tests")
    first = store.identity_hmac("operator@example.invalid", "project-1")

    assert first.startswith("hmac-sha256:")
    assert first == store.identity_hmac("operator@example.invalid", "project-1")
    assert first != store.identity_hmac("operator@example.invalid", "project-2")
    assert "operator" not in first

    with pytest.raises(ArtifactIntegrityError, match="non-empty"):
        store.identity_hmac("", "project-1")
    with pytest.raises(ArtifactIntegrityError, match="SHA-256"):
        store.read_rule_bundle("../not-a-digest")


def test_receipt_chain_bounds_cycles_order_and_renewal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"chain-edge-key-for-tests")
    bundle = _bundle()
    approval = _receipt(bundle)
    promotion = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="promotion",
        state="canary",
    )
    store.append_receipt(approval)
    store.append_receipt(promotion)

    monkeypatch.setattr(artifact_store_module, "_MAX_RECEIPT_CHAIN", 1)
    with pytest.raises(ReceiptChainError, match="bounded"):
        store.verify_receipt_chain(promotion.receipt_digest)
    monkeypatch.setattr(artifact_store_module, "_MAX_RECEIPT_CHAIN", 256)

    cyclic = approval.model_copy(
        update={"previous_receipt_digest": approval.receipt_digest}
    )
    monkeypatch.setattr(store, "read_receipt", lambda digest: cyclic)
    with pytest.raises(ReceiptChainError, match="cycle"):
        store.verify_receipt_chain(approval.receipt_digest)
    monkeypatch.undo()

    backward_data = promotion.model_dump(
        mode="python", by_alias=True, exclude={"receipt_digest"}
    )
    backward_data["created_at"] = NOW - timedelta(seconds=1)
    backward = ApprovalReceiptV1.create(**backward_data)
    with pytest.raises(ReceiptChainError, match="precedes"):
        store.append_receipt(backward)

    renewal = _receipt(
        bundle,
        previous=approval.receipt_digest,
        receipt_type="renewal",
        state="shadow",
    )
    store.append_receipt(renewal)
    assert store.read_receipt(renewal.receipt_digest) == renewal


def test_invalid_receipts_and_incomplete_chains_fail_before_acceptance(
    tmp_path: Path,
) -> None:
    store = DecisionArtifactStore(str(tmp_path), key=b"invalid-chain-key-for-tests")
    bundle = _bundle()
    approval = _receipt(bundle)

    non_root = approval.model_copy(
        update={"receipt_type": ReceiptType.PROMOTION, "lifecycle_state": "canary"}
    )
    with pytest.raises(ReceiptChainError, match="begin"):
        store.append_receipt(non_root)

    invalid = approval.model_copy(update={"candidate_digest": SHA_B})
    with pytest.raises(ArtifactIntegrityError, match="canonical"):
        store.append_receipt(invalid)

    with pytest.raises(ReceiptChainError, match="incomplete"):
        store.verify_receipt_chain(SHA_C)


def test_record_corruption_shapes_and_write_failures_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle()
    store = DecisionArtifactStore(str(tmp_path), key=b"record-corruption-key")
    store.create_rule_bundle(bundle)
    integrity = cast(
        IntegrityStore,
        object.__getattribute__(store, "_integrity"),
    )

    def load_value(value: object) -> None:
        monkeypatch.setattr(integrity, "load", lambda name: value)

    with pytest.raises(ArtifactIntegrityError, match="object"):
        load_value([])
        store.read_rule_bundle(bundle.candidate_digest)
    with pytest.raises(ArtifactIntegrityError, match="fields"):
        load_value({})
        store.read_rule_bundle(bundle.candidate_digest)

    identifier = bundle.candidate_digest
    base: dict[str, object] = {
        "schema": "gludd.decision-authenticated-artifact/v1",
        "kind": "rule",
        "identifier": identifier,
        "payload": bundle.model_dump(mode="json", by_alias=True),
    }
    with pytest.raises(ArtifactIntegrityError, match="tag is invalid"):
        load_value({**base, "authentication_tag": 7})
        store.read_rule_bundle(identifier)
    with pytest.raises(ArtifactIntegrityError, match="scope"):
        load_value({
            **base,
            "schema": "wrong",
            "authentication_tag": "hmac-sha256:" + "0" * 64,
        })
        store.read_rule_bundle(identifier)
    with pytest.raises(ArtifactIntegrityError, match="tag mismatched"):
        load_value({**base, "authentication_tag": "hmac-sha256:" + "0" * 64})
        store.read_rule_bundle(identifier)

    invalid_payload: dict[str, object] = {**base, "payload": "not-an-object"}
    load_value({
        **invalid_payload,
        "authentication_tag": "hmac-sha256:" + integrity.sign(invalid_payload),
    })
    with pytest.raises(ArtifactIntegrityError, match="payload"):
        store.read_rule_bundle(identifier)

    empty_payload: dict[str, object] = {**base, "payload": {}}
    load_value({
        **empty_payload,
        "authentication_tag": "hmac-sha256:" + integrity.sign(empty_payload),
    })
    with pytest.raises(ArtifactIntegrityError, match="schema"):
        store.read_rule_bundle(identifier)

    monkeypatch.undo()
    failed_store = DecisionArtifactStore(
        str(tmp_path / "failed-write"), key=b"record-write-key"
    )
    failed_integrity = cast(
        IntegrityStore,
        object.__getattribute__(failed_store, "_integrity"),
    )

    def fail_save(name: str, data: object) -> str:
        raise OSError("simulated write failure")

    monkeypatch.setattr(failed_integrity, "save", fail_save)
    with pytest.raises(ArtifactIntegrityError, match="write"):
        failed_store.create_rule_bundle(bundle)
    with pytest.raises(ArtifactAlreadyExists):
        failed_store.create_rule_bundle(bundle)
