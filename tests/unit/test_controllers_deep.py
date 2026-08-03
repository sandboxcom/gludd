"""Deep tests for untested controllers: floor, budget guard, saturation,
compaction aggressiveness, merge conflict, bucket allocation."""

from __future__ import annotations

import pytest

from general_ludd.controllers.bucket import allocate_buckets
from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.controllers.compaction_aggressiveness import (
    AccuracySample,
    CompactionAggressivenessController,
)
from general_ludd.controllers.floor import FloorController
from general_ludd.controllers.merge_conflict import (
    ConflictHunk,
    ConflictKind,
    MergeConflictController,
    ResolutionStrategy,
)
from general_ludd.controllers.pid import ControllerOutputs
from general_ludd.controllers.saturation import (
    BackfillAssignment,
    SaturationController,
    SourceCapacity,
)
from general_ludd.rules.engine import RuleAction
from general_ludd.scheduling.scheduler import WorkItem
from general_ludd.schemas.queue import Queue

# ── FloorController ────────────────────────────────────────────────────


class TestFloorController:
    def test_default_floor_is_5(self):
        ctrl = FloorController()
        assert ctrl.floor == 5

    def test_explicit_floor(self):
        ctrl = FloorController(floor=12)
        assert ctrl.floor == 12

    def test_health_defaults_to_100(self):
        ctrl = FloorController()
        assert ctrl.health == 100.0

    def test_get_max_active_at_full_health(self):
        ctrl = FloorController(floor=10)
        assert ctrl.get_max_active() == 10

    def test_get_max_active_zero_below_25_health(self):
        ctrl = FloorController(floor=10)
        ctrl.update_health(20.0)
        assert ctrl.get_max_active() == 0

    def test_get_max_active_halved_below_50_health(self):
        ctrl = FloorController(floor=10)
        ctrl.update_health(40.0)
        assert ctrl.get_max_active() == 5

    def test_health_clamped_to_0_100(self):
        ctrl = FloorController()
        ctrl.update_health(-10.0)
        assert ctrl.health == 0.0
        ctrl.update_health(150.0)
        assert ctrl.health == 100.0

    def test_auto_tune_lowers_floor_on_low_success_rate(self):
        ctrl = FloorController(floor=10)
        result = ctrl.auto_tune(cpu_pct=50.0, memory_pct=50.0, dispatch_success_rate=80.0, queue_depth=5)
        assert result == 8
        assert ctrl.floor == 8

    def test_auto_tune_raises_floor_on_high_queue_depth(self):
        ctrl = FloorController(floor=5)
        result = ctrl.auto_tune(cpu_pct=30.0, memory_pct=30.0, dispatch_success_rate=98.0, queue_depth=25)
        assert result == 7
        assert ctrl.floor == 7

    def test_auto_tune_no_change_in_neutral_zone(self):
        ctrl = FloorController(floor=8)
        result = ctrl.auto_tune(cpu_pct=40.0, memory_pct=40.0, dispatch_success_rate=92.0, queue_depth=10)
        assert result == 8
        assert ctrl.floor == 8

    def test_auto_tune_respects_floor_floor(self):
        ctrl = FloorController(floor=1)
        ctrl.auto_tune(cpu_pct=90.0, memory_pct=90.0, dispatch_success_rate=50.0, queue_depth=50)
        assert ctrl.floor == 1

    def test_auto_tune_respects_ceiling_20(self):
        ctrl = FloorController(floor=19)
        result = ctrl.auto_tune(cpu_pct=10.0, memory_pct=10.0, dispatch_success_rate=99.0, queue_depth=50)
        assert result == 20

    def test_auto_tune_records_history(self):
        ctrl = FloorController(floor=10)
        ctrl.auto_tune(cpu_pct=80.0, memory_pct=60.0, dispatch_success_rate=70.0, queue_depth=3)
        assert len(ctrl.floor_history) == 1
        entry = ctrl.floor_history[0]
        assert entry["floor"] == 8
        assert entry["previous_floor"] == 10
        assert entry["reason"] == "low_success_rate"

    def test_floor_history_returns_copy(self):
        ctrl = FloorController(floor=10)
        ctrl.auto_tune(cpu_pct=60.0, memory_pct=40.0, dispatch_success_rate=85.0, queue_depth=5)
        hist = ctrl.floor_history
        hist.append({})
        assert len(ctrl.floor_history) == 1


