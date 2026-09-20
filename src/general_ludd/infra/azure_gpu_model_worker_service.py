"""Compose Azure GPU ownership, guest configuration, and universal dispatch."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.infra.ansible_model_worker_runtime import (
    AnsibleModelWorkerConfigurationRuntime,
)
from general_ludd.infra.azure_gpu_worker_materializer import (
    AzureGpuWorkerProvisioningSpec,
)
from general_ludd.infra.azure_gpu_worker_runtime import (
    AzureGpuWorkerInfrastructureRuntime,
    AzureGpuWorkerRuntimeTrace,
)
from general_ludd.infra.model_worker_gateway_dispatch import (
    EndpointGatewayFactory,
    ModelWorkerGatewayDispatcher,
)
from general_ludd.infra.model_worker_lifecycle import (
    ModelWorkerConfigurationRuntime,
    ModelWorkerInfrastructureRuntime,
    ModelWorkerLifecycleManager,
    ModelWorkerLifecyclePolicy,
    ModelWorkerLifecycleTrace,
    OwnedModelWorkerPool,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse


class _CredentialSource(Protocol):
    def acquire(self) -> AzureAcceleratorCredentialLease: ...

    def release(self, lease: AzureAcceleratorCredentialLease) -> None: ...


class _PlaybookRunner(Protocol):
    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]: ...


def _discard_lifecycle_trace(_trace: ModelWorkerLifecycleTrace) -> None:
    return None


def _discard_infrastructure_trace(_trace: AzureGpuWorkerRuntimeTrace) -> None:
    return None


class AzureGpuModelWorkerService:
    """One stable model profile backed by owned Azure VM or VMSS generations."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        manager: ModelWorkerLifecycleManager,
        dispatcher: ModelWorkerGatewayDispatcher,
        profile_id: str,
        model_name: str,
    ) -> None:
        """Retain an already validated lifecycle composition and identity."""
        self._gateway = gateway
        self._manager = manager
        self._dispatcher = dispatcher
        self._profile_id = profile_id
        self._model_name = model_name

    @classmethod
    def compose(
        cls,
        *,
        gateway: ModelGateway,
        infrastructure: ModelWorkerInfrastructureRuntime,
        configuration: ModelWorkerConfigurationRuntime,
        profile_id: str,
        model_name: str,
        endpoint_gateway_factory: EndpointGatewayFactory | None = None,
        drain_timeout_seconds: float = 120.0,
        profile_options: Mapping[str, object] | None = None,
        lifecycle_trace_sink: Callable[[ModelWorkerLifecycleTrace], None] = (
            _discard_lifecycle_trace
        ),
    ) -> AzureGpuModelWorkerService:
        """Compose prebuilt provider adapters through the universal gateway."""
        if not isinstance(gateway, ModelGateway):
            raise ValueError("gateway must be ModelGateway")
        dispatcher = ModelWorkerGatewayDispatcher(
            gateway,
            profile_id=profile_id,
            model_name=model_name,
            endpoint_gateway_factory=endpoint_gateway_factory,
            drain_timeout_seconds=drain_timeout_seconds,
            profile_options=profile_options,
        )
        manager = ModelWorkerLifecycleManager(
            infrastructure=infrastructure,
            configuration=configuration,
            dispatcher=dispatcher,
            trace_sink=lifecycle_trace_sink,
        )
        return cls(
            gateway=gateway,
            manager=manager,
            dispatcher=dispatcher,
            profile_id=profile_id,
            model_name=model_name,
        )

    @property
    def gateway(self) -> ModelGateway:
        """Return the shared provider-neutral gateway carrying this profile."""
        return self._gateway

    @property
    def profile_id(self) -> str:
        """Return the stable logical profile selected by universal task routing."""
        return self._profile_id

    def acquire(self, policy: ModelWorkerLifecyclePolicy) -> OwnedModelWorkerPool:
        """Acquire, attest, and publish one replacement model-worker generation."""
        if (
            not isinstance(policy, ModelWorkerLifecyclePolicy)
            or policy.launch_plan.model_id != self._model_name
        ):
            raise ValueError("policy does not match the service model identity")
        return self._manager.acquire(policy)

    def _require_profile(self, profile_id: str) -> None:
        if profile_id != self._profile_id:
            raise ValueError("profile_id does not identify this owned service")

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Serve a buffered universal task through the active generation."""
        self._require_profile(profile_id)
        return self._gateway.call_model(profile_id, messages, **kwargs)

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]:
        """Serve a stream while the dispatcher retains its generation lease."""
        self._require_profile(profile_id)
        yield from self._gateway.call_model_stream(profile_id, messages, **kwargs)


def build_azure_gpu_model_worker_service(
    *,
    gateway: ModelGateway,
    spec: AzureGpuWorkerProvisioningSpec,
    work_root: str | os.PathLike[str],
    credential_source: _CredentialSource,
    ansible_runner: _PlaybookRunner,
    profile_id: str,
    model_name: str,
    endpoint_gateway_factory: EndpointGatewayFactory | None = None,
    drain_timeout_seconds: float = 120.0,
    retire_timeout_seconds: int = 300,
    profile_options: Mapping[str, object] | None = None,
    lifecycle_trace_sink: Callable[[ModelWorkerLifecycleTrace], None] = (
        _discard_lifecycle_trace
    ),
    infrastructure_trace_sink: Callable[[AzureGpuWorkerRuntimeTrace], None] = (
        _discard_infrastructure_trace
    ),
) -> AzureGpuModelWorkerService:
    """Build the production OpenTofu, SDK, Ansible, and dispatch composition."""
    infrastructure = AzureGpuWorkerInfrastructureRuntime(
        spec=spec,
        work_root=work_root,
        credential_source=credential_source,
        trace_sink=infrastructure_trace_sink,
    )
    configuration = AnsibleModelWorkerConfigurationRuntime(
        ansible_runner,
        retire_timeout_seconds=retire_timeout_seconds,
    )
    return AzureGpuModelWorkerService.compose(
        gateway=gateway,
        infrastructure=infrastructure,
        configuration=configuration,
        profile_id=profile_id,
        model_name=model_name,
        endpoint_gateway_factory=endpoint_gateway_factory,
        drain_timeout_seconds=drain_timeout_seconds,
        profile_options=profile_options,
        lifecycle_trace_sink=lifecycle_trace_sink,
    )


__all__ = (
    "AzureGpuModelWorkerService",
    "build_azure_gpu_model_worker_service",
)
