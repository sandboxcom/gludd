"""Compose a lazy, self-owned Azure Container App model candidate.

Parsing and planning remain pure. Credentials and Azure resources are acquired
only when the approved candidate factory is called, and that backend owns the
paired release lifecycle.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from general_ludd.azure.accelerator_credentials import (
    build_azure_workload_identity,
    load_azure_accelerator_credentials,
)
from general_ludd.azure.resource_group_bootstrap import ensure_azure_resource_group
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    AzureContainerAppOwnedCandidateFactory,
)
from general_ludd.infra.azure_containerapp_runtime_resources import (
    build_azure_containerapp_runtime_resources,
)
from general_ludd.infra.azure_containerapp_topology import AzureRunnerTopologyPlan
from general_ludd.infra.azure_idle_retention import AzureIdleRetentionPolicy
from general_ludd.security.state import project_state
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    AzureCredentialAcquisition,
    AzureCredentialProvider,
    OpenBaoAzureCredentialProvider,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    FileAzureCredentialProvider as _FileAzureCredentialProvider,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_credentials import (
    WorkloadIdentityAzureCredentialProvider as _WorkloadIdentityAzureCredentialProvider,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_planning import (
    azure_bootstrap_owner_digest,
    plan_azure_bootstrap_topology,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_runtime import (
    ConfiguredAzureContainerAppBootstrapFactory,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_runtime import (
    runtime_trace as _runtime_trace,
)
from general_ludd.self_improve.azure_containerapp_bootstrap_settings import (
    AzureContainerAppBootstrapSettings,
    parse_azure_containerapp_bootstrap_settings,
)
from general_ludd.self_improve.live_candidate_wiring import (
    ContainerAppCandidateBootstrapFactory,
    LiveCandidateWiringPolicy,
)
from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    ModelCandidateProvider,
)


class FileAzureCredentialProvider(_FileAzureCredentialProvider):
    """Keep the patchable public SDK boundary while delegating lifecycle logic."""

    def __init__(self, path: Path, subscription_id: str) -> None:
        """Bind the current module SDK loader for delayed credential reads."""
        super().__init__(
            path,
            subscription_id,
            loader=load_azure_accelerator_credentials,
        )


class WorkloadIdentityAzureCredentialProvider(
    _WorkloadIdentityAzureCredentialProvider
):
    """Keep the patchable public SDK boundary for workload identity."""

    def __init__(
        self,
        *,
        client_id: str,
        subscription_id: str,
        tenant_id: str,
        federated_token_file: Path,
    ) -> None:
        """Bind the current module SDK builder for delayed assertion reads."""
        super().__init__(
            client_id=client_id,
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            federated_token_file=federated_token_file,
            builder=build_azure_workload_identity,
        )


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


@dataclass(frozen=True, slots=True)
class _BootstrapPlan:
    requirement: ModelServingRequirement
    call_budget: BackendCallBudget
    topology: AzureRunnerTopologyPlan
    app_policy: AzureContainerAppLiveProofPolicy
    environment_policy: AzureEnvironmentLifecyclePolicy
    work_root: Path
    environment_work_root: Path


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _configured_section(
    self_improve_config: Mapping[str, object],
) -> Mapping[str, object] | None:
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
    return raw


def _model_requirement(
    settings: AzureContainerAppBootstrapSettings,
) -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id=settings.model_name,
        revision=settings.model_revision,
        parameter_count=settings.parameter_count,
        weight_bits=settings.weight_bits,
        kv_cache_mib=settings.kv_cache_mib,
        runtime_overhead_mib=settings.runtime_overhead_mib,
    )


def _call_budget(settings: AzureContainerAppBootstrapSettings) -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=1,
        max_input_tokens=settings.max_input_tokens,
        max_output_tokens=settings.max_output_tokens,
        max_total_tokens=settings.max_total_tokens,
        max_cost_microusd=settings.max_cost_microusd,
        timeout_seconds=settings.timeout_seconds,
    )


def _expiry(now: Callable[[], datetime], ttl_minutes: int) -> str:
    timestamp = now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("now must return a timezone-aware datetime")
    expires = timestamp.astimezone(UTC) + timedelta(minutes=ttl_minutes)
    return expires.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_plan(
    canonical_root: Path,
    settings: AzureContainerAppBootstrapSettings,
    progress_sink: Callable[[str], None],
    now: Callable[[], datetime],
) -> _BootstrapPlan:
    requirement = _model_requirement(settings)
    call_budget = _call_budget(settings)
    topology = plan_azure_bootstrap_topology(settings, requirement, progress_sink)
    app_plan = topology.apps[0]
    owner_digest = azure_bootstrap_owner_digest(canonical_root, settings)
    app_suffix = hashlib.sha256(
        f"{owner_digest}:{app_plan.runner_id}".encode("ascii")
    ).hexdigest()[:16]
    app_policy = AzureContainerAppLiveProofPolicy(
        subscription_id=settings.subscription_id,
        resource_group=settings.resource_group,
        environment_name=settings.environment_name,
        workload_profile_name=app_plan.profile_name,
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
            AzureEnvironmentProfile(
                app_plan.profile_name,
                app_plan.workload_profile_type,
            ),
        ),
        owner_digest=owner_digest,
        plan_digest=topology.plan_digest,
        expires_at_utc=_expiry(now, settings.ttl_minutes),
    )
    state = project_state(project_root=canonical_root)
    return _BootstrapPlan(
        requirement=requirement,
        call_budget=call_budget,
        topology=topology,
        app_policy=app_policy,
        environment_policy=environment_policy,
        work_root=state.directory("azure-containerapp", "applications"),
        environment_work_root=state.directory(
            "azure-containerapp",
            "environments",
        ),
    )


def _default_credential_provider(
    settings: AzureContainerAppBootstrapSettings,
) -> AzureCredentialProvider:
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


def _build_factory(
    plan: _BootstrapPlan,
    settings: AzureContainerAppBootstrapSettings,
    credential_provider: AzureCredentialProvider | None,
    resource_group_bootstrapper: Callable[..., object],
    resources_builder: Callable[..., Any],
    owned_factory_type: type[Any],
    progress_sink: Callable[[str], None],
) -> ConfiguredAzureContainerAppBootstrapFactory:
    provider = credential_provider or _default_credential_provider(settings)
    return ConfiguredAzureContainerAppBootstrapFactory(
        app_policy=plan.app_policy,
        environment_policy=plan.environment_policy,
        requirement=plan.requirement,
        work_root=plan.work_root,
        environment_work_root=plan.environment_work_root,
        credential_provider=provider,
        resource_group_bootstrapper=resource_group_bootstrapper,
        resources_builder=resources_builder,
        owned_factory_type=owned_factory_type,
        progress_sink=progress_sink,
        idle_retention_policy=settings.idle_retention_policy,
        expected_next_demand_seconds=settings.expected_next_demand_seconds,
    )


def build_azure_containerapp_bootstrap_wiring(
    repo_root: Path,
    self_improve_config: Mapping[str, object],
    *,
    progress_sink: Callable[[str], None],
    credential_provider: AzureCredentialProvider | None = None,
    resources_builder: Callable[..., Any] = (
        build_azure_containerapp_runtime_resources
    ),
    resource_group_bootstrapper: Callable[..., object] = ensure_azure_resource_group,
    owned_factory_type: type[Any] = AzureContainerAppOwnedCandidateFactory,
    now: Callable[[], datetime] = _utc_now,
) -> AzureContainerAppBootstrapWiring | None:
    """Validate global config and compose a lazy self-owned Azure candidate."""
    raw = _configured_section(self_improve_config)
    if raw is None:
        return None
    if not callable(progress_sink):
        raise ValueError("progress_sink must be callable")
    settings = parse_azure_containerapp_bootstrap_settings(raw)
    canonical_root = repo_root.resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("repo_root must be an existing directory")
    plan = _build_plan(canonical_root, settings, progress_sink, now)
    factory = _build_factory(
        plan,
        settings,
        credential_provider,
        resource_group_bootstrapper,
        resources_builder,
        owned_factory_type,
        progress_sink,
    )
    policy = LiveCandidateWiringPolicy(
        local_budget=plan.call_budget,
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_CONTAINER_APP,
        ),
        containerapp_bootstrap_digest=factory.deployment_digest,
        containerapp_budget=plan.call_budget,
        containerapp_estimated_cost_microusd=(
            settings.estimated_request_cost_microusd
        ),
    )
    progress_sink(
        "SELF_IMPROVE_AZURE_BOOTSTRAP phase=configured "
        f"operation_digest={factory.deployment_digest} "
        f"max_replicas={plan.topology.apps[0].max_replicas} secret_output=false"
    )
    return AzureContainerAppBootstrapWiring(
        policy=policy,
        bootstrap_factory=factory,
        requirement=plan.requirement,
        topology=plan.topology,
        app_policy=plan.app_policy,
        environment_policy=plan.environment_policy,
        idle_retention_policy=settings.idle_retention_policy,
    )


__all__ = (
    "AzureContainerAppBootstrapWiring",
    "AzureCredentialAcquisition",
    "ConfiguredAzureContainerAppBootstrapFactory",
    "FileAzureCredentialProvider",
    "OpenBaoAzureCredentialProvider",
    "WorkloadIdentityAzureCredentialProvider",
    "_runtime_trace",
    "build_azure_containerapp_bootstrap_wiring",
)
