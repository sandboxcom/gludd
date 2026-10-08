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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.dispatch.dynamic_dispatcher import (
    DispatchResult,
    ToolCall,
    structured_tool_calls_to_calls,
)
from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import ResourceProfile, Todo, TodoStatus, WorkType

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


def _todo(
    work_type: str | WorkType = WorkType.CODE, *, project_id: str | None = None
) -> Todo:
    return Todo(
        title="generate something",
        todo_id="TODO-GENTOOL",
        status=TodoStatus.ACTIVE,
        queue="core",
        work_type=WorkType(work_type),
        resource_profile=ResourceProfile.LOW_RESOURCE,
        prompt_profile="default",
        project_id=project_id,
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
            await loop._dispatch_execute_job(_todo("code", project_id="proj-a"))

        dispatcher.dispatch_all.assert_awaited_once()
        dispatched = dispatcher.dispatch_all.await_args.args[0]
        assert [c.name for c in dispatched] == ["fs/write_file", "git/commit"]
        assert {c.kind for c in dispatched} == {"mcp"}
        assert dispatched[0].args == {"path": "out.txt", "content": "hello"}

    @pytest.mark.asyncio
    async def test_structured_tool_result_persists_under_todo_project(self) -> None:
        """Successful Phase-1 tool output stays in the validated todo project."""
        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(
                    ok=True,
                    kind="mcp",
                    name="fs/write_file",
                    output="project-a-output",
                )
            ]
        )
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.return_value = {}
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=MagicMock(),
            dispatcher=dispatcher,
            variable_repo=variable_repo,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", _structured_tool_calls()[:1]),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        variable_repo.set_var.assert_awaited_once_with(
            namespace="tool_results",
            key="tool_result:fs/write_file",
            value="project-a-output",
            project_id="proj-a",
        )

    @pytest.mark.asyncio
    async def test_one_failed_project_write_does_not_drop_later_tool_results(
        self,
    ) -> None:
        """Persistence stays best-effort per result without widening its scope."""
        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(
            return_value=[
                DispatchResult(ok=True, kind="mcp", name="first", output="one"),
                DispatchResult(ok=True, kind="mcp", name="second", output="two"),
            ]
        )
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.return_value = {}
        variable_repo.set_var.side_effect = [RuntimeError("write unavailable"), None]
        runner = _make_runner()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
            dispatcher=dispatcher,
            variable_repo=variable_repo,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", _structured_tool_calls()),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        assert variable_repo.set_var.await_count == 2
        assert all(
            call.kwargs["project_id"] == "proj-a"
            for call in variable_repo.set_var.await_args_list
        )
        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_structured_calls_without_dispatcher_remain_non_fatal(self) -> None:
        runner = _make_runner()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", _structured_tool_calls()[:1]),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_excess_structured_calls_are_denied_before_dispatch(self) -> None:
        from general_ludd.dispatch.limits import MAX_CALLS_PER_REQUEST

        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(return_value=[])
        runner = _make_runner()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
            dispatcher=dispatcher,
        )
        excess_calls = _structured_tool_calls()[:1] * (MAX_CALLS_PER_REQUEST + 1)

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", excess_calls),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        dispatcher.dispatch_all.assert_not_awaited()
        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_tool_results_record_only_successful_project_output(
        self,
    ) -> None:
        dispatch_results = [
            DispatchResult(ok=True, kind="mcp", name="first", output="one"),
            DispatchResult(ok=False, kind="mcp", name="second", error="denied"),
        ]
        dispatcher = MagicMock()
        dispatcher.dispatch_all = AsyncMock(return_value=dispatch_results)
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.return_value = {}
        run_recorder = MagicMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=MagicMock(),
            dispatcher=dispatcher,
            variable_repo=variable_repo,
            run_recorder=run_recorder,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", _structured_tool_calls()),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        variable_repo.set_var.assert_awaited_once_with(
            namespace="tool_results",
            key="tool_result:first",
            value="one",
            project_id="proj-a",
        )
        tool_events = [
            call.args[1]
            for call in run_recorder.record.call_args_list
            if call.args[1]["type"] == "tool_calls_dispatched"
        ]
        assert tool_events == [
            {
                "type": "tool_calls_dispatched",
                "timestamp": tool_events[0]["timestamp"],
                "total": 2,
                "ok": 1,
                "error_count": 1,
                "calls": [result.to_dict() for result in dispatch_results],
            }
        ]

    @pytest.mark.asyncio
    async def test_shared_var_and_model_failures_do_not_abort_runner(self) -> None:
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.side_effect = RuntimeError("database unavailable")
        runner = _make_runner()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
            variable_repo=variable_repo,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                side_effect=RuntimeError("model unavailable"),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_prompt_profile_synthesizes_todo_prompt(self) -> None:
        runner = _make_runner()
        invoke = MagicMock(return_value=("generated", None))
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
        )
        todo = _todo(project_id="proj-a").model_copy(update={"prompt_profile": None})

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                invoke,
            ),
        ):
            await loop._dispatch_execute_job(todo)

        assert invoke.call_args.kwargs["prompt_text"] == "Task: generate something"

    @pytest.mark.asyncio
    async def test_project_dispatch_checkpoints_validated_identity_before_model(
        self,
    ) -> None:
        checkpoint_manager = MagicMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=MagicMock(),
            checkpoint_manager=checkpoint_manager,
        )
        todo = _todo(project_id="proj-a").model_copy(update={"version": 7})

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", None),
            ),
        ):
            await loop._dispatch_execute_job(todo)

        checkpoint_manager.checkpoint.assert_called_once()
        snapshot = checkpoint_manager.checkpoint.call_args.args[0]
        assert snapshot.dispatch_state.project_id == "proj-a"
        assert snapshot.dispatch_state.todo_version == 7
        assert snapshot.dispatch_state.resume_shard_id == "proj-a:TODO-GENTOOL"

    @pytest.mark.asyncio
    async def test_unscoped_dispatch_does_not_write_project_checkpoint(self) -> None:
        checkpoint_manager = MagicMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=MagicMock(),
            checkpoint_manager=checkpoint_manager,
        )

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", None),
            ),
        ):
            await loop._dispatch_execute_job(_todo())

        checkpoint_manager.checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_generation_records_routed_deployment_performance(self) -> None:
        gateway = MagicMock()
        gateway.get_profile.return_value = SimpleNamespace(
            provider="provider-a",
            model_name="model-a",
            cost_per_input_token=0.01,
            cost_per_output_token=0.02,
        )
        health_router = MagicMock()
        health_router.check_and_route.return_value = "healthy-fallback"
        health_router.health_checker = MagicMock()
        performance_repo = AsyncMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=gateway,
        )
        loop._deployment_health_router = health_router
        loop._model_perf_repo = performance_repo

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("generated", None),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        health_router.health_checker.record_success.assert_called_once_with(
            "healthy-fallback"
        )
        assert performance_repo.record_call.await_args.kwargs["success"] is True
        assert (
            performance_repo.record_call.await_args.kwargs["model_profile_id"]
            == "healthy-fallback"
        )
        assert performance_repo.record_call.await_args.kwargs["service"] == "provider-a"

    @pytest.mark.asyncio
    async def test_unhealthy_deployment_and_model_failure_are_recorded(self) -> None:
        gateway = MagicMock()
        gateway.get_profile.return_value = None
        health_router = MagicMock()
        health_router.check_and_route.return_value = None
        health_router.health_checker = MagicMock()
        performance_repo = AsyncMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=gateway,
        )
        loop._deployment_health_router = health_router
        loop._model_perf_repo = performance_repo

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                side_effect=RuntimeError("model unavailable"),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        health_router.health_checker.record_failure.assert_called_once_with(
            "default",
            "model unavailable",
            kind="error",
        )
        assert performance_repo.record_call.await_args.kwargs["success"] is False
        assert performance_repo.record_call.await_args.kwargs["service"] == "unknown"

    @pytest.mark.asyncio
    async def test_same_deployment_empty_response_and_perf_failure_are_non_fatal(
        self,
    ) -> None:
        health_router = MagicMock()
        health_router.check_and_route.return_value = "default"
        health_router.health_checker = MagicMock()
        performance_repo = AsyncMock()
        performance_repo.record_call.side_effect = RuntimeError("metrics unavailable")
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=MagicMock(),
        )
        loop._deployment_health_router = health_router
        loop._model_perf_repo = performance_repo

        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", _passthrough_to_thread()),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=(None, None),
            ),
        ):
            await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        health_router.health_checker.record_success.assert_not_called()
        health_router.health_checker.record_failure.assert_not_called()
        performance_repo.record_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_gateway_records_unknown_performance_profile(self) -> None:
        performance_repo = AsyncMock()
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=_make_runner(),
            model_gateway=None,
        )
        loop._model_perf_repo = performance_repo

        await loop._dispatch_execute_job(_todo(project_id="proj-a"))

        assert performance_repo.record_call.await_args.kwargs["service"] == "unknown"
        assert performance_repo.record_call.await_args.kwargs["success"] is False

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
            await loop._dispatch_execute_job(_todo("code", project_id="proj-a"))

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
            await loop._dispatch_execute_job(_todo("code", project_id="proj-a"))

        phase_two.assert_awaited_once()
        variable_repo.set_var.assert_any_await(
            namespace="tool_results",
            key="tool_loop_result:EXEC-TODO-GENTOOL",
            value="tool-refined output",
            project_id="proj-a",
        )
        runner.run_playbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase_two_project_write_failure_is_non_fatal_and_recorded(
        self,
    ) -> None:
        runner = _make_runner()
        variable_repo = AsyncMock()
        variable_repo.load_vars_for_project.return_value = {}
        variable_repo.set_var.side_effect = RuntimeError("write unavailable")
        run_recorder = MagicMock()
        phase_two = AsyncMock(return_value="tool-refined output")
        loop = EventLoop(
            worker_base_url="http://worker:8000",
            config={},
            runner=runner,
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            variable_repo=variable_repo,
            run_recorder=run_recorder,
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
            await loop._dispatch_execute_job(_todo(project_id="proj-b"))

        variable_repo.set_var.assert_awaited_once_with(
            namespace="tool_results",
            key="tool_loop_result:EXEC-TODO-GENTOOL",
            value="tool-refined output",
            project_id="proj-b",
        )
        event_types = [call.args[1]["type"] for call in run_recorder.record.call_args_list]
        assert "tool_loop_completed" in event_types
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
