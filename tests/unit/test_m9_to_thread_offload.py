"""M9 (W3.3): blocking run_playbook is offloaded via asyncio.to_thread.

``run_playbook`` is a blocking I/O call (it shells out to ansible-runner and can
take many seconds). Calling it directly on the asyncio event loop freezes every
other coroutine for its entire duration. M9 wraps every such call in
``asyncio.to_thread`` so the loop stays responsive and CancelledError propagates
cleanly.

These tests pin the offload on the two remaining surfaces:
  * the worker HTTP path ``worker/app.py:execute_job``; and
  * the daemon ``event_loop/loop.py:_dispatch_review_job`` runner branch.

Each asserts (1) ``asyncio.to_thread`` received ``run_playbook`` as its target,
and (2) the event loop stays responsive: a concurrently-scheduled coroutine
makes progress while the (real-thread) blocking call is in flight.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.event_loop.loop import EventLoop
from general_ludd.worker.app import create_app


def _make_adapter(tmp: str, job_id: str) -> MagicMock:
    adapter = MagicMock()
    job_root = os.path.join(tmp, job_id)
    os.makedirs(os.path.join(job_root, "env"), exist_ok=True)
    os.makedirs(os.path.join(job_root, "project"), exist_ok=True)
    os.makedirs(os.path.join(job_root, "inventory"), exist_ok=True)
    os.makedirs(os.path.join(job_root, "artifacts"), exist_ok=True)
    adapter.list_playbooks.return_value = ["noop.yml", "return_review.yml"]
    adapter.prepare_job_dirs.return_value = {
        "root": job_root,
        "env": os.path.join(job_root, "env"),
        "project": os.path.join(job_root, "project"),
        "inventory": os.path.join(job_root, "inventory"),
        "artifacts": os.path.join(job_root, "artifacts"),
    }
    adapter.write_vars.return_value = os.path.join(job_root, "env", "extravars")
    adapter.run_playbook.return_value = {"rc": 0, "output": "", "events": []}
    return adapter


class TestWorkerExecuteToThreadOffload:
    @pytest.mark.asyncio
    @patch.dict(os.environ, {"GLUDD_PSK_DISABLE": "1"})
    @patch("general_ludd.worker.app.asyncio.to_thread", new_callable=AsyncMock)
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_execute_offloads_run_playbook_via_to_thread(
        self, mock_get_runner: MagicMock, mock_to_thread: AsyncMock
    ) -> None:
        tmp = tempfile.mkdtemp()
        adapter = _make_adapter(tmp, "JOB-M9")
        mock_get_runner.return_value = adapter
        # to_thread is called for prepare_job_dirs, write_vars, and run_playbook.
        # Each returns a different shape — use side_effect so the correct value
        # is returned per-call (dirs dict → str path → run result dict).
        def _to_thread_side_effect(func, *args, **kwargs):
            if func is adapter.run_playbook:
                return {"rc": 0, "output": "", "events": []}
            if func is adapter.write_vars:
                return adapter.write_vars.return_value
            if func is adapter.prepare_job_dirs:
                return adapter.prepare_job_dirs.return_value
            if func is shutil.rmtree:
                shutil.rmtree(*args, **kwargs)
            return None
        mock_to_thread.side_effect = _to_thread_side_effect

        # No gateway: keep the test about the offload, not the model call.
        app = create_app(gateway=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/jobs/execute",
                json={
                    "job_id": "JOB-M9",
                    "todo_id": "TODO-M9",
                    "playbook": "noop.yml",
                    "queue": "core",
                },
            )
            assert resp.status_code == 200

        # run_playbook was NOT called inline; it was handed to asyncio.to_thread.
        adapter.run_playbook.assert_not_called()
        # to_thread is called 3 times (prepare_job_dirs, write_vars, run_playbook);
        # the last call must be run_playbook.
        assert mock_to_thread.await_count >= 3
        # Verify at least one to_thread call was for run_playbook.
        run_playbook_dispatched = any(
            call_args.args[0] is adapter.run_playbook
            for call_args in mock_to_thread.await_args_list
        )
        assert run_playbook_dispatched, "run_playbook was never dispatched via asyncio.to_thread"

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"GLUDD_PSK_DISABLE": "1"})
    @patch("general_ludd.worker.app.get_runner")
    async def test_worker_execute_keeps_event_loop_responsive(
        self, mock_get_runner: MagicMock
    ) -> None:
        tmp = tempfile.mkdtemp()
        adapter = _make_adapter(tmp, "JOB-RESP")

        # Deterministic handshake (no wall-clock race): run_playbook signals it
        # has STARTED, then blocks until the test releases it. The test only
        # releases AFTER it has confirmed the ticker advanced. If run_playbook
        # ran on the event-loop thread, the ticker could never advance to set
        # `release` -> deadlock would time out the test. So even a single tick
        # while the call is in flight proves the call was offloaded.
        started = threading.Event()
        release = threading.Event()

        def _blocking_run_playbook(**_kwargs: Any) -> dict[str, Any]:
            started.set()
            # Bounded wait so a regression fails fast instead of hanging forever.
            assert release.wait(timeout=10.0), "ticker never released blocking call"
            return {"rc": 0, "output": "", "events": []}

        adapter.run_playbook.side_effect = _blocking_run_playbook
        mock_get_runner.return_value = adapter

        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            # Wait until the blocking call is in flight, then prove the loop is
            # responsive by advancing at least once while it's still blocked.
            while not started.is_set():
                await asyncio.sleep(0.001)
            await asyncio.sleep(0)
            ticks += 1
            # Loop is responsive -> let the blocking worker thread finish.
            release.set()

        app = create_app(gateway=None)
        transport = ASGITransport(app=app)

        async def _do_request() -> int:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/jobs/execute",
                    json={
                        "job_id": "JOB-RESP",
                        "todo_id": "TODO-RESP",
                        "playbook": "noop.yml",
                        "queue": "core",
                    },
                )
                return resp.status_code

        status, _ = await asyncio.gather(_do_request(), _ticker())
        assert status == 200
        # The ticker advanced WHILE the blocking playbook was in flight in a
        # worker thread. This is impossible if the call were synchronous on the
        # event loop (the ticker could not run to set `release`).
        assert ticks >= 1


class TestLoopReviewToThreadOffload:
    @pytest.mark.asyncio
    @patch("general_ludd.event_loop.loop.asyncio.to_thread", new_callable=AsyncMock)
    async def test_review_runner_branch_offloads_run_playbook_via_to_thread(
        self, mock_to_thread: AsyncMock
    ) -> None:
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/REVIEW", "env": "/tmp/REVIEW/env"}
        runner.write_vars.return_value = "/tmp/REVIEW/env/extravars"
        runner.run_playbook.return_value = {"rc": 0, "output": "", "events": []}
        mock_to_thread.return_value = runner.run_playbook.return_value

        # No reviewer / no session -> the runner branch of _dispatch_review_job
        # is the path under test (in-process review is not taken).
        loop = EventLoop(worker_base_url="http://worker:8000", config={}, runner=runner)

        tr = MagicMock()
        tr.return_id = "RET-M9"
        tr.todo_id = "TODO-M9"
        tr.queue = "model"
        tr.work_type = "review"
        tr.plan_artifact = None
        tr.project_id = None

        await loop._dispatch_review_job(tr)

        # run_playbook is offloaded, never run inline on the event loop.
        runner.run_playbook.assert_not_called()
        mock_to_thread.assert_awaited_once()
        assert mock_to_thread.await_args.args[0] is runner.run_playbook

    @pytest.mark.asyncio
    async def test_review_runner_branch_keeps_event_loop_responsive(self) -> None:
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/REVIEW2", "env": "/tmp/REVIEW2/env"}
        runner.write_vars.return_value = "/tmp/REVIEW2/env/extravars"

        # Deterministic handshake (no wall-clock race): the blocking call
        # signals it has STARTED and blocks until the ticker — having confirmed
        # the loop advanced — releases it. If the call ran on the event loop the
        # ticker could never run to release it, and the bounded wait would fail.
        started = threading.Event()
        release = threading.Event()

        def _blocking_run_playbook(**_kwargs: Any) -> dict[str, Any]:
            started.set()
            assert release.wait(timeout=10.0), "ticker never released blocking call"
            return {"rc": 0, "output": "", "events": []}

        runner.run_playbook.side_effect = _blocking_run_playbook

        loop = EventLoop(worker_base_url="http://worker:8000", config={}, runner=runner)

        tr = MagicMock()
        tr.return_id = "RET-RESP"
        tr.todo_id = "TODO-RESP"
        tr.queue = "model"
        tr.work_type = "review"
        tr.plan_artifact = None
        tr.project_id = None

        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            while not started.is_set():
                await asyncio.sleep(0.001)
            await asyncio.sleep(0)
            ticks += 1
            release.set()

        _, _ = await asyncio.gather(loop._dispatch_review_job(tr), _ticker())
        # The ticker advanced WHILE the blocking playbook was in flight in a
        # worker thread — impossible unless run_playbook was offloaded.
        assert ticks >= 1
        runner.run_playbook.assert_called_once()
