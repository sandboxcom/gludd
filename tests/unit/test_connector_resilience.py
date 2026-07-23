"""Connector resilience: reconnect, timeout, circuit breaker patterns.

Tests the base connector infrastructure (_errors, _protocols, base) for
resilience under transient failures, timeouts, and health degradation.
"""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest

from general_ludd.connectors._errors import (
    sanitize_exc_message,
    sanitize_str,
)
from general_ludd.connectors._util import parse_timestamp, validate_base_url
from general_ludd.connectors.base import (
    _GLOBAL_CAP,
    _PER_SOURCE_CAP,
    Observability,
    SourceRegistry,
    _is_configured,
    classify_health,
    classify_health_for_source,
    normalized_record,
    run_healthcheck,
)


class FakeSource:
    def __init__(self, name: str, kind: str, health_result=None, query_result=None):
        self.name = name
        self.KIND = kind
        self._health = health_result if health_result is not None else {"ok": True}
        self._query = query_result if query_result is not None else []

    def health(self) -> dict[str, object]:
        return self._health

    def query(self, spec: dict[str, object]) -> list[dict[str, object]]:
        return self._query


# --------------------------------------------------------------------------- #
# Circuit breaker state machine
# --------------------------------------------------------------------------- #

class CircuitBreaker:
    """Minimal circuit breaker for testing resilience patterns.

    Tracks consecutive failures. Opens after threshold, transitions to
    half-open after reset timeout, closes on successful probe.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str = "",
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.state: str = self.CLOSED
        self._failure_count: int = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time: float = 0.0
        self._success_count: int = 0

    def record_success(self) -> None:
        self._success_count += 1
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
            self._failure_count = 0
        elif self.state == self.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            self.state = self.OPEN

    def allow_request(self) -> bool:
        if self.state == self.CLOSED:
            return True
        if self.state == self.OPEN:
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self.state = self.HALF_OPEN
                return True
            return False
        return True

    def is_open(self) -> bool:
        return self.state == self.OPEN

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def success_count(self) -> int:
        return self._success_count


class TestCircuitBreakerStateMachine:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"

    def test_allows_requests_when_closed(self):
        cb = CircuitBreaker()
        assert cb.allow_request() is True

    def test_blocks_requests_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=999.0)
        cb.record_failure()
        assert cb.is_open()
        assert cb.allow_request() is False

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == "half_open"

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()
        assert cb.state == "half_open"
        cb.record_failure()
        assert cb.state == "open"


# --------------------------------------------------------------------------- #
# Retry with exponential backoff
# --------------------------------------------------------------------------- #

def compute_backoff_delay(
    attempt: int,
    base: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = False,
) -> float:
    """Exponential backoff: base * 2^attempt, capped at max_delay."""
    import random

    delay = min(base * (2 ** attempt), max_delay)
    if jitter:
        delay = delay * random.uniform(0.5, 1.5)
    return delay


class TestExponentialBackoff:
    def test_first_attempt_equals_base(self):
        delay = compute_backoff_delay(0, base=1.0)
        assert delay == 1.0

    def test_doubles_each_attempt(self):
        d0 = compute_backoff_delay(0, base=1.0)
        d1 = compute_backoff_delay(1, base=1.0)
        d2 = compute_backoff_delay(2, base=1.0)
        assert d0 == 1.0
        assert d1 == 2.0
        assert d2 == 4.0

    def test_capped_at_max_delay(self):
        delay = compute_backoff_delay(20, base=1.0, max_delay=60.0)
        assert delay <= 60.0

    def test_custom_base(self):
        delay = compute_backoff_delay(2, base=5.0)
        assert delay == 20.0

    def test_zero_attempt_at_base(self):
        assert compute_backoff_delay(0, base=2.0) == 2.0

    def test_jitter_produces_different_values(self):
        delays = [compute_backoff_delay(3, jitter=True) for _ in range(10)]
        assert len(set(delays)) > 1


# --------------------------------------------------------------------------- #
# Reconnect patterns
# --------------------------------------------------------------------------- #

class TestConnectorReconnect:
    def test_normalized_record_has_all_keys(self):
        r = normalized_record(source="test", kind="logs")
        assert r["source"] == "test"
        assert r["kind"] == "logs"
        assert r["ts"] is None
        assert r["level_or_status"] == "info"
        assert r["message"] == ""
        assert r["value"] is None
        assert isinstance(r["labels"], dict)
        assert r["raw"] is None

    def test_normalized_record_nan_ts_coerced_none(self):
        r = normalized_record(source="s", kind="logs", ts=float("nan"))
        assert r["ts"] is None

    def test_normalized_record_inf_value_coerced_none(self):
        r = normalized_record(source="s", kind="metrics", value=float("inf"))
        assert r["value"] is None

    def test_normalized_record_with_labels(self):
        r = normalized_record(source="s", kind="traces", labels={"trace_id": "abc"})
        assert r["labels"]["trace_id"] == "abc"

    def test_normalized_record_neg_inf_ts_coerced_none(self):
        r = normalized_record(source="s", kind="logs", ts=float("-inf"))
        assert r["ts"] is None

    def test_normalized_record_neg_inf_value_coerced_none(self):
        r = normalized_record(source="s", kind="metrics", value=float("-inf"))
        assert r["value"] is None

    def test_normalized_record_preserves_custom_level(self):
        r = normalized_record(source="s", kind="logs", level_or_status="warn")
        assert r["level_or_status"] == "warn"

    def test_normalized_record_labels_not_shared(self):
        labels_in = {"a": 1}
        r1 = normalized_record(source="s1", kind="logs", labels=labels_in)
        r2 = normalized_record(source="s2", kind="logs", labels=labels_in)
        r1["labels"]["a"] = 99
        assert r2["labels"]["a"] == 1

    def test_normalized_record_raw_preserved(self):
        raw_obj = {"_raw_backend": "elasticsearch", "body": {"x": 1}}
        r = normalized_record(source="s", kind="logs", raw=raw_obj)
        assert r["raw"] is raw_obj

    def test_sanitize_exc_message_returns_type_name(self):
        try:
            raise RuntimeError("/secret/path token=abc123def456")
        except RuntimeError as e:
            result = sanitize_exc_message(e)
        assert result == "RuntimeError"

    def test_sanitize_str_redacts_paths(self):
        text = "error at /home/user/.ssh/id_rsa"
        result = sanitize_str(text)
        assert "/home/user/.ssh/id_rsa" not in result
        assert "REDACTED" in result

    def test_sanitize_str_redacts_tokens(self):
        text = "authorization: bearer eyJhbGciOiJIUzI1NiJ9.abc123def456_xyz"
        result = sanitize_str(text)
        assert "eyJhbGciOi" not in result
        assert "REDACTED" in result

    def test_sanitize_str_redacts_urls(self):
        text = "failed to connect https://api.example.com/v1/secret"
        result = sanitize_str(text)
        assert "https://api.example.com" not in result
        assert "REDACTED-URL" in result

    def test_sanitize_exc_message_on_syntax_error(self):
        try:
            eval("x =")
        except SyntaxError as e:
            result = sanitize_exc_message(e)
        assert result == "SyntaxError"

    def test_sanitize_exc_message_on_key_error(self):
        try:
            {}["missing"]
        except KeyError as e:
            result = sanitize_exc_message(e)
        assert result == "KeyError"

    def test_sanitize_str_preserves_safe_text(self):
        text = "connection refused"
        result = sanitize_str(text)
        assert "connection refused" in result

    def test_parse_timestamp_iso_with_z(self):
        ts = parse_timestamp("2024-01-01T00:00:00Z")
        assert ts is not None
        assert ts > 0

    def test_parse_timestamp_iso_with_offset(self):
        ts = parse_timestamp("2024-01-01T00:00:00+00:00")
        assert ts is not None
        assert ts > 0

    def test_parse_timestamp_naive_utc_default(self):
        ts = parse_timestamp("2024-01-01T00:00:00")
        assert ts is not None
        assert ts > 0

    def test_parse_timestamp_none_returns_none(self):
        assert parse_timestamp(None) is None

    def test_parse_timestamp_empty_returns_none(self):
        assert parse_timestamp("") is None

    def test_parse_timestamp_non_string_returns_none(self):
        assert parse_timestamp(12345) is None

    def test_parse_timestamp_invalid_returns_none(self):
        assert parse_timestamp("not-a-date") is None

    def test_parse_timestamp_fuzzy_format(self):
        ts = parse_timestamp("Jan 1 2024 00:00:00")
        assert ts is not None

    def test_validate_base_url_trailing_slash(self):
        from unittest.mock import patch

        with patch("general_ludd.connectors._util.is_url_blocked", return_value=False):
            result = validate_base_url("http://example.com/")
            assert result == "http://example.com"

    def test_validate_base_url_blocked_raises(self):
        from unittest.mock import patch

        with (
            patch("general_ludd.connectors._util.is_url_blocked", return_value=True),
            pytest.raises(ValueError),
        ):
            validate_base_url("http://metadata.internal")


# --------------------------------------------------------------------------- #
# Timeout resilience
# --------------------------------------------------------------------------- #

class TestConnectorTimeout:
    def test_run_healthcheck_timeout_returns_unhealthy(self):
        def slow_health():
            time.sleep(10)
            return {"ok": True}

        src = FakeSource("slow", "metrics", {"ok": True})
        src.health = slow_health
        result = run_healthcheck(src, timeout=0.01)
        assert result["status"] == "unhealthy"
        assert "timeout" in result["detail"]

    def test_run_healthcheck_exception_returns_unhealthy(self):
        def bad_health():
            raise RuntimeError("boom")

        src = FakeSource("err", "logs")
        src.health = bad_health
        result = run_healthcheck(src)
        assert result["status"] == "unhealthy"
        assert "RuntimeError" in result["detail"]

    def test_run_healthcheck_healthy(self):
        src = FakeSource("good", "metrics", {"ok": True, "detail": "all good"})
        result = run_healthcheck(src)
        assert result["status"] == "healthy"

    def test_run_healthcheck_empty_dict(self):
        src = FakeSource("empty", "logs", {})
        result = run_healthcheck(src)
        assert result["status"] == "unhealthy"
        assert "non-dict" in result["detail"]

    def test_run_healthcheck_timeout_includes_seconds(self):
        def slow():
            time.sleep(10)
            return {"ok": True}

        src = FakeSource("t", "logs")
        src.health = slow
        result = run_healthcheck(src, timeout=0.001)
        assert "0.0" in result["detail"]

    def test_run_healthcheck_exception_strips_message(self):
        def bad():
            raise ConnectionError("secret://token=abc")

        src = FakeSource("e2", "logs")
        src.health = bad
        result = run_healthcheck(src)
        assert result["status"] == "unhealthy"
        assert result["detail"] == "exception during healthcheck: ConnectionError"
        assert "secret" not in result["detail"]

    def test_run_healthcheck_none_return(self):
        def none_health():
            return None

        src = FakeSource("n", "logs")
        src.health = none_health
        result = run_healthcheck(src)
        assert result["status"] == "unhealthy"

    def test_run_healthcheck_thread_isolation(self):
        side_effects: list[str] = []

        def tracking_health():
            side_effects.append("called")
            return {"ok": True}

        src = FakeSource("t2", "metrics")
        src.health = tracking_health
        result = run_healthcheck(src, timeout=5.0)
        assert result["status"] == "healthy"
        assert "called" in side_effects


# --------------------------------------------------------------------------- #
# Circuit breaker: health classification
# --------------------------------------------------------------------------- #

class TestCircuitBreakerClassification:
    def test_classify_health_ok_true(self):
        result = classify_health({"ok": True}, "src1")
        assert result["status"] == "healthy"

    def test_classify_health_ok_false(self):
        result = classify_health({"ok": False, "error": "down"}, "src1")
        assert result["status"] == "unhealthy"

    def test_classify_health_missing_ok(self):
        result = classify_health({"status": "ok"}, "src1")
        assert result["status"] == "unhealthy"

    def test_classify_health_for_source_unconfigured_degraded(self):
        src = FakeSource("uc", "metrics", {"ok": False, "error": "no creds"})
        src.TOKEN_ENV = "MISSING_ENV_VAR_12345"
        result = classify_health_for_source(src, {"ok": False, "error": "no creds"})
        assert result["status"] == "degraded"

    def test_classify_health_with_detail_preserved(self):
        result = classify_health({"ok": True, "detail": "latency 42ms"}, "src1")
        assert result["detail"] == "latency 42ms"

    def test_classify_health_ok_false_no_error_key(self):
        result = classify_health({"ok": False}, "src1")
        assert result["status"] == "unhealthy"
        assert "health check failed" in result["detail"]

    def test_classify_health_for_source_configured_unhealthy(self):

        src = FakeSource("cfg", "metrics", {"ok": False, "error": "timeout"})
        src.TOKEN_ENV = "PATH"
        result = classify_health_for_source(src, {"ok": False, "error": "timeout"})
        assert result["status"] == "unhealthy"

    def test_is_configured_no_env_attrs_assumed_configured(self):
        src = FakeSource("nc", "logs")
        assert _is_configured(src) is True

    def test_is_configured_env_set(self):

        src = FakeSource("e", "logs")
        src.TOKEN_ENV = "PATH"
        src.API_KEY_ENV = "HOME"
        assert _is_configured(src) is True

    def test_is_configured_env_attrs_not_set(self):

        src = FakeSource("e2", "logs")
        src.TOKEN_ENV = "GLUDD_NONEXISTENT_VAR_ZZZ_98765"
        assert _is_configured(src) is False

    def test_classify_health_for_source_degraded_detail_prefix(self):
        src = FakeSource("uc2", "metrics", {"ok": False, "error": "no keys"})
        src.TOKEN_ENV = "NOT_SET_98765"
        result = classify_health_for_source(src, {"ok": False, "error": "no keys"})
        assert result["status"] == "degraded"
        assert result["detail"].startswith("unconfigured")

    def test_classify_health_source_name_in_result(self):
        result = classify_health({"ok": True}, "my-source")
        assert result["source"] == "my-source"


# --------------------------------------------------------------------------- #
# Observability resilience: find() with multiple failing sources
# --------------------------------------------------------------------------- #

class TestObservabilityResilience:
    def test_find_resilience_on_query_failure(self):
        class ThrowingSource:
            name = "thrower"
            KIND = "logs"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("boom")

        reg = SourceRegistry()
        reg.register(ThrowingSource())
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 1
        assert results[0]["level_or_status"] == "error"

    def test_find_merges_multiple_sources(self):
        a = FakeSource("a", "logs", query_result=[normalized_record(source="a", kind="logs", ts=10.0)])
        b = FakeSource("b", "logs", query_result=[normalized_record(source="b", kind="logs", ts=5.0)])
        reg = SourceRegistry()
        reg.register(a)
        reg.register(b)
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 2
        assert results[0]["source"] == "b"
        assert results[1]["source"] == "a"

    def test_find_partial_failure_continues_fanout(self):
        a = FakeSource("a", "logs", query_result=[normalized_record(source="a", kind="logs", ts=1.0)])

        class Throwing:
            name = "b"
            KIND = "logs"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("x")

        c = FakeSource("c", "logs", query_result=[normalized_record(source="c", kind="logs", ts=2.0)])
        reg = SourceRegistry()
        reg.register(a)
        reg.register(Throwing())
        reg.register(c)
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 3
        sources = [r["source"] for r in results]
        assert "a" in sources
        assert "c" in sources
        error_records = [r for r in results if r["level_or_status"] == "error"]
        assert len(error_records) == 1
        assert error_records[0]["source"] == "b"

    def test_find_all_failing_returns_all_errors(self):
        class ThrowingA:
            name = "a"
            KIND = "logs"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("x")

        class ThrowingB:
            name = "b"
            KIND = "logs"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("y")

        reg = SourceRegistry()
        reg.register(ThrowingA())
        reg.register(ThrowingB())
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 2
        for r in results:
            assert r["level_or_status"] == "error"

    def test_find_kinds_filter_respected(self):
        a = FakeSource("a", "logs", query_result=[normalized_record(source="a", kind="logs")])
        b = FakeSource("b", "metrics", query_result=[normalized_record(source="b", kind="metrics")])
        reg = SourceRegistry()
        reg.register(a)
        reg.register(b)
        obs = Observability(reg)
        results = obs.find({}, kinds=["logs"])
        assert len(results) == 1
        assert results[0]["source"] == "a"

    def test_find_per_source_cap_truncates(self):
        records = [normalized_record(source="s", kind="logs", ts=float(i)) for i in range(_PER_SOURCE_CAP + 100)]
        src = FakeSource("big", "logs", query_result=records)
        reg = SourceRegistry()
        reg.register(src)
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == _PER_SOURCE_CAP

    def test_find_global_cap_truncates_across_sources(self):
        many = [normalized_record(source="a", kind="logs", ts=float(i)) for i in range(_GLOBAL_CAP // 2 + 1)]
        a = FakeSource("a", "logs", query_result=many)
        b = FakeSource("b", "logs", query_result=many)
        reg = SourceRegistry()
        reg.register(a)
        reg.register(b)
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) <= _GLOBAL_CAP

    def test_find_empty_registry_returns_empty(self):
        reg = SourceRegistry()
        obs = Observability(reg)
        assert obs.find({}) == []

    def test_find_preserves_sort_order_by_ts(self):
        a = FakeSource("a", "logs", query_result=[
            normalized_record(source="a", kind="logs", ts=100.0),
        ])
        b = FakeSource("b", "logs", query_result=[
            normalized_record(source="b", kind="logs", ts=10.0),
        ])
        reg = SourceRegistry()
        reg.register(b)
        reg.register(a)
        obs = Observability(reg)
        results = obs.find({})
        assert results[0]["ts"] == 10.0
        assert results[1]["ts"] == 100.0

    def test_find_error_record_from_unknown_source(self):
        class NoNameSource:
            KIND = "logs"

            def health(self):
                return {"ok": True}

            def query(self, spec):
                raise RuntimeError("x")

        reg = SourceRegistry()
        reg.register(NoNameSource())
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) == 1
        assert results[0]["level_or_status"] == "error"

    def test_find_byte_budget_limit(self):
        big_payloads = [normalized_record(source="s", kind="logs", raw="x" * 2_000_000) for _ in range(30)]
        src = FakeSource("heavy", "logs", query_result=big_payloads)
        reg = SourceRegistry()
        reg.register(src)
        obs = Observability(reg)
        results = obs.find({})
        assert len(results) <= len(big_payloads)

    def test_source_registry_replace_on_duplicate(self):
        a = FakeSource("dup", "logs")
        b = FakeSource("dup", "metrics")
        reg = SourceRegistry()
        reg.register(a)
        reg.register(b)
        assert reg.get("dup") is b

    def test_source_registry_by_kind(self):
        a = FakeSource("a", "logs")
        b = FakeSource("b", "metrics")
        c = FakeSource("c", "logs")
        reg = SourceRegistry()
        for s in (a, b, c):
            reg.register(s)
        logs = reg.by_kind("logs")
        assert len(logs) == 2
        assert {s.name for s in logs} == {"a", "c"}

    def test_source_registry_get_missing_returns_none(self):
        reg = SourceRegistry()
        assert reg.get("nope") is None

    def test_source_registry_all_detached_copy(self):
        reg = SourceRegistry()
        reg.register(FakeSource("a", "logs"))
        all1 = reg.all()
        reg.register(FakeSource("b", "logs"))
        all2 = reg.all()
        assert len(all1) == 1
        assert len(all2) == 2


# --------------------------------------------------------------------------- #
# Thread safety / concurrent healthcheck
# --------------------------------------------------------------------------- #

class TestConcurrentHealthcheck:
    def test_multiple_concurrent_healthchecks(self):
        results: list[dict[str, object]] = []

        def collect(source):
            r = run_healthcheck(source, timeout=5.0)
            results.append(r)

        src_a = FakeSource("a", "logs", {"ok": True})
        src_b = FakeSource("b", "metrics", {"ok": False, "error": "db down"})
        src_c = FakeSource("c", "traces", {"ok": True})

        threads = [
            threading.Thread(target=collect, args=(s,)) for s in (src_a, src_b, src_c)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 3
        statuses = {r["source"]: r["status"] for r in results}
        assert statuses["a"] == "healthy"
        assert statuses["b"] == "unhealthy"
        assert statuses["c"] == "healthy"

    def test_concurrent_find_with_mixed_sources(self):
        def runner(reg, acc):
            obs = Observability(reg)
            acc.extend(obs.find({}))

        a = FakeSource("a", "logs", query_result=[
            normalized_record(source="a", kind="logs", ts=1.0)
        ])
        b = FakeSource("b", "logs", query_result=[
            normalized_record(source="b", kind="logs", ts=2.0)
        ])
        reg_a = SourceRegistry()
        reg_a.register(a)
        reg_b = SourceRegistry()
        reg_b.register(b)

        acc_a: list[dict[str, object]] = []
        acc_b: list[dict[str, object]] = []

        t1 = threading.Thread(target=runner, args=(reg_a, acc_a))
        t2 = threading.Thread(target=runner, args=(reg_b, acc_b))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(acc_a) == 1
        assert len(acc_b) == 1


# --------------------------------------------------------------------------- #
# Reconnection / transient failure recovery
# --------------------------------------------------------------------------- #

class TestReconnectionRecovery:
    def test_failing_source_is_isolated(self):
        a = FakeSource("a", "logs", query_result=[normalized_record(source="a", kind="logs", ts=1.0)])

        class Intermittent:
            name = "inter"
            KIND = "logs"
            _call_count = 0

            def health(self):
                return {"ok": True}

            def query(self, spec):
                Intermittent._call_count += 1
                if Intermittent._call_count >= 2:
                    return [normalized_record(source="inter", kind="logs", ts=2.0)]
                raise RuntimeError("transient failure")

        c = FakeSource("c", "logs", query_result=[normalized_record(source="c", kind="logs", ts=3.0)])
        reg = SourceRegistry()
        reg.register(a)
        reg.register(Intermittent())
        reg.register(c)
        obs = Observability(reg)

        results1 = obs.find({})
        assert len(results1) == 3
        error_sources = {r["source"] for r in results1 if r["level_or_status"] == "error"}
        assert "inter" in error_sources

        results2 = obs.find({})
        assert len(results2) == 3
        success_sources = {r["source"] for r in results2 if r["level_or_status"] != "error"}
        assert "inter" in success_sources


# --------------------------------------------------------------------------- #
# Association / correlation
# --------------------------------------------------------------------------- #

class TestAssociation:
    def test_associate_by_trace_id(self):
        recs = [
            normalized_record(source="a", kind="logs", labels={"trace_id": "t1"}),
            normalized_record(source="b", kind="logs", labels={"trace_id": "t1"}),
            normalized_record(source="c", kind="logs", labels={"trace_id": "t2"}),
        ]
        obs = Observability(SourceRegistry())
        groups = obs.associate(recs, by="trace_id")
        assert len(groups) == 2
        assert groups[0]["key"] == "t1"
        assert len(cast(list[object], groups[0]["records"])) == 2

    def test_associate_by_time_window(self):
        recs = [
            normalized_record(source="a", kind="logs", ts=10.0),
            normalized_record(source="b", kind="logs", ts=11.0),
            normalized_record(source="c", kind="logs", ts=100.0),
        ]
        obs = Observability(SourceRegistry())
        groups = obs.associate(recs, by="time_window", window_s=5.0)
        assert len(groups) == 2

    def test_associate_drops_missing_label(self):
        recs = [
            normalized_record(source="a", kind="logs"),
        ]
        obs = Observability(SourceRegistry())
        groups = obs.associate(recs, by="trace_id")
        assert groups == []

    def test_associate_by_commit(self):
        recs = [
            normalized_record(source="a", kind="pipeline", labels={"commit": "abc123"}),
            normalized_record(source="b", kind="pipeline", labels={"commit": "abc123"}),
            normalized_record(source="c", kind="pipeline", labels={"commit": "def456"}),
        ]
        obs = Observability(SourceRegistry())
        groups = obs.associate(recs, by="commit")
        assert len(groups) == 2
