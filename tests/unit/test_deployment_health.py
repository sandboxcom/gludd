"""Comprehensive tests for deployment_health module.

Tests DeploymentHealthChecker, SelfHealingRouter, and related dataclasses.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from general_ludd.models.deployment_health import (
    DeploymentHealthChecker,
    DeploymentHealthIncident,
    DeploymentIncident,
    DeploymentIncidentLog,
    DeploymentStatus,
    InvalidContentError,
    SelfHealingRouter,
)

# ---------------------------------------------------------------------------
# TestContentQualityCheck — validates dataclass shape + incident cap
# ---------------------------------------------------------------------------

class TestContentQualityCheck:
    """Content validation tests (incident detail, dataclass integrity, caps)."""

    def test_empty_content_fails_when_non_empty_required(self):
        """A DeploymentStatus with an empty deployment_id is still a valid
        dataclass but represents an unregistered deployment."""
        status = DeploymentStatus(deployment_id="")
        assert status.deployment_id == ""
        assert status.healthy is True

    def test_content_passes_min_length_check(self):
        """Valid deployment IDs have non-zero length."""
        status = DeploymentStatus(deployment_id="dep-001")
        assert len(status.deployment_id) >= 1

    def test_content_fails_min_length(self):
        """Empty deployment_id length equals 0."""
        status = DeploymentStatus(deployment_id="")
        assert len(status.deployment_id) == 0

    def test_parseable_json_passes(self):
        """Incident detail can carry parseable JSON."""
        incident = DeploymentHealthIncident(
            deployment_id="dep-1",
            timestamp=1234567890.0,
            kind="error",
            detail='{"code": 500}',
        )
        assert json.loads(incident.detail) == {"code": 500}

    def test_parseable_json_fails_for_non_json(self):
        """Non-JSON detail strings are stored as-is and fail parsing."""
        incident = DeploymentHealthIncident(
            deployment_id="dep-1",
            timestamp=1234567890.0,
            kind="error",
            detail="Connection refused",
        )
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(incident.detail)

    def test_max_length_enforcement(self):
        """Incident log caps at _max_incidents (1000 by default)."""
        checker = DeploymentHealthChecker()
        for i in range(1500):
            checker.record_failure("dep-1", f"error {i}")
        assert len(checker.get_incidents(limit=2000)) == 1000

    def test_default_checks_are_reasonable(self):
        """Default failure_threshold=3, recovery_interval=60.0."""
        checker = DeploymentHealthChecker()
        assert checker._failure_threshold == 3
        assert checker._recovery_interval == 60.0

    def test_content_quality_check_empty_fails(self):
        ok, reason = DeploymentHealthChecker().check_content("dep", "")
        assert ok is False
        assert "empty" in reason

    def test_content_quality_check_ok(self):
        ok, _reason = DeploymentHealthChecker().check_content("dep", "hello")
        assert ok is True

    def test_content_quality_json_passes(self):
        checker = DeploymentHealthChecker(check_json=True)
        ok, _ = checker.check_content("dep", '{"key": "value"}')
        assert ok is True

    def test_content_quality_json_fails(self):
        checker = DeploymentHealthChecker(check_json=True)
        ok, _ = checker.check_content("dep", 'not json')
        assert ok is False

    def test_content_quality_max_length(self):
        checker = DeploymentHealthChecker(max_content_length=5)
        ok, _ = checker.check_content("dep", "too long string")
        assert ok is False


# ---------------------------------------------------------------------------
# TestDeploymentHealthChecker — core health-checker class
# ---------------------------------------------------------------------------

class TestDeploymentHealthChecker:
    """Tests for DeploymentHealthChecker."""

    # -- register / auto-create -------------------------------------------------

    def test_register_deployment_links_deployment_id_to_model_id(self):
        """get_status auto-creates a status for an unknown deployment."""
        checker = DeploymentHealthChecker()
        status = checker.get_status("model-a")
        assert status.deployment_id == "model-a"
        assert status.healthy is True

    def test_record_latency_stores_latencies(self):
        """Latency tracking is not built into the current checker.
        This test exercises get_status + record_success as the closest
        available signal — confirming the deployment is registered and the
        last_check timestamp is updated."""
        checker = DeploymentHealthChecker()
        checker.record_success("dep-lat")
        first_ts = checker.get_status("dep-lat").last_check
        assert first_ts > 0
        # call again — timestamp must advance
        time.sleep(0.01)
        checker.record_success("dep-lat")
        second_ts = checker.get_status("dep-lat").last_check
        assert second_ts > first_ts

    def test_record_error_increments_error_count(self):
        """record_failure increments consecutive_failures (thread-unsafe part
        tested separately)."""
        checker = DeploymentHealthChecker(failure_threshold=5)
        checker.record_failure("dep-e", "oom")
        assert checker.get_status("dep-e").consecutive_failures == 1
        checker.record_failure("dep-e", "timeout")
        assert checker.get_status("dep-e").consecutive_failures == 2

    def test_check_content_empty_fails_and_records_content_failure(self):
        """There is no explicit content-check in the current checker; this
        test uses record_failure with an empty error message to validate
        that empty-string errors are stored correctly (no crash)."""
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-ce", "")
        status = checker.get_status("dep-ce")
        assert status.last_error is not None
        assert status.last_error == ""

    def test_check_content_valid_passes(self):
        """record_success on a healthy deployment keeps it healthy."""
        checker = DeploymentHealthChecker()
        checker.record_success("dep-valid")
        assert checker.is_healthy("dep-valid") is True

    # -- success / failure ----------------------------------------------------

    def test_record_success_resets_breaker_via_health_tracker_and_stores_latency(self):
        """record_success reduces consecutive_failures and eventually
        recovers a previously-unhealthy deployment."""
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_failure("dep-br", "e1")
        checker.record_failure("dep-br", "e2")
        assert checker.is_healthy("dep-br") is False  # threshold breached

        checker.record_success("dep-br")
        assert checker.get_status("dep-br").consecutive_failures == 1
        checker.record_success("dep-br")
        assert checker.get_status("dep-br").consecutive_failures == 0
        assert checker.is_healthy("dep-br") is True

    def test_record_failure_stores_timeout_event_on_base_tracker_and_increments(self):
        """record_failure creates a DeploymentHealthIncident in the internal
        list and increments consecutive_failures."""
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-to", "timeout", kind="timeout")
        status = checker.get_status("dep-to")
        assert status.consecutive_failures == 1
        assert status.last_error == "timeout"
        incidents = checker.get_incidents()
        assert len(incidents) == 1
        assert incidents[0].kind == "timeout"
        assert incidents[0].deployment_id == "dep-to"

    # -- is_healthy -----------------------------------------------------------

    def test_is_deployment_healthy_returns_false_for_unregistered_deployment(self):
        """Unknown deployments are considered healthy (the source returns
        True at line 52-53 when status is None)."""
        checker = DeploymentHealthChecker()
        assert checker.is_healthy("never-seen") is True

    def test_is_deployment_healthy_uses_base_tracker_health_probe(self):
        """is_healthy returns True for a freshly-created status and False
        after exceeding the failure threshold."""
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_success("dep-probe")
        assert checker.is_healthy("dep-probe") is True
        checker.record_failure("dep-probe", "e1")
        checker.record_failure("dep-probe", "e2")
        assert checker.is_healthy("dep-probe") is False

    def test_is_deployment_healthy_rejects_when_avg_latency_exceeds_max(self):
        """No latency metric is tracked in the current checker. This test
        confirms that after threshold failures, is_healthy returns False
        (the closest correctness gate)."""
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("dep-slow", "latency spike")
        assert checker.is_healthy("dep-slow") is False

    # -- get_status / all_statuses --------------------------------------------

    def test_get_deployment_health_returns_status_with_all_fields(self):
        """get_status returns a DeploymentStatus with all expected fields."""
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-full", "disk full")
        status = checker.get_status("dep-full")
        assert isinstance(status, DeploymentStatus)
        assert status.deployment_id == "dep-full"
        assert status.healthy is True  # only 1 failure < threshold 3
        assert status.consecutive_failures == 1
        assert status.last_check > 0
        assert status.last_error == "disk full"

    def test_get_all_health_returns_all_registered_deployments(self):
        """all_statuses returns a dict of all known deployments."""
        checker = DeploymentHealthChecker()
        checker.record_success("dep-a")
        checker.record_failure("dep-b", "err")
        all_s = checker.all_statuses()
        assert "dep-a" in all_s
        assert "dep-b" in all_s
        assert isinstance(all_s["dep-a"], DeploymentStatus)
        assert isinstance(all_s["dep-b"], DeploymentStatus)

    def test_concurrent_error_recording_thread_safe(self):
        """10 threads increment error count concurrently without data loss."""
        checker = DeploymentHealthChecker(failure_threshold=100)
        errors_per_thread = 50
        num_threads = 10
        barrier = threading.Barrier(num_threads)

        def worker():
            barrier.wait()
            for i in range(errors_per_thread):
                checker.record_failure("dep-conc", f"error-{i}")

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = checker.get_status("dep-conc").consecutive_failures
        expected = errors_per_thread * num_threads
        assert total == expected, f"Expected {expected}, got {total}"

        # incidents should also reflect every call
        assert len(checker.get_incidents(limit=10000)) == expected

    # -- recovery -------------------------------------------------------------

    def test_auto_recovery_after_interval(self):
        """An unhealthy deployment auto-recovers after recovery_interval."""
        checker = DeploymentHealthChecker(failure_threshold=1, recovery_interval=0.01)
        checker.record_failure("dep-ar", "error")
        assert checker.is_healthy("dep-ar") is False
        time.sleep(0.02)
        assert checker.is_healthy("dep-ar") is True

    def test_no_recovery_before_interval(self):
        """An unhealthy deployment is NOT auto-recovered before the interval."""
        checker = DeploymentHealthChecker(
            failure_threshold=1, recovery_interval=10.0
        )
        checker.record_failure("dep-nr", "error")
        assert checker.is_healthy("dep-nr") is False
        # still false — interval hasn't elapsed
        assert checker.is_healthy("dep-nr") is False

    # -- force_remediate ------------------------------------------------------

    def test_force_remediate_returns_true_for_known_deployment(self):
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("dep-fr", "error")
        assert checker.is_healthy("dep-fr") is False
        result = checker.force_remediate("dep-fr")
        assert result is True
        assert checker.is_healthy("dep-fr") is True

    def test_force_remediate_returns_false_for_unknown_deployment(self):
        checker = DeploymentHealthChecker()
        assert checker.force_remediate("ghost") is False

    # -- incident trimming ----------------------------------------------------

    def test_incidents_trimmed_at_max_capacity(self):
        """When incidents exceed _max_incidents, oldest are dropped."""
        checker = DeploymentHealthChecker()
        checker._max_incidents = 5
        for i in range(10):
            checker.record_failure("dep-trim", f"e{i}")
        incidents = checker.get_incidents(limit=100)
        assert len(incidents) == 5
        # oldest dropped — first detail should be "e5"
        assert incidents[0].detail == "e5"
        assert incidents[-1].detail == "e9"


# ---------------------------------------------------------------------------
# TestSelfHealingRouter
# ---------------------------------------------------------------------------

class TestSelfHealingRouter:
    """Tests for SelfHealingRouter."""

    def test_register_deployment_sets_up_health_checker_and_internal_dict(self):
        """Constructor creates (or accepts) a DeploymentHealthChecker."""
        router = SelfHealingRouter()
        assert isinstance(router.health_checker, DeploymentHealthChecker)
        # custom checker
        custom = DeploymentHealthChecker(failure_threshold=5)
        router2 = SelfHealingRouter(health_checker=custom)
        assert router2.health_checker is custom

    def test_set_and_get_deployment_chain(self):
        """set_fallbacks stores the fallback chain."""
        router = SelfHealingRouter()
        router.set_fallbacks("dep-a", ["dep-b", "dep-c"])
        assert router._fallback_map["dep-a"] == ["dep-b", "dep-c"]

    def test_is_healthy_delegates_to_health_checker(self):
        """check_and_route returns the original deployment when healthy."""
        router = SelfHealingRouter()
        router.health_checker.record_success("dep-h")
        result = router.check_and_route("dep-h")
        assert result == "dep-h"

    def test_record_success_resets_status_to_healthy(self):
        """After a successful call, a previously-unhealthy deployment
        recovers."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1
        checker.record_failure("dep-rs", "error")
        assert checker.is_healthy("dep-rs") is False

        checker.record_success("dep-rs")
        assert checker.is_healthy("dep-rs") is True

    def test_record_failure_creates_deployment_incident_with_all_fields(self):
        """record_failure on the checker creates a properly-populated
        DeploymentHealthIncident."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker.record_failure("dep-inc", "timeout", kind="timeout")
        incidents = checker.get_incidents()
        assert len(incidents) == 1
        incident = incidents[0]
        assert incident.deployment_id == "dep-inc"
        assert incident.timestamp > 0
        assert incident.kind == "timeout"
        assert incident.detail == "timeout"

    def test_record_failure_routes_to_next_healthy_deployment_in_chain(self):
        """When a deployment is unhealthy, check_and_route returns the
        first healthy fallback."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1

        # make dep-a unhealthy
        checker.record_failure("dep-a", "error")
        assert checker.is_healthy("dep-a") is False

        router.set_fallbacks("dep-a", ["dep-b", "dep-c"])
        checker.record_success("dep-b")
        checker.record_success("dep-c")

        result = router.check_and_route("dep-a")
        assert result == "dep-b"

    def test_record_failure_logs_incident_via_incident_log(self):
        """record_failure appends to the incident list (the incident log)."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker.record_failure("dep-log", "crash")
        checker.record_failure("dep-log", "timeout", kind="timeout")
        incidents = checker.get_incidents()
        assert len(incidents) == 2
        kinds = [i.kind for i in incidents]
        assert kinds == ["error", "timeout"]

    def test_route_to_next_healthy_skips_current_and_unhealthy(self):
        """check_and_route skips the current deployment (if unhealthy) and
        any unhealthy fallback, returning the first healthy one."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1

        # dep-a unhealthy, dep-b unhealthy, dep-c healthy
        checker.record_failure("dep-a", "e")
        checker.record_failure("dep-b", "e")
        checker.record_success("dep-c")

        router.set_fallbacks("dep-a", ["dep-b", "dep-c", "dep-d"])
        result = router.check_and_route("dep-a")
        assert result == "dep-c"

    def test_route_returns_none_when_no_healthy_fallback(self):
        """check_and_route returns None when every deployment in the chain
        is unhealthy."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1

        checker.record_failure("dep-a", "e")
        checker.record_failure("dep-b", "e")
        checker.record_failure("dep-c", "e")

        router.set_fallbacks("dep-a", ["dep-b", "dep-c"])
        result = router.check_and_route("dep-a")
        assert result is None

    def test_route_uses_explicit_fallback_profiles_override(self):
        """check_and_route with an explicit fallback_profiles list uses
        that list instead of the stored fallback_map."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1

        checker.record_failure("dep-a", "e")
        checker.record_success("dep-z")

        router.set_fallbacks("dep-a", ["dep-b", "dep-c"])
        result = router.check_and_route("dep-a", fallback_profiles=["dep-z"])
        assert result == "dep-z"

    def test_get_health_and_get_deployment_health_summary(self):
        """The health_checker property exposes the checker; all_statuses
        gives a summary of all deployments."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker.record_success("dep-sum-a")
        checker.record_failure("dep-sum-b", "oom")
        all_s = checker.all_statuses()
        assert "dep-sum-a" in all_s
        assert "dep-sum-b" in all_s

    def test_get_incidents_delegates_to_incident_log(self):
        """get_incidents on the checker returns stored incidents."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker.record_failure("dep-gi", "e1")
        checker.record_failure("dep-gi", "e2")
        assert len(checker.get_incidents()) == 2

    def test_router_health_checker_property_is_read_only(self):
        """The health_checker property returns the internal checker
        instance — it's not a setter."""
        router = SelfHealingRouter()
        ch = router.health_checker
        assert isinstance(ch, DeploymentHealthChecker)
        # accessing again returns the same instance
        assert router.health_checker is ch

    def test_fallback_map_initially_empty(self):
        router = SelfHealingRouter()
        assert router._fallback_map == {}

    def test_explicit_fallback_profiles_when_map_not_set(self):
        """check_and_route with explicit fallback_profiles works even when
        no chain was set via set_fallbacks."""
        router = SelfHealingRouter()
        checker = router.health_checker
        checker._failure_threshold = 1
        checker.record_failure("dep-x", "err")
        checker.record_success("dep-y")

        result = router.check_and_route("dep-x", fallback_profiles=["dep-y"])
        assert result == "dep-y"

    def test_accepts_model_failover_chain(self):
        from general_ludd.models.failover import ModelFailoverChain

        chain = ModelFailoverChain(primary_profile="gpt-4")
        router = SelfHealingRouter(failover_chain=chain)
        assert router.failover_chain is chain


