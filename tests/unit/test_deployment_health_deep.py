"""Deep tests for DeploymentHealthChecker, covering latency, content quality,
threshold boundaries, model availability, incident pruning, and custom config.

Targets the 1.1% coverage gap with 25+ focused test cases.
"""

from __future__ import annotations

import time

import pytest

from general_ludd.models.deployment_health import (
    ContentQualityCheck,
    DeploymentHealthChecker,
    DeploymentIncident,
    DeploymentIncidentLog,
    DeploymentStatus,
    InvalidContentError,
)

# ---------------------------------------------------------------------------
# ContentQualityCheck — evaluate boundary conditions
# ---------------------------------------------------------------------------


class TestContentQualityCheckBoundaries:
    """Boundary testing for ContentQualityCheck.evaluate."""

    def test_non_empty_passes_with_content(self):
        cq = ContentQualityCheck(non_empty=True)
        ok, reason = cq.evaluate("hello")
        assert ok is True
        assert reason == "OK"

    def test_non_empty_fails_on_empty(self):
        cq = ContentQualityCheck(non_empty=True)
        ok, reason = cq.evaluate("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_min_length_boundary_exactly_at(self):
        cq = ContentQualityCheck(min_length=5)
        ok, _reason = cq.evaluate("12345")
        assert ok is True

    def test_min_length_boundary_just_below(self):
        cq = ContentQualityCheck(min_length=5)
        ok, reason = cq.evaluate("1234")
        assert ok is False
        assert "too short" in reason.lower()

    def test_max_length_boundary_exactly_at(self):
        cq = ContentQualityCheck(max_length=10)
        ok, _reason = cq.evaluate("1234567890")
        assert ok is True

    def test_max_length_boundary_just_above(self):
        cq = ContentQualityCheck(max_length=10)
        ok, reason = cq.evaluate("1234567890X")
        assert ok is False
        assert "too long" in reason.lower()

    def test_parseable_json_valid(self):
        cq = ContentQualityCheck(parseable_json=True)
        ok, _reason = cq.evaluate('{"a": 1}')
        assert ok is True

    def test_parseable_json_invalid(self):
        cq = ContentQualityCheck(parseable_json=True)
        ok, reason = cq.evaluate("not json")
        assert ok is False
        assert "not parseable JSON" in reason

    def test_parseable_json_empty_object(self):
        cq = ContentQualityCheck(parseable_json=True)
        ok, _reason = cq.evaluate("{}")
        assert ok is True

    def test_multiple_checks_all_pass(self):
        cq = ContentQualityCheck(non_empty=True, min_length=2, max_length=10)
        ok, _reason = cq.evaluate("hi")
        assert ok is True

    def test_multiple_checks_first_fails(self):
        cq = ContentQualityCheck(non_empty=True, min_length=2, max_length=10)
        ok, reason = cq.evaluate("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_min_length_checked_before_max_length(self):
        cq = ContentQualityCheck(non_empty=False, min_length=5, max_length=10)
        ok, reason = cq.evaluate("abc")
        assert ok is False
        assert "too short" in reason.lower()

    def test_all_defaults_pass_content(self):
        cq = ContentQualityCheck()
        ok, reason = cq.evaluate("anything")
        assert ok is True
        assert reason == "OK"


# ---------------------------------------------------------------------------
# DeploymentIncidentLog — pruning and persistence fallback
# ---------------------------------------------------------------------------


class TestDeploymentIncidentLogDeep:
    """Deep tests for DeploymentIncidentLog pruning and fallback."""

    def test_pruning_at_max_in_memory(self):
        log = DeploymentIncidentLog(in_memory=True, max_in_memory=3)
        for i in range(5):
            log.record(
                DeploymentIncident(
                    timestamp=float(i),
                    deployment_id=f"d{i}",
                    model_id="m",
                    error_type="err",
                    error_message=f"msg {i}",
                )
            )
        incidents = log.get_incidents()
        assert len(incidents) == 3
        assert incidents[0].deployment_id == "d2"
        assert incidents[-1].deployment_id == "d4"

    def test_pruning_exactly_at_capacity(self):
        log = DeploymentIncidentLog(in_memory=True, max_in_memory=3)
        for i in range(3):
            log.record(
                DeploymentIncident(
                    timestamp=float(i),
                    deployment_id=f"d{i}",
                    model_id="m",
                    error_type="err",
                    error_message=f"msg {i}",
                )
            )
        assert len(log.get_incidents()) == 3

    def test_default_max_in_memory_is_1000(self):
        log = DeploymentIncidentLog()
        assert log._max_in_memory == 1000

    def test_get_incidents_returns_copy_not_reference(self):
        log = DeploymentIncidentLog(in_memory=True)
        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="d1",
            model_id="m1",
            error_type="err",
            error_message="msg",
        )
        log.record(inc)
        copy1 = log.get_incidents()
        copy1.pop()
        assert len(log.get_incidents()) == 1

    def test_record_without_audit_repo_stores_in_memory_only(self):
        log = DeploymentIncidentLog()
        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="d1",
            model_id="m1",
            error_type="err",
            error_message="msg",
        )
        log.record(inc)
        assert len(log.get_incidents()) == 1

    def test_persist_async_no_event_loop_falls_back(self):
        log = DeploymentIncidentLog(in_memory=True)
        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="d1",
            model_id="m1",
            error_type="err",
            error_message="msg",
        )
        log._persist_async(inc)
        assert log._incidents == []


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — latency tracking
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerLatency:
    """Latency tracking and avg-latency threshold checks."""

    def test_record_latency_stores_sample(self):
        checker = DeploymentHealthChecker(max_latency_samples=5)
        checker.record_latency("dep-1", 0.5)
        with checker._lock:
            samples = checker._latency_samples["dep-1"]
        assert list(samples) == [0.5]

    def test_record_latency_prunes_old_samples(self):
        checker = DeploymentHealthChecker(max_latency_samples=3)
        for v in [0.1, 0.2, 0.3, 0.4, 0.5]:
            checker.record_latency("dep-1", v)
        with checker._lock:
            samples = checker._latency_samples["dep-1"]
        assert list(samples) == [0.3, 0.4, 0.5]

    def test_is_healthy_with_latency_just_below_threshold(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=1.0)
        checker.record_latency("dep-1", 0.5)
        checker.record_latency("dep-1", 0.5)
        checker.record_latency("dep-1", 0.5)
        checker.record_success("dep-1")
        assert checker.is_healthy("dep-1") is True

    def test_is_healthy_with_latency_just_above_threshold(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=0.5)
        checker.record_latency("dep-1", 0.3)
        checker.record_latency("dep-1", 0.9)
        checker.record_success("dep-1")
        assert checker.is_healthy("dep-1") is False

    def test_is_healthy_with_latency_exactly_at_threshold(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=0.5)
        checker.record_latency("dep-1", 0.5)
        checker.record_success("dep-1")
        assert checker.is_healthy("dep-1") is True

    def test_latency_check_ignored_when_no_samples(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=0.1)
        checker.record_success("dep-1")
        assert checker.is_healthy("dep-1") is True

    def test_latency_check_ignored_when_threshold_none(self):
        checker = DeploymentHealthChecker(max_avg_latency_s=None)
        checker.record_latency("dep-1", 10.0)
        checker.record_success("dep-1")
        assert checker.is_healthy("dep-1") is True


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — content quality integration
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerContentQuality:
    """Content quality check integration with ContentQualityCheck."""

    def test_check_content_uses_content_quality_check_fail(self):
        cq = ContentQualityCheck(min_length=100)
        checker = DeploymentHealthChecker(content_quality=cq)
        ok, reason = checker.check_content("dep-1", "short")
        assert ok is False
        assert "too short" in reason.lower()

    def test_check_content_uses_content_quality_check_pass(self):
        cq = ContentQualityCheck(min_length=3)
        checker = DeploymentHealthChecker(content_quality=cq)
        ok, _reason = checker.check_content("dep-1", "abc")
        assert ok is True

    def test_check_content_none_fails(self):
        checker = DeploymentHealthChecker()
        ok, reason = checker.check_content("dep-1", None)
        assert ok is False
        assert reason == "Content is None"

    def test_check_content_empty_string_fails(self):
        checker = DeploymentHealthChecker()
        ok, reason = checker.check_content("dep-1", "   ")
        assert ok is False
        assert "empty" in reason.lower()

    def test_check_content_json_with_dict_passes(self):
        checker = DeploymentHealthChecker(check_json=True)
        ok, _reason = checker.check_content("dep-1", {"key": "value"})
        assert ok is True

    def test_check_content_json_with_list_passes(self):
        checker = DeploymentHealthChecker(check_json=True)
        ok, _reason = checker.check_content("dep-1", [1, 2, 3])
        assert ok is True

    def test_check_content_json_with_int_fails(self):
        checker = DeploymentHealthChecker(check_json=True)
        ok, reason = checker.check_content("dep-1", 42)
        assert ok is False
        assert "not JSON-compatible" in reason

    def test_check_content_max_length_dict_serialized(self):
        checker = DeploymentHealthChecker(max_content_length=15)
        data = {"a" * 10: "b" * 10}
        ok, reason = checker.check_content("dep-1", data)
        assert ok is False
        assert "exceeds max" in reason

    def test_check_content_max_length_non_string(self):
        checker = DeploymentHealthChecker(max_content_length=5)
        ok, reason = checker.check_content("dep-1", [1, 2, 3, 4, 5, 6])
        assert ok is False
        assert "exceeds max" in reason


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — model availability (circuit breaker)
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerModelAvailability:
    """Circuit-breaker threshold boundary conditions."""

    def test_exactly_at_failure_threshold_marks_unhealthy(self):
        checker = DeploymentHealthChecker(failure_threshold=3)
        checker.record_failure("dep-1", "e1")
        checker.record_failure("dep-1", "e2")
        assert checker.is_healthy("dep-1") is True
        checker.record_failure("dep-1", "e3")
        assert checker.is_healthy("dep-1") is False

    def test_just_below_failure_threshold_remains_healthy(self):
        checker = DeploymentHealthChecker(failure_threshold=3)
        checker.record_failure("dep-1", "e1")
        checker.record_failure("dep-1", "e2")
        assert checker.is_healthy("dep-1") is True

    def test_just_above_failure_threshold_remains_unhealthy(self):
        checker = DeploymentHealthChecker(failure_threshold=3)
        checker.record_failure("dep-1", "e1")
        checker.record_failure("dep-1", "e2")
        checker.record_failure("dep-1", "e3")
        checker.record_failure("dep-1", "e4")
        assert checker.is_healthy("dep-1") is False

    def test_model_availability_via_deployment_model_mapping(self):
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.set_deployment_model("dep-1", "gpt-4")
        checker.record_failure("dep-1", "timeout")
        assert checker.is_healthy("dep-1") is False


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — custom initialization
# ---------------------------------------------------------------------------


