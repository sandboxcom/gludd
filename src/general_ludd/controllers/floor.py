"""Floor controller — throttles active todo claims based on floor enforcement.

The controller reads the ``FLOOR`` env var (default 10) for the base max
active count. A health score (0.0-100.0, default 100) can reduce the effective
max: below 50 the cap is halved; below 25 dispatch is entirely blocked.
"""

from __future__ import annotations

import os


class FloorController:
    """Throttle how many active todos can be claimed in a tick.

    ``get_max_active()`` returns the effective cap: ``FLOOR`` env var (default
    10) modulated by the health score. Call ``update_health(score)`` to feed in
    a system health metric (e.g. memory pressure, CPU temp, error rate).
    """

    def __init__(self, floor: int | None = None) -> None:
        env_floor = os.environ.get("FLOOR")
        if floor is not None:
            self._floor = floor
        elif env_floor is not None:
            self._floor = int(env_floor)
        else:
            self._floor = 10
        self._health: float = 100.0

    @property
    def floor(self) -> int:
        return self._floor

    @property
    def health(self) -> float:
        return self._health

    def update_health(self, score: float) -> None:
        self._health = max(0.0, min(100.0, score))

    def get_max_active(self) -> int:
        if self._health < 25.0:
            return 0
        if self._health < 50.0:
            return max(1, self._floor // 2)
        return self._floor
