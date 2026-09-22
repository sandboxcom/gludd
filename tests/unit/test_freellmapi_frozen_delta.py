"""Contracts for the universal FreeLLMAPI frozen-delta gate."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import cast

import pytest

import general_ludd.models.freellmapi_frozen_delta as frozen_delta
from general_ludd.models.freellmapi_frozen_delta import (
    FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION,
    FreeLLMAPIFrozenDeltaError,
    FreeLLMAPIFrozenDeltaFault,
    evaluate_frozen_delta,
)

_FIXTURES = tuple(hashlib.sha256(f"fixture-{index}".encode()).hexdigest() for index in range(4))
_INPUT_SCHEMA = hashlib.sha256(b"input-schema").hexdigest()
_OUTPUT_SCHEMA = hashlib.sha256(b"output-schema").hexdigest()
_SECRET = "private prompt and model output must never appear"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )
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
            "bundle_sha256": hashlib.sha256(b"current-bundle").hexdigest(),
        },
    }
    candidate["candidate_id"] = f"sha256:{_canonical_digest(candidate)}"
    return candidate


def _reidentify_candidate(candidate: dict[str, object]) -> None:
    candidate.pop("candidate_id", None)
    candidate["candidate_id"] = f"sha256:{_canonical_digest(candidate)}"


def _plan(candidate: dict[str, object] | None = None) -> dict[str, object]:
    locked = candidate or _candidate()
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


def _abi(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "abi_version": 1,
        "operation": "score_candidate_features",
        "upstream_export": "expectedReliability",
        "input_schema_sha256": _INPUT_SCHEMA,
        "output_schema_sha256": _OUTPUT_SCHEMA,
        "host_capabilities": [],
    }
    value.update(overrides)
    return value


def _observations(
    *,
    quality_gain: float = 0.08,
    shadow_latency_ms: float = 11.0,
    shadow_memory_mib: float = 101.0,
    shadow_cost_usd: float = 0.0,
    shadow_failed: bool = False,
) -> list[dict[str, object]]:
    return [
        {
            "fixture_digest": fixture,
            "baseline_quality": 0.70,
            "shadow_quality": 0.70 + quality_gain,
            "baseline_latency_ms": 10.0,
            "shadow_latency_ms": shadow_latency_ms,
            "baseline_memory_mib": 100.0,
            "shadow_memory_mib": shadow_memory_mib,
            "baseline_cost_usd": 0.0,
            "shadow_cost_usd": shadow_cost_usd,
            "baseline_failed": False,
            "shadow_failed": shadow_failed,
            "forbidden_payload": _SECRET,
        }
        for fixture in reversed(_FIXTURES)
    ]


def test_positive_preregistered_delta_is_deterministic_but_never_runtime_admitting() -> None:
    first = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(),
    )
    second = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=list(reversed(_observations())),
    )

    assert first == second
    assert first["schema_version"] == FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION
    assert first["decision"] == "accepted_for_build_review"
    assert first["runtime_admitted"] is False
    assert first["abi_compatible"] is True
    assert first["fixture_count"] == len(_FIXTURES)
    assert first["metrics"] == {
        "mean_quality_gain": pytest.approx(0.08),
        "mean_adjusted_gain": pytest.approx(0.0797),
        "lower_confidence_bound": pytest.approx(0.0797),
        "mean_latency_penalty": pytest.approx(0.0001),
        "mean_memory_penalty": pytest.approx(0.0002),
        "mean_cost_penalty": 0.0,
        "mean_failure_penalty": 0.0,
    }
    assert cast(str, first["evidence_id"]).startswith("sha256:")
    assert _SECRET not in json.dumps(first, sort_keys=True)


def test_delta_gate_declares_a_small_universal_public_api() -> None:
    assert frozen_delta.__all__ == [
        "FREELLMAPI_FROZEN_DELTA_GATE",
        "FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION",
        "FreeLLMAPIFrozenDeltaError",
        "FreeLLMAPIFrozenDeltaFault",
        "evaluate_frozen_delta",
    ]
    assert "self_improve" not in frozen_delta.__file__
    source = Path(frozen_delta.__file__).read_text(encoding="utf-8")
    assert "general_ludd.self_improve" not in source


def test_nonpositive_and_penalty_dominated_results_are_rejected() -> None:
    nonpositive = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(quality_gain=0.0),
    )
    failed = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=_observations(quality_gain=0.08, shadow_failed=True),
    )

    assert nonpositive["decision"] == "rejected_nonpositive_delta"
    assert failed["decision"] == "rejected_nonpositive_delta"
    assert nonpositive["runtime_admitted"] is False
    failed_metrics = cast(dict[str, float], failed["metrics"])
    assert failed_metrics["mean_failure_penalty"] == 0.5


def test_statistically_inconclusive_gain_is_rejected() -> None:
    observations = _observations()
    gains = (0.01, 0.02, 0.09, 0.10)
    for observation, gain in zip(observations, gains, strict=True):
        observation["shadow_quality"] = 0.70 + gain

    result = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(),
        observations=observations,
    )

    metrics = cast(dict[str, float], result["metrics"])
    assert metrics["mean_adjusted_gain"] > 0.02
    assert metrics["lower_confidence_bound"] < 0.02
    assert result["decision"] == "rejected_inconclusive_delta"


@pytest.mark.parametrize(
    "abi_patch",
    [
        {"abi_version": 2},
        {"operation": "eval"},
        {"upstream_export": "speedScore"},
        {"host_capabilities": ["network"]},
    ],
)
def test_incompatible_abi_is_a_content_free_rejection(abi_patch: dict[str, object]) -> None:
    result = evaluate_frozen_delta(
        candidate_lock=_candidate(),
        plan=_plan(),
        abi_manifest=_abi(**abi_patch),
        observations=_observations(),
    )

    assert result["decision"] == "rejected_abi_incompatible"
    assert result["abi_compatible"] is False
    assert result["runtime_admitted"] is False
    assert result["metrics"] is None


def test_candidate_identity_and_pending_state_are_bound_before_evaluation() -> None:
    candidate = _candidate()
    candidate["upstream"]["commit"] = "5" * 40  # type: ignore[index]
    with pytest.raises(FreeLLMAPIFrozenDeltaError) as tampered:
        evaluate_frozen_delta(
            candidate_lock=candidate,
            plan=_plan(),
            abi_manifest=_abi(),
            observations=_observations(),
        )
    assert tampered.value.fault is FreeLLMAPIFrozenDeltaFault.CANDIDATE_INVALID
    assert str(tampered.value) == "candidate_invalid"

    candidate = _candidate()
    candidate["decision"]["runtime_admitted"] = True  # type: ignore[index]
    _reidentify_candidate(candidate)
    with pytest.raises(FreeLLMAPIFrozenDeltaError) as admitted:
        evaluate_frozen_delta(
            candidate_lock=candidate,
            plan=_plan(candidate),
            abi_manifest=_abi(),
            observations=_observations(),
        )
    assert admitted.value.fault is FreeLLMAPIFrozenDeltaFault.CANDIDATE_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        lambda candidate: candidate.__setitem__("candidate_id", "not-a-digest"),
        lambda candidate: candidate.__setitem__("decision", []),
        lambda candidate: candidate.__setitem__("upstream", []),
        lambda candidate: candidate["upstream"].__setitem__("repository", "attacker/repo"),
        lambda candidate: candidate["upstream"].__setitem__("tag", ""),
        lambda candidate: candidate["upstream"].__setitem__("commit", "short"),
        lambda candidate: candidate["sources"].__setitem__("scoring", []),
        lambda candidate: candidate["sources"]["scoring"].__setitem__(
            "symbols_present", [1]
        ),
        lambda candidate: candidate["sources"]["scoring"].__setitem__(
            "symbols_missing", ["expectedReliability"]
        ),
        lambda candidate: candidate.__setitem__("admitted_artifact", []),
        lambda candidate: candidate["admitted_artifact"].__setitem__("abi_version", 2),
        lambda candidate: candidate["admitted_artifact"].__setitem__(
            "bundle_sha256", "bad"
        ),
    ],
)
def test_malformed_candidate_components_fail_closed(mutation: object) -> None:
    candidate = _candidate()
    mutation(candidate)  # type: ignore[operator]
    if candidate.get("candidate_id") != "not-a-digest":
        _reidentify_candidate(candidate)

    with pytest.raises(FreeLLMAPIFrozenDeltaError) as caught:
        evaluate_frozen_delta(
            candidate_lock=candidate,
            plan=_plan(candidate),
            abi_manifest=_abi(),
            observations=_observations(),
        )

    assert caught.value.fault is FreeLLMAPIFrozenDeltaFault.CANDIDATE_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan.__setitem__("schema_version", 2),
        lambda plan: plan.__setitem__("gate", "other-gate"),
        lambda plan: plan.__setitem__("corpus_sha256", "0" * 64),
        lambda plan: plan.__setitem__(
            "fixture_digests", [*_FIXTURES[:-1], _FIXTURES[0]]
        ),
        lambda plan: plan.__setitem__("minimum_adjusted_gain", 0.0),
        lambda plan: plan.__setitem__("confidence_z", math.inf),
        lambda plan: plan["penalties"].__setitem__("latency_ms", -1.0),
        lambda plan: plan["penalties"].pop("failure"),
        lambda plan: plan.__setitem__("capability_id", "generic_eval"),
        lambda plan: plan.__setitem__("abi_version", 2),
        lambda plan: plan.__setitem__("owner", ""),
        lambda plan: plan.__setitem__("observation_window", ""),
        lambda plan: plan.__setitem__("removal_condition", None),
        lambda plan: plan.__setitem__("non_json_value", object()),
    ],
)
def test_malformed_or_unbound_plan_fails_closed(mutation: object) -> None:
    plan = _plan()
    mutation(plan)  # type: ignore[operator]

    with pytest.raises(FreeLLMAPIFrozenDeltaError) as caught:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=plan,
            abi_manifest=_abi(),
            observations=_observations(),
        )

    assert caught.value.fault is FreeLLMAPIFrozenDeltaFault.PLAN_INVALID


@pytest.mark.parametrize(
    "mutator",
    [
        lambda rows: rows.pop(),
        lambda rows: rows.append(copy.deepcopy(rows[0])),
        lambda rows: rows[0].__setitem__("fixture_digest", "f" * 64),
        lambda rows: rows[0].__setitem__("baseline_quality", -0.1),
        lambda rows: rows[0].__setitem__("baseline_quality", True),
        lambda rows: rows[0].__setitem__("baseline_quality", 1.1),
        lambda rows: rows[0].__setitem__("shadow_quality", math.nan),
        lambda rows: rows[0].__setitem__("shadow_latency_ms", -1.0),
        lambda rows: rows[0].__setitem__("shadow_failed", 1),
    ],
)
def test_incomplete_duplicate_or_invalid_observations_fail_closed(mutator: object) -> None:
    observations = _observations()
    mutator(observations)  # type: ignore[operator]

    with pytest.raises(FreeLLMAPIFrozenDeltaError) as caught:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=_plan(),
            abi_manifest=_abi(),
            observations=observations,
        )

    assert caught.value.fault is FreeLLMAPIFrozenDeltaFault.OBSERVATIONS_INVALID


@pytest.mark.parametrize(
    "patch",
    [
        {"unexpected": "field"},
        {"input_schema_sha256": "bad"},
        {"output_schema_sha256": None},
        {"host_capabilities": "none"},
        {"host_capabilities": [1]},
        {"abi_version": True},
        {"operation": None},
    ],
)
def test_malformed_abi_manifest_fails_closed(patch: dict[str, object]) -> None:
    with pytest.raises(FreeLLMAPIFrozenDeltaError) as caught:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=_plan(),
            abi_manifest=_abi(**patch),
            observations=_observations(),
        )

    assert caught.value.fault is FreeLLMAPIFrozenDeltaFault.ABI_INVALID


def test_non_mapping_observation_and_short_fixture_plan_fail_closed() -> None:
    invalid_rows = cast(list[dict[str, object]], [*_observations()[:-1], []])
    with pytest.raises(FreeLLMAPIFrozenDeltaError) as observation_error:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=_plan(),
            abi_manifest=_abi(),
            observations=invalid_rows,
        )
    assert (
        observation_error.value.fault
        is FreeLLMAPIFrozenDeltaFault.OBSERVATIONS_INVALID
    )

    plan = _plan()
    plan["fixture_digests"] = [_FIXTURES[0]]
    with pytest.raises(FreeLLMAPIFrozenDeltaError) as plan_error:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=plan,
            abi_manifest=_abi(),
            observations=_observations(),
        )
    assert plan_error.value.fault is FreeLLMAPIFrozenDeltaFault.PLAN_INVALID


def test_plan_must_select_a_symbol_present_in_the_bound_candidate() -> None:
    plan = _plan()
    plan["selected_export"] = "rateLimitFactor"

    with pytest.raises(FreeLLMAPIFrozenDeltaError) as caught:
        evaluate_frozen_delta(
            candidate_lock=_candidate(),
            plan=plan,
            abi_manifest=_abi(upstream_export="rateLimitFactor"),
            observations=_observations(),
        )

    assert caught.value.fault is FreeLLMAPIFrozenDeltaFault.PLAN_INVALID
