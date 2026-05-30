"""PID controller for load and budget management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from agentic_harness.controllers.load_scrape import LoadSnapshot
    from agentic_harness.schemas.queue import Queue


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

    def evaluate_snapshot(
        self,
        snapshot: LoadSnapshot,
        queues: list[Queue],
    ) -> ControllerOutputs:
        from agentic_harness.schemas.todo import ResourceProfile

        outputs = ControllerOutputs()

        for queue in queues:
            profile = ResourceProfile(queue.resource_profile)
            buckets = queue.soft_cap

            if profile == ResourceProfile.LOCAL_HEAVY:
                if snapshot.loadavg_10m >= snapshot.logical_cpu_count:
                    buckets = max(1, buckets // 2)
                    outputs.throttle_reasons.append(
                        f"local_heavy throttle: {queue.queue_name}"
                    )

            elif profile == ResourceProfile.AI_HEAVY:
                pass

            elif profile == ResourceProfile.HYBRID:
                if snapshot.loadavg_10m > snapshot.logical_cpu_count:
                    excess = snapshot.loadavg_10m - snapshot.logical_cpu_count
                    penalty = min(1.0, excess / snapshot.logical_cpu_count)
                    buckets = max(1, int(buckets * (1 - penalty)))
                    outputs.throttle_reasons.append(
                        f"hybrid partial penalty: {queue.queue_name} penalty={penalty:.2f}"
                    )

            elif profile == ResourceProfile.NETWORK_HEAVY:
                pass

            elif profile == ResourceProfile.LOW_RESOURCE and snapshot.loadavg_10m >= snapshot.logical_cpu_count * 1.5:
                buckets = max(1, buckets // 2)
                outputs.throttle_reasons.append(
                    f"low_resource throttle: {queue.queue_name}"
                )

            outputs.desired_active_buckets_by_queue[queue.queue_name] = buckets

        total = sum(outputs.desired_active_buckets_by_queue.values())
        outputs.desired_total_active_buckets = max(1, total)
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

    def estimate_call_cost(self, tokens: int, cost_per_1k: float) -> float:
        return (tokens / 1000.0) * cost_per_1k

    def check_local_model_resources(self, snapshot: LoadSnapshot) -> dict[str, bool | str]:
        blocked = []
        if snapshot.cpu_percent > 95:
            blocked.append("cpu_percent > 95")
        if snapshot.memory_available_percent < 10:
            blocked.append("memory_available < 10%")
        if snapshot.disk_free_percent < 5:
            blocked.append("disk_free < 5%")
        if snapshot.loadavg_10m >= snapshot.logical_cpu_count * 2:
            blocked.append("loadavg_10m >= 2x cpu_count")

        allowed = len(blocked) == 0
        return {
            "allowed": allowed,
            "reasons": "; ".join(blocked) if blocked else "ok",
        }

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
