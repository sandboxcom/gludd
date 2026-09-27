"""Bind FreeLLMAPI corpus, native-provider, and rollback release evidence.

The proof chain is deliberately non-promoting. It executes the currently
admitted pure scoring artifact against a tracked held-out corpus, validates one
opt-in call through Gludd's native provider gateway, and exercises the
fail-closed rollback path. A newer upstream source candidate still requires a
reproducible bundle and CI provenance before it can become runnable.
"""

from __future__ import annotations

from collections.abc import Mapping

from general_ludd.models.freellmapi_release_common import (
    EVIDENCE_ID_RE,
    FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
    FREELLMAPI_ROLLBACK_GATE,
    FreeLLMAPIReleaseProofError,
    FreeLLMAPIReleaseProofFault,
    as_mapping,
    fail,
    sha256_digest,
    stable_evidence_id,
)
from general_ludd.models.freellmapi_release_corpus import run_frozen_corpus_proof
from general_ludd.models.freellmapi_release_provider import (
    build_live_provider_receipt,
    validate_live_receipt,
    verify_receipt_identity,
)


def exercise_release_rollback(
    *,
    candidate_lock: Mapping[str, object],
    corpus_proof: Mapping[str, object],
    live_provider_receipt: Mapping[str, object],
    active_lease_digest: str,
) -> dict[str, object]:
    """Exercise fail-closed prepromotion rollback while preserving one lease."""
    fault = FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID
    candidate_id = candidate_lock.get("candidate_id")
    if EVIDENCE_ID_RE.fullmatch(str(candidate_id)) is None:
        fail(fault)
    verify_receipt_identity(corpus_proof, fault)
    verify_receipt_identity(live_provider_receipt, fault)
    if (
        corpus_proof.get("candidate_id") != candidate_id
        or corpus_proof.get("decision") != "frozen_corpus_verified"
        or corpus_proof.get("runtime_admitted") is not False
        or live_provider_receipt.get("candidate_id") != candidate_id
        or live_provider_receipt.get("corpus_evidence_id")
        != corpus_proof.get("evidence_id")
        or live_provider_receipt.get("decision")
        not in {"live_provider_verified", "live_provider_rejected"}
        or live_provider_receipt.get("runtime_admitted") is not False
    ):
        fail(fault)
    lease_digest = sha256_digest(active_lease_digest, fault)
    admitted = as_mapping(candidate_lock.get("admitted_artifact"), fault)
    blue_digest = sha256_digest(admitted.get("bundle_sha256"), fault)
    green_candidate = str(candidate_id)
    live_decision = str(live_provider_receipt["decision"])
    receipt: dict[str, object] = {
        "schema_version": FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
        "gate": FREELLMAPI_ROLLBACK_GATE,
        "candidate_id": green_candidate,
        "corpus_evidence_id": corpus_proof["evidence_id"],
        "live_provider_evidence_id": live_provider_receipt["evidence_id"],
        "serving_before": blue_digest,
        "green_candidate": green_candidate,
        "events": [
            "green_evidence_verified",
            (
                "promotion_blocked_missing_provenance"
                if live_decision == "live_provider_verified"
                else "promotion_blocked_live_provider_rejected"
            ),
            "blue_assignment_retained",
            "green_review_generation_closed",
        ],
        "active_lease_before": lease_digest,
        "active_lease_after": lease_digest,
        "serving_after": blue_digest,
        "protected_artifacts": [blue_digest],
        "promotion_attempted": False,
        "decision": "rollback_verified_promotion_blocked",
        "runtime_admitted": False,
    }
    receipt["evidence_id"] = stable_evidence_id(receipt)
    return receipt


def validate_tracked_release_receipts(
    *,
    candidate_lock: Mapping[str, object],
    corpus: Mapping[str, object],
    plan: Mapping[str, object],
    abi_manifest: Mapping[str, object],
    corpus_receipt: Mapping[str, object],
    live_provider_receipt: Mapping[str, object],
    rollback_receipt: Mapping[str, object],
) -> dict[str, object]:
    """Recompute and bind all tracked receipts without authorizing promotion."""
    expected_corpus = run_frozen_corpus_proof(
        candidate_lock=candidate_lock,
        corpus=corpus,
        plan=plan,
        abi_manifest=abi_manifest,
    )
    if dict(corpus_receipt) != expected_corpus:
        fail(FreeLLMAPIReleaseProofFault.RECEIPT_INVALID)
    validate_live_receipt(
        live_provider_receipt,
        candidate_id=candidate_lock.get("candidate_id"),
        corpus_id=corpus_receipt.get("evidence_id"),
    )
    lease_digest = sha256_digest(
        rollback_receipt.get("active_lease_before"),
        FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID,
    )
    expected_rollback = exercise_release_rollback(
        candidate_lock=candidate_lock,
        corpus_proof=corpus_receipt,
        live_provider_receipt=live_provider_receipt,
        active_lease_digest=lease_digest,
    )
    if dict(rollback_receipt) != expected_rollback:
        fail(FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID)
    live_rejected = live_provider_receipt.get("decision") == "live_provider_rejected"
    result: dict[str, object] = {
        "schema_version": FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
        "candidate_id": candidate_lock["candidate_id"],
        "corpus_evidence_id": corpus_receipt["evidence_id"],
        "live_provider_evidence_id": live_provider_receipt["evidence_id"],
        "rollback_evidence_id": rollback_receipt["evidence_id"],
        "decision": "proof_chain_verified_promotion_blocked",
        "external_block": (
            "live_provider_rate_limited_and_missing_v0_11_1_reproducible_bundle_and_ci_provenance"
            if live_rejected
            else "missing_v0_11_1_reproducible_bundle_and_ci_provenance"
        ),
        "runtime_admitted": False,
    }
    result["evidence_id"] = stable_evidence_id(result)
    return result


__all__ = [
    "FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION",
    "FreeLLMAPIReleaseProofError",
    "FreeLLMAPIReleaseProofFault",
    "build_live_provider_receipt",
    "exercise_release_rollback",
    "run_frozen_corpus_proof",
    "validate_tracked_release_receipts",
]
