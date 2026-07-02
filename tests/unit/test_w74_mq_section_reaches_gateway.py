"""W7.4 end-to-end verification: rendered MQ section reaches gateway.call_model.

This test traces the full path:
    _dispatch_execute_job
        → _append_message_queue_section  (adds MQ section to prompt_text)
        → invoke_model_for_generation    (AgentCapabilities.prepare_messages → gateway.call_model)

We verify that when ``message_queue_prompt=True`` and a generation work_type is
used, the MQ section text (specifically "you are agent 'coder'" and "gludd_facts")
AND the original prompt text both appear in the ``messages`` argument that reaches
``gateway.call_model``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _FakeTodo:
    """Minimal todo object satisfying all attribute accesses in _dispatch_execute_job."""

    def __init__(
        self,
        todo_id: str = "todo-w74",
        work_type: str = "code",
        assigned_agent: str = "coder",
        prompt_profile: str | None = "test_profile.j2",
        queue: str = "core",
        priority: str = "medium",
        title: str = "W7.4 test task",
        description: str = "verify MQ section reaches gateway",
        project_id: str | None = None,
        model_profile: str | None = None,
        resource_profile: str | None = "low_resource",
        plan_artifact: str | None = None,
    ) -> None:
        self.todo_id = todo_id
        self.work_type = work_type
        self.assigned_agent = assigned_agent
        self.prompt_profile = prompt_profile
        self.queue = queue
        self.priority = priority
        self.title = title
        self.description = description
        self.project_id = project_id
        self.model_profile = model_profile
        self.resource_profile = resource_profile
        self.plan_artifact = plan_artifact


class _FakePromptRegistry:
    """Returns a predictable prompt string for any profile."""

    PROMPT_BODY = "ORIGINAL TASK PROMPT: do the work"

    def render(self, profile: str, **kwargs: Any) -> str:
        return self.PROMPT_BODY


class _FakeGatewayResponse:
    content = "Model response content"


class _FakeModelGateway:
    """Records call_model invocations so we can assert on messages."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call_model(
        self, profile_id: str, *, messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        self.calls.append({"profile_id": profile_id, "messages": messages})
        return _FakeGatewayResponse()


