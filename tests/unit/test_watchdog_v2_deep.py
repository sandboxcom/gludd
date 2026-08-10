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
import time

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


# ═══════════════════════════════════════════════════════════════════════════════
# 8. DEEP EDGE CASES — BACKOFF TIMER
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackoffTimerDeep:
    def test_attempt_zero_current_delay_is_base(self):
        bt = BackoffTimer(base_s=15.0, multiplier=2.0, max_s=300.0)
        assert bt.attempt == 0
        assert bt.current_delay == 15.0

    def test_attempt_one_current_delay_is_base_times_multiplier_to_zero(self):
        bt = BackoffTimer(base_s=10.0, multiplier=3.0, max_s=300.0)
        assert bt.attempt == 0
        bt.record_restart()
        assert bt.attempt == 1
        assert bt.current_delay == 10.0  # base * 3^(1-1) = 10 * 1 = 10

    def test_cooldown_remaining_decreases_over_time(self):
        import time as _time

        bt = BackoffTimer(base_s=0.5, multiplier=1.0, max_s=10.0)
        bt.record_restart()
        initial = bt.cooldown_remaining
        assert initial > 0
        _time.sleep(0.6)
        assert bt.cooldown_remaining == 0.0
        assert bt.can_restart()

    def test_record_restart_during_cooldown_increments_attempt(self):
        bt = BackoffTimer(base_s=100.0, multiplier=2.0, max_s=1000.0)
        bt.record_restart()
        assert bt.attempt == 1
        assert not bt.can_restart()
        bt.record_restart()
        assert bt.attempt == 2
        assert bt.current_delay == 100.0 * (2.0**1)

    def test_max_backoff_at_exact_cap(self):
        bt = BackoffTimer(base_s=50.0, multiplier=2.0, max_s=200.0)
        bt.record_restart()
        assert bt.current_delay == 50.0
        bt.record_restart()
        assert bt.current_delay == 100.0
        bt.record_restart()
        assert bt.current_delay == 200.0
        bt.record_restart()
        assert bt.current_delay == 200.0

    def test_multiple_record_success_noop(self):
        bt = BackoffTimer()
        bt.record_restart()
        bt.record_success()
        assert bt.attempt == 0
        bt.record_success()
        assert bt.attempt == 0

    def test_reset_from_mid_cooldown(self):
        bt = BackoffTimer(base_s=100.0, multiplier=2.0, max_s=500.0)
        bt.record_restart()
        assert not bt.can_restart()
        bt.reset()
        assert bt.can_restart()
        assert bt.attempt == 0
        assert bt.cooldown_remaining == 0.0

    def test_multiplier_of_one_constant_delay(self):
        bt = BackoffTimer(base_s=20.0, multiplier=1.0, max_s=300.0)
        for _ in range(5):
            bt.record_restart()
        assert bt.current_delay == 20.0

    def test_concurrent_record_restart_no_race(self):
        bt = BackoffTimer(base_s=0.0, multiplier=1.0, max_s=10.0)
        errors = []

        def hammer():
            try:
                for _ in range(200):
                    bt.record_restart()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert bt.attempt == 8 * 200

    def test_negative_backoff_base_clamped_by_max(self):
        bt = BackoffTimer(base_s=-10.0, multiplier=2.0, max_s=5.0)
        bt.record_restart()
        assert bt.current_delay <= 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# 9. DEEP EDGE CASES — CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceCircuitBreakerDeep:
    def test_record_restart_far_above_threshold(self):
        cb = ServiceCircuitBreaker(max_restarts=3)
        assert cb.record_restart(999)
        assert cb.is_open

    def test_record_restart_below_threshold_no_side_effect(self):
        cb = ServiceCircuitBreaker(max_restarts=10)
        assert not cb.record_restart(0)
        assert not cb.is_open
        assert cb.reason is None

    def test_record_healthy_when_closed_noop(self):
        cb = ServiceCircuitBreaker(max_restarts=3, reset_after_healthy_checks=2)
        closed = cb.record_healthy()
        assert not closed
        assert not cb.is_open

    def test_record_healthy_resets_only_at_threshold(self):
        cb = ServiceCircuitBreaker(max_restarts=2, reset_after_healthy_checks=4)
        cb.record_restart(2)
        assert cb.is_open
        for _ in range(3):
            cb.record_healthy()
        assert cb.is_open
        assert cb.record_healthy()
        assert not cb.is_open

    def test_reset_after_healthy_checks_one(self):
        cb = ServiceCircuitBreaker(max_restarts=1, reset_after_healthy_checks=1)
        cb.record_restart(1)
        assert cb.is_open
        assert cb.record_healthy()
        assert not cb.is_open

    def test_double_restart_no_second_open(self):
        cb = ServiceCircuitBreaker(max_restarts=2)
        cb.record_restart(2)
        assert cb.is_open
        cb.record_restart(5)
        assert cb.is_open

    def test_reset_when_already_closed_noop(self):
        cb = ServiceCircuitBreaker(max_restarts=3)
        cb.reset()
        assert not cb.is_open

    def test_concurrent_open_and_reset(self):
        cb = ServiceCircuitBreaker(max_restarts=2)
        errors = []

        def opener():
            try:
                for _ in range(100):
                    cb.record_restart(2)
            except Exception as exc:
                errors.append(exc)

        def resetter():
            try:
                for _ in range(100):
                    cb.reset()
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=opener)
        t2 = threading.Thread(target=resetter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == []


# ═══════════════════════════════════════════════════════════════════════════════
# 10. DEEP EDGE CASES — SERVICE WATCHER TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceWatcherTransitions:
    def test_degraded_to_healthy_after_single_success(self):
        w = ServiceWatcher(_cfg(health=True, unhealthy_strikes=5, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.DEGRADED
        w._cfg.health_check = lambda: True
        assert w.check()
        assert w.state == ServiceState.HEALTHY

    def test_unhealthy_to_healthy_after_single_success(self):
        w = ServiceWatcher(_cfg(health=True, unhealthy_strikes=3, degraded_after=2))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert w.state == ServiceState.UNHEALTHY
        w._cfg.health_check = lambda: True
        assert w.check()
        assert w.state == ServiceState.HEALTHY

    def test_unhealthy_to_circuit_open_directly(self):
        w = ServiceWatcher(
            _cfg(health=False, restart_ok=False, restart_cooldown=0.0, max_restarts=2, backoff_base_s=0.0)
        )
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN

    def test_circuit_open_stays_open_on_check(self):
        w = ServiceWatcher(_cfg(health=True, max_restarts=1))
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        w._state = ServiceState.CIRCUIT_OPEN
        w._breaker.record_restart(1)
        assert not w.check()
        assert w.state == ServiceState.CIRCUIT_OPEN

    def test_circuit_open_sustained_health_does_not_auto_close(self):
        w = ServiceWatcher(
            _cfg(health=True, restart_ok=False, max_restarts=1, restart_cooldown=0.0, backoff_base_s=0.0)
        )
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN
        w._cfg.health_check = lambda: True
        for _ in range(10):
            w.check()
        assert w.state == ServiceState.CIRCUIT_OPEN

    def test_restart_while_circuit_open_returns_false(self):
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
        assert not w.attempt_restart()

    def test_health_check_exception_treated_as_unhealthy(self):
        w = ServiceWatcher(_cfg(health=True, unhealthy_strikes=3, degraded_after=2))

        def raise_exc():
            raise RuntimeError("health check crash")

        w._cfg.health_check = raise_exc
        assert not w.check()
        assert w.state == ServiceState.HEALTHY
        assert not w.check()
        assert w.state == ServiceState.DEGRADED

    def test_restart_cooldown_blocks_premature_restart(self):
        w = ServiceWatcher(
            _cfg(
                health=False,
                restart_ok=True,
                restart_cooldown=999.0,
                unhealthy_strikes=1,
                degraded_after=1,
                backoff_base_s=0.0,
            )
        )
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.UNHEALTHY
        ok = w.attempt_restart()
        assert ok
        assert w.state == ServiceState.HEALTHY
        w._cfg.health_check = lambda: False
        w.check()
        w.check()
        assert w.state == ServiceState.UNHEALTHY
        assert not w.attempt_restart()

    def test_max_restarts_zero_opens_immediately(self):
        w = ServiceWatcher(
            _cfg(
                health=False,
                restart_ok=False,
                restart_cooldown=0.0,
                max_restarts=0,
                unhealthy_strikes=1,
                degraded_after=1,
                backoff_base_s=0.0,
            )
        )
        w._cfg.health_check = lambda: False
        w.check()
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN

    def test_strikes_increment_beyond_unhealthy_threshold(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=2, degraded_after=1))
        w._cfg.health_check = lambda: False
        for _ in range(10):
            w.check()
        s = w.status()
        assert s.strikes == 10
        assert s.total_failures == 10
        assert s.total_checks == 10

    def test_restart_count_resets_on_successful_restart(self):
        w = ServiceWatcher(
            _cfg(health=False, restart_ok=True, restart_cooldown=0.0, unhealthy_strikes=1, degraded_after=1)
        )
        w._cfg.health_check = lambda: False
        w.check()
        w.attempt_restart()
        assert w.status().restart_count == 0

    def test_restart_count_does_not_reset_on_failed_restart(self):
        w = ServiceWatcher(
            _cfg(health=False, restart_ok=False, restart_cooldown=0.0, unhealthy_strikes=1, degraded_after=1)
        )
        w._cfg.health_check = lambda: False
        w.check()
        w.attempt_restart()
        s = w.status()
        assert s.restart_count == 1

    def test_consecutive_healthy_resets_on_failure(self):
        w = ServiceWatcher(_cfg(health=True, max_restarts=2))
        for _ in range(5):
            w.check()
        w._cfg.health_check = lambda: False
        w.check()
        assert w._consecutive_healthy == 0

    def test_status_includes_circuit_open_reason(self):
        w = ServiceWatcher(
            _cfg(health=False, restart_ok=False, restart_cooldown=0.0, max_restarts=1, backoff_base_s=0.0)
        )
        w._cfg.health_check = lambda: False
        for _ in range(3):
            w.check()
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN
        s = w.status()
        assert s.circuit_open_reason is not None
        assert "max_restarts" in s.circuit_open_reason

    def test_check_on_unhealthy_no_state_change_when_strikes_equal_threshold(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=2, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.DEGRADED
        w.check()
        assert w.state == ServiceState.UNHEALTHY
        w.check()
        assert w.state == ServiceState.UNHEALTHY


# ═══════════════════════════════════════════════════════════════════════════════
# 11. DEEP EDGE CASES — WATCHDOG V2 ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestWatchdogV2Deep:
    def test_poll_all_auto_restricts_restart_when_cooldown_active(self):
        restarts = []
        wd = WatchdogV2()
        cfg = _cfg(
            health=False,
            restart_ok=True,
            restart_cooldown=900.0,
            unhealthy_strikes=1,
            degraded_after=1,
            backoff_base_s=0.0,
        )

        def track():
            restarts.append(time.time())
            return True

        cfg.restart = track
        wd.register(cfg)
        wd.poll_all()
        wd.poll_all()
        assert len(restarts) >= 1
        first_count = len(restarts)
        wd.poll_all()
        assert len(restarts) == first_count

    def test_report_read_only_does_not_trigger_restart(self):
        restarts = []
        wd = WatchdogV2()
        cfg = _cfg(health=False, restart_ok=True, restart_cooldown=0.0, unhealthy_strikes=1, degraded_after=1)

        def track():
            restarts.append(1)
            return True

        cfg.restart = track
        wd.register(cfg)
        wd.poll_all()
        wd.poll_all()
        after_poll = len(restarts)
        wd.report()
        assert len(restarts) == after_poll

    def test_poll_all_sets_overall_healthy_false_with_one_degraded(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="ok"))
        wd.register(_cfg(health=False, name="flaky", unhealthy_strikes=5, degraded_after=1))
        wd.poll_all()
        report = wd.poll_all()
        assert not report.overall_healthy

    def test_poll_all_sets_overall_healthy_false_with_one_circuit_open(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="ok"))
        cfg = _cfg(
            health=False,
            restart_ok=False,
            restart_cooldown=0.0,
            name="broken",
            unhealthy_strikes=1,
            degraded_after=1,
            max_restarts=1,
            backoff_base_s=0.0,
        )
        wd.register(cfg)
        wd.poll_all()
        wd.poll_all()
        report = wd.poll_all()
        assert not report.overall_healthy

    def test_cycle_count_does_not_reset_on_report(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        wd.poll_all()
        wd.poll_all()
        assert wd.cycle_count == 2
        wd.report()
        assert wd.cycle_count == 2

    def test_reset_all_clears_all_watchers(self):
        wd = WatchdogV2()
        cfg = _cfg(
            health=False,
            restart_ok=False,
            restart_cooldown=0.0,
            unhealthy_strikes=1,
            degraded_after=1,
            max_restarts=2,
            backoff_base_s=0.0,
        )
        wd.register(cfg)
        for _ in range(5):
            wd.poll_all()
        wd.reset_all()
        s = wd.get("test").status()
        assert s.state == ServiceState.HEALTHY
        assert s.total_checks == 0
        assert s.total_failures == 0
        assert s.restart_count == 0
        assert s.strikes == 0

    def test_empty_watchdog_poll_all(self):
        wd = WatchdogV2()
        report = wd.poll_all()
        assert report.services == []
        assert report.overall_healthy

    def test_empty_watchdog_report(self):
        wd = WatchdogV2()
        report = wd.report()
        assert report.services == []
        assert report.overall_healthy

    def test_timestamp_monotonic_increases(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True))
        r1 = wd.poll_all()
        r2 = wd.poll_all()
        assert r2.timestamp >= r1.timestamp


# ═══════════════════════════════════════════════════════════════════════════════
# 12. DEEP EDGE CASES — CONFIG BOUNDARIES
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigBoundaries:
    def test_zero_check_interval(self):
        cfg = _cfg(health=True, check_interval_s=0.0)
        assert cfg.check_interval_s == 0.0

    def test_negative_check_interval(self):
        cfg = _cfg(health=True, check_interval_s=-5.0)
        assert cfg.check_interval_s == -5.0

    def test_very_large_max_restarts(self):
        w = ServiceWatcher(
            _cfg(
                health=False,
                restart_ok=False,
                restart_cooldown=0.0,
                max_restarts=10_000,
                unhealthy_strikes=1,
                degraded_after=1,
                backoff_base_s=0.0,
            )
        )
        w._cfg.health_check = lambda: False
        w.check()
        for _ in range(50):
            w.attempt_restart()
        assert w.state != ServiceState.CIRCUIT_OPEN

    def test_max_backoff_s_custom_value(self):
        cfg = _cfg(health=True, max_backoff_s=60.0)
        bt = BackoffTimer(base_s=10.0, multiplier=10.0, max_s=cfg.max_backoff_s)
        for _ in range(5):
            bt.record_restart()
        assert bt.current_delay == 60.0

    def test_degraded_after_missed_zero(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=3, degraded_after=0))
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.DEGRADED

    def test_degraded_after_missed_one_on_first_failure(self):
        w = ServiceWatcher(_cfg(health=True, unhealthy_strikes=5, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.DEGRADED

    def test_unhealthy_strike_count_one(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=1, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        assert w.state == ServiceState.UNHEALTHY


# ═══════════════════════════════════════════════════════════════════════════════
# 13. SERIALIZATION EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════


class TestSerializationDeep:
    def test_circuit_open_state_serializes_correctly(self):
        w = ServiceWatcher(
            _cfg(
                health=False,
                restart_ok=False,
                restart_cooldown=0.0,
                max_restarts=1,
                unhealthy_strikes=1,
                degraded_after=1,
                backoff_base_s=0.0,
            )
        )
        w._cfg.health_check = lambda: False
        w.check()
        w.attempt_restart()
        assert w.state == ServiceState.UNHEALTHY
        w.attempt_restart()
        assert w.state == ServiceState.CIRCUIT_OPEN
        d = w.status().to_dict()
        assert d["state"] == "circuit_open"
        assert d["circuit_open_reason"] is not None

    def test_degraded_state_serializes_correctly(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=5, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        d = w.status().to_dict()
        assert d["state"] == "degraded"

    def test_unhealthy_state_serializes_correctly(self):
        w = ServiceWatcher(_cfg(health=False, unhealthy_strikes=1, degraded_after=1))
        w._cfg.health_check = lambda: False
        w.check()
        d = w.status().to_dict()
        assert d["state"] == "unhealthy"

    def test_report_to_dict_with_mixed_states(self):
        wd = WatchdogV2()
        wd.register(_cfg(health=True, name="a"))
        cfg = _cfg(
            health=False,
            restart_ok=False,
            restart_cooldown=0.0,
            name="b",
            unhealthy_strikes=1,
            degraded_after=1,
            max_restarts=1,
            backoff_base_s=0.0,
        )
        wd.register(cfg)
        wd.poll_all()
        wd.poll_all()
        d = wd.report().to_dict()
        states = {s["name"]: s["state"] for s in d["services"]}
        assert states["a"] == "healthy"
        assert not d["overall_healthy"]
