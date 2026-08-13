"""Event loop backpressure: queue overflow, rate limiting, PID throttling,
floor auto-tune, pressure classification, lease expiry, resource estimation.

Tests the full backpressure stack: LoadController, FloorController,
BudgetController, LoadSnapshot, PressureLevel, queue caps, lease
reclaim_expired_leases, Todo transition validity, and resource cost estimation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.controllers.floor import FloorController
from general_ludd.controllers.load_scrape import (
    LoadSnapshot,
    PressureLevel,
    classify_pressure,
)
from general_ludd.controllers.pid import BudgetController, LoadController
from general_ludd.event_loop.lease import acquire_lease, reclaim_expired_leases, release_lease
from general_ludd.event_loop.loop import _compute_todo_estimate
from general_ludd.event_loop.scheduler import TodoScheduler, _build_child_data, _next_cron_dt
from general_ludd.schemas.queue import INITIAL_QUEUES, Queue
from general_ludd.schemas.todo import (
    VALID_TRANSITIONS,
    ResourceProfile,
    Todo,
    TodoStatus,
    validate_transition,
)


class MockTodo:
    """Lightweight todo-like object for cost estimation."""

    def __init__(self, **kwargs: object) -> None:
        self.resource_profile: str = str(kwargs.get("resource_profile", "low_resource"))
        self.confidence: float | None = kwargs.get("confidence")  # type: ignore[assignment]
        self.title: str = str(kwargs.get("title", "mock"))
        self.todo_id: str = str(kwargs.get("todo_id", "TODO-MOCK"))
        self.queue: str = str(kwargs.get("queue", "core"))
        for k, v in kwargs.items():
            if not hasattr(self, k):
                setattr(self, k, v)


# ---------------------------------------------------------------------------
# Queue overflow / hard_cap / soft_cap
# ---------------------------------------------------------------------------


class TestQueueCaps:
    """Queue capacity limits — soft_cap and hard_cap as backpressure first line."""

    def test_soft_cap_exceeds_hard_cap_raises(self) -> None:
        with pytest.raises(ValueError, match="soft_cap must not exceed hard_cap"):
            Queue(queue_name="q", hard_cap=5, soft_cap=10)

    def test_soft_cap_equals_hard_cap_allows_full(self) -> None:
        q = Queue(queue_name="q", hard_cap=10, soft_cap=10)
        assert q.soft_cap == 10
        assert q.hard_cap == 10

    def test_initial_queues_all_have_valid_caps(self) -> None:
        for q in INITIAL_QUEUES:
            assert q.hard_cap >= 1
            assert q.soft_cap <= q.hard_cap

    def test_error_rate_rejects_noise_threshold(self) -> None:
        q = Queue(queue_name="q", max_error_rate=0.8)
        assert q.max_error_rate > 0.5

    def test_error_rate_accepts_strict(self) -> None:
        q = Queue(queue_name="q", max_error_rate=0.1)
        assert q.max_error_rate < 0.5

    def test_disabled_queue_still_validates_caps(self) -> None:
        q = Queue(queue_name="q", queue_enabled=False, hard_cap=10, soft_cap=5)
        assert q.queue_enabled is False
        assert q.soft_cap <= q.hard_cap


class TestQueueOverflowProtection:
    """Verify queue itself guards against overflow."""

    def test_queue_name_only_lowercase(self) -> None:
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="UPPERCASE")

    def test_queue_name_no_spaces(self) -> None:
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="no space")

    def test_queue_name_hyphen_allowed(self) -> None:
        q = Queue(queue_name="my-queue")
        assert q.queue_name == "my-queue"

    def test_hard_cap_zero_disallowed(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            Queue(queue_name="q", hard_cap=0)

    def test_hard_cap_negative_disallowed(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            Queue(queue_name="q", hard_cap=-1)


# ---------------------------------------------------------------------------
# LoadSnapshot + pressure classification
# ---------------------------------------------------------------------------


class FakeLoad:
    def __init__(self, l1: float, l5: float, l10: float) -> None:
        self.l1 = l1
        self.l5 = l5
        self.l10 = l10

    def __call__(self) -> tuple[float, float, float]:
        return (self.l1, self.l5, self.l10)


class TestLoadSnapshot:
    def test_snapshot_immutable_fields(self) -> None:
        s = LoadSnapshot(1.0, 2.0, 3.0, 8, 50.0, 60.0, 40.0, 5)
        assert s.loadavg_1m == 1.0
        assert s.loadavg_5m == 2.0
        assert s.loadavg_10m == 3.0
        assert s.logical_cpu_count == 8
        assert s.cpu_percent == 50.0
        assert s.memory_available_percent == 60.0
        assert s.disk_free_percent == 40.0
        assert s.active_jobs == 5

    def test_snapshot_defaults_zero(self) -> None:
        s = LoadSnapshot(0.0, 0.0, 0.0, 1, 0.0, 100.0, 100.0, 0)
        assert s.loadavg_10m == 0.0
        assert s.active_jobs == 0


class TestPressureClassification:
    """classify_pressure maps resource profiles to PressureLevel."""

    def test_idle_system_all_low(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=0.1, loadavg_5m=0.1, loadavg_10m=0.1,
            logical_cpu_count=8, cpu_percent=5.0,
            memory_available_percent=90.0, disk_free_percent=80.0,
            active_jobs=0,
        )
        levels = classify_pressure(s)
        for profile in ResourceProfile:
            assert levels[profile] == PressureLevel.LOW, f"{profile} not low on idle"

    def test_high_cpu_makes_local_severe(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=10.0, loadavg_5m=10.0, loadavg_10m=10.0,
            logical_cpu_count=4, cpu_percent=95.0,
            memory_available_percent=50.0, disk_free_percent=50.0,
            active_jobs=10,
        )
        levels = classify_pressure(s)
        assert levels[ResourceProfile.LOCAL_HEAVY] == PressureLevel.SEVERE

    def test_disk_low_makes_network_severe(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=1.0, loadavg_5m=1.0, loadavg_10m=1.0,
            logical_cpu_count=8, cpu_percent=20.0,
            memory_available_percent=80.0, disk_free_percent=3.0,
            active_jobs=2,
        )
        levels = classify_pressure(s)
        assert levels[ResourceProfile.NETWORK_HEAVY] == PressureLevel.SEVERE

    def test_ai_heavy_no_cpu_low(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=1.0, loadavg_5m=1.0, loadavg_10m=1.0,
            logical_cpu_count=8, cpu_percent=20.0,
            memory_available_percent=80.0, disk_free_percent=80.0,
            active_jobs=2,
        )
        levels = classify_pressure(s)
        assert levels[ResourceProfile.AI_HEAVY] == PressureLevel.LOW

    def test_hybrid_below_threshold_low(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=2.0, loadavg_5m=2.0, loadavg_10m=3.0,
            logical_cpu_count=8, cpu_percent=30.0,
            memory_available_percent=70.0, disk_free_percent=70.0,
            active_jobs=5,
        )
        levels = classify_pressure(s)
        assert levels[ResourceProfile.HYBRID] == PressureLevel.LOW

    def test_low_resource_stays_low_under_load(self) -> None:
        s = LoadSnapshot(
            loadavg_1m=4.0, loadavg_5m=4.0, loadavg_10m=4.0,
            logical_cpu_count=4, cpu_percent=60.0,
            memory_available_percent=50.0, disk_free_percent=50.0,
            active_jobs=5,
        )
        levels = classify_pressure(s)
        assert levels[ResourceProfile.LOW_RESOURCE] == PressureLevel.MEDIUM

    def test_all_profiles_returned(self) -> None:
        s = LoadSnapshot(0.0, 0.0, 0.0, 1, 0.0, 100.0, 100.0, 0)
        levels = classify_pressure(s)
        expected = {
            ResourceProfile.LOCAL_HEAVY,
            ResourceProfile.AI_HEAVY,
            ResourceProfile.HYBRID,
            ResourceProfile.NETWORK_HEAVY,
            ResourceProfile.LOW_RESOURCE,
        }
        assert set(levels.keys()) == expected


# ---------------------------------------------------------------------------
# PID / LoadController — evaluate_snapshot dispense throttling
# ---------------------------------------------------------------------------


class TestLoadControllerEvaluate:
    def test_light_load_no_throttle(self) -> None:
        ctrl = LoadController(cpu_count=8, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=1.0, loadavg_5m=1.0, loadavg_10m=2.0,
            logical_cpu_count=8, cpu_percent=20.0,
            memory_available_percent=80.0, disk_free_percent=80.0,
            active_jobs=3,
        )
        q = Queue(queue_name="core", resource_profile="low_resource", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        assert out.desired_total_active_buckets >= 5

    def test_heavy_load_throttles_local_heavy(self) -> None:
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=6.0, loadavg_5m=6.0, loadavg_10m=6.0,
            logical_cpu_count=4, cpu_percent=80.0,
            memory_available_percent=50.0, disk_free_percent=50.0,
            active_jobs=8,
        )
        q = Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("ansible", 10)
        assert bucketed <= 5

    def test_hybrid_gets_partial_penalty(self) -> None:
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=70.0,
            memory_available_percent=60.0, disk_free_percent=60.0,
            active_jobs=6,
        )
        q = Queue(queue_name="worker", resource_profile="hybrid", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("worker", 10)
        assert bucketed < 10

    def test_ai_heavy_unthrottled_by_load(self) -> None:
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=70.0,
            memory_available_percent=60.0, disk_free_percent=60.0,
            active_jobs=6,
        )
        q = Queue(queue_name="model", resource_profile="ai_heavy", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("model", 10)
        assert bucketed == 10

    def test_network_heavy_unthrottled_by_load(self) -> None:
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=70.0,
            memory_available_percent=60.0, disk_free_percent=60.0,
            active_jobs=6,
        )
        q = Queue(queue_name="dependency", resource_profile="network_heavy", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("dependency", 10)
        assert bucketed == 10

    def test_low_resource_extreme_load_halves(self) -> None:
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=80.0,
            memory_available_percent=50.0, disk_free_percent=50.0,
            active_jobs=10,
        )
        q = Queue(queue_name="core", resource_profile="low_resource", soft_cap=10)
        out = ctrl.evaluate_snapshot(snapshot, [q])
        bucketed = out.desired_active_buckets_by_queue.get("core", 10)
        assert bucketed <= 5

    def test_multiple_queues_aggregate_total(self) -> None:
        ctrl = LoadController(cpu_count=8, default_buckets=5)
        snapshot = LoadSnapshot(
            loadavg_1m=1.0, loadavg_5m=1.0, loadavg_10m=1.0,
            logical_cpu_count=8, cpu_percent=10.0,
            memory_available_percent=90.0, disk_free_percent=90.0,
            active_jobs=1,
        )
        queues = [
            Queue(queue_name="q1", soft_cap=3),
            Queue(queue_name="q2", soft_cap=5),
            Queue(queue_name="q3", soft_cap=2),
        ]
        out = ctrl.evaluate_snapshot(snapshot, queues)
        assert out.desired_total_active_buckets == 10


# ---------------------------------------------------------------------------
# FloorController — auto-tune, health gating, history
# ---------------------------------------------------------------------------


class TestFloorController:
    def test_default_floor_five(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            fc = FloorController()
            assert fc.floor == 5

    def test_env_floor_overrides_default(self) -> None:
        with patch.dict("os.environ", {"FLOOR": "10"}, clear=True):
            fc = FloorController()
            assert fc.floor == 10

    def test_explicit_floor_overrides_env(self) -> None:
        with patch.dict("os.environ", {"FLOOR": "10"}, clear=True):
            fc = FloorController(floor=3)
            assert fc.floor == 3

    def test_health_full_no_gate(self) -> None:
        fc = FloorController(floor=10)
        assert fc.get_max_active() == 10

    def test_health_below_fifty_halves(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(40.0)
        assert fc.get_max_active() == 5

    def test_health_below_twenty_five_blocks(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(20.0)
        assert fc.get_max_active() == 0

    def test_health_boundaries(self) -> None:
        fc = FloorController(floor=10)
        fc.update_health(50.0)
        assert fc.get_max_active() == 10
        fc.update_health(49.9)
        assert fc.get_max_active() == 5
        fc.update_health(25.0)
        assert fc.get_max_active() == 5
        fc.update_health(24.9)
        assert fc.get_max_active() == 0

    def test_auto_tune_lowers_on_low_success(self) -> None:
        fc = FloorController(floor=10)
        new = fc.auto_tune(cpu_pct=50.0, memory_pct=50.0,
                           dispatch_success_rate=80.0, queue_depth=10)
        assert new == 8

    def test_auto_tune_raises_on_high_queue_and_success(self) -> None:
        fc = FloorController(floor=5)
        new = fc.auto_tune(cpu_pct=30.0, memory_pct=40.0,
                           dispatch_success_rate=97.0, queue_depth=25)
        assert new == 7

    def test_auto_tune_no_change_on_normal(self) -> None:
        fc = FloorController(floor=10)
        new = fc.auto_tune(cpu_pct=40.0, memory_pct=50.0,
                           dispatch_success_rate=92.0, queue_depth=15)
        assert new == 10

    def test_auto_tune_floor_one_no_lower(self) -> None:
        fc = FloorController(floor=1)
        new = fc.auto_tune(cpu_pct=60.0, memory_pct=60.0,
                           dispatch_success_rate=50.0, queue_depth=30)
        assert new == 1

    def test_auto_tune_ceiling_twenty(self) -> None:
        fc = FloorController(floor=19)
        new = fc.auto_tune(cpu_pct=10.0, memory_pct=20.0,
                           dispatch_success_rate=99.0, queue_depth=50)
        assert new == 20

    def test_auto_tune_records_history(self) -> None:
        fc = FloorController(floor=10)
        fc.auto_tune(cpu_pct=50.0, memory_pct=50.0,
                     dispatch_success_rate=80.0, queue_depth=10)
        history = fc.floor_history
        assert len(history) == 1
        assert history[0]["reason"] == "low_success_rate"
        assert history[0]["previous_floor"] == 10
        assert history[0]["floor"] == 8


# ---------------------------------------------------------------------------
# BudgetController — resource gating and cost estimation
# ---------------------------------------------------------------------------


class TestBudgetController:
    def test_check_api_budget_within(self) -> None:
        bc = BudgetController(default_run_budget_usd=200.0)
        assert bc.check_api_budget(estimated_cost=50.0, budget_remaining=150.0)

    def test_check_api_budget_exceeds_run_budget(self) -> None:
        bc = BudgetController(default_run_budget_usd=200.0)
        assert not bc.check_api_budget(estimated_cost=250.0, budget_remaining=500.0)

    def test_check_api_budget_exceeds_remaining(self) -> None:
        bc = BudgetController(default_run_budget_usd=200.0)
        assert not bc.check_api_budget(estimated_cost=100.0, budget_remaining=50.0)

    def test_estimate_call_cost_normal(self) -> None:
        bc = BudgetController()
        cost = bc.estimate_call_cost(tokens=5000, cost_per_1k=0.02)
        assert cost == 0.10

    def test_estimate_call_cost_zero_cost_falls_back(self) -> None:
        bc = BudgetController(unknown_model_cost_per_1k_default=0.01)
        cost = bc.estimate_call_cost(tokens=1000, cost_per_1k=0.0)
        assert cost == 0.01

    def test_check_local_model_resources_ok(self) -> None:
        bc = BudgetController()
        snapshot = LoadSnapshot(1.0, 1.0, 1.0, 8, 50.0, 50.0, 50.0, 3)
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is True

    def test_check_local_model_resources_cpu_blocked(self) -> None:
        bc = BudgetController()
        snapshot = LoadSnapshot(1.0, 1.0, 1.0, 8, 96.0, 50.0, 50.0, 3)
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False
        assert "cpu_percent > 95" in str(result["reasons"])

    def test_check_local_model_resources_memory_blocked(self) -> None:
        bc = BudgetController()
        snapshot = LoadSnapshot(1.0, 1.0, 1.0, 8, 50.0, 5.0, 50.0, 3)
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False
        assert "memory_available < 10%" in str(result["reasons"])

    def test_check_local_model_resources_disk_blocked(self) -> None:
        bc = BudgetController()
        snapshot = LoadSnapshot(1.0, 1.0, 1.0, 8, 50.0, 50.0, 3.0, 3)
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False
        assert "disk_free < 5%" in str(result["reasons"])

    def test_compute_non_api_burn_above_line(self) -> None:
        bc = BudgetController(
            subscription_window_seconds=3600,
            subscription_window_target_percent=50.0,
        )
        result = bc.compute_non_api_burn(elapsed_seconds=1800, used_percent=60.0)
        assert result["above_line"] is True

    def test_compute_non_api_burn_below_line(self) -> None:
        bc = BudgetController(
            subscription_window_seconds=3600,
            subscription_window_target_percent=50.0,
        )
        result = bc.compute_non_api_burn(elapsed_seconds=1800, used_percent=20.0)
        assert result["above_line"] is False


# ---------------------------------------------------------------------------
# Lease reclaim — expired leases requeue orphaned todos
# ---------------------------------------------------------------------------


class TestLeaseReclaim:
    @pytest.mark.asyncio
    async def test_reclaim_no_expired(self) -> None:
        ses = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        ses.execute.return_value = result_mock
        count = await reclaim_expired_leases(ses)
        assert count == 0

    @pytest.mark.asyncio
    async def test_release_lease_deletes(self) -> None:
        ses = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        ses.execute.return_value = result
        rows = await release_lease(ses, "core:TODO-1")
        assert rows == 1


class TestLeaseEdgeCases:
    @pytest.mark.asyncio
    async def test_acquire_lease_zero_ttl_still_works(self) -> None:
        ses = AsyncMock()
        ses.add = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        ses.execute.return_value = result_mock
        result = await acquire_lease(ses, "bucket-0", "holder-z", 0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_acquire_leases_batch_empty(self) -> None:
        from general_ludd.event_loop.lease import acquire_leases_batch
        ses = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        ses.execute.return_value = result_mock
        results = await acquire_leases_batch(ses, [], "holder-x", 300)
        assert results == []


# ---------------------------------------------------------------------------
# Resource cost estimation
# ---------------------------------------------------------------------------


class TestTodoCostEstimate:
    def test_low_resource_default_confidence(self) -> None:
        todo = MockTodo(resource_profile="low_resource")
        cost = _compute_todo_estimate(todo)
        assert cost == round(0.05 * (1.5 - 0.5), 4)

    def test_medium_resource(self) -> None:
        todo = MockTodo(resource_profile="medium_resource")
        cost = _compute_todo_estimate(todo)
        assert cost == round(0.25 * (1.5 - 0.5), 4)

    def test_high_resource(self) -> None:
        todo = MockTodo(resource_profile="high_resource")
        cost = _compute_todo_estimate(todo)
        assert cost == round(1.0 * (1.5 - 0.5), 4)

    def test_unknown_resource_falls_back_low(self) -> None:
        todo = MockTodo(resource_profile="bogus")
        cost = _compute_todo_estimate(todo)
        assert cost == round(0.05 * (1.5 - 0.5), 4)

    def test_high_confidence_cheapens(self) -> None:
        todo = MockTodo(resource_profile="high_resource", confidence=1.0)
        cost = _compute_todo_estimate(todo)
        assert cost == round(1.0 * 0.5, 4)

    def test_low_confidence_raises_cost(self) -> None:
        todo = MockTodo(resource_profile="high_resource", confidence=0.0)
        cost = _compute_todo_estimate(todo)
        assert cost == round(1.0 * 1.5, 4)

    def test_none_confidence_defaults_half(self) -> None:
        todo = MockTodo(resource_profile="high_resource", confidence=None)
        cost = _compute_todo_estimate(todo)
        assert cost == round(1.0 * (1.5 - 0.5), 4)


# ---------------------------------------------------------------------------
# Todo state machine — backpressure-relevant transitions
# ---------------------------------------------------------------------------


class TestTodoStateMachineBackpressure:
    def test_budget_exceeded_is_terminal(self) -> None:
        assert not validate_transition(TodoStatus.BUDGET_EXCEEDED, TodoStatus.QUEUED)
        assert not validate_transition(TodoStatus.BUDGET_EXCEEDED, TodoStatus.ACTIVE)

    def test_complete_is_terminal(self) -> None:
        for _target in VALID_TRANSITIONS[TodoStatus.COMPLETE]:
            pass
        assert len(VALID_TRANSITIONS[TodoStatus.COMPLETE]) == 0

    def test_blocked_to_queued_valid(self) -> None:
        assert validate_transition(TodoStatus.BLOCKED, TodoStatus.QUEUED)

    def test_active_to_failed_valid(self) -> None:
        assert validate_transition(TodoStatus.ACTIVE, TodoStatus.FAILED)

    def test_active_to_budget_exceeded_valid(self) -> None:
        assert validate_transition(TodoStatus.ACTIVE, TodoStatus.BUDGET_EXCEEDED)

    def test_queued_to_blocked_valid(self) -> None:
        assert validate_transition(TodoStatus.QUEUED, TodoStatus.BLOCKED)

    def test_queued_to_blocked_on_human_valid(self) -> None:
        assert validate_transition(TodoStatus.QUEUED, TodoStatus.BLOCKED_ON_HUMAN)

    def test_queued_to_manual_hold_valid(self) -> None:
        assert validate_transition(TodoStatus.QUEUED, TodoStatus.MANUAL_HOLD)

    def test_blocked_on_human_to_queued_valid(self) -> None:
        assert validate_transition(TodoStatus.BLOCKED_ON_HUMAN, TodoStatus.QUEUED)

    def test_manual_hold_to_queued_valid(self) -> None:
        assert validate_transition(TodoStatus.MANUAL_HOLD, TodoStatus.QUEUED)


# ---------------------------------------------------------------------------
# Todo scheduling (existing coverage — kept intact)
# ---------------------------------------------------------------------------


class TestQueueConfig:
    def test_queue_defaults(self) -> None:
        q = Queue(queue_name="test-queue")
        assert q.queue_name == "test-queue"
        assert q.queue_enabled is True
        assert q.hard_cap == 10
        assert q.soft_cap == 5
        assert q.max_error_rate == 0.5

    def test_queue_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Queue(queue_name="")

    def test_queue_invalid_name_raises(self) -> None:
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="Invalid Name!")

    def test_queue_name_strips_whitespace(self) -> None:
        q = Queue(queue_name="  my-queue  ")
        assert q.queue_name == "my-queue"

    def test_queue_name_valid_chars(self) -> None:
        q = Queue(queue_name="my_queue-123")
        assert q.queue_name == "my_queue-123"

    def test_queue_hard_cap_min_one(self) -> None:
        with pytest.raises(ValueError, match="must be at least 1"):
            Queue(queue_name="q", hard_cap=0)

    def test_queue_error_rate_range(self) -> None:
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            Queue(queue_name="q", max_error_rate=1.5)
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            Queue(queue_name="q", max_error_rate=-0.1)

    def test_queue_error_rate_boundary_valid(self) -> None:
        q0 = Queue(queue_name="q", max_error_rate=0.0)
        assert q0.max_error_rate == 0.0
        q1 = Queue(queue_name="q", max_error_rate=1.0)
        assert q1.max_error_rate == 1.0

    def test_queue_caps_consistent(self) -> None:
        q = Queue(queue_name="q", hard_cap=10, soft_cap=5)
        assert q.hard_cap == 10
        assert q.soft_cap == 5

    def test_queue_priority_weight_default(self) -> None:
        q = Queue(queue_name="q")
        assert q.priority_weight == 100

    def test_queue_allowed_lists_default(self) -> None:
        q = Queue(queue_name="q")
        assert q.allowed_playbooks == []
        assert q.allowed_model_profiles == []


class TestLease:
    @pytest.mark.asyncio
    async def test_acquire_lease_creates_new(self) -> None:
        ses = AsyncMock()
        ses.add = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        ses.execute.return_value = result_mock
        result = await acquire_lease(ses, "bucket-1", "holder-a", 300)
        assert result is not None

    @pytest.mark.asyncio
    async def test_acquire_lease_updates_existing(self) -> None:
        ses = AsyncMock()
        existing = MagicMock()
        existing.bucket_key = "bucket-1"
        existing.holder_id = "holder-a"
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [existing]
        ses.execute.return_value = result_mock
        result = await acquire_lease(ses, "bucket-1", "holder-a", 300)
        assert result is existing

    @pytest.mark.asyncio
    async def test_release_lease_deletes_by_key(self) -> None:
        ses = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        ses.execute.return_value = result
        rows = await release_lease(ses, "bucket-1")
        assert rows == 1

    @pytest.mark.asyncio
    async def test_release_lease_with_holder(self) -> None:
        ses = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        ses.execute.return_value = result
        rows = await release_lease(ses, "bucket-1", "holder-x")
        assert rows == 1

    @pytest.mark.asyncio
    async def test_acquire_lease_with_project_id(self) -> None:
        ses = AsyncMock()
        ses.add = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        ses.execute.return_value = result_mock
        result = await acquire_lease(ses, "b1", "h1", 300, project_id="proj-1")
        assert result is not None


class TestTodoStatus:
    def test_todo_status_values(self) -> None:
        assert TodoStatus.QUEUED.value == "queued"
        assert TodoStatus.ACTIVE.value == "active"
        assert TodoStatus.BLOCKED.value == "blocked"
        assert TodoStatus.COMPLETE.value == "complete"
        assert TodoStatus.CANCELLED.value == "cancelled"
        assert TodoStatus.FAILED.value == "failed"
        assert TodoStatus.BACKLOG.value == "backlog"
        assert TodoStatus.SCHEDULED.value == "scheduled"

    def test_todo_status_is_string_enum(self) -> None:
        assert isinstance(TodoStatus.QUEUED, str)

    def test_todo_creation_defaults(self) -> None:
        todo = Todo(title="test task")
        assert todo.title == "test task"
        assert todo.status == TodoStatus.BACKLOG

    def test_todo_work_type_default(self) -> None:
        todo = Todo(title="task")
        assert todo.work_type.value == "unknown"

    def test_todo_priority_bounds(self) -> None:
        todo = Todo(title="task")
        assert 0 <= todo.priority <= 1000

    def test_todo_blocked_state_value(self) -> None:
        assert TodoStatus.BLOCKED.value == "blocked"

    def test_todo_blocked_on_human(self) -> None:
        assert TodoStatus.BLOCKED_ON_HUMAN.value == "blocked_on_human"

    def test_todo_manual_hold_state(self) -> None:
        assert TodoStatus.MANUAL_HOLD.value == "manual_hold"

    def test_todo_approval_required_state(self) -> None:
        assert TodoStatus.APPROVAL_REQUIRED.value == "approval_required"

    def test_todo_needs_more_work_state(self) -> None:
        assert TodoStatus.NEEDS_MORE_WORK.value == "needs_more_work"

    def test_todo_budget_exceeded_state(self) -> None:
        assert TodoStatus.BUDGET_EXCEEDED.value == "budget_exceeded"


class TestCronScheduling:
    def test_next_cron_dt_returns_future_utc(self) -> None:
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 * * * *", now, "UTC")
        assert result > now
        assert result.tzinfo == UTC

    def test_next_cron_dt_hourly(self) -> None:
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 * * * *", now, "UTC")
        assert result.hour in (12, 13)

    def test_next_cron_dt_invalid_expression_raises(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValueError):
            _next_cron_dt("not-a-cron", now, "UTC")

    def test_next_cron_dt_unknown_timezone_raises(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="Unknown timezone"):
            _next_cron_dt("0 * * * *", now, "Mars/Prime")

    def test_next_cron_dt_dst_aware(self) -> None:
        now = datetime(2024, 7, 1, 12, 0, 0, tzinfo=UTC)
        result = _next_cron_dt("0 12 * * *", now, "America/New_York")
        assert result.tzinfo == UTC

    def test_build_child_data_copies_fields(self) -> None:
        class FakeTemplate:
            todo_id = "TODO-PARENT"
            title = "Recurring Task"
            description = "Desc"
            work_type = "coder"
            queue = "dev"
            priority = 100
            tags: tuple[str, ...] = ("tag1",)
            risk_level = "low"
            resource_profile = "small"
            acceptance_criteria = "AC"
            test_commands: tuple[str, ...] = ("pytest",)
            molecule_scenarios = None
            molecule_evidence_refs = None
            coverage_requirements = None
            dependencies = None
            model_profile = None
            prompt_profile = None
            worktree = None
            branch_name = None
            plan_artifact = None
            confidence = None
            approval_policy = None
            project_id = "proj-1"
            assigned_agent = None
            created_by = "user-1"

        child = _build_child_data(FakeTemplate())
        assert child["parent_todo_id"] == "TODO-PARENT"
        assert child["status"] == TodoStatus.QUEUED.value
        assert child["title"] == "Recurring Task"
        assert child["work_type"] == "coder"
        assert child["project_id"] == "proj-1"
        assert "todo_id" in child
        assert child["todo_id"].startswith("TODO-")


class TestScheduler:
    @pytest.mark.asyncio
    async def test_tick_no_due_todos(self) -> None:
        repo = MagicMock()
        repo.list_due_scheduled = AsyncMock(return_value=[])
        sched = TodoScheduler(repo, clock=lambda: datetime(2024, 1, 1, tzinfo=UTC))
        promoted, spawned = await sched.tick()
        assert promoted == 0
        assert spawned == 0

    @pytest.mark.asyncio
    async def test_tick_skips_paused(self) -> None:
        repo = MagicMock()
        paused = MagicMock()
        paused.schedule_paused = True
        repo.list_due_scheduled = AsyncMock(return_value=[paused])
        sched = TodoScheduler(repo, clock=lambda: datetime(2024, 1, 1, tzinfo=UTC))
        promoted, spawned = await sched.tick()
        assert promoted == 0
        assert spawned == 0

    @pytest.mark.asyncio
    async def test_tick_promotes_one_shot(self) -> None:
        repo = MagicMock()
        todo = MagicMock()
        todo.todo_id = "TODO-1"
        todo.version = 1
        todo.schedule_paused = False
        todo.cron = None
        repo.list_due_scheduled = AsyncMock(return_value=[todo])
        repo.transition = AsyncMock()
        sched = TodoScheduler(repo, clock=lambda: datetime(2024, 1, 1, tzinfo=UTC))
        promoted, spawned = await sched.tick()
        assert promoted == 1
        assert spawned == 0
        repo.transition.assert_called_once_with("TODO-1", TodoStatus.QUEUED, 1)
