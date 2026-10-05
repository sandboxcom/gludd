"""Frozen, content-free contracts for the exact FreeLLMAPI three-arm replay."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, cast

THREE_ARM_GATE = "freellmapi-exact-three-arm-v1"
THREE_ARM_SCHEMA_VERSION = 1
EXPECTED_GROUP_COUNT = 32
EXPECTED_EXCLUSION_COUNT = 8

_CANDIDATE_ID = (
    "sha256:69d63b09199c37f38c02c711559b15e0fd5dc5ecc0e64e2d94c597dc5e5d3998"
)
_CANDIDATE_BUNDLE = (
    "e9e5d87a0d1e9697e0681b52afb719b7fd1fcdd601bd7fcbaf430cc502114845"
)
_CANDIDATE_COMMIT = "4191d8e7abef39fcd93fab009123467036f39750"
_ADMITTED_BUNDLE = (
    "d3078364c02f482909681e21895c4e86dc11cc66c1da7ae2007ad35b096ddf7d"
)
_ARMS = (
    "gludd_native",
    "freellmapi_v0_9_9",
    "freellmapi_v0_11_1",
)
_DIMENSIONS = {
    "provider_family": ("openai_compatible", "anthropic", "google", "local"),
    "health_quota_state": ("healthy", "constrained"),
    "endpoint_state": ("cold", "warm"),
    "capability_class": ("text", "tools"),
}
_EXCLUSION_REASONS = (
    "missing_outcome",
    "missing_reliability_counts",
    "nonfinite_measurement",
    "identity_not_digest",
    "unsupported_capability",
    "duplicate_group",
    "post_freeze_arrival",
    "raw_content_present",
)
_THRESHOLDS = {
    "bootstrap_confidence": 0.95,
    "bootstrap_samples": 10_000,
    "bootstrap_seed": 1_630_111,
    "quality_lcb_min": 0.02,
    "mcnemar_p_max": 0.05,
    "max_stratum_loss": 0.02,
    "p95_added_latency_ms": 5.0,
    "max_call_latency_ms": 25.0,
    "rss_delta_mib": 32.0,
    "max_cost_usd": 0.0,
    "max_bridge_faults": 0,
    "node_tolerance": 1e-12,
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CORPUS_KEYS = frozenset(
    {"schema_version", "corpus_id", "groups", "exclusions"}
)
_GROUP_KEYS = frozenset(
    {
        "group_digest",
        "candidate_identity_digest",
        "provider_family",
        "health_quota_state",
        "endpoint_state",
        "capability_class",
        "successes",
        "failures",
        "community_successes",
        "community_failures",
        "truth",
    }
)
_EXCLUSION_KEYS = frozenset({"exclusion_digest", "record_digest", "reason"})
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "plan_id",
        "gate",
        "candidate_id",
        "candidate_bundle_sha256",
        "candidate_upstream_commit",
        "admitted_bundle_sha256",
        "active_bundle_sha256",
        "arms",
        "dimensions",
        "group_count",
        "exclusion_count",
        "group_digests",
        "exclusion_digests",
        "corpus_sha256",
        "thresholds",
        "decision",
        "runtime_admitted",
        "network_allowed",
        "cost_allowed",
        "node_crosscheck_required",
    }
)


class ThreeArmContractFault(StrEnum):
    """Stable failure categories for frozen replay documents."""

    PLAN_INVALID = "plan_invalid"
    CORPUS_INVALID = "corpus_invalid"


class ThreeArmContractError(ValueError):
    """A content-free frozen-contract validation error."""

    def __init__(self, fault: ThreeArmContractFault) -> None:
        """Create an error exposing only its stable fault category."""
        self.fault = fault
        super().__init__(fault.value)


@dataclass(frozen=True, slots=True)
class ThreeArmThresholds:
    """Preregistered acceptance thresholds."""

    bootstrap_confidence: float
    bootstrap_samples: int
    bootstrap_seed: int
    quality_lcb_min: float
    mcnemar_p_max: float
    max_stratum_loss: float
    p95_added_latency_ms: float
    max_call_latency_ms: float
    rss_delta_mib: float
    max_cost_usd: float
    max_bridge_faults: int
    node_tolerance: float


@dataclass(frozen=True, slots=True)
class ThreeArmGroup:
    """One content-free replay cell from the frozen Cartesian design."""

    group_digest: str
    candidate_identity_digest: str
    provider_family: str
    health_quota_state: str
    endpoint_state: str
    capability_class: str
    successes: float
    failures: float
    community_successes: float
    community_failures: float
    truth: bool


@dataclass(frozen=True, slots=True)
class ThreeArmExclusion:
    """One preregistered, content-free excluded-record class."""

    exclusion_digest: str
    record_digest: str
    reason: str


@dataclass(frozen=True, slots=True)
class ThreeArmCorpus:
    """Validated frozen replay corpus."""

    corpus_id: str
    groups: tuple[ThreeArmGroup, ...]
    exclusions: tuple[ThreeArmExclusion, ...]


@dataclass(frozen=True, slots=True)
class ThreeArmPlan:
    """Validated non-promoting replay plan."""

    plan_id: str
    gate: str
    candidate_id: str
    candidate_bundle_sha256: str
    admitted_bundle_sha256: str
    active_bundle_sha256: str
    arms: tuple[str, ...]
    corpus_sha256: str
    thresholds: ThreeArmThresholds
    decision: str
    runtime_admitted: bool
    node_crosscheck_required: bool


def _fail(fault: ThreeArmContractFault) -> NoReturn:
    raise ThreeArmContractError(fault)


def canonical_digest(value: object) -> str:
    """Return the stable SHA-256 of one strict canonical JSON value."""
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical_json_invalid") from exc
    return hashlib.sha256(payload).hexdigest()


def _record_digest(prefix: str, index: int) -> str:
    return hashlib.sha256(f"{prefix}:{index:02d}".encode("ascii")).hexdigest()


def _sealed_record(record: dict[str, object], field: str) -> dict[str, object]:
    sealed = dict(record)
    sealed[field] = canonical_digest(record)
    return sealed


def build_frozen_documents(
    *,
    candidate_id: str = _CANDIDATE_ID,
    candidate_bundle_sha256: str = _CANDIDATE_BUNDLE,
    admitted_bundle_sha256: str = _ADMITTED_BUNDLE,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the deterministic preregistered plan and 32+8 corpus documents."""
    if (
        candidate_id != _CANDIDATE_ID
        or candidate_bundle_sha256 != _CANDIDATE_BUNDLE
        or admitted_bundle_sha256 != _ADMITTED_BUNDLE
    ):
        _fail(ThreeArmContractFault.PLAN_INVALID)
    groups: list[dict[str, object]] = []
    values = tuple(_DIMENSIONS.values())
    for index, combination in enumerate(itertools.product(*values)):
        provider, health, endpoint, capability = combination
        provider_index = _DIMENSIONS["provider_family"].index(provider)
        health_index = _DIMENSIONS["health_quota_state"].index(health)
        endpoint_index = _DIMENSIONS["endpoint_state"].index(endpoint)
        capability_index = _DIMENSIONS["capability_class"].index(capability)
        truth = (provider_index + health_index + endpoint_index + capability_index) % 2 == 0
        record: dict[str, object] = {
            "candidate_identity_digest": _record_digest("candidate", index),
            "provider_family": provider,
            "health_quota_state": health,
            "endpoint_state": endpoint,
            "capability_class": capability,
            "successes": 1.0 if truth else 5.0,
            "failures": 5.0 if truth else 1.0,
            "community_successes": 20.0 if truth else 0.0,
            "community_failures": 0.0 if truth else 20.0,
            "truth": truth,
        }
        groups.append(_sealed_record(record, "group_digest"))
    exclusions = [
        _sealed_record(
            {
                "record_digest": _record_digest("excluded", index),
                "reason": reason,
            },
            "exclusion_digest",
        )
        for index, reason in enumerate(_EXCLUSION_REASONS)
    ]
    corpus_unsigned: dict[str, object] = {
        "schema_version": THREE_ARM_SCHEMA_VERSION,
        "groups": groups,
        "exclusions": exclusions,
    }
    corpus: dict[str, object] = {
        **corpus_unsigned,
        "corpus_id": f"sha256:{canonical_digest(corpus_unsigned)}",
    }
    plan_unsigned: dict[str, object] = {
        "schema_version": THREE_ARM_SCHEMA_VERSION,
        "gate": THREE_ARM_GATE,
        "candidate_id": candidate_id,
        "candidate_bundle_sha256": candidate_bundle_sha256,
        "candidate_upstream_commit": _CANDIDATE_COMMIT,
        "admitted_bundle_sha256": admitted_bundle_sha256,
        "active_bundle_sha256": admitted_bundle_sha256,
        "arms": list(_ARMS),
        "dimensions": {key: list(value) for key, value in _DIMENSIONS.items()},
        "group_count": EXPECTED_GROUP_COUNT,
        "exclusion_count": EXPECTED_EXCLUSION_COUNT,
        "group_digests": [group["group_digest"] for group in groups],
        "exclusion_digests": [
            exclusion["exclusion_digest"] for exclusion in exclusions
        ],
        "corpus_sha256": canonical_digest(corpus),
        "thresholds": dict(_THRESHOLDS),
        "decision": "hold_only",
        "runtime_admitted": False,
        "network_allowed": False,
        "cost_allowed": False,
        "node_crosscheck_required": True,
    }
    plan = {
        **plan_unsigned,
        "plan_id": f"sha256:{canonical_digest(plan_unsigned)}",
    }
    return plan, corpus


