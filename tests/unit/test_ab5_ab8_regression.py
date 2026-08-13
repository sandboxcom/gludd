"""AB-5 / AB-8 regression tests: ``call_model`` MUST run off the event loop.

AB-5 (``src/general_ludd/execution/tool_loop.py``) routes the synchronous gateway
through a dedicated, bounded model executor so unrelated default-executor work
cannot starve it. AB-8 (``src/general_ludd/execution/engine.py``) uses
``asyncio.to_thread``. Both keep blocking model HTTP calls off the event loop.

A silent revert — dropping the ``asyncio.to_thread`` wrap and instead calling
``self._gateway.call_model(...)`` directly inside the coroutine — would still
PASS the existing import smoke / end-to-end tests (the value still comes back),
but it would freeze the event loop for the full duration of every model call.
In production that is a throughput / liveness bug, not a correctness bug, so it
is easy to miss.

These tests pin both mechanisms and the symptom: a concurrent probe coroutine
runs during the model call instead of being starved by a blocked loop.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.execution.engine import ExecutionEngine
from general_ludd.execution.tool_loop import ToolCallLoop, _run_model_call
from general_ludd.schemas.job import JobSpec


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
class _BlockingGateway:
    """A fake gateway whose ``call_model`` blocks the calling thread for
    ``delay`` seconds and yields the event loop to OTHER threads/tasks only via
    the asyncio.to_thread offload (i.e. it does NOT await anything itself).

    Crucially, the sleep is a SYNCHRONOUS ``time.sleep`` — exactly the pattern a
    real blocking HTTP client would exhibit. If the engine/loop calls this
    directly on the event loop thread, NO other coroutine can make progress
    while we sleep. If they wrap us in ``asyncio.to_thread``, the event loop is
    free to run other coroutines on its own thread while our worker thread
    sleeps.
    """

    def __init__(self, delay: float = 0.25, payload: str = "model-output") -> None:
        self.delay = delay
        self.payload = payload
        self.call_count = 0

    def call_model(self, *_args: object, **_kwargs: object) -> MagicMock:
        import time

        self.call_count += 1
        time.sleep(self.delay)  # SYNCHRONOUS — blocks the calling thread only.
        return MagicMock(content=self.payload)


def _make_job() -> JobSpec:
    return JobSpec(
        job_id="JOB-AB",
        todo_id="TODO-AB",
        playbook="code",
        queue="core",
        work_type="code",
        prompt_text="AB regression",
    )


def _make_tool_loop(gateway: object) -> ToolCallLoop:
    """A ToolCallLoop wired only enough to exercise ``_call_model`` /
    ``_call_with_tools``. No MCP client or registry needed for the direct
    model-call paths."""
    return ToolCallLoop(model_gateway=gateway, mcp_client=None, mcp_registry=None)


def _make_engine(gateway: object) -> ExecutionEngine:
    return ExecutionEngine(model_gateway=gateway, workspace_path=tempfile.mkdtemp())


# --------------------------------------------------------------------------- #
# AB-5 — ToolCallLoop._call_model / _call_with_tools wrap in asyncio.to_thread
# --------------------------------------------------------------------------- #
class TestAB5ToolLoopOffload:
    @pytest.mark.asyncio
    async def test_ab5_tool_loop_call_model_uses_isolated_executor(self) -> None:
        """``_call_model`` must dispatch through the isolated model executor."""
        gateway = _BlockingGateway()
        loop = _make_tool_loop(gateway)

        offload_calls: list[tuple[object, tuple, dict]] = []

        async def _spy_offload(fn, *args, **kwargs):
            offload_calls.append((fn, args, kwargs))
            return await _run_model_call(fn, *args, **kwargs)

        with patch(
            "general_ludd.execution.tool_loop._run_model_call",
            _spy_offload,
        ):
            await loop._call_model(_make_job(), "system", "user")

        assert gateway.call_count == 1, "gateway.call_model must have run exactly once"
        assert offload_calls, (
            "AB-5 REGRESSION: _call_model bypassed the isolated model executor."
        )
        fn, _args, kwargs = offload_calls[0]
        assert fn == gateway.call_model, (
            "The isolated executor was called, but not with gateway.call_model as "
            f"its first argument (got {fn!r})."
        )
        assert kwargs.get("work_type") == "code"

    @pytest.mark.asyncio
    async def test_ab5_tool_loop_call_with_tools_uses_isolated_executor(self) -> None:
        """Same isolated-executor pin for the tools-bearing variant."""
        gateway = _BlockingGateway()
        loop = _make_tool_loop(gateway)

        offload_calls: list[tuple[object, tuple, dict]] = []

        async def _spy_offload(fn, *args, **kwargs):
            offload_calls.append((fn, args, kwargs))
            return await _run_model_call(fn, *args, **kwargs)

        with patch(
            "general_ludd.execution.tool_loop._run_model_call",
            _spy_offload,
        ):
            await loop._call_with_tools(
                _make_job(),
                [{"role": "user", "content": "hi"}],
                [{"name": "tool_a"}],
            )

        assert gateway.call_count == 1
        assert offload_calls, (
            "AB-5 REGRESSION: _call_with_tools bypassed the isolated model executor."
        )
        fn, _args, _kwargs = offload_calls[0]
        assert fn == gateway.call_model

    @pytest.mark.asyncio
    async def test_ab5_tool_loop_isolated_from_default_executor_starvation(self) -> None:
        """Model calls must not queue behind unrelated default-executor work."""
        gateway = _BlockingGateway(delay=0)
        loop = _make_tool_loop(gateway)
        event_loop = asyncio.get_running_loop()
        release = threading.Event()
        started = threading.Event()
        default_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="test-saturated-default",
        )
        event_loop.set_default_executor(default_executor)

        def _occupy_default_executor() -> None:
            started.set()
            release.wait(timeout=5)

        blocker = event_loop.run_in_executor(None, _occupy_default_executor)
        assert started.wait(timeout=1)
        try:
            response = await asyncio.wait_for(
                loop._call_with_tools(
                    _make_job(),
                    [{"role": "user", "content": "hi"}],
                    [{"name": "tool_a"}],
                ),
                timeout=0.5,
            )
            assert response.content == "model-output"
        finally:
            release.set()
            await blocker
            default_executor.shutdown(wait=True)

    @pytest.mark.asyncio
    async def test_ab5_tool_loop_does_not_block_event_loop(self) -> None:
        """End-to-end liveness proof: while ``_call_model`` is in flight, a
        concurrent probe coroutine must run to completion. If the loop is
        blocked (direct sync call), the probe only gets to run AFTER the model
        call returns, so its observed start-to-finish wall time balloons to
        roughly ``delay`` seconds."""
        delay = 0.30
        gateway = _BlockingGateway(delay=delay)
        loop = _make_tool_loop(gateway)

        async def _probe() -> float:
            """A tiny coroutine that yields a few times. On an unblocked loop
            this completes in well under ``delay`` seconds."""
            start = asyncio.get_event_loop().time()
            for _ in range(5):
                await asyncio.sleep(0)
            return asyncio.get_event_loop().time() - start

        async def _driver() -> tuple[float, float]:
            probe_task = asyncio.ensure_future(_probe())
            # Give the probe a chance to be scheduled before the (blocking)
            # model call starts.
            await asyncio.sleep(0)
            await loop._call_model(_make_job(), "system", "user")
            probe_elapsed = await probe_task
            return probe_elapsed

        probe_elapsed = await asyncio.wait_for(_driver(), timeout=5.0)

        # If the loop were blocked, the probe could not finish until the model
        # call returned, so its measured elapsed time would be >= delay. On a
        # properly offloaded loop it finishes near-instantly -- well under
        # half of delay, which is more robust to xdist scheduling jitter than
        # the raw `< delay` bound while still proving the loop wasn't blocked.
        assert probe_elapsed < delay * 0.5, (
            f"AB-5 REGRESSION: probe coroutine took {probe_elapsed:.3f}s to "
            f"complete while a {delay}s blocking model call was in flight — "
            "the event loop was blocked, not offloaded via asyncio.to_thread."
        )


# --------------------------------------------------------------------------- #
# AB-8 — ExecutionEngine.execute_async wraps call_model in asyncio.to_thread
# --------------------------------------------------------------------------- #
class TestAB8EngineToThread:
    @pytest.mark.asyncio
    async def test_ab8_engine_execute_async_uses_to_thread(self) -> None:
        """``execute_async`` must route the synchronous ``call_model`` through
        ``asyncio.to_thread`` (engine.py:492)."""
        gateway = _BlockingGateway(payload="```\nFILE: x.py\nprint('ok')\n```")
        engine = _make_engine(gateway)

        to_thread_calls: list[tuple[object, tuple, dict]] = []
        real_to_thread = asyncio.to_thread

        async def _spy_to_thread(fn, *args, **kwargs):
            to_thread_calls.append((fn, args, kwargs))
            return await real_to_thread(fn, *args, **kwargs)

        with patch(
            "general_ludd.execution.engine.asyncio.to_thread",
            _spy_to_thread,
        ):
            await engine.execute_async(_make_job())

        assert gateway.call_count == 1, "gateway.call_model must have run exactly once"
        assert to_thread_calls, (
            "AB-8 REGRESSION: execute_async did NOT route the blocking "
            "call_model through asyncio.to_thread — the event loop would be "
            "blocked for the full duration of every model call."
        )
        fn, _args, _kwargs = to_thread_calls[0]
        assert fn == gateway.call_model, (
            "asyncio.to_thread was called, but not with the model gateway's "
            f"call_model as its first argument (got {fn!r})."
        )

    @pytest.mark.asyncio
    async def test_ab8_engine_does_not_block_event_loop(self) -> None:
        """End-to-end liveness proof for AB-8: while ``execute_async`` is in
        flight, a concurrent probe coroutine must complete in well under the
        blocking model call's duration."""
        delay = 0.30
        gateway = _BlockingGateway(delay=delay, payload="```\nFILE: x.py\ny\n```")
        engine = _make_engine(gateway)

        async def _probe() -> float:
            start = asyncio.get_event_loop().time()
            for _ in range(5):
                await asyncio.sleep(0)
            return asyncio.get_event_loop().time() - start

        async def _driver() -> tuple[float, float]:
            probe_task = asyncio.ensure_future(_probe())
            await asyncio.sleep(0)
            await engine.execute_async(_make_job())
            probe_elapsed = await probe_task
            return probe_elapsed

        probe_elapsed = await asyncio.wait_for(_driver(), timeout=10.0)

        # Well under half of delay: more robust to xdist scheduling jitter
        # than the raw `< delay` bound while still proving the loop wasn't
        # blocked (see matching AB-5 rationale above).
        assert probe_elapsed < delay * 0.5, (
            f"AB-8 REGRESSION: probe coroutine took {probe_elapsed:.3f}s to "
            f"complete while a {delay}s blocking model call was in flight — "
            "the event loop was blocked, not offloaded via asyncio.to_thread."
        )
