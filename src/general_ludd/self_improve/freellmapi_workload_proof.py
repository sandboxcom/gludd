"""Bind hermetic self-improvement outcomes to a held FreeLLMAPI candidate.

The receipt produced here contains only stable identities, digests, and typed
decisions.  It proves both workload decision paths and the existing fail-closed
rollback without treating a fake run, or a rejected live request, as promotion
evidence.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import NoReturn

from general_ludd.models.freellmapi_frozen_delta import FreeLLMAPIFrozenDeltaError
from general_ludd.models.freellmapi_frozen_delta_validation import validate_candidate
from general_ludd.models.freellmapi_release_common import (
    FREELLMAPI_ROLLBACK_GATE,
    FreeLLMAPIReleaseProofError,
    FreeLLMAPIReleaseProofFault,
    as_mapping,
    evidence_id,
    sha256_digest,
    stable_evidence_id,
    verify_receipt_identity,
)
from general_ludd.self_improve.workload import WorkloadResult

FREELLMAPI_WORKLOAD_PROOF_SCHEMA_VERSION = 1
FREELLMAPI_WORKLOAD_PROOF_GATE = "freellmapi-self-improvement-workload-proof-v1"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "gate",
        "candidate_id",
        "candidate_tag",
        "candidate_commit",
        "candidate_source_archive_sha256",
        "accepted_workload",
        "rejected_workload",
        "rollback_evidence_id",
        "serving_artifact_sha256",
        "promotion_attempted",
        "decision",
        "runtime_admitted",
        "evidence_id",
    }
)
_OUTCOME_KEYS = frozenset(
    {
        "decision",
        "passed",
        "provider",
        "candidate_evidence_identity_sha256",
        "task_sha256",
        "response_sha256",
        "claim_sha256",
        "protocol",
    }
)


class FreeLLMAPIWorkloadProofError(ValueError):
    """Fail-closed workload-proof error with no task or response content."""

    def __init__(self) -> None:
        """Expose one stable, content-free failure category."""
        super().__init__("workload_proof_invalid")


def _fail() -> NoReturn:
    raise FreeLLMAPIWorkloadProofError


def _text_digest(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail()
    return hashlib.sha256(encoded).hexdigest()


def _candidate_facts(
    candidate_lock: Mapping[str, object],
) -> tuple[str, str, str, str, str]:
    """Validate and return the immutable candidate and serving identities."""
    try:
        candidate_id, _ = validate_candidate(candidate_lock)
        upstream = as_mapping(
            candidate_lock.get("upstream"), FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID
        )
        archive = as_mapping(
            candidate_lock.get("archive"), FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID
        )
        admitted = as_mapping(
            candidate_lock.get("admitted_artifact"),
            FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID,
        )
        tag = upstream.get("tag")
        commit = upstream.get("commit")
        if (
            not isinstance(tag, str)
            or not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        ):
            _fail()
        archive_digest = sha256_digest(
            archive.get("sha256"), FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID
        )
        serving_digest = sha256_digest(
            admitted.get("bundle_sha256"),
            FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID,
        )
    except (FreeLLMAPIFrozenDeltaError, FreeLLMAPIReleaseProofError):
        _fail()
    return candidate_id, tag, commit, archive_digest, serving_digest


def _rollback_binding(
    rollback_receipt: Mapping[str, object],
    *,
    candidate_id: str,
    serving_digest: str,
) -> str:
    """Validate the existing non-promoting rollback and return its identity."""
    fault = FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID
    try:
        verify_receipt_identity(rollback_receipt, fault)
        rollback_id = evidence_id(rollback_receipt.get("evidence_id"), fault)
        before = sha256_digest(rollback_receipt.get("serving_before"), fault)
        after = sha256_digest(rollback_receipt.get("serving_after"), fault)
        lease_before = sha256_digest(rollback_receipt.get("active_lease_before"), fault)
        lease_after = sha256_digest(rollback_receipt.get("active_lease_after"), fault)
    except FreeLLMAPIReleaseProofError:
        _fail()
    if (
        rollback_receipt.get("schema_version") != 1
        or rollback_receipt.get("gate") != FREELLMAPI_ROLLBACK_GATE
        or rollback_receipt.get("candidate_id") != candidate_id
        or rollback_receipt.get("green_candidate") != candidate_id
        or before != serving_digest
        or after != serving_digest
        or lease_before != lease_after
        or rollback_receipt.get("protected_artifacts") != [serving_digest]
        or rollback_receipt.get("promotion_attempted") is not False
        or rollback_receipt.get("decision")
        != "rollback_verified_promotion_blocked"
        or rollback_receipt.get("runtime_admitted") is not False
    ):
        _fail()
    return rollback_id


def _workload_outcome(
    result: WorkloadResult,
    *,
    expected_passed: bool,
) -> dict[str, object]:
    """Reduce one completed workload to deterministic content-free evidence."""
    if (
        not isinstance(result, WorkloadResult)
        or result.phase != "completed"
        or len(result.candidates) != 1
        or len(result.executions) != 1
    ):
        _fail()
    execution = result.executions[0]
    candidate = result.candidates[0]
    envelope = execution.envelope
    identity_digest = execution.evidence_identity_digest
    if (
        execution.candidate_identity != candidate.identity
        or identity_digest != candidate.identity.evidence_identity_digest
        or _DIGEST_RE.fullmatch(identity_digest) is None
        or execution.passed is not expected_passed
        or not isinstance(execution.request, str)
        or not execution.request
        or not isinstance(execution.response, str)
        or not isinstance(execution.claim_id, str)
        or not execution.claim_id
        or envelope is None
        or envelope.protocol != "gludd-self-improve-workload-v1"
        or envelope.request != execution.request
        or envelope.response != execution.response
        or envelope.metadata
        != {
            "provider": candidate.identity.provider.value,
            "evidence_identity_digest": identity_digest,
        }
        or result.evaluation
        != {
            "total": 1,
            "passed": int(expected_passed),
            "pass_rate": float(expected_passed),
        }
        or result.learned_outcomes
        != [{"status": "recorded", "evaluation": result.evaluation}]
        or execution.response.startswith("proposal:") is not expected_passed
    ):
        _fail()
    return {
        "decision": "accepted" if expected_passed else "rejected",
        "passed": expected_passed,
        "provider": candidate.identity.provider.value,
        "candidate_evidence_identity_sha256": identity_digest,
        "task_sha256": _text_digest(execution.request),
        "response_sha256": _text_digest(execution.response),
        "claim_sha256": _text_digest(execution.claim_id),
        "protocol": envelope.protocol,
    }


def _validate_outcome(value: object, *, expected_passed: bool) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != _OUTCOME_KEYS:
        _fail()
    expected_decision = "accepted" if expected_passed else "rejected"
    if (
        value.get("decision") != expected_decision
        or value.get("passed") is not expected_passed
        or value.get("protocol") != "gludd-self-improve-workload-v1"
        or not isinstance(value.get("provider"), str)
    ):
        _fail()
    for key in (
        "candidate_evidence_identity_sha256",
        "task_sha256",
        "response_sha256",
        "claim_sha256",
    ):
        item = value.get(key)
        if not isinstance(item, str) or _DIGEST_RE.fullmatch(item) is None:
            _fail()
    return value


def build_freellmapi_workload_proof(
    *,
    candidate_lock: Mapping[str, object],
    rollback_receipt: Mapping[str, object],
    accepted_workload: WorkloadResult,
    rejected_workload: WorkloadResult,
) -> dict[str, object]:
    """Build a deterministic accepted/rejected proof that remains on HOLD."""
    candidate_id, tag, commit, archive_digest, serving_digest = _candidate_facts(
        candidate_lock
    )
    rollback_id = _rollback_binding(
        rollback_receipt,
        candidate_id=candidate_id,
        serving_digest=serving_digest,
    )
    accepted = _workload_outcome(accepted_workload, expected_passed=True)
    rejected = _workload_outcome(rejected_workload, expected_passed=False)
    shared_keys = (
        "provider",
        "candidate_evidence_identity_sha256",
        "task_sha256",
        "protocol",
    )
    if any(accepted[key] != rejected[key] for key in shared_keys):
        _fail()
    if accepted["claim_sha256"] == rejected["claim_sha256"]:
        _fail()
    receipt: dict[str, object] = {
        "schema_version": FREELLMAPI_WORKLOAD_PROOF_SCHEMA_VERSION,
        "gate": FREELLMAPI_WORKLOAD_PROOF_GATE,
        "candidate_id": candidate_id,
        "candidate_tag": tag,
        "candidate_commit": commit,
        "candidate_source_archive_sha256": archive_digest,
        "accepted_workload": accepted,
        "rejected_workload": rejected,
        "rollback_evidence_id": rollback_id,
        "serving_artifact_sha256": serving_digest,
        "promotion_attempted": False,
        "decision": "workload_pair_verified_rollback_bound_hold",
        "runtime_admitted": False,
    }
    receipt["evidence_id"] = stable_evidence_id(receipt)
    return receipt


def validate_freellmapi_workload_proof(
    receipt: Mapping[str, object],
    *,
    candidate_lock: Mapping[str, object],
    rollback_receipt: Mapping[str, object],
) -> dict[str, object]:
    """Validate a durable workload receipt against candidate and rollback roots."""
    candidate_id, tag, commit, archive_digest, serving_digest = _candidate_facts(
        candidate_lock
    )
    rollback_id = _rollback_binding(
        rollback_receipt,
        candidate_id=candidate_id,
        serving_digest=serving_digest,
    )
    if set(receipt) != _RECEIPT_KEYS:
        _fail()
    try:
        verify_receipt_identity(receipt, FreeLLMAPIReleaseProofFault.RECEIPT_INVALID)
    except FreeLLMAPIReleaseProofError:
        _fail()
    accepted = _validate_outcome(receipt.get("accepted_workload"), expected_passed=True)
    rejected = _validate_outcome(receipt.get("rejected_workload"), expected_passed=False)
    shared_keys = (
        "provider",
        "candidate_evidence_identity_sha256",
        "task_sha256",
        "protocol",
    )
    if (
        receipt.get("schema_version") != FREELLMAPI_WORKLOAD_PROOF_SCHEMA_VERSION
        or receipt.get("gate") != FREELLMAPI_WORKLOAD_PROOF_GATE
        or receipt.get("candidate_id") != candidate_id
        or receipt.get("candidate_tag") != tag
        or receipt.get("candidate_commit") != commit
        or receipt.get("candidate_source_archive_sha256") != archive_digest
        or receipt.get("rollback_evidence_id") != rollback_id
        or receipt.get("serving_artifact_sha256") != serving_digest
        or receipt.get("promotion_attempted") is not False
        or receipt.get("decision")
        != "workload_pair_verified_rollback_bound_hold"
        or receipt.get("runtime_admitted") is not False
        or any(accepted[key] != rejected[key] for key in shared_keys)
        or accepted["claim_sha256"] == rejected["claim_sha256"]
    ):
        _fail()
    return dict(receipt)


__all__ = (
    "FREELLMAPI_WORKLOAD_PROOF_GATE",
    "FREELLMAPI_WORKLOAD_PROOF_SCHEMA_VERSION",
    "FreeLLMAPIWorkloadProofError",
    "build_freellmapi_workload_proof",
    "validate_freellmapi_workload_proof",
)
