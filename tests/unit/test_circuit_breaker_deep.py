"""Deep circuit breaker and fault tolerance tests for ModelHealthTracker.

Covers the full state machine: CLOSED (healthy), OPEN (tripped), HALF-OPEN (probing).
Tests failure threshold, cooldown/recovery timeout, single-flight probe guards,
non-retryable bypass, state transitions, cascading failure prevention, and the
DeploymentHealthChecker wrapper layer.

Covers ModelHealthTracker from timeout_detector.py and DeploymentHealthChecker
from deployment_health.py.
"""

from __future__ import annotations

import threading
import time

from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutEvent,
    TimeoutKind,
)


class TestCircuitBreakerClosedState:
    """CLOSED state: circuit is healthy, calls are allowed."""

    def test_initially_healthy(self):
        """Circuit starts CLOSED — is_healthy returns True for any model."""
        cb = ModelHealthTracker()
        assert cb.is_healthy("model-a") is True
        assert cb.is_healthy("model-b") is True

    def test_stays_healthy_below_threshold(self):
        """Below failure_threshold, circuit remains CLOSED."""
        cb = ModelHealthTracker(failure_threshold=3)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        assert cb.is_healthy("m1") is True

    def test_success_resets_consecutive_counter(self):
        """A success resets the consecutive failure counter to 0."""
        cb = ModelHealthTracker(failure_threshold=2)
        cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        cb.record_success("m1")
        assert cb.is_healthy("m1") is True
        assert cb.get_health("m1")["consecutive_failures"] == 0


class TestCircuitBreakerOpenState:
    """OPEN state: failure threshold reached, circuit blocks calls until cooldown."""

    def test_opens_at_threshold(self):
        """Exactly at failure_threshold, circuit opens."""
        cb = ModelHealthTracker(failure_threshold=3)
        for _ in range(3):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.READ_TIMEOUT, time.monotonic(), 2.0))
        assert cb.is_healthy("m1") is False

    def test_opens_beyond_threshold(self):
        """Beyond failure_threshold, circuit stays open."""
        cb = ModelHealthTracker(failure_threshold=2)
        for _ in range(5):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.PROVIDER_ERROR, time.monotonic(), 0.5))
        assert cb.is_healthy("m1") is False

    def test_healthy_while_open_does_not_reset_counter(self):
        """is_healthy returning False must not reset the consecutive counter."""
        cb = ModelHealthTracker(failure_threshold=2)
        for _ in range(3):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        assert cb.is_healthy("m1") is False
        assert cb.get_health("m1")["consecutive_failures"] == 3

    def test_non_retryable_errors_do_not_trip_circuit(self):
        """AUTH_ERROR and CONTEXT_LENGTH are always healthy — they never trip."""
        cb = ModelHealthTracker(failure_threshold=1)
        cb.record_event(TimeoutEvent("m1", TimeoutKind.AUTH_ERROR, time.monotonic(), 0.1))
        assert cb.is_healthy("m1") is True
        cb.record_event(TimeoutEvent("m1", TimeoutKind.CONTEXT_LENGTH, time.monotonic(), 0.1))
        assert cb.is_healthy("m1") is True

    def test_rate_limited_counts_toward_threshold(self):
        """RATE_LIMITED IS counted toward the threshold — it trips the breaker."""
        cb = ModelHealthTracker(failure_threshold=2)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.RATE_LIMITED, time.monotonic(), 0.1))
        assert cb.is_healthy("m1") is False


class TestCircuitBreakerHalfOpenState:
    """HALF-OPEN state: cooldown elapsed, exactly one probe admitted."""

    def test_cooldown_elapsed_admits_single_probe(self):
        """After cooldown, exactly one caller gets through as a probe."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        assert cb.is_healthy("m1") is False
        time.sleep(0.02)  # exceed cooldown
        assert cb.is_healthy("m1") is True  # first probe admitted
        assert cb.is_healthy("m1") is False  # second caller blocked

    def test_probe_success_re_closes_circuit(self):
        """A successful half-open probe resets the breaker to CLOSED."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.PROVIDER_ERROR, time.monotonic(), 1.0))
        time.sleep(0.02)
        assert cb.is_healthy("m1") is True  # probe admitted
        cb.record_success("m1")
        assert cb.is_healthy("m1") is True  # now CLOSED
        assert cb.get_health("m1")["consecutive_failures"] == 0

    def test_probe_failure_re_arms_circuit(self):
        """A failed half-open probe re-opens the breaker."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        time.sleep(0.02)
        assert cb.is_healthy("m1") is True  # probe admitted
        cb.record_event(TimeoutEvent("m1", TimeoutKind.READ_TIMEOUT, time.monotonic(), 2.0))
        assert cb.is_healthy("m1") is False  # breaker re-armed

    def test_status_poll_never_consumes_probe(self):
        """get_health() uses admit_probe=False — it never consumes the half-open slot."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        time.sleep(0.02)
        h1 = cb.get_health("m1")
        assert h1["healthy"] is True  # poll says healthy (would admit)
        assert cb.is_healthy("m1") is True  # real probe still available


