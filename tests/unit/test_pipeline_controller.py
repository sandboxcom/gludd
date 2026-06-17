"""Unit tests for the pure 3-lane PipelineController.

These tests exercise the multitask + merge pipeline modeled over INJECTED
protocols only — no real daemon, git, or clock. Each lane is driven by a fake
that records calls so the test can assert the lane's contract:

  - DispatchLane keeps N workers busy and NEVER drops below the floor.
  - IntegrateLane merges completed units while others still run, and 3-way
    merges (never clobbers) when the merge strategy reports an overlap.
  - GateLane batches snapshot -> gate -> commit while lanes 1 & 2 keep producing.

The single hard invariant under test: across any sequence of ticks — even when
many workers finish at once — ``running >= floor`` is never breached, because a
tick that would drop below floor backfills FIRST.
"""

from __future__ import annotations

import pytest

from general_ludd.orchestration.pipeline_controller import (
    DispatchLane,
    GateLane,
    IntegrateLane,
    PipelineController,
    PipelineState,
)

# --------------------------------------------------------------------------- #
# Fakes implementing the injected protocols.
# --------------------------------------------------------------------------- #


class FakeWorkerPool:
    """A worker-pool double: tracks running ids, dispatches from a backlog.

    ``running()`` returns the authoritative live count. ``dispatch(item)``
    starts a worker and returns its id. ``finish(n)`` simulates n workers
    completing (returning their ids as a completed batch) — the test uses it
    to force "many finish at once".
    """

    def __init__(self) -> None:
        self._next_id = 0
        self.active: list[str] = []
        self.dispatched: list[str] = []

    def running(self) -> int:
        return len(self.active)

    def dispatch(self, item: object) -> str:
        wid = f"w{self._next_id}"
        self._next_id += 1
        self.active.append(wid)
        self.dispatched.append(wid)
        return wid

    def finish(self, n: int) -> list[str]:
        """Complete the n oldest workers; return their ids (the completed units)."""
        done = self.active[:n]
        self.active = self.active[n:]
        return done


class FakeMergeStrategy:
    """A merge double: configurable overlap + records merge calls."""

    def __init__(self, overlapping: set[str] | None = None) -> None:
        self._overlapping = overlapping or set()
        self.three_way_calls: list[str] = []
        self.fast_forward_calls: list[str] = []

    def detect_overlap(self, unit: str) -> bool:
        return unit in self._overlapping

    def three_way_merge(self, unit: str) -> bool:
        self.three_way_calls.append(unit)
        return True

    def fast_forward(self, unit: str) -> bool:
        self.fast_forward_calls.append(unit)
        return True


class FakeGateRunner:
    """A gate double: records snapshot/gate/commit, configurable gate result."""

    def __init__(self, gate_passes: bool = True) -> None:
        self.gate_passes = gate_passes
        self.snapshots = 0
        self.gates = 0
        self.commits: list[list[str]] = []

    def snapshot(self, units: list[str]) -> str:
        self.snapshots += 1
        return f"snap-{self.snapshots}"

    def gate(self, snapshot_id: str) -> bool:
        self.gates += 1
        return self.gate_passes

    def commit(self, snapshot_id: str, units: list[str]) -> str:
        self.commits.append(list(units))
        return f"commit-{len(self.commits)}"


def _controller(
    *,
    floor: int = 3,
    target: int = 5,
    pool: FakeWorkerPool | None = None,
    merge: FakeMergeStrategy | None = None,
    gate: FakeGateRunner | None = None,
) -> tuple[PipelineController, FakeWorkerPool, FakeMergeStrategy, FakeGateRunner]:
    pool = pool or FakeWorkerPool()
    merge = merge or FakeMergeStrategy()
    gate = gate or FakeGateRunner()
    ctrl = PipelineController(
        dispatch=DispatchLane(pool=pool, target=target, floor=floor),
        integrate=IntegrateLane(merge=merge),
        gate=GateLane(runner=gate),
    )
    return ctrl, pool, merge, gate


# --------------------------------------------------------------------------- #
# DispatchLane: backfill, floor, dispatch-ahead.
# --------------------------------------------------------------------------- #


