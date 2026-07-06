from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Per-model bounded history of recent failure descriptions kept for flakiness
# detection (get_recent_failures / is_flaky). Memory is bounded to this many
# entries per model regardless of how many calls a model accumulates.
_FAILURE_RING_MAXLEN = 50


@dataclass
class ModelUsage:
    model_id: str
    provider: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_cost_usd: float = 0.0
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls

    def record_call(self, input_tokens: int, output_tokens: int, success: bool) -> None:
        self.total_calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        if success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1
        self.total_cost_usd = (
            self.total_input_tokens * self.cost_per_input_token
            + self.total_output_tokens * self.cost_per_output_token
        )


@dataclass
class AgentMetrics:
    agent_id: str
    agent_name: str = ""
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    status: str = "running"
    project: str = ""
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def total_tokens(self) -> int:
        return sum(
            m.total_input_tokens + m.total_output_tokens
            for m in self.model_usage.values()
        )

    @property
    def total_cost_usd(self) -> float:
        return sum(m.total_cost_usd for m in self.model_usage.values())

    def get_or_create_usage(self, model_id: str, **kwargs: Any) -> ModelUsage:
        if model_id not in self.model_usage:
            self.model_usage[model_id] = ModelUsage(model_id=model_id, **kwargs)
        return self.model_usage[model_id]

    def record_model_call(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        **kwargs: Any,
    ) -> None:
        usage = self.get_or_create_usage(model_id, **kwargs)
        usage.record_call(input_tokens, output_tokens, success)
        self.last_activity = time.time()


@dataclass
class CostEstimate:
    total_cost_usd: float = 0.0
    subscription_name: str = ""
    subscription_cost_usd_per_month: float = 0.0
    tokens_per_week: int = 0
    weeks_per_month: float = 4.33
    tokens_used: int = 0

    @property
    def cost_as_pct_of_subscription(self) -> float:
        if self.subscription_cost_usd_per_month == 0:
            return 0.0
        return (self.total_cost_usd / self.subscription_cost_usd_per_month) * 100

    @property
    def tokens_as_pct_of_weekly(self) -> float:
        if self.tokens_per_week == 0:
            return 0.0
        return (self.tokens_used / self.tokens_per_week) * 100

    @property
    def tokens_remaining_this_week(self) -> int:
        return max(0, self.tokens_per_week - self.tokens_used)


