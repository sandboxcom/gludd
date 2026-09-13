"""Official Azure SDK readers and exact GPU-utilization attestation.

Terraform/AzAPI remains the sole mutation boundary.  These adapters expose only
the named Container Apps reads needed for independent lifecycle evidence and one
Azure Monitor metric query bound to the deployed Container App revision.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Protocol, cast

from general_ludd.infra.azure_containerapp_arm import (
    ENVIRONMENT_PREFLIGHT_API_VERSION,
    AzureContainerAppARMError,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)

_GPU_METRIC_NAME = "GpuUtilizationPercentage"
_GPU_METRIC_NAMESPACE = "Microsoft.App/containerapps"
_MAX_COLLECTION_ITEMS = 512
_MAX_METRIC_POINTS = 10_000
_MAX_TOKEN_CHARS = 8192
_MAX_STATUS_DETAIL_CHARS = 4096
_REPLICA_RUNNING_STATES = frozenset(
    {"Running", "NotRunning", "Unknown"}
)
_CONTAINER_RUNNING_STATES = frozenset(
    {"Running", "Waiting", "Terminated", "Unknown"}
)
_CONTAINER_APP_ID = re.compile(
    r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[A-Za-z0-9_().-]{1,90}/"
    r"providers/Microsoft\.App/containerApps/[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?",
    re.IGNORECASE,
)


class _ClosableClient(Protocol):
    def close(self) -> None: ...


class _MetricsReader(Protocol):
    def list(self, **kwargs: object) -> object: ...


class _MonitorClient(_ClosableClient, Protocol):
    metrics: _MetricsReader


class AzureContainerAppsSDKReadError(AzureContainerAppARMError):
    """Fixed-context failure from an official Container Apps SDK read."""


class AzureGPUMetricResponseReason(StrEnum):
    """Content-free Azure Monitor response invariant that was rejected."""

    MISSING_METRIC_COLLECTION = "missing_metric_collection"
    AMBIGUOUS_METRIC_COUNT = "ambiguous_metric_count"
    METRIC_NAME_MISMATCH = "metric_name_mismatch"
    METRIC_UNIT_MISMATCH = "metric_unit_mismatch"
    REVISION_DIMENSION_MISMATCH = "revision_dimension_mismatch"
    SAMPLE_VALUE_INVALID = "sample_value_invalid"
    SAMPLE_COUNT_EXCEEDED = "sample_count_exceeded"
    RESPONSE_SHAPE_INVALID = "response_shape_invalid"
    EVIDENCE_CONTRACT_INVALID = "evidence_contract_invalid"
    METRIC_QUERY_REJECTED = "metric_query_rejected"


class AzureGPUUtilizationAttestationError(BackendInfrastructureError):
    """Fixed-context refusal when positive GPU evidence cannot be proven."""

    def __init__(
        self,
        failure: BackendFailure,
        reason: AzureGPUMetricResponseReason | None = None,
        *,
        http_status: int = 0,
    ) -> None:
        """Retain only the typed backend class and content-free invariant."""
        if reason is not None and (
            not isinstance(reason, AzureGPUMetricResponseReason)
            or failure is not BackendFailure.INVALID_RESPONSE
        ):
            raise ValueError("metric response reason requires invalid_response")
        if (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or (http_status != 0 and not 100 <= http_status <= 599)
        ):
            raise ValueError("http_status must be zero or a valid HTTP status")
        super().__init__(failure)
        self.reason = reason.value if reason is not None else None
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class AzureGPUUtilizationEvidence:
    """Content-free positive utilization evidence for one exact revision."""

    metric_name: str
    maximum_percent: float
    positive_sample_count: int
    revision_name: str


def build_container_apps_sdk_client(
    credential: object,
    subscription_id: str,
) -> _ClosableClient:
    """Construct Microsoft's Container Apps management client lazily."""
    try:
        from azure.core.credentials import TokenCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient
    except ImportError:
        raise RuntimeError("Azure Container Apps SDK dependency is unavailable") from None
    return cast(
        _ClosableClient,
        ContainerAppsAPIClient(
            credential=cast(TokenCredential, credential),
            subscription_id=subscription_id,
            retry_total=0,
        ),
    )


