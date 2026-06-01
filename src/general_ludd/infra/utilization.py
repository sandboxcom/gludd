from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ComputeEndpoint:
    endpoint_id: str
    url: str
    model: str = ""
    gpu_type: str = ""
    gpu_count: int = 1
    max_concurrent: int = 4
    current_load: int = 0
    total_requests: int = 0
    total_tokens: int = 0
    cache_hits: int = 0
    last_used: float = 0.0
    active: bool = True

    @property
    def utilization(self) -> float:
        if self.max_concurrent == 0:
            return 0.0
        return self.current_load / self.max_concurrent

    @property
    def cache_hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    @property
    def is_available(self) -> bool:
        return self.active and self.current_load < self.max_concurrent

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - self.current_load)


@dataclass
class TaskRouting:
    task_id: str
    endpoint_id: str
    model: str = ""
    cache_hit: bool = False
    reason: str = ""


class UtilizationTracker:
    def __init__(self) -> None:
        self._endpoints: dict[str, ComputeEndpoint] = {}
        self._task_history: dict[str, str] = {}

    def register_endpoint(self, endpoint_id: str, url: str, model: str = "", **kwargs: Any) -> ComputeEndpoint:
        ep = ComputeEndpoint(endpoint_id=endpoint_id, url=url, model=model, **kwargs)
        self._endpoints[endpoint_id] = ep
        logger.info("Registered compute endpoint %s (%s, model=%s)", endpoint_id, url, model)
        return ep

    def unregister_endpoint(self, endpoint_id: str) -> None:
        ep = self._endpoints.pop(endpoint_id, None)
        if ep:
            ep.active = False

    def list_endpoints(self, active_only: bool = True) -> list[ComputeEndpoint]:
        eps = list(self._endpoints.values())
        if active_only:
            eps = [e for e in eps if e.active]
        return eps

    def get_endpoint(self, endpoint_id: str) -> ComputeEndpoint | None:
        return self._endpoints.get(endpoint_id)

    def route_task(self, task_id: str, model: str = "", prefer_model: bool = True) -> TaskRouting | None:
        candidates = [e for e in self._endpoints.values() if e.is_available]
        if not candidates:
            return None
        if prefer_model and model:
            model_matches = [e for e in candidates if e.model == model]
            if model_matches:
                candidates = model_matches
        cache_candidates = []
        if model:
            cache_candidates = [e for e in candidates if self._would_cache_hit(task_id, e)]
        if cache_candidates:
            ep = min(cache_candidates, key=lambda e: e.utilization)
            ep.current_load += 1
            ep.total_requests += 1
            ep.cache_hits += 1
            ep.last_used = time.time()
            self._task_history[task_id] = ep.endpoint_id
            return TaskRouting(
                task_id=task_id,
                endpoint_id=ep.endpoint_id,
                model=ep.model,
                cache_hit=True,
                reason="cache_hit",
            )
        ep = min(candidates, key=lambda e: e.utilization)
        ep.current_load += 1
        ep.total_requests += 1
        ep.last_used = time.time()
        self._task_history[task_id] = ep.endpoint_id
        return TaskRouting(
            task_id=task_id,
            endpoint_id=ep.endpoint_id,
            model=ep.model,
            cache_hit=False,
            reason="least_utilized",
        )

    def release_task(self, task_id: str) -> None:
        ep_id = self._task_history.pop(task_id, None)
        if ep_id:
            ep = self._endpoints.get(ep_id)
            if ep and ep.current_load > 0:
                ep.current_load -= 1

    def _would_cache_hit(self, task_id: str, endpoint: ComputeEndpoint) -> bool:
        return False

    def get_utilization_report(self) -> dict[str, Any]:
        endpoints = self.list_endpoints(active_only=False)
        total_capacity = sum(e.max_concurrent for e in endpoints if e.active)
        total_load = sum(e.current_load for e in endpoints if e.active)
        overall_util = (total_load / total_capacity * 100) if total_capacity > 0 else 0.0
        return {
            "overall_utilization_pct": overall_util,
            "total_capacity": total_capacity,
            "total_load": total_load,
            "endpoints": [
                {
                    "endpoint_id": e.endpoint_id,
                    "url": e.url,
                    "model": e.model,
                    "utilization_pct": e.utilization * 100,
                    "current_load": e.current_load,
                    "max_concurrent": e.max_concurrent,
                    "available_slots": e.available_slots,
                    "cache_hit_rate": e.cache_hit_rate,
                    "active": e.active,
                }
                for e in endpoints
            ],
        }

    def find_underutilized(self, threshold: float = 0.5) -> list[ComputeEndpoint]:
        return [e for e in self._endpoints.values() if e.active and e.utilization < threshold]

    def suggest_task_assignment(self, count: int = 1) -> list[TaskRouting]:
        suggestions: list[TaskRouting] = []
        for _ in range(count):
            best = None
            best_util = float("inf")
            for e in self._endpoints.values():
                if e.is_available and e.utilization < best_util:
                    best = e
                    best_util = e.utilization
            if best:
                suggestions.append(
                    TaskRouting(
                        task_id="",
                        endpoint_id=best.endpoint_id,
                        model=best.model,
                        reason="suggested_for_utilization",
                    )
                )
            else:
                break
        return suggestions

    def record_tokens(self, endpoint_id: str, token_count: int) -> None:
        ep = self._endpoints.get(endpoint_id)
        if ep:
            ep.total_tokens += token_count
