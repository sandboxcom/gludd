"""Frozen-corpus execution and delta proof for a pinned FreeLLMAPI artifact."""

from __future__ import annotations

import re
from collections.abc import Mapping

from general_ludd.models.freellmapi_frozen_delta import evaluate_frozen_delta
from general_ludd.models.freellmapi_frozen_delta_validation import validate_candidate
from general_ludd.models.freellmapi_release_common import (
    FREELLMAPI_FROZEN_CORPUS_GATE,
    FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
    FreeLLMAPIReleaseProofFault,
    as_mapping,
    canonical_digest,
    fail,
    nonnegative_number,
    sha256_digest,
    stable_evidence_id,
)
from general_ludd.models.freellmapi_scoring_kernel import (
    FreeLLMScoringFactors,
    FreeLLMScoringInput,
    FreeLLMScoringKernel,
    FreeLLMScoringSource,
)

_CORPUS_KEYS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "artifact_bundle_sha256",
        "artifact_upstream_commit",
        "selected_export",
        "fixtures",
    }
)
_FIXTURE_KEYS = frozenset(
    {
        "candidate_identity_digest",
        "successes",
        "failures",
        "community_successes",
        "community_failures",
        "tokens_per_second",
        "ttfb_ms",
        "used_tokens",
        "budget_tokens",
        "rate_window_used_fraction",
        "rate_limit_penalty",
        "held_out_success",
        "baseline_expected_reliability",
        "fixture_digest",
    }
)


def _fixture(
    raw: object,
) -> tuple[FreeLLMScoringInput, FreeLLMScoringFactors, bool, str]:
    fault = FreeLLMAPIReleaseProofFault.CORPUS_INVALID
    record = as_mapping(raw, fault)
    if set(record) != _FIXTURE_KEYS:
        fail(fault)
    unsigned = dict(record)
    stored_digest = sha256_digest(unsigned.pop("fixture_digest", None), fault)
    if canonical_digest(unsigned, fault) != stored_digest:
        fail(fault)
    outcome = record.get("held_out_success")
    if not isinstance(outcome, bool):
        fail(fault)
    identity = sha256_digest(record.get("candidate_identity_digest"), fault)
    baseline = nonnegative_number(
        record.get("baseline_expected_reliability"), fault, maximum=1.0
    )
    scoring_input = FreeLLMScoringInput(
        candidate_identity_digest=identity,
        successes=nonnegative_number(record.get("successes"), fault),
        failures=nonnegative_number(record.get("failures"), fault),
        community_successes=nonnegative_number(
            record.get("community_successes"), fault
        ),
        community_failures=nonnegative_number(
            record.get("community_failures"), fault
        ),
        tokens_per_second=nonnegative_number(
            record.get("tokens_per_second"), fault
        ),
        ttfb_ms=(
            None
            if record.get("ttfb_ms") is None
            else nonnegative_number(record.get("ttfb_ms"), fault)
        ),
        used_tokens=nonnegative_number(record.get("used_tokens"), fault),
        budget_tokens=nonnegative_number(record.get("budget_tokens"), fault),
        rate_window_used_fraction=(
            None
            if record.get("rate_window_used_fraction") is None
            else nonnegative_number(
                record.get("rate_window_used_fraction"), fault, maximum=1.0
            )
        ),
        rate_limit_penalty=nonnegative_number(
            record.get("rate_limit_penalty"), fault, maximum=10.0
        ),
    )
    fallback = FreeLLMScoringFactors(
        candidate_identity_digest=identity,
        reliability_alpha=1.0,
        reliability_beta=1.0,
        expected_reliability=baseline,
        speed=0.5,
        headroom=1.0,
        rate_window_headroom=1.0,
        rate_limit=1.0,
    )
    return scoring_input, fallback, outcome, stored_digest