def build_monitor_sdk_client(
    credential: object,
    subscription_id: str,
) -> _MonitorClient:
    """Construct Microsoft's Monitor management client lazily."""
    try:
        from azure.core.credentials import TokenCredential
        from azure.mgmt.monitor import MonitorManagementClient
    except ImportError:
        raise RuntimeError("Azure Monitor SDK dependency is unavailable") from None
    return cast(
        _MonitorClient,
        MonitorManagementClient(
            credential=cast(TokenCredential, credential),
            subscription_id=subscription_id,
            retry_total=0,
        ),
    )


def _member(
    value: object,
    snake_name: str,
    camel_name: str | None = None,
    *,
    default: object = None,
) -> object:
    if isinstance(value, Mapping):
        if snake_name in value:
            return value[snake_name]
        if camel_name is not None and camel_name in value:
            return value[camel_name]
        return default
    observed = getattr(value, snake_name, default)
    if observed is default and camel_name is not None:
        return getattr(value, camel_name, default)
    return observed


def _sequence(value: object, label: str, *, limit: int = _MAX_COLLECTION_ITEMS) -> Sequence[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > limit
    ):
        raise AzureContainerAppsSDKReadError(
            f"Azure SDK {label} response is incomplete or ambiguous"
        )
    return value


def _bounded_iterable(value: object, label: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(
        value,
        Iterable,
    ):
        raise AzureContainerAppsSDKReadError(
            f"Azure SDK {label} response is incomplete or ambiguous"
        )
    records: list[object] = []
    for record in value:
        records.append(record)
        if len(records) > _MAX_COLLECTION_ITEMS:
            raise AzureContainerAppsSDKReadError(
                f"Azure SDK {label} response is incomplete or ambiguous"
            )
    return tuple(records)


def _enum_text(value: object) -> object:
    return getattr(value, "value", value)


def _status_code(error: BaseException) -> int | None:
    direct = getattr(error, "status_code", None)
    if isinstance(direct, int):
        return direct
    response = getattr(error, "response", None)
    observed = getattr(response, "status_code", None)
    return observed if isinstance(observed, int) else None


def _gpu_monitor_failure(error: BaseException) -> BackendFailure:
    """Reduce one Monitor exception to a provider-text-free failure class."""
    if isinstance(error, BackendInfrastructureError):
        return error.failure
    if isinstance(error, TimeoutError):
        return BackendFailure.TIMEOUT
    status_code = _status_code(error)
    if status_code == 401:
        return BackendFailure.AUTHENTICATION
    if status_code == 403:
        return BackendFailure.AUTHORIZATION
    if status_code == 404:
        return BackendFailure.NOT_FOUND
    if status_code in {408, 504}:
        return BackendFailure.TIMEOUT
    if status_code == 429:
        return BackendFailure.RATE_LIMITED
    if status_code is not None and 400 <= status_code < 500:
        return BackendFailure.INVALID_RESPONSE
    return BackendFailure.TRANSPORT


def _sdk_read(
    operation: Callable[[], object],
    *,
    absent_on_404: bool = False,
) -> object | None:
    try:
        return operation()
    except AzureContainerAppsSDKReadError:
        raise
    except Exception as error:
        status_code = _status_code(error)
        if absent_on_404 and status_code == 404:
            return None
        raise AzureContainerAppsSDKReadError(
            "Azure Container Apps SDK read failed",
            status_code=status_code,
        ) from None


def _validated_token(token: str) -> None:
    if (
        not isinstance(token, str)
        or not token
        or len(token) > _MAX_TOKEN_CHARS
        or any(character.isspace() or ord(character) < 32 for character in token)
    ):
        raise ValueError("bearer token has an invalid shape")


def _environment_document(value: object) -> dict[str, object]:
    properties = _member(value, "properties")
    if properties is None:
        properties = value
    profiles = _sequence(
        _member(properties, "workload_profiles", "workloadProfiles", default=()),
        "environment workload profile",
    )
    normalized_profiles = [
        {
            "name": _member(profile, "name"),
            "workloadProfileType": _enum_text(
                _member(profile, "workload_profile_type", "workloadProfileType")
            ),
            "minimumCount": _member(profile, "minimum_count", "minimumCount", default=0),
            "maximumCount": _member(profile, "maximum_count", "maximumCount"),
        }
        for profile in profiles
    ]
    return {
        "id": _member(value, "id"),
        "name": _member(value, "name"),
        "type": _member(value, "type"),
        "location": _member(value, "location"),
        "tags": _member(value, "tags", default={}),
        "properties": {
            "provisioningState": _enum_text(
                _member(properties, "provisioning_state", "provisioningState")
            ),
            "workloadProfiles": normalized_profiles,
        },
    }


def _usage_document(values: object) -> dict[str, object]:
    records = _bounded_iterable(values, "environment usage")
    normalized: list[dict[str, object]] = []
    for record in records:
        name = _member(record, "name")
        normalized.append(
            {
                "name": {
                    "value": _member(name, "value"),
                    "localizedValue": _member(
                        name,
                        "localized_value",
                        "localizedValue",
                    ),
                },
                "unit": _enum_text(_member(record, "unit", default="Count")),
                "currentValue": _member(record, "current_value", "currentValue"),
                "limit": _member(record, "limit"),
            }
        )
    return {"value": normalized}


def _profile_state_document(values: object) -> dict[str, object]:
    records = _bounded_iterable(values, "workload profile state")
    normalized: list[dict[str, object]] = []
    for record in records:
        properties = _member(record, "properties")
        normalized.append(
            {
                "name": _member(record, "name"),
                "properties": {
                    "currentCount": _member(
                        properties,
                        "current_count",
                        "currentCount",
                    ),
                    "maximumCount": _member(
                        properties,
                        "maximum_count",
                        "maximumCount",
                    ),
                    "minimumCount": _member(
                        properties,
                        "minimum_count",
                        "minimumCount",
                    ),
                },
            }
        )
    return {"value": normalized}


def _container_document(value: object) -> dict[str, object]:
    properties = _member(value, "properties")
    if properties is None:
        properties = value
    configuration = _member(properties, "configuration")
    ingress = _member(configuration, "ingress")
    template = _member(properties, "template")
    containers = _sequence(
        _member(template, "containers", default=()),
        "Container App containers",
    )
    scale = _member(template, "scale")
    rules = _sequence(
        _member(scale, "rules", default=()),
        "Container App scale rules",
    )
    return {
        "id": _member(value, "id"),
        "name": _member(value, "name"),
        "type": _member(value, "type"),
        "location": _member(value, "location"),
        "properties": {
            "provisioningState": _enum_text(
                _member(properties, "provisioning_state", "provisioningState")
            ),
            "latestReadyRevisionName": _member(
                properties,
                "latest_ready_revision_name",
                "latestReadyRevisionName",
            ),
            "workloadProfileName": _member(
                properties,
                "workload_profile_name",
                "workloadProfileName",
            ),
            "managedEnvironmentId": _member(
                properties,
                "environment_id",
                "managedEnvironmentId",
            ),
            "configuration": {
                "ingress": {"fqdn": _member(ingress, "fqdn")},
            },
            "template": {
                "containers": [
                    {
                        "image": _member(container, "image"),
                        "args": list(
                            _sequence(
                                _member(container, "args", default=()),
                                "Container App arguments",
                            )
                        ),
                    }
                    for container in containers
                ],
                "scale": {
                    "minReplicas": _member(scale, "min_replicas", "minReplicas"),
                    "maxReplicas": _member(scale, "max_replicas", "maxReplicas"),
                    "rules": [
                        {
                            "name": _member(rule, "name"),
                            "http": {
                                "metadata": _member(
                                    _member(rule, "http"),
                                    "metadata",
                                    default={},
                                )
                            },
                        }
                        for rule in rules
                    ],
                },
            },
        },
    }


def _revision_document(value: object) -> dict[str, object]:
    properties = _member(value, "properties")
    if properties is None:
        properties = value
    return {
        "name": _member(value, "name"),
        "properties": {
            "active": _member(properties, "active"),
            "replicas": _member(properties, "replicas"),
            "healthState": _enum_text(
                _member(properties, "health_state", "healthState")
            ),
            "provisioningState": _enum_text(
                _member(properties, "provisioning_state", "provisioningState")
            ),
            "runningState": _enum_text(
                _member(properties, "running_state", "runningState")
            ),
        },
    }


def _safe_running_state(value: object, allowed: frozenset[str]) -> str:
    normalized = _enum_text(value)
    return normalized if isinstance(normalized, str) and normalized in allowed else "Unknown"


def _startup_reason_class(value: object) -> str | None:
    """Map provider-controlled replica details to a fixed diagnostic class."""
    if not isinstance(value, str) or not value or len(value) > _MAX_STATUS_DETAIL_CHARS:
        return None
    normalized = value.casefold()
    checks = (
        (("workload profile full", "insufficient gpu", "no nodes available"), "capacity_exhausted"),
        (("imagepullbackoff", "errimagepull", "failed to pull image"), "image_pull_failure"),
        (("crashloopbackoff", "containercrashing"), "container_crash"),
        (("startup probe",), "startup_probe_failure"),
        (("readiness probe",), "readiness_probe_failure"),
        (("out of memory", "oomkilled"), "resource_exhausted"),
        (("pulling image", "container creating"), "image_initializing"),
        (("system identity container",), "identity_initializing"),
    )
    for markers, classification in checks:
        if any(marker in normalized for marker in markers):
            return classification
    return None


def _replica_status_document(value: object) -> dict[str, object]:
    """Aggregate bounded replica health without retaining names or provider text."""
    raw_records = _member(value, "value", default=value)
    records = _bounded_iterable(raw_records, "Container App replica inventory")
    replica_states: set[str] = set()
    container_states: set[str] = set()
    reason_classes: set[str] = set()
    ready_count = 0
    started_count = 0
    restart_count = 0
    container_count = 0
    for record in records:
        properties = _member(record, "properties")
        if properties is None:
            properties = record
        replica_states.add(
            _safe_running_state(
                _member(properties, "running_state", "runningState"),
                _REPLICA_RUNNING_STATES,
            )
        )
        replica_reason = _startup_reason_class(
            _member(properties, "running_state_details", "runningStateDetails")
        )
        if replica_reason is not None:
            reason_classes.add(replica_reason)
        containers = _sequence(
            _member(properties, "containers", default=()),
            "Container App replica containers",
        )
        container_count += len(containers)
        if container_count > _MAX_COLLECTION_ITEMS:
            raise AzureContainerAppsSDKReadError(
                "Azure SDK Container App replica response is incomplete or ambiguous"
            )
        for container in containers:
            if _member(container, "ready") is True:
                ready_count += 1
            if _member(container, "started") is True:
                started_count += 1
            observed_restarts = _member(
                container,
                "restart_count",
                "restartCount",
                default=0,
            )
            if (
                isinstance(observed_restarts, bool)
                or not isinstance(observed_restarts, int)
                or not 0 <= observed_restarts <= 1_000_000
            ):
                raise AzureContainerAppsSDKReadError(
                    "Azure SDK Container App replica response is incomplete or ambiguous"
                )
            restart_count += observed_restarts
            container_states.add(
                _safe_running_state(
                    _member(container, "running_state", "runningState"),
                    _CONTAINER_RUNNING_STATES,
                )
            )
            container_reason = _startup_reason_class(
                _member(container, "running_state_details", "runningStateDetails")
            )
            if container_reason is not None:
                reason_classes.add(container_reason)
    return {
        "replicaCount": len(records),
        "readyContainerCount": ready_count,
        "startedContainerCount": started_count,
        "restartCount": restart_count,
        "replicaRunningStates": sorted(replica_states),
        "containerRunningStates": sorted(container_states),
        "reasonClasses": sorted(reason_classes),
    }


class _SDKOwner:
    def __init__(self, client: _ClosableClient) -> None:
        self.client = client
        self._closed = False
        self._lock = RLock()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.client.close()


class _SDKView:
    def __init__(self, owner: _SDKOwner) -> None:
        self._owner = owner

    @property
    def _client(self) -> Any:
        return self._owner.client

    def close(self) -> None:
        self._owner.close()


class AzureContainerAppsSDKPreflightTransport(_SDKView):
    """Normalize the three exact preflight reads from Microsoft's SDK."""

    def __init__(self, owner: _SDKOwner, policy: AzureContainerAppLiveProofPolicy) -> None:
        super().__init__(owner)
        self._policy = policy
        root = policy.environment_id
        self._operations = {
            f"{root}?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}": self._environment,
            f"{root}/usages?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}": self._usages,
            f"{root}/workloadProfileStates?api-version={ENVIRONMENT_PREFLIGHT_API_VERSION}": (
                self._profile_states
            ),
        }

    def _environment(self) -> dict[str, object]:
        value = _sdk_read(
            lambda: self._client.managed_environments.get(
                self._policy.resource_group,
                self._policy.environment_name,
            )
        )
        return _environment_document(value)

    def _usages(self) -> dict[str, object]:
        value = _sdk_read(
            lambda: self._client.managed_environment_usages.list(
                self._policy.resource_group,
                self._policy.environment_name,
            )
        )
        return _usage_document(value)

    def _profile_states(self) -> dict[str, object]:
        value = _sdk_read(
            lambda: self._client.managed_environments.list_workload_profile_states(
                self._policy.resource_group,
                self._policy.environment_name,
            )
        )
        return _profile_state_document(value)

    def get_json(self, path: str, bearer_token: str) -> object:
        _validated_token(bearer_token)
        operation = self._operations.get(path)
        if operation is None:
            raise ValueError("path must be an approved SDK read")
        return operation()


class AzureContainerAppsSDKAppTransport(_SDKView):
    """Read and normalize one exact Container App through Microsoft's SDK."""

    def __init__(self, owner: _SDKOwner, policy: AzureContainerAppLiveProofPolicy) -> None:
        super().__init__(owner)
        self._policy = policy

    def get_json(self, bearer_token: str) -> object | None:
        _validated_token(bearer_token)
        value = _sdk_read(
            lambda: self._client.container_apps.get(
                self._policy.resource_group,
                self._policy.app_name,
            ),
            absent_on_404=True,
        )
        return None if value is None else _container_document(value)

    def get_active_revision_json(self, bearer_token: str) -> object | None:
        """Read one unambiguous active or sole revision during startup."""
        _validated_token(bearer_token)
        values = _sdk_read(
            lambda: self._client.container_apps_revisions.list_revisions(
                self._policy.resource_group,
                self._policy.app_name,
            )
        )
        records = _bounded_iterable(values, "Container App revision inventory")
        documents = tuple(_revision_document(record) for record in records)
        prefix = f"{self._policy.app_name}--"
        for document in documents:
            name = document.get("name")
            if (
                not isinstance(name, str)
                or not name.startswith(prefix)
                or len(name) > 64
                or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name[len(prefix) :]) is None
            ):
                raise AzureContainerAppsSDKReadError(
                    "Azure SDK revision inventory is incomplete or ambiguous"
                )
        active = tuple(
            document
            for document in documents
            if _member(document.get("properties"), "active") is True
        )
        if len(active) == 1:
            return active[0]
        if not active and len(documents) == 1:
            return documents[0]
        if not documents:
            return None
        raise AzureContainerAppsSDKReadError(
            "Azure SDK revision inventory is incomplete or ambiguous"
        )

    def get_revision_json(
        self,
        bearer_token: str,
        revision_name: str,
    ) -> object | None:
        """Read one exact app-owned revision without exposing provider errors."""
        _validated_token(bearer_token)
        prefix = f"{self._policy.app_name}--"
        if (
            not isinstance(revision_name, str)
            or not revision_name.startswith(prefix)
            or len(revision_name) > 64
            or re.fullmatch(r"[a-z0-9][a-z0-9-]*", revision_name[len(prefix) :])
            is None
        ):
            raise ValueError("revision_name must identify the approved app")
        value = _sdk_read(
            lambda: self._client.container_apps_revisions.get_revision(
                self._policy.resource_group,
                self._policy.app_name,
                revision_name,
            ),
            absent_on_404=True,
        )
        if value is None:
            return None
        document = _revision_document(value)
        if document.get("name") != revision_name:
            raise AzureContainerAppsSDKReadError(
                "Azure SDK revision response is incomplete or ambiguous"
            )
        return document

    def get_replica_status_json(
        self,
        bearer_token: str,
        revision_name: str,
    ) -> object:
        """Read aggregate status for replicas owned by one exact app revision."""
        _validated_token(bearer_token)
        prefix = f"{self._policy.app_name}--"
        if (
            not isinstance(revision_name, str)
            or not revision_name.startswith(prefix)
            or len(revision_name) > 64
            or re.fullmatch(r"[a-z0-9][a-z0-9-]*", revision_name[len(prefix) :])
            is None
        ):
            raise ValueError("revision_name must identify the approved app")
        value = _sdk_read(
            lambda: self._client.container_apps_revision_replicas.list_replicas(
                self._policy.resource_group,
                self._policy.app_name,
                revision_name,
            )
        )
        return _replica_status_document(value)


