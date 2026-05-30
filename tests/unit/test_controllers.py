"""Unit tests for load and budget controllers."""

from __future__ import annotations

import pytest

from agentic_harness.controllers.pid import (
    BudgetController,
    ControllerInputs,
    LoadController,
)


class TestLoadController:
    def test_does_not_throttle_when_load_below_cpu_count(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=2.0, logical_cpu_count=4)
        assert ctrl.should_throttle_local_heavy(inputs) is False

    def test_throttles_local_heavy_when_10m_load_exceeds_cpu_count(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=5.0, logical_cpu_count=4)
        assert ctrl.should_throttle_local_heavy(inputs) is True

    def test_does_not_throttle_ai_heavy_for_high_cpu(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=10.0, logical_cpu_count=4)
        assert ctrl.should_throttle_ai_heavy(inputs) is False

    def test_hybrid_queue_gets_partial_load_penalty(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=6.0, logical_cpu_count=4)
        throttled, penalty = ctrl.should_throttle_hybrid(inputs)
        assert throttled is True
        assert penalty > 0

    def test_hybrid_queue_no_penalty_when_load_low(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=2.0, logical_cpu_count=4)
        throttled, penalty = ctrl.should_throttle_hybrid(inputs)
        assert throttled is False
        assert penalty == 0

    def test_evaluate_reduces_buckets_under_load(self):
        ctrl = LoadController(cpu_count=4, default_buckets=5)
        inputs = ControllerInputs(loadavg_10m=5.0, logical_cpu_count=4)
        outputs = ctrl.evaluate(inputs)
        assert outputs.desired_total_active_buckets < 5
        assert len(outputs.throttle_reasons) > 0


class TestLoadControllerEvaluateSnapshot:
    def test_local_heavy_throttles_when_10m_load_exceeds_cpu_count(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot
        from agentic_harness.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snap = LoadSnapshot(
            loadavg_1m=5.0, loadavg_5m=5.0, loadavg_10m=5.0,
            logical_cpu_count=4, cpu_percent=90.0,
            memory_available_percent=40.0, disk_free_percent=60.0,
            active_jobs=8,
        )
        queues = [Queue(queue_name="ansible", resource_profile="local_heavy", soft_cap=5)]
        outputs = ctrl.evaluate_snapshot(snap, queues)
        assert outputs.desired_active_buckets_by_queue["ansible"] < 5
        assert any("ansible" in r for r in outputs.throttle_reasons)

    def test_ai_heavy_does_not_throttle_for_high_cpu(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot
        from agentic_harness.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snap = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=95.0,
            memory_available_percent=40.0, disk_free_percent=60.0,
            active_jobs=8,
        )
        queues = [Queue(queue_name="model", resource_profile="ai_heavy", soft_cap=5)]
        outputs = ctrl.evaluate_snapshot(snap, queues)
        assert outputs.desired_active_buckets_by_queue["model"] == 5

    def test_hybrid_queue_partial_penalty(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot
        from agentic_harness.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snap = LoadSnapshot(
            loadavg_1m=6.0, loadavg_5m=6.0, loadavg_10m=6.0,
            logical_cpu_count=4, cpu_percent=80.0,
            memory_available_percent=50.0, disk_free_percent=60.0,
            active_jobs=6,
        )
        queues = [Queue(queue_name="worker", resource_profile="hybrid", soft_cap=5)]
        outputs = ctrl.evaluate_snapshot(snap, queues)
        buckets = outputs.desired_active_buckets_by_queue["worker"]
        assert 1 <= buckets < 5

    def test_network_heavy_no_cpu_throttle(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot
        from agentic_harness.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snap = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=95.0,
            memory_available_percent=40.0, disk_free_percent=60.0,
            active_jobs=8,
        )
        queues = [Queue(queue_name="dependency", resource_profile="network_heavy", soft_cap=5)]
        outputs = ctrl.evaluate_snapshot(snap, queues)
        assert outputs.desired_active_buckets_by_queue["dependency"] == 5

    def test_low_resource_continues_under_moderate_cpu(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot
        from agentic_harness.schemas.queue import Queue

        ctrl = LoadController(cpu_count=4, default_buckets=5)
        snap = LoadSnapshot(
            loadavg_1m=3.0, loadavg_5m=3.0, loadavg_10m=3.0,
            logical_cpu_count=4, cpu_percent=70.0,
            memory_available_percent=50.0, disk_free_percent=60.0,
            active_jobs=4,
        )
        queues = [Queue(queue_name="git", resource_profile="low_resource", soft_cap=5)]
        outputs = ctrl.evaluate_snapshot(snap, queues)
        assert outputs.desired_active_buckets_by_queue["git"] == 5


class TestBudgetController:
    def test_api_budget_blocks_call_over_200_default(self):
        ctrl = BudgetController(default_run_budget_usd=200.0)
        assert ctrl.check_api_budget(201.0, 300.0) is False

    def test_api_budget_allows_within_budget(self):
        ctrl = BudgetController(default_run_budget_usd=200.0)
        assert ctrl.check_api_budget(50.0, 150.0) is True

    def test_api_budget_blocks_when_remaining_low(self):
        ctrl = BudgetController(default_run_budget_usd=200.0)
        assert ctrl.check_api_budget(100.0, 50.0) is False

    def test_non_api_window_targets_99_percent_linear_burn(self):
        ctrl = BudgetController(
            subscription_window_seconds=18000.0,
            subscription_window_target_percent=99.0,
        )
        result = ctrl.compute_non_api_burn(
            elapsed_seconds=9000.0,
            used_percent=50.0,
        )
        assert result["target_percent"] == pytest.approx(49.5)
        assert result["above_line"] is True

    def test_non_api_window_below_line(self):
        ctrl = BudgetController(
            subscription_window_seconds=18000.0,
            subscription_window_target_percent=99.0,
        )
        result = ctrl.compute_non_api_burn(
            elapsed_seconds=9000.0,
            used_percent=40.0,
        )
        assert result["above_line"] is False

    def test_estimate_call_cost(self):
        ctrl = BudgetController(default_run_budget_usd=200.0)
        cost = ctrl.estimate_call_cost(tokens=1000, cost_per_1k=0.03)
        assert cost == pytest.approx(0.03)

    def test_estimate_call_cost_blocks_large(self):
        ctrl = BudgetController(default_run_budget_usd=200.0)
        cost = ctrl.estimate_call_cost(tokens=10_000_000, cost_per_1k=0.03)
        assert ctrl.check_api_budget(cost, 500.0) is False

    def test_local_model_resource_check_allowed(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot

        ctrl = BudgetController()
        snap = LoadSnapshot(
            loadavg_1m=3.0, loadavg_5m=3.0, loadavg_10m=3.0,
            logical_cpu_count=4, cpu_percent=50.0,
            memory_available_percent=50.0, disk_free_percent=60.0,
            active_jobs=3,
        )
        result = ctrl.check_local_model_resources(snap)
        assert result["allowed"] is True

    def test_local_model_resource_check_blocked(self):
        from agentic_harness.controllers.load_scrape import LoadSnapshot

        ctrl = BudgetController()
        snap = LoadSnapshot(
            loadavg_1m=8.0, loadavg_5m=8.0, loadavg_10m=8.0,
            logical_cpu_count=4, cpu_percent=98.0,
            memory_available_percent=3.0, disk_free_percent=5.0,
            active_jobs=20,
        )
        result = ctrl.check_local_model_resources(snap)
        assert result["allowed"] is False
