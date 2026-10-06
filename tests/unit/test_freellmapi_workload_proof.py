"""Durable hermetic workload proof for the pinned FreeLLMAPI review candidate."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from general_ludd.self_improve.freellmapi_workload_proof import (
    FREELLMAPI_WORKLOAD_PROOF_GATE,
    FreeLLMAPIWorkloadProofError,
    build_freellmapi_workload_proof,
    validate_freellmapi_workload_proof,
)
from general_ludd.self_improve.model_candidates import (
    CandidateBackend,
    LocalGGUFCandidateIdentity,
    ModelCandidateIdentity,
)
from general_ludd.self_improve.workload import (
    SelfImprovementWorkload,
    WorkloadResult,
)

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config/freellmapi"
_EVIDENCE = _ROOT / "docs/evidence/freellmapi_self_improvement_workload_proof.json"
_TASK = "apply one hermetic atomic change"
_IDENTITY = LocalGGUFCandidateIdentity(
    model_id="freellmapi-workload-fixture",
    filename="freellmapi-workload-fixture.gguf",
    artifact_sha256=hashlib.sha256(b"hermetic-local-model").hexdigest(),
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _resign(value: dict[str, object]) -> None:
    unsigned = dict(value)
    unsigned.pop("evidence_id", None)
    payload = json.dumps(
        unsigned, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    value["evidence_id"] = "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _Backend:
    candidate_identity: ModelCandidateIdentity
    response: str

    def generate(
        self,
        request: str,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        assert request == _TASK
        assert max_output_tokens == 100
        assert timeout_seconds == 30.0
        return self.response


class _Registry:
    def __init__(self, backend: CandidateBackend[str, str]) -> None:
        self._backend = backend

    def discover(self) -> tuple[CandidateBackend[str, str], ...]:
        return (self._backend,)


class _TodoStore:
    def __init__(self, claim_id: str) -> None:
        self._claim_id = claim_id
        self.claimed: list[str] = []
        self.released: list[str] = []

    def claim(self, identity: ModelCandidateIdentity) -> str:
        self.claimed.append(identity.evidence_identity_digest)
        return self._claim_id

    def release(self, claim_id: str) -> None:
        self.released.append(claim_id)


def _run(response: str, claim_id: str) -> tuple[WorkloadResult, _TodoStore]:
    store = _TodoStore(claim_id)
    workload = SelfImprovementWorkload(
        [_Registry(_Backend(_IDENTITY, response))],
        todo_store=store,
        max_workers=1,
    )
    return workload.run(_TASK), store


def _proof_inputs() -> tuple[
    dict[str, object],
    dict[str, object],
    WorkloadResult,
    WorkloadResult,
]:
    accepted, accepted_store = _run("proposal:accepted-hermetic-change", "claim-accepted-v1")
    rejected, rejected_store = _run("invalid-hermetic-change", "claim-rejected-v1")
    assert accepted_store.released == ["claim-accepted-v1"]
    assert rejected_store.released == ["claim-rejected-v1"]
    return (
        _load(_CONFIG / "upstream_candidate.json"),
        _load(_CONFIG / "rollback_receipt.json"),
        accepted,
        rejected,
    )


def _build() -> dict[str, object]:
    candidate, rollback, accepted, rejected = _proof_inputs()
    return build_freellmapi_workload_proof(
        candidate_lock=candidate,
        rollback_receipt=rollback,
        accepted_workload=accepted,
        rejected_workload=rejected,
    )


def test_hermetic_pair_replays_the_tracked_durable_receipt() -> None:
    receipt = _build()

    assert receipt == _load(_EVIDENCE)
    assert validate_freellmapi_workload_proof(
        receipt,
        candidate_lock=_load(_CONFIG / "upstream_candidate.json"),
        rollback_receipt=_load(_CONFIG / "rollback_receipt.json"),
    ) == receipt
    assert receipt["gate"] == FREELLMAPI_WORKLOAD_PROOF_GATE
    assert receipt["decision"] == "workload_pair_verified_rollback_bound_hold"
    assert receipt["runtime_admitted"] is False


def test_receipt_is_content_free_and_binds_candidate_task_and_rollback() -> None:
    receipt = _build()
    encoded = json.dumps(receipt, sort_keys=True)
    candidate = _load(_CONFIG / "upstream_candidate.json")
    rollback = _load(_CONFIG / "rollback_receipt.json")

    assert receipt["candidate_id"] == candidate["candidate_id"]
    assert receipt["rollback_evidence_id"] == rollback["evidence_id"]
    assert receipt["serving_artifact_sha256"] == rollback["serving_after"]
    accepted = receipt["accepted_workload"]
    rejected = receipt["rejected_workload"]
    assert isinstance(accepted, dict)
    assert isinstance(rejected, dict)
    assert accepted["decision"] == "accepted"
    assert rejected["decision"] == "rejected"
    assert accepted["task_sha256"] == rejected["task_sha256"]
    assert _TASK not in encoded
    assert "accepted-hermetic-change" not in encoded
    assert "invalid-hermetic-change" not in encoded
    assert "claim-accepted-v1" not in encoded
    assert "claim-rejected-v1" not in encoded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", "sha256:" + "0" * 64),
        ("rollback_evidence_id", "sha256:" + "0" * 64),
        ("serving_artifact_sha256", "0" * 64),
        ("runtime_admitted", True),
        ("evidence_id", "sha256:" + "0" * 64),
    ],
)
def test_tracked_receipt_tampering_fails_closed(field: str, value: object) -> None:
    receipt = _build()
    receipt[field] = value

    with pytest.raises(FreeLLMAPIWorkloadProofError, match="workload_proof_invalid"):
        validate_freellmapi_workload_proof(
            receipt,
            candidate_lock=_load(_CONFIG / "upstream_candidate.json"),
            rollback_receipt=_load(_CONFIG / "rollback_receipt.json"),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "swapped_outcomes",
        "mismatched_task",
        "mismatched_identity",
        "missing_claim",
        "wrong_phase",
        "wrong_evaluation",
        "rollback_admitted",
        "rollback_promoted",
        "rollback_candidate_drift",
        "candidate_drift",
    ],
)
def test_builder_rejects_incoherent_or_promoting_evidence(mutation: str) -> None:
    candidate, rollback, accepted, rejected = _proof_inputs()
    if mutation == "swapped_outcomes":
        accepted, rejected = rejected, accepted
    elif mutation == "mismatched_task":
        rejected, _ = _run("invalid-hermetic-change", "claim-rejected-v1")
        execution = rejected.executions[0]
        object.__setattr__(execution, "request", "different task")
    elif mutation == "mismatched_identity":
        execution = rejected.executions[0]
        object.__setattr__(execution, "evidence_identity_digest", "0" * 64)
    elif mutation == "missing_claim":
        object.__setattr__(accepted.executions[0], "claim_id", None)
    elif mutation == "wrong_phase":
        object.__setattr__(accepted, "phase", "running")
    elif mutation == "wrong_evaluation":
        accepted.evaluation["passed"] = 0
    elif mutation == "rollback_admitted":
        rollback["runtime_admitted"] = True
    elif mutation == "rollback_promoted":
        rollback["promotion_attempted"] = True
    elif mutation == "rollback_candidate_drift":
        rollback["candidate_id"] = "sha256:" + "0" * 64
    else:
        candidate["candidate_id"] = "sha256:" + "0" * 64

    with pytest.raises(FreeLLMAPIWorkloadProofError, match="workload_proof_invalid"):
        build_freellmapi_workload_proof(
            candidate_lock=candidate,
            rollback_receipt=rollback,
            accepted_workload=accepted,
            rejected_workload=rejected,
        )


def test_public_surface_is_narrow() -> None:
    import general_ludd.self_improve.freellmapi_workload_proof as proof

    assert proof.__all__ == (
        "FREELLMAPI_WORKLOAD_PROOF_GATE",
        "FREELLMAPI_WORKLOAD_PROOF_SCHEMA_VERSION",
        "FreeLLMAPIWorkloadProofError",
        "build_freellmapi_workload_proof",
        "validate_freellmapi_workload_proof",
    )


def test_nested_outcome_tampering_fails_closed() -> None:
    receipt = copy.deepcopy(_build())
    accepted = receipt["accepted_workload"]
    assert isinstance(accepted, dict)
    accepted["claim_sha256"] = "0" * 64

    with pytest.raises(FreeLLMAPIWorkloadProofError, match="workload_proof_invalid"):
        validate_freellmapi_workload_proof(
            receipt,
            candidate_lock=_load(_CONFIG / "upstream_candidate.json"),
            rollback_receipt=_load(_CONFIG / "rollback_receipt.json"),
        )


def test_builder_requires_distinct_claims_and_one_shared_task() -> None:
    candidate, rollback, accepted, rejected = _proof_inputs()
    object.__setattr__(rejected.executions[0], "claim_id", "claim-accepted-v1")
    with pytest.raises(FreeLLMAPIWorkloadProofError):
        build_freellmapi_workload_proof(
            candidate_lock=candidate,
            rollback_receipt=rollback,
            accepted_workload=accepted,
            rejected_workload=rejected,
        )

    candidate, rollback, accepted, rejected = _proof_inputs()
    execution = rejected.executions[0]
    assert execution.envelope is not None
    object.__setattr__(execution, "request", "another atomic task")
    object.__setattr__(execution.envelope, "request", "another atomic task")
    with pytest.raises(FreeLLMAPIWorkloadProofError):
        build_freellmapi_workload_proof(
            candidate_lock=candidate,
            rollback_receipt=rollback,
            accepted_workload=accepted,
            rejected_workload=rejected,
        )


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("missing_key", None),
        ("wrong_decision", "rejected"),
        ("invalid_digest", "not-a-digest"),
    ],
)
def test_resigned_nested_outcome_forgery_fails_closed(
    mutation: str, value: object
) -> None:
    receipt = _build()
    accepted = receipt["accepted_workload"]
    assert isinstance(accepted, dict)
    if mutation == "missing_key":
        accepted.pop("protocol")
    elif mutation == "wrong_decision":
        accepted["decision"] = value
    else:
        accepted["claim_sha256"] = value
    _resign(receipt)

    with pytest.raises(FreeLLMAPIWorkloadProofError):
        validate_freellmapi_workload_proof(
            receipt,
            candidate_lock=_load(_CONFIG / "upstream_candidate.json"),
            rollback_receipt=_load(_CONFIG / "rollback_receipt.json"),
        )


def test_resigned_receipt_and_rollback_contradictions_fail_closed() -> None:
    receipt = _build()
    receipt["runtime_admitted"] = True
    _resign(receipt)
    with pytest.raises(FreeLLMAPIWorkloadProofError):
        validate_freellmapi_workload_proof(
            receipt,
            candidate_lock=_load(_CONFIG / "upstream_candidate.json"),
            rollback_receipt=_load(_CONFIG / "rollback_receipt.json"),
        )

    candidate, rollback, accepted, rejected = _proof_inputs()
    rollback["promotion_attempted"] = True
    _resign(rollback)
    with pytest.raises(FreeLLMAPIWorkloadProofError):
        build_freellmapi_workload_proof(
            candidate_lock=candidate,
            rollback_receipt=rollback,
            accepted_workload=accepted,
            rejected_workload=rejected,
        )