class TestRecoveryTimeoutBoundaries:
    """Recovery cooldown timeout boundary conditions and edge cases."""

    def test_within_cooldown_stays_open(self):
        """During cooldown window, the breaker stays OPEN."""
        cb = ModelHealthTracker(failure_threshold=1, cooldown_seconds=60.0)
        cb.record_event(TimeoutEvent("m1", TimeoutKind.PROVIDER_ERROR, time.monotonic(), 5.0))
        assert cb.is_healthy("m1") is False

    def test_probe_slot_leak_auto_expiry(self):
        """A leaked probe slot (caller never record_event/record_success) expires after cooldown."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        time.sleep(0.02)
        assert cb.is_healthy("m1") is True  # probe slot claimed
        # Simulate caller crash — never calls record_event or record_success
        time.sleep(0.02)  # exceed cooldown — slot is stale
        assert cb.is_healthy("m1") is True  # fresh probe admitted after stale slot expires

    def test_multiple_models_independent_circuits(self):
        """Each model_id has its own independent circuit breaker."""
        cb = ModelHealthTracker(failure_threshold=2)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        assert cb.is_healthy("m1") is False  # m1 is OPEN
        assert cb.is_healthy("m2") is True  # m2 is CLOSED (no failures)


class TestCascadingPrevention:
    """Prevent cascading failures: concurrent callers, thundering herd, and
    the half-open probe stampede guard."""

    def test_concurrent_threads_only_one_probe(self):
        """Multiple concurrent callers during half-open — only one admitted."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        time.sleep(0.02)

        admitted = [0]
        lock = threading.Lock()

        def caller():
            if cb.is_healthy("m1"):
                with lock:
                    admitted[0] += 1

        threads = [threading.Thread(target=caller) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert admitted[0] == 1, f"Expected exactly 1 probe admitted, got {admitted[0]}"

    def test_record_event_clears_probe_flag(self):
        """record_event clears the in-flight probe flag so a fresh probe can be
        admitted after the next cooldown."""
        cb = ModelHealthTracker(failure_threshold=2, cooldown_seconds=0.01)
        for _ in range(2):
            cb.record_event(TimeoutEvent("m1", TimeoutKind.CONNECTION_TIMEOUT, time.monotonic(), 1.0))
        time.sleep(0.02)
        assert cb.is_healthy("m1") is True  # probe slot claimed
        # The probe fails (record_event), which clears the flag
        cb.record_event(TimeoutEvent("m1", TimeoutKind.READ_TIMEOUT, time.monotonic(), 2.0))
        # Wait another cooldown
        time.sleep(0.02)
        assert cb.is_healthy("m1") is True  # fresh probe admitted

    def test_non_retryable_never_blocks_half_open(self):
        """AUTH_ERROR and CONTEXT_LENGTH always pass is_healthy even at threshold."""
        cb = ModelHealthTracker(failure_threshold=1, cooldown_seconds=0.01)
        cb.record_event(TimeoutEvent("m1", TimeoutKind.AUTH_ERROR, time.monotonic(), 0.1))
        assert cb.is_healthy("m1") is True
        # Record more — still healthy
        cb.record_event(TimeoutEvent("m1", TimeoutKind.CONTEXT_LENGTH, time.monotonic(), 0.1))
        assert cb.is_healthy("m1") is True


class TestDeploymentHealthWrapper:
    """DeploymentHealthChecker wraps ModelHealthTracker with additional
    deployment→model mapping, content quality checks, and latency tracking."""

    def test_is_healthy_delegates_to_tracker(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=2, recovery_interval=0.01)
        assert dh.is_healthy("dep-a") is True
        for _ in range(2):
            dh.record_failure("dep-a", Exception("boom"))
        assert dh.is_healthy("dep-a") is False

    def test_record_success_resets_underlying_tracker(self):
        import time as _time

        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=2, recovery_interval=0.01)
        for _ in range(2):
            dh.record_failure("dep-a", Exception("boom"))
        assert dh.is_healthy("dep-a") is False
        dh.record_success("dep-a")
        _time.sleep(0.02)
        assert dh.is_healthy("dep-a") is True

    def test_deployment_to_model_mapping(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=2)
        dh.set_deployment_model("dep-a", "model-x")
        # Failures on dep-a map to model-x's tracker
        for _ in range(2):
            dh.record_failure("dep-a", Exception("boom"))
        assert dh.is_healthy("dep-a") is False
        # dep-b still healthy (different deployment)
        assert dh.is_healthy("dep-b") is True

    def test_content_quality_check_failures_count(self):
        from general_ludd.models.deployment_health import (
            ContentQualityCheck,
            DeploymentHealthChecker,
        )

        cq = ContentQualityCheck(non_empty=True)
        dh = DeploymentHealthChecker(failure_threshold=2, content_quality=cq)
        assert dh.is_healthy("dep-a") is True
        for _ in range(2):
            dh.record_failure("dep-a", Exception("empty content"))
        assert dh.is_healthy("dep-a") is False

    def test_latency_threshold_marks_unhealthy(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=10, max_avg_latency_s=0.001)
        dh.record_latency("dep-a", 10.0)
        assert dh.is_healthy("dep-a") is False

    def test_incident_log_records_failures(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=10)
        dh.record_failure("dep-a", Exception("incident 1"))
        dh.record_failure("dep-a", Exception("incident 2"))
        incidents = dh.get_incidents()
        assert len(incidents) >= 2

    def test_all_statuses_reports_every_deployment(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=10)
        dh.record_failure("dep-a", Exception("fail"))
        dh.record_failure("dep-b", Exception("fail"))
        statuses = dh.all_statuses()
        assert "dep-a" in statuses
        assert "dep-b" in statuses

    def test_force_remediate_resets_health(self):
        from general_ludd.models.deployment_health import DeploymentHealthChecker

        dh = DeploymentHealthChecker(failure_threshold=2, recovery_interval=0.01)
        for _ in range(2):
            dh.record_failure("dep-a", Exception("boom"))
        assert dh.is_healthy("dep-a") is False
        dh.force_remediate("dep-a")
        assert dh.is_healthy("dep-a") is True
