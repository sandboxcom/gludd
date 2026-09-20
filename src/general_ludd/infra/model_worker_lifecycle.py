"""Provider-neutral ownership lifecycle for attested model-worker pools.

Infrastructure adapters create and remove hosts. Configuration adapters attest
and launch immutable runner generations. Dispatch adapters publish and drain
ready endpoints. This module owns ordering and compensation across providers.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from general_ludd.infra.model_worker_lifecycle_contracts import (
    ModelWorkerConfigurationRuntime,
    ModelWorkerDispatchRuntime,
    ModelWorkerEndpoint,
    ModelWorkerHost,
    ModelWorkerInfrastructureRuntime,
    ModelWorkerLifecycleError,
    ModelWorkerLifecycleEvent,
    ModelWorkerLifecyclePolicy,
    ModelWorkerLifecycleTrace,
    ProvisionedModelWorkerPool,
    _text,
)


class OwnedModelWorkerPool:
    """Active dispatch lease whose close owns drain and full cleanup."""

    def __init__(
        self,
        owner: ModelWorkerLifecycleManager,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
        dispatch_lease: str,
        operation_digest: str,
    ) -> None:
        """Retain the exact acquired owners for one idempotent close."""
        self._owner = owner
        self._deployment = deployment
        self._endpoints = endpoints
        self._dispatch_lease = dispatch_lease
        self._operation_digest = operation_digest
        self._active = True
        self._lock = RLock()

    @property
    def endpoints(self) -> tuple[ModelWorkerEndpoint, ...]:
        """Return the immutable endpoints admitted for work dispatch."""
        return self._endpoints

    @property
    def active(self) -> bool:
        """Return whether this pool still owns a published dispatch lease."""
        with self._lock:
            return self._active

    def close(self) -> None:
        """Drain work, retire services, destroy infrastructure, and prove absence."""
        with self._lock:
            if not self._active:
                return
            if not self._owner._release(
                self._deployment,
                self._endpoints,
                self._dispatch_lease,
                self._operation_digest,
            ):
                raise ModelWorkerLifecycleError("cleanup", cleanup_failed=True)
            self._active = False


class ModelWorkerLifecycleManager:
    """Own acquisition and compensation across provider-neutral adapters."""

    def __init__(
        self,
        *,
        infrastructure: ModelWorkerInfrastructureRuntime,
        configuration: ModelWorkerConfigurationRuntime,
        dispatcher: ModelWorkerDispatchRuntime,
        trace_sink: Callable[[ModelWorkerLifecycleTrace], None],
    ) -> None:
        """Bind the three lifecycle owners and one required observable trace."""
        for dependency, methods, name in (
            (infrastructure, ("provision", "destroy", "exists"), "infrastructure"),
            (configuration, ("configure", "retire"), "configuration"),
            (dispatcher, ("publish", "withdraw"), "dispatcher"),
        ):
            if any(not callable(getattr(dependency, method, None)) for method in methods):
                raise ValueError(f"{name} has an invalid lifecycle boundary")
        if not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        self._infrastructure = infrastructure
        self._configuration = configuration
        self._dispatcher = dispatcher
        self._trace_sink = trace_sink

    def _emit(
        self,
        event: ModelWorkerLifecycleEvent,
        operation_digest: str,
        worker_count: int,
        reason_code: str | None = None,
    ) -> None:
        self._trace_sink(
            ModelWorkerLifecycleTrace(
                event=event,
                operation_digest=operation_digest,
                worker_count=worker_count,
                reason_code=reason_code,
            )
        )

    def _cleanup_emit(
        self,
        event: ModelWorkerLifecycleEvent,
        operation_digest: str,
        worker_count: int,
    ) -> bool:
        try:
            self._emit(event, operation_digest, worker_count)
        except Exception:
            return False
        return True

    @staticmethod
    def _validated_endpoints(
        deployment: ProvisionedModelWorkerPool,
        endpoints: object,
    ) -> tuple[ModelWorkerEndpoint, ...]:
        if (
            not isinstance(endpoints, tuple)
            or not endpoints
            or any(not isinstance(endpoint, ModelWorkerEndpoint) for endpoint in endpoints)
        ):
            raise ValueError("configuration must return model worker endpoints")
        host_ids = {host.host_id for host in deployment.hosts}
        endpoint_ids = tuple(endpoint.host_id for endpoint in endpoints)
        if set(endpoint_ids) != host_ids or len(set(endpoint_ids)) != len(endpoint_ids):
            raise ValueError("configured endpoints must cover each provisioned host once")
        expected_urls = {host.host_id: host.endpoint_url for host in deployment.hosts}
        if any(
            endpoint.endpoint_url != expected_urls[endpoint.host_id]
            for endpoint in endpoints
        ):
            raise ValueError("configured endpoints differ from provisioned endpoints")
        return endpoints

    def acquire(self, policy: ModelWorkerLifecyclePolicy) -> OwnedModelWorkerPool:
        """Provision, attest, configure, and publish one replacement pool."""
        if not isinstance(policy, ModelWorkerLifecyclePolicy):
            raise ValueError("policy must be ModelWorkerLifecyclePolicy")
        operation_digest = policy.operation_digest
        deployment: ProvisionedModelWorkerPool | None = None
        endpoints: tuple[ModelWorkerEndpoint, ...] = ()
        dispatch_lease: str | None = None
        phase = "provision"
        try:
            self._emit(
                ModelWorkerLifecycleEvent.PROVISION_STARTED,
                operation_digest,
                0,
            )
            provisioned = self._infrastructure.provision(policy)
            if not isinstance(provisioned, ProvisionedModelWorkerPool):
                raise ValueError("infrastructure returned an invalid deployment")
            deployment = provisioned
            count = len(deployment.hosts)
            self._emit(
                ModelWorkerLifecycleEvent.PROVISIONED,
                operation_digest,
                count,
            )
            phase = "configure"
            self._emit(
                ModelWorkerLifecycleEvent.CONFIGURE_STARTED,
                operation_digest,
                count,
            )
            endpoints = self._validated_endpoints(
                deployment,
                self._configuration.configure(deployment, policy),
            )
            self._emit(ModelWorkerLifecycleEvent.READY, operation_digest, count)
            phase = "publish"
            self._emit(
                ModelWorkerLifecycleEvent.PUBLISH_STARTED,
                operation_digest,
                count,
            )
            published = self._dispatcher.publish(deployment, endpoints)
            dispatch_lease = _text(published, "dispatch_lease")
            self._emit(
                ModelWorkerLifecycleEvent.PUBLISHED,
                operation_digest,
                count,
            )
            return OwnedModelWorkerPool(
                self,
                deployment,
                endpoints,
                dispatch_lease,
                operation_digest,
            )
        except BaseException:
            cleanup_failed = False
            if deployment is not None:
                cleanup_failed = not self._release(
                    deployment,
                    endpoints,
                    dispatch_lease,
                    operation_digest,
                )
            self._cleanup_emit(
                ModelWorkerLifecycleEvent.FAILED,
                operation_digest,
                0 if deployment is None else len(deployment.hosts),
            )
            raise ModelWorkerLifecycleError(
                phase,
                cleanup_failed=cleanup_failed,
            ) from None

    def _release(
        self,
        deployment: ProvisionedModelWorkerPool,
        endpoints: tuple[ModelWorkerEndpoint, ...],
        dispatch_lease: str | None,
        operation_digest: str,
    ) -> bool:
        """Release owners in reverse order once dispatch is safely drained."""
        clean = True
        count = len(deployment.hosts)
        if dispatch_lease is not None:
            clean = self._cleanup_emit(
                ModelWorkerLifecycleEvent.DRAIN_STARTED,
                operation_digest,
                count,
            ) and clean
            try:
                self._dispatcher.withdraw(dispatch_lease)
            except Exception:
                # A failed drain can mean requests still hold the generation.
                # Preserve its service and infrastructure so a later close can
                # retry without terminating in-flight model work.
                return False
            else:
                clean = self._cleanup_emit(
                    ModelWorkerLifecycleEvent.DRAINED,
                    operation_digest,
                    count,
                ) and clean
        if endpoints:
            clean = self._cleanup_emit(
                ModelWorkerLifecycleEvent.RETIRE_STARTED,
                operation_digest,
                count,
            ) and clean
            try:
                self._configuration.retire(deployment, endpoints)
            except Exception:
                clean = False
            else:
                clean = self._cleanup_emit(
                    ModelWorkerLifecycleEvent.RETIRED,
                    operation_digest,
                    count,
                ) and clean
        clean = self._cleanup_emit(
            ModelWorkerLifecycleEvent.DESTROY_STARTED,
            operation_digest,
            count,
        ) and clean
        try:
            self._infrastructure.destroy(deployment)
        except Exception:
            clean = False
        else:
            clean = self._cleanup_emit(
                ModelWorkerLifecycleEvent.DESTROYED,
                operation_digest,
                count,
            ) and clean
        try:
            remains = self._infrastructure.exists(deployment)
        except Exception:
            return False
        if remains:
            return False
        return self._cleanup_emit(
            ModelWorkerLifecycleEvent.ABSENCE_VERIFIED,
            operation_digest,
            count,
        ) and clean


__all__ = (
    "ModelWorkerConfigurationRuntime",
    "ModelWorkerDispatchRuntime",
    "ModelWorkerEndpoint",
    "ModelWorkerHost",
    "ModelWorkerInfrastructureRuntime",
    "ModelWorkerLifecycleError",
    "ModelWorkerLifecycleEvent",
    "ModelWorkerLifecycleManager",
    "ModelWorkerLifecyclePolicy",
    "ModelWorkerLifecycleTrace",
    "OwnedModelWorkerPool",
    "ProvisionedModelWorkerPool",
)