def _mapping(value: object, fault: ThreeArmContractFault) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(fault)
    return cast(Mapping[str, object], value)


def _digest(value: object, fault: ThreeArmContractFault) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(fault)
    return value


def _number(value: object, fault: ThreeArmContractFault) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        _fail(fault)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        _fail(fault)
    return number


def _validate_group(value: object) -> ThreeArmGroup:
    fault = ThreeArmContractFault.CORPUS_INVALID
    record = _mapping(value, fault)
    if set(record) != _GROUP_KEYS:
        _fail(fault)
    unsigned = dict(record)
    stored_digest = _digest(unsigned.pop("group_digest", None), fault)
    if stored_digest != canonical_digest(unsigned):
        _fail(fault)
    truth = record.get("truth")
    if not isinstance(truth, bool):
        _fail(fault)
    strata = {
        name: record.get(name)
        for name in (
            "provider_family",
            "health_quota_state",
            "endpoint_state",
            "capability_class",
        )
    }
    if any(value not in _DIMENSIONS[name] for name, value in strata.items()):
        _fail(fault)
    return ThreeArmGroup(
        group_digest=stored_digest,
        candidate_identity_digest=_digest(record.get("candidate_identity_digest"), fault),
        provider_family=cast(str, strata["provider_family"]),
        health_quota_state=cast(str, strata["health_quota_state"]),
        endpoint_state=cast(str, strata["endpoint_state"]),
        capability_class=cast(str, strata["capability_class"]),
        successes=_number(record.get("successes"), fault),
        failures=_number(record.get("failures"), fault),
        community_successes=_number(record.get("community_successes"), fault),
        community_failures=_number(record.get("community_failures"), fault),
        truth=truth,
    )


