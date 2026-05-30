"""PID controller for load and budget management."""

from __future__ import annotations

from dataclasses import dataclass, field

import psutil


@dataclass
class ControllerInputs:
    loadavg_1m: float = 0.0
    loadavg_5m: float = 0.0
    loadavg_10m: float = 0.0
    logical_cpu_count: int = 1
    cpu_percent: float = 0.0
    memory_available_percent: float = 100.0
    disk_free_percent: float = 100.0
    active_ansible_jobs: int = 0
    active_gunicorn_jobs: int = 0
    queue_depth_by_queue: dict[str, int] = field(default_factory=dict)
    api_budget_remaining: float = 200.0


@dataclass
class ControllerOutputs:
    desired_total_active_buckets: int = 5
    desired_active_buckets_by_queue: dict[str, int] = field(default_factory=dict)
    throttle_reasons: list[str] = field(default_factory=list)
    hard_caps_applied: list[str] = field(default_factory=list)


class LoadController:
    def __init__(
        self,
        cpu_count: int | None = None,
        default_buckets: int = 5,
    ) -> None:
        self.cpu_count = cpu_count or psutil.cpu_count(logical=True) or 1
        self.default_buckets = default_buckets

    def evaluate(self, inputs: ControllerInputs) -> ControllerOutputs:
        outputs = ControllerOutputs()
        load_10m = inputs.loadavg_10m
        if load_10m > self.cpu_count:
            outputs.throttle_reasons.append(
                f"10m load {load_10m:.2f} exceeds cpu count {self.cpu_count}"
            )
            outputs.desired_total_active_buckets = max(1, self.default_buckets // 2)
        else:
            outputs.desired_total_active_buckets = self.default_buckets

        return outputs

    def should_throttle_local_heavy(self, inputs: ControllerInputs) -> bool:
        return inputs.loadavg_10m > self.cpu_count

    def should_throttle_ai_heavy(self, inputs: ControllerInputs) -> bool:
        return False

    def should_throttle_hybrid(self, inputs: ControllerInputs) -> tuple[bool, float]:
        if inputs.loadavg_10m > self.cpu_count:
            penalty = min(1.0, (inputs.loadavg_10m - self.cpu_count) / self.cpu_count)
            return True, penalty
        return False, 0.0


class BudgetController:
    def __init__(
        self,
        default_run_budget_usd: float = 200.0,
        subscription_window_seconds: float = 18000.0,
        subscription_window_target_percent: float = 99.0,
    ) -> None:
        self.default_run_budget_usd = default_run_budget_usd
        self.subscription_window_seconds = subscription_window_seconds
        self.subscription_window_target_percent = subscription_window_target_percent

    def check_api_budget(
        self, estimated_cost: float, budget_remaining: float
    ) -> bool:
        return estimated_cost <= budget_remaining and estimated_cost <= self.default_run_budget_usd

    def compute_non_api_burn(
        self, elapsed_seconds: float, used_percent: float
    ) -> dict[str, float]:
        if self.subscription_window_seconds <= 0:
            return {"burn_percent": 0.0, "target_percent": 0.0, "above_line": False}
        expected_percent = (
            self.subscription_window_target_percent
            * (elapsed_seconds / self.subscription_window_seconds)
        )
        return {
            "burn_percent": used_percent,
            "target_percent": expected_percent,
            "above_line": used_percent > expected_percent,
        }