class AzureContainerAppsSDKLifecycleTransport(_SDKView):
    """Read one environment and bounded same-group app inventory via the SDK."""

    def __init__(self, owner: _SDKOwner, policy: AzureContainerAppLiveProofPolicy) -> None:
        super().__init__(owner)
        self._policy = policy

    def get_environment(self, bearer_token: str) -> object | None:
        _validated_token(bearer_token)
        value = _sdk_read(
            lambda: self._client.managed_environments.get(
                self._policy.resource_group,
                self._policy.environment_name,
            ),
            absent_on_404=True,
        )
        return None if value is None else _environment_document(value)

    def list_environment_app_ids(self, bearer_token: str) -> tuple[str, ...]:
        _validated_token(bearer_token)
        values = _sdk_read(
            lambda: self._client.container_apps.list_by_resource_group(
                self._policy.resource_group
            )
        )
        records = _bounded_iterable(values, "Container App inventory")
        matching: list[str] = []
        seen: set[str] = set()
        prefix = (
            f"/subscriptions/{self._policy.subscription_id}/resourceGroups/"
            f"{self._policy.resource_group}/providers/Microsoft.App/containerApps/"
        )
        for record in records:
            resource_id = _member(record, "id")
            environment_id = _member(
                record,
                "environment_id",
                "managedEnvironmentId",
            )
            if (
                not isinstance(resource_id, str)
                or not resource_id.casefold().startswith(prefix.casefold())
                or resource_id.casefold() in seen
                or not isinstance(environment_id, str)
            ):
                raise AzureContainerAppsSDKReadError(
                    "Azure SDK Container App inventory is incomplete or ambiguous"
                )
            seen.add(resource_id.casefold())
            if environment_id.casefold() == self._policy.environment_id.casefold():
                matching.append(resource_id)
        return tuple(sorted(matching, key=str.casefold))


