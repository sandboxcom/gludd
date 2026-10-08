"""Immutable evidence and narrow boundaries for Container App GPU preflight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from general_ludd.infra.azure_containerapp_gpu import AzureContainerAppGPUProfile

ARM_SCOPE = "https://management.azure.com/.default"


class AzureContainerAppPreflightError(ValueError):
    """Raised when read-only Azure evidence is missing, invalid, or insufficient."""


class AzureContainerAppEvidenceError(AzureContainerAppPreflightError):
    """Carry a typed refusal reason for invalid read-only Azure evidence."""

    def __init__(self, reason: str, message: str) -> None:
        """Record a stable machine-readable reason and censored human message."""
        super().__init__(message)
        self.reason = reason


class AccessToken(Protocol):
    """Minimum Azure access-token boundary used by the preflight."""

    token: str


class TokenCredential(Protocol):
    """Minimum Azure Identity credential boundary used by the preflight."""

    def get_token(self, *scopes: str) -> AccessToken:
        """Return an access token for the requested Azure scopes."""
        ...


class ARMJSONTransport(Protocol):
    """Fixed-origin Azure Resource Manager JSON GET boundary."""

    def get_json(self, path: str, bearer_token: str) -> object:
        """Read one allowlisted ARM path and return decoded JSON."""
        ...


@dataclass(frozen=True, slots=True)
class ContainerAppUsage:
    """One validated, non-secret environment quota record."""

    name: str
    current_value: float
    limit: float
    unit: str

    @property
    def remaining(self) -> float:
        """Return non-negative quota headroom."""
        return max(self.limit - self.current_value, 0.0)


@dataclass(frozen=True, slots=True)
class _ConfiguredWorkloadProfile:
    name: str
    workload_profile_type: str
    minimum_count: int
    maximum_count: int | None


@dataclass(frozen=True, slots=True)
class _EnvironmentEvidence:
    configured_profile: _ConfiguredWorkloadProfile
    available_profile_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _WorkloadProfileState:
    name: str
    current_count: int
    maximum_count: int
    minimum_count: int

    @property
    def remaining(self) -> int:
        return self.maximum_count - self.current_count


@dataclass(frozen=True, slots=True)
class PreflightTrace:
    """Content-free phase transition suitable for events and durable logs."""

    phase: str
    location: str
    profile_name: str | None = None
    record_count: int | None = None
    record_names: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AzureContainerAppPreflightResult:
    """Read-only proof that one right-sized GPU profile has remaining quota."""

    ready: bool
    location: str
    profile: AzureContainerAppGPUProfile
    required_vram_mib: int
    available_profile_types: tuple[str, ...]
    quota_scope: str
    quota_verified: bool
    quota_name: str | None
    quota_remaining: float | None


__all__ = (
    "ARM_SCOPE",
    "ARMJSONTransport",
    "AccessToken",
    "AzureContainerAppEvidenceError",
    "AzureContainerAppPreflightError",
    "AzureContainerAppPreflightResult",
    "ContainerAppUsage",
    "PreflightTrace",
    "TokenCredential",
)