class TestDeploymentHealthCheckerCustomInit:
    """Custom configuration initialization and internal state verification."""

    def test_full_custom_init(self):
        cq = ContentQualityCheck(non_empty=True, min_length=10)
        log = DeploymentIncidentLog(in_memory=True, max_in_memory=50)
        checker = DeploymentHealthChecker(
            failure_threshold=5,
            recovery_interval=30.0,
            max_content_length=500,
            check_json=True,
            max_avg_latency_s=2.0,
            max_latency_samples=100,
            incident_log=log,
            content_quality=cq,
        )
        assert checker._failure_threshold == 5
        assert checker._recovery_interval == 30.0
        assert checker._max_content_length == 500
        assert checker._check_json is True
        assert checker._max_avg_latency_s == 2.0
        assert checker._max_latency_samples == 100
        assert checker._incident_log is log
        assert checker._content_quality is cq

    def test_default_max_latency_samples_is_50(self):
        checker = DeploymentHealthChecker()
        assert checker._max_latency_samples == 50


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — validate_content raises
# ---------------------------------------------------------------------------


class TestValidateContent:
    """validate_content raises InvalidContentError on failures."""

    def test_validate_content_raises_on_empty(self):
        checker = DeploymentHealthChecker()
        with pytest.raises(InvalidContentError) as excinfo:
            checker.validate_content("")
        assert "empty" in str(excinfo.value).lower()

    def test_validate_content_raises_on_none(self):
        checker = DeploymentHealthChecker()
        with pytest.raises(InvalidContentError) as excinfo:
            checker.validate_content(None)
        assert "None" in str(excinfo.value)

    def test_validate_content_passes_with_valid_content(self):
        checker = DeploymentHealthChecker()
        checker.validate_content("valid content")

    def test_validate_content_uses_content_quality_check(self):
        cq = ContentQualityCheck(min_length=100)
        checker = DeploymentHealthChecker(content_quality=cq)
        with pytest.raises(InvalidContentError) as excinfo:
            checker.validate_content("short")
        assert "too short" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — incident log accumulation and pruning
