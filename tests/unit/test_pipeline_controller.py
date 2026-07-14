from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.pipeline.controller import (
    PipelineController,
    _suppress_cancel,
)
from general_ludd.pipeline.state import (
    CompletedUnit,
    Heartbeat,
    LaneState,
    PipelineConfig,
)


class TestSuppressCancel:
    def test_normal_exit_returns_false(self) -> None:
        with _suppress_cancel() as sc:
            pass
        assert sc.__exit__(None, None, None) is False

    def test_cancelled_error_returns_true(self) -> None:
        exc = asyncio.CancelledError()
        with _suppress_cancel() as sc:
            pass
        assert sc.__exit__(asyncio.CancelledError, exc, None) is True

    def test_non_cancel_error_returns_false(self) -> None:
        exc = ValueError("boom")
        with _suppress_cancel() as sc:
            pass
        assert sc.__exit__(ValueError, exc, None) is False

    def test_enter_returns_self(self) -> None:
        sc = _suppress_cancel()
        assert sc.__enter__() is sc


class TestPipelineControllerInit:
    def test_default_construction(self) -> None:
        dispatch_fn: MagicMock = MagicMock()
        merge_fn: MagicMock = MagicMock()
        gate_fn: MagicMock = MagicMock()
        ctrl = PipelineController(
            PipelineConfig(), dispatch_fn, merge_fn, gate_fn,
        )
        assert isinstance(ctrl._state, LaneState)
        assert ctrl._state.total_dispatched == 0
        assert ctrl._running is False

    def test_custom_state_injected(self) -> None:
        state = LaneState(total_dispatched=5)
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
            state=state,
        )
        assert ctrl._state.total_dispatched == 5

    def test_backpressured_delegates_to_dispatch_lane(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(target=3, max_worktrees=3),
            MagicMock(), MagicMock(), MagicMock(),
        )
        assert ctrl.backpressured() is False

    @pytest.mark.asyncio
    async def test_submit_appends_to_pending(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
        )
        count = await ctrl.submit(["u1", "u2", "u3"])
        assert count == 3
        assert len(ctrl._state.pending) == 3
        assert ctrl._state.pending[0] == "u1"

    @pytest.mark.asyncio
    async def test_submit_empty_returns_zero(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
        )
        count = await ctrl.submit([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_report_completed_enqueues_unit(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
        )
        unit = CompletedUnit(unit_id="u99", worktree_path="/tmp/wt")
        await ctrl.report_completed(unit)
        assert len(ctrl._state.completed_awaiting_merge) == 1
        assert ctrl._state.completed_awaiting_merge[0].unit_id == "u99"


class TestPipelineControllerHeartbeat:
    def test_default_heartbeat_sink_logs(self) -> None:
        hb = Heartbeat(
            epoch=1000.0, running=2, pending=1, awaiting_merge=0,
            awaiting_gate=0, last_gate_epoch=0.0, backpressure=False,
        )
        with patch("logging.Logger.info") as mock_info:
            PipelineController._default_heartbeat_sink(hb)
        assert mock_info.called


class TestPipelineControllerTaskCallback:
    def test_on_task_done_cancelled_is_silent(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = True
        with patch("logging.Logger.error") as mock_err:
            PipelineController._on_task_done(task)
        mock_err.assert_not_called()

    def test_on_task_done_no_exception_is_silent(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None
        with patch("logging.Logger.error") as mock_err:
            PipelineController._on_task_done(task)
        mock_err.assert_not_called()

    def test_on_task_done_exception_is_logged(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        exc = RuntimeError("lane crash")
        task.exception.return_value = exc
        task.get_name.return_value = "pipeline-dispatch"
        with patch("logging.Logger.error") as mock_err:
            PipelineController._on_task_done(task)
        assert mock_err.called


class TestPipelineControllerStatus:
    @pytest.mark.asyncio
    async def test_status_returns_dict_with_expected_keys(self) -> None:
        state = LaneState(
            running={"agent-1", "agent-2"},
            pending=deque(["todo-1"]),
            total_dispatched=10,
            total_merged=5,
            total_gates_run=3,
            total_gates_green=3,
        )
        ctrl = PipelineController(
            PipelineConfig(enabled=True, floor=2, target=4, max_worktrees=6),
            MagicMock(), MagicMock(), MagicMock(),
            state=state,
        )
        status = await ctrl.status()
        assert status["enabled"] is True
        assert status["running"] == ["agent-1", "agent-2"]
        assert status["pending"] == ["todo-1"]
        assert status["worktree_count"] == 2
        assert status["backpressure"] is False
        assert status["counters"]["dispatched"] == 10
        assert status["counters"]["merged"] == 5
        assert status["config"]["floor"] == 2
        assert status["config"]["target"] == 4
        assert "desired_target" in status

    @pytest.mark.asyncio
    async def test_status_with_awaiting_merge(self) -> None:
        state = LaneState(
            completed_awaiting_merge=deque([
                CompletedUnit(unit_id="u1", worktree_path="/tmp/wt1"),
                CompletedUnit(unit_id="u2", worktree_path="/tmp/wt2"),
            ]),
        )
        ctrl = PipelineController(
            PipelineConfig(enabled=False),
            MagicMock(), MagicMock(), MagicMock(),
            state=state,
        )
        status = await ctrl.status()
        assert status["awaiting_merge"] == ["u1", "u2"]
        assert status["worktree_count"] == 2
        assert status["enabled"] is False


class TestPipelineControllerLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_tasks(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(enabled=True),
            MagicMock(), MagicMock(), MagicMock(),
        )
        await ctrl.start()
        assert ctrl._running is True
        assert len(ctrl._tasks) == 4
        task_names = [t.get_name() for t in ctrl._tasks]
        assert "pipeline-dispatch" in task_names
        assert "pipeline-integrate" in task_names
        assert "pipeline-gate" in task_names
        assert "pipeline-heartbeat" in task_names

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
        )
        await ctrl.start()
        first_tasks = ctrl._tasks
        await ctrl.start()
        assert ctrl._tasks is first_tasks

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_running(self) -> None:
        ctrl = PipelineController(
            PipelineConfig(),
            MagicMock(), MagicMock(), MagicMock(),
        )
        await ctrl.stop()
        assert ctrl._running is False