# ── RunBudgetGuard ─────────────────────────────────────────────────────


class TestRunBudgetGuard:
    def test_init_default_unlimited(self):
        guard = RunBudgetGuard()
        result = guard.check_run_budget()
        assert result["allowed"] is True
        assert result["total_spend"] == 0.0

    def test_record_spend_increments_total(self):
        guard = RunBudgetGuard()
        guard.record_spend(5.0)
        assert guard.get_total_spend() == 5.0
        guard.record_spend(3.0)
        assert guard.get_total_spend() == 8.0

    def test_record_spend_rejects_negative(self):
        guard = RunBudgetGuard()
        with pytest.raises(ValueError):
            guard.record_spend(-1.0)

    def test_record_spend_rejects_nan(self):
        guard = RunBudgetGuard()
        with pytest.raises(ValueError):
            guard.record_spend(float("nan"))

    def test_record_spend_rejects_inf(self):
        guard = RunBudgetGuard()
        with pytest.raises(ValueError):
            guard.record_spend(float("inf"))

    def test_check_run_budget_blocks_when_exceeded(self):
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(12.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False
        assert result["total_spend"] == 12.0
        assert result["remaining_budget"] == 0.0

    def test_wall_clock_blocks_after_timeout(self, monkeypatch):
        fake_now = 0.0
        monkeypatch.setattr("general_ludd.controllers.budget.time.monotonic", lambda: fake_now)
        guard = RunBudgetGuard(run_timeout_seconds=60.0)
        fake_now = 100.0
        result = guard.check_wall_clock()
        assert result["allowed"] is False

    def test_wall_clock_allows_within_timeout(self):
        guard = RunBudgetGuard(run_timeout_seconds=3600.0)
        result = guard.check_wall_clock()
        assert result["allowed"] is True
        elapsed = result["elapsed_seconds"]
        assert isinstance(elapsed, (int, float))
        assert elapsed <= 3600.0

    def test_per_call_budget_blocks_over_limit(self):
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(10.0)
        assert result["allowed"] is False

    def test_per_call_budget_allows_within_limit(self):
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(3.0)
        assert result["allowed"] is True

    def test_per_call_fails_closed_on_nan(self):
        guard = RunBudgetGuard(per_call_budget_usd=5.0)
        result = guard.check_per_call(float("nan"))
        assert result["allowed"] is False

    def test_check_all_limits_cascades_wall_clock(self, monkeypatch):
        fake_now = 0.0
        monkeypatch.setattr("general_ludd.controllers.budget.time.monotonic", lambda: fake_now)
        guard = RunBudgetGuard(run_budget_usd=100.0, run_timeout_seconds=10.0)
        fake_now = 60.0
        result = guard.check_all_limits()
        assert result["allowed"] is False
        assert "wall-clock timeout" in str(result["reason"])

    def test_check_all_limits_cascades_budget(self):
        guard = RunBudgetGuard(run_budget_usd=10.0)
        guard.record_spend(15.0)
        result = guard.check_all_limits()
        assert result["allowed"] is False

    def test_check_all_limits_cascades_per_call(self):
        guard = RunBudgetGuard(run_budget_usd=100.0, per_call_budget_usd=2.0)
        result = guard.check_all_limits(estimated_cost=5.0)
        assert result["allowed"] is False

    def test_elapsed_seconds_increases_with_time(self, monkeypatch):
        fake_now = 0.0
        monkeypatch.setattr("general_ludd.controllers.budget.time.monotonic", lambda: fake_now)
        guard = RunBudgetGuard()
        assert guard.get_elapsed_seconds() == 0.0
        fake_now = 30.0
        assert guard.get_elapsed_seconds() == 30.0


# ── SaturationController ───────────────────────────────────────────────


class TestSaturationController:
    def test_utilization_zero_target(self):
        assert SaturationController.utilization(5, 0) == 0.0

    def test_utilization_full(self):
        assert SaturationController.utilization(10, 10) == 1.0

    def test_utilization_half(self):
        assert SaturationController.utilization(5, 10) == 0.5

    def test_utilization_no_running(self):
        assert SaturationController.utilization(0, 10) == 0.0

    def test_utilization_over_target_clamped(self):
        assert SaturationController.utilization(15, 10) == 1.0

    def test_plan_backfill_no_headroom(self):
        ctrl = SaturationController()
        backlog = [WorkItem(id="a")]
        result = ctrl.plan_backfill(target=5, running=5, backlog=backlog)
        assert result == []

    def test_plan_backfill_full_headroom(self):
        ctrl = SaturationController()
        items = [WorkItem(id=f"t{i}") for i in range(5)]
        result = ctrl.plan_backfill(target=10, running=5, backlog=items)
        assert len(result) == 5

    def test_plan_backfill_partial_backlog(self):
        ctrl = SaturationController()
        items = [WorkItem(id="a")]
        result = ctrl.plan_backfill(target=10, running=0, backlog=items)
        assert len(result) == 1

    def test_plan_backfill_empty_backlog(self):
        ctrl = SaturationController()
        result = ctrl.plan_backfill(target=10, running=0, backlog=[])
        assert result == []

    def test_plan_backfill_by_source_with_caps(self):
        ctrl = SaturationController()
        caps = [
            SourceCapacity(source_id="gpu-a", capacity=3, running=1),
            SourceCapacity(source_id="gpu-b", capacity=3, running=2),
        ]
        items = [WorkItem(id=f"t{i}") for i in range(10)]
        result = ctrl.plan_backfill_by_source(target=10, running=0, backlog=items, per_source_caps=caps)
        assert len(result.items) == 3  # headroom: 2 + 1 = 3, min(10, 3, 10)
        assert len(result.by_source["gpu-a"]) == 2
        assert len(result.by_source["gpu-b"]) == 1

    def test_plan_backfill_by_source_no_caps(self):
        ctrl = SaturationController()
        items = [WorkItem(id=f"t{i}") for i in range(3)]
        result = ctrl.plan_backfill_by_source(target=5, running=2, backlog=items)
        assert len(result.items) == 3
        assert result.by_source == {}

    def test_source_capacity_headroom(self):
        sc = SourceCapacity(source_id="x", capacity=10, running=7)
        assert sc.headroom == 3

    def test_source_capacity_headroom_never_negative(self):
        sc = SourceCapacity(source_id="x", capacity=5, running=10)
        assert sc.headroom == 0

    def test_backfill_assignment_defaults(self):
        ba = BackfillAssignment()
        assert ba.items == []
        assert ba.by_source == {}


# ── CompactionAggressivenessController ─────────────────────────────────


class TestCompactionAggressivenessController:
    def test_accuracy_sample_rate_none_on_zero_total(self):
        sample = AccuracySample(passed=0, total=0)
        assert sample.rate is None

    def test_accuracy_sample_rate_perfect(self):
        sample = AccuracySample(passed=10, total=10)
        assert sample.rate == 1.0

    def test_accuracy_sample_rate_half(self):
        sample = AccuracySample(passed=5, total=10)
        assert sample.rate == 0.5

    def test_next_level_holds_below_min_samples(self):
        ctrl = CompactionAggressivenessController(min_samples=5)
        sample = AccuracySample(passed=4, total=4)
        result = ctrl.compute(current_level=3, sample=sample)
        assert result == 3

    def test_next_level_climbs_when_accuracy_holds(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=9, total=10)
        result = ctrl.compute(current_level=2, sample=sample)
        assert result == 3

    def test_next_level_backs_off_when_accuracy_drops(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=3, total=10)
        result = ctrl.compute(current_level=3, sample=sample)
        assert result == 2

    def test_next_level_holds_at_max_level(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5, max_level=3)
        sample = AccuracySample(passed=10, total=10)
        result = ctrl.compute(current_level=3, sample=sample)
        assert result == 3

    def test_next_level_floors_at_zero(self):
        ctrl = CompactionAggressivenessController(floor=0.9, min_samples=5)
        sample = AccuracySample(passed=3, total=10)
        result = ctrl.compute(current_level=0, sample=sample)
        assert result == 0

    def test_next_level_clamp_negative_to_zero(self):
        result = CompactionAggressivenessController._clamp(-5, 5)
        assert result == 0

    def test_disable_signaled_true_at_floor_below_accuracy(self):
        ctrl = CompactionAggressivenessController(floor=0.8, min_samples=5)
        sample = AccuracySample(passed=3, total=10)
        assert ctrl.disable_signaled(0, sample) is True

    def test_disable_signaled_false_above_floor(self):
        ctrl = CompactionAggressivenessController(floor=0.8, min_samples=5)
        sample = AccuracySample(passed=9, total=10)
        assert ctrl.disable_signaled(0, sample) is False

    def test_disable_signaled_false_below_min_samples(self):
        ctrl = CompactionAggressivenessController(floor=0.8, min_samples=10)
        sample = AccuracySample(passed=2, total=5)
        assert ctrl.disable_signaled(0, sample) is False

    def test_disable_signaled_false_at_nonzero_level(self):
        ctrl = CompactionAggressivenessController(floor=0.8, min_samples=5)
        sample = AccuracySample(passed=1, total=10)
        assert ctrl.disable_signaled(2, sample) is False


# ── MergeConflictController ────────────────────────────────────────────


class TestMergeConflictController:
    def controller(self):
        return MergeConflictController()

    def test_parse_hunks_empty_for_plain_content(self):
        hunks = self.controller().parse_hunks("hello\nworld\n")
        assert hunks == []

    def test_parse_hunks_single_conflict(self):
        content = "<<<<<<< ours\na = 1\n=======\na = 2\n>>>>>>> theirs\n"
        hunks = self.controller().parse_hunks(content)
        assert len(hunks) == 1
        assert hunks[0].ours == ("a = 1",)
        assert hunks[0].theirs == ("a = 2",)
        assert hunks[0].start_line == 1

    def test_parse_hunks_ignores_unterminated(self):
        content = "<<<<<<< ours\na = 1\n"
        hunks = self.controller().parse_hunks(content)
        assert hunks == []

    def test_parse_hunks_multiple_conflicts(self):
        content = (
            "before\n"
            "<<<<<<< ours\n"
            "x = 1\n"
            "=======\n"
            "x = 2\n"
            ">>>>>>> theirs\n"
            "middle\n"
            "<<<<<<< ours\n"
            "y = 3\n"
            "=======\n"
            "y = 4\n"
            ">>>>>>> theirs\n"
            "after\n"
        )
        hunks = self.controller().parse_hunks(content)
        assert len(hunks) == 2
        assert hunks[0].start_line == 2
        assert hunks[1].start_line == 8

    def test_classify_identical(self):
        hunk = ConflictHunk(ours=("a",), theirs=("a",), start_line=1)
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.IDENTICAL

    def test_classify_add_on_one_side_ours(self):
        hunk = ConflictHunk(ours=("new line",), theirs=(), start_line=1)
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.ADD_ON_ONE_SIDE

    def test_classify_add_on_one_side_theirs(self):
        hunk = ConflictHunk(ours=(), theirs=("new line",), start_line=1)
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.ADD_ON_ONE_SIDE

    def test_classify_whitespace_only(self):
        hunk = ConflictHunk(ours=("  a",), theirs=("a",), start_line=1)
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.WHITESPACE_ONLY

    def test_classify_import_block(self):
        hunk = ConflictHunk(
            ours=("import os", "import sys"),
            theirs=("import os", "from typing import Any"),
            start_line=1,
        )
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.IMPORT_BLOCK

    def test_classify_semantic(self):
        hunk = ConflictHunk(ours=("foo()",), theirs=("bar()",), start_line=1)
        kind = self.controller().classify(hunk)
        assert kind is ConflictKind.SEMANTIC

    def test_resolve_identical_returns_take_either(self):
        hunk = ConflictHunk(ours=("a",), theirs=("a",), start_line=1)
        res = self.controller().resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_EITHER
        assert res.confidence == 1.0
        assert res.resolved_lines == ("a",)

    def test_resolve_add_one_side_take_ours(self):
        hunk = ConflictHunk(ours=("new",), theirs=(), start_line=1)
        res = self.controller().resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_OURS
        assert res.resolved_lines == ("new",)

    def test_resolve_add_one_side_take_theirs(self):
        hunk = ConflictHunk(ours=(), theirs=("new",), start_line=1)
        res = self.controller().resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_THEIRS
        assert res.resolved_lines == ("new",)

    def test_resolve_import_block_take_union(self):
        hunk = ConflictHunk(
            ours=("import os",),
            theirs=("import sys",),
            start_line=1,
        )
        res = self.controller().resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.TAKE_UNION
        assert res.resolved_lines is not None
        assert set(res.resolved_lines) == {"import os", "import sys"}

    def test_resolve_semantic_escalates(self):
        hunk = ConflictHunk(ours=("foo()",), theirs=("bar()",), start_line=1)
        res = self.controller().resolve_hunk(hunk)
        assert res.strategy is ResolutionStrategy.ESCALATE
        assert res.confidence == 0.0
        assert res.resolved_lines is None

    def test_plan_file_auto_resolvable(self):
        ctrl = self.controller()
        content = "<<<<<<< ours\nhello\n=======\nhello\n>>>>>>> theirs\n"
        plan = ctrl.plan_file("/fake/path.py", content)
        assert plan.auto_resolvable is True
        assert plan.escalation_count == 0

    def test_plan_file_not_auto_resolvable_with_semantic(self):
        ctrl = self.controller()
        content = "<<<<<<< ours\nfoo()\n=======\nbar()\n>>>>>>> theirs\n"
        plan = ctrl.plan_file("/fake/path.py", content)
        assert plan.auto_resolvable is False
        assert plan.escalation_count == 1

    def test_union_imports_sorts_and_deduplicates(self):
        result = MergeConflictController._union_imports(
            ("import os", "import sys"),
            ("import sys", "from typing import Any"),
        )
        assert result == ("from typing import Any", "import os", "import sys")

    def test_union_imports_drops_blanks(self):
        result = MergeConflictController._union_imports(
            ("import os", ""),
            ("", "import sys"),
        )
        assert result == ("import os", "import sys")


# ── allocate_buckets ───────────────────────────────────────────────────


class TestAllocateBuckets:
    def test_basic_pid_allocation(self):
        pid = ControllerOutputs(
            desired_total_active_buckets=3,
            desired_active_buckets_by_queue={"ansible": 2, "model": 3},
            throttle_reasons=[],
        )
        queues = [
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=5, hard_cap=10),
            Queue(queue_name="model", resource_profile="ai_heavy", soft_cap=5, hard_cap=10),
        ]
        result = allocate_buckets(pid, [], queues)
        assert result == {"ansible": 2, "model": 3}

    def test_falls_back_to_soft_cap(self):
        pid = ControllerOutputs(
            desired_total_active_buckets=3,
            desired_active_buckets_by_queue={},
            throttle_reasons=[],
        )
        queues = [
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=5, hard_cap=10),
        ]
        result = allocate_buckets(pid, [], queues)
        assert result["ansible"] == 5

    def test_rule_action_reduces_buckets(self):
        pid = ControllerOutputs(
            desired_total_active_buckets=3,
            desired_active_buckets_by_queue={"ansible": 5},
            throttle_reasons=[],
        )
        queues = [
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=5, hard_cap=10),
        ]
        actions = [RuleAction(rule_id="r1", action_type="reduce_buckets", params={"queue": "ansible", "reduction": 3})]
        result = allocate_buckets(pid, actions, queues)
        assert result["ansible"] == 2

    def test_rule_action_reduction_floors_at_one(self):
        pid = ControllerOutputs(
            desired_total_active_buckets=3,
            desired_active_buckets_by_queue={"ansible": 3},
            throttle_reasons=[],
        )
        queues = [
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=5, hard_cap=10),
        ]
        actions = [RuleAction(rule_id="r1", action_type="reduce_buckets", params={"queue": "ansible", "reduction": 10})]
        result = allocate_buckets(pid, actions, queues)
        assert result["ansible"] == 1

    def test_capped_by_hard_cap(self):
        pid = ControllerOutputs(
            desired_total_active_buckets=5,
            desired_active_buckets_by_queue={"ansible": 20},
            throttle_reasons=[],
        )
        queues = [
            Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=3, hard_cap=5),
        ]
        result = allocate_buckets(pid, [], queues)
        assert result["ansible"] == 5


