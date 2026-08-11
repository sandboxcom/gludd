"""Deep unit tests for ModelFailoverChain — gaps not covered by adversarial/concurrency.

Covers: should_retry response-attribute chain, max_retries/backoff storage,
semaphore defaults, large-input behaviour, instance isolation, and timestamp ordering.
"""

from __future__ import annotations

import threading
import time

import pytest

from general_ludd.models.failover import (
    _DEFAULT_MAX_CONCURRENT_FAILOVERS,
    _DEFAULT_SEMAPHORE_TIMEOUT,
    ModelFailoverChain,
)


# --------------------------------------------------------------------------- #
# should_retry — response-attribute chain
# --------------------------------------------------------------------------- #
class _ErrWithResponse(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.response = _FakeResponse(status_code)


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _ErrNestedStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.status_code = status_code
        self.response = _FakeResponse(status_code)


class TestShouldRetryResponseChain:
    def test_response_status_code_used_when_no_direct_status_code(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(_ErrWithResponse(503)) is True
        assert chain.should_retry(_ErrWithResponse(404)) is False

    def test_response_status_code_fallback_for_all_retryable(self):
        chain = ModelFailoverChain("p")
        for code in (429, 500, 502, 503, 504):
            assert chain.should_retry(_ErrWithResponse(code)) is True, f"code={code}"

    def test_direct_status_code_priority_over_response(self):
        err = _ErrNestedStatus(404)
        err.status_code = 503
        chain = ModelFailoverChain("p")
        assert chain.should_retry(err) is True

    def test_error_with_no_status_and_no_response(self):
        err = Exception("generic failure")
        chain = ModelFailoverChain("p")
        assert chain.should_retry(err) is False

    def test_error_with_response_but_no_status_code(self):
        class _RespNoCode:
            pass

        err = Exception("fail")
        err.response = _RespNoCode()  # type: ignore[attr-defined]
        chain = ModelFailoverChain("p")
        assert chain.should_retry(err) is False


# --------------------------------------------------------------------------- #
# should_retry — keyword edge cases
# --------------------------------------------------------------------------- #
class TestShouldRetryKeywordEdges:
    def test_empty_error_string(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("")) is False

    def test_unicode_keyword_in_message(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("request \N{WARNING SIGN} timeout")) is True

    def test_keyword_buried_in_long_message(self):
        chain = ModelFailoverChain("p")
        long_msg = "x" * 1000 + " timeout " + "y" * 1000
        assert chain.should_retry(Exception(long_msg)) is True

    def test_exact_keyword_boundaries(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("timeout")) is True
        assert chain.should_retry(Exception("timeout123")) is True
        assert chain.should_retry(Exception("123timeout")) is True

    def test_rate_limit_without_space(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("ratelimit")) is False

    def test_capacity_keyword(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("over capacity error")) is True

    def test_unavailable_keyword(self):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception("service unavailable")) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "timeout occurred",
            "read timeout",
            "TIMEOUT",
            "rate limit",
            "RATE LIMIT exceeded",
            "service unavailable",
            "at capacity",
            "capacity reached",
        ],
    )
    def test_keyword_variants(self, msg: str):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception(msg)) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "forbidden",
            "unauthorized",
            "payment required",
            "not found",
            "conflict",
            "unprocessable",
            "too many requests",
            "internal error",
        ],
    )
    def test_non_retryable_keyword_variants(self, msg: str):
        chain = ModelFailoverChain("p")
        assert chain.should_retry(Exception(msg)) is False


