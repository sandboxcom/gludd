from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Memory-leak guard (follow-up to P3 470253a): ``_task_history`` maps task_id ->
# endpoint_id and is added to on every ``route_task`` but only removed by a
# matching ``release_task``. A caller that routes a task and never releases it
# (crash, dropped result, missed cleanup) leaks one entry per task forever. Cap
# the dict with FIFO eviction of the oldest task so a long-lived tracker cannot
# grow without bound; an evicted task simply loses its routing record (a later
# release_task for it becomes a harmless no-op, exactly as for an unknown id).
_MAX_TASK_HISTORY = 10_000


@dataclass
class ComputeEndpoint:
    endpoint_id: str
    url: str
    model: str = ""
    gpu_type: str = ""
    gpu_count: int = 1
    gpu_sm_util: float = 0.0
    gpu_mem_util: float = 0.0
    gpu_temp_c: float = 0.0
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
    def __init__(self, max_history: int = 60) -> None:
        self._endpoints: dict[str, ComputeEndpoint] = {}
        # OrderedDict so we can FIFO-evict the oldest routing record when the
        # history exceeds _MAX_TASK_HISTORY (popitem(last=False)). Plain dict
        # assignment/pop semantics are preserved.
        self._task_history: collections.OrderedDict[str, str] = collections.OrderedDict()
        # Per-endpoint GPU metric history: endpoint_id → deque of (timestamp, gpu_sm_util_pct).
        self._gpu_history: dict[str, collections.deque[tuple[float, float]]] = {}
        self._max_gpu_history = max_history

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

    def _record_routing(self, task_id: str, endpoint_id: str) -> None:
        """Record a task->endpoint routing, bounding the history with FIFO eviction.

        Re-inserting an existing task_id moves it to the most-recent end (so a
        re-routed task is not prematurely evicted). Once the history exceeds
        ``_MAX_TASK_HISTORY`` the oldest entry is dropped.
        """
        self._task_history[task_id] = endpoint_id
        self._task_history.move_to_end(task_id)
        while len(self._task_history) > _MAX_TASK_HISTORY:
            self._task_history.popitem(last=False)

    def route_task(
        self,
        task_id: str,
        model: str = "",
        prefer_model: bool = True,
        scheduling_hint: Any | None = None,
    ) -> TaskRouting | None:
        candidates = [e for e in self._endpoints.values() if e.is_available]
        if not candidates:
            return None
        # bill-9: when a scheduling hint specifies a preferred GPU type,
        # filter candidates to those matching the preferred GPU type first.
        if scheduling_hint is not None:
            hint_gpu = getattr(scheduling_hint, "preferred_gpu_type", None)
            if hint_gpu:
                gpu_matches = [
                    e for e in candidates
                    if e.gpu_type and e.gpu_type.lower() == hint_gpu.lower()
                ]
                if gpu_matches:
                    candidates = gpu_matches
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
            self._record_routing(task_id, ep.endpoint_id)
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
        self._record_routing(task_id, ep.endpoint_id)
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

    def update_gpu_metrics(self, endpoint_id: str, metrics: dict[str, float]) -> None:
        ep = self._endpoints.get(endpoint_id)
        if ep is None:
            return
        sm_util = metrics.get("gpu_sm_util_pct")
        mem_util = metrics.get("gpu_mem_util_pct")
        temp_c = metrics.get("gpu_temp_c")
        if sm_util is not None:
            ep.gpu_sm_util = float(sm_util)
        if mem_util is not None:
            ep.gpu_mem_util = float(mem_util)
        if temp_c is not None:
            ep.gpu_temp_c = float(temp_c)
        if sm_util is not None:
            now = time.time()
            hist = self._gpu_history.setdefault(endpoint_id, collections.deque())
            hist.append((now, float(sm_util)))
            while len(hist) > self._max_gpu_history:
                hist.popleft()

    def find_idle_gpus(self, threshold: float = 5.0, window: float = 900.0) -> list[ComputeEndpoint]:
        now = time.time()
        idle: list[ComputeEndpoint] = []
        for ep in self._endpoints.values():
            if not ep.active:
                continue
            if not ep.gpu_type:
                continue
            hist = self._gpu_history.get(ep.endpoint_id)
            if hist is not None:
                window_start = now - window
                recent = [(ts, sm) for ts, sm in hist if ts >= window_start]
                if len(recent) >= 1 and all(sm <= threshold for _ts, sm in recent):
                    idle.append(ep)
                    continue
            if ep.current_load == 0 and ep.last_used > 0 and (now - ep.last_used) >= window:
                idle.append(ep)
        return idle
