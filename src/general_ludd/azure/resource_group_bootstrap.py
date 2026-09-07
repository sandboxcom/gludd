"""Create one absent, owner-tagged Azure accelerator resource group.

The same exact-resource-group credential later used for Container Apps performs
this bootstrap.  Its role deliberately cannot delete the group, alter IAM,
register providers, or access data.  Microsoft's SDK performs the operation, and
an existing group is never adopted unless every identity field and owner tag match.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentials,
    build_azure_management_credential,
)
from general_ludd.azure.accelerator_role import (
    validate_resource_group,
    validate_subscription_id,
)

_LOCATION = re.compile(r"^[a-z][a-z0-9]{1,31}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_BASE_TAGS = {
    "gludd-managed-by": "general-ludd",
    "gludd-purpose": "accelerator-boundary",
}


@runtime_checkable
class _Closable(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class _ResourceGroups(Protocol):
    def get(self, resource_group: str) -> object: ...

    def create_or_update(
        self,
        resource_group: str,
        parameters: Mapping[str, object],
    ) -> object: ...


@runtime_checkable
class _ResourceClient(_Closable, Protocol):
    resource_groups: _ResourceGroups


class AzureResourceGroupBootstrapState(StrEnum):
    """Safe observable states in one exact resource-group acquisition."""

    CHECK_STARTED = "check_started"
    ABSENT = "absent"
    CREATE_STARTED = "create_started"
    CREATED = "created"
    REUSED = "reused"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AzureResourceGroupBootstrapTrace:
    """Content-free evidence for an exact bootstrap transition."""

    state: AzureResourceGroupBootstrapState
    failure_class: str | None = None


@dataclass(frozen=True, slots=True)
class AzureResourceGroupBootstrapResult:
    """Non-sensitive result for a created or already-owned group."""

    state: AzureResourceGroupBootstrapState


class AzureResourceGroupBootstrapError(RuntimeError):
    """Fixed-context bootstrap failure that excludes provider-controlled text."""

    def __init__(self, failure_class: str) -> None:
        """Retain only one bounded failure class."""
        self.failure_class = failure_class
        super().__init__("Azure resource-group bootstrap failed")


@dataclass(frozen=True, slots=True)
class AzureResourceGroupBootstrapPolicy:
    """Exact owned resource-group identity admitted to the bootstrap role."""

    subscription_id: str
    resource_group: str
    location: str
    owner_digest: str

    def __post_init__(self) -> None:
        """Validate all policy values before constructing an Azure client."""
        validate_subscription_id(self.subscription_id)
        validate_resource_group(self.resource_group)
        if not self.resource_group.casefold().startswith("gludd-"):
            raise ValueError("resource_group must use the gludd- namespace")
        if _LOCATION.fullmatch(self.location) is None:
            raise ValueError("location must be a canonical Azure location")
        if _DIGEST.fullmatch(self.owner_digest) is None:
            raise ValueError("owner_digest must be a lowercase SHA-256 digest")

    @property
    def tags(self) -> Mapping[str, str]:
        """Return the exact immutable ownership tag set."""
        return {**_BASE_TAGS, "gludd-owner-digest": self.owner_digest}


def _build_credential(credentials: AzureAcceleratorCredentials) -> _Closable:
    try:
        return cast(_Closable, build_azure_management_credential(credentials))
    except RuntimeError:
        raise AzureResourceGroupBootstrapError("dependency") from None


def _build_client(credential: object, subscription_id: str) -> _ResourceClient:
    try:
        from azure.core.credentials import TokenCredential
        from azure.mgmt.resource.resources import ResourceManagementClient
    except ImportError:
        raise AzureResourceGroupBootstrapError("dependency") from None
    return cast(
        _ResourceClient,
        ResourceManagementClient(
            credential=cast(TokenCredential, credential),
            subscription_id=subscription_id,
        ),
    )


def _status_code(error: BaseException) -> int | None:
    direct = getattr(error, "status_code", None)
    if isinstance(direct, int):
        return direct
    observed = getattr(getattr(error, "response", None), "status_code", None)
    return observed if isinstance(observed, int) else None


def _failure_class(error: BaseException) -> str:
    if isinstance(error, AzureResourceGroupBootstrapError):
        return error.failure_class
    status_code = _status_code(error)
    if status_code == 401:
        return "authentication"
    if status_code == 403:
        return "authorization"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "quota"
    return "internal"


def _member(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _normalized_location(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_exact_owned_group(
    value: object,
    policy: AzureResourceGroupBootstrapPolicy,
) -> bool:
    return bool(
        _member(value, "name") == policy.resource_group
        and _normalized_location(_member(value, "location")) == policy.location
        and _member(value, "tags") == policy.tags
    )


def _close(value: object | None) -> bool:
    if value is None:
        return True
    close = getattr(value, "close", None)
    if not callable(close):
        return True
    try:
        close()
    except Exception:
        return False
    return True


def _discard_trace(_event: AzureResourceGroupBootstrapTrace) -> None:
    return None


def ensure_azure_resource_group(
    policy: AzureResourceGroupBootstrapPolicy,
    credentials: AzureAcceleratorCredentials,
    *,
    credential_builder: Callable[[AzureAcceleratorCredentials], object] = _build_credential,
    client_builder: Callable[[object, str], _ResourceClient] = _build_client,
    trace_sink: Callable[[AzureResourceGroupBootstrapTrace], None] = _discard_trace,
) -> AzureResourceGroupBootstrapResult:
    """Create one absent owned group or reuse one exact existing group."""
    if not isinstance(policy, AzureResourceGroupBootstrapPolicy):
        raise ValueError("policy must be AzureResourceGroupBootstrapPolicy")
    if not isinstance(credentials, AzureAcceleratorCredentials):
        raise ValueError("credentials must use the Azure accelerator contract")
    if credentials.subscription_id != policy.subscription_id:
        raise ValueError("bootstrap credential subscription mismatch")
    if not callable(trace_sink):
        raise ValueError("trace_sink must be callable")

    credential: object | None = None
    client: _ResourceClient | None = None
    result: AzureResourceGroupBootstrapResult | None = None
    failure: str | None = None
    try:
        trace_sink(AzureResourceGroupBootstrapTrace(AzureResourceGroupBootstrapState.CHECK_STARTED))
        credential = credential_builder(credentials)
        client = client_builder(credential, policy.subscription_id)
        try:
            existing = client.resource_groups.get(policy.resource_group)
        except Exception as error:
            if _status_code(error) != 404:
                raise
            trace_sink(AzureResourceGroupBootstrapTrace(AzureResourceGroupBootstrapState.ABSENT))
            trace_sink(
                AzureResourceGroupBootstrapTrace(
                    AzureResourceGroupBootstrapState.CREATE_STARTED
                )
            )
            created = client.resource_groups.create_or_update(
                policy.resource_group,
                {"location": policy.location, "tags": dict(policy.tags)},
            )
            if not _is_exact_owned_group(created, policy):
                raise AzureResourceGroupBootstrapError("ownership") from None
            observed = client.resource_groups.get(policy.resource_group)
            if not _is_exact_owned_group(observed, policy):
                raise AzureResourceGroupBootstrapError("ownership") from None
            result = AzureResourceGroupBootstrapResult(
                AzureResourceGroupBootstrapState.CREATED
            )
        else:
            if not _is_exact_owned_group(existing, policy):
                raise AzureResourceGroupBootstrapError("ownership")
            result = AzureResourceGroupBootstrapResult(
                AzureResourceGroupBootstrapState.REUSED
            )
    except BaseException as error:
        failure = _failure_class(error)
    client_closed = _close(client)
    credential_closed = _close(credential)
    if not client_closed or not credential_closed:
        failure = "cleanup"
    if failure is not None:
        try:
            trace_sink(
                AzureResourceGroupBootstrapTrace(
                    AzureResourceGroupBootstrapState.FAILED,
                    failure,
                )
            )
        except Exception:
            failure = "internal"
        raise AzureResourceGroupBootstrapError(failure)
    if result is None:
        raise AzureResourceGroupBootstrapError("internal")
    trace_sink(AzureResourceGroupBootstrapTrace(result.state))
    return result


__all__ = (
    "AzureResourceGroupBootstrapError",
    "AzureResourceGroupBootstrapPolicy",
    "AzureResourceGroupBootstrapResult",
    "AzureResourceGroupBootstrapState",
    "AzureResourceGroupBootstrapTrace",
    "ensure_azure_resource_group",
)
