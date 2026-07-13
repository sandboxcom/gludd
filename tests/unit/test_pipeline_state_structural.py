"""Structural tests for pipeline/state.py — PipelineConfig, LaneState, and supporting types."""

from __future__ import annotations

from collections import deque

import pytest

from general_ludd.pipeline.state import (
    CompletedUnit,
    Heartbeat,
    LaneState,
    MergeOutcome,
    PipelineConfig,
)


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.enabled is False
        assert cfg.floor == 1
        assert cfg.target == 3
        assert cfg.gate_debounce_s == 30.0
        assert cfg.max_worktrees == 6
        assert cfg.dispatch_interval_s == 0.5
        assert cfg.integrate_interval_s == 0.5
        assert cfg.gate_poll_interval_s == 0.5
        assert cfg.heartbeat_interval_s == 5.0

    def test_custom_values(self):
        cfg = PipelineConfig(enabled=True, floor=5, target=10, max_worktrees=15, gate_debounce_s=60.0)
        assert cfg.enabled is True
        assert cfg.floor == 5
        assert cfg.target == 10
        assert cfg.max_worktrees == 15
        assert cfg.gate_debounce_s == 60.0

    def test_rejects_negative_floor(self):
        with pytest.raises(ValueError, match="floor must be >= 0"):
            PipelineConfig(floor=-1)

    def test_rejects_target_lt_floor(self):
        with pytest.raises(ValueError, match="target must be >= floor"):
            PipelineConfig(floor=5, target=3)

    def test_rejects_max_worktrees_lt_target(self):
        with pytest.raises(ValueError, match="max_worktrees must be >= target"):
            PipelineConfig(target=10, max_worktrees=5)

    def test_rejects_negative_gate_debounce(self):
        with pytest.raises(ValueError, match="gate_debounce_s must be >= 0"):
            PipelineConfig(gate_debounce_s=-1.0)

    def test_frozen(self):
        cfg = PipelineConfig()
        with pytest.raises(AttributeError):
            cfg.floor = 10  # type: ignore[misc]


class TestCompletedUnit:
    def test_minimal(self):
        cu = CompletedUnit(unit_id="u1", worktree_path="/tmp/wt")
        assert cu.unit_id == "u1"
        assert cu.worktree_path == "/tmp/wt"
        assert cu.branch is None

    def test_with_branch(self):
        cu = CompletedUnit(unit_id="u1", worktree_path="/tmp/wt", branch="agent-fix")
        assert cu.branch == "agent-fix"


class TestMergeOutcome:
    def test_merged(self):
        mo = MergeOutcome(unit_id="u1", merged=True, detail="clean merge")
        assert mo.unit_id == "u1"
        assert mo.merged is True
        assert mo.clobber_refused is False
        assert mo.detail == "clean merge"

    def test_clobber_refused(self):
        mo = MergeOutcome(unit_id="u1", merged=False, clobber_refused=True, detail="conflict")
        assert mo.merged is False
        assert mo.clobber_refused is True

    def test_frozen(self):
        mo = MergeOutcome(unit_id="u1", merged=True)
        with pytest.raises(AttributeError):
            mo.merged = False  # type: ignore[misc]


class TestHeartbeat:
    def test_fields(self):
        hb = Heartbeat(
            epoch=1234567890.0,
            running=5,
            pending=3,
            awaiting_merge=2,
            awaiting_gate=1,
            last_gate_epoch=1234567800.0,
            backpressure=False,
        )
        assert hb.epoch == 1234567890.0
        assert hb.running == 5
        assert hb.pending == 3
        assert hb.awaiting_merge == 2
        assert hb.awaiting_gate == 1
        assert hb.backpressure is False

    def test_mutable(self):
        hb = Heartbeat(
            epoch=0.0, running=0, pending=0, awaiting_merge=0,
            awaiting_gate=0, last_gate_epoch=0.0, backpressure=False,
        )
        hb.running = 5
        assert hb.running == 5


class TestLaneState:
    def test_default_state(self):
        state = LaneState()
        assert state.running == set()
        assert isinstance(state.pending, deque)
        assert len(state.pending) == 0
        assert isinstance(state.completed_awaiting_merge, deque)
        assert isinstance(state.merged_awaiting_gate, list)
        assert state.last_gate_epoch == 0.0
        assert state.total_dispatched == 0
        assert state.total_merged == 0
        assert state.total_clobbers_refused == 0
        assert state.total_gates_run == 0
        assert state.total_gates_green == 0

    def test_worktree_count_empty(self):
        state = LaneState()
        assert state.worktree_count() == 0

    def test_worktree_count_with_running(self):
        state = LaneState(running={"a", "b", "c"})
        assert state.worktree_count() == 3

    def test_worktree_count_with_completed(self):
        cu = CompletedUnit(unit_id="u1", worktree_path="/tmp/wt")
        state = LaneState(completed_awaiting_merge=deque([cu, cu]))
        assert state.worktree_count() == 2

    def test_worktree_count_combined(self):
        cu = CompletedUnit(unit_id="u1", worktree_path="/tmp/wt")
        state = LaneState(running={"a", "b"}, completed_awaiting_merge=deque([cu]))
        assert state.worktree_count() == 3

    def test_snapshot_heartbeat(self):
        cu = CompletedUnit(unit_id="u1", worktree_path="/tmp/wt")
        state = LaneState(
            running={"a", "b"},
            pending=deque(["c", "d", "e"]),
            completed_awaiting_merge=deque([cu]),
            merged_awaiting_gate=["f"],
            last_gate_epoch=1000.0,
            total_dispatched=10,
            total_merged=5,
            total_clobbers_refused=1,
            total_gates_run=3,
            total_gates_green=2,
        )
        hb = state.snapshot_heartbeat(backpressure=True)
        assert hb.running == 2
        assert hb.pending == 3
        assert hb.awaiting_merge == 1
        assert hb.awaiting_gate == 1
        assert hb.last_gate_epoch == 1000.0
        assert hb.backpressure is True
