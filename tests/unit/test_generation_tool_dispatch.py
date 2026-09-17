"""Generation-path STRUCTURED tool-call dispatch (flagship gap regression).

Before this fix, ``invoke_model_for_generation`` returned ONLY the model's text
content and discarded ``ModelResponse.tool_calls``.  The daemon (loop.py) and
worker (worker/app.py) generation paths then re-parsed the *text* via
``parse_tool_calls`` — which cannot recover the model's structured
(OpenAI-nested) tool/function calls — so model-driven tool actions
(MCP / git / file writes) were SILENTLY DROPPED on both surfaces.  The gap
existed precisely because no test asserted that structured tool_calls actually
reach the dispatcher.

These tests pin the fix:

  * ``invoke_model_for_generation`` returns a ``(content, tool_calls)`` tuple
    carrying the model's structured ``tool_calls`` through to the callers.
  * ``structured_tool_calls_to_calls`` maps the OpenAI-nested shape to
    ``ToolCall(kind="mcp", name, args)`` (decoding the JSON ``arguments``).
  * The daemon ``_dispatch_execute_job`` path actually calls
    ``dispatcher.dispatch_all`` with those calls (correct name + kind + args)
    when the model returns structured tool_calls.
  * The worker ``/jobs/execute`` path does the same.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    DispatchResult,
    ToolCall,
    structured_tool_calls_to_calls,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import Todo, TodoStatus

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def _structured_tool_calls() -> list[dict[str, Any]]:
    """The OpenAI-nested shape carried on ModelResponse.tool_calls."""
    return [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "fs/write_file",
                "arguments": '{"path": "out.txt", "content": "hello"}',
            },
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {"name": "git/commit", "arguments": {"message": "wip"}},
        },
    ]


def _fake_response(content: str = "generated", tool_calls: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    resp.tool_calls = tool_calls
    return resp


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.prepare_job_dirs.return_value = {
        "root": "/tmp/EXEC-gentool",
        "env": "/tmp/EXEC-gentool/env",
    }
    runner.write_vars.return_value = "/tmp/EXEC-gentool/env/extravars"
    runner.run_playbook.return_value = {"rc": 0, "output": "", "events": []}
    return runner


def _passthrough_to_thread() -> AsyncMock:
    async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return AsyncMock(side_effect=_run)


def _todo(work_type: str = "code") -> Todo:
    return Todo(
        title="generate something",
        todo_id="TODO-GENTOOL",
        status=TodoStatus.ACTIVE,
        queue="core",
        work_type=work_type,
        resource_profile="low_resource",
        prompt_profile="default",
    )


# --------------------------------------------------------------------------- #
# invoke_model_for_generation returns (content, tool_calls)
# --------------------------------------------------------------------------- #


class TestInvokeReturnsTuple:
    def test_returns_content_and_structured_tool_calls(self) -> None:
        from general_ludd.models.job_invocation import invoke_model_for_generation

        tcs = _structured_tool_calls()
        gw = MagicMock()
        gw.call_model.return_value = _fake_response(content="answer", tool_calls=tcs)

        with patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps:
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [
                {"role": "user", "content": "do work"}
            ]
            MockCaps.return_value = mock_caps
            result = invoke_model_for_generation(
                gw,
                job_id="J-tuple",
                work_type="code",
                model_profile="default",
                prompt_text="do work",
                skill_body=None,
            )

        assert isinstance(result, tuple)
        content, tool_calls = result
        assert content == "answer"
        assert tool_calls == tcs

    def test_returns_none_none_when_no_prompt(self) -> None:
        from general_ludd.models.job_invocation import invoke_model_for_generation

        gw = MagicMock()
        result = invoke_model_for_generation(
            gw,
            job_id="J-noprompt",
            work_type="code",
            model_profile="default",
            prompt_text=None,
            skill_body=None,
        )
        assert result == (None, None)
        gw.call_model.assert_not_called()


# --------------------------------------------------------------------------- #
# structured_tool_calls_to_calls — name/kind/args mapping
# --------------------------------------------------------------------------- #


class TestStructuredToCalls:
    def test_maps_to_mcp_kind_and_decodes_json_args(self) -> None:
        calls = structured_tool_calls_to_calls(_structured_tool_calls())
        assert len(calls) == 2
        assert all(isinstance(c, ToolCall) for c in calls)
        # kind is resolved to "mcp" (model-emitted function/tool calls are the
        # MCP/function tools the ToolCallLoop routes to its MCP client).
        assert {c.kind for c in calls} == {"mcp"}
        assert [c.name for c in calls] == ["fs/write_file", "git/commit"]
        # JSON-string arguments are decoded; dict arguments pass through.
        assert calls[0].args == {"path": "out.txt", "content": "hello"}
        assert calls[1].args == {"message": "wip"}

    def test_none_and_empty_yield_empty(self) -> None:
        assert structured_tool_calls_to_calls(None) == []
        assert structured_tool_calls_to_calls([]) == []

    def test_malformed_args_become_empty_dict(self) -> None:
        calls = structured_tool_calls_to_calls(
            [{"function": {"name": "x/y", "arguments": "{not json"}}]
        )
        assert len(calls) == 1
        assert calls[0].kind == "mcp"
        assert calls[0].args == {}

    def test_skips_items_without_name(self) -> None:
        calls = structured_tool_calls_to_calls(
            [{"function": {"arguments": "{}"}}, {"not_a_dict": True}]
        )
        assert calls == []


# --------------------------------------------------------------------------- #
# Daemon path: _dispatch_execute_job dispatches STRUCTURED tool_calls
# --------------------------------------------------------------------------- #


class TestDaemonGenerationDispatchesStructuredCalls:
    @pytest.mark.asyncio
    async def test_structured_tool_calls_reach_dispatch_all(self) -> None:
        """The flagship regression: a model that returns STRUCTURED tool_calls
        must have those calls dispatched on the daemon generation path.

        Previously the path parsed model_response TEXT (which here is plain prose
        carrying NO embedded tool-call JSON) so dispatch_all was NEVER called —
        the model's structured tool_calls were silently discarded.
        """
        runner = _make_runner()
        gateway = MagicMock(name="ModelGateway")

        # A dispatcher mock that records the calls it receives.
        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(ok=True, kind="mcp", name="fs/write_file", output="ok"),
                DispatchResult(ok=True, kind="mcp", name="git/commit", output="ok"),
            ]
        )

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=gateway,
            dispatcher=dispatcher,
        )

        tcs = _structured_tool_calls()

        to_thread = _passthrough_to_thread()
        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", to_thread),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                # Model returns PROSE text (no embedded tool-call JSON) PLUS
                # structured tool_calls — exactly the shape that used to be lost.
                return_value=("Here is the code you asked for.", tcs),
            ),
        ):
            await loop._dispatch_execute_job(_todo("code"))

        dispatcher.dispatch_all.assert_awaited_once()
        dispatched = dispatcher.dispatch_all.await_args.args[0]
        assert [c.name for c in dispatched] == ["fs/write_file", "git/commit"]
        assert {c.kind for c in dispatched} == {"mcp"}
        assert dispatched[0].args == {"path": "out.txt", "content": "hello"}

    @pytest.mark.asyncio
    async def test_no_tool_calls_means_no_dispatch(self) -> None:
        """Prose-only generation (no structured tool_calls) must NOT dispatch."""
        runner = _make_runner()
        gateway = MagicMock(name="ModelGateway")
        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(return_value=[])

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=gateway,
            dispatcher=dispatcher,
        )

        to_thread = _passthrough_to_thread()
        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", to_thread),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("just some generated text", None),
            ),
        ):
            await loop._dispatch_execute_job(_todo("code"))

        dispatcher.dispatch_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tool_requiring_generation_runs_phase_two_and_persists_output(
        self,
    ) -> None:
        runner = _make_runner()
        gateway = MagicMock(name="ModelGateway")
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.return_value = {}
        phase_two = AsyncMock(return_value="tool-refined output")

        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=gateway,
            mcp_client=MagicMock(),
            variable_repo=variable_repo,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("initial analysis", None),
            ),
            patch("general_ludd.execution.tool_loop.ToolCallLoop") as loop_type,
        ):
            loop_type.return_value.run_with_tools = phase_two
            await loop._dispatch_execute_job(_todo("code"))

        phase_two.assert_awaited_once()
        variable_repo.set_var.assert_any_await(
            namespace="tool_results",
            key="tool_loop_result:EXEC-TODO-GENTOOL",
            value="tool-refined output",
        )
        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_langgraph_phase_two_receives_bounded_runtime_context(self) -> None:
        runner = _make_runner()
        gateway = MagicMock(name="ModelGateway")
        detector = MagicMock()
        phase_two = AsyncMock(return_value=None)
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={
                "use_langgraph_tool_loop": True,
                "tool_loop": {"max_total_tokens": 321},
            },
            runner=runner,
            model_gateway=gateway,
            mcp_client=MagicMock(),
            daemon_state={"_adversarial_detector": detector},
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=(None, None),
            ),
            patch(
                "general_ludd.execution.langgraph_agent.LangGraphAgentLoop"
            ) as loop_type,
        ):
            loop_type.return_value.run_with_tools = phase_two
            await loop._dispatch_execute_job(_todo("analysis"))

        assert loop_type.call_args.kwargs["adversarial_detector"] is detector
        assert loop_type.call_args.kwargs["max_total_tokens"] == 321
        phase_two.assert_awaited_once()
        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase_two_failure_is_additive_and_playbook_still_runs(self) -> None:
        runner = _make_runner()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("initial analysis", None),
            ),
            patch("general_ludd.execution.tool_loop.ToolCallLoop") as loop_type,
        ):
            loop_type.return_value.run_with_tools = AsyncMock(
                side_effect=RuntimeError("phase two unavailable")
            )
            await loop._dispatch_execute_job(_todo("code"))

        runner.run_playbook.assert_called_once()


# --------------------------------------------------------------------------- #
# Worker path: /jobs/execute dispatches STRUCTURED tool_calls
# --------------------------------------------------------------------------- #


class TestWorkerGenerationDispatchesStructuredCalls:
    def test_worker_execute_dispatches_structured_calls(self) -> None:
        from fastapi.testclient import TestClient

        from general_ludd.worker.app import create_app

        tcs = _structured_tool_calls()

        # A gateway whose call_model returns content + structured tool_calls.
        gateway = MagicMock()
        gateway.call_model.return_value = _fake_response(
            content="generated", tool_calls=tcs
        )

        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(ok=True, kind="mcp", name="fs/write_file", output="ok"),
                DispatchResult(ok=True, kind="mcp", name="git/commit", output="ok"),
            ]
        )

        runner = MagicMock()
        runner.list_playbooks.return_value = ["test_playbook"]
        runner.prepare_job_dirs.return_value = {"root": "/tmp/wjob", "env": "/tmp/wjob/env"}
        runner.write_vars.return_value = None
        runner.run_playbook.return_value = {"rc": 0, "output": "done", "events": []}

        with (
            patch.dict(os.environ, {"GLUDD_PSK_DISABLE": "1"}),
            patch("general_ludd.worker.app.get_runner", return_value=runner),
            patch("general_ludd.agents.capabilities.AgentCapabilities") as MockCaps,
            TestClient(create_app(gateway=gateway, dispatcher=dispatcher)) as client,
        ):
            mock_caps = MagicMock()
            mock_caps.prepare_messages.return_value = [
                {"role": "user", "content": "write a function"}
            ]
            MockCaps.return_value = mock_caps
            resp = client.post(
                "/jobs/execute",
                json={
                    "job_id": "JOB-WTOOL-1",
                    "playbook": "test_playbook",
                    "queue": "core",
                    "work_type": "code",
                    "prompt_text": "write a function",
                },
            )

        assert resp.status_code == 200, resp.text
        dispatcher.dispatch_all.assert_awaited_once()
        dispatched = dispatcher.dispatch_all.await_args.args[0]
        assert [c.name for c in dispatched] == ["fs/write_file", "git/commit"]
        assert {c.kind for c in dispatched} == {"mcp"}
        # The dispatch results are surfaced in the response.
        body = resp.json()
        assert any(
            "fs/write_file" in str(r) for r in body.get("tool_dispatch_results", [])
        ), body


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(asyncio.sleep(0))