def _make_loop_with_runner(gateway: _FakeModelGateway) -> Any:
    """Build a minimal EventLoop wired for the runner path + model gateway."""
    from general_ludd.event_loop.loop import EventLoop

    # Mock runner — needs prepare_job_dirs, write_vars, run_playbook.
    mock_runner = MagicMock()
    mock_runner.prepare_job_dirs.return_value = {"root": "/tmp/test-w74-jobs"}
    mock_runner.write_vars.return_value = None
    mock_runner.run_playbook.return_value = None

    loop = EventLoop(
        config={"message_queue_prompt": True},
        runner=mock_runner,
        model_gateway=gateway,
        prompt_registry=_FakePromptRegistry(),
    )
    return loop


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMQSectionReachesGateway:
    """W7.4: verify the rendered MQ section arrives in gateway.call_model messages."""

    @pytest.mark.asyncio
    async def test_mq_section_present_in_gateway_messages(self) -> None:
        """Full path: _dispatch_execute_job → gateway.call_model contains MQ text."""
        gateway = _FakeModelGateway()
        loop = _make_loop_with_runner(gateway)

        todo = _FakeTodo(assigned_agent="coder", work_type="code")

        # Patch AgentCapabilities.prepare_messages to pass messages through without
        # compaction so we can inspect the exact content reaching gateway.call_model.
        # The real ContextCompactor would strip empty messages; this bypass ensures
        # the user turn content is not accidentally dropped by compaction logic.
        def _passthrough_prepare(self_caps: Any, system_prompt: str, history: list[dict]) -> list[dict]:
            result = []
            if system_prompt and system_prompt.strip():
                result.append({"role": "system", "content": system_prompt})
            result.extend(history)
            return result

        with patch(
            "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
            _passthrough_prepare,
        ):
            await loop._dispatch_execute_job(todo)

        # gateway.call_model must have been called exactly once.
        assert len(gateway.calls) == 1, (
            f"Expected 1 gateway call, got {len(gateway.calls)}"
        )

        call = gateway.calls[0]
        messages = call["messages"]

        # Collect all message content for assertion.
        all_content = "\n".join(m["content"] for m in messages)

        # Original prompt text must be present.
        assert _FakePromptRegistry.PROMPT_BODY in all_content, (
            f"Original prompt not found in gateway messages.\nContent:\n{all_content}"
        )

        # MQ section markers must be present.
        assert "you are agent 'coder'" in all_content, (
            f"MQ role announcement not found in gateway messages.\nContent:\n{all_content}"
        )
        assert "gludd_facts" in all_content, (
            f"gludd_facts tool reference not found in gateway messages.\nContent:\n{all_content}"
        )

    @pytest.mark.asyncio
    async def test_mq_section_absent_when_flag_off(self) -> None:
        """When message_queue_prompt is False, gateway messages have no MQ section."""
        from general_ludd.event_loop.loop import EventLoop

        gateway = _FakeModelGateway()
        mock_runner = MagicMock()
        mock_runner.prepare_job_dirs.return_value = {"root": "/tmp/test-w74-jobs-off"}
        mock_runner.write_vars.return_value = None
        mock_runner.run_playbook.return_value = None

        loop = EventLoop(
            config={},  # flag OFF
            runner=mock_runner,
            model_gateway=gateway,
            prompt_registry=_FakePromptRegistry(),
        )

        todo = _FakeTodo(assigned_agent="coder", work_type="code")

        def _passthrough_prepare(self_caps: Any, system_prompt: str, history: list[dict]) -> list[dict]:
            result = []
            if system_prompt and system_prompt.strip():
                result.append({"role": "system", "content": system_prompt})
            result.extend(history)
            return result

        with patch(
            "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
            _passthrough_prepare,
        ):
            await loop._dispatch_execute_job(todo)

        assert len(gateway.calls) == 1

        call = gateway.calls[0]
        all_content = "\n".join(m["content"] for m in call["messages"])

        # Original prompt must still be there.
        assert _FakePromptRegistry.PROMPT_BODY in all_content

        # MQ section must NOT be present.
        assert "you are agent 'coder'" not in all_content, (
            "MQ section appeared even though message_queue_prompt=False"
        )
        assert "gludd_facts" not in all_content, (
            "gludd_facts appeared even though message_queue_prompt=False"
        )

    @pytest.mark.asyncio
    async def test_user_turn_contains_mq_appended_after_prompt(self) -> None:
        """MQ section is appended AFTER original prompt text in the user turn."""
        gateway = _FakeModelGateway()
        loop = _make_loop_with_runner(gateway)

        todo = _FakeTodo(assigned_agent="coder", work_type="code")

        def _passthrough_prepare(self_caps: Any, system_prompt: str, history: list[dict]) -> list[dict]:
            result = []
            if system_prompt and system_prompt.strip():
                result.append({"role": "system", "content": system_prompt})
            result.extend(history)
            return result

        with patch(
            "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
            _passthrough_prepare,
        ):
            await loop._dispatch_execute_job(todo)

        assert len(gateway.calls) == 1
        messages = gateway.calls[0]["messages"]

        # Find the user turn.
        user_turns = [m for m in messages if m["role"] == "user"]
        assert user_turns, "No user turn found in gateway messages"

        user_content = user_turns[0]["content"]

        # Prompt comes before MQ section.
        prompt_pos = user_content.find(_FakePromptRegistry.PROMPT_BODY)
        mq_pos = user_content.find("you are agent 'coder'")

        assert prompt_pos != -1, "Original prompt not in user turn"
        assert mq_pos != -1, "MQ section not in user turn"
        assert prompt_pos < mq_pos, (
            "MQ section appears BEFORE original prompt — expected it appended AFTER"
        )