# ---------------------------------------------------------------------------


class TestIncidentAccumulation:
    """Incident accumulation, pruning, and retrieval."""

    def test_incidents_pruned_at_max_incidents(self):
        checker = DeploymentHealthChecker()
        checker._max_incidents = 5
        for i in range(10):
            checker._add_incident(f"dep-{i % 2}", "error", f"detail {i}")
        incidents = checker.get_incidents(limit=50)
        assert len(incidents) == 5
        assert incidents[0].detail == "detail 5"
        assert incidents[-1].detail == "detail 9"

    def test_incidents_default_max_is_1000(self):
        checker = DeploymentHealthChecker()
        assert checker._max_incidents == 1000

    def test_force_remediate_creates_remediated_incident(self):
        checker = DeploymentHealthChecker(failure_threshold=1)
        checker.record_failure("dep-1", "error")
        assert checker.force_remediate("dep-1") is True
        incidents = checker.get_incidents(limit=10)
        remediated = [i for i in incidents if i.kind == "remediated"]
        assert len(remediated) == 1
        assert "Forced remediation" in remediated[0].detail

    def test_get_incidents_default_limit_100(self):
        checker = DeploymentHealthChecker()
        for i in range(5):
            checker.record_failure("dep-1", f"e{i}")
        result = checker.get_incidents()
        assert len(result) == 5

    def test_get_incidents_limit_clamps(self):
        checker = DeploymentHealthChecker()
        for i in range(10):
            checker._add_incident("dep-1", "error", f"d{i}")
        assert len(checker.get_incidents(limit=3)) == 3

    def test_get_incidents_limit_larger_than_stored(self):
        checker = DeploymentHealthChecker()
        for i in range(5):
            checker._add_incident("dep-1", "error", f"d{i}")
        assert len(checker.get_incidents(limit=100)) == 5


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — status and recovery
# ---------------------------------------------------------------------------


