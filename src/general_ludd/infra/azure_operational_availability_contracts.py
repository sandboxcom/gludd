"""Provider-neutral contracts for Azure operational-availability evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement

_SCOPE_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_RUNTIME_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_IMAGE_RE = re.compile(r"[^@\s]+@(sha256:[0-9a-f]{64})")
_LOCATION_RE = re.compile(r"[a-z][a-z0-9]{1,31}")
_SKU_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]{0,199}")


class AzureInfrastructurePhase(StrEnum):
    """Fixed phases used by Azure infrastructure evidence channels."""

    PREFLIGHT = "preflight"
    CANDIDATE_STARTUP = "candidate_startup"
    REQUEST = "request"


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


__all__ = (
    "AzureAvailabilityAssessment",
    "AzureAvailabilityIndex",
    "AzureAvailabilityScope",
    "AzureAvailabilityTerminal",
    "AzureInfrastructurePhase",
    "build_azure_availability_scope",
)