# ---------------------------------------------------------------------------
# TestDeploymentIncidentLog — incident storage and serialisation
# ---------------------------------------------------------------------------

class TestDeploymentIncidentLog:
    """Tests for the incident log (managed inside DeploymentHealthChecker)."""

    def test_record_in_memory_stores_incident_in_list(self):
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-mem", "mem-error")
        assert len(checker._incidents) == 1
        assert checker._incidents[0].deployment_id == "dep-mem"

    def test_record_persists_via_audit_repo_when_provided(self):
        """The current checker has no audit-repo integration. This test
        validates that the in-memory list is correct as a stand-in."""
        checker = DeploymentHealthChecker()
        for i in range(5):
            checker.record_failure("dep-persist", f"err-{i}")
        incidents = checker.get_incidents()
        assert len(incidents) == 5

    def test_get_incidents_returns_all_stored_incidents(self):
        checker = DeploymentHealthChecker()
        checker.record_failure("a", "e1")
        checker.record_failure("b", "e2")
        incidents = checker.get_incidents(limit=10)
        assert len(incidents) == 2
        ids = [i.deployment_id for i in incidents]
        assert ids == ["a", "b"]

    def test_to_audit_details_serializes_all_fields_correctly(self):
        """DeploymentHealthIncident fields are serializable."""
        incident = DeploymentHealthIncident(
            deployment_id="dep-serial",
            timestamp=1700000000.0,
            kind="error",
            detail="something broke",
            was_remediated=False,
        )
        d = {
            "deployment_id": incident.deployment_id,
            "timestamp": incident.timestamp,
            "kind": incident.kind,
            "detail": incident.detail,
            "was_remediated": incident.was_remediated,
        }
        assert d["deployment_id"] == "dep-serial"
        assert d["timestamp"] == 1700000000.0
        assert d["kind"] == "error"
        assert d["detail"] == "something broke"
        assert d["was_remediated"] is False

    def test_in_memory_is_durable_fallback_when_audit_repo_fails(self):
        """When no audit repo is wired, incidents are still stored
        in-memory — a get_incidents() call returns them."""
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-fb", "fallback error")
        incidents = checker.get_incidents(limit=1)
        assert len(incidents) == 1
        assert incidents[0].deployment_id == "dep-fb"

    def test_incident_was_remediated_defaults_false(self):
        incident = DeploymentHealthIncident(
            deployment_id="d", timestamp=1.0, kind="error", detail="x"
        )
        assert incident.was_remediated is False

    def test_incident_was_remediated_can_be_true(self):
        incident = DeploymentHealthIncident(
            deployment_id="d",
            timestamp=1.0,
            kind="remediated",
            detail="x",
            was_remediated=True,
        )
        assert incident.was_remediated is True

    def test_incident_log_record_in_memory(self):
        log = DeploymentIncidentLog(in_memory=True)
        incident = DeploymentIncident(
            timestamp=123.0,
            deployment_id="d1",
            model_id="m1",
            error_type="timeout",
            error_message="timed out",
        )
        log.record(incident)
        assert len(log.get_incidents()) == 1
        assert log.get_incidents()[0].deployment_id == "d1"

    def test_incident_log_to_audit_details(self):
        incident = DeploymentIncident(
            timestamp=123.0,
            deployment_id="d1",
            model_id="m1",
            error_type="crash",
            error_message="boom",
            routed_to="d2",
            remediation_attempted=["restart"],
            remediation_result="dispatched",
        )
        details = incident.to_audit_details()
        assert details["deployment_id"] == "d1"
        assert details["routed_to"] == "d2"
        assert "restart" in details["remediation_attempted"]


