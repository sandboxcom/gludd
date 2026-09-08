"""Immutable contracts for Terraform-owned Azure Container Apps environments."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from general_ludd.infra.azure_containerapp_gpu import A100_PROFILE, T4_PROFILE

_RESOURCE_GROUP_PATTERN = re.compile(r"(?=.{1,90}\Z)[A-Za-z0-9_().-]+(?<!\.)")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?")
_LOCATION_PATTERN = re.compile(r"[a-z][a-z0-9]{1,31}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_EXPIRES_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_PROFILE_PAIRS = {
    "gpu-t4": T4_PROFILE.workload_profile_type,
    "gpu-a100": A100_PROFILE.workload_profile_type,
}
_MANAGED_BY = "general-ludd"
_LIFECYCLE_VERSION = "1"


class AzureEnvironmentLifecycleError(RuntimeError):
    """Censored failure at one environment lifecycle phase."""

    def __init__(self, phase: str, *, reason: str | None = None) -> None:
        """Expose only a fixed phase, never provider or resource data."""
        super().__init__(f"Azure environment lifecycle failed: {phase}")
        self.phase = phase
        self.reason = reason


@dataclass(frozen=True, slots=True, order=True)
class AzureEnvironmentProfile:
    """One reviewed serverless-GPU profile configured on an environment."""

    profile_name: str
    workload_profile_type: str

    def __post_init__(self) -> None:
        """Require the canonical name/type pairing used by runner plans."""
        if _PROFILE_PAIRS.get(self.profile_name) != self.workload_profile_type:
            raise ValueError("profile name and workload type must be an approved pair")


def _canonical_subscription_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return ""


def _canonical_profiles(
    profiles: tuple[AzureEnvironmentProfile, ...],
) -> tuple[AzureEnvironmentProfile, ...]:
    if (
        not isinstance(profiles, tuple)
        or not 1 <= len(profiles) <= len(_PROFILE_PAIRS)
        or not all(isinstance(item, AzureEnvironmentProfile) for item in profiles)
    ):
        raise ValueError("profiles must contain one or two approved profiles")
    canonical = tuple(sorted(profiles))
    if len(set(canonical)) != len(canonical):
        raise ValueError("profiles must not contain duplicates")
    if len({profile.profile_name for profile in canonical}) != len(canonical):
        raise ValueError("profiles must use unique names")
    return canonical


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AzureEnvironmentLifecyclePolicy:
    """Immutable desired state and cleanup authority for one environment."""

    subscription_id: str
    resource_group: str
    environment_name: str
    location: str
    profiles: tuple[AzureEnvironmentProfile, ...]
    owner_digest: str
    plan_digest: str
    expires_at_utc: str

    def __post_init__(self) -> None:
        """Validate and canonicalize every value before Terraform can run."""
        if self.subscription_id != _canonical_subscription_id(self.subscription_id):
            raise ValueError("subscription_id must be a canonical UUID")
        if _RESOURCE_GROUP_PATTERN.fullmatch(self.resource_group) is None:
            raise ValueError("resource_group must be a safe Azure name")
        if _ENVIRONMENT_NAME_PATTERN.fullmatch(self.environment_name) is None:
            raise ValueError("environment_name must be a safe lowercase Azure name")
        if _LOCATION_PATTERN.fullmatch(self.location) is None:
            raise ValueError("location must be a canonical Azure region")
        object.__setattr__(self, "profiles", _canonical_profiles(self.profiles))
        if _DIGEST_PATTERN.fullmatch(self.owner_digest) is None:
            raise ValueError("owner_digest must be a lowercase SHA-256 digest")
        if _DIGEST_PATTERN.fullmatch(self.plan_digest) is None:
            raise ValueError("plan_digest must be a lowercase SHA-256 digest")
        if _EXPIRES_PATTERN.fullmatch(self.expires_at_utc) is None:
            raise ValueError("expires_at_utc must be an RFC3339 UTC second")

    @property
    def resource_group_id(self) -> str:
        """Return the exact pre-existing resource-group boundary."""
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/"
            f"{self.resource_group}"
        )

    @property
    def environment_id(self) -> str:
        """Return the exact managed-environment ARM resource ID."""
        return (
            f"{self.resource_group_id}/providers/Microsoft.App/managedEnvironments/"
            f"{self.environment_name}"
        )

    @property
    def ownership_tags(self) -> dict[str, str]:
        """Return tags required both in Terraform and independent ARM truth."""
        return {
            "gludd-managed-by": _MANAGED_BY,
            "gludd-lifecycle-version": _LIFECYCLE_VERSION,
            "gludd-owner-digest": self.owner_digest,
            "gludd-plan-digest": self.plan_digest,
            "gludd-expires-at": self.expires_at_utc,
        }

    @property
    def state_digest(self) -> str:
        """Return the stable owner/resource identity for persistent Terraform state."""
        return _sha256(
            {
                "environment_id": self.environment_id.casefold(),
                "owner_digest": self.owner_digest,
            }
        )

    @property
    def operation_digest(self) -> str:
        """Return a content-free correlation digest for lifecycle telemetry."""
        return _sha256(
            {
                "environment_id": self.environment_id,
                "location": self.location,
                "profiles": [
                    (profile.profile_name, profile.workload_profile_type)
                    for profile in self.profiles
                ],
                "owner_digest": self.owner_digest,
                "plan_digest": self.plan_digest,
                "expires_at_utc": self.expires_at_utc,
            }
        )


class EnvironmentLifecycleDisposition(StrEnum):
    """Observable outcome of one lifecycle reconciliation."""

    CREATED = "created"
    REUSED = "reused"
    RECONCILED = "reconciled"
    DESTROYED = "destroyed"
    RETAINED = "retained"
    ABSENT = "absent"


class EnvironmentLifecycleEvent(StrEnum):
    """Content-free state transitions for lifecycle tracing."""

    INSPECTION_STARTED = "inspection_started"
    INSPECTION_FAILED = "inspection_failed"
    ENVIRONMENT_ABSENT = "environment_absent"
    OWNERSHIP_VERIFIED = "ownership_verified"
    PLAN_STARTED = "plan_started"
    PLAN_AUDITED = "plan_audited"
    APPLY_STARTED = "apply_started"
    APPLY_SUCCEEDED = "apply_succeeded"
    READINESS_VERIFIED = "readiness_verified"
    RELEASE_STARTED = "release_started"
    APP_INVENTORY_VERIFIED = "app_inventory_verified"
    RETENTION_EXPIRED = "retention_expired"
    ENVIRONMENT_RETAINED = "environment_retained"
    DESTROY_STARTED = "destroy_started"
    DESTROY_SUCCEEDED = "destroy_succeeded"
    ABSENCE_VERIFIED = "absence_verified"


@dataclass(frozen=True, slots=True)
class EnvironmentLifecycleTrace:
    """Secret- and resource-name-free lifecycle telemetry."""

    event: EnvironmentLifecycleEvent
    operation_digest: str
    profile_count: int = 0
    active_app_count: int = 0
    retention_plan_digest: str | None = None
    retention_seconds_remaining: int = 0
    retention_hourly_cost_microusd: int = 0
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AzureEnvironmentLifecycleResult:
    """Public postcondition evidence for one reconciliation."""

    disposition: EnvironmentLifecycleDisposition
    environment_id: str
    effective_profiles: tuple[AzureEnvironmentProfile, ...]
    active_app_count: int = 0


@runtime_checkable
class AzureContainerAppEnvironmentRuntime(Protocol):
    """Terraform effects plus independent ARM read postconditions."""

    def read_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        expect_absent: bool,
    ) -> object | None:
        """Read or observably await the exact managed environment."""
        ...

    def plan(self, policy: AzureEnvironmentLifecyclePolicy) -> object:
        """Return the JSON form of one saved Terraform plan."""
        ...

    def apply(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        """Apply only the previously audited saved Terraform plan."""
        ...

    def list_environment_apps(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        """Return exact app IDs independently observed in the environment."""
        ...

    def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        """Destroy only resources owned by this environment Terraform state."""
        ...


__all__ = [
    "AzureContainerAppEnvironmentRuntime",
    "AzureEnvironmentLifecycleError",
    "AzureEnvironmentLifecyclePolicy",
    "AzureEnvironmentLifecycleResult",
    "AzureEnvironmentProfile",
    "EnvironmentLifecycleDisposition",
    "EnvironmentLifecycleEvent",
    "EnvironmentLifecycleTrace",
]
