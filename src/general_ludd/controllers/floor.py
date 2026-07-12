"""Floor controller — throttles active todo claims based on floor enforcement.

The controller reads the ``FLOOR`` env var (default 10) for the base max
active count. A health score (0.0-100.0, default 100) can reduce the effective
max: below 50 the cap is halved; below 25 dispatch is entirely blocked.

The ``auto_tune()`` method dynamically adjusts the floor based on system
metrics: dispatch success rate, queue depth, CPU, and memory pressure.
Floor history is recorded for trend analysis.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime


class FloorController:
    """Throttle how many active todos can be claimed in a tick.

    ``get_max_active()`` returns the effective cap: ``FLOOR`` env var (default
    10) modulated by the health score. Call ``update_health(score)`` to feed in
    a system health metric (e.g. memory pressure, CPU temp, error rate).

    ``auto_tune()`` dynamically adjusts the floor value based on dispatch
    success rate and queue pressure, bounded to [1, 20].
    """

    def __init__(self, floor: int | None = None) -> None:
        env_floor = os.environ.get("FLOOR")
        if floor is not None:
            self._floor = floor
        elif env_floor is not None:
            self._floor = int(env_floor)
        else:
            self._floor = 2
        self._health: float = 100.0
        self._floor_history: list[dict[str, object]] = []

    @property
    def floor(self) -> int:
        return self._floor

    @property
    def health(self) -> float:
        return self._health

    @property
    def floor_history(self) -> list[dict[str, object]]:
        """Ordered list of floor-change records for trend analysis."""
        return list(self._floor_history)

    def update_health(self, score: float) -> None:
        self._health = max(0.0, min(100.0, score))

    def get_max_active(self) -> int:
        if self._health < 25.0:
            return 0
        if self._health < 50.0:
            return max(1, self._floor // 2)
        return self._floor

    def auto_tune(
        self,
        cpu_pct: float,
        memory_pct: float,
        dispatch_success_rate: float,
        queue_depth: int,
    ) -> int:
        """Dynamically adjust the floor based on system metrics.

        Returns the new floor value after adjustment.

        Rules:
        - If dispatch success rate < 90%, lower floor by 2 (minimum 1).
        - If queue depth > 20 and success rate > 95%, raise floor by 2 (maximum 20).
        - Otherwise no change (floor stays at current value).
        """
        old_floor = self._floor
        reason = "no_change"

        if dispatch_success_rate < 90.0:
            if self._floor > 1:
                self._floor = max(1, self._floor - 2)
                reason = "low_success_rate"
        elif queue_depth > 20 and dispatch_success_rate > 95.0 and self._floor < 20:
            self._floor = min(20, self._floor + 2)
            reason = "high_queue_depth"

        self._floor_history.append({
            "floor": self._floor,
            "previous_floor": old_floor,
            "cpu_pct": cpu_pct,
            "memory_pct": memory_pct,
            "dispatch_success_rate": dispatch_success_rate,
            "queue_depth": queue_depth,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": reason,
        })

        return self._floor