# ── PauseRecord round-trip ─────────────────────────────────────────────


class TestPauseRecord:
    def test_pause_record_defaults(self):
        from general_ludd.controllers.pause_controller import PauseRecord

        rec = PauseRecord(kind="project", target_id="proj-1", paused_at=100.0)
        assert rec.kind == "project"
        assert rec.target_id == "proj-1"
        assert rec.reason == ""
        assert rec.quiesce_status == "none"
        assert rec.quiesce_errors == []

    def test_pause_record_serialize_round_trip(self):
        from general_ludd.controllers.pause_controller import PauseRecord

        rec = PauseRecord(
            kind="model",
            target_id="claude-sonnet",
            paused_at=200.0,
            reason="cost overrun",
            last_state={"tokens": 5000},
        )
        data = rec.model_dump()
        reloaded = PauseRecord.model_validate(data)
        assert reloaded.kind == rec.kind
        assert reloaded.reason == rec.reason
        assert reloaded.last_state == rec.last_state


# ── BudgetManager budget check ─────────────────────────────────────────


class TestBudgetManager:
    def test_constructor_defaults(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager()
        status = mgr.get_status()
        assert status["daily_spend"] == 0.0
        assert status["paused"] is False

    def test_check_todo_budget_allows_within_limit(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(per_todo_limit_usd=10.0)
        result = mgr.check_todo_budget("t1", 5.0)
        assert result["allowed"] is True

    def test_check_todo_budget_blocks_over_limit(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(per_todo_limit_usd=10.0)
        result = mgr.check_todo_budget("t1", 15.0)
        assert result["allowed"] is False

    def test_check_daily_budget_allows_within_limit(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(daily_limit_usd=100.0)
        result = mgr.check_daily_budget(10.0)
        assert result["allowed"] is True
        assert result["charged"] is True

    def test_check_daily_budget_blocks_over_limit(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(daily_limit_usd=50.0)
        result = mgr.check_daily_budget(60.0)
        assert result["allowed"] is False

    def test_daily_budget_cumulative_tracking(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(daily_limit_usd=100.0)
        mgr.check_daily_budget(30.0)
        mgr.check_daily_budget(30.0)
        status = mgr.get_status()
        assert status["daily_spend"] == 60.0
        assert status["daily_pct"] == 60.0

    def test_daily_budget_kill_switch_sticky(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(daily_limit_usd=100.0)
        mgr.check_daily_budget(101.0)
        status = mgr.get_status()
        assert status["paused"] is True
        result = mgr.check_daily_budget(1.0)
        assert result["allowed"] is False
        assert result["reason"] == "budget_exhausted"

    def test_record_spend_reconciles_reservation(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(per_todo_limit_usd=10.0, daily_limit_usd=100.0)
        mgr.check_todo_budget("t1", 5.0)
        mgr.check_daily_budget_reserved("t1", 5.0)
        mgr.record_spend("t1", 4.0)
        status = mgr.get_status()
        assert status["daily_spend"] == 4.0

    def test_release_reservation_rolls_back(self):
        from general_ludd.controllers.budget_manager import BudgetManager

        mgr = BudgetManager(per_todo_limit_usd=10.0, daily_limit_usd=100.0)
        mgr.check_todo_budget("t1", 5.0)
        mgr.check_daily_budget_reserved("t1", 5.0)
        mgr.release_reservation("t1")
        status = mgr.get_status()
        assert status["daily_spend"] == 0.0
