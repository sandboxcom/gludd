"""Deep tests for supervision/watchdog_v2.py — health check, auto-restart,
backoff, circuit breaker, ServiceWatcher, WatchdogV2, BackoffTimer,
ServiceCircuitBreaker.

Covers:
  - BackoffTimer: exponential growth, reset, can_restart, cooldown, jitter
  - ServiceCircuitBreaker: open on max restarts, reset on sustained health
  - ServiceWatcher: health→degraded→unhealthy→restart→circuit_open lifecycle
  - WatchdogV2: registration, poll_all, report, unregister, multi-service
  - Edge cases: restart exceptions, rapid polls, concurrent access, max backoff cap
"""

from __future__ import annotations

import threading

import pytest

from general_ludd.supervision.watchdog_v2 import (
    BackoffTimer,
    ServiceCircuitBreaker,
    ServiceConfig,
    ServiceState,
    ServiceWatcher,
    WatchdogV2,
)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. BACKOFF TIMER
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackoffTimer:
    def test_initial_state_can_restart(self):
        bt = BackoffTimer()
        assert bt.can_restart()
        assert bt.attempt == 0
        assert bt.current_delay == 10.0
        assert bt.cooldown_remaining == 0.0

    def test_exponential_growth(self):
        bt = BackoffTimer(base_s=10.0, multiplier=2.0, max_s=300.0)
        delays = []
        for _ in range(5):
            bt.record_restart()
            delays.append(bt.current_delay)
        assert delays == [10.0, 20.0, 40.0, 80.0, 160.0]

    def test_max_backoff_cap(self):
        bt = BackoffTimer(base_s=10.0, multiplier=4.0, max_s=300.0)
        for _ in range(10):
            bt.record_restart()
        assert bt.current_delay == 300.0
        assert bt.attempt == 10

    def test_cooldown_blocks_restart(self):
        bt = BackoffTimer(base_s=100.0, multiplier=1.0, max_s=300.0)
        bt.record_restart()
        assert bt.cooldown_remaining > 0
        assert not bt.can_restart()

    def test_record_success_resets(self):
        bt = BackoffTimer()
        for _ in range(3):
            bt.record_restart()
        assert bt.attempt == 3
        bt.record_success()
        assert bt.attempt == 0
        assert bt.can_restart()

    def test_reset_clears_all(self):
        bt = BackoffTimer()
        for _ in range(5):
            bt.record_restart()
        bt.reset()
        assert bt.attempt == 0
        assert bt.current_delay == 10.0
        assert bt.can_restart()

    def test_zero_base_passes(self):
        bt = BackoffTimer(base_s=0.0, multiplier=2.0, max_s=10.0)
        bt.record_restart()
        assert bt.current_delay == 0.0

    def test_jitter_enabled_by_default(self):
        bt = BackoffTimer()
        assert bt._jitter is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceCircuitBreaker:
    def test_starts_closed(self):
        cb = ServiceCircuitBreaker(max_restarts=3)
        assert not cb.is_open
        assert cb.reason is None

    def test_opens_at_threshold(self):
        cb = ServiceCircuitBreaker(max_restarts=3)
        assert not cb.record_restart(2)
        assert not cb.is_open
        assert cb.record_restart(3)
        assert cb.is_open
        assert cb.reason is not None

    def test_does_not_open_below_threshold(self):
        cb = ServiceCircuitBreaker(max_restarts=5)
        assert not cb.record_restart(4)
        assert not cb.is_open

    def test_reset_on_sustained_health(self):
        cb = ServiceCircuitBreaker(max_restarts=2, reset_after_healthy_checks=3)
        cb.record_restart(2)
        assert cb.is_open
        for _ in range(2):
            closed = cb.record_healthy()
            assert not closed
        closed = cb.record_healthy()
        assert closed
        assert not cb.is_open
        assert cb.reason is None

    def test_single_healthy_does_not_reset(self):
        cb = ServiceCircuitBreaker(max_restarts=2, reset_after_healthy_checks=5)
        cb.record_restart(2)
        assert cb.is_open
        assert not cb.record_healthy()

    def test_unhealthy_resets_consecutive_counter(self):
        cb = ServiceCircuitBreaker(max_restarts=2, reset_after_healthy_checks=3)
        cb.record_restart(2)
        assert cb.is_open
        cb.record_healthy()
        cb.record_healthy()
        cb.record_unhealthy()
        cb.record_healthy()
        cb.record_healthy()
        assert cb.is_open

    def test_reset_clears_state(self):
        cb = ServiceCircuitBreaker(max_restarts=1)
        cb.record_restart(1)
        assert cb.is_open
        cb.reset()
        assert not cb.is_open
        assert cb.reason is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SERVICE WATCHER
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceWatcher:
    def test_initial_state_healthy(self):
        w = ServiceWatcher(_cfg(health=True))
        assert w.state == ServiceState.HEALTHY

    def test_single_healthy_check(self):
        w = ServiceWatcher(_cfg(health=True))
        assert w.check()
        assert w.state == ServiceState.HEALTHY

    def test_degraded_after_strike(self):
        w = ServiceWatcher(_cfg(health=True, unhealthy_strikes=3, degraded_after=2))
        assert w.check()
        assert w.state == ServiceState.HEALTHY
        w._cfg.health_check = lambda: False
        assert not w.check()
        assert w.state == ServiceState.HEALTHY
        assert not w.check()
        assert w.state == ServiceState.DEGRADED

    def test_unhealthy_after_strikes(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=3, degraded_after=2))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert w.state == ServiceState.UNHEALTHY

    def test_healthy_resets_strikes(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=3, degraded_after=2))
        w._cfg.health_check = lambda: False
        for _ in range(2):
            w.check()
        assert w.state == ServiceState.DEGRADED
        w._cfg.health_check = lambda: True
        assert w.check()
        assert w.state == ServiceState.HEALTHY

    def test_restart_success_resets_state(self):
        w = ServiceWatcher(_cfg(health=False, restart_ok=True, restart_cooldown=0.0))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert w.state == ServiceState.UNHEALTHY
        assert w.attempt_restart()
        assert w.state == ServiceState.HEALTHY

    def test_restart_failure_stays_unhealthy(self):
        w = ServiceWatcher(_cfg(health=False, restart_ok=False, restart_cooldown=0.0))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert w.state == ServiceState.UNHEALTHY
        assert not w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY

    def test_circuit_opens_on_max_restarts(self):
        w = ServiceWatcher(
            _cfg(health=False, restart_ok=False, restart_cooldown=0.0, max_restarts=2, backoff_base_s=0.0)
        )
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN

    def test_check_returns_false_when_circuit_open(self):
        w = ServiceWatcher(_cfg(health=True, max_restarts=1))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        w.attempt_restart()
        w._breaker.record_restart(1)
        w._state = ServiceState.CIRCUIT_OPEN
        assert not w.check()

    def test_restart_exception_handled(self):
        w = ServiceWatcher(_cfg(health=False, restart_raises=True, restart_cooldown=0.0))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert not w.attempt_restart()

    def test_status_records_all_fields(self):
        w = ServiceWatcher(_cfg(health=True))
        w.check()
        s = w.status()
        assert s.name == "test"
        assert s.state == ServiceState.HEALTHY
        assert s.total_checks == 1
        assert s.restart_count == 0

    def test_lifecycle_full_cycle(self):
        """Healthy → degraded → unhealthy → restart fail → repeat → circuit open → reset"""
        w = ServiceWatcher(
            _cfg(
                health=False,
                restart_ok=False,
                restart_cooldown=0.0,
                max_restarts=3,
                unhealthy_strikes=2,
                degraded_after=1,
                backoff_base_s=0.0,
            )
        )
        w._cfg.health_check = lambda: False
        assert w.state == ServiceState.HEALTHY
        w.check()
        assert w.state == ServiceState.DEGRADED
        w.check()
        assert w.state == ServiceState.UNHEALTHY
        for _ in range(4):
            w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN
        w.reset()
        assert w.state == ServiceState.HEALTHY
        assert w.status().total_checks == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. WATCHDOG V2 (multi-service)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWatchdogV2:
    def test_register_service(self):
        wd = WatchdogV2()
        svc = _cfg(health=True)
        wd.register(svc)
        assert "test" in wd.list_services()

    def test_duplicate_register_raises(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        with pytest.raises(ValueError, match="already registered"):
            wd.register(_cfg(health=True))

    def test_unregister_removes_service(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        wd.unregister("test")
        assert wd.list_services() == []

    def test_unregister_missing_noop(self):
        wd = WatchdogV2()
        wd.unregister("ghost")

    def test_get_returns_watcher(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        assert wd.get("test") is not None
        assert wd.get("ghost") is None

    def test_poll_all_healthy(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="a"))
        wd.register(_cfg(health=True, name="b"))
        report = wd.poll_all()
        assert report.overall_healthy
        assert len(report.services) == 2

    def test_poll_all_with_unhealthy(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="a"))
        wd.register(_cfg(health=False, name="b", unhealthy_strikes=1, degraded_after=1))
        wd.poll_all()
        report = wd.poll_all()
        assert not report.overall_healthy
        b_status = next(s for s in report.services if s.name == "b")
        assert b_status.state in (ServiceState.DEGRADED, ServiceState.UNHEALTHY)

    def test_report_read_only(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="a"))
        wd.register(_cfg(health=False, name="b", unhealthy_strikes=1, degraded_after=1))
        r1 = wd.report()
        r2 = wd.report()
        assert r1.services[0].total_checks == r2.services[0].total_checks

    def test_cycle_count_increments(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        assert wd.cycle_count == 0
        wd.poll_all()
        assert wd.cycle_count == 1
        wd.poll_all()
        wd.poll_all()
        assert wd.cycle_count == 3

    def test_reset_all(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=False, name="a", unhealthy_strikes=1, degraded_after=1))
        wd.poll_all()
        wd.poll_all()
        assert wd.cycle_count == 2
        wd.reset_all()
        assert wd.cycle_count == 0
        s = wd.get("a").status()
        assert s.total_checks == 0

    def test_auto_restart_on_unhealthy(self):
        restarted = []
        wd = WatchdogV2()
        cfg = _cfg(health=False, restart_ok=True, restart_cooldown=0.0, unhealthy_strikes=1, degraded_after=1)
        orig = cfg.restart

        def track():
            restarted.append(1)
            return orig()

        cfg.restart = track
        wd.register(cfg)
        wd.poll_all()
        wd.poll_all()
        assert len(restarted) >= 1

    def test_multi_service_independent(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="healthy"))
        wd.register(_cfg(health=False, name="unhealthy", unhealthy_strikes=2, degraded_after=1))
        wd.register(_cfg(health=True, name="also_healthy"))
        for _ in range(3):
            wd.poll_all()
        healthy_status = wd.get("healthy").status()
        unhealthy_status = wd.get("unhealthy").status()
        also_status = wd.get("also_healthy").status()
        assert healthy_status.state == ServiceState.HEALTHY
        assert also_status.state == ServiceState.HEALTHY
        assert unhealthy_status.state != ServiceState.HEALTHY


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    def test_concurrent_polls_no_crash(self):
        wd = WatchdogV2()
        for i in range(10):
            wd.register(_cfg(health=True, name=f"svc_{i}"))
        errors = []

        def worker():
            try:
                for _ in range(50):
                    wd.poll_all()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_concurrent_register_list(self):
        wd = WatchdogV2()
        errors = []

        def registrant(idx):
            try:
                wd.register(_cfg(health=True, name=f"svc_{idx}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=registrant, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(wd.list_services()) == 20

    def test_concurrent_reset(self):
        wd = WatchdogV2()
        for i in range(5):
            wd.register(_cfg(health=True, name=f"svc_{i}"))
        for _ in range(5):
            wd.poll_all()
        errors = []

        def reseter():
            try:
                wd.reset_all()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reseter) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. REPORT / SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerialization:
    def test_service_status_to_dict(self):
        w = ServiceWatcher(_cfg(health=True))
        w.check()
        d = w.status().to_dict()
        assert "name" in d
        assert "state" in d
        assert "total_checks" in d
        assert d["total_checks"] == 1
        assert d["state"] == "healthy"

    def test_watchdog_report_to_dict(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="a"))
        wd.register(_cfg(health=False, name="b", unhealthy_strikes=1, degraded_after=1))
        wd.poll_all()
        wd.poll_all()
        d = wd.report().to_dict()
        assert "timestamp" in d
        assert "overall_healthy" in d
        assert "services" in d
        assert len(d["services"]) == 2
        a_state = next(s["state"] for s in d["services"] if s["name"] == "a")
        assert a_state == "healthy"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _cfg(
    health: bool = True,
    name: str = "test",
    restart_ok: bool = True,
    restart_raises: bool = False,
    restart_cooldown: float = 30.0,
    unhealthy_strikes: int = 3,
    degraded_after: int = 2,
    max_restarts: int = 3,
    **kwargs,
) -> ServiceConfig:
    def hc():
        return health
    if restart_raises:
        def rc():
            return (_ for _ in ()).throw(RuntimeError("restart failed"))
    elif restart_ok:
        def rc():
            return True
    else:
        def rc():
            return False
    return ServiceConfig(
        name=name,
        health_check=hc,
        restart=rc,
        restart_cooldown_s=restart_cooldown,
        unhealthy_strike_count=unhealthy_strikes,
        degraded_after_missed=degraded_after,
        max_restarts=max_restarts,
        **kwargs,
    )
