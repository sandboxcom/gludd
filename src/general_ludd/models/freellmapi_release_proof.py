"""Bind FreeLLMAPI corpus, native-provider, and rollback release evidence.

The proof chain is deliberately non-promoting. It executes the currently
admitted pure scoring artifact against a tracked held-out corpus, validates one
opt-in call through Gludd's native provider gateway, and exercises the
fail-closed rollback path. A newer upstream source candidate still requires a
reproducible bundle and CI provenance before it can become runnable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from general_ludd.models.freellmapi_frozen_delta import FreeLLMAPIFrozenDeltaError
from general_ludd.models.freellmapi_frozen_delta_validation import validate_candidate
from general_ludd.models.freellmapi_release_common import (
    FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
    FREELLMAPI_ROLLBACK_GATE,
    FreeLLMAPIReleaseProofError,
    FreeLLMAPIReleaseProofFault,
    as_mapping,
    evidence_id,
    fail,
    sha256_digest,
    stable_evidence_id,
    verify_receipt_identity,
)
from general_ludd.models.freellmapi_release_corpus import (
    run_frozen_corpus_proof,
    validate_corpus_receipt,
)
from general_ludd.models.freellmapi_release_provider import (
    build_live_provider_receipt,
    validate_live_receipt,
)
from general_ludd.models.freellmapi_upstream_build import FreeLLMAPIUpstreamBuildFault

_PROVENANCE_KEYS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "corpus_evidence_id",
        "live_provider_evidence_id",
        "rollback_evidence_id",
        "decision",
        "external_block",
        "runtime_admitted",
        "evidence_id",
    }
)
_STABLE_TAG_RE = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)


def _trusted_provenance(
    receipt: Mapping[str, object], trusted_evidence_id: str
) -> None:
    fault = FreeLLMAPIReleaseProofFault.RECEIPT_INVALID
    trusted = evidence_id(trusted_evidence_id, fault)
    verify_receipt_identity(receipt, fault)
    external_block = receipt.get("external_block")
    if (
        set(receipt) != _PROVENANCE_KEYS
        or receipt.get("evidence_id") != trusted
        or receipt.get("schema_version") != FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION
        or receipt.get("decision") != "proof_chain_verified_promotion_blocked"
        or receipt.get("runtime_admitted") is not False
        or not isinstance(external_block, str)
        or re.fullmatch(r"[a-z0-9_]{1,512}", external_block) is None
    ):
        fail(fault)
    evidence_id(receipt.get("candidate_id"), fault)
    evidence_id(receipt.get("corpus_evidence_id"), fault)
    evidence_id(receipt.get("live_provider_evidence_id"), fault)
    evidence_id(receipt.get("rollback_evidence_id"), fault)


def _external_block(
    *,
    candidate_lock: Mapping[str, object],
    live_provider_receipt: Mapping[str, object],
    upstream_build_failure: str | None,
) -> str:
    fault = FreeLLMAPIReleaseProofFault.RECEIPT_INVALID
    upstream = as_mapping(candidate_lock.get("upstream"), fault)
    tag = upstream.get("tag")
    if not isinstance(tag, str) or _STABLE_TAG_RE.fullmatch(tag) is None:
        fail(fault)
    blockers: list[str] = []
    if live_provider_receipt.get("decision") == "live_provider_rejected":
        blockers.append(f"live_provider_{live_provider_receipt['provider_failure']}")
    if upstream_build_failure is not None:
        if not isinstance(upstream_build_failure, str):
            fail(fault)
        try:
            build_failure = FreeLLMAPIUpstreamBuildFault(upstream_build_failure)
        except ValueError:
            fail(fault)
        blockers.append(f"upstream_build_{build_failure.value}")
    blockers.append(
        f"missing_{tag.replace('.', '_')}_reproducible_bundle_and_ci_provenance"
    )
    return "_and_".join(blockers)


def exercise_release_rollback(
    *,
    candidate_lock: Mapping[str, object],
    corpus_proof: Mapping[str, object],
    live_provider_receipt: Mapping[str, object],
    active_lease_digest: str,
) -> dict[str, object]:
    """Exercise fail-closed prepromotion rollback while preserving one lease."""
    fault = FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID
    try:
        candidate_id, _ = validate_candidate(candidate_lock)
        validate_corpus_receipt(corpus_proof, candidate_lock=candidate_lock)
        validate_live_receipt(
            live_provider_receipt,
            candidate_id=candidate_id,
            corpus_id=corpus_proof.get("evidence_id"),
        )
    except (FreeLLMAPIFrozenDeltaError, FreeLLMAPIReleaseProofError):
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
    provenance_receipt: Mapping[str, object],
    trusted_provenance_evidence_id: str,
    upstream_build_failure: str | None = None,
) -> dict[str, object]:
    """Recompute and bind all tracked receipts without authorizing promotion."""
    _trusted_provenance(provenance_receipt, trusted_provenance_evidence_id)
    if provenance_receipt.get("candidate_id") != candidate_lock.get("candidate_id"):
        fail(FreeLLMAPIReleaseProofFault.RECEIPT_INVALID)
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
    result: dict[str, object] = {
        "schema_version": FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
        "candidate_id": candidate_lock["candidate_id"],
        "corpus_evidence_id": corpus_receipt["evidence_id"],
        "live_provider_evidence_id": live_provider_receipt["evidence_id"],
        "rollback_evidence_id": rollback_receipt["evidence_id"],
        "decision": "proof_chain_verified_promotion_blocked",
        "external_block": _external_block(
            candidate_lock=candidate_lock,
            live_provider_receipt=live_provider_receipt,
            upstream_build_failure=upstream_build_failure,
        ),
        "runtime_admitted": False,
    }
    result["evidence_id"] = stable_evidence_id(result)
    if dict(provenance_receipt) != result:
        fail(FreeLLMAPIReleaseProofFault.RECEIPT_INVALID)
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