# --------------------------------------------------------------------------- #
# Constructor parameter storage
# --------------------------------------------------------------------------- #
class TestConstructorParameters:
    def test_max_retries_stored(self):
        chain = ModelFailoverChain("p", max_retries=7)
        assert chain._max_retries == 7

    def test_backoff_seconds_stored(self):
        chain = ModelFailoverChain("p", backoff_seconds=5.5)
        assert chain._backoff == 5.5

    def test_default_max_retries(self):
        chain = ModelFailoverChain("p")
        assert chain._max_retries == 3

    def test_default_backoff_seconds(self):
        chain = ModelFailoverChain("p")
        assert chain._backoff == 2.0

    def test_max_retries_zero(self):
        chain = ModelFailoverChain("p", max_retries=0)
        assert chain._max_retries == 0

    def test_backoff_zero(self):
        chain = ModelFailoverChain("p", backoff_seconds=0.0)
        assert chain._backoff == 0.0

    def test_negative_max_retries_accepted(self):
        chain = ModelFailoverChain("p", max_retries=-1)
        assert chain._max_retries == -1

    def test_negative_backoff_accepted(self):
        chain = ModelFailoverChain("p", backoff_seconds=-1.0)
        assert chain._backoff == -1.0

    def test_max_concurrent_failovers_none_defaults(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=None)
        assert chain._semaphore._initial_value == _DEFAULT_MAX_CONCURRENT_FAILOVERS  # type: ignore[attr-defined]

    def test_semaphore_timeout_is_default(self):
        chain = ModelFailoverChain("p")
        assert chain._semaphore_timeout == _DEFAULT_SEMAPHORE_TIMEOUT


# --------------------------------------------------------------------------- #
# Semaphore behaviour — edge cases
# --------------------------------------------------------------------------- #
class TestSemaphoreEdges:
    def test_record_failover_releases_semaphore_after_success(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=1)
        result = chain.record_failover("p", "f1", "err")
        assert result is True
        result2 = chain.record_failover("p", "f2", "err2")
        assert result2 is True

    def test_semaphore_initial_value_zero(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=0)
        assert chain._semaphore._initial_value == 0  # type: ignore[attr-defined]
        result = chain.record_failover("p", "f1", "err")
        assert result is False

    def test_semaphore_large_max(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=10_000)
        for i in range(200):
            result = chain.record_failover("p", f"f{i}", f"err{i}")
            assert result is True
        assert len(chain.get_failover_events()) == 200