class MetricsCollector:
    def __init__(self) -> None:
        self._agents: dict[str, AgentMetrics] = {}
        self._global_model_usage: dict[str, ModelUsage] = {}
        # Bounded per-model ring of recent failure descriptions (most-recent
        # appended last). Keyed by model_profile_id (the same key used in
        # _global_model_usage). Each deque is capped at _FAILURE_RING_MAXLEN so
        # total memory stays bounded no matter how many calls occur.
        self._recent_failures: dict[str, deque[str]] = {}
        self._task_times: dict[str, list[float]] = {}
        self._task_loc: dict[str, list[int]] = {}
        # Per-task-type metrics (audit recommendation #7): tracks tokens,
        # success/failure counts, latency stats, and failure-mode categories
        # keyed by task type string (e.g. "code_generation", "review").
        # Structure: {task_type: {"total": int, "successes": int, "failures": int,
        #   "tokens_in": int, "tokens_out": int, "latencies": [float],
        #   "failure_modes": {error_category: count}, "phases": {phase: [float]}}}
        self._task_type_metrics: dict[str, dict[str, Any]] = {}
        # Model calls are recorded from worker threads (gateway model calls run
        # off the event loop via asyncio.to_thread), so concurrent
        # record_model_call invocations race on the shared dicts above
        # (non-atomic ``+=`` in ModelUsage.record_call, and "dict changed size
        # during iteration" in the readers). This lock serialises every
        # read/mutate of the shared state. It is an RLock, NOT a plain Lock,
        # because several readers compose other locked readers
        # (get_full_report -> list_running_agents -> list_agents, and
        # get_full_report -> get_agent_summary); a plain Lock would self-deadlock
        # on that same-thread re-entry. Mirrors the SpendLimiter lock pattern.
        self._lock = threading.RLock()

    def register_agent(
        self, agent_id: str, agent_name: str = "", project: str = ""
    ) -> AgentMetrics:
        metrics = AgentMetrics(
            agent_id=agent_id, agent_name=agent_name, project=project
        )
        with self._lock:
            self._agents[agent_id] = metrics
        return metrics

    def unregister_agent(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].status = "stopped"

    def record_task_time(self, project_id: str, elapsed_seconds: float) -> None:
        if project_id:
            with self._lock:
                self._task_times.setdefault(project_id, []).append(
                    float(elapsed_seconds)
                )

    def record_task_loc(self, project_id: str, loc_changed: int) -> None:
        if project_id:
            with self._lock:
                self._task_loc.setdefault(project_id, []).append(int(loc_changed))

    def get_agent(self, agent_id: str) -> AgentMetrics | None:
        with self._lock:
            return self._agents.get(agent_id)

    def list_agents(
        self, status: str | None = None, project: str | None = None
    ) -> list[AgentMetrics]:
        with self._lock:
            agents = list(self._agents.values())
        if status:
            agents = [a for a in agents if a.status == status]
        if project:
            agents = [a for a in agents if a.project == project]
        return agents

    def list_running_agents(self) -> list[AgentMetrics]:
        return self.list_agents(status="running")

    def record_model_call(
        self,
        agent_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        error: str | None = None,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.record_model_call(
                    model_id, input_tokens, output_tokens, success, **kwargs
                )
            if model_id not in self._global_model_usage:
                self._global_model_usage[model_id] = ModelUsage(
                    model_id=model_id, **kwargs
                )
            self._global_model_usage[model_id].record_call(
                input_tokens, output_tokens, success
            )
            # Append-on-failure to the bounded per-model failure ring. This is
            # the ONLY behavioral extension of the recording API: cost/token/
            # success accounting above is unchanged. `error` is optional and
            # defaults to a generic marker, so existing callers (which never
            # pass it) are unaffected and successful calls never touch the ring.
            if not success:
                ring = self._recent_failures.get(model_id)
                if ring is None:
                    ring = deque(maxlen=_FAILURE_RING_MAXLEN)
                    self._recent_failures[model_id] = ring
                ring.append(error if error else "unspecified failure")

    def get_recent_failures(
        self, model_profile_id: str, window_calls: int = 10
    ) -> list[str]:
        """Return up to ``window_calls`` recent failure descriptions for a model.

        Most-recent first. Returns an empty list for an unknown model or any
        non-positive window. Never raises — flakiness detection must be safe to
        call from the optimization advisor on the hot path.
        """
        try:
            if window_calls <= 0:
                return []
            with self._lock:
                ring = self._recent_failures.get(model_profile_id)
                if not ring:
                    return []
                # ring holds oldest..newest; reverse for most-recent-first,
                # then cap. Snapshot under the lock so a concurrent append on
                # the worker thread can't mutate the deque mid-read.
                return list(reversed(ring))[:window_calls]
        except Exception:
            return []

    def is_flaky(
        self,
        model_profile_id: str,
        threshold: float = 0.7,
        min_calls: int = 4,
    ) -> bool:
        """True when a model looks flaky: enough calls AND low success rate.

        A model is flaky when it has at least ``min_calls`` total calls AND its
        overall ``success_rate`` is strictly below ``threshold``. We use the
        cumulative ``ModelUsage.success_rate`` (no recent-window rate is cheaply
        available — only the bounded failure ring is retained, not a rolling
        per-call success/fail sequence). Unknown model -> False. Never raises.
        """
        try:
            with self._lock:
                usage = self._global_model_usage.get(model_profile_id)
                if usage is None:
                    return False
                if usage.total_calls < min_calls:
                    return False
                return usage.success_rate < threshold
        except Exception:
            return False

    def get_global_model_usage(self) -> dict[str, ModelUsage]:
        with self._lock:
            return dict(self._global_model_usage)

    def get_cost_estimate(
        self,
        subscription_name: str = "",
        subscription_cost_usd_per_month: float = 0.0,
        tokens_per_week: int = 0,
    ) -> CostEstimate:
        with self._lock:
            total_cost = sum(a.total_cost_usd for a in self._agents.values())
            total_tokens = sum(a.total_tokens for a in self._agents.values())
        return CostEstimate(
            total_cost_usd=total_cost,
            subscription_name=subscription_name,
            subscription_cost_usd_per_month=subscription_cost_usd_per_month,
            tokens_per_week=tokens_per_week,
            tokens_used=total_tokens,
        )

    def get_agent_summary(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return {}
            return {
                "agent_id": agent.agent_id,
                "agent_name": agent.agent_name,
                "status": agent.status,
                "project": agent.project,
                "uptime_seconds": agent.uptime_seconds,
                "total_tokens": agent.total_tokens,
                "total_cost_usd": agent.total_cost_usd,
                "models_used": {
                    mid: {
                        "total_calls": u.total_calls,
                        "successful_calls": u.successful_calls,
                        "failed_calls": u.failed_calls,
                        "success_rate": u.success_rate,
                        "input_tokens": u.total_input_tokens,
                        "output_tokens": u.total_output_tokens,
                        "cost_usd": u.total_cost_usd,
                    }
                    for mid, u in agent.model_usage.items()
                },
            }

    def get_full_report(self) -> dict[str, Any]:
        # RLock re-entry: this composes list_running_agents and
        # get_agent_summary, which each re-acquire the same lock on this thread.
        with self._lock:
            return {
                "total_agents": len(self._agents),
                "running_agents": len(self.list_running_agents()),
                "agents": [self.get_agent_summary(aid) for aid in self._agents],
                "global_model_usage": {
                    mid: {
                        "total_calls": u.total_calls,
                        "success_rate": u.success_rate,
                        "total_cost_usd": u.total_cost_usd,
                    }
                    for mid, u in self._global_model_usage.items()
                },
            }

    def get_cost_by_project(self) -> dict[str, float]:
        project_costs: dict[str, float] = {}
        # Hold the lock across the whole aggregation: agent.total_cost_usd
        # iterates agent.model_usage, which record_model_call mutates under this
        # same RLock. Summing outside the lock (snapshot of the dict only) still
        # races the per-agent model_usage mutation → "dict changed size during
        # iteration". RLock is re-entrant so nested locked reads are safe.
        with self._lock:
            for agent in self._agents.values():
                if agent.project:
                    project_costs[agent.project] = (
                        project_costs.get(agent.project, 0.0)
                        + agent.total_cost_usd
                    )
        return project_costs

    def get_time_by_project(self) -> dict[str, float]:
        with self._lock:
            return {
                pid: sum(times) for pid, times in self._task_times.items()
            }

    def get_loc_by_project(self) -> dict[str, int]:
        with self._lock:
            return {
                pid: sum(locs) for pid, locs in self._task_loc.items()
            }

    def get_project_accounting_summary(
        self,
    ) -> dict[str, dict[str, Any]]:
        with self._lock:
            _merge_cost: dict[str, float] = {}
            for agent in self._agents.values():
                if agent.project:
                    _merge_cost[agent.project] = (
                        _merge_cost.get(agent.project, 0.0)
                        + agent.total_cost_usd
                    )
            time_: dict[str, float] = {
                pid: sum(times) for pid, times in self._task_times.items()
            }
            loc: dict[str, int] = {
                pid: sum(locs) for pid, locs in self._task_loc.items()
            }
            all_projects: set[str] = set(_merge_cost) | set(time_) | set(loc)
            return {
                pid: {
                    "cost_usd": _merge_cost.get(pid, 0.0),
                    "elapsed_seconds": time_.get(pid, 0.0),
                    "loc_changed": loc.get(pid, 0),
                }
                for pid in all_projects
            }

    # ------------------------------------------------------------------
    # Per-task-type metrics (audit recommendation #7)
    # ------------------------------------------------------------------

    def record_task_result(
        self,
        task_type: str,
        success: bool,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
        error: str | None = None,
        phase_latency_ms: dict[str, float] | None = None,
    ) -> None:
        """Record a completed task's outcome, token usage, and timing.

        ``task_type`` is a freeform category (e.g. "code_generation",
        "review", "game_building"). ``error``, when present, is classified
        by its first word (e.g. "ImportError", "SyntaxError") and tallied
        in ``failure_modes`` so operators can see which errors are most
        common without grepping logs.
        """
        with self._lock:
            entry = self._task_type_metrics.get(task_type)
            if entry is None:
                entry = {
                    "total": 0,
                    "successes": 0,
                    "failures": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "latencies": [],
                    "failure_modes": {},
                    "phases": {},
                }
                self._task_type_metrics[task_type] = entry

            entry["total"] += 1
            entry["tokens_in"] += int(tokens_in)
            entry["tokens_out"] += int(tokens_out)
            entry["latencies"].append(float(latency_ms))

            if success:
                entry["successes"] += 1
            else:
                entry["failures"] += 1
                if error:
                    category = error.split(":")[0].split(" ")[0].strip()
                    entry["failure_modes"][category] = (
                        entry["failure_modes"].get(category, 0) + 1
                    )

            if phase_latency_ms:
                for phase, ms in phase_latency_ms.items():
                    entry["phases"].setdefault(phase, []).append(float(ms))

    def get_task_type_summary(self) -> dict[str, dict[str, Any]]:
        """Return per-task-type aggregate stats suitable for JSON export.

        Returns an empty dict when no task results have been recorded.
        """
        with self._lock:
            summary: dict[str, dict[str, Any]] = {}
            for task_type, entry in self._task_type_metrics.items():
                latencies = entry["latencies"]
                total = entry["total"]
                result: dict[str, Any] = {
                    "total": total,
                    "successes": entry["successes"],
                    "failures": entry["failures"],
                    "success_rate": (
                        entry["successes"] / total if total > 0 else 0.0
                    ),
                    "tokens_in": entry["tokens_in"],
                    "tokens_out": entry["tokens_out"],
                    "latency_total_ms": sum(latencies) if latencies else 0.0,
                    "latency_avg_ms": (
                        sum(latencies) / len(latencies) if latencies else 0.0
                    ),
                    "latency_min_ms": min(latencies) if latencies else 0.0,
                    "latency_max_ms": max(latencies) if latencies else 0.0,
                    "failure_modes": dict(entry["failure_modes"]),
                }
                # Per-phase averages
                phase_avgs: dict[str, float] = {}
                for phase, vals in entry["phases"].items():
                    if vals:
                        phase_avgs[phase] = sum(vals) / len(vals)
                result["phase_avg_ms"] = phase_avgs
                summary[task_type] = result
            return summary

    def reset_task_metrics(self) -> None:
        """Clear all per-task-type counters and latencies."""
        with self._lock:
            self._task_type_metrics.clear()
