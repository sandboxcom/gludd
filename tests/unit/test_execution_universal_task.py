"""Canonical contract tests for the provider-neutral universal task runtime."""

from __future__ import annotations

from types import SimpleNamespace

from general_ludd.execution.universal_task import (
    AcceleratorPlannerProtocol,
    ModelGatewayProtocol,
    ModelResponseProtocol,
    SchedulerProtocol,
    TaskAdapterProtocol,
    ToolRunnerProtocol,
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


def test_universal_boundaries_support_runtime_dependency_validation() -> None:
    """Injected production dependencies must be checkable before execution."""
    assert isinstance(SimpleNamespace(content="{}", cost_estimate=0.0), ModelResponseProtocol)
    assert isinstance(_Gateway(), ModelGatewayProtocol)
    assert isinstance(_Scheduler(), SchedulerProtocol)
    assert isinstance(_Planner(), AcceleratorPlannerProtocol)
    assert isinstance(_ToolRunner(), ToolRunnerProtocol)
    assert isinstance(_Adapter(), TaskAdapterProtocol)
