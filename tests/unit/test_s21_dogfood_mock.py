"""S.21: dogfood mock gateway injection point for EventLoop._dispatch_execute_job.

The seam ``EventLoop._mock_gateway`` allows tests to inject a mock model gateway
without monkeypatching the entire _dispatch_execute_job method. When set (non-None),
it is used in place of self._model_gateway for the generation, billing, and
performance-recording paths inside the dispatch method.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeTodo:
    def __init__(
        self,
        todo_id: str = "todo-s21",
        work_type: str = "code",
        queue: str = "core",
        title: str = "S.21 test task",
        description: str = "verify mock gateway seam",
        prompt_profile: str = "test.j2",
        project_id: str | None = None,
    ) -> None:
        self.todo_id = todo_id
        self.work_type = work_type
        self.queue = queue
        self.title = title
        self.description = description
        self.prompt_profile = prompt_profile
        self.project_id = project_id
        self.priority = "medium"
        self.model_profile = None
        self.resource_profile = "low_resource"
        self.plan_artifact = None
        self.assigned_agent = None
        self.agent_name = None
        self.tags = None
        self.acceptance_criteria = None
        self.definition_of_done = None


class _FakeGatewayResponse:
    content = "mock model response"


class _RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call_model(
        self, profile_id: str, *, messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        self.calls.append({"profile_id": profile_id, "messages": messages})
        return _FakeGatewayResponse()

    def get_profile(self, profile_id: str) -> MagicMock:
        return MagicMock(
            provider="mock_provider", model_name="mock_model",
            cost_per_input_token=0.0, cost_per_output_token=0.0,
        )


class _FakePromptRegistry:
    def render(self, profile: str, **kwargs: Any) -> str:
        return "Test prompt body"


def _make_loop_with_mock_runner(gateway: Any, mock_gateway: Any = None) -> Any:
    from general_ludd.event_loop.loop import EventLoop

    mock_runner = MagicMock()
    mock_runner.prepare_job_dirs.return_value = {"root": "/tmp/test-s21"}
    mock_runner.write_vars.return_value = None
    mock_runner.run_playbook.return_value = None

    loop = EventLoop(
        config={},
        runner=mock_runner,
        model_gateway=gateway,
        prompt_registry=_FakePromptRegistry(),
    )
    loop._mock_gateway = mock_gateway
    return loop


class TestMockGatewaySeam:
    @pytest.mark.asyncio
    async def test_mock_gateway_defaults_to_none(self) -> None:
        loop = _make_loop_with_mock_runner(MagicMock())
        assert loop._mock_gateway is None

    @pytest.mark.asyncio
    async def test_mock_gateway_used_instead_of_real(self) -> None:
        real = _RecordingGateway()
        mock = _RecordingGateway()
        loop = _make_loop_with_mock_runner(gateway=real, mock_gateway=mock)

        await loop._dispatch_execute_job(_FakeTodo())

        assert len(real.calls) == 0, "real gateway should not be called when mock is set"
        assert len(mock.calls) == 1, "mock gateway should be called"
        assert mock.calls[0]["profile_id"] == "default"

    @pytest.mark.asyncio
    async def test_real_gateway_used_when_mock_is_none(self) -> None:
        real = _RecordingGateway()
        loop = _make_loop_with_mock_runner(gateway=real, mock_gateway=None)

        await loop._dispatch_execute_job(_FakeTodo())

        assert len(real.calls) == 1, "real gateway should be called when mock is None"

    @pytest.mark.asyncio
    async def test_mock_gateway_can_be_cleared(self) -> None:
        real = _RecordingGateway()
        mock = _RecordingGateway()
        loop = _make_loop_with_mock_runner(gateway=real, mock_gateway=mock)

        await loop._dispatch_execute_job(_FakeTodo(todo_id="first"))
        assert len(mock.calls) == 1
        assert len(real.calls) == 0

        loop._mock_gateway = None
        await loop._dispatch_execute_job(_FakeTodo(todo_id="second"))
        assert len(real.calls) == 1, "real gateway should be called after mock cleared"

    @pytest.mark.asyncio
    async def test_no_gateway_called_when_both_none(self) -> None:
        loop = _make_loop_with_mock_runner(gateway=None, mock_gateway=None)

        await loop._dispatch_execute_job(_FakeTodo())