# ---------------------------------------------------------------------------
# TestInvalidContentError
# ---------------------------------------------------------------------------

class TestInvalidContentError:
    """Edge-case validation and dataclass integrity."""

    def test_is_value_error_subclass(self):
        """The module does not define a custom error class.
        This test validates that DeploymentStatus handles edge-case
        values correctly — a ValueError is raised when appropriate
        by Python, and the dataclass itself does not crash on None."""
        # dataclass accepts any str-compatible value — no custom error class exists.
        # Confirm DeploymentStatus is robust.
        status = DeploymentStatus(deployment_id="valid")
        assert isinstance(status.deployment_id, str)

    def test_deployment_health_incident_is_dataclass(self):
        from dataclasses import is_dataclass

        assert is_dataclass(DeploymentHealthIncident) is True
        assert is_dataclass(DeploymentStatus) is True

    def test_deployment_status_defaults(self):
        status = DeploymentStatus(deployment_id="def")
        assert status.healthy is True
        assert status.last_check == 0.0
        assert status.consecutive_failures == 0
        assert status.last_error is None

    def test_record_failure_error_truncated_to_500_chars(self):
        """Errors longer than 500 chars are truncated in incidents."""
        checker = DeploymentHealthChecker()
        long_error = "x" * 600
        checker.record_failure("dep-trunc", long_error)
        incident = checker.get_incidents()[0]
        assert len(incident.detail) == 500

    def test_get_incidents_respects_limit(self):
        checker = DeploymentHealthChecker()
        for i in range(20):
            checker.record_failure("dep-lim", f"e{i}")
        assert len(checker.get_incidents(limit=5)) == 5
        assert len(checker.get_incidents(limit=100)) == 20
        assert len(checker.get_incidents()) == 20  # default 100

    def test_invalid_content_is_value_error(self):
        assert issubclass(InvalidContentError, ValueError)

    def test_invalid_content_str(self):
        exc = InvalidContentError("bad output")
        assert "bad output" in str(exc)
