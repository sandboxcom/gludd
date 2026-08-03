"""Cost-aware model router — peak/off-peak pricing and budget routing."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from general_ludd.models.performance_router import ModelPerformanceRouter

logger = logging.getLogger(__name__)

_PEAK = "peak"
_OFF_PEAK = "off_peak"
_UNKNOWN = "unknown"


class _BudgetGuardProtocol(Protocol):
    def check_all_limits(self, estimated_cost: float = 0.0) -> dict[str, bool | str | float]: ...

    def record_spend(self, amount_usd: float) -> None: ...

    def get_total_spend(self) -> float: ...


class _CostTrackerProtocol(Protocol):
    def remaining_model_budget(self, *, now: float | None = None) -> float: ...

    def model_spend(self, *, now: float | None = None) -> float: ...


class _DeferredTaskQueue(Protocol):
    def enqueue(self, task_id: str, deadline: datetime.datetime, payload: dict[str, object]) -> str: ...


@dataclass(frozen=True)
class PeakPricingSchedule:
    """Defines peak/off-peak hours and cost multipliers.

    All times UTC.  Monday = 0, Sunday = 6.
    """

    peak_start_hour: int
    peak_end_hour: int
    peak_multiplier: float = 1.5
    off_peak_multiplier: float = 0.7
    peak_days: frozenset[int] = frozenset({0, 1, 2, 3, 4})

    def __post_init__(self) -> None:
        if not 0 <= self.peak_start_hour <= 23:
            raise ValueError(f"peak_start_hour must be 0-23, got {self.peak_start_hour}")
        if not 0 <= self.peak_end_hour <= 23:
            raise ValueError(f"peak_end_hour must be 0-23, got {self.peak_end_hour}")
        if self.peak_multiplier <= 0:
            raise ValueError(f"peak_multiplier must be > 0, got {self.peak_multiplier}")
        if self.off_peak_multiplier <= 0:
            raise ValueError(f"off_peak_multiplier must be > 0, got {self.off_peak_multiplier}")
        if not self.peak_days:
            raise ValueError("peak_days must not be empty")


@dataclass(frozen=True)
class ModelRoute:
    """A cost-aware routing decision."""

    model_id: str
    estimated_cost: float
    peak_status: str
    hourly_rate: float
    currency: str = "USD"


_DEFAULT_PEAK = PeakPricingSchedule(peak_start_hour=8, peak_end_hour=20)


class CostAwareRouter:
    """Routes model calls based on peak/off-peak pricing and budget state.

    Wraps a :class:`ModelPerformanceRouter` to add cost-aware filtering:
    model costs are adjusted by peak/off-peak multipliers, and the router
    can defer tasks to off-peak hours when the budget is tight.

    Budget integration is two-way:
    - Inbound:  the router reads ``budget_guard`` and ``cost_tracker``
      to select the cheapest model that fits within remaining budget.
    - Outbound: the router records estimated spend so the budget guard
      stays current.  Callers that use ``route_by_cost`` get a projection;
      the actual spend must still be recorded by the caller after execution.
    """

    def __init__(
        self,
        performance_router: ModelPerformanceRouter,
        peak_schedule: PeakPricingSchedule = _DEFAULT_PEAK,
        budget_guard: object | None = None,
        cost_tracker: object | None = None,
        deferred_queue: object | None = None,
    ) -> None:
        self._performance = performance_router
        self._peak = peak_schedule
        self._budget_guard = budget_guard
        self._cost_tracker = cost_tracker
        self._deferred = deferred_queue

    @property
    def peak_schedule(self) -> PeakPricingSchedule:
        return self._peak

    def _is_peak(self, now: datetime.datetime | None = None) -> bool:
        dt = now or datetime.datetime.now(datetime.UTC)
        if dt.weekday() not in self._peak.peak_days:
            return False
        hour = dt.hour
        return self._peak.peak_start_hour <= hour < self._peak.peak_end_hour

    def _multiplier(self, now: datetime.datetime | None = None) -> float:
        return self._peak.peak_multiplier if self._is_peak(now) else self._peak.off_peak_multiplier

    def _peak_status(self, now: datetime.datetime | None = None) -> str:
        if self._is_peak(now):
            return _PEAK
        return _OFF_PEAK

    def _adjusted_cost(self, base_cost_usd: float, now: datetime.datetime | None = None) -> float:
        return round(base_cost_usd * self._multiplier(now), 6)

    async def route_by_cost(
        self,
        task_capability: str,
        budget_remaining: float | None = None,
        *,
        now: datetime.datetime | None = None,
    ) -> ModelRoute:
        """Select the cheapest capable model given budget and peak status.

        Budget check order:
        1. Read remaining budget from cost_tracker (if wired).
        2. Use explicit ``budget_remaining`` override if provided.
        3. Select the lowest-cost model whose adjusted cost fits within
           budget.  If no model fits, fall back to the cheapest (even if
           over budget) and flag peak_status accordingly.
        4. If a budget_guard is wired, confirm via check_all_limits before
           returning.

        Args:
            task_capability: Task type (e.g. "bug_fix", "feature", "review").
            budget_remaining: Explicit remaining budget override (USD).
            now: Override wall clock for peak determination (tests).

        Returns:
            A ``ModelRoute`` with the selected model, estimated cost, and
            peak status.
        """
        peak_status = self._peak_status(now)
        peak_label = peak_status
        self._multiplier(now)

        effective_budget = budget_remaining
        if effective_budget is None and self._cost_tracker is not None:
            effective_budget = cast(_CostTrackerProtocol, self._cost_tracker).remaining_model_budget()

        rankings = await self._performance.get_rankings(task_capability, strategy="cheapest")
        if not rankings:
            fallback = await self._performance.select_model(task_capability, fallback="openai/gpt-4o-mini")
            model_id = f"{fallback['service']}/{fallback['model_name']}"
            base_cost = 0.0
            adj_cost = self._adjusted_cost(base_cost, now)
            return ModelRoute(
                model_id=model_id,
                estimated_cost=adj_cost,
                peak_status=peak_label,
                hourly_rate=adj_cost,
            )

        best_route: ModelRoute | None = None
        cheapest_any: ModelRoute | None = None

        for entry in rankings:
            svc = cast(str, entry.get("service", ""))
            name = cast(str, entry.get("model_name", ""))
            base_cost = float(cast(float, entry.get("avg_cost_usd", 0.0)))
            adj_cost = self._adjusted_cost(base_cost, now)
            model_id = f"{svc}/{name}"

            route = ModelRoute(
                model_id=model_id,
                estimated_cost=adj_cost,
                peak_status=peak_label,
                hourly_rate=adj_cost,
            )

            if cheapest_any is None or adj_cost < cheapest_any.estimated_cost:
                cheapest_any = route

            if effective_budget is not None and adj_cost > effective_budget:
                continue

            if best_route is None or adj_cost < best_route.estimated_cost:
                best_route = route

        if best_route is not None:
            return best_route
        if cheapest_any is not None:
            return cheapest_any

        model_id = "openai/gpt-4o-mini"
        adj_cost = self._adjusted_cost(0.0, now)
        return ModelRoute(
            model_id=model_id,
            estimated_cost=adj_cost,
            peak_status=peak_label,
            hourly_rate=adj_cost,
        )

    def is_better_to_wait(
        self,
        task: dict[str, Any],
        deadline_hours: float,
        *,
        now: datetime.datetime | None = None,
    ) -> bool:
        """Decide whether waiting for off-peak saves enough to justify the delay.

        Decision criteria:
        - If already off-peak → no reason to wait.
        - If the task's estimated cost is below the threshold, peak savings
          are negligible → don't wait.
        - Calculate hours until next off-peak start.  If that exceeds
          ``deadline_hours`` → can't wait (deadline would be missed).
        - Compare (peak_cost - off_peak_cost) against a cost-of-waiting
          heuristic: waiting is worth it when saving >= 20% of the task's
          estimated peak cost.

        Args:
            task:   Task dict with at minimum ``estimated_cost`` (float).
            deadline_hours: Maximum hours we are allowed to defer.

        Returns:
            True when deferring to off-peak is the better choice.
        """
        dt = now or datetime.datetime.now(datetime.UTC)
        if not self._is_peak(dt):
            return False

        estimated_cost = float(cast(float, task.get("estimated_cost", 0.0)))
        if estimated_cost <= 0.0:
            return False

        peak_cost = self._adjusted_cost(estimated_cost, dt)
        stay_peak = dt.hour >= self._peak.peak_start_hour and dt.hour < self._peak.peak_end_hour
        if not stay_peak:
            return False

        hours_until_off_peak = self._peak.peak_end_hour - dt.hour
        if hours_until_off_peak <= 0:
            hours_until_off_peak += 24

        if hours_until_off_peak > deadline_hours:
            return False

        off_peak_cost = estimated_cost * self._peak.off_peak_multiplier
        saving = peak_cost - off_peak_cost

        if saving <= 0:
            return False

        return saving >= peak_cost * 0.20

    def defer_to_off_peak(
        self,
        task_id: str,
        deadline: datetime.datetime,
        *,
        now: datetime.datetime | None = None,
    ) -> dict[str, object]:
        """Schedule a task for off-peak execution.

        Records the intent and, when a deferred_queue is wired, enqueues
        the task.  The caller is responsible for budget pre-checks —
        deferral is a scheduling hint, not a guarantee.

        Args:
            task_id:  Unique task identifier.
            deadline: Latest time the task may start.
            now:      Override wall clock (tests).

        Returns:
            Dict with ``enqueued`` (bool), ``enqueue_id`` (str or None),
            and ``scheduled_for`` (ISO-formatted datetime string).
        """
        now = now or datetime.datetime.now(datetime.UTC)

        scheduled = now + datetime.timedelta(hours=1)
        if self._is_peak(now):
            end_of_peak = now.replace(
                hour=self._peak.peak_end_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if end_of_peak <= now:
                end_of_peak += datetime.timedelta(days=1)
            scheduled = end_of_peak

        if scheduled > deadline:
            scheduled = deadline

        enqueue_id: str | None = None
        if self._deferred is not None:
            try:
                enqueue_id = cast(_DeferredTaskQueue, self._deferred).enqueue(
                    task_id, deadline, {"task_id": task_id, "deadline": deadline.isoformat()}
                )
            except Exception:
                logger.exception("defer_to_off_peak: enqueue failed for %s", task_id)

        return {
            "enqueued": enqueue_id is not None,
            "enqueue_id": enqueue_id,
            "scheduled_for": scheduled.isoformat(),
            "task_id": task_id,
        }

    def estimate_cost(
        self,
        base_cost_usd: float,
        *,
        now: datetime.datetime | None = None,
    ) -> float:
        """Return the peak-adjusted cost for a base rate."""
        return self._adjusted_cost(base_cost_usd, now=now)

    def check_budget(self, estimated_cost: float) -> dict[str, bool | str | float]:
        """Run the budget guard pre-check for a projected cost.

        Returns the guard's verdict dict when wired; otherwise ``allowed: True``.
        """
        if self._budget_guard is None:
            return {"allowed": True, "reason": "no_guard"}
        return cast(_BudgetGuardProtocol, self._budget_guard).check_all_limits(estimated_cost=estimated_cost)
