"""Frozen-corpus, native-provider, and rollback proof for FreeLLMAPI."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest

import general_ludd.models.freellmapi_release_corpus as release_corpus_module
from general_ludd.models.freellmapi_release_proof import (
    FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
    FreeLLMAPIReleaseProofError,
    FreeLLMAPIReleaseProofFault,
    build_live_provider_receipt,
    exercise_release_rollback,
    run_frozen_corpus_proof,
    validate_tracked_release_receipts,
)
from general_ludd.self_improve.azure_backend import CandidateBackendAccounting
from general_ludd.self_improve.freellmapi_backend_types import (
    FreeLLMAPIBackendTrace,
    FreeLLMAPITraceEvent,
)
from general_ludd.self_improve.model_candidates import BackendFailure

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _ROOT / "config/freellmapi"
_TRACKED_PROVENANCE_ID = (
    "sha256:0e9fdb5759bef683c5a71f929cba4d551c1b65784f362536f5e404ae5f186bc1"
)


class _ComplexityMetrics(Protocol):
    loc: int
    maintainability_index: float


class _ComplexityModule(Protocol):
    def _analyze_file(self, path: Path) -> _ComplexityMetrics: ...


def _load(name: str) -> dict[str, object]:
    value = json.loads((_CONFIG / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _resign(receipt: dict[str, object]) -> dict[str, object]:
    unsigned = dict(receipt)
    unsigned.pop("evidence_id", None)
    receipt["evidence_id"] = "sha256:" + _canonical_digest(unsigned)
    return receipt


def _provenance(
    *,
    candidate_id: object,
    corpus_receipt: dict[str, object],
    live_receipt: dict[str, object],
    rollback_receipt: dict[str, object],
    external_block: str,
) -> dict[str, object]:
    return _resign(
        {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "corpus_evidence_id": corpus_receipt["evidence_id"],
            "live_provider_evidence_id": live_receipt["evidence_id"],
            "rollback_evidence_id": rollback_receipt["evidence_id"],
            "decision": "proof_chain_verified_promotion_blocked",
            "external_block": external_block,
            "runtime_admitted": False,
        }
    )


def _relinked_chain(
    candidate: dict[str, object],
    corpus: dict[str, object],
    plan: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    corpus_receipt = run_frozen_corpus_proof(
        candidate_lock=candidate,
        corpus=corpus,
        plan=plan,
        abi_manifest=_abi(),
    )
    live_receipt = copy.deepcopy(_load("live_provider_receipt.json"))
    live_receipt["candidate_id"] = candidate["candidate_id"]
    live_receipt["corpus_evidence_id"] = corpus_receipt["evidence_id"]
    _resign(live_receipt)
    rollback_receipt = exercise_release_rollback(
        candidate_lock=candidate,
        corpus_proof=corpus_receipt,
        live_provider_receipt=live_receipt,
        active_lease_digest="f" * 64,
    )
    return corpus_receipt, live_receipt, rollback_receipt


def _corpus(candidate: dict[str, object]) -> dict[str, object]:
    admitted = cast(dict[str, object], candidate["admitted_artifact"])
    fixtures: list[dict[str, object]] = []
    cases = (
        ("a" * 64, 18.0, 2.0, 4.0, 1.0, True),
        ("b" * 64, 12.0, 1.0, 2.0, 1.0, True),
        ("c" * 64, 1.0, 12.0, 1.0, 3.0, False),
        ("d" * 64, 2.0, 18.0, 1.0, 4.0, False),
    )
    for identity, successes, failures, community_successes, community_failures, outcome in cases:
        fixture: dict[str, object] = {
            "candidate_identity_digest": identity,
            "successes": successes,
            "failures": failures,
            "community_successes": community_successes,
            "community_failures": community_failures,
            "tokens_per_second": 60.0,
            "ttfb_ms": 300.0,
            "used_tokens": 100.0,
            "budget_tokens": 1000.0,
            "rate_window_used_fraction": 0.1,
            "rate_limit_penalty": 0.0,
            "held_out_success": outcome,
            "baseline_expected_reliability": 0.5,
        }
        fixture["fixture_digest"] = _canonical_digest(fixture)
        fixtures.append(fixture)
    return {
        "schema_version": 1,
        "candidate_id": candidate["candidate_id"],
        "artifact_bundle_sha256": admitted["bundle_sha256"],
        "artifact_upstream_commit": admitted["upstream_commit"],
        "selected_export": "expectedReliability",
        "fixtures": fixtures,
    }


def _plan(candidate: dict[str, object], corpus: dict[str, object]) -> dict[str, object]:
    fixtures = cast(list[dict[str, object]], corpus["fixtures"])
    fixture_digests = [fixture["fixture_digest"] for fixture in fixtures]
    return {
        "schema_version": 1,
        "gate": "freellmapi-frozen-delta-v1",
        "candidate_id": candidate["candidate_id"],
        "selected_export": "expectedReliability",
        "capability_id": "score_candidate_features",
        "abi_version": 1,
        "fixture_digests": fixture_digests,
        "corpus_sha256": _canonical_digest({"fixture_digests": fixture_digests}),
        "metric": "paired-adjusted-quality-v1",
        "minimum_adjusted_gain": 0.02,
        "confidence_z": 1.96,
        "penalties": {
            "latency_ms": 0.0,
            "memory_mib": 0.0,
            "cost_usd": 1.0,
            "failure": 0.5,
        },
        "owner": "general_ludd.models",
        "observation_window": "four-held-out-binary-provider-outcomes",
        "removal_condition": "remove if lower confidence bound falls below 0.02",
    }


def _abi() -> dict[str, object]:
    return {
        "abi_version": 1,
        "operation": "score_candidate_features",
        "upstream_export": "expectedReliability",
        "input_schema_sha256": hashlib.sha256(b"freellmapi-frozen-score-input-v1").hexdigest(),
        "output_schema_sha256": hashlib.sha256(b"freellmapi-frozen-score-output-v1").hexdigest(),
        "host_capabilities": [],
    }


def test_real_pinned_kernel_proves_positive_and_removal_paths() -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _corpus(candidate)

    proof = run_frozen_corpus_proof(
        candidate_lock=candidate,
        corpus=corpus,
        plan=_plan(candidate, corpus),
        abi_manifest=_abi(),
    )

    assert proof["schema_version"] == FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION
    assert proof["decision"] == "frozen_corpus_verified"
    accepted = cast(dict[str, object], proof["accepted"])
    rejected = cast(dict[str, object], proof["rejected"])
    assert accepted["decision"] == "accepted_for_build_review"
    assert rejected["decision"] == "rejected_nonpositive_delta"
    assert proof["runtime_admitted"] is False
    assert proof["artifact_upstream_commit"] == "780a7d8d6dcbc818eb10ec17da210635b569ae22"
    assert proof["candidate_id"] == candidate["candidate_id"]


def test_live_receipt_binds_native_trace_without_provider_content() -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _corpus(candidate)
    proof = run_frozen_corpus_proof(
        candidate_lock=candidate,
        corpus=corpus,
        plan=_plan(candidate, corpus),
        abi_manifest=_abi(),
    )
    candidate_digest = "e" * 64
    traces = (
        FreeLLMAPIBackendTrace(
            event=FreeLLMAPITraceEvent.REQUEST_STARTED,
            candidate_digest=candidate_digest,
            request_number=1,
        ),
        FreeLLMAPIBackendTrace(
            event=FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
            candidate_digest=candidate_digest,
            request_number=1,
            input_tokens=7,
            output_tokens=3,
            total_tokens=10,
        ),
    )

    receipt = build_live_provider_receipt(
        candidate_id=str(candidate["candidate_id"]),
        corpus_evidence_id=str(proof["evidence_id"]),
        provider="zai",
        model="glm-5.1",
        traces=traces,
        accounting=CandidateBackendAccounting(
            requests_started=1,
            responses_received=1,
            responses_accepted=1,
            requests_failed=0,
            provider_input_tokens=7,
            provider_output_tokens=3,
            provider_total_tokens=10,
        ),
        observed_at="2026-09-27T12:00:00Z",
        external_opt_in=True,
        queue_empty_after=True,
        provisioned_compute_remaining=0,
    )

    payload = json.dumps(receipt, sort_keys=True)
    assert receipt["decision"] == "live_provider_verified"
    assert receipt["transport_owner"] == "general_ludd.models.gateway"
    assert receipt["runtime_admitted"] is False
    assert receipt["provider"] == "zai"
    assert receipt["provider_model_sha256"] == hashlib.sha256(b"zai\0glm-5.1").hexdigest()
    assert "glm-5.1" not in payload
    assert candidate_digest in payload


def test_rollback_restores_blue_and_preserves_active_lease() -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _corpus(candidate)
    corpus_proof = run_frozen_corpus_proof(
        candidate_lock=candidate,
        corpus=corpus,
        plan=_plan(candidate, corpus),
        abi_manifest=_abi(),
    )
    live = build_live_provider_receipt(
        candidate_id=str(candidate["candidate_id"]),
        corpus_evidence_id=str(corpus_proof["evidence_id"]),
        provider="zai",
        model="glm-5.1",
        traces=(
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_STARTED,
                candidate_digest="e" * 64,
                request_number=1,
            ),
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
                candidate_digest="e" * 64,
                request_number=1,
                input_tokens=7,
                output_tokens=3,
                total_tokens=10,
            ),
        ),
        accounting=CandidateBackendAccounting(1, 1, 1, 0, 7, 3, 10),
        observed_at="2026-09-27T12:00:00Z",
        external_opt_in=True,
        queue_empty_after=True,
        provisioned_compute_remaining=0,
    )

    receipt = exercise_release_rollback(
        candidate_lock=candidate,
        corpus_proof=corpus_proof,
        live_provider_receipt=live,
        active_lease_digest="f" * 64,
    )

    admitted = cast(dict[str, object], candidate["admitted_artifact"])
    assert receipt["decision"] == "rollback_verified_promotion_blocked"
    assert receipt["serving_before"] == admitted["bundle_sha256"]
    assert receipt["serving_after"] == admitted["bundle_sha256"]
    assert receipt["active_lease_before"] == receipt["active_lease_after"]
    assert receipt["protected_artifacts"] == [admitted["bundle_sha256"]]
    assert receipt["promotion_attempted"] is False
    assert receipt["runtime_admitted"] is False


def test_live_rate_limit_is_a_real_content_free_rejection_receipt() -> None:
    receipt = build_live_provider_receipt(
        candidate_id="sha256:" + "a" * 64,
        corpus_evidence_id="sha256:" + "b" * 64,
        provider="zai",
        model="glm-5.1",
        traces=(
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_STARTED,
                candidate_digest="c" * 64,
                request_number=1,
            ),
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_FAILED,
                candidate_digest="c" * 64,
                request_number=1,
                failure=BackendFailure.RATE_LIMITED,
            ),
        ),
        accounting=CandidateBackendAccounting(1, 0, 0, 1, 0, 0, 0),
        observed_at="2026-09-27T12:00:00Z",
        external_opt_in=True,
        queue_empty_after=True,
        provisioned_compute_remaining=0,
    )

    assert receipt["decision"] == "live_provider_rejected"
    assert receipt["provider_failure"] == "rate_limited"
    assert receipt["runtime_admitted"] is False
    assert "余额不足" not in json.dumps(receipt, ensure_ascii=False)


def test_tampered_corpus_live_trace_and_receipt_fail_closed() -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _corpus(candidate)
    plan = _plan(candidate, corpus)
    fixtures = cast(list[dict[str, object]], corpus["fixtures"])
    fixtures[0]["successes"] = 99.0
    with pytest.raises(FreeLLMAPIReleaseProofError) as corpus_error:
        run_frozen_corpus_proof(
            candidate_lock=candidate,
            corpus=corpus,
            plan=plan,
            abi_manifest=_abi(),
        )
    assert corpus_error.value.fault is FreeLLMAPIReleaseProofFault.CORPUS_INVALID

    with pytest.raises(FreeLLMAPIReleaseProofError) as live_error:
        build_live_provider_receipt(
            candidate_id=str(candidate["candidate_id"]),
            corpus_evidence_id="sha256:" + "a" * 64,
            provider="zai",
            model="glm-5.1",
            traces=(),
            accounting=CandidateBackendAccounting(1, 1, 1, 0, 1, 1, 2),
            observed_at="2026-09-27T12:00:00Z",
            external_opt_in=True,
            queue_empty_after=True,
            provisioned_compute_remaining=0,
        )
    assert live_error.value.fault is FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_id", "bad"),
        ("corpus_evidence_id", "bad"),
        ("provider", "ZAI"),
        ("model", ""),
        ("external_opt_in", False),
        ("queue_empty_after", False),
        ("provisioned_compute_remaining", 1),
        ("observed_at", "not-a-timestamp"),
        ("accounting", object()),
    ],
)
def test_live_receipt_rejects_unbounded_or_unverifiable_inputs(
    field: str, value: object
) -> None:
    kwargs: dict[str, object] = {
        "candidate_id": "sha256:" + "a" * 64,
        "corpus_evidence_id": "sha256:" + "b" * 64,
        "provider": "zai",
        "model": "glm-5.1",
        "traces": (
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_STARTED,
                candidate_digest="c" * 64,
                request_number=1,
            ),
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
                candidate_digest="c" * 64,
                request_number=1,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
        ),
        "accounting": CandidateBackendAccounting(1, 1, 1, 0, 1, 1, 2),
        "observed_at": "2026-09-27T12:00:00Z",
        "external_opt_in": True,
        "queue_empty_after": True,
        "provisioned_compute_remaining": 0,
    }
    kwargs[field] = value

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        build_live_provider_receipt(
            candidate_id=cast(str, kwargs["candidate_id"]),
            corpus_evidence_id=cast(str, kwargs["corpus_evidence_id"]),
            provider=cast(str, kwargs["provider"]),
            model=cast(str, kwargs["model"]),
            traces=cast(Sequence[object], kwargs["traces"]),
            accounting=kwargs["accounting"],
            observed_at=cast(str, kwargs["observed_at"]),
            external_opt_in=cast(bool, kwargs["external_opt_in"]),
            queue_empty_after=cast(bool, kwargs["queue_empty_after"]),
            provisioned_compute_remaining=cast(
                int, kwargs["provisioned_compute_remaining"]
            ),
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.LIVE_PROVIDER_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        lambda corpus: corpus.__setitem__("schema_version", 2),
        lambda corpus: corpus.__setitem__("candidate_id", "sha256:" + "0" * 64),
        lambda corpus: corpus.__setitem__("selected_export", "speedScore"),
        lambda corpus: corpus.__setitem__("artifact_bundle_sha256", "0" * 64),
        lambda corpus: corpus.__setitem__("fixtures", []),
    ],
)
def test_corpus_metadata_drift_fails_before_execution(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _corpus(candidate)
    plan = _plan(candidate, corpus)
    mutation(corpus)

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        run_frozen_corpus_proof(
            candidate_lock=candidate,
            corpus=corpus,
            plan=plan,
            abi_manifest=_abi(),
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.CORPUS_INVALID


def test_receipt_identity_and_rollback_candidate_drift_fail_closed() -> None:
    candidate = _load("upstream_candidate.json")
    corpus_receipt = _load("frozen_corpus_receipt.json")
    live_receipt = _load("live_provider_receipt.json")
    live_receipt["evidence_id"] = "sha256:" + "0" * 64
    with pytest.raises(FreeLLMAPIReleaseProofError) as receipt_error:
        exercise_release_rollback(
            candidate_lock=candidate,
            corpus_proof=corpus_receipt,
            live_provider_receipt=live_receipt,
            active_lease_digest="f" * 64,
        )
    assert receipt_error.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID

    candidate["candidate_id"] = "not-an-identity"
    with pytest.raises(FreeLLMAPIReleaseProofError) as candidate_error:
        exercise_release_rollback(
            candidate_lock=candidate,
            corpus_proof=corpus_receipt,
            live_provider_receipt=_load("live_provider_receipt.json"),
            active_lease_digest="f" * 64,
        )
    assert candidate_error.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID


def test_tracked_receipts_are_self_consistent_and_non_promoting() -> None:
    provenance = _load("release_provenance_receipt.json")
    result = validate_tracked_release_receipts(
        candidate_lock=_load("upstream_candidate.json"),
        corpus=_load("frozen_corpus.json"),
        plan=_load("frozen_delta_plan.json"),
        abi_manifest=_load("frozen_delta_abi.json"),
        corpus_receipt=_load("frozen_corpus_receipt.json"),
        live_provider_receipt=_load("live_provider_receipt.json"),
        rollback_receipt=_load("rollback_receipt.json"),
        provenance_receipt=provenance,
        trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
    )

    assert result == provenance
    assert result["decision"] == "proof_chain_verified_promotion_blocked"
    assert result["runtime_admitted"] is False
    assert result["external_block"] == (
        "live_provider_rate_limited_and_missing_v0_11_1_reproducible_"
        "bundle_and_ci_provenance"
    )

    tampered = copy.deepcopy(_load("rollback_receipt.json"))
    tampered["serving_after"] = "0" * 64
    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        validate_tracked_release_receipts(
            candidate_lock=_load("upstream_candidate.json"),
            corpus=_load("frozen_corpus.json"),
            plan=_load("frozen_delta_plan.json"),
            abi_manifest=_load("frozen_delta_abi.json"),
            corpus_receipt=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=_load("live_provider_receipt.json"),
            rollback_receipt=tampered,
            provenance_receipt=provenance,
            trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
        )
    assert caught.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID


def test_coordinated_corpus_tampering_cannot_replace_trusted_chain() -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _load("frozen_corpus.json")
    plan = _load("frozen_delta_plan.json")
    fixtures = cast(list[dict[str, object]], corpus["fixtures"])
    fixtures.reverse()
    fixture_digests = [fixture["fixture_digest"] for fixture in fixtures]
    plan["fixture_digests"] = fixture_digests
    plan["corpus_sha256"] = _canonical_digest(
        {"fixture_digests": fixture_digests}
    )
    corpus_receipt, live_receipt, rollback_receipt = _relinked_chain(
        candidate, corpus, plan
    )
    forged_provenance = _provenance(
        candidate_id=candidate["candidate_id"],
        corpus_receipt=corpus_receipt,
        live_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        external_block=(
            "live_provider_rate_limited_and_missing_v0_11_1_reproducible_"
            "bundle_and_ci_provenance"
        ),
    )

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        validate_tracked_release_receipts(
            candidate_lock=candidate,
            corpus=corpus,
            plan=plan,
            abi_manifest=_abi(),
            corpus_receipt=corpus_receipt,
            live_provider_receipt=live_receipt,
            rollback_receipt=rollback_receipt,
            provenance_receipt=forged_provenance,
            trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID


def test_upstream_revision_and_delta_config_drift_cannot_replace_trusted_chain() -> None:
    candidate = _load("upstream_candidate.json")
    upstream = cast(dict[str, object], candidate["upstream"])
    upstream["commit"] = "1" * 40
    candidate.pop("candidate_id")
    candidate["candidate_id"] = "sha256:" + _canonical_digest(candidate)
    corpus = _load("frozen_corpus.json")
    corpus["candidate_id"] = candidate["candidate_id"]
    plan = _load("frozen_delta_plan.json")
    plan["candidate_id"] = candidate["candidate_id"]
    plan["confidence_z"] = 1.95
    corpus_receipt, live_receipt, rollback_receipt = _relinked_chain(
        candidate, corpus, plan
    )
    forged_provenance = _provenance(
        candidate_id=candidate["candidate_id"],
        corpus_receipt=corpus_receipt,
        live_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        external_block=(
            "live_provider_rate_limited_and_missing_v0_11_1_reproducible_"
            "bundle_and_ci_provenance"
        ),
    )

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        validate_tracked_release_receipts(
            candidate_lock=candidate,
            corpus=corpus,
            plan=plan,
            abi_manifest=_abi(),
            corpus_receipt=corpus_receipt,
            live_provider_receipt=live_receipt,
            rollback_receipt=rollback_receipt,
            provenance_receipt=forged_provenance,
            trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID


def test_forged_live_receipt_with_valid_content_hash_fails_rollback() -> None:
    live_receipt = _load("live_provider_receipt.json")
    live_receipt["request_count"] = 9
    _resign(live_receipt)

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        exercise_release_rollback(
            candidate_lock=_load("upstream_candidate.json"),
            corpus_proof=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=live_receipt,
            active_lease_digest="f" * 64,
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID


def test_rollback_revalidates_candidate_and_corpus_provenance() -> None:
    candidate = _load("upstream_candidate.json")
    upstream = cast(dict[str, object], candidate["upstream"])
    upstream["commit"] = "1" * 40
    with pytest.raises(FreeLLMAPIReleaseProofError) as candidate_error:
        exercise_release_rollback(
            candidate_lock=candidate,
            corpus_proof=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=_load("live_provider_receipt.json"),
            active_lease_digest="f" * 64,
        )
    assert candidate_error.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID

    corpus_receipt = _load("frozen_corpus_receipt.json")
    corpus_receipt["artifact_upstream_commit"] = "1" * 40
    _resign(corpus_receipt)
    live_receipt = _load("live_provider_receipt.json")
    live_receipt["corpus_evidence_id"] = corpus_receipt["evidence_id"]
    _resign(live_receipt)
    with pytest.raises(FreeLLMAPIReleaseProofError) as corpus_error:
        exercise_release_rollback(
            candidate_lock=_load("upstream_candidate.json"),
            corpus_proof=corpus_receipt,
            live_provider_receipt=live_receipt,
            active_lease_digest="f" * 64,
        )
    assert corpus_error.value.fault is FreeLLMAPIReleaseProofFault.ROLLBACK_INVALID


@pytest.mark.parametrize(
    "shared_field",
    [
        "candidate_id",
        "plan_id",
        "corpus_sha256",
        "selected_export",
        "capability_id",
        "abi_compatible",
        "fixture_count",
    ],
)
def test_accepted_and_rejected_delta_receipts_require_identical_context(
    monkeypatch: pytest.MonkeyPatch, shared_field: str
) -> None:
    candidate = _load("upstream_candidate.json")
    corpus = _load("frozen_corpus.json")
    plan = _load("frozen_delta_plan.json")
    baseline = run_frozen_corpus_proof(
        candidate_lock=candidate,
        corpus=corpus,
        plan=plan,
        abi_manifest=_abi(),
    )
    accepted = copy.deepcopy(cast(dict[str, object], baseline["accepted"]))
    rejected = copy.deepcopy(cast(dict[str, object], baseline["rejected"]))
    rejected[shared_field] = (
        not rejected[shared_field]
        if isinstance(rejected[shared_field], bool)
        else 99
        if isinstance(rejected[shared_field], int)
        else "sha256:" + "0" * 64
    )
    _resign(rejected)
    receipts = iter((accepted, rejected))
    monkeypatch.setattr(
        release_corpus_module,
        "evaluate_frozen_delta",
        lambda **_kwargs: next(receipts),
    )

    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        run_frozen_corpus_proof(
            candidate_lock=candidate,
            corpus=corpus,
            plan=plan,
            abi_manifest=_abi(),
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.CORPUS_INVALID


@pytest.mark.parametrize(
    ("provider_failure", "build_failure", "expected_block"),
    [
        (
            "timeout",
            None,
            "live_provider_timeout_and_missing_v0_11_1_reproducible_bundle_"
            "and_ci_provenance",
        ),
        (
            "rate_limited",
            "toolchain_invalid",
            "live_provider_rate_limited_and_upstream_build_toolchain_invalid_"
            "and_missing_v0_11_1_reproducible_bundle_and_ci_provenance",
        ),
    ],
)
def test_provider_429_and_upstream_toolchain_failures_remain_distinct(
    provider_failure: str,
    build_failure: str | None,
    expected_block: str,
) -> None:
    candidate = _load("upstream_candidate.json")
    corpus_receipt = _load("frozen_corpus_receipt.json")
    live_receipt = _load("live_provider_receipt.json")
    live_receipt["provider_failure"] = provider_failure
    _resign(live_receipt)
    rollback_receipt = exercise_release_rollback(
        candidate_lock=candidate,
        corpus_proof=corpus_receipt,
        live_provider_receipt=live_receipt,
        active_lease_digest="f" * 64,
    )
    provenance = _provenance(
        candidate_id=candidate["candidate_id"],
        corpus_receipt=corpus_receipt,
        live_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        external_block=expected_block,
    )

    result = validate_tracked_release_receipts(
        candidate_lock=candidate,
        corpus=_load("frozen_corpus.json"),
        plan=_load("frozen_delta_plan.json"),
        abi_manifest=_load("frozen_delta_abi.json"),
        corpus_receipt=corpus_receipt,
        live_provider_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        provenance_receipt=provenance,
        trusted_provenance_evidence_id=cast(str, provenance["evidence_id"]),
        upstream_build_failure=build_failure,
    )

    assert result["external_block"] == expected_block


def test_provider_failure_cannot_be_mislabeled_as_toolchain_failure() -> None:
    provenance = _load("release_provenance_receipt.json")
    with pytest.raises(FreeLLMAPIReleaseProofError) as caught:
        validate_tracked_release_receipts(
            candidate_lock=_load("upstream_candidate.json"),
            corpus=_load("frozen_corpus.json"),
            plan=_load("frozen_delta_plan.json"),
            abi_manifest=_load("frozen_delta_abi.json"),
            corpus_receipt=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=_load("live_provider_receipt.json"),
            rollback_receipt=_load("rollback_receipt.json"),
            provenance_receipt=provenance,
            trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
            upstream_build_failure="rate_limited",
        )

    assert caught.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID


def test_trusted_root_binds_candidate_corpus_and_final_verdict() -> None:
    candidate = _load("upstream_candidate.json")
    wrong_candidate_root = _load("release_provenance_receipt.json")
    wrong_candidate_root["candidate_id"] = "sha256:" + "0" * 64
    _resign(wrong_candidate_root)
    with pytest.raises(FreeLLMAPIReleaseProofError) as candidate_error:
        validate_tracked_release_receipts(
            candidate_lock=candidate,
            corpus=_load("frozen_corpus.json"),
            plan=_load("frozen_delta_plan.json"),
            abi_manifest=_load("frozen_delta_abi.json"),
            corpus_receipt=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=_load("live_provider_receipt.json"),
            rollback_receipt=_load("rollback_receipt.json"),
            provenance_receipt=wrong_candidate_root,
            trusted_provenance_evidence_id=cast(
                str, wrong_candidate_root["evidence_id"]
            ),
        )
    assert candidate_error.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID

    wrong_corpus = _load("frozen_corpus_receipt.json")
    wrong_corpus["fixture_count"] = 5
    _resign(wrong_corpus)
    with pytest.raises(FreeLLMAPIReleaseProofError) as corpus_error:
        validate_tracked_release_receipts(
            candidate_lock=candidate,
            corpus=_load("frozen_corpus.json"),
            plan=_load("frozen_delta_plan.json"),
            abi_manifest=_load("frozen_delta_abi.json"),
            corpus_receipt=wrong_corpus,
            live_provider_receipt=_load("live_provider_receipt.json"),
            rollback_receipt=_load("rollback_receipt.json"),
            provenance_receipt=_load("release_provenance_receipt.json"),
            trusted_provenance_evidence_id=_TRACKED_PROVENANCE_ID,
        )
    assert corpus_error.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID

    wrong_verdict = _load("release_provenance_receipt.json")
    wrong_verdict["external_block"] = "missing_reviewed_build"
    _resign(wrong_verdict)
    with pytest.raises(FreeLLMAPIReleaseProofError) as verdict_error:
        validate_tracked_release_receipts(
            candidate_lock=candidate,
            corpus=_load("frozen_corpus.json"),
            plan=_load("frozen_delta_plan.json"),
            abi_manifest=_load("frozen_delta_abi.json"),
            corpus_receipt=_load("frozen_corpus_receipt.json"),
            live_provider_receipt=_load("live_provider_receipt.json"),
            rollback_receipt=_load("rollback_receipt.json"),
            provenance_receipt=wrong_verdict,
            trusted_provenance_evidence_id=cast(str, wrong_verdict["evidence_id"]),
        )
    assert verdict_error.value.fault is FreeLLMAPIReleaseProofFault.RECEIPT_INVALID


def test_successful_live_call_has_no_provider_failure_block() -> None:
    candidate = _load("upstream_candidate.json")
    corpus_receipt = _load("frozen_corpus_receipt.json")
    live_receipt = build_live_provider_receipt(
        candidate_id=cast(str, candidate["candidate_id"]),
        corpus_evidence_id=cast(str, corpus_receipt["evidence_id"]),
        provider="zai",
        model="glm-5.1",
        traces=(
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_STARTED,
                candidate_digest="e" * 64,
                request_number=1,
            ),
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
                candidate_digest="e" * 64,
                request_number=1,
                input_tokens=7,
                output_tokens=3,
                total_tokens=10,
            ),
        ),
        accounting=CandidateBackendAccounting(1, 1, 1, 0, 7, 3, 10),
        observed_at="2026-09-27T12:00:00Z",
        external_opt_in=True,
        queue_empty_after=True,
        provisioned_compute_remaining=0,
    )
    rollback_receipt = exercise_release_rollback(
        candidate_lock=candidate,
        corpus_proof=corpus_receipt,
        live_provider_receipt=live_receipt,
        active_lease_digest="f" * 64,
    )
    expected_block = "missing_v0_11_1_reproducible_bundle_and_ci_provenance"
    provenance = _provenance(
        candidate_id=candidate["candidate_id"],
        corpus_receipt=corpus_receipt,
        live_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        external_block=expected_block,
    )

    result = validate_tracked_release_receipts(
        candidate_lock=candidate,
        corpus=_load("frozen_corpus.json"),
        plan=_load("frozen_delta_plan.json"),
        abi_manifest=_load("frozen_delta_abi.json"),
        corpus_receipt=corpus_receipt,
        live_provider_receipt=live_receipt,
        rollback_receipt=rollback_receipt,
        provenance_receipt=provenance,
        trusted_provenance_evidence_id=cast(str, provenance["evidence_id"]),
    )

    assert result["external_block"] == expected_block


@pytest.mark.parametrize(
    "module_name",
    [
        "freellmapi_release_common.py",
        "freellmapi_release_corpus.py",
        "freellmapi_release_proof.py",
        "freellmapi_release_provider.py",
    ],
)
def test_release_proof_modules_stay_above_maintainability_floor(
    module_name: str,
) -> None:
    complexity = cast(
        _ComplexityModule,
        importlib.import_module("tests.unit.test_code_complexity_deep"),
    )
    metrics = complexity._analyze_file(
        _ROOT / "src/general_ludd/models" / module_name
    )
    assert metrics.loc > 0
    assert metrics.maintainability_index >= 20.0, repr(metrics)
