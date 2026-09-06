"""Ownership-safe Terraform lifecycle for Azure Container Apps environments.

Terraform is the sole mutation authority.  This module orchestrates its audited
plan/apply/destroy boundary and treats independent Azure Resource Manager reads
as postcondition evidence.  It never handles credentials, provider output, or
model content directly.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, runtime_checkable

from general_ludd.infra.azure_containerapp_gpu import A100_PROFILE, T4_PROFILE

_RESOURCE_GROUP_PATTERN = re.compile(r"(?=.{1,90}\Z)[A-Za-z0-9_().-]+(?<!\.)")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?")
_LOCATION_PATTERN = re.compile(r"[a-z][a-z0-9]{1,31}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_EXPIRES_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_APP_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")
_ENVIRONMENT_API_TYPE = "Microsoft.App/managedEnvironments@2025-07-01"
_ENVIRONMENT_RESOURCE_TYPE = "Microsoft.App/managedEnvironments"
_RESOURCE_ADDRESS = "module.environment.azapi_resource.managed_environment"
_PROFILE_PAIRS = {
    "gpu-t4": T4_PROFILE.workload_profile_type,
    "gpu-a100": A100_PROFILE.workload_profile_type,
}
_MANAGED_BY = "general-ludd"
_LIFECYCLE_VERSION = "1"


class AzureEnvironmentLifecycleError(RuntimeError):
    """Censored failure at one environment lifecycle phase."""

    def __init__(self, phase: str) -> None:
        """Expose only a fixed phase, never provider or resource data."""
        super().__init__(f"Azure environment lifecycle failed: {phase}")
        self.phase = phase


@dataclass(frozen=True, slots=True, order=True)
class AzureEnvironmentProfile:
    """One reviewed serverless-GPU profile configured on an environment."""

    profile_name: str
    workload_profile_type: str

    def __post_init__(self) -> None:
        """Require the canonical name/type pairing used by runner plans."""
        if _PROFILE_PAIRS.get(self.profile_name) != self.workload_profile_type:
            raise ValueError("profile name and workload type must be an approved pair")


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
    teardown_when_idle: bool = True

    def __post_init__(self) -> None:
        """Validate and canonicalize every value before Terraform can run."""
        try:
            canonical_subscription = str(uuid.UUID(self.subscription_id))
        except (ValueError, AttributeError):
            canonical_subscription = ""
        if self.subscription_id != canonical_subscription:
            raise ValueError("subscription_id must be a canonical UUID")
        if _RESOURCE_GROUP_PATTERN.fullmatch(self.resource_group) is None:
            raise ValueError("resource_group must be a safe Azure name")
        if _ENVIRONMENT_NAME_PATTERN.fullmatch(self.environment_name) is None:
            raise ValueError("environment_name must be a safe lowercase Azure name")
        if _LOCATION_PATTERN.fullmatch(self.location) is None:
            raise ValueError("location must be a canonical Azure region")
        if (
            not isinstance(self.profiles, tuple)
            or not 1 <= len(self.profiles) <= len(_PROFILE_PAIRS)
            or not all(isinstance(item, AzureEnvironmentProfile) for item in self.profiles)
        ):
            raise ValueError("profiles must contain one or two approved profiles")
        canonical_profiles = tuple(sorted(self.profiles))
        if len(set(canonical_profiles)) != len(canonical_profiles):
            raise ValueError("profiles must not contain duplicates")
        if len({profile.profile_name for profile in canonical_profiles}) != len(
            canonical_profiles
        ):
            raise ValueError("profiles must use unique names")
        object.__setattr__(self, "profiles", canonical_profiles)
        if _DIGEST_PATTERN.fullmatch(self.owner_digest) is None:
            raise ValueError("owner_digest must be a lowercase SHA-256 digest")
        if _DIGEST_PATTERN.fullmatch(self.plan_digest) is None:
            raise ValueError("plan_digest must be a lowercase SHA-256 digest")
        if _EXPIRES_PATTERN.fullmatch(self.expires_at_utc) is None:
            raise ValueError("expires_at_utc must be an RFC3339 UTC second")
        if not isinstance(self.teardown_when_idle, bool):
            raise ValueError("teardown_when_idle must be boolean")

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
    def operation_digest(self) -> str:
        """Return a content-free correlation digest for lifecycle telemetry."""
        payload = {
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
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


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
    ENVIRONMENT_ABSENT = "environment_absent"
    OWNERSHIP_VERIFIED = "ownership_verified"
    PLAN_STARTED = "plan_started"
    PLAN_AUDITED = "plan_audited"
    APPLY_STARTED = "apply_started"
    APPLY_SUCCEEDED = "apply_succeeded"
    READINESS_VERIFIED = "readiness_verified"
    RELEASE_STARTED = "release_started"
    APP_INVENTORY_VERIFIED = "app_inventory_verified"
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


_TraceSink = Callable[[EnvironmentLifecycleTrace], None]


def _discard_trace(_trace: EnvironmentLifecycleTrace) -> None:
    return None


def _emit(
    sink: _TraceSink,
    event: EnvironmentLifecycleEvent,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    active_app_count: int = 0,
) -> None:
    try:
        sink(
            EnvironmentLifecycleTrace(
                event=event,
                operation_digest=policy.operation_digest,
                profile_count=len(policy.profiles),
                active_app_count=active_app_count,
            )
        )
    except Exception:
        raise AzureEnvironmentLifecycleError("trace") from None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError
    return value


def _string_member(value: Mapping[str, object], key: str) -> str:
    member = value.get(key)
    if not isinstance(member, str):
        raise ValueError
    return member


def _profile_tuple(value: object) -> tuple[AzureEnvironmentProfile, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= len(_PROFILE_PAIRS):
        raise ValueError
    profiles: list[AzureEnvironmentProfile] = []
    for raw_profile in value:
        profile = _mapping(raw_profile)
        profiles.append(
            AzureEnvironmentProfile(
                profile_name=_string_member(profile, "name"),
                workload_profile_type=_string_member(
                    profile, "workloadProfileType"
                ),
            )
        )
    result = tuple(sorted(profiles))
    if len(result) != len(set(result)):
        raise ValueError
    return result


def _validated_environment_profiles(
    document: object,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    require_desired_profiles: bool,
    require_current_tags: bool,
) -> tuple[AzureEnvironmentProfile, ...]:
    try:
        root = _mapping(document)
        if (
            _string_member(root, "id").casefold() != policy.environment_id.casefold()
            or _string_member(root, "name") != policy.environment_name
            or _string_member(root, "type").casefold()
            != _ENVIRONMENT_RESOURCE_TYPE.casefold()
            or _string_member(root, "location").casefold()
            != policy.location.casefold()
        ):
            raise ValueError
        tags = _mapping(root.get("tags"))
        required_tags = {
            "gludd-managed-by": _MANAGED_BY,
            "gludd-lifecycle-version": _LIFECYCLE_VERSION,
            "gludd-owner-digest": policy.owner_digest,
        }
        if require_current_tags:
            required_tags = policy.ownership_tags
        if any(tags.get(key) != value for key, value in required_tags.items()):
            raise ValueError
        properties = _mapping(root.get("properties"))
        if _string_member(properties, "provisioningState") != "Succeeded":
            raise ValueError
        profiles = _profile_tuple(properties.get("workloadProfiles"))
        if require_desired_profiles and not set(policy.profiles).issubset(profiles):
            raise ValueError
        return profiles
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("ownership") from None


def _expected_plan_after(policy: AzureEnvironmentLifecyclePolicy) -> dict[str, object]:
    return {
        "type": _ENVIRONMENT_API_TYPE,
        "name": policy.environment_name,
        "parent_id": policy.resource_group_id,
        "location": policy.location,
        "tags": policy.ownership_tags,
        "body": {
            "properties": {
                "workloadProfiles": [
                    {
                        "name": profile.profile_name,
                        "workloadProfileType": profile.workload_profile_type,
                    }
                    for profile in policy.profiles
                ]
            }
        },
    }


def audit_environment_plan(
    plan: object,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    existed_before: bool,
) -> bool:
    """Require exactly one bounded environment create, update, or no-op."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be a Terraform plan object")
    if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError("policy must be AzureEnvironmentLifecyclePolicy")
    if not isinstance(existed_before, bool):
        raise ValueError("existed_before must be boolean")
    try:
        changes = plan.get("resource_changes")
        if not isinstance(changes, list) or len(changes) != 1:
            raise ValueError
        resource = _mapping(changes[0])
        if (
            resource.get("address") != _RESOURCE_ADDRESS
            or resource.get("mode") != "managed"
            or resource.get("type") != "azapi_resource"
            or resource.get("name") != "managed_environment"
            or resource.get("provider_name")
            != "registry.terraform.io/azure/azapi"
        ):
            raise ValueError
        change = _mapping(resource.get("change"))
        actions = change.get("actions")
        allowed = ({("no-op",), ("update",)} if existed_before else {("create",)})
        if not isinstance(actions, list) or tuple(actions) not in allowed:
            raise ValueError
        if change.get("after") != _expected_plan_after(policy):
            raise ValueError
        after_unknown = change.get("after_unknown")
        if after_unknown not in ({}, None):
            raise ValueError
        return tuple(actions) != ("no-op",)
    except (KeyError, TypeError, ValueError):
        raise AzureEnvironmentLifecycleError("plan") from None


