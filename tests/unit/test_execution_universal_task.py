"""Canonical contract tests for the provider-neutral universal task runtime."""

from __future__ import annotations

import importlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from general_ludd.execution.universal_task_types import (
    AcceleratorPlannerProtocol,
    ModelGatewayProtocol,
    ModelResponseProtocol,
    SchedulerProtocol,
    TaskAdapterProtocol,
    TaskStatus,
    ToolRunnerProtocol,
    UniversalTaskRequest,
    UniversalTaskResult,
)


class _Gateway:
    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> SimpleNamespace:
        del profile_id, messages, kwargs
        return SimpleNamespace(content="{}", cost_estimate=0.0)


class _Scheduler:
    def plan(self, items: list[object]) -> list[list[str]]:
        del items
        return []


class _Planner:
    def discover_hardware(self) -> tuple[object, ...]:
        return ()


class _ToolRunner:
    def run(self, tool_name: str, payload: dict[str, object]) -> dict[str, object]:
        del tool_name, payload
        return {}


class _Adapter:
    capability = "test"

    def build_messages(self, request: object, target: object) -> list[dict[str, str]]:
        del request, target
        return []

    def preflight(self, request: object, target: object) -> object:
        del request, target
        return object()

    def parse_candidate(self, content: str) -> object:
        return content

    def assess_candidate(
        self,
        request: object,
        candidate: object,
        tool_runner: object | None,
    ) -> object:
        del request, candidate, tool_runner
        return object()


class _BadCapabilityAdapter(_Adapter):
    capability = " test "


class _SelfImproveOnlyAdapter(_Adapter):
    capability = "self_improve.proposal"


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[UniversalTaskRequest, TaskAdapterProtocol]] = []

    def execute(
        self,
        request: UniversalTaskRequest,
        adapter: TaskAdapterProtocol,
    ) -> UniversalTaskResult:
        self.calls.append((request, adapter))
        return UniversalTaskResult(
            task_id=request.task_id,
            status=TaskStatus.SUCCEEDED,
            route=None,
            candidate={"validated_by": adapter.capability},
        )


def test_universal_boundaries_support_runtime_dependency_validation() -> None:
    """Injected production dependencies must be checkable before execution."""
    assert isinstance(SimpleNamespace(content="{}", cost_estimate=0.0), ModelResponseProtocol)
    assert isinstance(_Gateway(), ModelGatewayProtocol)
    assert isinstance(_Scheduler(), SchedulerProtocol)
    assert isinstance(_Planner(), AcceleratorPlannerProtocol)
    assert isinstance(_ToolRunner(), ToolRunnerProtocol)
    assert isinstance(_Adapter(), TaskAdapterProtocol)


def test_runtime_dispatches_by_capability_and_refuses_unknown_work() -> None:
    """Callers should submit tasks without selecting a domain adapter themselves."""
    runtime_module = importlib.import_module(
        "general_ludd.execution.universal_task_runtime"
    )
    runtime_type = runtime_module.UniversalTaskRuntime
    executor = _Executor()
    adapter = _Adapter()
    runtime = runtime_type(executor=executor, adapters=(adapter,))
    request = UniversalTaskRequest(
        task_id="task-1",
        capability="test",
        instruction="Exercise the registered test capability.",
        budget_usd=0.0,
    )

    accepted = runtime.execute(request)
    refused = runtime.execute(replace(request, capability="unknown"))

    assert accepted.status is TaskStatus.SUCCEEDED
    assert len(executor.calls) == 1
    assert executor.calls[0][0] == request
    assert executor.calls[0][1].capability == adapter.capability
    assert refused.status is TaskStatus.REFUSED
    assert refused.reasons == ("capability_adapter_unavailable",)
    assert runtime.capabilities == ("test",)


def test_runtime_rejects_duplicate_or_non_adapter_registrations() -> None:
    """Registry ambiguity and objects outside the adapter protocol fail closed."""
    runtime_type = importlib.import_module(
        "general_ludd.execution.universal_task_runtime"
    ).UniversalTaskRuntime

    with pytest.raises(ValueError, match="duplicate adapter capability"):
        runtime_type(executor=_Executor(), adapters=(_Adapter(), _Adapter()))
    with pytest.raises(TypeError, match="TaskAdapterProtocol"):
        runtime_type(executor=_Executor(), adapters=(object(),))
    with pytest.raises(TypeError, match="UniversalTaskExecutorProtocol"):
        runtime_type(executor=object(), adapters=(_Adapter(),))
    with pytest.raises(ValueError, match="canonical non-empty text"):
        runtime_type(executor=_Executor(), adapters=(_BadCapabilityAdapter(),))


def test_self_improvement_only_registry_refuses_polymer_and_arduino_exactly() -> None:
    """Missing domain adapters cannot silently redirect work to self-improvement."""
    runtime_type = importlib.import_module(
        "general_ludd.execution.universal_task_runtime"
    ).UniversalTaskRuntime
    executor = _Executor()
    runtime = runtime_type(
        executor=executor,
        adapters=(_SelfImproveOnlyAdapter(),),
    )

    results = tuple(
        runtime.execute(
            UniversalTaskRequest(
                task_id=f"task-{capability}",
                capability=capability,
                instruction="Execute only the requested domain capability.",
                budget_usd=0.0,
            )
        )
        for capability in ("polymer_design", "arduino-cpp")
    )

    assert executor.calls == []
    assert all(result.status is TaskStatus.REFUSED for result in results)
    assert all(
        result.reasons == ("capability_adapter_unavailable",)
        for result in results
    )
    assert all(
        result.evidence == {
            "registered_capabilities": ("self_improve.proposal",)
        }
        for result in results
    )
