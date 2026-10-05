"""Off-peak task deferral - schedule expensive work for cheaper hours.

AG.12: OffPeakScheduler queues tasks whose estimated cost is significantly
higher now than during the off-peak window. SavingsTracker tracks cumulative
savings from deferrals across the lifetime of the process.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from general_ludd.budget.combined_cost import CombinedCostTracker


@dataclass
class OffPeakTicket:
    """A deferred task waiting for its off-peak window.

    All time fields use ``time.time()`` (wall-clock seconds since epoch).
    """

    task_id: str
    task_spec: dict[str, Any]
    deadline: float
    estimated_cost_now: float
    estimated_cost_off_peak: float
    savings: float
    scheduled_at: float
    runnable_after: float

    @property
    def is_ready(self) -> bool:
        """Return whether the ticket's runnable wall-clock time has arrived."""
        return time.time() >= self.runnable_after


@dataclass
class SavingsTracker:
    """Accumulate lifetime savings from off-peak deferrals.

    Thread-safe: all mutation is guarded by an internal lock.
    """

    _total_deferred: int = 0
    _total_savings: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, savings: float) -> None:
        """Record one finite, nonnegative deferred-task saving."""
        if not math.isfinite(savings) or savings < 0:
            return
        with self._lock:
            self._total_deferred += 1
            self._total_savings += savings

    @property
    def total_deferred(self) -> int:
        """Return the number of accepted savings records."""
        with self._lock:
            return self._total_deferred

    @property
    def total_savings(self) -> float:
        """Return the accumulated accepted savings amount."""
        with self._lock:
            return self._total_savings

    def snapshot(self) -> dict[str, object]:
        """Return an atomic copy of the lifetime savings counters."""
        with self._lock:
            return {
                "total_deferred": self._total_deferred,
                "total_savings": self._total_savings,
            }