def _validated_boundaries(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink,
) -> None:
    if not isinstance(policy, AzureEnvironmentLifecyclePolicy):
        raise ValueError("policy must be AzureEnvironmentLifecyclePolicy")
    if not isinstance(runtime, AzureContainerAppEnvironmentRuntime):
        raise ValueError("runtime must implement AzureContainerAppEnvironmentRuntime")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")


def _read_environment(
    runtime: AzureContainerAppEnvironmentRuntime,
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    expect_absent: bool,
    phase: str,
) -> object | None:
    try:
        return runtime.read_environment(policy, expect_absent=expect_absent)
    except Exception:
        raise AzureEnvironmentLifecycleError(phase) from None


def _merged_policy(
    policy: AzureEnvironmentLifecyclePolicy,
    existing_profiles: tuple[AzureEnvironmentProfile, ...],
) -> AzureEnvironmentLifecyclePolicy:
    profiles = tuple(sorted(set(existing_profiles) | set(policy.profiles)))
    return replace(policy, profiles=profiles)


def _recover_new_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    runtime: AzureContainerAppEnvironmentRuntime,
) -> bool:
    """Best-effort recovery for a create that may have partially succeeded."""
    try:
        result = release_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=_discard_trace,
        )
        return result.disposition in {
            EnvironmentLifecycleDisposition.ABSENT,
            EnvironmentLifecycleDisposition.DESTROYED,
        }
    except Exception:
        return False


