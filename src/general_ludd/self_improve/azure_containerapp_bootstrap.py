"""Configuration-driven Azure Container App bootstrap for managed candidates.

The composition is deliberately lazy: parsing, sizing, topology planning, and
state allocation perform no Azure operation.  Credentials and Terraform/ARM
clients are acquired only when the approved mixed-candidate assembly requests
the Azure candidate, and the returned backend owns their complete teardown.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Protocol, cast

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorAuthentication,
    AzureAcceleratorCredentials,
    AzureAcceleratorWorkloadIdentity,
    build_azure_workload_identity,
    load_azure_accelerator_credentials,
)
from general_ludd.azure.resource_group_bootstrap import (
    AzureResourceGroupBootstrapPolicy,
    ensure_azure_resource_group,
)
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)
from general_ludd.infra.azure_containerapp_gpu import (
    A100_PROFILE,
    T4_PROFILE,
    ModelServingRequirement,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    AzureContainerAppOwnedCandidateFactory,
    OwnedCandidateLifecycleTrace,
    owned_candidate_deployment_digest,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    build_azure_containerapp_runtime_resources,
)
from general_ludd.infra.azure_containerapp_topology import (
    AzureFleetConstraints,
    AzureProfileCapacity,
    AzureRunnerDemand,
    AzureRunnerTopologyPlan,
    TopologyTrace,
    plan_azure_runner_topology,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPolicy,
    AzureRetentionPreset,
)
from general_ludd.security.state import project_state
from general_ludd.self_improve.live_candidate_wiring import (
    ContainerAppCandidateBackend,
    ContainerAppCandidateBootstrapFactory,
    LiveCandidateWiringPolicy,
)
from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    ModelCandidateProvider,
)

_PROTOCOL = "gludd-configured-azure-containerapp-bootstrap-v1"
_BASE_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "enabled",
        "acknowledgement",
        "subscription_id",
        "resource_group",
        "environment_name",
        "location",
        "allowed_cidr",
        "container_image",
        "model_name",
        "model_revision",
        "parameter_count",
        "weight_bits",
        "kv_cache_mib",
        "runtime_overhead_mib",
        "peak_concurrency",
        "per_replica_concurrency",
        "t4_max_replicas",
        "a100_max_replicas",
        "t4_hourly_cost_microusd",
        "a100_hourly_cost_microusd",
        "max_hourly_cost_microusd",
        "max_cost_usd",
        "ttl_minutes",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
        "max_cost_microusd",
        "timeout_seconds",
        "estimated_request_cost_microusd",
        "idle_retention",
    }
)
_FILE_AUTH_KEYS = _BASE_CONFIG_KEYS | {"auth_file"}
_WORKLOAD_IDENTITY_KEYS = _BASE_CONFIG_KEYS | {
    "client_id",
    "tenant_id",
    "federated_token_file",
}
_RETENTION_KEYS = frozenset(
    {
        "schema_version",
        "preset",
        "max_idle_hourly_cost_microusd",
        "max_idle_monthly_cost_microusd",
        "max_retention_cost_microusd",
        "max_retention_seconds",
        "max_price_age_seconds",
        "max_latency_age_seconds",
        "max_cost_per_saved_hour_microusd",
        "expected_next_demand_seconds",
    }
)


class _CredentialProvider(Protocol):
    def acquire(self) -> AzureCredentialAcquisition:
        """Acquire one exact subscription credential and its release callback."""
        ...


class _LeaseSource(Protocol):
    def acquire(self) -> AzureAcceleratorCredentialLease: ...

    def release(self, lease: AzureAcceleratorCredentialLease) -> None: ...


class _RuntimeResources(Protocol):
    runtime: object
    environment_runtime: object
    backend_factory: object

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AzureCredentialAcquisition:
    """One credential acquisition whose release action is deliberately opaque."""

    credentials: AzureAcceleratorAuthentication
    release: Callable[[], None] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the credential and opaque release boundary."""
        if not isinstance(
            self.credentials,
            (AzureAcceleratorCredentials, AzureAcceleratorWorkloadIdentity),
        ):
            raise ValueError("credentials must use the accelerator credential contract")
        if not callable(self.release):
            raise ValueError("credential release must be callable")


