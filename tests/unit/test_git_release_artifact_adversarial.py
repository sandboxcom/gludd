"""Adversarial release-artifact binding and authorization receipt tests."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from general_ludd.git_release.provenance import (
    ArtifactVerificationReceipt,
    Attestation,
    ProvenanceRecord,
    ReceiptPurpose,
    SignatureState,
    build_provenance,
    verify_artifact_receipt,
    verify_provenance,
)
from general_ludd.git_release.release_state import (
    ReleaseState,
    ReleaseStateMachine,
    TransitionError,
)

_ARTIFACT = b"release artifact\n"
_PRIOR_ARTIFACT = b"prior release artifact\n"
_LOCK = json.dumps(
    {"generated_at": "2026-09-27T00:00:00Z", "packages": {"alpha": "1.2.3"}},
    sort_keys=True,
).encode()
_SOURCE_SHA = "a" * 40
_PRIOR_SOURCE_SHA = "b" * 40
_RELEASE_ID = "release-2026.09.27"
_PRIOR_RELEASE_ID = "release-2026.09.26"
_TARGET = "production/us-east"
_SUBJECT = "gludd-2026.09.27.tar.gz"
_BUILDER = "github-actions:release-builder"


def _record(
    *,
    artifact: bytes = _ARTIFACT,
    release_id: str = _RELEASE_ID,
    source_sha: str = _SOURCE_SHA,
    subject: str = _SUBJECT,
) -> ProvenanceRecord:
    return build_provenance(
        artifact_name=subject,
        artifact_bytes=artifact,
        dependency_lock_bytes=_LOCK,
        builder_identity=_BUILDER,
        release_id=release_id,
        source_sha=source_sha,
        signature_state=SignatureState.VERIFIED,
    )


def _verify(
    record: ProvenanceRecord,
    *,
    artifact: bytes | None = _ARTIFACT,
    lock: bytes | None = _LOCK,
    signer_payload: str | None = None,
    provenance_release_id: str = _RELEASE_ID,
    provenance_source_sha: str = _SOURCE_SHA,
    authorization_id: str = _RELEASE_ID,
    authorization_source_sha: str = _SOURCE_SHA,
    purpose: ReceiptPurpose = ReceiptPurpose.DEPLOY,
    target: str = _TARGET,
) -> object:
    assert record.attestation is not None
    return verify_provenance(
        record,
        expected_artifact_bytes=artifact,
        expected_lock_bytes=lock,
        expected_subject=record.subject,
        expected_builder_identity=_BUILDER,
        expected_release_id=provenance_release_id,
        expected_source_sha=provenance_source_sha,
        verified_attestation_digest=(
            record.attestation.digest if signer_payload is None else signer_payload
        ),
        authorization_id=authorization_id,
        authorization_source_sha=authorization_source_sha,
        receipt_purpose=purpose,
        deployment_target=target,
    )


def _receipt(
    *,
    purpose: ReceiptPurpose = ReceiptPurpose.DEPLOY,
    artifact: bytes = _ARTIFACT,
    provenance_release_id: str = _RELEASE_ID,
    provenance_source_sha: str = _SOURCE_SHA,
    subject: str = _SUBJECT,
) -> ArtifactVerificationReceipt:
    record = _record(
        artifact=artifact,
        release_id=provenance_release_id,
        source_sha=provenance_source_sha,
        subject=subject,
    )
    result = _verify(
        record,
        artifact=artifact,
        provenance_release_id=provenance_release_id,
        provenance_source_sha=provenance_source_sha,
        purpose=purpose,
    )
    assert result.ok, result.reasons
    assert result.receipt is not None
    return result.receipt


def test_verified_deploy_receipt_cross_binds_all_release_evidence() -> None:
    receipt = _receipt()

    assert receipt.purpose is ReceiptPurpose.DEPLOY
    assert receipt.authorization_id == _RELEASE_ID
    assert receipt.authorization_source_sha == _SOURCE_SHA
    assert receipt.subject == _SUBJECT
    assert receipt.artifact_digest == hashlib.sha256(_ARTIFACT).hexdigest()
    assert receipt.signer_payload_digest == receipt.attestation_digest
    assert verify_artifact_receipt(
        receipt,
        expected_purpose=ReceiptPurpose.DEPLOY,
        expected_authorization_id=_RELEASE_ID,
        expected_authorization_source_sha=_SOURCE_SHA,
        expected_deployment_target=_TARGET,
        expected_artifact_digest=receipt.artifact_digest,
    ).ok


@pytest.mark.parametrize(
    ("artifact", "lock", "reason"),
    [
        (None, _LOCK, "artifact-bytes-missing"),
        (_ARTIFACT, None, "dependency-lock-bytes-missing"),
        (b"swapped artifact\n", _LOCK, "artifact-digest-mismatch"),
    ],
)
def test_verification_fails_closed_without_independent_inputs(
    artifact: bytes | None,
    lock: bytes | None,
    reason: str,
) -> None:
    result = _verify(_record(), artifact=artifact, lock=lock)

    assert not result.ok
    assert result.receipt is None
    assert any(item.startswith(reason) for item in result.reasons)


def test_verification_fails_closed_without_signer_payload() -> None:
    record = _record()
    result = verify_provenance(
        record,
        expected_artifact_bytes=_ARTIFACT,
        expected_lock_bytes=_LOCK,
        expected_subject=_SUBJECT,
        expected_builder_identity=_BUILDER,
        expected_release_id=_RELEASE_ID,
        expected_source_sha=_SOURCE_SHA,
        authorization_id=_RELEASE_ID,
        authorization_source_sha=_SOURCE_SHA,
        receipt_purpose=ReceiptPurpose.DEPLOY,
        deployment_target=_TARGET,
    )

    assert not result.ok
    assert result.receipt is None
    assert "signature-payload-digest-missing" in result.reasons


def test_signed_record_cannot_be_replayed_for_another_release_or_source() -> None:
    record = _record()
    replay = _verify(
        record,
        provenance_release_id="release-other",
        provenance_source_sha="c" * 40,
    )

    assert not replay.ok
    assert replay.receipt is None
    assert "release-id-mismatch" in replay.reasons
    assert "source-sha-mismatch" in replay.reasons


def test_signed_predicate_subject_builder_and_lock_are_policy_bound() -> None:
    record = _record()
    assert record.attestation is not None
    statement = deepcopy(record.attestation.statement)
    statement["subject"][0]["name"] = "swapped.tar.gz"
    statement["predicateType"] = "https://example.invalid/predicate/v1"
    statement["predicate"]["runDetails"]["builder"]["id"] = "attacker"
    statement["predicate"]["buildDefinition"]["resolvedDependencies"][0][
        "digest"
    ]["sha256"] = "f" * 64
    signed_digest = hashlib.sha256(
        json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    forged = replace(
        record,
        attestation=Attestation(
            predicate_type="https://example.invalid/predicate/v1",
            statement=statement,
            digest=signed_digest,
        ),
    )

    result = _verify(forged, signer_payload=signed_digest)

    assert not result.ok
    assert {
        "attestation-subject-mismatch",
        "attestation-predicate-type-mismatch",
        "attestation-builder-mismatch",
        "attestation-lock-digest-mismatch",
    } <= set(result.reasons)


def test_sbom_dependency_swap_is_rejected_even_when_metadata_matches() -> None:
    record = _record()
    swapped_sbom = deepcopy(record.sbom)
    swapped_sbom["components"] = [
        {"type": "library", "name": "alpha", "version": "9.9.9"}
    ]

    result = _verify(replace(record, sbom=swapped_sbom))

    assert not result.ok
    assert result.receipt is None
    assert "sbom-components-mismatch" in result.reasons


def test_build_rejects_parsed_lock_that_does_not_match_hashed_lock_bytes() -> None:
    with pytest.raises(ValueError, match="dependency_lock"):
        build_provenance(
            artifact_name=_SUBJECT,
            artifact_bytes=_ARTIFACT,
            dependency_lock_bytes=_LOCK,
            dependency_lock={"packages": {"alpha": "9.9.9"}},
            builder_identity=_BUILDER,
            release_id=_RELEASE_ID,
            source_sha=_SOURCE_SHA,
        )


def test_deploy_and_rollback_receipts_are_not_interchangeable() -> None:
    deploy = _receipt()
    rollback_check = verify_artifact_receipt(
        deploy,
        expected_purpose=ReceiptPurpose.ROLLBACK,
        expected_authorization_id=_RELEASE_ID,
        expected_authorization_source_sha=_SOURCE_SHA,
        expected_deployment_target=_TARGET,
        expected_artifact_digest=deploy.artifact_digest,
    )

    assert not rollback_check.ok
    assert "receipt-purpose-mismatch" in rollback_check.reasons
    assert not verify_artifact_receipt(
        None,
        expected_purpose=ReceiptPurpose.DEPLOY,
        expected_authorization_id=_RELEASE_ID,
        expected_authorization_source_sha=_SOURCE_SHA,
        expected_deployment_target=_TARGET,
        expected_artifact_digest=deploy.artifact_digest,
    ).ok


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"authorization_id": "release-replay"}, "receipt-authorization-id-mismatch"),
        (
            {"authorization_source_sha": "c" * 40},
            "receipt-authorization-source-sha-mismatch",
        ),
        ({"deployment_target": "production/eu"}, "receipt-deployment-target-mismatch"),
        ({"artifact_digest": "d" * 64}, "receipt-artifact-digest-mismatch"),
        ({"receipt_digest": "e" * 64}, "receipt-digest-mismatch"),
    ],
)
def test_receipt_replay_and_swapped_context_are_rejected(
    changes: dict[str, str],
    reason: str,
) -> None:
    receipt = replace(_receipt(), **changes)

    result = verify_artifact_receipt(
        receipt,
        expected_purpose=ReceiptPurpose.DEPLOY,
        expected_authorization_id=_RELEASE_ID,
        expected_authorization_source_sha=_SOURCE_SHA,
        expected_deployment_target=_TARGET,
        expected_artifact_digest=hashlib.sha256(_ARTIFACT).hexdigest(),
    )

    assert not result.ok
    assert reason in result.reasons


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "deploy"},
        {"signer_payload_digest": None},
        {"receipt_digest": None},
    ],
)
def test_malformed_deserialized_receipt_fails_closed_without_raising(
    changes: dict[str, object],
) -> None:
    malformed = replace(_receipt(), **changes)  # type: ignore[arg-type]

    result = verify_artifact_receipt(
        malformed,
        expected_purpose=ReceiptPurpose.DEPLOY,
        expected_authorization_id=_RELEASE_ID,
        expected_authorization_source_sha=_SOURCE_SHA,
        expected_deployment_target=_TARGET,
        expected_artifact_digest=hashlib.sha256(_ARTIFACT).hexdigest(),
    )

    assert not result.ok
    assert result.reasons


def test_state_machine_requires_exact_deploy_and_rollback_receipts() -> None:
    new_digest = hashlib.sha256(_ARTIFACT).hexdigest()
    prior_digest = hashlib.sha256(_PRIOR_ARTIFACT).hexdigest()
    deploy_receipt = _receipt()
    rollback_receipt = _receipt(
        purpose=ReceiptPurpose.ROLLBACK,
        artifact=_PRIOR_ARTIFACT,
        provenance_release_id=_PRIOR_RELEASE_ID,
        provenance_source_sha=_PRIOR_SOURCE_SHA,
        subject="gludd-2026.09.26.tar.gz",
    )
    machine = ReleaseStateMachine(
        source_sha=_SOURCE_SHA,
        artifact_digest=new_digest,
        release_id=_RELEASE_ID,
        deployment_target=_TARGET,
    )
    machine.advance(target=ReleaseState.PLAN)
    machine.advance(target=ReleaseState.BUILD_ONCE)
    machine.advance(
        target=ReleaseState.VERIFY_OFFLINE,
        gate_evidence=[("unit", "passed", "log://unit")],
    )

    missing = machine.advance(target=ReleaseState.STAGE, artifact_digest=new_digest)
    assert missing.blocked
    assert "missing-deploy-receipt" in missing.reasons
    staged = machine.advance(
        target=ReleaseState.STAGE,
        artifact_digest=new_digest,
        artifact_receipt=deploy_receipt,
    )
    assert not staged.blocked
    machine.advance(
        target=ReleaseState.CANARY,
        prior_digest=prior_digest,
        health_gate_passed=True,
    )

    with pytest.raises(TransitionError, match="rollback receipt"):
        machine.rollback(reason="regression")
    with pytest.raises(TransitionError, match="rollback receipt"):
        machine.rollback(reason="regression", artifact_receipt=deploy_receipt)
    machine.rollback(reason="regression", artifact_receipt=rollback_receipt)
    assert machine.state is ReleaseState.ROLLBACK
    assert machine.serving_digest == prior_digest