def ensure_azure_containerapp_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink = _discard_trace,
) -> AzureEnvironmentLifecycleResult:
    """Create or reconcile an owned environment through an audited Terraform plan."""
    _validated_boundaries(policy, runtime, trace_sink)
    _emit(trace_sink, EnvironmentLifecycleEvent.INSPECTION_STARTED, policy)
    existing = _read_environment(
        runtime, policy, expect_absent=False, phase="inspection"
    )
    existed_before = existing is not None
    effective_policy = policy
    if existing is None:
        _emit(trace_sink, EnvironmentLifecycleEvent.ENVIRONMENT_ABSENT, policy)
    else:
        existing_profiles = _validated_environment_profiles(
            existing,
            policy,
            require_desired_profiles=False,
            require_current_tags=False,
        )
        effective_policy = _merged_policy(policy, existing_profiles)
        _emit(
            trace_sink,
            EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED,
            effective_policy,
        )
    _emit(trace_sink, EnvironmentLifecycleEvent.PLAN_STARTED, effective_policy)
    try:
        plan = runtime.plan(effective_policy)
        changed = audit_environment_plan(
            plan,
            effective_policy,
            existed_before=existed_before,
        )
    except AzureEnvironmentLifecycleError:
        raise
    except Exception:
        raise AzureEnvironmentLifecycleError("plan") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.PLAN_AUDITED, effective_policy)
    _emit(trace_sink, EnvironmentLifecycleEvent.APPLY_STARTED, effective_policy)
    try:
        runtime.apply(effective_policy)
    except Exception:
        if not existed_before and not _recover_new_environment(
            effective_policy, runtime
        ):
            raise AzureEnvironmentLifecycleError("cleanup") from None
        raise AzureEnvironmentLifecycleError("apply") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.APPLY_SUCCEEDED, effective_policy)
    verified = _read_environment(
        runtime,
        effective_policy,
        expect_absent=False,
        phase="readiness",
    )
    try:
        if verified is None:
            raise AzureEnvironmentLifecycleError("ownership")
        _validated_environment_profiles(
            verified,
            effective_policy,
            require_desired_profiles=True,
            require_current_tags=True,
        )
    except AzureEnvironmentLifecycleError:
        if not existed_before and not _recover_new_environment(
            effective_policy, runtime
        ):
            raise AzureEnvironmentLifecycleError("cleanup") from None
        raise AzureEnvironmentLifecycleError("readiness") from None
    _emit(
        trace_sink,
        EnvironmentLifecycleEvent.READINESS_VERIFIED,
        effective_policy,
    )
    disposition = (
        EnvironmentLifecycleDisposition.CREATED
        if not existed_before
        else (
            EnvironmentLifecycleDisposition.RECONCILED
            if changed
            else EnvironmentLifecycleDisposition.REUSED
        )
    )
    return AzureEnvironmentLifecycleResult(
        disposition=disposition,
        environment_id=effective_policy.environment_id,
        effective_profiles=effective_policy.profiles,
    )