class TestStatusAndRecovery:
    """Status reporting, auto-recovery, and recovery via record_success."""

    def test_auto_recovery_resets_consecutive_failures(self):
        checker = DeploymentHealthChecker(failure_threshold=1, recovery_interval=0.01)
        checker.record_failure("dep-1", "e1")
        assert checker.get_status("dep-1").consecutive_failures == 1
        time.sleep(0.02)
        checker.is_healthy("dep-1")
        status = checker.get_status("dep-1")
        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert status.last_error is None

    def test_record_success_decrements_and_recovers(self):
        checker = DeploymentHealthChecker(failure_threshold=2)
        checker.record_failure("dep-1", "e1")
        checker.record_failure("dep-1", "e2")
        assert checker.is_healthy("dep-1") is False
        checker.record_success("dep-1")
        assert checker.get_status("dep-1").consecutive_failures == 1
        checker.record_success("dep-1")
        assert checker.get_status("dep-1").consecutive_failures == 0
        assert checker.is_healthy("dep-1") is True

    def test_all_statuses_returns_snapshot_not_live_reference(self):
        checker = DeploymentHealthChecker()
        checker.record_success("dep-1")
        snapshot = checker.all_statuses()
        snapshot["dep-new"] = DeploymentStatus(deployment_id="dep-new")
        assert "dep-new" not in checker.all_statuses()

    def test_get_status_auto_creates_unknown(self):
        checker = DeploymentHealthChecker()
        status = checker.get_status("ghost")
        assert status.deployment_id == "ghost"
        assert status.healthy is True
        assert status.consecutive_failures == 0

    def test_record_failure_truncates_error_at_500_chars(self):
        checker = DeploymentHealthChecker()
        long_err = "A" * 600
        checker.record_failure("dep-1", long_err)
        last_error = checker.get_status("dep-1").last_error
        assert last_error is not None
        assert len(last_error) == 500

    def test_record_failure_with_exception_object(self):
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-1", ValueError("bad value"))
        last_error = checker.get_status("dep-1").last_error
        assert last_error is not None
        assert "bad value" in last_error

    def test_record_failure_with_string_error(self):
        checker = DeploymentHealthChecker()
        checker.record_failure("dep-1", "connection refused")
        assert checker.get_status("dep-1").last_error == "connection refused"

    def test_record_success_does_not_decrement_below_zero(self):
        checker = DeploymentHealthChecker(failure_threshold=5)
        checker.record_success("dep-1")
        assert checker.get_status("dep-1").consecutive_failures == 0


