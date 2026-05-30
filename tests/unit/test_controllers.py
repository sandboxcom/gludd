"""Unit tests for load and budget controllers."""

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
