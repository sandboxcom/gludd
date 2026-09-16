"""Versioned, content-free operational availability for Azure placements.

The evidence in this module is intentionally independent from model-quality
calibration.  It binds one region, provider inventory SKU, immutable runtime,
and generic serving topology while excluding model names, prompts, endpoints,
credentials, and provider-authored error text.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.self_improve.azure_infrastructure_evidence import (
    AzureInfrastructurePhase,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_COLLECTION = "self_improve.azure_operational_availability"
_SCHEMA_VERSION = 1
_SCOPE_VERSION = 1
_MAX_EVIDENCE_AGE_SECONDS = 86_400
_MAX_FAILURE_THRESHOLD = 100
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_RUNTIME_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE_RE = re.compile(r"[^@\s]+@(sha256:[0-9a-f]{64})")
_LOCATION_RE = re.compile(r"[a-z][a-z0-9]{1,31}")
_SKU_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]{0,199}")
_RECORD_KEYS = frozenset(
    {
        "collection",
        "deployment_identity_digest",
        "evidence_digest",
        "location",
        "outcome",
        "phase",
        "registered_at",
        "resource_sku",
        "runtime_version_digest",
        "schema_version",
        "scope_digest",
        "scope_version",
        "topology_digest",
    }
)


class AzureOperationalEvidenceError(ValueError):
    """Refuse malformed or drifted records from the owned collection."""


class AzureAvailabilityTerminal(StrEnum):
    """Terminal placement outcomes eligible to influence future placement."""

    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _coordinate(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{label} must be one finite non-negative timestamp")
    return float(value)


def _runtime_digest(container_image: object) -> str:
    if not isinstance(container_image, str):
        raise ValueError("container_image must use one immutable SHA-256 digest")
    matched = _IMAGE_RE.fullmatch(container_image)
    if matched is None:
        raise ValueError("container_image must use one immutable SHA-256 digest")
    return matched.group(1)


@dataclass(frozen=True, slots=True)
class AzureAvailabilityScope:
    """One versioned region/SKU/runtime/topology evidence stratum."""

    location: str
    resource_sku: str
    runtime_version_digest: str
    topology_digest: str
    scope_version: int = _SCOPE_VERSION

    def __post_init__(self) -> None:
        """Reject ambiguous coordinates and unsupported scope versions."""
        _coordinate(self.location, _LOCATION_RE, "location")
        _coordinate(self.resource_sku, _SKU_RE, "resource_sku")
        _coordinate(
            self.runtime_version_digest,
            _RUNTIME_DIGEST_RE,
            "runtime_version_digest",
        )
        _coordinate(self.topology_digest, _DIGEST_RE, "topology_digest")
        if type(self.scope_version) is not int or self.scope_version != _SCOPE_VERSION:
            raise ValueError("scope_version is unsupported")

    def payload(self) -> dict[str, object]:
        """Return the complete content-free scope payload."""
        return {
            "location": self.location,
            "resource_sku": self.resource_sku,
            "runtime_version_digest": self.runtime_version_digest,
            "scope_version": self.scope_version,
            "topology_digest": self.topology_digest,
        }

    @property
    def scope_digest(self) -> str:
        """Bind the scope protocol and every placement coordinate."""
        return _canonical_digest(
            {
                "protocol": "gludd-azure-availability-scope-v1",
                **self.payload(),
            }
        )


def build_azure_availability_scope(
    *,
    location: str,
    resource_sku: str,
    container_image: str,
    requirement: ModelServingRequirement,
) -> AzureAvailabilityScope:
    """Build a scope from generic serving demand without model identifiers."""
    if not isinstance(requirement, ModelServingRequirement):
        raise ValueError("requirement must be a ModelServingRequirement")
    topology_digest = _canonical_digest(
        {
            "protocol": "gludd-azure-serving-topology-v1",
            "required_vram_mib": requirement.required_vram_mib,
            "runtime": "vllm-openai",
        }
    )
    return AzureAvailabilityScope(
        location=location,
        resource_sku=resource_sku,
        runtime_version_digest=_runtime_digest(container_image),
        topology_digest=topology_digest,
    )


@dataclass(frozen=True, slots=True)
class AzureAvailabilityAssessment:
    """Finite-horizon placement evidence for one exact scope."""

    scope_digest: str
    observed_outcomes: int
    successful_outcomes: int
    failed_outcomes: int
    consecutive_failures: int
    availability_score: float
    feasible: bool
    last_observed_at: float | None


@dataclass(frozen=True, slots=True)
class AzureAvailabilityIndex:
    """Immutable recent assessments addressable only by exact scope digest."""

    assessments: tuple[AzureAvailabilityAssessment, ...]

    def assess(self, scope: AzureAvailabilityScope) -> AzureAvailabilityAssessment:
        """Return exact evidence or a neutral, feasible prior."""
        if not isinstance(scope, AzureAvailabilityScope):
            raise ValueError("scope must be an AzureAvailabilityScope")
        for assessment in self.assessments:
            if assessment.scope_digest == scope.scope_digest:
                return assessment
        return AzureAvailabilityAssessment(
            scope_digest=scope.scope_digest,
            observed_outcomes=0,
            successful_outcomes=0,
            failed_outcomes=0,
            consecutive_failures=0,
            availability_score=0.5,
            feasible=True,
            last_observed_at=None,
        )

    @property
    def observed_scope_count(self) -> int:
        """Return the number of exact scopes with recent terminal evidence."""
        return len(self.assessments)


def record_azure_availability_terminal(
    store: CapabilityEvidenceStore,
    *,
    scope: AzureAvailabilityScope,
    deployment_identity_digest: str,
    phase: AzureInfrastructurePhase,
    outcome: AzureAvailabilityTerminal,
    clock: Callable[[], float] = time.time,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Persist one validated terminal placement outcome and a censored trace."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    if not isinstance(scope, AzureAvailabilityScope):
        raise ValueError("scope must be an AzureAvailabilityScope")
    checked_deployment = _coordinate(
        deployment_identity_digest,
        _DIGEST_RE,
        "deployment_identity_digest",
    )
    if phase not in {
        AzureInfrastructurePhase.PREFLIGHT,
        AzureInfrastructurePhase.CANDIDATE_STARTUP,
    }:
        raise ValueError("phase is not eligible for placement learning")
    if not isinstance(outcome, AzureAvailabilityTerminal):
        raise ValueError("outcome must be an AzureAvailabilityTerminal")
    if not callable(clock):
        raise ValueError("clock must be callable")
    try:
        registered_at = _timestamp(clock(), "clock")
    except ValueError:
        raise
    except Exception:
        raise ValueError("clock must return one finite non-negative timestamp") from None
    if trace_sink is not None and not callable(trace_sink):
        raise ValueError("trace_sink must be callable")
    payload: dict[str, object] = {
        "collection": _COLLECTION,
        "deployment_identity_digest": checked_deployment,
        "location": scope.location,
        "outcome": outcome.value,
        "phase": phase.value,
        "registered_at": registered_at,
        "resource_sku": scope.resource_sku,
        "runtime_version_digest": scope.runtime_version_digest,
        "schema_version": _SCHEMA_VERSION,
        "scope_digest": scope.scope_digest,
        "scope_version": scope.scope_version,
        "topology_digest": scope.topology_digest,
    }
    record = {**payload, "evidence_digest": _canonical_digest(payload)}
    count = store.register_evidence(record)
    if trace_sink is not None:
        try:
            trace_sink(
                {
                    "deployment_identity_digest": checked_deployment,
                    "event": "SELF_IMPROVE_AZURE_AVAILABILITY_RECORDED",
                    "outcome": outcome.value,
                    "phase": phase.value,
                    "resource_sku": scope.resource_sku,
                    "schema_version": _SCHEMA_VERSION,
                    "scope_digest": scope.scope_digest,
                    "scope_version": scope.scope_version,
                }
            )
        except Exception:
            raise RuntimeError(
                "Azure availability trace publication failed"
            ) from None
    return count


def _owned_record(raw: Mapping[str, Any]) -> bool:
    return raw.get("collection") == _COLLECTION


def _validated_record(
    record: Mapping[str, Any],
    *,
    now_epoch: float,
) -> tuple[AzureAvailabilityScope, AzureAvailabilityTerminal, float]:
    if set(record) != _RECORD_KEYS:
        raise AzureOperationalEvidenceError("availability record schema is malformed")
    if record.get("schema_version") != _SCHEMA_VERSION:
        raise AzureOperationalEvidenceError("availability schema version drifted")
    if record.get("scope_version") != _SCOPE_VERSION:
        raise AzureOperationalEvidenceError("availability scope version drifted")
    try:
        observed_at = _timestamp(record.get("registered_at"), "registered_at")
    except ValueError as exc:
        raise AzureOperationalEvidenceError(str(exc)) from None
    if observed_at > now_epoch:
        raise AzureOperationalEvidenceError("availability record is from the future")
    try:
        scope = AzureAvailabilityScope(
            location=record["location"],
            resource_sku=record["resource_sku"],
            runtime_version_digest=record["runtime_version_digest"],
            topology_digest=record["topology_digest"],
            scope_version=record["scope_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        label = str(exc) or "scope"
        raise AzureOperationalEvidenceError(label) from None
    if record.get("scope_digest") != scope.scope_digest:
        raise AzureOperationalEvidenceError("availability scope digest drifted")
    try:
        _coordinate(
            record.get("deployment_identity_digest"),
            _DIGEST_RE,
            "deployment_identity_digest",
        )
        phase_value = record.get("phase")
        outcome_value = record.get("outcome")
        if not isinstance(phase_value, str) or not isinstance(outcome_value, str):
            raise ValueError("phase or outcome is invalid")
        phase = AzureInfrastructurePhase(phase_value)
        outcome = AzureAvailabilityTerminal(outcome_value)
    except (TypeError, ValueError) as exc:
        raise AzureOperationalEvidenceError(str(exc)) from None
    if phase not in {
        AzureInfrastructurePhase.PREFLIGHT,
        AzureInfrastructurePhase.CANDIDATE_STARTUP,
    }:
        raise AzureOperationalEvidenceError("availability phase drifted")
    payload = {
        key: value for key, value in record.items() if key != "evidence_digest"
    }
    if record.get("evidence_digest") != _canonical_digest(payload):
        raise AzureOperationalEvidenceError("availability evidence digest drifted")
    return scope, outcome, observed_at


def load_azure_availability_index(
    store: CapabilityEvidenceStore,
    *,
    max_age_seconds: int,
    minimum_failures: int,
    now_epoch: float | None = None,
) -> AzureAvailabilityIndex:
    """Load strict recent placement evidence without mutating durable history."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or not 1 <= max_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS
    ):
        raise ValueError("max_age_seconds is outside its hard bound")
    if (
        isinstance(minimum_failures, bool)
        or not isinstance(minimum_failures, int)
        or not 1 <= minimum_failures <= _MAX_FAILURE_THRESHOLD
    ):
        raise ValueError("minimum_failures is outside its hard bound")
    current = _timestamp(time.time() if now_epoch is None else now_epoch, "now_epoch")
    grouped: dict[
        str,
        list[tuple[AzureAvailabilityScope, AzureAvailabilityTerminal, float]],
    ] = defaultdict(list)
    for raw in store.list_all():
        if not _owned_record(raw):
            continue
        scope, outcome, observed_at = _validated_record(raw, now_epoch=current)
        if current - observed_at <= max_age_seconds:
            grouped[scope.scope_digest].append((scope, outcome, observed_at))
    assessments: list[AzureAvailabilityAssessment] = []
    for scope_digest, records in grouped.items():
        ordered = sorted(records, key=lambda item: item[2])
        successful = sum(
            outcome is AzureAvailabilityTerminal.AVAILABLE
            for _scope, outcome, _observed_at in ordered
        )
        failed = len(ordered) - successful
        consecutive_failures = 0
        for _scope, outcome, _observed_at in ordered:
            if outcome is AzureAvailabilityTerminal.AVAILABLE:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        assessments.append(
            AzureAvailabilityAssessment(
                scope_digest=scope_digest,
                observed_outcomes=len(ordered),
                successful_outcomes=successful,
                failed_outcomes=failed,
                consecutive_failures=consecutive_failures,
                availability_score=(1.0 + successful) / (2.0 + len(ordered)),
                feasible=consecutive_failures < minimum_failures,
                last_observed_at=ordered[-1][2],
            )
        )
    return AzureAvailabilityIndex(
        assessments=tuple(sorted(assessments, key=lambda item: item.scope_digest))
    )


__all__ = (
    "AzureAvailabilityAssessment",
    "AzureAvailabilityIndex",
    "AzureAvailabilityScope",
    "AzureAvailabilityTerminal",
    "AzureOperationalEvidenceError",
    "build_azure_availability_scope",
    "load_azure_availability_index",
    "record_azure_availability_terminal",
)
