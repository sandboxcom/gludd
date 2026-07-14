from __future__ import annotations

from collections import deque

import pytest

from general_ludd.pipeline.state import (
    CompletedUnit,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)


class TestPipelineConfigValidation:
    def test_floor_zero_is_valid(self) -> None:
        cfg = PipelineConfig(floor=0)
        assert cfg.floor == 0

    def test_target_equals_floor_is_valid(self) -> None:
        cfg = PipelineConfig(floor=5, target=5)
        assert cfg.target == 5

    def test_max_worktrees_equals_target_is_valid(self) -> None:
        cfg = PipelineConfig(target=5, max_worktrees=5)
        assert cfg.max_worktrees == 5

    def test_gate_debounce_zero_is_valid(self) -> None:
        cfg = PipelineConfig(gate_debounce_s=0.0)
        assert cfg.gate_debounce_s == 0.0

    def test_rejects_negative_floor(self) -> None:
        with pytest.raises(ValueError, match="floor must be >= 0"):
            PipelineConfig(floor=-1)

    def test_rejects_target_below_floor(self) -> None:
        with pytest.raises(ValueError, match="target must be >= floor"):
            PipelineConfig(floor=5, target=4)

    def test_rejects_max_worktrees_below_target(self) -> None:
        with pytest.raises(ValueError, match="max_worktrees must be >= target"):
            PipelineConfig(target=10, max_worktrees=9)

    def test_rejects_negative_gate_debounce(self) -> None:
        with pytest.raises(ValueError, match="gate_debounce_s must be >= 0"):
            PipelineConfig(gate_debounce_s=-0.5)

    def test_config_is_hashable(self) -> None:
        cfg1 = PipelineConfig(floor=3, target=5, max_worktrees=7)
        cfg2 = PipelineConfig(floor=3, target=5, max_worktrees=7)
        assert cfg1 == cfg2


class TestCompletedUnit:
    def test_default_branch_none(self) -> None:
        cu = CompletedUnit(unit_id="task-1", worktree_path="/tmp/wt1")
        assert cu.branch is None

    def test_all_fields_set(self) -> None:
        cu = CompletedUnit(unit_id="task-2", worktree_path="/tmp/wt2", branch="feat/x")
        assert cu.unit_id == "task-2"
        assert cu.worktree_path == "/tmp/wt2"
        assert cu.branch == "feat/x"


class TestMergeOutcome:
    def test_merged_default_clobber(self) -> None:
        mo = MergeOutcome(unit_id="u1", merged=True)
        assert mo.clobber_refused is False
        assert mo.detail == ""

    def test_not_merged_not_clobbered(self) -> None:
        mo = MergeOutcome(unit_id="u2", merged=False, detail="unrelated fail")
        assert mo.clobber_refused is False
        assert mo.detail == "unrelated fail"

    def test_clobber_refused_not_merged(self) -> None:
        mo = MergeOutcome(unit_id="u3", merged=False, clobber_refused=True, detail="diverge")
        assert mo.merged is False
        assert mo.clobber_refused is True


class TestLaneState:
    def test_default_counters_zero(self) -> None:
        state = LaneState()
        assert state.total_dispatched == 0
        assert state.total_merged == 0
        assert state.total_clobbers_refused == 0
        assert state.total_gates_run == 0
        assert state.total_gates_green == 0

    def test_running_is_set(self) -> None:
        state = LaneState(running={"a", "b"})
        assert len(state.running) == 2
        assert "a" in state.running

    def test_pending_is_deque(self) -> None:
        state = LaneState(pending=deque(["x", "y"]))
        assert isinstance(state.pending, deque)
        assert list(state.pending) == ["x", "y"]

    def test_merged_awaiting_gate_is_list(self) -> None:
        state = LaneState(merged_awaiting_gate=["m1", "m2"])
        assert state.merged_awaiting_gate == ["m1", "m2"]

    def test_snapshot_heartbeat_preserves_epoch(self) -> None:
        state = LaneState(last_gate_epoch=9999.0)
        hb = state.snapshot_heartbeat(backpressure=False)
        assert hb.last_gate_epoch == 9999.0

    def test_snapshot_heartbeat_backpressure_true(self) -> None:
        state = LaneState()
        hb = state.snapshot_heartbeat(backpressure=True)
        assert hb.backpressure is True

    def test_counter_mutation(self) -> None:
        state = LaneState()
        state.total_dispatched += 1
        state.total_merged += 2
        assert state.total_dispatched == 1
        assert state.total_merged == 2