class TestDispatchLane:
    def test_backfills_to_target_from_empty(self):
        pool = FakeWorkerPool()
        lane = DispatchLane(pool=pool, target=5, floor=2)
        result = lane.tick(backlog=list(range(10)))
        assert pool.running() == 5
        assert len(result.dispatched) == 5
        assert result.running == 5
        assert result.below_floor is False

    def test_never_dispatches_above_target(self):
        pool = FakeWorkerPool()
        lane = DispatchLane(pool=pool, target=3, floor=1)
        lane.tick(backlog=list(range(10)))
        lane.tick(backlog=list(range(10)))
        assert pool.running() == 3

    def test_backfill_bounded_by_backlog(self):
        pool = FakeWorkerPool()
        lane = DispatchLane(pool=pool, target=5, floor=2)
        result = lane.tick(backlog=[1, 1])  # only 2 available
        assert len(result.dispatched) == 2
        assert pool.running() == 2

    def test_backfills_after_workers_finish(self):
        pool = FakeWorkerPool()
        lane = DispatchLane(pool=pool, target=5, floor=2)
        lane.tick(backlog=list(range(10)))
        assert pool.running() == 5
        pool.finish(4)  # 4 of 5 complete at once -> running would be 1 (< floor)
        assert pool.running() == 1
        lane.tick(backlog=list(range(10)))
        assert pool.running() == 5  # refilled back to target

    def test_floor_violation_reported_when_backlog_cannot_refill(self):
        pool = FakeWorkerPool()
        lane = DispatchLane(pool=pool, target=5, floor=3)
        lane.tick(backlog=list(range(10)))
        pool.finish(5)  # everything finishes -> running 0, below floor 3
        # Empty backlog -> cannot refill above floor.
        result = lane.tick(backlog=[])
        assert result.below_floor is True
        assert pool.running() == 0


# --------------------------------------------------------------------------- #
# IntegrateLane: merge while others run; 3-way on overlap; reclaim on success.
# --------------------------------------------------------------------------- #


class TestIntegrateLane:
    def test_non_overlapping_unit_fast_forwards(self):
        merge = FakeMergeStrategy(overlapping=set())
        lane = IntegrateLane(merge=merge)
        result = lane.tick(completed=["u1"])
        assert merge.fast_forward_calls == ["u1"]
        assert merge.three_way_calls == []
        assert result.integrated == ["u1"]

    def test_overlapping_unit_three_way_merges_not_clobber(self):
        merge = FakeMergeStrategy(overlapping={"u2"})
        lane = IntegrateLane(merge=merge)
        result = lane.tick(completed=["u2"])
        assert merge.three_way_calls == ["u2"]
        assert merge.fast_forward_calls == []
        assert result.integrated == ["u2"]

    def test_mixed_batch_routes_each_unit_correctly(self):
        merge = FakeMergeStrategy(overlapping={"b"})
        lane = IntegrateLane(merge=merge)
        result = lane.tick(completed=["a", "b", "c"])
        assert merge.fast_forward_calls == ["a", "c"]
        assert merge.three_way_calls == ["b"]
        assert set(result.integrated) == {"a", "b", "c"}

    def test_failed_merge_not_integrated(self):
        class FailingMerge(FakeMergeStrategy):
            def three_way_merge(self, unit: str) -> bool:
                self.three_way_calls.append(unit)
                return False

        merge = FailingMerge(overlapping={"x"})
        lane = IntegrateLane(merge=merge)
        result = lane.tick(completed=["x"])
        assert result.integrated == []
        assert "x" in result.failed


# --------------------------------------------------------------------------- #
# GateLane: batch snapshot -> gate -> commit; does not drain dispatch.
# --------------------------------------------------------------------------- #


class TestGateLane:
    def test_passing_gate_commits_the_batch(self):
        runner = FakeGateRunner(gate_passes=True)
        lane = GateLane(runner=runner)
        result = lane.tick(integrated=["u1", "u2"])
        assert runner.snapshots == 1
        assert runner.gates == 1
        assert runner.commits == [["u1", "u2"]]
        assert result.committed == ["u1", "u2"]

    def test_failing_gate_does_not_commit(self):
        runner = FakeGateRunner(gate_passes=False)
        lane = GateLane(runner=runner)
        result = lane.tick(integrated=["u1"])
        assert runner.gates == 1
        assert runner.commits == []
        assert result.committed == []

    def test_empty_batch_skips_snapshot_and_gate(self):
        runner = FakeGateRunner()
        lane = GateLane(runner=runner)
        result = lane.tick(integrated=[])
        assert runner.snapshots == 0
        assert runner.gates == 0
        assert result.committed == []