def _validated_app_inventory(
    inventory: object,
    policy: AzureEnvironmentLifecyclePolicy,
) -> tuple[str, ...]:
    if not isinstance(inventory, tuple) or not all(
        isinstance(resource_id, str) for resource_id in inventory
    ):
        raise AzureEnvironmentLifecycleError("inventory")
    prefix = (
        f"{policy.resource_group_id}/providers/Microsoft.App/containerApps/"
    ).casefold()
    for resource_id in inventory:
        if not resource_id.casefold().startswith(prefix):
            raise AzureEnvironmentLifecycleError("inventory")
        name = resource_id[len(prefix) :]
        if _APP_NAME_PATTERN.fullmatch(name) is None:
            raise AzureEnvironmentLifecycleError("inventory")
    return inventory


def release_azure_containerapp_environment(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    runtime: AzureContainerAppEnvironmentRuntime,
    trace_sink: _TraceSink = _discard_trace,
) -> AzureEnvironmentLifecycleResult:
    """Destroy an idle owned environment through Terraform and prove absence."""
    _validated_boundaries(policy, runtime, trace_sink)
    _emit(trace_sink, EnvironmentLifecycleEvent.RELEASE_STARTED, policy)
    existing = _read_environment(
        runtime, policy, expect_absent=False, phase="inspection"
    )
    if existing is None:
        _emit(trace_sink, EnvironmentLifecycleEvent.ABSENCE_VERIFIED, policy)
        return AzureEnvironmentLifecycleResult(
            disposition=EnvironmentLifecycleDisposition.ABSENT,
            environment_id=policy.environment_id,
            effective_profiles=policy.profiles,
        )
    profiles = _validated_environment_profiles(
        existing,
        policy,
        require_desired_profiles=False,
        require_current_tags=False,
    )
    effective_policy = _merged_policy(policy, profiles)
    _emit(
        trace_sink,
        EnvironmentLifecycleEvent.OWNERSHIP_VERIFIED,
        effective_policy,
    )
    if not policy.teardown_when_idle:
        _emit(
            trace_sink,
            EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED,
            effective_policy,
        )
        return AzureEnvironmentLifecycleResult(
            disposition=EnvironmentLifecycleDisposition.RETAINED,
            environment_id=policy.environment_id,
            effective_profiles=effective_policy.profiles,
        )
    try:
        apps = _validated_app_inventory(
            runtime.list_environment_apps(effective_policy),
            effective_policy,
        )
    except AzureEnvironmentLifecycleError:
        raise
    except Exception:
        raise AzureEnvironmentLifecycleError("inventory") from None
    _emit(
        trace_sink,
        EnvironmentLifecycleEvent.APP_INVENTORY_VERIFIED,
        effective_policy,
        active_app_count=len(apps),
    )
    if apps:
        _emit(
            trace_sink,
            EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED,
            effective_policy,
            active_app_count=len(apps),
        )
        return AzureEnvironmentLifecycleResult(
            disposition=EnvironmentLifecycleDisposition.RETAINED,
            environment_id=policy.environment_id,
            effective_profiles=effective_policy.profiles,
            active_app_count=len(apps),
        )
    _emit(trace_sink, EnvironmentLifecycleEvent.DESTROY_STARTED, effective_policy)
    try:
        runtime.destroy(effective_policy)
    except Exception:
        raise AzureEnvironmentLifecycleError("destroy") from None
    _emit(trace_sink, EnvironmentLifecycleEvent.DESTROY_SUCCEEDED, effective_policy)
    remaining = _read_environment(
        runtime,
        effective_policy,
        expect_absent=True,
        phase="absence",
    )
    if remaining is not None:
        raise AzureEnvironmentLifecycleError("absence")
    _emit(trace_sink, EnvironmentLifecycleEvent.ABSENCE_VERIFIED, effective_policy)
    return AzureEnvironmentLifecycleResult(
        disposition=EnvironmentLifecycleDisposition.DESTROYED,
        environment_id=policy.environment_id,
        effective_profiles=effective_policy.profiles,
    )


__all__ = [
    "AzureContainerAppEnvironmentRuntime",
    "AzureEnvironmentLifecycleError",
    "AzureEnvironmentLifecyclePolicy",
    "AzureEnvironmentLifecycleResult",
    "AzureEnvironmentProfile",
    "EnvironmentLifecycleDisposition",
    "EnvironmentLifecycleEvent",
    "EnvironmentLifecycleTrace",
    "audit_environment_plan",
    "ensure_azure_containerapp_environment",
    "release_azure_containerapp_environment",
]