@dataclass(frozen=True, slots=True)
class AzureContainerAppsSDKReadTransports:
    """Three compatibility views over one exact official SDK client."""

    preflight: AzureContainerAppsSDKPreflightTransport
    lifecycle: AzureContainerAppsSDKLifecycleTransport
    app: AzureContainerAppsSDKAppTransport

    def __init__(
        self,
        *,
        client: _ClosableClient,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> None:
        """Create three bounded read views over one shared SDK client."""
        if not isinstance(policy, AzureContainerAppLiveProofPolicy):
            raise ValueError("policy must be AzureContainerAppLiveProofPolicy")
        if not callable(getattr(client, "close", None)):
            raise ValueError("client must be a closable Container Apps SDK client")
        owner = _SDKOwner(client)
        object.__setattr__(self, "preflight", AzureContainerAppsSDKPreflightTransport(owner, policy))
        object.__setattr__(self, "lifecycle", AzureContainerAppsSDKLifecycleTransport(owner, policy))
        object.__setattr__(self, "app", AzureContainerAppsSDKAppTransport(owner, policy))


def _metric_revision(series: object) -> str | None:
    metadata = _member(series, "metadatavalues", "metadata_values", default=())
    for item in _sequence(metadata, "metric dimensions"):
        name = _member(_member(item, "name"), "value")
        if name == "revisionName":
            value = _member(item, "value")
            return value if isinstance(value, str) else None
    return None


def _positive_metric_values(response: object, revision_name: str) -> tuple[float, ...]:
    metric_values = _member(response, "value")
    if metric_values is None:
        raise AzureGPUUtilizationAttestationError(
            BackendFailure.INVALID_RESPONSE,
            AzureGPUMetricResponseReason.MISSING_METRIC_COLLECTION,
        )
    metrics = _sequence(metric_values, "GPU metric")
    if not metrics:
        return ()
    if len(metrics) != 1:
        raise AzureGPUUtilizationAttestationError(
            BackendFailure.INVALID_RESPONSE,
            AzureGPUMetricResponseReason.AMBIGUOUS_METRIC_COUNT,
        )
    metric = metrics[0]
    if _member(_member(metric, "name"), "value") != _GPU_METRIC_NAME:
        raise AzureGPUUtilizationAttestationError(
            BackendFailure.INVALID_RESPONSE,
            AzureGPUMetricResponseReason.METRIC_NAME_MISMATCH,
        )
    if _enum_text(_member(metric, "unit")) != "Percent":
        raise AzureGPUUtilizationAttestationError(
            BackendFailure.INVALID_RESPONSE,
            AzureGPUMetricResponseReason.METRIC_UNIT_MISMATCH,
        )
    series_values = _sequence(
        _member(metric, "timeseries", "time_series", default=()),
        "GPU metric series",
    )
    samples: list[float] = []
    point_count = 0
    for series in series_values:
        points = _sequence(
            _member(series, "data", default=()),
            "GPU metric points",
            limit=_MAX_METRIC_POINTS,
        )
        if not points:
            continue
        if _metric_revision(series) != revision_name:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INVALID_RESPONSE,
                AzureGPUMetricResponseReason.REVISION_DIMENSION_MISMATCH,
            )
        for point in points:
            point_count += 1
            value = _member(point, "maximum")
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise AzureGPUUtilizationAttestationError(
                    BackendFailure.INVALID_RESPONSE,
                    AzureGPUMetricResponseReason.SAMPLE_VALUE_INVALID,
                )
            if value > 0:
                samples.append(float(value))
    if point_count > _MAX_METRIC_POINTS:
        raise AzureGPUUtilizationAttestationError(
            BackendFailure.INVALID_RESPONSE,
            AzureGPUMetricResponseReason.SAMPLE_COUNT_EXCEEDED,
        )
    return tuple(samples)