# --------------------------------------------------------------------------- #
# PipelineController.tick(): all three lanes advance concurrently.
# --------------------------------------------------------------------------- #


class TestPipelineControllerTick:
    def test_tick_returns_pipeline_state(self):
        ctrl, _pool, _merge, _gate = _controller()
        state = ctrl.tick(backlog=list(range(10)), completed=[], integrated=[])
        assert isinstance(state, PipelineState)
        assert state.running == 5
        assert state.integrated == []
        assert state.committed == []
        assert state.floor_violations == 0

    def test_completed_unit_integrates_while_others_still_run(self):
        ctrl, pool, _merge, _gate = _controller(target=5, floor=2)
        ctrl.tick(backlog=list(range(10)), completed=[], integrated=[])
        assert pool.running() == 5
        done = pool.finish(2)  # 2 finish, 3 still running
        state = ctrl.tick(backlog=list(range(10)), completed=done, integrated=[])
        # The completed units integrated...
        assert set(state.integrated) == set(done)
        # ...while the others were kept running (refilled to target).
        assert state.running == 5

    def test_overlapping_completed_unit_three_way_merges(self):
        merge = FakeMergeStrategy(overlapping={"w0"})
        ctrl, pool, merge, _gate = _controller(merge=merge)
        ctrl.tick(backlog=list(range(10)), completed=[], integrated=[])
        done = pool.finish(1)  # "w0" finishes
        ctrl.tick(backlog=list(range(10)), completed=done, integrated=[])
        assert merge.three_way_calls == ["w0"]
        assert merge.fast_forward_calls == []

    def test_gate_batches_without_draining_dispatch(self):
        ctrl, pool, _merge, gate = _controller(target=5, floor=2)
        ctrl.tick(backlog=list(range(20)), completed=[], integrated=[])
        # A gate runs on an integrated batch while dispatch keeps producing.
        state = ctrl.tick(
            backlog=list(range(20)), completed=[], integrated=["a", "b"]
        )
        assert gate.commits == [["a", "b"]]
        # Dispatch was NOT drained by the gate — still at target.
        assert state.running == 5
        assert pool.running() == 5

    def test_floor_never_breached_when_many_finish_at_once(self):
        # The headline invariant: across ticks, even when nearly all workers
        # finish simultaneously, a tick refills BEFORE settling and running is
        # never observed below floor in the returned state.
        ctrl, pool, _merge, _gate = _controller(target=8, floor=5)
        ctrl.tick(backlog=list(range(50)), completed=[], integrated=[])
        for _ in range(10):
            # Slam: 7 of 8 finish at once.
            done = pool.finish(min(7, pool.running()))
            state = ctrl.tick(
                backlog=list(range(50)), completed=done, integrated=[]
            )
            assert state.running >= 5, f"floor breached: running={state.running}"
            assert state.floor_violations == 0

    def test_floor_violation_counted_when_backlog_exhausted(self):
        # When the backlog cannot refill above floor, the violation is COUNTED
        # (surfaced in state) rather than silently swallowed.
        ctrl, pool, _merge, _gate = _controller(target=5, floor=3)
        ctrl.tick(backlog=list(range(5)), completed=[], integrated=[])
        done = pool.finish(5)  # all finish; backlog now empty
        state = ctrl.tick(backlog=[], completed=done, integrated=[])
        assert state.running == 0
        assert state.floor_violations == 1

    def test_floor_assert_enforced_internally(self):
        # The DispatchLane's floor invariant is an assert: when a refill IS
        # possible it must bring running >= floor, never return below it.
        ctrl, pool, _merge, _gate = _controller(target=6, floor=4)
        ctrl.tick(backlog=list(range(100)), completed=[], integrated=[])
        done = pool.finish(6)
        state = ctrl.tick(backlog=list(range(100)), completed=done, integrated=[])
        assert state.running >= 4
        assert pool.running() >= 4


def test_module_exports():
    from general_ludd.orchestration import pipeline_controller as mod

    for name in (
        "DispatchLane",
        "IntegrateLane",
        "GateLane",
        "PipelineController",
        "PipelineState",
    ):
        assert hasattr(mod, name), name


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