def _corpus(
    candidate_lock: Mapping[str, object], corpus: Mapping[str, object]
) -> tuple[
    tuple[FreeLLMScoringInput, ...],
    tuple[FreeLLMScoringFactors, ...],
    tuple[bool, ...],
    tuple[str, ...],
    str,
    str,
]:
    fault = FreeLLMAPIReleaseProofFault.CORPUS_INVALID
    if set(corpus) != _CORPUS_KEYS or corpus.get("schema_version") != 1:
        fail(fault)
    try:
        candidate_id, exports = validate_candidate(candidate_lock)
    except Exception:
        fail(FreeLLMAPIReleaseProofFault.CANDIDATE_INVALID)
    if corpus.get("candidate_id") != candidate_id:
        fail(fault)
    selected_export = corpus.get("selected_export")
    if selected_export != "expectedReliability" or selected_export not in exports:
        fail(fault)
    admitted = as_mapping(candidate_lock.get("admitted_artifact"), fault)
    artifact_digest = sha256_digest(
        corpus.get("artifact_bundle_sha256"), fault
    )
    artifact_commit = corpus.get("artifact_upstream_commit")
    if (
        artifact_digest != admitted.get("bundle_sha256")
        or artifact_commit != admitted.get("upstream_commit")
        or not isinstance(artifact_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", artifact_commit) is None
    ):
        fail(fault)
    raw_fixtures = corpus.get("fixtures")
    if not isinstance(raw_fixtures, list) or not 2 <= len(raw_fixtures) <= 256:
        fail(fault)
    parsed = tuple(_fixture(raw) for raw in raw_fixtures)
    inputs = tuple(item[0] for item in parsed)
    fallbacks = tuple(item[1] for item in parsed)
    outcomes = tuple(item[2] for item in parsed)
    digests = tuple(item[3] for item in parsed)
    if len(set(digests)) != len(digests):
        fail(fault)
    return inputs, fallbacks, outcomes, digests, artifact_digest, artifact_commit


def _quality(prediction: float, outcome: bool) -> float:
    target = 1.0 if outcome else 0.0
    return 1.0 - (prediction - target) ** 2


def _observations(
    *,
    fixtures: tuple[str, ...],
    fallbacks: tuple[FreeLLMScoringFactors, ...],
    factors: tuple[FreeLLMScoringFactors, ...],
    outcomes: tuple[bool, ...],
    shadow_failed: bool,
) -> list[dict[str, object]]:
    return [
        {
            "fixture_digest": fixture,
            "baseline_quality": _quality(fallback.expected_reliability, outcome),
            "shadow_quality": (
                0.0
                if shadow_failed
                else _quality(factor.expected_reliability, outcome)
            ),
            "baseline_latency_ms": 0.0,
            "shadow_latency_ms": 0.0,
            "baseline_memory_mib": 0.0,
            "shadow_memory_mib": 0.0,
            "baseline_cost_usd": 0.0,
            "shadow_cost_usd": 0.0,
            "baseline_failed": False,
            "shadow_failed": shadow_failed,
        }
        for fixture, fallback, factor, outcome in zip(
            fixtures, fallbacks, factors, outcomes, strict=True
        )
    ]


def run_frozen_corpus_proof(
    *,
    candidate_lock: Mapping[str, object],
    corpus: Mapping[str, object],
    plan: Mapping[str, object],
    abi_manifest: Mapping[str, object],
) -> dict[str, object]:
    """Execute the pinned artifact and its removal path on held-out outcomes."""
    inputs, fallbacks, outcomes, fixture_digests, artifact_digest, artifact_commit = (
        _corpus(candidate_lock, corpus)
    )
    expected_corpus_digest = canonical_digest(
        {"fixture_digests": list(fixture_digests)},
        FreeLLMAPIReleaseProofFault.CORPUS_INVALID,
    )
    if (
        plan.get("fixture_digests") != list(fixture_digests)
        or plan.get("corpus_sha256") != expected_corpus_digest
        or plan.get("selected_export") != corpus.get("selected_export")
    ):
        fail(FreeLLMAPIReleaseProofFault.CORPUS_INVALID)

    enabled = FreeLLMScoringKernel(enabled=True).factor_batch(
        inputs,
        fallback=fallbacks,
    )
    disabled = FreeLLMScoringKernel(enabled=False).factor_batch(
        inputs,
        fallback=fallbacks,
    )
    accepted = evaluate_frozen_delta(
        candidate_lock=candidate_lock,
        plan=plan,
        abi_manifest=abi_manifest,
        observations=_observations(
            fixtures=fixture_digests,
            fallbacks=fallbacks,
            factors=enabled.factors,
            outcomes=outcomes,
            shadow_failed=enabled.source is not FreeLLMScoringSource.FREELLMAPI_SHADOW,
        ),
    )
    rejected = evaluate_frozen_delta(
        candidate_lock=candidate_lock,
        plan=plan,
        abi_manifest=abi_manifest,
        observations=_observations(
            fixtures=fixture_digests,
            fallbacks=fallbacks,
            factors=disabled.factors,
            outcomes=outcomes,
            shadow_failed=False,
        ),
    )
    if (
        accepted.get("decision") != "accepted_for_build_review"
        or rejected.get("decision") != "rejected_nonpositive_delta"
        or accepted.get("runtime_admitted") is not False
        or rejected.get("runtime_admitted") is not False
    ):
        fail(FreeLLMAPIReleaseProofFault.CORPUS_INVALID)
    record: dict[str, object] = {
        "schema_version": FREELLMAPI_RELEASE_PROOF_SCHEMA_VERSION,
        "gate": FREELLMAPI_FROZEN_CORPUS_GATE,
        "candidate_id": candidate_lock["candidate_id"],
        "corpus_sha256": expected_corpus_digest,
        "artifact_bundle_sha256": artifact_digest,
        "artifact_upstream_commit": artifact_commit,
        "fixture_count": len(fixture_digests),
        "accepted": accepted,
        "rejected": rejected,
        "decision": "frozen_corpus_verified",
        "runtime_admitted": False,
    }
    record["evidence_id"] = stable_evidence_id(record)
    return record


__all__ = ["run_frozen_corpus_proof"]