class OffPeakScheduler:
    """Queue tasks for execution during off-peak hours.

    Off-peak is defined as a recurring daily window
    (``off_peak_start_hour``-``off_peak_end_hour``, local time).
    Tasks submitted via :meth:`schedule` whose current-hour estimated cost
    exceeds their off-peak-hour cost by at least ``min_savings_ratio`` are
    deferred.  Ready tasks (whose off-peak window has arrived) are fetched
    via :meth:`get_ready_tasks` and executed via :meth:`run_deferred`.

    All time fields (deadline, runnable_after, scheduled_at) use wall-clock
    ``time.time()`` seconds since the epoch.

    Args:
        cost_tracker:      A CombinedCostTracker for estimating model-API costs.
        off_peak_start:    Local hour (0-23) when off-peak begins.
        off_peak_end:      Local hour (0-23) when off-peak ends.
        cost_multiplier_peak:    Multiplier for peak-hour costs (default 1.5).
        cost_multiplier_off:     Multiplier for off-peak costs (default 1.0).
        min_savings_ratio: Minimum ``(now - off_peak) / now`` to defer
                           (default 0.20).
        executor:          Async callable ``(task_spec) -> result`` to actually
                           run deferred tasks.
        ticket_ttl:        Seconds after ``deadline`` that a ticket is pruned
                           (default 3600).
        clock:             Injectable wall clock for one coherent scheduling
                           decision. Defaults to ``time.time``.
    """

    def __init__(
        self,
        cost_tracker: CombinedCostTracker | None = None,
        *,
        off_peak_start: int = 0,
        off_peak_end: int = 6,
        cost_multiplier_peak: float = 1.5,
        cost_multiplier_off: float = 1.0,
        min_savings_ratio: float = 0.20,
        executor: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        ticket_ttl: float = 3600.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize policy, executor, and wall-clock dependencies."""
        _validate_hour(off_peak_start, "off_peak_start")
        _validate_hour(off_peak_end, "off_peak_end")
        if cost_multiplier_peak < 1.0:
            raise ValueError("cost_multiplier_peak must be >= 1.0")
        if cost_multiplier_off < 0:
            raise ValueError("cost_multiplier_off must be >= 0")
        if not (0.0 <= min_savings_ratio <= 1.0):
            raise ValueError("min_savings_ratio must be in [0.0, 1.0]")

        self._cost_tracker = cost_tracker
        self._off_start = off_peak_start
        self._off_end = off_peak_end
        self._peak_mul = cost_multiplier_peak
        self._off_mul = cost_multiplier_off
        self._min_ratio = min_savings_ratio
        self._executor = executor
        self._ticket_ttl = ticket_ttl
        self._clock = clock or time.time

        self._tickets: dict[str, OffPeakTicket] = {}
        self._lock = threading.Lock()
        self._savings = SavingsTracker()
        self._task_counter: int = 0

    @property
    def savings(self) -> SavingsTracker:
        """Return the scheduler's lifetime savings tracker."""
        return self._savings

    @property
    def pending_count(self) -> int:
        """Return the number of deferred tickets still owned by the scheduler."""
        with self._lock:
            return len(self._tickets)

    def _is_off_peak(self, t: float | None = None) -> bool:
        now = self._clock() if t is None else t
        hr = time.localtime(now).tm_hour
        if self._off_start <= self._off_end:
            return self._off_start <= hr < self._off_end
        return hr >= self._off_start or hr < self._off_end

    def _next_off_peak(self, t: float | None = None) -> float:
        now = self._clock() if t is None else t
        lt = time.localtime(now)
        today_start = now - lt.tm_hour * 3600 - lt.tm_min * 60 - lt.tm_sec
        off_peak_start_today = today_start + self._off_start * 3600
        if self._is_off_peak(now):
            return now
        if off_peak_start_today > now:
            return off_peak_start_today
        return off_peak_start_today + 86400

    def schedule(
        self,
        task_spec: dict[str, Any],
        deadline: float,
        *,
        estimated_cost_now: float | None = None,
        estimated_cost_off_peak: float | None = None,
    ) -> OffPeakTicket | None:
        """Queue a task for off-peak execution if the savings justify deferral.

        Args:
            task_spec:                Arbitrary dict passed to the executor.
            deadline:                 Wall-clock deadline (``time.time()``-based).
            estimated_cost_now:       Override the peak-cost estimate. When
                                      ``None`` and a ``cost_tracker`` is wired,
                                      the current model spend is used as a
                                      baseline proxy. Otherwise defaults to 0.
            estimated_cost_off_peak:  Override the off-peak-cost estimate. When
                                      ``None``, derived from the peak estimate
                                      and the configured multipliers.

        Returns:
            An ``OffPeakTicket`` if the task was deferred, or ``None`` if the
            savings are too small or off-peak is already active.
        """
        now_wall = self._clock()
        peak = estimated_cost_now
        if peak is None:
            peak = self._cost_tracker.model_spend() if self._cost_tracker else 0.0
        if peak < 0:
            peak = 0.0

        off = estimated_cost_off_peak
        if off is None:
            raw = peak * (self._off_mul / self._peak_mul) if self._peak_mul > 0 else peak
            off = max(0.0, raw)

        savings = peak - off
        ratio = 0.0 if peak <= 0 or savings <= 0 else savings / peak

        if self._is_off_peak(now_wall):
            return None

        if ratio < self._min_ratio:
            return None

        self._task_counter += 1
        tid = f"off-peak-{self._task_counter:06d}"

        ticket = OffPeakTicket(
            task_id=tid,
            task_spec=task_spec,
            deadline=deadline,
            estimated_cost_now=peak,
            estimated_cost_off_peak=off,
            savings=savings,
            scheduled_at=now_wall,
            runnable_after=self._next_off_peak(now_wall),
        )

        with self._lock:
            self._tickets[tid] = ticket

        self._savings.record(savings)
        return ticket

    def get_ready_tasks(self) -> list[OffPeakTicket]:
        """Return all tickets whose off-peak window has arrived."""
        now = self._clock()
        with self._lock:
            ready: list[OffPeakTicket] = [
                t for t in self._tickets.values() if now >= t.runnable_after and now <= t.deadline + self._ticket_ttl
            ]
        return ready

    async def run_deferred(self) -> list[dict[str, Any]]:
        """Execute all ready deferred tasks and remove them from the queue.

        Returns a list of ``{"task_id": str, "result": Any}`` dicts.
        If no executor is configured, returns an empty list.
        """
        if self._executor is None:
            return []

        ready = self.get_ready_tasks()
        results: list[dict[str, Any]] = []

        for ticket in ready:
            with self._lock:
                self._tickets.pop(ticket.task_id, None)
            try:
                result = await self._executor(ticket.task_spec)
                results.append({"task_id": ticket.task_id, "result": result})
            except Exception as exc:
                results.append({"task_id": ticket.task_id, "error": str(exc)})

        self._prune_expired()
        return results

    def _prune_expired(self) -> int:
        now = self._clock()
        pruned = 0
        with self._lock:
            expired = [tid for tid, t in self._tickets.items() if now > t.deadline + self._ticket_ttl]
            for tid in expired:
                del self._tickets[tid]
                pruned += 1
        return pruned

    async def _background_loop(
        self,
        poll_interval: float = 60.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Periodically execute ready deferred tasks.

        Intended as a background asyncio task. Runs until ``stop_event`` is set.
        """
        while stop_event is None or not stop_event.is_set():
            await self.run_deferred()
            await asyncio.sleep(poll_interval)

    def get_status(self) -> dict[str, object]:
        """Return an atomic scheduler status snapshot."""
        with self._lock:
            return {
                "pending_count": len(self._tickets),
                "savings": self._savings.snapshot(),
                "off_peak_start": self._off_start,
                "off_peak_end": self._off_end,
                "off_peak_active": self._is_off_peak(),
            }


def _validate_hour(hour: int, name: str) -> None:
    if not (0 <= hour <= 23):
        raise ValueError(f"{name} must be in [0, 23], got {hour!r}")
