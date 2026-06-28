"""C1 (W3.1) in-process runner branch: model gateway invocation parity.

The daemon's in-process Ansible runner path
(``event_loop/loop.py:_dispatch_execute_job`` runner branch) must invoke the
model the SAME way the worker HTTP path does for a generation work type, then
feed the generated text into the playbook ``job_vars`` (``model_response``)
before running the playbook. Both surfaces share
``models/job_invocation.py`` so they cannot drift.

These tests pin the in-process branch:
  * a gateway-backed loop calls the shared ``invoke_model_for_generation``
    helper for a generation work type and threads ``model_response`` into the
    runner's ``write_vars`` job_vars;
  * a NON-generation work type never calls the model (no gateway invocation);
  * a loop with NO gateway never calls the model and still runs the playbook.

The runner branch offloads BOTH the model call and ``run_playbook`` through
``general_ludd.event_loop.loop.asyncio.to_thread``. We patch ``to_thread`` with
a side effect that synchronously invokes its target (calling the real model
helper / playbook through the patched seam) so the offload contract and the
threaded ``model_response`` are both observable without spinning real threads.
``invoke_model_for_generation`` is patched in the loop's OWN namespace, where it
was imported, so the loop's bound reference is the mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_runner() -> MagicMock:
    """A runner stub whose write_vars/run_playbook are observable.

    prepare_job_dirs returns a private_data_dir root so the in-process branch
    has somewhere to write vars; run_playbook is sync (it is offloaded via
    asyncio.to_thread by the loop) and returns a benign result.
    """
    runner = MagicMock()
    runner.prepare_job_dirs.return_value = {"root": "/tmp/EXEC-job", "env": "/tmp/EXEC-job/env"}
    runner.write_vars.return_value = "/tmp/EXEC-job/env/extravars"
    runner.run_playbook.return_value = {"rc": 0, "output": "", "events": []}
    return runner


def _make_loop(*, gateway: Any | None, runner: MagicMock) -> EventLoop:
    """Build a runner-backed loop with no DB/session so only the runner
    branch of _dispatch_execute_job is exercised."""
    return EventLoop(
        worker_base_url="http://worker:8000",
        config={},
        runner=runner,
        model_gateway=gateway,
    )


def _todo(work_type: str) -> Todo:
    return Todo(
        title="generate something",
        todo_id="TODO-GEN",
        status=TodoStatus.ACTIVE,
        queue="core",
        work_type=work_type,
        resource_profile="low_resource",
        prompt_profile="default",
    )


def _passthrough_to_thread() -> AsyncMock:
    """An async ``to_thread`` stub that synchronously runs its target.

    The runner branch offloads both the model call and ``run_playbook`` through
    ``asyncio.to_thread(fn, *args, **kwargs)``. Calling the target lets the real
    (patched) ``invoke_model_for_generation`` mock register its call and return
    the threaded ``model_response``.
    """

    async def _run(fn: Any, *args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return AsyncMock(side_effect=_run)


class TestInProcessModelInvocation:
    @pytest.mark.asyncio
    async def test_generation_work_type_invokes_gateway_and_feeds_model_response(
        self,
    ) -> None:
        gateway = MagicMock(name="ModelGateway")
        runner = _make_runner()
        loop = _make_loop(gateway=gateway, runner=runner)

        to_thread = _passthrough_to_thread()
        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", to_thread),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("GENERATED-CODE", None),
            ) as mock_invoke,
        ):
            await loop._dispatch_execute_job(_todo("code"))

        # 1. The shared single-source helper was called with this gateway, for
        #    the generation work type (offloaded via to_thread).
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args.args[0] is gateway
        assert mock_invoke.call_args.kwargs["work_type"] == "code"

        # 2. The generated text is threaded into the runner's job_vars.
        runner.write_vars.assert_called_once()
        job_vars = runner.write_vars.call_args.kwargs["job_vars"]
        assert job_vars["model_response"] == "GENERATED-CODE"

        # 3. The playbook still runs, also offloaded to a worker thread.
        assert any(
            call.args and call.args[0] is runner.run_playbook
            for call in to_thread.await_args_list
        )

    @pytest.mark.asyncio
    async def test_non_generation_work_type_skips_model_call(self) -> None:
        gateway = MagicMock(name="ModelGateway")
        runner = _make_runner()
        loop = _make_loop(gateway=gateway, runner=runner)

        to_thread = _passthrough_to_thread()
        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", to_thread),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("SHOULD-NOT-BE-USED", None),
            ) as mock_invoke,
        ):
            # "review" is NOT a generation work type — no model call.
            await loop._dispatch_execute_job(_todo("review"))

        mock_invoke.assert_not_called()
        runner.write_vars.assert_called_once()
        job_vars = runner.write_vars.call_args.kwargs["job_vars"]
        # No generated text is fed for a non-generation work type.
        assert job_vars.get("model_response") is None
        # The playbook still runs (offloaded).
        assert any(
            call.args and call.args[0] is runner.run_playbook
            for call in to_thread.await_args_list
        )

    @pytest.mark.asyncio
    async def test_no_gateway_skips_model_call_but_runs_playbook(self) -> None:
        runner = _make_runner()
        loop = _make_loop(gateway=None, runner=runner)

        to_thread = _passthrough_to_thread()
        with (
            patch("general_ludd.event_loop.loop.asyncio.to_thread", to_thread),
            patch(
                "general_ludd.event_loop.loop.invoke_model_for_generation",
                return_value=("SHOULD-NOT-BE-USED", None),
            ) as mock_invoke,
        ):
            # Generation work type, but no gateway wired -> no model call.
            await loop._dispatch_execute_job(_todo("code"))

        mock_invoke.assert_not_called()
        runner.write_vars.assert_called_once()
        job_vars = runner.write_vars.call_args.kwargs["job_vars"]
        assert job_vars.get("model_response") is None
        # Playbook still runs even with no model in the loop.
        assert any(
            call.args and call.args[0] is runner.run_playbook
            for call in to_thread.await_args_list
        )
