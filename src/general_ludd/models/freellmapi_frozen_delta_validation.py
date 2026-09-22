"""Validate immutable candidates, preregistered plans, and bridge ABI."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

from general_ludd.models.freellmapi_frozen_delta_contracts import (
    FREELLMAPI_FROZEN_DELTA_GATE,
    FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION,
    FreeLLMAPIFrozenDeltaFault,
    PenaltyWeights,
    ValidatedPlan,
    candidate_id,
    canonical_digest,
    digest,
    fail,
    mapping,
    nonempty,
    number,
)

_PLAN_CAPABILITIES = frozenset(
    {
        "score_candidate_features",
        "normalize_provider_observation",
        "fuse_candidate_outputs",
    }
)
_PENALTY_KEYS = frozenset({"latency_ms", "memory_mib", "cost_usd", "failure"})
_ABI_KEYS = frozenset(
    {
        "abi_version",
        "operation",
        "upstream_export",
        "input_schema_sha256",
        "output_schema_sha256",
        "host_capabilities",
    }
)


def _source_symbols(
    candidate: Mapping[str, object], fault: FreeLLMAPIFrozenDeltaFault
) -> frozenset[str]:
    sources = mapping(candidate.get("sources"), fault)
    scoring = mapping(sources.get("scoring"), fault)
    present = scoring.get("symbols_present")
    missing = scoring.get("symbols_missing")
    if not isinstance(present, list) or not isinstance(missing, list):
        fail(fault)
    combined = present + missing
    if any(not isinstance(symbol, str) or not symbol for symbol in combined):
        fail(fault)
    symbols = cast("list[str]", combined)
    if len(set(symbols)) != len(symbols):
        fail(fault)
    return frozenset(cast("list[str]", present))


def validate_candidate(
    candidate: Mapping[str, object],
) -> tuple[str, frozenset[str]]:
    """Bind evaluation to one self-consistent, still-non-runnable candidate."""
    fault = FreeLLMAPIFrozenDeltaFault.CANDIDATE_INVALID
    stored_id = candidate_id(candidate.get("candidate_id"), fault)
    unsigned = dict(candidate)
    unsigned.pop("candidate_id", None)
    if stored_id != f"sha256:{canonical_digest(unsigned, fault)}":
        fail(fault)

    decision = mapping(candidate.get("decision"), fault)
    if dict(decision) != {
        "runtime_admitted": False,
        "state": "pending_frozen_delta",
        "required_gate": FREELLMAPI_FROZEN_DELTA_GATE,
    }:
        fail(fault)
    upstream = mapping(candidate.get("upstream"), fault)
    if upstream.get("repository") != "tashfeenahmed/freellmapi":
        fail(fault)
    nonempty(upstream.get("tag"), fault)
    commit = nonempty(upstream.get("commit"), fault)
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        fail(fault)

    present = _source_symbols(candidate, fault)
    admitted = mapping(candidate.get("admitted_artifact"), fault)
    if admitted.get("abi_version") != 1:
        fail(fault)
    digest(admitted.get("bundle_sha256"), fault)
    return stored_id, present


def _fixture_digests(
    value: object, fault: FreeLLMAPIFrozenDeltaFault
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) < 2:
        fail(fault)
    fixtures = tuple(digest(item, fault) for item in value)
    if len(set(fixtures)) != len(fixtures):
        fail(fault)
    return fixtures


def _penalties(
    value: object, fault: FreeLLMAPIFrozenDeltaFault
) -> PenaltyWeights:
    penalties = mapping(value, fault)
    if set(penalties) != _PENALTY_KEYS:
        fail(fault)
    return PenaltyWeights(
        latency_ms=number(penalties["latency_ms"], fault, maximum=1_000_000.0),
        memory_mib=number(penalties["memory_mib"], fault, maximum=1_000_000.0),
        cost_usd=number(penalties["cost_usd"], fault, maximum=1_000_000.0),
        failure=number(penalties["failure"], fault, maximum=1_000_000.0),
    )


def validate_plan(
    plan: Mapping[str, object],
    *,
    candidate_identity: str,
    candidate_exports: frozenset[str],
) -> ValidatedPlan:
    """Validate a frozen experiment plan before reading observations."""
    fault = FreeLLMAPIFrozenDeltaFault.PLAN_INVALID
    if (
        plan.get("schema_version") != FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION
        or plan.get("gate") != FREELLMAPI_FROZEN_DELTA_GATE
        or candidate_id(plan.get("candidate_id"), fault) != candidate_identity
        or plan.get("metric") != "paired-adjusted-quality-v1"
    ):
        fail(fault)
    selected_export = nonempty(plan.get("selected_export"), fault)
    capability_id = nonempty(plan.get("capability_id"), fault)
    if selected_export not in candidate_exports or capability_id not in _PLAN_CAPABILITIES:
        fail(fault)
    if plan.get("abi_version") != 1:
        fail(fault)

    fixtures = _fixture_digests(plan.get("fixture_digests"), fault)
    corpus_sha256 = digest(plan.get("corpus_sha256"), fault)
    if corpus_sha256 != canonical_digest({"fixture_digests": list(fixtures)}, fault):
        fail(fault)
    minimum_gain = number(
        plan.get("minimum_adjusted_gain"),
        fault,
        maximum=1.0,
        exclusive_minimum=True,
    )
    confidence_z = number(
        plan.get("confidence_z"),
        fault,
        maximum=10.0,
        exclusive_minimum=True,
    )
    penalties = _penalties(plan.get("penalties"), fault)
    nonempty(plan.get("owner"), fault)
    nonempty(plan.get("observation_window"), fault)
    nonempty(plan.get("removal_condition"), fault)
    return ValidatedPlan(
        plan_id=f"sha256:{canonical_digest(plan, fault)}",
        fixtures=fixtures,
        corpus_sha256=corpus_sha256,
        selected_export=selected_export,
        capability_id=capability_id,
        abi_version=1,
        minimum_adjusted_gain=minimum_gain,
        confidence_z=confidence_z,
        penalties=penalties,
    )


def abi_compatible(
    abi_manifest: Mapping[str, object], plan: ValidatedPlan
) -> bool:
    """Return compatibility while treating malformed ABI records as faults."""
    fault = FreeLLMAPIFrozenDeltaFault.ABI_INVALID
    if set(abi_manifest) != _ABI_KEYS:
        fail(fault)
    digest(abi_manifest.get("input_schema_sha256"), fault)
    digest(abi_manifest.get("output_schema_sha256"), fault)
    host_capabilities = abi_manifest.get("host_capabilities")
    if not isinstance(host_capabilities, list) or any(
        not isinstance(item, str) for item in host_capabilities
    ):
        fail(fault)
    version = abi_manifest.get("abi_version")
    operation = abi_manifest.get("operation")
    upstream_export = abi_manifest.get("upstream_export")
    if isinstance(version, bool) or not isinstance(version, int):
        fail(fault)
    if not isinstance(operation, str) or not isinstance(upstream_export, str):
        fail(fault)
    return (
        version == plan["abi_version"]
        and operation == plan["capability_id"]
        and upstream_export == plan["selected_export"]
        and not host_capabilities
    )