class AzureContainerAppGPUUtilizationAttestor:
    """Poll one exact revision until Azure Monitor proves positive GPU use."""

    def __init__(
        self,
        *,
        client: _MonitorClient,
        expected_resource_id: str,
        progress_sink: Callable[[str], None] = lambda _message: None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
        poll_timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 10.0,
        lookback: timedelta = timedelta(minutes=15),
    ) -> None:
        """Initialize an exact-resource, bounded Azure Monitor polling policy."""
        if (
            not isinstance(expected_resource_id, str)
            or _CONTAINER_APP_ID.fullmatch(expected_resource_id) is None
        ):
            raise ValueError("expected_resource_id must identify one exact Container App")
        if not callable(getattr(client, "close", None)) or not callable(
            getattr(getattr(client, "metrics", None), "list", None)
        ):
            raise ValueError("client must expose the Azure Monitor metrics reader")
        if not all(callable(callback) for callback in (progress_sink, now, sleep)):
            raise ValueError("GPU attestation callbacks must be callable")
        if (
            isinstance(poll_timeout_seconds, bool)
            or not isinstance(poll_timeout_seconds, (int, float))
            or not 1 <= poll_timeout_seconds <= 900
            or isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not 1 <= poll_interval_seconds <= 60
            or not isinstance(lookback, timedelta)
            or not timedelta(minutes=1) <= lookback <= timedelta(hours=1)
        ):
            raise ValueError("GPU attestation timing is outside the bounded policy")
        self._client = client
        self._expected_resource_id = expected_resource_id
        self._progress_sink = progress_sink
        self._now = now
        self._sleep = sleep
        self._poll_timeout_seconds = float(poll_timeout_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._lookback = lookback
        self._closed = False
        self._lock = RLock()

    def _timestamp(self) -> datetime:
        try:
            value = self._now()
        except Exception:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            ) from None
        if value.tzinfo is None or value.utcoffset() is None:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            )
        return value.astimezone(UTC)

    def _report_response_rejection(self, reason: str) -> None:
        try:
            self._progress_sink(
                "azure_containerapp_gpu_metric phase=response_rejected state=failed "
                f"reason={reason} secret_output=false"
            )
        except Exception:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            ) from None

    def _wait_for_poll(self, phase: str, *, http_status: int = 0) -> None:
        """Emit one content-free heartbeat and wait one bounded interval."""
        status = f" http_status={http_status}" if http_status else ""
        try:
            self._progress_sink(
                "azure_containerapp_gpu_metric "
                f"phase={phase} state=heartbeat{status} secret_output=false"
            )
        except Exception:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            ) from None
        try:
            self._sleep(self._poll_interval_seconds)
        except Exception:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            ) from None

    def attest(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> AzureGPUUtilizationEvidence:
        """Return positive GPU evidence for the exact deployed revision."""
        if not isinstance(identity, AzureContainerAppCandidateIdentity) or (
            identity.resource_id.casefold() != self._expected_resource_id.casefold()
        ):
            raise ValueError("identity must bind the exact Container App")
        started = self._timestamp()
        deadline = started + timedelta(seconds=self._poll_timeout_seconds)
        while True:
            observed = self._timestamp()
            try:
                response = self._client.metrics.list(
                    resource_uri=self._expected_resource_id,
                    timespan=(
                        f"{(observed - self._lookback).isoformat()}/"
                        f"{observed.isoformat()}"
                    ),
                    interval="PT1M",
                    metricnames=_GPU_METRIC_NAME,
                    aggregation="Maximum",
                    filter="revisionName eq '*'",
                    metricnamespace=_GPU_METRIC_NAMESPACE,
                    validate_dimensions=False,
                )
            except Exception as error:
                http_status = _status_code(error) or 0
                failure = _gpu_monitor_failure(error)
                if http_status == 400:
                    if observed < deadline:
                        self._wait_for_poll("query_pending", http_status=http_status)
                        continue
                    raise AzureGPUUtilizationAttestationError(
                        failure,
                        AzureGPUMetricResponseReason.METRIC_QUERY_REJECTED,
                        http_status=http_status,
                    ) from None
                raise AzureGPUUtilizationAttestationError(
                    failure,
                    http_status=http_status,
                ) from None
            try:
                samples = _positive_metric_values(response, identity.revision_name)
            except AzureGPUUtilizationAttestationError as error:
                if error.reason is not None:
                    self._report_response_rejection(error.reason)
                raise
            except Exception:
                reason = AzureGPUMetricResponseReason.RESPONSE_SHAPE_INVALID
                self._report_response_rejection(reason.value)
                raise AzureGPUUtilizationAttestationError(
                    BackendFailure.INVALID_RESPONSE,
                    reason,
                ) from None
            if samples:
                return AzureGPUUtilizationEvidence(
                    metric_name=_GPU_METRIC_NAME,
                    maximum_percent=max(samples),
                    positive_sample_count=len(samples),
                    revision_name=identity.revision_name,
                )
            if observed >= deadline:
                raise AzureGPUUtilizationAttestationError(
                    BackendFailure.TIMEOUT
                )
            self._wait_for_poll("awaiting_positive_sample")

    def close(self) -> None:
        """Close the shared Monitor client exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._client.close()
        except Exception:
            raise AzureGPUUtilizationAttestationError(
                BackendFailure.INTERNAL
            ) from None


__all__ = (
    "AzureContainerAppGPUUtilizationAttestor",
    "AzureContainerAppsSDKReadError",
    "AzureContainerAppsSDKReadTransports",
    "AzureGPUMetricResponseReason",
    "AzureGPUUtilizationAttestationError",
    "AzureGPUUtilizationEvidence",
    "build_container_apps_sdk_client",
    "build_monitor_sdk_client",
)
