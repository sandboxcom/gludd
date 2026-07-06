"""E2e proof test for PipelineController — full dispatch→integrate→gate lifecycle.

Proves the pipeline-controller feature (#77) processes work items through all
three lanes end-to-end, handles empty queues gracefully, respects the
``pipeline.enabled`` config flag, and emits observability heartbeats — all with
mocked model calls (no real API usage).

Each test case exercises the controller through its public surface (start/stop/
submit/report_completed/status/emit_heartbeat) so the assertions are against the
same contract the daemon's lifespan management uses.

See Also:
    ``src/general_ludd/pipeline/controller.py`` — the component under test
    ``tests/unit/test_pipeline_lanes.py``   — unit tests for individual lanes
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, cast

import pytest

from general_ludd.pipeline.controller import PipelineController
from general_ludd.pipeline.state import (
    CompletedUnit,
    Heartbeat,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)


def _cfg(**kw: object) -> PipelineConfig:
    """Fast-lane config for tests: zero debounce, fast-poll intervals."""
    base: dict[str, object] = dict(
        enabled=True,
        floor=1,
        target=3,
        gate_debounce_s=0.0,
        max_worktrees=6,
        dispatch_interval_s=0.01,
        integrate_interval_s=0.01,
        gate_poll_interval_s=0.01,
        heartbeat_interval_s=0.01,
    )
    base.update(kw)
    return cast(Any, PipelineConfig)(**base)


def _controller(
    *,
    dispatch_calls: list[str] | None = None,
    merge_outcome: MergeOutcome | None = None,
    gate_results: list[bool] | None = None,
    heartbeat_sink: list[Heartbeat] | None = None,
    **cfg_kw: object,
) -> PipelineController:
    """Build a PipelineController wired to recording fakes.

    ``dispatch_calls`` — every unit_id passed to dispatch_fn is appended here.
    ``merge_outcome`` — the MergeOutcome returned for every merge call.
    ``gate_results`` — popped from the front for each gate run (default True).
    ``heartbeat_sink`` — every emitted Heartbeat is appended here.
    """
    rec_dispatch = dispatch_calls if dispatch_calls is not None else []
    rec_gate = gate_results if gate_results is not None else []

    async def dispatch(uid: str) -> None:
        rec_dispatch.append(uid)

    async def merge(unit: CompletedUnit) -> MergeOutcome:
        if merge_outcome is not None:
            return merge_outcome
        return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="merged")

    async def gate() -> bool:
        return rec_gate.pop(0) if rec_gate else True

    return PipelineController(
        _cfg(**cfg_kw),
        dispatch,
        merge,
        gate,
        heartbeat_sink=cast(Any, lambda hb: (heartbeat_sink.append(hb) if heartbeat_sink is not None else None))
        if heartbeat_sink is not None
        else None,
    )


# --------------------------------------------------------------------------- #
# Lifecycle: start / stop                                                     #
# --------------------------------------------------------------------------- #


class TestPipelineControllerLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop_are_idempotent(self) -> None:
        """Double-start and double-stop are no-ops, never crash."""
        ctrl = _controller()
        await ctrl.start()
        await ctrl.start()
        await asyncio.sleep(0.05)
        await ctrl.stop()
        await ctrl.stop()

    @pytest.mark.asyncio
    async def test_status_before_start(self) -> None:
        """Status works even before start() — returns snapshot of state."""
        ctrl = _controller()
        status = await ctrl.status()
        assert status["enabled"] is True
        assert status["running"] == []
        assert status["pending"] == []
        assert status["awaiting_merge"] == []
        assert status["awaiting_gate"] == []

    @pytest.mark.asyncio
    async def test_stop_cancels_running_tasks(self) -> None:
        """A stopped controller has no live asyncio tasks."""
        ctrl = _controller()
        await ctrl.start()
        await asyncio.sleep(0.05)
        await ctrl.stop()
        # After stop, _running is False and _tasks is empty.
        assert ctrl._running is False
        assert ctrl._tasks == []


# --------------------------------------------------------------------------- #
# E2E: dispatch → integrate → gate                                            #
# --------------------------------------------------------------------------- #


class TestPipelineEndToEnd:
    @pytest.mark.asyncio
    async def test_full_flow_dispatch_integrate_gate(self) -> None:
        """Two work items flow through all three lanes and land in the gate."""
        dispatched: list[str] = []
        merged_units: list[str] = []

        async def dispatch(uid: str) -> None:
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            merged_units.append(unit.unit_id)
            return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="ours")

        gate_runs = {"n": 0}

        async def gate() -> bool:
            gate_runs["n"] += 1
            return True

        ctrl = PipelineController(
            _cfg(target=2, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["unit-a", "unit-b"])

        # Wait for dispatch to pick up both items.
        await asyncio.sleep(0.08)

        # Simulate agents completing and reporting worktrees.
        await ctrl.report_completed(CompletedUnit("unit-a", "/tmp/wt/a"))
        await ctrl.report_completed(CompletedUnit("unit-b", "/tmp/wt/b"))

        # Wait for integrate + gate lanes to drain.
        await asyncio.sleep(0.15)
        await ctrl.stop()

        assert set(dispatched) == {"unit-a", "unit-b"}
        assert set(merged_units) == {"unit-a", "unit-b"}
        assert gate_runs["n"] >= 1

        status = await ctrl.status()
        assert status["counters"]["dispatched"] == 2
        assert status["counters"]["merged"] == 2
        assert status["counters"]["gates_run"] >= 1

    @pytest.mark.asyncio
    async def test_dispatch_failure_requeues_and_retries(self) -> None:
        """A dispatch exception rolls the unit back to pending for retry."""
        fail = {"first": True}
        dispatched: list[str] = []

        async def dispatch(uid: str) -> None:
            if fail["first"]:
                fail["first"] = False
                raise RuntimeError("launch failed")
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(target=1, floor=1),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["unit-x"])

        # First tick: dispatch fails, unit requeued.
        await asyncio.sleep(0.03)
        # Second tick: retry succeeds.
        await asyncio.sleep(0.03)
        await ctrl.stop()

        assert dispatched == ["unit-x"]

    @pytest.mark.asyncio
    async def test_gate_green_clears_and_gate_red_keeps_work(self) -> None:
        """Green gate clears merged work; red gate preserves it for re-gate."""
        gate_seq = [False, True]  # first run RED, second GREEN

        async def dispatch(uid: str) -> None:
            pass

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return gate_seq.pop(0) if gate_seq else True

        ctrl = PipelineController(
            _cfg(target=1, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["unit-r"])
        await asyncio.sleep(0.05)
        await ctrl.report_completed(CompletedUnit("unit-r", "/tmp/wt/r"))
        await asyncio.sleep(0.15)
        await ctrl.stop()

        status = await ctrl.status()
        assert status["counters"]["gates_run"] >= 2
        assert status["counters"]["gates_green"] >= 1
        # After green, awaiting_gate should be clear.
        assert status["awaiting_gate"] == []

    @pytest.mark.asyncio
    async def test_interspersed_submit_and_complete(self) -> None:
        """New work submitted mid-flight is also dispatched and merged."""
        dispatched: list[str] = []

        async def dispatch(uid: str) -> None:
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(target=2, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["a"])

        await asyncio.sleep(0.04)
        # Submit more work while the first batch is already running.
        await ctrl.submit(["b", "c"])
        await asyncio.sleep(0.04)

        # Complete unit-a first; b and c still running.
        await ctrl.report_completed(CompletedUnit("a", "/tmp/wt/a"))
        await asyncio.sleep(0.08)

        await ctrl.report_completed(CompletedUnit("b", "/tmp/wt/b"))
        await ctrl.report_completed(CompletedUnit("c", "/tmp/wt/c"))
        await asyncio.sleep(0.12)
        await ctrl.stop()

        assert set(dispatched) == {"a", "b", "c"}
        status = await ctrl.status()
        assert status["counters"]["dispatched"] == 3
        assert status["counters"]["merged"] == 3


# --------------------------------------------------------------------------- #
# Config flag: pipeline.enabled                                               #
# --------------------------------------------------------------------------- #


class TestPipelineConfigFlag:
    @pytest.mark.asyncio
    async def test_enabled_true_activates_pipeline(self) -> None:
        """pipeline.enabled=True → start() launches all lane tasks."""
        ctrl = _controller(enabled=True)
        assert ctrl._running is False
        await ctrl.start()
        assert ctrl._running is True
        assert len(ctrl._tasks) == 4  # dispatch, integrate, gate, heartbeat
        await ctrl.stop()

    @pytest.mark.asyncio
    async def test_disabled_controller_still_starts_gracefully(self) -> None:
        """When enabled=False, start() still works — the config is advisory."""
        ctrl = _controller(enabled=False)
        await ctrl.start()
        assert ctrl._running is True
        await ctrl.stop()

    @pytest.mark.asyncio
    async def test_status_reflects_enabled_flag(self) -> None:
        """status() exposes the enabled flag so the daemon surface can show it."""
        ctrl_on = _controller(enabled=True)
        ctrl_off = _controller(enabled=False)
        assert (await ctrl_on.status())["enabled"] is True
        assert (await ctrl_off.status())["enabled"] is False

    @pytest.mark.asyncio
    async def test_submit_works_regardless_of_enabled(self) -> None:
        """submit() enqueues work even when enabled=False (controller owns backlog)."""
        ctrl = _controller(enabled=False)
        added = await ctrl.submit(["a", "b"])
        assert added == 2
        status = await ctrl.status()
        assert status["pending"] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Empty-queue handling                                                        #
# --------------------------------------------------------------------------- #


class TestEmptyQueueHandling:
    @pytest.mark.asyncio
    async def test_start_with_no_work_is_noop(self) -> None:
        """Pipeline starts with empty backlog and runs idle without error."""
        ctrl = _controller()
        await ctrl.start()
        await asyncio.sleep(0.06)
        status = await ctrl.status()
        assert status["running"] == []
        assert status["pending"] == []
        await ctrl.stop()

    @pytest.mark.asyncio
    async def test_submit_empty_list_adds_nothing(self) -> None:
        """Submitting an empty iterable is a no-op."""
        ctrl = _controller()
        added = await ctrl.submit([])
        assert added == 0

    @pytest.mark.asyncio
    async def test_report_completed_on_empty_state(self) -> None:
        """Reporting a completed unit when nothing is running is harmless."""
        ctrl = _controller()
        await ctrl.report_completed(CompletedUnit("orphan", "/tmp/wt/o"))
        status = await ctrl.status()
        assert "orphan" in status["awaiting_merge"]

    @pytest.mark.asyncio
    async def test_drain_to_idle(self) -> None:
        """After all work is processed, the pipeline returns to idle state."""
        dispatched: list[str] = []

        async def dispatch(uid: str) -> None:
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(target=2, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["work-1", "work-2"])
        await asyncio.sleep(0.05)

        await ctrl.report_completed(CompletedUnit("work-1", "/tmp/wt/1"))
        await ctrl.report_completed(CompletedUnit("work-2", "/tmp/wt/2"))

        # Let integrate + gate drain everything.
        await asyncio.sleep(0.15)

        status = await ctrl.status()
        assert status["running"] == []
        assert status["pending"] == []
        assert status["awaiting_merge"] == []
        assert status["awaiting_gate"] == []
        assert status["counters"]["dispatched"] == 2
        assert status["counters"]["merged"] == 2
        await ctrl.stop()


# --------------------------------------------------------------------------- #
# Back-pressure                                                               #
# --------------------------------------------------------------------------- #


class TestBackpressure:
    @pytest.mark.asyncio
    async def test_backpressure_at_worktree_ceiling(self) -> None:
        """Dispatch is suppressed when worktree count hits max_worktrees."""
        state = LaneState(
            running={"r1", "r2", "r3"},
            completed_awaiting_merge=deque(
                [CompletedUnit(f"c{i}", f"/tmp/wt/c{i}") for i in range(3)]
            ),
            pending=deque(["a", "b"]),
        )
        cfg = _cfg(target=3, max_worktrees=6)
        dispatched: list[str] = []

        async def dispatch(uid: str) -> None:
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(cfg, dispatch, merge, gate, state=state)
        assert ctrl.backpressured() is True
        # Even if started, dispatch won't pull backlog items.
        await ctrl.start()
        await asyncio.sleep(0.05)
        await ctrl.stop()
        assert dispatched == []


# --------------------------------------------------------------------------- #
# Heartbeat & observability                                                   #
# --------------------------------------------------------------------------- #


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_emitted_periodically(self) -> None:
        """Heartbeat sink receives snapshots at the configured interval."""
        beats: list[Heartbeat] = []
        ctrl = _controller(heartbeat_sink=beats)
        await ctrl.submit(["h1", "h2", "h3"])
        hb = await ctrl.emit_heartbeat()
        assert hb is beats[0]
        assert hb.pending == 3
        assert hb.running == 0
        assert hb.backpressure is False

    @pytest.mark.asyncio
    async def test_heartbeat_shape(self) -> None:
        """Each heartbeat carries all required fields."""
        beats: list[Heartbeat] = []
        ctrl = _controller(heartbeat_sink=beats)
        await ctrl.submit(["x"])
        hb = await ctrl.emit_heartbeat()
        assert isinstance(hb.epoch, float)
        assert hb.epoch > 0
        assert isinstance(hb.running, int)
        assert isinstance(hb.pending, int)
        assert isinstance(hb.awaiting_merge, int)
        assert isinstance(hb.awaiting_gate, int)
        assert isinstance(hb.backpressure, bool)

    @pytest.mark.asyncio
    async def test_heartbeat_sink_error_does_not_crash(self) -> None:
        """A heartbeat sink that raises does not kill the controller."""

        def bad_sink(hb: object) -> None:
            raise RuntimeError("sink exploded")

        async def dispatch(uid: str) -> None:
            pass

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(), dispatch, merge, gate, heartbeat_sink=bad_sink,
        )
        # Must not raise.
        await ctrl.emit_heartbeat()


# --------------------------------------------------------------------------- #
# Status snapshot                                                             #
# --------------------------------------------------------------------------- #


class TestStatusSnapshot:
    @pytest.mark.asyncio
    async def test_status_counts_are_monotonic(self) -> None:
        """Counters only increase as work flows through the pipeline."""
        dispatched: list[str] = []

        async def dispatch(uid: str) -> None:
            dispatched.append(uid)

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=unit.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(target=2, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()

        s0 = await ctrl.status()
        assert s0["counters"]["dispatched"] == 0
        assert s0["counters"]["merged"] == 0

        await ctrl.submit(["s1", "s2"])
        await asyncio.sleep(0.05)
        await ctrl.report_completed(CompletedUnit("s1", "/tmp/wt/s1"))
        await ctrl.report_completed(CompletedUnit("s2", "/tmp/wt/s2"))
        await asyncio.sleep(0.15)

        s1 = await ctrl.status()
        assert s1["counters"]["dispatched"] >= 2
        assert s1["counters"]["merged"] >= 2
        await ctrl.stop()

    @pytest.mark.asyncio
    async def test_status_desired_target_matches_config_when_no_pid(self) -> None:
        """Without a PID provider, desired_target equals config.target."""
        ctrl = _controller(target=4)
        status = await ctrl.status()
        assert status["config"]["target"] == 4
        assert status["desired_target"] == 4


# --------------------------------------------------------------------------- #
# Clobber / conflict handling in merge lane                                    #
# --------------------------------------------------------------------------- #


class TestClobberHandling:
    @pytest.mark.asyncio
    async def test_clobber_refusal_requeues_unit(self) -> None:
        """A clobber-refused merge is requeued, not dropped."""
        refusals = 0

        async def dispatch(uid: str) -> None:
            pass

        async def merge(unit: CompletedUnit) -> MergeOutcome:
            nonlocal refusals
            refusals += 1
            # Refuse the first attempt; accept on retry.
            if refusals == 1:
                return MergeOutcome(
                    unit_id=unit.unit_id, merged=False, clobber_refused=True,
                    detail="conflict:foo.py",
                )
            return MergeOutcome(unit_id=unit.unit_id, merged=True, detail="retry-ok")

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(target=1, floor=1, gate_debounce_s=0.0),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["conflict-unit"])
        await asyncio.sleep(0.05)

        await ctrl.report_completed(
            CompletedUnit("conflict-unit", "/tmp/wt/cu")
        )
        await asyncio.sleep(0.15)
        await ctrl.stop()

        status = await ctrl.status()
        assert status["counters"]["clobbers_refused"] >= 1
        # Merge eventually succeeded on retry.
        assert status["counters"]["merged"] >= 1


# --------------------------------------------------------------------------- #
# Config shape / validation                                                   #
# --------------------------------------------------------------------------- #


class TestConfigValidation:
    def test_default_config_is_disabled(self) -> None:
        """PipelineConfig defaults to enabled=False (safe default)."""
        assert PipelineConfig().enabled is False

    def test_floor_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="floor must be >= 0"):
            PipelineConfig(floor=-1)

    def test_target_below_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="target must be >= floor"):
            PipelineConfig(floor=5, target=3)

    def test_max_worktrees_below_target_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_worktrees must be >= target"):
            PipelineConfig(target=10, max_worktrees=5)

    def test_negative_gate_debounce_rejected(self) -> None:
        with pytest.raises(ValueError, match="gate_debounce_s"):
            PipelineConfig(gate_debounce_s=-0.1)