def _validate_exclusion(value: object) -> ThreeArmExclusion:
    fault = ThreeArmContractFault.CORPUS_INVALID
    record = _mapping(value, fault)
    if set(record) != _EXCLUSION_KEYS:
        _fail(fault)
    unsigned = dict(record)
    stored_digest = _digest(unsigned.pop("exclusion_digest", None), fault)
    if stored_digest != canonical_digest(unsigned):
        _fail(fault)
    reason = record.get("reason")
    if not isinstance(reason, str) or reason not in _EXCLUSION_REASONS:
        _fail(fault)
    return ThreeArmExclusion(
        exclusion_digest=stored_digest,
        record_digest=_digest(record.get("record_digest"), fault),
        reason=reason,
    )


def _validate_corpus(corpus: Mapping[str, object]) -> ThreeArmCorpus:
    fault = ThreeArmContractFault.CORPUS_INVALID
    if set(corpus) != _CORPUS_KEYS or corpus.get("schema_version") != THREE_ARM_SCHEMA_VERSION:
        _fail(fault)
    unsigned = dict(corpus)
    corpus_id = unsigned.pop("corpus_id", None)
    if (
        not isinstance(corpus_id, str)
        or corpus_id != f"sha256:{canonical_digest(unsigned)}"
    ):
        _fail(fault)
    raw_groups = corpus.get("groups")
    raw_exclusions = corpus.get("exclusions")
    if (
        not isinstance(raw_groups, list)
        or len(raw_groups) != EXPECTED_GROUP_COUNT
        or not isinstance(raw_exclusions, list)
        or len(raw_exclusions) != EXPECTED_EXCLUSION_COUNT
    ):
        _fail(fault)
    groups = tuple(_validate_group(value) for value in raw_groups)
    exclusions = tuple(_validate_exclusion(value) for value in raw_exclusions)
    combinations = {
        (
            item.provider_family,
            item.health_quota_state,
            item.endpoint_state,
            item.capability_class,
        )
        for item in groups
    }
    expected = set(itertools.product(*_DIMENSIONS.values()))
    if combinations != expected:
        _fail(fault)
    if (
        len({item.group_digest for item in groups}) != EXPECTED_GROUP_COUNT
        or len({item.candidate_identity_digest for item in groups})
        != EXPECTED_GROUP_COUNT
        or tuple(item.reason for item in exclusions) != _EXCLUSION_REASONS
        or len({item.exclusion_digest for item in exclusions})
        != EXPECTED_EXCLUSION_COUNT
    ):
        _fail(fault)
    for dimension, values in _DIMENSIONS.items():
        for stratum in values:
            truths = [item.truth for item in groups if getattr(item, dimension) == stratum]
            if truths.count(True) != truths.count(False):
                _fail(fault)
    return ThreeArmCorpus(corpus_id=corpus_id, groups=groups, exclusions=exclusions)


