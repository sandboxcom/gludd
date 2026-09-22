"""Evaluate FreeLLMAPI exports against a preregistered frozen corpus.

The gate is deliberately universal model infrastructure. It produces only a
content-free review decision and can never admit executable code at runtime.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import TypedDict

from general_ludd.models.freellmapi_frozen_delta_contracts import (
    FREELLMAPI_FROZEN_DELTA_GATE,
    FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION,
    FreeLLMAPIFrozenDeltaError,
    FreeLLMAPIFrozenDeltaFault,
    ValidatedPlan,
    canonical_digest,
    digest,
    fail,
    mapping,
    number,
)
from general_ludd.models.freellmapi_frozen_delta_validation import (
    abi_compatible,
    validate_candidate,
    validate_plan,
)


class _Observation(TypedDict):
    """Content-free measurements retained from one paired fixture run."""

    fixture_digest: str
    baseline_quality: float
    shadow_quality: float
    baseline_latency_ms: float
    shadow_latency_ms: float
    baseline_memory_mib: float
    shadow_memory_mib: float
    baseline_cost_usd: float
    shadow_cost_usd: float
    baseline_failed: bool
    shadow_failed: bool


def _observation_number(
    observation: Mapping[str, object], key: str, *, maximum: float | None = None
) -> float:
    return number(
        observation.get(key),
        FreeLLMAPIFrozenDeltaFault.OBSERVATIONS_INVALID,
        maximum=maximum,
    )


def _validated_observation(raw: object) -> _Observation:
    fault = FreeLLMAPIFrozenDeltaFault.OBSERVATIONS_INVALID
    observation = mapping(raw, fault)
    baseline_failed = observation.get("baseline_failed")
    shadow_failed = observation.get("shadow_failed")
    if not isinstance(baseline_failed, bool) or not isinstance(shadow_failed, bool):
        fail(fault)
    return _Observation(
        fixture_digest=digest(observation.get("fixture_digest"), fault),
        baseline_quality=_observation_number(
            observation, "baseline_quality", maximum=1.0
        ),
        shadow_quality=_observation_number(
            observation, "shadow_quality", maximum=1.0
        ),
        baseline_latency_ms=_observation_number(
            observation, "baseline_latency_ms"
        ),
        shadow_latency_ms=_observation_number(observation, "shadow_latency_ms"),
        baseline_memory_mib=_observation_number(
            observation, "baseline_memory_mib"
        ),
        shadow_memory_mib=_observation_number(observation, "shadow_memory_mib"),
        baseline_cost_usd=_observation_number(observation, "baseline_cost_usd"),
        shadow_cost_usd=_observation_number(observation, "shadow_cost_usd"),
        baseline_failed=baseline_failed,
        shadow_failed=shadow_failed,
    )


def _validated_observations(
    observations: Sequence[Mapping[str, object]], fixtures: tuple[str, ...]
) -> tuple[_Observation, ...]:
    fault = FreeLLMAPIFrozenDeltaFault.OBSERVATIONS_INVALID
    if isinstance(observations, (str, bytes)) or len(observations) != len(fixtures):
        fail(fault)
    indexed: dict[str, _Observation] = {}
    for raw in observations:
        observation = _validated_observation(raw)
        fixture = observation["fixture_digest"]
        if fixture in indexed:
            fail(fault)
        indexed[fixture] = observation
    if set(indexed) != set(fixtures):
        fail(fault)
    return tuple(indexed[fixture] for fixture in sorted(fixtures))


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values)


def _rounded(value: float) -> float:
    return round(value, 12)


def _metrics(
    observations: tuple[_Observation, ...], plan: ValidatedPlan
) -> dict[str, float]:
    penalties = plan["penalties"]
    quality_gains: list[float] = []
    adjusted_gains: list[float] = []
    latency_penalties: list[float] = []
    memory_penalties: list[float] = []
    cost_penalties: list[float] = []
    failure_penalties: list[float] = []
    for observation in observations:
        quality_gain = observation["shadow_quality"] - observation["baseline_quality"]
        latency_penalty = max(
            0.0,
            observation["shadow_latency_ms"] - observation["baseline_latency_ms"],
        ) * penalties["latency_ms"]
        memory_penalty = max(
            0.0,
            observation["shadow_memory_mib"] - observation["baseline_memory_mib"],
        ) * penalties["memory_mib"]
        cost_penalty = max(
            0.0,
            observation["shadow_cost_usd"] - observation["baseline_cost_usd"],
        ) * penalties["cost_usd"]
        failure_delta = int(observation["shadow_failed"]) - int(
            observation["baseline_failed"]
        )
        failure_penalty = max(0, failure_delta) * penalties["failure"]
        quality_gains.append(quality_gain)
        latency_penalties.append(latency_penalty)
        memory_penalties.append(memory_penalty)
        cost_penalties.append(cost_penalty)
        failure_penalties.append(failure_penalty)
        adjusted_gains.append(
            quality_gain
            - latency_penalty
            - memory_penalty
            - cost_penalty
            - failure_penalty
        )

    mean_adjusted = _mean(adjusted_gains)
    standard_error = statistics.stdev(adjusted_gains) / math.sqrt(len(adjusted_gains))
    lower_bound = mean_adjusted - plan["confidence_z"] * standard_error
    return {
        "mean_quality_gain": _rounded(_mean(quality_gains)),
        "mean_adjusted_gain": _rounded(mean_adjusted),
        "lower_confidence_bound": _rounded(lower_bound),
        "mean_latency_penalty": _rounded(_mean(latency_penalties)),
        "mean_memory_penalty": _rounded(_mean(memory_penalties)),
        "mean_cost_penalty": _rounded(_mean(cost_penalties)),
        "mean_failure_penalty": _rounded(_mean(failure_penalties)),
    }


def _decision(metrics: Mapping[str, float], minimum_gain: float) -> str:
    if metrics["mean_quality_gain"] <= 0.0 or metrics["mean_adjusted_gain"] <= 0.0:
        return "rejected_nonpositive_delta"
    if metrics["lower_confidence_bound"] < minimum_gain:
        return "rejected_inconclusive_delta"
    return "accepted_for_build_review"


def _record(
    *,
    candidate_identity: str,
    plan: ValidatedPlan,
    compatible: bool,
    decision: str,
    metrics: Mapping[str, float] | None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION,
        "gate": FREELLMAPI_FROZEN_DELTA_GATE,
        "candidate_id": candidate_identity,
        "plan_id": plan["plan_id"],
        "corpus_sha256": plan["corpus_sha256"],
        "selected_export": plan["selected_export"],
        "capability_id": plan["capability_id"],
        "abi_compatible": compatible,
        "fixture_count": len(plan["fixtures"]),
        "decision": decision,
        "runtime_admitted": False,
        "metrics": dict(metrics) if metrics is not None else None,
    }
    record["evidence_id"] = "sha256:" + canonical_digest(
        record, FreeLLMAPIFrozenDeltaFault.PLAN_INVALID
    )
    return record


def evaluate_frozen_delta(
    *,
    candidate_lock: Mapping[str, object],
    plan: Mapping[str, object],
    abi_manifest: Mapping[str, object],
    observations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Return deterministic review evidence without changing runtime admission."""
    candidate_identity, candidate_exports = validate_candidate(candidate_lock)
    validated_plan = validate_plan(
        plan,
        candidate_identity=candidate_identity,
        candidate_exports=candidate_exports,
    )
    if not abi_compatible(abi_manifest, validated_plan):
        return _record(
            candidate_identity=candidate_identity,
            plan=validated_plan,
            compatible=False,
            decision="rejected_abi_incompatible",
            metrics=None,
        )
    paired = _validated_observations(observations, validated_plan["fixtures"])
    metrics = _metrics(paired, validated_plan)
    return _record(
        candidate_identity=candidate_identity,
        plan=validated_plan,
        compatible=True,
        decision=_decision(metrics, validated_plan["minimum_adjusted_gain"]),
        metrics=metrics,
    )


__all__ = [
    "FREELLMAPI_FROZEN_DELTA_GATE",
    "FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION",
    "FreeLLMAPIFrozenDeltaError",
    "FreeLLMAPIFrozenDeltaFault",
    "evaluate_frozen_delta",
]
