"""Hermetic workflow boundary for FreeLLMAPI self-improvement candidates."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from general_ludd.models.freellmapi_workflow import (
    FREELLMAPI_WORKFLOW_SCHEMA_VERSION,
    evaluate_freellmapi_candidate,
)

_FIXTURES = tuple(hashlib.sha256(f"workflow-fixture-{index}".encode()).hexdigest() for index in range(4))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _candidate() -> dict[str, object]:
    candidate: dict[str, object] = {
        "schema_version": 1,
        "decision": {
            "runtime_admitted": False,
            "state": "pending_frozen_delta",
            "required_gate": "freellmapi-frozen-delta-v1",
        },
        "upstream": {
            "repository": "tashfeenahmed/freellmapi",
            "tag": "v0.11.1",
            "commit": "4" * 40,
        },
        "sources": {
            "scoring": {
                "symbols_present": [
                    "reliabilityPosterior",
                    "expectedReliability",
                    "speedScore",
                ],
                "symbols_missing": [],
            }
        },
        "admitted_artifact": {
            "abi_version": 1,
            "bundle_sha256": hashlib.sha256(b"workflow-bundle").hexdigest(),
        },
    }
    candidate["candidate_id"] = f"sha256:{_canonical_digest(candidate)}"
    return candidate


def _plan() -> dict[str, object]:
    locked = _candidate()
    fixture_payload = {"fixture_digests": list(_FIXTURES)}
    return {
        "schema_version": 1,
        "gate": "freellmapi-frozen-delta-v1",
        "candidate_id": locked["candidate_id"],
        "selected_export": "expectedReliability",
        "capability_id": "score_candidate_features",
        "abi_version": 1,
        "fixture_digests": list(_FIXTURES),
        "corpus_sha256": _canonical_digest(fixture_payload),
        "metric": "paired-adjusted-quality-v1",
        "minimum_adjusted_gain": 0.02,
        "confidence_z": 1.96,
        "penalties": {
            "latency_ms": 0.0001,
            "memory_mib": 0.0002,
            "cost_usd": 1.0,
            "failure": 0.5,
        },
        "owner": "general_ludd.models",
        "observation_window": "four-fixed-provider-routing-fixtures",
        "removal_condition": "remove when the lower confidence bound falls below 0.02",
    }


def _abi() -> dict[str, object]:
    return {
        "abi_version": 1,
        "operation": "score_candidate_features",
        "upstream_export": "expectedReliability",
        "input_schema_sha256": hashlib.sha256(b"input-schema").hexdigest(),
        "output_schema_sha256": hashlib.sha256(b"output-schema").hexdigest(),
        "host_capabilities": [],
    }


def _observations(*, quality_gain: float = 0.08) -> list[dict[str, object]]:
    return [
        {
            "fixture_digest": fixture,
            "baseline_quality": 0.70,
            "shadow_quality": 0.70 + quality_gain,
            "baseline_latency_ms": 10.0,
            "shadow_latency_ms": 11.0,
            "baseline_memory_mib": 100.0,
            "shadow_memory_mib": 101.0,
            "baseline_cost_usd": 0.0,
            "shadow_cost_usd": 0.0,
            "baseline_failed": False,
            "shadow_failed": False,
        }
        for fixture in _FIXTURES
    ]


def test_workflow_api_exists_and_declares_stable_public_surface() -> None:
    import general_ludd.models.freellmapi_workflow as workflow

    assert workflow.__all__ == [
        "FREELLMAPI_WORKFLOW_SCHEMA_VERSION",
        "evaluate_freellmapi_candidate",
    ]


def test_positive_fake_delta_is_accepted_for_build_review() -> None:
    result = evaluate_freellmapi_candidate(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(),
    )

    assert result["schema_version"] == FREELLMAPI_WORKFLOW_SCHEMA_VERSION
    assert result["decision"] == "accepted_for_build_review"
    assert result["runtime_admitted"] is False
    assert result["abi_compatible"] is True
    assert cast(str, result["evidence_id"]).startswith("sha256:")
    assert result["candidate_id"] == _candidate()["candidate_id"]


def test_nonpositive_fake_delta_is_rejected() -> None:
    result = evaluate_freellmapi_candidate(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(quality_gain=0.0),
    )

    assert result["decision"] == "rejected_nonpositive_delta"
    assert result["runtime_admitted"] is False


def test_incompatible_abi_fake_delta_is_rejected() -> None:
    result = evaluate_freellmapi_candidate(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest={**_abi(), "abi_version": 2},
        observations=_observations(),
    )

    assert result["decision"] == "rejected_abi_incompatible"
    assert result["runtime_admitted"] is False
    assert result["metrics"] is None


def test_workflow_result_is_content_free_and_deterministic() -> None:
    first = evaluate_freellmapi_candidate(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=list(reversed(_observations())),
    )
    second = evaluate_freellmapi_candidate(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(),
    )

    assert first == second
    payload = json.dumps(first, sort_keys=True)
    assert "pending_frozen_delta" not in payload
    assert "4" * 40 not in payload