# ---------------------------------------------------------------------------
# DeploymentHealthChecker — multi-checker composition
# ---------------------------------------------------------------------------


class TestMultiCheckerComposition:
    """AND/OR composition of health checks: circuit breaker + latency + content."""

    def test_unhealthy_circuit_and_latency_ok_still_unhealthy(self):
        checker = DeploymentHealthChecker(failure_threshold=2, max_avg_latency_s=10.0)
        checker.record_failure("dep-1", "e1")
        checker.record_latency("dep-1", 0.1)
        assert checker.is_healthy("dep-1") is True
        checker.record_failure("dep-1", "e2")
        assert checker.is_healthy("dep-1") is False

    def test_healthy_circuit_and_high_latency_is_unhealthy(self):
        checker = DeploymentHealthChecker(failure_threshold=5, max_avg_latency_s=0.1)
        checker.record_success("dep-1")
        checker.record_latency("dep-1", 0.5)
        assert checker.is_healthy("dep-1") is False

    def test_healthy_circuit_and_low_latency_is_healthy(self):
        checker = DeploymentHealthChecker(failure_threshold=5, max_avg_latency_s=1.0)
        checker.record_success("dep-1")
        checker.record_latency("dep-1", 0.1)
        assert checker.is_healthy("dep-1") is True

    def test_content_check_does_not_affect_circuit_breaker(self):
        """check_content failures do not increment consecutive_failures."""
        checker = DeploymentHealthChecker()
        ok, _ = checker.check_content("dep-1", "")
        assert ok is False
        assert checker.get_status("dep-1").consecutive_failures == 0


# ---------------------------------------------------------------------------
# DeploymentIncident — to_audit_details serialization
# ---------------------------------------------------------------------------


class TestDeploymentIncidentDeep:
    """Deep serialization and field presence tests for DeploymentIncident."""

    def test_to_audit_details_with_all_optionals(self):
        inc = DeploymentIncident(
            timestamp=1234567890.0,
            deployment_id="dep-1",
            model_id="gpt-4",
            error_type="timeout",
            error_message="Connection timed out",
            routed_to="dep-2",
            remediation_attempted=["restart", "scale"],
            remediation_result="recovered",
        )
        details = inc.to_audit_details()
        assert details["routed_to"] == "dep-2"
        assert details["remediation_attempted"] == ["restart", "scale"]
        assert details["remediation_result"] == "recovered"

    def test_to_audit_details_without_optionals(self):
        inc = DeploymentIncident(
            timestamp=1.0,
            deployment_id="dep-1",
            model_id="gpt-4",
            error_type="crash",
            error_message="segfault",
        )
        details = inc.to_audit_details()
        assert "routed_to" not in details
        assert "remediation_attempted" not in details
        assert "remediation_result" not in details