# --------------------------------------------------------------------------- #
# Event record shape — additional assertions
# --------------------------------------------------------------------------- #
class TestEventRecordShape:
    def test_exception_type_none_uses_unknown(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "f1", "err", exception_type=None)
        assert chain.get_failover_events()[0]["exception_type"] == "unknown"

    def test_exception_type_empty_string(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "f1", "err", exception_type="")
        assert chain.get_failover_events()[0]["exception_type"] == "unknown"

    def test_error_message_very_long(self):
        chain = ModelFailoverChain("p")
        long_err = "x" * 10_000
        chain.record_failover("p", "f1", long_err)
        assert len(chain.get_failover_events()[0]["error"]) == 10_000

    def test_from_profile_empty_string(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("", "f1", "err")
        assert chain.get_failover_events()[0]["from"] == ""

    def test_to_profile_empty_string(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "", "err")
        assert chain.get_failover_events()[0]["to"] == ""

    def test_unicode_profile_names(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("\N{WARNING SIGN}", "\N{SKULL}", "err")
        e = chain.get_failover_events()[0]
        assert e["from"] == "\N{WARNING SIGN}"
        assert e["to"] == "\N{SKULL}"


# --------------------------------------------------------------------------- #
# Attempt counter behaviour
# --------------------------------------------------------------------------- #
class TestAttemptCounter:
    def test_counter_shared_across_all_recordings(self):
        chain = ModelFailoverChain("p", ["f1", "f2", "f3", "f4"])
        for i in range(10):
            chain.record_failover("p", "f1", f"err{i}")
        attempts = [e["attempt"] for e in chain.get_failover_events()]
        assert attempts == list(range(1, 11))

    def test_counter_unique_per_instance(self):
        a = ModelFailoverChain("pA")
        b = ModelFailoverChain("pB")
        a.record_failover("pA", "f1", "errA")
        b.record_failover("pB", "f1", "errB")
        assert a.get_failover_events()[0]["attempt"] == 1
        assert b.get_failover_events()[0]["attempt"] == 1

    def test_counter_beyond_max_retries_is_mechanical(self):
        chain = ModelFailoverChain("p", max_retries=3)
        for i in range(100):
            chain.record_failover("p", "f1", f"err{i}")
        assert chain.get_failover_events()[-1]["attempt"] == 100


# --------------------------------------------------------------------------- #
# get_chain — edge cases
# --------------------------------------------------------------------------- #
class TestGetChainEdges:
    def test_chain_with_many_fallbacks(self):
        fb = [f"f{i}" for i in range(1000)]
        chain = ModelFailoverChain("p", fb)
        assert len(chain.get_chain()) == 1001

    def test_get_chain_does_not_mutate_internal_state(self):
        chain = ModelFailoverChain("p", ["f1", "f2"])
        c = chain.get_chain()
        c.clear()
        assert chain.get_chain() == ["p", "f1", "f2"]

    def test_fallback_list_copy_preserves_single_element(self):
        fb = ["only_fallback"]
        chain = ModelFailoverChain("p", fb)
        fb.pop()
        assert chain.get_chain() == ["p", "only_fallback"]


# --------------------------------------------------------------------------- #
# Timestamp ordering
# --------------------------------------------------------------------------- #
class TestTimestampOrdering:
    def test_timestamps_are_monotonic(self):
        chain = ModelFailoverChain("p", ["f1", "f2", "f3"])
        for i in range(5):
            chain.record_failover("p", "f1", f"err{i}")
        timestamps = [e["timestamp"] for e in chain.get_failover_events()]
        assert timestamps == sorted(timestamps), "timestamps must be monotonic"

    def test_timestamps_increase_with_forced_delay(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "f1", "e1")
        time.sleep(0.01)
        chain.record_failover("f1", "f2", "e2")
        t1 = chain.get_failover_events()[0]["timestamp"]
        t2 = chain.get_failover_events()[1]["timestamp"]
        assert t2 > t1


# --------------------------------------------------------------------------- #
# Thread safety — read/write isolation
# --------------------------------------------------------------------------- #
class TestReadWriteIsolation:
    def test_concurrent_read_while_writing_never_returns_partial(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=100)
        num_writers = 20
        writes_per = 100

        partial_reads: list[int] = []
        stop = threading.Event()

        def writer(tid: int) -> None:
            for i in range(writes_per):
                chain.record_failover(f"p-{tid}", f"f-{tid}-{i}", f"e-{tid}-{i}")

        def reader() -> None:
            while not stop.is_set():
                events = chain.get_failover_events()
                # Each event must have exactly 6 keys (no partial writes)
                for e in events:
                    if len(e) != 6:
                        partial_reads.append(len(e))
                        return

        writers = [threading.Thread(target=writer, args=(i,)) for i in range(num_writers)]
        reader_thread = threading.Thread(target=reader)

        for w in writers:
            w.start()
        reader_thread.start()
        for w in writers:
            w.join()
        stop.set()
        reader_thread.join()

        assert partial_reads == [], f"partial reads detected: {partial_reads}"


# --------------------------------------------------------------------------- #
# Instance isolation
# --------------------------------------------------------------------------- #
class TestInstanceIsolation:
    def test_instances_do_not_share_events(self):
        a = ModelFailoverChain("pA", ["fA"])
        b = ModelFailoverChain("pB", ["fB"])
        a.record_failover("pA", "fA", "errA")
        assert len(b.get_failover_events()) == 0

    def test_instances_do_not_share_locks(self):
        a = ModelFailoverChain("pA")
        b = ModelFailoverChain("pB")
        assert a._events_lock is not b._events_lock
        assert a._semaphore is not b._semaphore