class FileAzureCredentialProvider:
    """Lazily load one private Azure CLI JSON credential for every session."""

    def __init__(self, path: Path, subscription_id: str) -> None:
        """Initialize a private credential path bound to one subscription."""
        self._path = path
        self._subscription_id = subscription_id

    def acquire(self) -> AzureCredentialAcquisition:
        """Load and validate a fresh credential acquisition."""
        credentials = load_azure_accelerator_credentials(
            self._path,
            expected_subscription_id=self._subscription_id,
        )
        return AzureCredentialAcquisition(credentials, lambda: None)


class WorkloadIdentityAzureCredentialProvider:
    """Lazily bind one private GitHub assertion to an Azure workload identity."""

    def __init__(
        self,
        *,
        client_id: str,
        subscription_id: str,
        tenant_id: str,
        federated_token_file: Path,
    ) -> None:
        """Capture only public identifiers and the private assertion path."""
        self._client_id = client_id
        self._subscription_id = subscription_id
        self._tenant_id = tenant_id
        self._federated_token_file = federated_token_file

    def acquire(self) -> AzureCredentialAcquisition:
        """Validate a fresh assertion immediately before Azure effects."""
        credentials = build_azure_workload_identity(
            client_id=self._client_id,
            subscription_id=self._subscription_id,
            tenant_id=self._tenant_id,
            federated_token_file=self._federated_token_file,
            expected_subscription_id=self._subscription_id,
        )
        return AzureCredentialAcquisition(credentials, lambda: None)


class OpenBaoAzureCredentialProvider:
    """Adapt an operator-configured OpenBao Azure role to the same lifecycle."""

    def __init__(self, source: _LeaseSource) -> None:
        """Initialize an exact dynamic-lease source."""
        if not callable(getattr(source, "acquire", None)) or not callable(
            getattr(source, "release", None)
        ):
            raise ValueError("OpenBao source must support exact acquire and release")
        self._source = source

    def acquire(self) -> AzureCredentialAcquisition:
        """Acquire one lease and bind its exact revocation callback."""
        lease = self._source.acquire()
        return AzureCredentialAcquisition(
            lease.credentials,
            lambda: self._source.release(lease),
        )


@dataclass(frozen=True, slots=True)
class _Settings:
    auth_file: Path | None = field(repr=False)
    client_id: str | None
    tenant_id: str | None
    federated_token_file: Path | None = field(repr=False)
    subscription_id: str
    resource_group: str
    environment_name: str
    location: str
    allowed_cidr: str
    container_image: str
    model_name: str
    model_revision: str
    parameter_count: int
    weight_bits: int
    kv_cache_mib: int
    runtime_overhead_mib: int
    peak_concurrency: int
    per_replica_concurrency: int
    t4_max_replicas: int
    a100_max_replicas: int
    t4_hourly_cost_microusd: int
    a100_hourly_cost_microusd: int
    max_hourly_cost_microusd: int
    max_cost_usd: float
    ttl_minutes: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_cost_microusd: int
    timeout_seconds: float
    estimated_request_cost_microusd: int
    idle_retention_policy: AzureIdleRetentionPolicy
    expected_next_demand_seconds: int | None


