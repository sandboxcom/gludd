"""Unit tests for the 3-lane multitask+merge pipeline (#77).

Each lane is tested in isolation with mocked dispatch / merge / gate functions
via its single-iteration ``step()`` so the assertions are deterministic (no
real sleeps, no real loops). The controller's start/stop/status/back-pressure
and the heartbeat are tested against the live asyncio task wiring.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, cast

import pytest

from general_ludd.pipeline.controller import PipelineController
from general_ludd.pipeline.lanes import DispatchLane, GateLane, IntegrateLane
from general_ludd.pipeline.state import (
    CompletedUnit,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)


def _cfg(**kw: object) -> PipelineConfig:
    base: dict[str, object] = dict(
        enabled=True, floor=1, target=3, gate_debounce_s=30.0, max_worktrees=6,
    )
    base.update(kw)
    return cast(Any, PipelineConfig)(**base)


# --------------------------------------------------------------------------- #
# PipelineConfig validation                                                    #
# --------------------------------------------------------------------------- #


class TestPipelineConfig:
    def test_target_below_floor_rejected(self) -> None:
        with pytest.raises(ValueError, match="target must be >= floor"):
            PipelineConfig(floor=3, target=1)

    def test_max_worktrees_below_target_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_worktrees must be >= target"):
            PipelineConfig(target=5, max_worktrees=2)

    def test_negative_debounce_rejected(self) -> None:
        with pytest.raises(ValueError, match="gate_debounce_s"):
            PipelineConfig(gate_debounce_s=-1.0)

    def test_defaults_disabled(self) -> None:
        assert PipelineConfig().enabled is False


# --------------------------------------------------------------------------- #
# DispatchLane — saturation backfill, floor, back-pressure                     #
# --------------------------------------------------------------------------- #


class TestDispatchLane:
    @pytest.mark.asyncio
    async def test_backfills_up_to_target(self) -> None:
        state = LaneState(pending=deque(["a", "b", "c", "d", "e"]))
        dispatched: list[str] = []

        async def fake_dispatch(uid: str) -> None:
            dispatched.append(uid)

        lane = DispatchLane(_cfg(target=3, floor=1), state, asyncio.Lock(), fake_dispatch)
        out = await lane.step()
        # target=3, running=0 -> pull exactly 3.
        assert out == ["a", "b", "c"]
        assert dispatched == ["a", "b", "c"]
        assert state.running == {"a", "b", "c"}
        assert list(state.pending) == ["d", "e"]

    @pytest.mark.asyncio
    async def test_backfill_only_refills_empty_slots(self) -> None:
        # 2 already running, target 3 -> pull exactly 1.
        state = LaneState(running={"x", "y"}, pending=deque(["a", "b", "c"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(_cfg(target=3), state, asyncio.Lock(), fake_dispatch)
        out = await lane.step()
        assert out == ["a"]
        assert state.running == {"x", "y", "a"}

    @pytest.mark.asyncio
    async def test_floor_pulls_even_when_target_math_is_zero(self) -> None:
        # Contrived: floor>target can't happen via config, so test the floor
        # branch directly: target == running but floor demands more. We model
        # this by making target small and running 0 but require floor coverage.
        state = LaneState(pending=deque(["a", "b"]))
        cfg = _cfg(floor=2, target=2)

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(cfg, state, asyncio.Lock(), fake_dispatch)
        out = await lane.step()
        assert set(out) == {"a", "b"}
        assert state.running == {"a", "b"}

    @pytest.mark.asyncio
    async def test_never_drops_below_floor_branch(self) -> None:
        # Directly exercise the floor backfill: pretend saturation returns too
        # few by setting target == running so plan_backfill yields nothing, yet
        # floor still demands one running unit.
        state = LaneState(running=set(), pending=deque(["a", "b", "c"]))
        cfg = _cfg(floor=1, target=1)

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(cfg, state, asyncio.Lock(), fake_dispatch)
        out = await lane.step()
        assert len(out) == 1
        assert len(state.running) >= cfg.floor

    @pytest.mark.asyncio
    async def test_backpressure_at_max_worktrees(self) -> None:
        # running + awaiting_merge already at ceiling -> dispatch nothing.
        state = LaneState(
            running={"r1", "r2"},
            completed_awaiting_merge=deque(
                [CompletedUnit(f"c{i}", f"/wt/c{i}") for i in range(4)]
            ),
            pending=deque(["a", "b"]),
        )
        cfg = _cfg(target=3, max_worktrees=6)
        assert state.worktree_count() == 6

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(cfg, state, asyncio.Lock(), fake_dispatch)
        assert lane.backpressured() is True
        out = await lane.step()
        assert out == []
        assert list(state.pending) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_disk_pressure_blocks_dispatch(self) -> None:
        state = LaneState(pending=deque(["a", "b"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(
            _cfg(), state, asyncio.Lock(), fake_dispatch, disk_ok=lambda: False,
        )
        assert lane.backpressured() is True
        assert await lane.step() == []
        assert list(state.pending) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_worktree_headroom_caps_backfill(self) -> None:
        # target has slack but only 1 worktree slot free -> dispatch only 1.
        state = LaneState(
            running={"r1", "r2", "r3", "r4"},  # 4 running
            pending=deque(["a", "b", "c"]),
        )
        cfg = _cfg(target=5, floor=1, max_worktrees=5)  # 1 free worktree slot

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(cfg, state, asyncio.Lock(), fake_dispatch)
        out = await lane.step()
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_dispatch_failure_requeues_unit(self) -> None:
        state = LaneState(pending=deque(["a"]))

        async def boom(uid: str) -> None:
            raise RuntimeError("launch failed")

        lane = DispatchLane(_cfg(target=1, floor=1), state, asyncio.Lock(), boom)
        out = await lane.step()
        assert out == []
        # Unit rolled back to pending, not stuck in running.
        assert "a" in state.pending
        assert "a" not in state.running

    @pytest.mark.asyncio
    async def test_empty_backlog_is_noop(self) -> None:
        state = LaneState()

        async def fake_dispatch(uid: str) -> None:
            raise AssertionError("should not dispatch")

        lane = DispatchLane(_cfg(), state, asyncio.Lock(), fake_dispatch)
        assert await lane.step() == []


# --------------------------------------------------------------------------- #
# DispatchLane — PID-driven targeting (uses gludd's existing LoadController)    #
# --------------------------------------------------------------------------- #


class TestDispatchLanePidDriven:
    """The keep-N-busy loop derives N from the load/PID ControllerOutputs."""

    @pytest.mark.asyncio
    async def test_total_buckets_drives_target_above_static(self) -> None:
        # Static config target is 3, but the PID controller wants 5 total
        # active buckets -> the lane backfills to 5, not the frozen 3.
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque(["a", "b", "c", "d", "e", "f"]))
        dispatched: list[str] = []

        async def fake_dispatch(uid: str) -> None:
            dispatched.append(uid)

        outputs = ControllerOutputs(desired_total_active_buckets=5)
        lane = DispatchLane(
            _cfg(target=3, floor=1, max_worktrees=10), state, asyncio.Lock(),
            fake_dispatch, pid_provider=lambda: outputs,
        )
        assert lane.desired_target() == 5
        out = await lane.step()
        assert out == ["a", "b", "c", "d", "e"]
        assert state.running == {"a", "b", "c", "d", "e"}

    @pytest.mark.asyncio
    async def test_pid_throttle_below_static_target(self) -> None:
        # The PID controller throttles desired concurrency to 1 (load high);
        # the lane shrinks the keep-busy target accordingly (not the static 3).
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque(["a", "b", "c", "d"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        outputs = ControllerOutputs(desired_total_active_buckets=1)
        lane = DispatchLane(
            _cfg(target=3, floor=1), state, asyncio.Lock(), fake_dispatch,
            pid_provider=lambda: outputs,
        )
        assert lane.desired_target() == 1
        out = await lane.step()
        assert out == ["a"]

    @pytest.mark.asyncio
    async def test_pid_group_selects_per_queue_bucket(self) -> None:
        # When a pid_group is configured the lane targets THAT group's
        # per-queue bucket count, honouring the queue-level throttle, rather
        # than the aggregate total.
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque(["a", "b", "c", "d", "e"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        outputs = ControllerOutputs(
            desired_total_active_buckets=99,  # aggregate is large…
            desired_active_buckets_by_queue={"core": 2, "qa": 4},
        )
        lane = DispatchLane(
            _cfg(target=3, floor=1, max_worktrees=10), state, asyncio.Lock(),
            fake_dispatch, pid_provider=lambda: outputs, pid_group="core",
        )
        # …but the pid_group "core" caps this lane at 2.
        assert lane.desired_target() == 2
        out = await lane.step()
        assert out == ["a", "b"]

    @pytest.mark.asyncio
    async def test_pid_group_absent_falls_back_to_total(self) -> None:
        # pid_group configured but not present in the by-queue map -> fall back
        # to the aggregate desired_total_active_buckets.
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque(["a", "b", "c", "d"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        outputs = ControllerOutputs(
            desired_total_active_buckets=3,
            desired_active_buckets_by_queue={"qa": 9},
        )
        lane = DispatchLane(
            _cfg(target=1, floor=1, max_worktrees=10), state, asyncio.Lock(),
            fake_dispatch, pid_provider=lambda: outputs, pid_group="core",
        )
        assert lane.desired_target() == 3

    @pytest.mark.asyncio
    async def test_floor_clamps_pid_throttle(self) -> None:
        # PID wants to throttle to 0/below floor; the floor is a HARD lower
        # bound so the lane still keeps `floor` agents busy while backlog remains.
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque(["a", "b"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        outputs = ControllerOutputs(desired_total_active_buckets=0)
        lane = DispatchLane(
            _cfg(floor=2, target=2), state, asyncio.Lock(), fake_dispatch,
            pid_provider=lambda: outputs,
        )
        # Floor=2 overrides the PID's 0.
        assert lane.desired_target() == 2
        out = await lane.step()
        assert set(out) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_pid_provider_none_falls_back_to_static_target(self) -> None:
        # Provider yields None (no live PID signal) -> static config.target.
        state = LaneState(pending=deque(["a", "b", "c", "d"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(
            _cfg(target=2, floor=1), state, asyncio.Lock(), fake_dispatch,
            pid_provider=lambda: None,
        )
        assert lane.desired_target() == 2
        out = await lane.step()
        assert out == ["a", "b"]

    @pytest.mark.asyncio
    async def test_no_pid_provider_preserves_static_behaviour(self) -> None:
        # No provider wired at all -> historical static target behaviour.
        state = LaneState(pending=deque(["a", "b", "c", "d"]))

        async def fake_dispatch(uid: str) -> None:
            pass

        lane = DispatchLane(_cfg(target=3, floor=1), state, asyncio.Lock(), fake_dispatch)
        assert lane.desired_target() == 3
        out = await lane.step()
        assert out == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_pid_target_evaluated_each_tick(self) -> None:
        # The provider is consulted EVERY tick — a dynamic load change retargets
        # the keep-busy loop live (not frozen at construction).
        from general_ludd.controllers.pid import ControllerOutputs

        state = LaneState(pending=deque([f"u{i}" for i in range(10)]))

        async def fake_dispatch(uid: str) -> None:
            pass

        wanted = {"n": 1}

        def provider() -> ControllerOutputs:
            return ControllerOutputs(desired_total_active_buckets=wanted["n"])

        lane = DispatchLane(
            _cfg(target=1, floor=1, max_worktrees=10), state, asyncio.Lock(),
            fake_dispatch, pid_provider=provider,
        )
        first = await lane.step()
        assert len(first) == 1  # PID wanted 1
        wanted["n"] = 4  # load eases; PID now wants 4 total
        second = await lane.step()
        # already 1 running, retarget to 4 -> backfill 3 more.
        assert len(second) == 3
        assert len(state.running) == 4


# --------------------------------------------------------------------------- #
# IntegrateLane — merge, clobber refusal, worktree reclaim                     #
# --------------------------------------------------------------------------- #


class TestIntegrateLane:
    @pytest.mark.asyncio
    async def test_clean_merge_reclaims_and_advances_to_gate(self) -> None:
        unit = CompletedUnit("u1", "/wt/u1")
        state = LaneState(running={"u1"}, completed_awaiting_merge=deque([unit]))

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True, detail="ours")

        lane = IntegrateLane(_cfg(), state, asyncio.Lock(), merge)
        outcome = await lane.step()
        assert outcome is not None and outcome.merged
        assert "u1" not in state.running  # worktree reclaimed
        assert state.merged_awaiting_gate == ["u1"]
        assert state.total_merged == 1

    @pytest.mark.asyncio
    async def test_clobber_refusal_preserves_worktree_and_requeues(self) -> None:
        unit = CompletedUnit("u1", "/wt/u1")
        state = LaneState(running={"u1"}, completed_awaiting_merge=deque([unit]))

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(
                unit_id=u.unit_id, merged=False, clobber_refused=True,
                detail="conflict",
            )

        lane = IntegrateLane(_cfg(), state, asyncio.Lock(), merge)
        outcome = await lane.step()
        assert outcome is not None and outcome.clobber_refused
        # Worktree NOT reclaimed; unit requeued (not in gate queue, not lost).
        assert "u1" in state.running
        assert [u.unit_id for u in state.completed_awaiting_merge] == ["u1"]
        assert state.merged_awaiting_gate == []
        assert state.total_clobbers_refused == 1

    @pytest.mark.asyncio
    async def test_clobber_gives_up_after_max_retries(self) -> None:
        unit = CompletedUnit("u1", "/wt/u1")
        state = LaneState(running={"u1"}, completed_awaiting_merge=deque([unit]))

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(
                unit_id=u.unit_id, merged=False, clobber_refused=True,
            )

        lane = IntegrateLane(
            _cfg(), state, asyncio.Lock(), merge, max_clobber_retries=2,
        )
        # 2 retries keep it requeued; the 3rd attempt drops it from the queue.
        await lane.step()
        await lane.step()
        await lane.step()
        assert [u.unit_id for u in state.completed_awaiting_merge] == []
        assert state.total_clobbers_refused == 3

    @pytest.mark.asyncio
    async def test_merge_exception_requeues_not_lost(self) -> None:
        unit = CompletedUnit("u1", "/wt/u1")
        state = LaneState(running={"u1"}, completed_awaiting_merge=deque([unit]))

        async def merge(u: CompletedUnit) -> MergeOutcome:
            raise RuntimeError("git lock timeout")

        lane = IntegrateLane(_cfg(), state, asyncio.Lock(), merge)
        outcome = await lane.step()
        assert outcome is not None and not outcome.merged
        assert [u.unit_id for u in state.completed_awaiting_merge] == ["u1"]

    @pytest.mark.asyncio
    async def test_idle_returns_none(self) -> None:
        state = LaneState()

        async def merge(u: CompletedUnit) -> MergeOutcome:
            raise AssertionError("should not merge")

        lane = IntegrateLane(_cfg(), state, asyncio.Lock(), merge)
        assert await lane.step() is None


# --------------------------------------------------------------------------- #
# GateLane — debounce, single-flight, commit-on-green                          #
# --------------------------------------------------------------------------- #


class TestGateLane:
    @pytest.mark.asyncio
    async def test_not_due_when_no_merged_work(self) -> None:
        state = LaneState()
        ran = {"n": 0}

        async def gate() -> bool:
            ran["n"] += 1
            return True

        lane = GateLane(_cfg(), state, asyncio.Lock(), gate, clock=lambda: 1000.0)
        assert await lane.step() is None
        assert ran["n"] == 0

    @pytest.mark.asyncio
    async def test_debounce_suppresses_until_window_elapses(self) -> None:
        state = LaneState(merged_awaiting_gate=["u1"], last_gate_epoch=1000.0)
        now = {"t": 1010.0}  # only 10s since last gate, debounce is 30s

        async def gate() -> bool:
            return True

        lane = GateLane(
            _cfg(gate_debounce_s=30.0), state, asyncio.Lock(), gate,
            clock=lambda: now["t"],
        )
        assert await lane.step() is None  # too soon
        now["t"] = 1031.0  # now 31s elapsed
        result = await lane.step()
        assert result is True

    @pytest.mark.asyncio
    async def test_green_clears_covered_snapshot(self) -> None:
        state = LaneState(merged_awaiting_gate=["u1", "u2"], last_gate_epoch=0.0)

        async def gate() -> bool:
            return True

        lane = GateLane(_cfg(gate_debounce_s=0.0), state, asyncio.Lock(), gate,
                        clock=lambda: 100.0)
        assert await lane.step() is True
        assert state.merged_awaiting_gate == []
        assert state.total_gates_green == 1
        assert state.last_gate_epoch == 100.0

    @pytest.mark.asyncio
    async def test_red_keeps_work_for_regate(self) -> None:
        state = LaneState(merged_awaiting_gate=["u1"], last_gate_epoch=0.0)

        async def gate() -> bool:
            return False

        lane = GateLane(_cfg(gate_debounce_s=0.0), state, asyncio.Lock(), gate,
                        clock=lambda: 100.0)
        assert await lane.step() is False
        # Work NOT cleared — re-gated next window.
        assert state.merged_awaiting_gate == ["u1"]
        assert state.total_gates_green == 0
        assert state.total_gates_run == 1

    @pytest.mark.asyncio
    async def test_units_merged_after_snapshot_survive_green(self) -> None:
        # A merge that lands WHILE the gate is in flight is gated by the NEXT
        # run, never silently dropped by this green.
        state = LaneState(merged_awaiting_gate=["u1"], last_gate_epoch=0.0)
        gate_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_gate() -> bool:
            gate_started.set()
            await release.wait()
            return True

        lane = GateLane(_cfg(gate_debounce_s=0.0), state, asyncio.Lock(), slow_gate,
                        clock=lambda: 100.0)
        task = asyncio.create_task(lane.step())
        await gate_started.wait()
        # New work merges mid-flight.
        state.merged_awaiting_gate.append("u2")
        release.set()
        assert await task is True
        # u1 cleared (covered), u2 survives for the next gate.
        assert state.merged_awaiting_gate == ["u2"]

    @pytest.mark.asyncio
    async def test_single_flight_no_overlap(self) -> None:
        state = LaneState(merged_awaiting_gate=["u1"], last_gate_epoch=0.0)
        concurrent = {"max": 0, "cur": 0}
        release = asyncio.Event()

        async def gate() -> bool:
            concurrent["cur"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["cur"])
            await release.wait()
            concurrent["cur"] -= 1
            return True

        lane = GateLane(_cfg(gate_debounce_s=0.0), state, asyncio.Lock(), gate,
                        clock=lambda: 100.0)
        t1 = asyncio.create_task(lane.step())
        await asyncio.sleep(0)
        # Second step while first in flight -> no-op (None), no second gate run.
        second = await lane.step()
        assert second is None
        release.set()
        await t1
        assert concurrent["max"] == 1


# --------------------------------------------------------------------------- #
# PipelineController — start/stop/status/heartbeat/back-pressure               #
# --------------------------------------------------------------------------- #


class TestPipelineController:
    def _controller(self, **kw: object) -> PipelineController:
        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        async def gate() -> bool:
            return True

        return PipelineController(
            _cfg(**kw), dispatch, merge, gate,
        )

    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self) -> None:
        ctrl = self._controller(
            dispatch_interval_s=0.01, integrate_interval_s=0.01,
            gate_poll_interval_s=0.01, heartbeat_interval_s=0.01,
        )
        await ctrl.start()
        await ctrl.start()  # second start is a no-op
        await asyncio.sleep(0.05)
        await ctrl.stop()
        await ctrl.stop()  # second stop is a no-op

    @pytest.mark.asyncio
    async def test_end_to_end_drains_backlog_through_lanes(self) -> None:
        merged: list[str] = []

        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            merged.append(u.unit_id)
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        gated = {"n": 0}

        async def gate() -> bool:
            gated["n"] += 1
            return True

        ctrl = PipelineController(
            _cfg(
                target=2, floor=1, gate_debounce_s=0.0,
                dispatch_interval_s=0.01, integrate_interval_s=0.01,
                gate_poll_interval_s=0.01, heartbeat_interval_s=1.0,
            ),
            dispatch, merge, gate,
        )
        await ctrl.start()
        await ctrl.submit(["a", "b"])
        # Let the dispatch lane pick them up.
        await asyncio.sleep(0.05)
        # Simulate the agents completing and reporting their worktrees.
        await ctrl.report_completed(CompletedUnit("a", "/wt/a"))
        await ctrl.report_completed(CompletedUnit("b", "/wt/b"))
        await asyncio.sleep(0.1)
        await ctrl.stop()
        assert set(merged) == {"a", "b"}
        assert gated["n"] >= 1

    @pytest.mark.asyncio
    async def test_status_snapshot_shape(self) -> None:
        ctrl = self._controller()
        await ctrl.submit(["a", "b", "c"])
        status = await ctrl.status()
        assert status["enabled"] is True
        assert status["pending"] == ["a", "b", "c"]
        assert status["running"] == []
        assert "counters" in status
        assert status["config"]["floor"] == 1
        # Static fallback: no PID provider -> desired_target == config.target.
        assert status["desired_target"] == status["config"]["target"]

    @pytest.mark.asyncio
    async def test_pid_provider_drives_controller_target(self) -> None:
        from general_ludd.controllers.pid import ControllerOutputs

        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        async def gate() -> bool:
            return True

        outputs = ControllerOutputs(desired_total_active_buckets=5)
        ctrl = PipelineController(
            _cfg(target=3, max_worktrees=10), dispatch, merge, gate,
            pid_provider=lambda: outputs,
        )
        status = await ctrl.status()
        # Live PID target (5) overrides the static config.target (3).
        assert status["config"]["target"] == 3
        assert status["desired_target"] == 5

    @pytest.mark.asyncio
    async def test_backpressure_reflects_disk(self) -> None:
        flag = {"ok": True}

        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(), dispatch, merge, gate, disk_ok=lambda: flag["ok"],
        )
        assert ctrl.backpressured() is False
        flag["ok"] = False
        assert ctrl.backpressured() is True

    @pytest.mark.asyncio
    async def test_heartbeat_emitted_to_sink(self) -> None:
        beats = []

        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(), dispatch, merge, gate, heartbeat_sink=beats.append,
        )
        await ctrl.submit(["a"])
        hb = await ctrl.emit_heartbeat()
        assert beats and beats[0] is hb
        assert hb.pending == 1

    @pytest.mark.asyncio
    async def test_heartbeat_sink_error_does_not_kill(self) -> None:
        def bad_sink(hb: object) -> None:
            raise RuntimeError("sink down")

        async def dispatch(uid: str) -> None:
            pass

        async def merge(u: CompletedUnit) -> MergeOutcome:
            return MergeOutcome(unit_id=u.unit_id, merged=True)

        async def gate() -> bool:
            return True

        ctrl = PipelineController(
            _cfg(), dispatch, merge, gate, heartbeat_sink=bad_sink,
        )
        # Should swallow the sink error, not raise.
        await ctrl.emit_heartbeat()
