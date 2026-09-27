"""Capability registry for the provider-neutral universal task pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from general_ludd.execution.universal_task_types import (
    TaskAdapterProtocol,
    TaskStatus,
    UniversalTaskRequest,
    UniversalTaskResult,
)


@runtime_checkable
class UniversalTaskExecutorProtocol(Protocol):
    """Structural executor seam used by the capability registry."""

    def execute(
        self,
        request: UniversalTaskRequest,
        adapter: TaskAdapterProtocol,
    ) -> UniversalTaskResult:
        """Execute one request with its registered capability adapter."""
        ...


class UniversalTaskRuntime:
    """Dispatch requests by capability without domain-specific caller branches."""

    def __init__(
        self,
        *,
        executor: UniversalTaskExecutorProtocol,
        adapters: Sequence[TaskAdapterProtocol],
    ) -> None:
        """Bind one executor to an immutable, unambiguous adapter registry."""
        if not isinstance(executor, UniversalTaskExecutorProtocol):
            raise TypeError("executor must implement UniversalTaskExecutorProtocol")
        registry: dict[str, TaskAdapterProtocol] = {}
        for adapter in adapters:
            if not isinstance(adapter, TaskAdapterProtocol):
                raise TypeError("adapters must implement TaskAdapterProtocol")
            capability = adapter.capability
            if (
                not isinstance(capability, str)
                or not capability
                or capability != capability.strip()
            ):
                raise ValueError("adapter capability must be canonical non-empty text")
            if capability in registry:
                raise ValueError(f"duplicate adapter capability: {capability}")
            registry[capability] = adapter
        self._executor = executor
        self._adapters = MappingProxyType(registry)

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Return the registered capability identities in stable order."""
        return tuple(sorted(self._adapters))

    def execute(self, request: UniversalTaskRequest) -> UniversalTaskResult:
        """Dispatch one request or refuse when no capability adapter is registered."""
        adapter = self._adapters.get(request.capability)
        if adapter is None:
            return UniversalTaskResult(
                task_id=request.task_id,
                status=TaskStatus.REFUSED,
                route=None,
                reasons=("capability_adapter_unavailable",),
                evidence={"registered_capabilities": self.capabilities},
            )
        return self._executor.execute(request, adapter)


__all__ = ["UniversalTaskExecutorProtocol", "UniversalTaskRuntime"]