def _text(config: Mapping[str, object], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def _integer(config: Mapping[str, object], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a bounded integer")
    return value


def _number(config: Mapping[str, object], name: str) -> float:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a bounded number")
    return float(value)


def _absolute_path(config: Mapping[str, object], name: str) -> Path:
    path = Path(_text(config, name)).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute confined path")
    return path


def _authentication_settings(
    config: Mapping[str, object],
) -> tuple[Path | None, str | None, str | None, Path | None]:
    keys = set(config)
    if keys == _FILE_AUTH_KEYS:
        return _absolute_path(config, "auth_file"), None, None, None
    if keys == _WORKLOAD_IDENTITY_KEYS:
        return (
            None,
            _text(config, "client_id"),
            _text(config, "tenant_id"),
            _absolute_path(config, "federated_token_file"),
        )
    raise ValueError("azure_containerapp must use the exact schema")


def _parse_idle_retention(
    raw: object,
) -> tuple[AzureIdleRetentionPolicy, int | None]:
    if not isinstance(raw, Mapping) or set(raw) != _RETENTION_KEYS:
        raise ValueError("idle retention exact schema is required")
    if raw.get("schema_version") != 1 or isinstance(
        raw.get("schema_version"),
        bool,
    ):
        raise ValueError("idle retention schema_version must equal 1")
    try:
        preset = AzureRetentionPreset(_text(raw, "preset"))
    except ValueError:
        raise ValueError("idle retention preset is invalid") from None
    expected = raw.get("expected_next_demand_seconds")
    if expected is not None and (
        isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected <= 0
    ):
        raise ValueError(
            "expected_next_demand_seconds must be a positive integer or null"
        )
    policy = AzureIdleRetentionPolicy(
        preset=preset,
        max_idle_hourly_cost_microusd=_integer(
            raw,
            "max_idle_hourly_cost_microusd",
        ),
        max_idle_monthly_cost_microusd=_integer(
            raw,
            "max_idle_monthly_cost_microusd",
        ),
        max_retention_cost_microusd=_integer(
            raw,
            "max_retention_cost_microusd",
        ),
        max_retention_seconds=_integer(raw, "max_retention_seconds"),
        max_price_age_seconds=_integer(raw, "max_price_age_seconds"),
        max_latency_age_seconds=_integer(raw, "max_latency_age_seconds"),
        max_cost_per_saved_hour_microusd=_integer(
            raw,
            "max_cost_per_saved_hour_microusd",
        ),
    )
    return policy, expected


def _parse_settings(config: Mapping[str, object]) -> _Settings:
    auth_file, client_id, tenant_id, federated_token_file = (
        _authentication_settings(config)
    )
    if config.get("schema_version") != 1 or isinstance(
        config.get("schema_version"), bool
    ):
        raise ValueError("azure_containerapp schema_version must equal 1")
    if config.get("enabled") is not True:
        raise ValueError("azure_containerapp enabled must be true")
    if config.get("acknowledgement") != LIVE_PROOF_ACKNOWLEDGEMENT:
        raise ValueError("azure_containerapp acknowledgement is invalid")
    retention_policy, expected_next_demand_seconds = _parse_idle_retention(
        config.get("idle_retention")
    )
    return _Settings(
        auth_file=auth_file,
        client_id=client_id,
        tenant_id=tenant_id,
        federated_token_file=federated_token_file,
        subscription_id=_text(config, "subscription_id"),
        resource_group=_text(config, "resource_group"),
        environment_name=_text(config, "environment_name"),
        location=_text(config, "location"),
        allowed_cidr=_text(config, "allowed_cidr"),
        container_image=_text(config, "container_image"),
        model_name=_text(config, "model_name"),
        model_revision=_text(config, "model_revision"),
        parameter_count=_integer(config, "parameter_count"),
        weight_bits=_integer(config, "weight_bits"),
        kv_cache_mib=_integer(config, "kv_cache_mib"),
        runtime_overhead_mib=_integer(config, "runtime_overhead_mib"),
        peak_concurrency=_integer(config, "peak_concurrency"),
        per_replica_concurrency=_integer(config, "per_replica_concurrency"),
        t4_max_replicas=_integer(config, "t4_max_replicas"),
        a100_max_replicas=_integer(config, "a100_max_replicas"),
        t4_hourly_cost_microusd=_integer(config, "t4_hourly_cost_microusd"),
        a100_hourly_cost_microusd=_integer(config, "a100_hourly_cost_microusd"),
        max_hourly_cost_microusd=_integer(config, "max_hourly_cost_microusd"),
        max_cost_usd=_number(config, "max_cost_usd"),
        ttl_minutes=_integer(config, "ttl_minutes"),
        max_input_tokens=_integer(config, "max_input_tokens"),
        max_output_tokens=_integer(config, "max_output_tokens"),
        max_total_tokens=_integer(config, "max_total_tokens"),
        max_cost_microusd=_integer(config, "max_cost_microusd"),
        timeout_seconds=_number(config, "timeout_seconds"),
        estimated_request_cost_microusd=_integer(
            config,
            "estimated_request_cost_microusd",
        ),
        idle_retention_policy=retention_policy,
        expected_next_demand_seconds=expected_next_demand_seconds,
    )


def _owner_digest(repo_root: Path, settings: _Settings) -> str:
    encoded = json.dumps(
        {
            "environment": settings.environment_name,
            "project_identity": hashlib.sha256(
                str(repo_root).encode("utf-8", errors="surrogatepass")
            ).hexdigest(),
            "protocol": _PROTOCOL,
            "resource_group": settings.resource_group,
            "subscription_id": settings.subscription_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _topology(
    settings: _Settings,
    requirement: ModelServingRequirement,
    progress_sink: Callable[[str], None],
) -> AzureRunnerTopologyPlan:
    constraints = AzureFleetConstraints(
        profile_capacities=(
            AzureProfileCapacity(
                T4_PROFILE.workload_profile_type,
                settings.t4_max_replicas,
                settings.t4_hourly_cost_microusd,
            ),
            AzureProfileCapacity(
                A100_PROFILE.workload_profile_type,
                settings.a100_max_replicas,
                settings.a100_hourly_cost_microusd,
            ),
        ),
        max_apps=1,
        max_total_replicas=max(
            settings.t4_max_replicas,
            settings.a100_max_replicas,
        ),
        max_replicas_per_app=max(
            settings.t4_max_replicas,
            settings.a100_max_replicas,
        ),
        max_hourly_cost_microusd=settings.max_hourly_cost_microusd,
        ttl_minutes=settings.ttl_minutes,
    )

    def trace(event: TopologyTrace) -> None:
        progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP "
            f"phase={event.phase} task_count={event.task_count} "
            f"batch_count={event.batch_count} app_count={event.app_count} "
            f"total_max_replicas={event.total_max_replicas} secret_output=false"
        )

    return plan_azure_runner_topology(
        (
            AzureRunnerDemand(
                task_id="self-improve",
                requirement=requirement,
                peak_concurrency=settings.peak_concurrency,
                per_replica_concurrency=settings.per_replica_concurrency,
            ),
        ),
        constraints,
        trace_sink=trace,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _release_once(release: Callable[[], None]) -> Callable[[], None]:
    lock = RLock()
    released = False

    def run() -> None:
        nonlocal released
        with lock:
            if released:
                return
            released = True
        release()

    return run


def _runtime_trace(
    progress_sink: Callable[[str], None],
    component: str,
    event: object,
) -> None:
    phase = getattr(event, "phase", getattr(event, "event", "unknown"))
    state = getattr(event, "state", "observed")
    digest = getattr(
        event,
        "operation_digest",
        getattr(event, "candidate_identity_digest", None),
    )
    elapsed = getattr(event, "elapsed_seconds", 0)
    failure_class = getattr(event, "failure_class", None)
    retention_digest = getattr(event, "retention_plan_digest", None)
    retention_seconds = getattr(event, "retention_seconds", 0)
    retention_hourly_cost = getattr(
        event,
        "retention_hourly_cost_microusd",
        0,
    )
    event_source = getattr(event, "event_source", None)
    allowed_fields = {
        "event_source": frozenset({"opentofu_ui", "azure_resource_manager"}),
        "resource_type": frozenset(
            {
                "azapi_resource",
                "microsoft.app/containerapps",
                "microsoft.app/managedenvironments",
            }
        ),
        "action": frozenset(
            {"noop", "create", "read", "update", "replace", "delete", "move"}
        ),
        "event_kind": frozenset(
            {
                "planned_change",
                "resource_drift",
                "apply_start",
                "apply_progress",
                "apply_complete",
                "apply_errored",
            }
        ),
        "provisioning_state": frozenset(
            {
                "succeeded",
                "failed",
                "canceled",
                "waiting",
                "in-progress",
                "provisioning",
                "deleting",
                "initialization-in-progress",
                "infrastructure-setup-in-progress",
                "infrastructure-setup-complete",
                "scheduled-for-delete",
                "upgrade-requested",
                "upgrade-failed",
            }
        ),
    }
    structured = ""
    if event_source in allowed_fields["event_source"]:
        values = {
            name: getattr(event, name, None)
            for name in (
                "event_source",
                "resource_type",
                "action",
                "event_kind",
                "provisioning_state",
            )
        }
        structured = " " + " ".join(
            f"{name}={value if value in allowed_fields[name] else 'none'}"
            for name, value in values.items()
        )
    progress_sink(
        "SELF_IMPROVE_AZURE_BOOTSTRAP "
        f"component={component} phase={phase} state={state} "
        f"operation_digest={digest or 'unbound'} elapsed_seconds={elapsed} "
        f"failure_class={failure_class or 'none'} "
        f"retention_plan_digest={retention_digest or 'none'} "
        f"retention_seconds={retention_seconds} "
        f"retention_hourly_cost_microusd={retention_hourly_cost}{structured} "
        "secret_output=false"
    )


class ConfiguredAzureContainerAppBootstrapFactory:
    """Create a fresh complete owned lifecycle for every approved trial scope."""

    def __init__(
        self,
        *,
        app_policy: AzureContainerAppLiveProofPolicy,
        environment_policy: AzureEnvironmentLifecyclePolicy,
        requirement: ModelServingRequirement,
        work_root: Path,
        environment_work_root: Path,
        credential_provider: _CredentialProvider,
        resource_group_bootstrapper: Callable[..., object],
        resources_builder: Callable[..., _RuntimeResources],
        owned_factory_type: type[Any],
        progress_sink: Callable[[str], None],
        idle_retention_policy: AzureIdleRetentionPolicy,
        expected_next_demand_seconds: int | None,
    ) -> None:
        """Initialize immutable lifecycle inputs for fresh candidate sessions."""
        self._app_policy = app_policy
        self._environment_policy = environment_policy
        self._requirement = requirement
        self._work_root = work_root
        self._environment_work_root = environment_work_root
        self._credential_provider = credential_provider
        if not callable(resource_group_bootstrapper):
            raise ValueError("resource_group_bootstrapper must be callable")
        self._resource_group_bootstrapper = resource_group_bootstrapper
        self._resources_builder = resources_builder
        self._owned_factory_type = owned_factory_type
        self._progress_sink = progress_sink
        self._idle_retention_policy = idle_retention_policy
        self._expected_next_demand_seconds = expected_next_demand_seconds
        self._deployment_digest = owned_candidate_deployment_digest(
            app_policy,
            environment_policy,
            idle_retention_policy,
            expected_next_demand_seconds,
        )

    @property
    def deployment_digest(self) -> str:
        """Return the immutable app-and-environment desired-state digest."""
        return self._deployment_digest

    def _ensure_resource_group(
        self,
        credentials: AzureAcceleratorAuthentication,
        release: Callable[[], None],
    ) -> None:
        """Acquire the owned resource group or release the credential lease."""
        try:
            self._resource_group_bootstrapper(
                AzureResourceGroupBootstrapPolicy(
                    subscription_id=self._app_policy.subscription_id,
                    resource_group=self._app_policy.resource_group,
                    location=self._app_policy.location,
                    owner_digest=self._environment_policy.owner_digest,
                ),
                credentials,
                trace_sink=lambda event: _runtime_trace(
                    self._progress_sink,
                    "resource_group",
                    event,
                ),
            )
        except BaseException:
            with suppress(Exception):
                release()
            raise RuntimeError(
                "Azure bootstrap resource-group acquisition failed"
            ) from None

    def __call__(self) -> ContainerAppCandidateBackend:
        """Acquire credentials and return one fully owned Azure backend."""
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquire_started "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        acquisition = self._credential_provider.acquire()
        if acquisition.credentials.subscription_id != self._app_policy.subscription_id:
            with suppress(Exception):
                acquisition.release()
            raise RuntimeError("Azure bootstrap credential scope mismatch")
        release = _release_once(acquisition.release)
        self._progress_sink(
            "SELF_IMPROVE_AZURE_BOOTSTRAP phase=credential_acquired "
            f"operation_digest={self._deployment_digest} secret_output=false"
        )
        self._ensure_resource_group(acquisition.credentials, release)
        try:
            resources = self._resources_builder(
                credentials=acquisition.credentials,
                policy=self._app_policy,
                requirement=self._requirement,
                work_root=self._work_root,
                environment_work_root=self._environment_work_root,
                credential_release=release,
                preflight_trace_sink=lambda event: _runtime_trace(
                    self._progress_sink,
                    "preflight",
                    event,
                ),
                terraform_trace_sink=lambda event: _runtime_trace(
                    self._progress_sink,
                    "app_terraform",
                    event,
                ),
                environment_terraform_trace_sink=lambda event: _runtime_trace(
                    self._progress_sink,
                    "environment_terraform",
                    event,
                ),
                backend_trace_sink=lambda event: _runtime_trace(
                    self._progress_sink,
                    "backend",
                    event,
                ),
                progress_sink=lambda message: self._progress_sink(
                    "SELF_IMPROVE_AZURE_BOOTSTRAP component=arm "
                    f"{message} secret_output=false"
                ),
            )
        except BaseException:
            with suppress(Exception):
                release()
            raise RuntimeError("Azure bootstrap resource construction failed") from None

        def owned_trace(event: OwnedCandidateLifecycleTrace) -> None:
            _runtime_trace(self._progress_sink, "owned_lifecycle", event)

        try:
            owner = self._owned_factory_type(
                app_policy=self._app_policy,
                environment_policy=self._environment_policy,
                environment_runtime=resources.environment_runtime,
                app_runtime=resources.runtime,
                backend_factory=resources.backend_factory,
                resource_release=resources.close,
                trace_sink=owned_trace,
                idle_retention_policy=self._idle_retention_policy,
                expected_next_demand_seconds=self._expected_next_demand_seconds,
            )
            if owner.deployment_digest != self._deployment_digest:
                resources.close()
                raise RuntimeError("Azure bootstrap configuration drift")
            return cast(ContainerAppCandidateBackend, owner())
        except RuntimeError as exc:
            if str(exc) == "Azure bootstrap configuration drift":
                raise
            with suppress(Exception):
                resources.close()
            raise RuntimeError("Azure bootstrap candidate acquisition failed") from None
        except BaseException:
            with suppress(Exception):
                resources.close()
            raise RuntimeError("Azure bootstrap candidate acquisition failed") from None


@dataclass(frozen=True, slots=True)
class AzureContainerAppBootstrapWiring:
    """Auditable pure plan plus the lazy factory admitted to candidate wiring."""

    policy: LiveCandidateWiringPolicy
    bootstrap_factory: ContainerAppCandidateBootstrapFactory = field(repr=False)
    requirement: ModelServingRequirement
    topology: AzureRunnerTopologyPlan
    app_policy: AzureContainerAppLiveProofPolicy
    environment_policy: AzureEnvironmentLifecyclePolicy
    idle_retention_policy: AzureIdleRetentionPolicy


def build_azure_containerapp_bootstrap_wiring(
    repo_root: Path,
    self_improve_config: Mapping[str, object],
    *,
    progress_sink: Callable[[str], None],
    credential_provider: _CredentialProvider | None = None,
    resources_builder: Callable[..., Any] = (
        build_azure_containerapp_runtime_resources
    ),
    resource_group_bootstrapper: Callable[..., object] = ensure_azure_resource_group,
    owned_factory_type: type[Any] = AzureContainerAppOwnedCandidateFactory,
    now: Callable[[], datetime] = _utc_now,
) -> AzureContainerAppBootstrapWiring | None:
    """Validate global config and compose a lazy self-owned Azure candidate."""
    if not isinstance(self_improve_config, Mapping):
        raise ValueError("self_improve configuration must be a mapping")
    raw = self_improve_config.get("azure_containerapp")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("azure_containerapp configuration must be a mapping")
    if raw.get("enabled") is False and set(raw) == {"schema_version", "enabled"}:
        if raw.get("schema_version") != 1 or isinstance(
            raw.get("schema_version"), bool
        ):
            raise ValueError("azure_containerapp schema_version must equal 1")
        return None
    if not callable(progress_sink):
        raise ValueError("progress_sink must be callable")
    settings = _parse_settings(raw)
    canonical_root = repo_root.resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    requirement = ModelServingRequirement(
        model_id=settings.model_name,
        revision=settings.model_revision,
        parameter_count=settings.parameter_count,
        weight_bits=settings.weight_bits,
        kv_cache_mib=settings.kv_cache_mib,
        runtime_overhead_mib=settings.runtime_overhead_mib,
    )
    call_budget = BackendCallBudget(
        max_calls=1,
        max_input_tokens=settings.max_input_tokens,
        max_output_tokens=settings.max_output_tokens,
        max_total_tokens=settings.max_total_tokens,
        max_cost_microusd=settings.max_cost_microusd,
        timeout_seconds=settings.timeout_seconds,
    )
    topology = _topology(settings, requirement, progress_sink)
    app_plan = topology.apps[0]
    profile_name = (
        "gpu-t4"
        if app_plan.workload_profile_type == T4_PROFILE.workload_profile_type
        else "gpu-a100"
    )
    timestamp = now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("now must return a timezone-aware datetime")
    timestamp = timestamp.astimezone(UTC)
    expires = (timestamp + timedelta(minutes=settings.ttl_minutes)).replace(
        microsecond=0
    )
    owner_digest = _owner_digest(canonical_root, settings)
    app_suffix = hashlib.sha256(
        f"{owner_digest}:{app_plan.runner_id}".encode("ascii")
    ).hexdigest()[:16]
    app_policy = AzureContainerAppLiveProofPolicy(
        subscription_id=settings.subscription_id,
        resource_group=settings.resource_group,
        environment_name=settings.environment_name,
        workload_profile_name=profile_name,
        workload_profile_type=app_plan.workload_profile_type,
        location=settings.location,
        app_name=f"gludd-vllm-{app_suffix}",
        allowed_cidr=settings.allowed_cidr,
        container_image=settings.container_image,
        model_name=settings.model_name,
        model_revision=settings.model_revision,
        max_cost_usd=settings.max_cost_usd,
        ttl_minutes=settings.ttl_minutes,
        call_budget=call_budget,
        estimated_request_cost_microusd=settings.estimated_request_cost_microusd,
        live=True,
        acknowledgement=LIVE_PROOF_ACKNOWLEDGEMENT,
        min_replicas=app_plan.min_replicas,
        max_replicas=app_plan.max_replicas,
        http_concurrent_requests=app_plan.per_replica_concurrency,
    )
    environment_policy = AzureEnvironmentLifecyclePolicy(
        subscription_id=settings.subscription_id,
        resource_group=settings.resource_group,
        environment_name=settings.environment_name,
        location=settings.location,
        profiles=(
            AzureEnvironmentProfile(profile_name, app_plan.workload_profile_type),
        ),
        owner_digest=owner_digest,
        plan_digest=topology.plan_digest,
        expires_at_utc=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    state = project_state(project_root=canonical_root)
    work_root = state.directory("azure-containerapp", "applications")
    environment_work_root = state.directory("azure-containerapp", "environments")
    provider = credential_provider or _default_credential_provider(settings)
    factory = ConfiguredAzureContainerAppBootstrapFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        requirement=requirement,
        work_root=work_root,
        environment_work_root=environment_work_root,
        credential_provider=provider,
        resource_group_bootstrapper=resource_group_bootstrapper,
        resources_builder=resources_builder,
        owned_factory_type=owned_factory_type,
        progress_sink=progress_sink,
        idle_retention_policy=settings.idle_retention_policy,
        expected_next_demand_seconds=settings.expected_next_demand_seconds,
    )
    policy = LiveCandidateWiringPolicy(
        local_budget=call_budget,
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_CONTAINER_APP,
        ),
        containerapp_bootstrap_digest=factory.deployment_digest,
        containerapp_budget=call_budget,
        containerapp_estimated_cost_microusd=(
            settings.estimated_request_cost_microusd
        ),
    )
    progress_sink(
        "SELF_IMPROVE_AZURE_BOOTSTRAP phase=configured "
        f"operation_digest={factory.deployment_digest} "
        f"max_replicas={app_plan.max_replicas} secret_output=false"
    )
    return AzureContainerAppBootstrapWiring(
        policy=policy,
        bootstrap_factory=factory,
        requirement=requirement,
        topology=topology,
        app_policy=app_policy,
        environment_policy=environment_policy,
        idle_retention_policy=settings.idle_retention_policy,
    )


def _default_credential_provider(settings: _Settings) -> _CredentialProvider:
    if settings.auth_file is not None:
        return FileAzureCredentialProvider(
            settings.auth_file,
            settings.subscription_id,
        )
    if (
        settings.client_id is None
        or settings.tenant_id is None
        or settings.federated_token_file is None
    ):
        raise ValueError("Azure workload identity configuration is incomplete")
    return WorkloadIdentityAzureCredentialProvider(
        client_id=settings.client_id,
        subscription_id=settings.subscription_id,
        tenant_id=settings.tenant_id,
        federated_token_file=settings.federated_token_file,
    )


__all__ = (
    "AzureContainerAppBootstrapWiring",
    "AzureCredentialAcquisition",
    "ConfiguredAzureContainerAppBootstrapFactory",
    "FileAzureCredentialProvider",
    "OpenBaoAzureCredentialProvider",
    "WorkloadIdentityAzureCredentialProvider",
    "build_azure_containerapp_bootstrap_wiring",
)
