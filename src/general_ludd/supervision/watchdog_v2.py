"""Watchdog v2: health check, auto-restart, backoff, circuit breaker.

Thread-safe watchdog for monitoring registered services.  Each service has:
  - a health-check callback (returns True if healthy)
  - an auto-restart callback (invoked on unhealthy detection)
  - exponential backoff between restart attempts
  - a per-service circuit breaker that stops restarting after repeated failures
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ServiceState(enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ServiceConfig:
    name: str
    health_check: Callable[[], bool]
    restart: Callable[[], bool]
    check_interval_s: float = 5.0
    restart_cooldown_s: float = 30.0
    max_restarts: int = 3
    backoff_base_s: float = 10.0
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 300.0
    unhealthy_strike_count: int = 2
    degraded_after_missed: int = 1


@dataclass
class ServiceStatus:
    name: str
    state: ServiceState
    last_healthy: float | None
    last_unhealthy: float | None
    restart_count: int
    current_backoff_s: float
    strikes: int
    total_checks: int
    total_failures: int
    circuit_open_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "last_healthy": self.last_healthy,
            "last_unhealthy": self.last_unhealthy,
            "restart_count": self.restart_count,
            "current_backoff_s": self.current_backoff_s,
            "strikes": self.strikes,
            "total_checks": self.total_checks,
            "total_failures": self.total_failures,
            "circuit_open_reason": self.circuit_open_reason,
        }


@dataclass
class WatchdogReport:
    timestamp: float
    services: list[ServiceStatus]
    overall_healthy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_healthy": self.overall_healthy,
            "services": [s.to_dict() for s in self.services],
        }


class ServiceCircuitBreaker:
    """Per-service breaker: opens after max_restarts, resets on sustained health."""

    def __init__(self, max_restarts: int, reset_after_healthy_checks: int = 5) -> None:
        self._max_restarts = max_restarts
        self._reset_after_healthy_checks = reset_after_healthy_checks
        self._open = False
        self._consecutive_healthy = 0
        self._reason: str | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def record_restart(self, current_count: int) -> bool:
        with self._lock:
            if current_count >= self._max_restarts:
                self._open = True
                self._reason = f"max_restarts({self._max_restarts}) exceeded"
                return True
            return False

    def record_healthy(self) -> bool:
        with self._lock:
            self._consecutive_healthy += 1
            if self._open and self._consecutive_healthy >= self._reset_after_healthy_checks:
                self._open = False
                self._consecutive_healthy = 0
                self._reason = None
                return True
            return False

    def record_unhealthy(self) -> None:
        with self._lock:
            self._consecutive_healthy = 0

    def reset(self) -> None:
        with self._lock:
            self._open = False
            self._consecutive_healthy = 0
            self._reason = None


class BackoffTimer:
    """Exponential backoff with jitter."""

    def __init__(
        self,
        base_s: float = 10.0,
        multiplier: float = 2.0,
        max_s: float = 300.0,
        jitter: bool = True,
    ) -> None:
        self._base = base_s
        self._multiplier = multiplier
        self._max = max_s
        self._jitter = jitter
        self._attempt = 0
        self._last_restart_ts: float | None = None
        self._lock = threading.RLock()

    @property
    def current_delay(self) -> float:
        with self._lock:
            return min(self._base * (self._multiplier ** max(0, self._attempt - 1)), self._max)

    @property
    def attempt(self) -> int:
        with self._lock:
            return self._attempt

    @property
    def cooldown_remaining(self) -> float:
        with self._lock:
            if self._last_restart_ts is None:
                return 0.0
            elapsed = time.monotonic() - self._last_restart_ts
            delay = self.current_delay
            return max(0.0, delay - elapsed)

    def can_restart(self) -> bool:
        with self._lock:
            return self._last_restart_ts is None or self.cooldown_remaining <= 0.0

    def record_restart(self) -> None:
        with self._lock:
            self._attempt += 1
            self._last_restart_ts = time.monotonic()

    def record_success(self) -> None:
        with self._lock:
            self._attempt = 0
            self._last_restart_ts = None

    def reset(self) -> None:
        with self._lock:
            self._attempt = 0
            self._last_restart_ts = None


class ServiceWatcher:
    """Watches a single registered service — health check, restart, backoff, circuit breaker."""

    def __init__(self, config: ServiceConfig) -> None:
        self._cfg = config
        self._state = ServiceState.HEALTHY
        self._last_healthy: float | None = None
        self._last_unhealthy: float | None = None
        self._restart_count = 0
        self._strikes = 0
        self._total_checks = 0
        self._total_failures = 0
        self._backoff = BackoffTimer(
            base_s=config.backoff_base_s,
            multiplier=config.backoff_multiplier,
            max_s=config.max_backoff_s,
        )
        self._breaker = ServiceCircuitBreaker(max_restarts=config.max_restarts)
        self._lock = threading.RLock()
        self._consecutive_healthy = 0
        # Track epoch of last restart for cooldown enforcement
        self._last_restart_epoch: float | None = None

    @property
    def state(self) -> ServiceState:
        with self._lock:
            return self._state

    def status(self) -> ServiceStatus:
        with self._lock:
            return ServiceStatus(
                name=self._cfg.name,
                state=self._state,
                last_healthy=self._last_healthy,
                last_unhealthy=self._last_unhealthy,
                restart_count=self._restart_count,
                current_backoff_s=self._backoff.current_delay,
                strikes=self._strikes,
                total_checks=self._total_checks,
                total_failures=self._total_failures,
                circuit_open_reason=self._breaker.reason,
            )

    def _transition(self, new_state: ServiceState) -> None:
        self._state = new_state

    def _attempt_restart(self) -> bool:
        if not self._backoff.can_restart():
            return False
        elapsed_since_last = 0.0
        if self._last_restart_epoch is not None:
            elapsed_since_last = time.monotonic() - self._last_restart_epoch
        if elapsed_since_last < self._cfg.restart_cooldown_s and self._last_restart_epoch is not None:
            return False
        opened = self._breaker.record_restart(self._restart_count)
        if opened:
            self._transition(ServiceState.CIRCUIT_OPEN)
            return False
        try:
            ok = self._cfg.restart()
        except Exception:
            ok = False
        self._backoff.record_restart()
        self._last_restart_epoch = time.monotonic()
        self._restart_count += 1
        if ok:
            self._backoff.record_success()
            self._last_healthy = time.monotonic()
            self._last_unhealthy = None
            self._strikes = 0
            self._restart_count = 0
            self._transition(ServiceState.HEALTHY)
            return True
        return False

    def check(self) -> bool:
        with self._lock:
            self._total_checks += 1
            if self._state == ServiceState.CIRCUIT_OPEN:
                self._breaker.record_unhealthy()
                return False
            healthy = False
            try:
                healthy = self._cfg.health_check()
            except Exception:
                healthy = False
            if healthy:
                self._last_healthy = time.monotonic()
                self._strikes = 0
                self._consecutive_healthy += 1
                self._breaker.record_healthy()
                if self._state in (ServiceState.UNHEALTHY, ServiceState.DEGRADED):
                    if self._breaker.is_open:
                        self._state = ServiceState.CIRCUIT_OPEN
                    else:
                        self._transition(ServiceState.HEALTHY)
                return True
            self._total_failures += 1
            self._last_unhealthy = time.monotonic()
            self._strikes += 1
            self._consecutive_healthy = 0
            self._breaker.record_unhealthy()
            if self._state == ServiceState.HEALTHY and self._strikes >= self._cfg.degraded_after_missed:
                self._transition(ServiceState.DEGRADED)
            if self._strikes >= self._cfg.unhealthy_strike_count:
                self._transition(ServiceState.UNHEALTHY)
            return False

    def attempt_restart(self) -> bool:
        with self._lock:
            return self._attempt_restart()

    def reset(self) -> None:
        with self._lock:
            self._state = ServiceState.HEALTHY
            self._last_healthy = None
            self._last_unhealthy = None
            self._restart_count = 0
            self._strikes = 0
            self._total_checks = 0
            self._total_failures = 0
            self._consecutive_healthy = 0
            self._last_restart_epoch = None
            self._backoff.reset()
            self._breaker.reset()


class WatchdogV2:
    """Multi-service watchdog.  Register named services, poll them."""

    def __init__(self) -> None:
        self._watchers: dict[str, ServiceWatcher] = {}
        self._lock = threading.Lock()
        self._running = False
        self._cycle_count = 0

    def register(self, config: ServiceConfig) -> ServiceWatcher:
        with self._lock:
            if config.name in self._watchers:
                raise ValueError(f"Service already registered: {config.name}")
            watcher = ServiceWatcher(config)
            self._watchers[config.name] = watcher
            return watcher

    def unregister(self, name: str) -> None:
        with self._lock:
            self._watchers.pop(name, None)

    def get(self, name: str) -> ServiceWatcher | None:
        with self._lock:
            return self._watchers.get(name)

    def list_services(self) -> list[str]:
        with self._lock:
            return sorted(self._watchers.keys())

    def poll_all(self) -> WatchdogReport:
        with self._lock:
            self._cycle_count += 1
            statuses: list[ServiceStatus] = []
            for watcher in self._watchers.values():
                watcher.check()
                if watcher.state == ServiceState.UNHEALTHY:
                    watcher.attempt_restart()
                statuses.append(watcher.status())
            overall = all(s.state == ServiceState.HEALTHY for s in statuses)
            return WatchdogReport(
                timestamp=time.monotonic(),
                services=statuses,
                overall_healthy=overall,
            )

    def report(self) -> WatchdogReport:
        with self._lock:
            statuses = [w.status() for w in self._watchers.values()]
            overall = all(s.state == ServiceState.HEALTHY for s in statuses)
            return WatchdogReport(
                timestamp=time.monotonic(),
                services=statuses,
                overall_healthy=overall,
            )

    def reset_all(self) -> None:
        with self._lock:
            self._cycle_count = 0
            for w in self._watchers.values():
                w.reset()

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count