def _thresholds(value: object) -> ThreeArmThresholds:
    fault = ThreeArmContractFault.PLAN_INVALID
    record = _mapping(value, fault)
    if dict(record) != _THRESHOLDS:
        _fail(fault)
    return ThreeArmThresholds(**cast(dict[str, object], dict(record)))  # type: ignore[arg-type]


def validate_three_arm_documents(
    plan_document: Mapping[str, object],
    corpus_document: Mapping[str, object],
) -> tuple[ThreeArmPlan, ThreeArmCorpus]:
    """Validate all freeze pins before any engine execution."""
    corpus = _validate_corpus(corpus_document)
    fault = ThreeArmContractFault.PLAN_INVALID
    plan = _mapping(plan_document, fault)
    if set(plan) != _PLAN_KEYS or plan.get("schema_version") != THREE_ARM_SCHEMA_VERSION:
        _fail(fault)
    unsigned = dict(plan)
    plan_id = unsigned.pop("plan_id", None)
    if not isinstance(plan_id, str) or plan_id != f"sha256:{canonical_digest(unsigned)}":
        _fail(fault)
    dimensions = plan.get("dimensions")
    expected_dimensions = {key: list(value) for key, value in _DIMENSIONS.items()}
    expected_scalars = {
        "gate": THREE_ARM_GATE,
        "candidate_id": _CANDIDATE_ID,
        "candidate_bundle_sha256": _CANDIDATE_BUNDLE,
        "candidate_upstream_commit": _CANDIDATE_COMMIT,
        "admitted_bundle_sha256": _ADMITTED_BUNDLE,
        "active_bundle_sha256": _ADMITTED_BUNDLE,
        "arms": list(_ARMS),
        "dimensions": expected_dimensions,
        "group_count": EXPECTED_GROUP_COUNT,
        "exclusion_count": EXPECTED_EXCLUSION_COUNT,
        "group_digests": [item.group_digest for item in corpus.groups],
        "exclusion_digests": [item.exclusion_digest for item in corpus.exclusions],
        "corpus_sha256": canonical_digest(corpus_document),
        "decision": "hold_only",
        "runtime_admitted": False,
        "network_allowed": False,
        "cost_allowed": False,
        "node_crosscheck_required": True,
    }
    if dimensions != expected_dimensions or any(
        plan.get(key) != expected for key, expected in expected_scalars.items()
    ):
        _fail(fault)
    candidate_id = plan.get("candidate_id")
    if (
        not isinstance(candidate_id, str)
        or _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
    ):
        _fail(fault)
    return (
        ThreeArmPlan(
            plan_id=plan_id,
            gate=THREE_ARM_GATE,
            candidate_id=candidate_id,
            candidate_bundle_sha256=_digest(plan.get("candidate_bundle_sha256"), fault),
            admitted_bundle_sha256=_digest(plan.get("admitted_bundle_sha256"), fault),
            active_bundle_sha256=_digest(plan.get("active_bundle_sha256"), fault),
            arms=_ARMS,
            corpus_sha256=cast(str, plan.get("corpus_sha256")),
            thresholds=_thresholds(plan.get("thresholds")),
            decision="hold_only",
            runtime_admitted=False,
            node_crosscheck_required=True,
        ),
        corpus,
    )


__all__ = [
    "EXPECTED_EXCLUSION_COUNT",
    "EXPECTED_GROUP_COUNT",
    "THREE_ARM_GATE",
    "THREE_ARM_SCHEMA_VERSION",
    "ThreeArmContractError",
    "ThreeArmContractFault",
    "ThreeArmCorpus",
    "ThreeArmExclusion",
    "ThreeArmGroup",
    "ThreeArmPlan",
    "ThreeArmThresholds",
    "build_frozen_documents",
    "canonical_digest",
    "validate_three_arm_documents",
]
