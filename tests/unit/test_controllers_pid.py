"""Structural tests for controllers/pid.py — load throttling and budget management."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.controllers.pid import (
    BudgetController,
    ControllerInputs,
    ControllerOutputs,
    LoadController,
)


class TestControllerInputs:
    def test_defaults(self) -> None:
        ci = ControllerInputs()
        assert ci.loadavg_1m == 0.0
        assert ci.logical_cpu_count == 1
        assert ci.memory_available_percent == 100.0
        assert ci.api_budget_remaining == 200.0

    def test_fields_exist(self) -> None:
        ci = ControllerInputs(
            active_ansible_jobs=3,
            queue_depth_by_queue={"q1": 5, "q2": 10},
        )
        assert ci.active_ansible_jobs == 3
        assert ci.queue_depth_by_queue["q1"] == 5


class TestControllerOutputs:
    def test_defaults(self) -> None:
        co = ControllerOutputs()
        assert co.desired_total_active_buckets == 5
        assert co.throttle_reasons == []
        assert co.hard_caps_applied == []

    def test_fields_mutable_defaults_independent(self) -> None:
        co1 = ControllerOutputs()
        co2 = ControllerOutputs()
        co1.throttle_reasons.append("high load")
        assert co2.throttle_reasons == []


class TestLoadControllerConstruction:
    def test_default_constructor(self) -> None:
        lc = LoadController()
        assert lc.cpu_count >= 1
        assert lc.default_buckets == 5

    def test_explicit_cpu_count(self) -> None:
        lc = LoadController(cpu_count=8)
        assert lc.cpu_count == 8

    def test_explicit_default_buckets(self) -> None:
        lc = LoadController(default_buckets=10)
        assert lc.default_buckets == 10


class TestLoadControllerEvaluatePrimitive:
    def test_under_load_no_throttle(self) -> None:
        lc = LoadController(cpu_count=8)
        inputs = ControllerInputs(
            loadavg_10m=4.0,
            logical_cpu_count=8,
        )
        inputs.logical_cpu_count = 8
        outputs = lc.evaluate(inputs)
        assert outputs.desired_total_active_buckets == 5
        assert outputs.throttle_reasons == []

    def test_over_load_throttles(self) -> None:
        lc = LoadController(cpu_count=4)
        inputs = ControllerInputs(
            loadavg_10m=8.0,
            logical_cpu_count=4,
        )
        inputs.logical_cpu_count = 4
        outputs = lc.evaluate(inputs)
        assert outputs.desired_total_active_buckets < 5
        assert len(outputs.throttle_reasons) >= 1

    def test_over_load_min_buckets_one(self) -> None:
        lc = LoadController(cpu_count=1, default_buckets=1)
        inputs = ControllerInputs(
            loadavg_10m=10.0,
            logical_cpu_count=1,
        )
        inputs.logical_cpu_count = 1
        outputs = lc.evaluate(inputs)
        assert outputs.desired_total_active_buckets == 1


class TestLoadControllerEvaluateSnapshot:
    def test_empty_queues(self) -> None:
        lc = LoadController()
        snapshot = MagicMock()
        snapshot.loadavg_10m = 1.0
        snapshot.logical_cpu_count = 8
        outputs = lc.evaluate_snapshot(snapshot, [])
        assert outputs.desired_total_active_buckets == 1

    def test_local_heavy_no_throttle(self) -> None:
        lc = LoadController()
        snapshot = MagicMock()
        snapshot.loadavg_10m = 1.0
        snapshot.logical_cpu_count = 8

        queue = MagicMock()
        queue.queue_name = "heavy-q"
        queue.resource_profile = "local_heavy"
        queue.soft_cap = 4

        outputs = lc.evaluate_snapshot(snapshot, [queue])
        assert outputs.desired_active_buckets_by_queue["heavy-q"] == 4

    def test_local_heavy_throttle(self) -> None:
        lc = LoadController()
        snapshot = MagicMock()
        snapshot.loadavg_10m = 12.0
        snapshot.logical_cpu_count = 8

        queue = MagicMock()
        queue.queue_name = "heavy-q"
        queue.resource_profile = "local_heavy"
        queue.soft_cap = 4

        outputs = lc.evaluate_snapshot(snapshot, [queue])
        assert outputs.desired_active_buckets_by_queue["heavy-q"] < 4
        assert any("local_heavy" in r for r in outputs.throttle_reasons)

    def test_hybrid_partial_penalty(self) -> None:
        lc = LoadController()
        snapshot = MagicMock()
        snapshot.loadavg_10m = 12.0
        snapshot.logical_cpu_count = 8

        queue = MagicMock()
        queue.queue_name = "hybrid-q"
        queue.resource_profile = "hybrid"
        queue.soft_cap = 10

        outputs = lc.evaluate_snapshot(snapshot, [queue])
        assert outputs.desired_active_buckets_by_queue["hybrid-q"] < 10
        assert any("hybrid" in r for r in outputs.throttle_reasons)

    def test_low_resource_throttle_at_extreme(self) -> None:
        lc = LoadController()
        snapshot = MagicMock()
        snapshot.loadavg_10m = 20.0
        snapshot.logical_cpu_count = 8

        queue = MagicMock()
        queue.queue_name = "low-q"
        queue.resource_profile = "low_resource"
        queue.soft_cap = 6

        outputs = lc.evaluate_snapshot(snapshot, [queue])
        assert outputs.desired_active_buckets_by_queue["low-q"] < 6


class TestLoadControllerShouldThrottle:
    def test_local_heavy(self) -> None:
        lc = LoadController(cpu_count=4)
        inputs = ControllerInputs(loadavg_10m=6.0)
        assert lc.should_throttle_local_heavy(inputs) is True

    def test_local_heavy_no_throttle(self) -> None:
        lc = LoadController(cpu_count=8)
        inputs = ControllerInputs(loadavg_10m=4.0)
        assert lc.should_throttle_local_heavy(inputs) is False

    def test_ai_heavy_never_throttles(self) -> None:
        lc = LoadController(cpu_count=1)
        inputs = ControllerInputs(loadavg_10m=100.0)
        assert lc.should_throttle_ai_heavy(inputs) is False

    def test_hybrid_throttle(self) -> None:
        lc = LoadController(cpu_count=4)
        inputs = ControllerInputs(loadavg_10m=6.0)
        should, penalty = lc.should_throttle_hybrid(inputs)
        assert should is True
        assert 0 < penalty <= 1.0

    def test_hybrid_no_throttle(self) -> None:
        lc = LoadController(cpu_count=8)
        inputs = ControllerInputs(loadavg_10m=4.0)
        should, _penalty = lc.should_throttle_hybrid(inputs)
        assert should is False


class TestBudgetControllerConstruction:
    def test_defaults(self) -> None:
        bc = BudgetController()
        assert bc.default_run_budget_usd == 200.0
        assert bc.subscription_window_seconds == 18000.0


class TestBudgetControllerCheckApiBudget:
    def test_within_budget(self) -> None:
        bc = BudgetController()
        assert bc.check_api_budget(10.0, 200.0) is True

    def test_exceeds_remaining(self) -> None:
        bc = BudgetController()
        assert bc.check_api_budget(50.0, 10.0) is False

    def test_exceeds_default_run(self) -> None:
        bc = BudgetController(default_run_budget_usd=50.0)
        assert bc.check_api_budget(100.0, 500.0) is False


class TestBudgetControllerEstimateCost:
    def test_known_model(self) -> None:
        bc = BudgetController()
        cost = bc.estimate_call_cost(5000, 0.01)
        assert cost == pytest.approx(0.05)

    def test_zero_cost_per_1k_falls_back(self) -> None:
        bc = BudgetController()
        cost = bc.estimate_call_cost(5000, 0.0)
        assert cost == pytest.approx(0.05)

    def test_large_token_count(self) -> None:
        bc = BudgetController()
        cost = bc.estimate_call_cost(100000, 0.002)
        assert cost == pytest.approx(0.2)


class TestBudgetControllerCheckLocalModel:
    def test_all_ok(self) -> None:
        bc = BudgetController()
        snapshot = MagicMock()
        snapshot.cpu_percent = 50.0
        snapshot.memory_available_percent = 50.0
        snapshot.disk_free_percent = 50.0
        snapshot.loadavg_10m = 1.0
        snapshot.logical_cpu_count = 8
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is True

    def test_cpu_blocked(self) -> None:
        bc = BudgetController()
        snapshot = MagicMock()
        snapshot.cpu_percent = 98.0
        snapshot.memory_available_percent = 50.0
        snapshot.disk_free_percent = 50.0
        snapshot.loadavg_10m = 1.0
        snapshot.logical_cpu_count = 8
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False
        assert "cpu" in result["reasons"]

    def test_load_blocked(self) -> None:
        bc = BudgetController()
        snapshot = MagicMock()
        snapshot.cpu_percent = 50.0
        snapshot.memory_available_percent = 50.0
        snapshot.disk_free_percent = 50.0
        snapshot.loadavg_10m = 20.0
        snapshot.logical_cpu_count = 8
        result = bc.check_local_model_resources(snapshot)
        assert result["allowed"] is False
        assert "loadavg" in result["reasons"]


class TestBudgetControllerComputeNonApiBurn:
    def test_zero_elapsed(self) -> None:
        bc = BudgetController()
        result = bc.compute_non_api_burn(0.0, 50.0)
        assert result["target_percent"] == 0.0
        assert result["above_line"] is True

    def test_mid_window(self) -> None:
        bc = BudgetController(subscription_window_seconds=3600.0)
        result = bc.compute_non_api_burn(1800.0, 50.0)
        assert result["target_percent"] == pytest.approx(49.5)
        assert result["above_line"] is True

    def test_zero_window(self) -> None:
        bc = BudgetController(subscription_window_seconds=0.0)
        result = bc.compute_non_api_burn(100.0, 50.0)
        assert result["burn_percent"] == 0.0
