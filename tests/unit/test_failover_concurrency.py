"""Thread-safety tests for ModelFailoverChain.record_failover.

These tests verify that concurrent calls from multiple threads do not
corrupt the failover event list (lost events, torn writes, or races),
that the bounded semaphore is correctly provisioned, and that the
events lock guards both writes and reads.
"""

from __future__ import annotations

import threading
import time

from general_ludd.models.failover import (
    _DEFAULT_MAX_CONCURRENT_FAILOVERS,
    _DEFAULT_SEMAPHORE_TIMEOUT,
    ModelFailoverChain,
)


class TestRecordFailoverConcurrency:
    def test_concurrent_record_failover_no_lost_events(self):
        chain = ModelFailoverChain("p", ["f1"])
        num_threads = 20
        calls_per_thread = 50
        expected = num_threads * calls_per_thread

        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(calls_per_thread):
                    chain.record_failover(
                        f"p-{tid}", f"f-{tid}-{i}", f"err-{tid}-{i}"
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"threads raised exceptions: {errors}"
        assert len(chain.get_failover_events()) == expected, (
            f"expected {expected} events, got {len(chain.get_failover_events())}"
        )

    def test_concurrent_record_and_read_consistent(self):
        chain = ModelFailoverChain("p", ["f1", "f2"])
        num_writers = 10
        writes_per = 200

        errors: list[Exception] = []
        stop_flag = threading.Event()

        def writer(tid: int) -> None:
            try:
                for i in range(writes_per):
                    chain.record_failover(
                        f"p-{tid}", f"f-{tid}-{i}", f"err-{tid}-{i}"
                    )
            except Exception as exc:
                errors.append(exc)

        read_counts: list[int] = []

        def reader() -> None:
            count = 0
            while not stop_flag.is_set():
                events = chain.get_failover_events()
                count += len(events)
            read_counts.append(count)

        writers = [
            threading.Thread(target=writer, args=(i,)) for i in range(num_writers)
        ]
        reader_thread = threading.Thread(target=reader)

        for w in writers:
            w.start()
        reader_thread.start()

        for w in writers:
            w.join()
        stop_flag.set()
        reader_thread.join()

        assert errors == [], f"threads raised exceptions: {errors}"
        expected = num_writers * writes_per
        assert len(chain.get_failover_events()) == expected, (
            f"expected {expected} events, got {len(chain.get_failover_events())}"
        )

    def test_events_lock_exists(self):
        chain = ModelFailoverChain("p")
        assert hasattr(chain, "_events_lock"), (
            "ModelFailoverChain must have _events_lock attribute"
        )
        assert isinstance(chain._events_lock, type(threading.Lock())), (
            "_events_lock must be a threading.Lock instance"
        )

    def test_events_no_duplicate_or_corrupt_keys(self):
        chain = ModelFailoverChain("p", ["f1"])
        num_threads = 10
        calls_per_thread = 30

        def worker(tid: int) -> None:
            for i in range(calls_per_thread):
                chain.record_failover(
                    f"src-{tid}", f"dst-{tid}-{i}", f"err-{tid}-{i}"
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = chain.get_failover_events()
        for event in events:
            assert isinstance(event, dict), f"corrupt event: {event}"
            required_keys = {"from", "to", "error", "attempt", "exception_type", "timestamp"}
            missing = required_keys - set(event.keys())
            assert not missing, f"event missing keys {missing}: {event}"
            assert isinstance(event["from"], str)
            assert isinstance(event["to"], str)
            assert isinstance(event["error"], str)
            assert isinstance(event["attempt"], int)
            assert isinstance(event["exception_type"], str)
            assert isinstance(event["timestamp"], float)


class TestSemaphoreBounding:
    def test_semaphore_attr_exists_and_is_bounded(self):
        chain = ModelFailoverChain("p")
        assert hasattr(chain, "_semaphore"), "must expose _semaphore"
        assert isinstance(chain._semaphore, threading.BoundedSemaphore)

    def test_default_max_concurrent_is_reasonable(self):
        chain = ModelFailoverChain("p")
        assert chain._semaphore._initial_value == _DEFAULT_MAX_CONCURRENT_FAILOVERS  # type: ignore[attr-defined]
        assert chain._semaphore_timeout == _DEFAULT_SEMAPHORE_TIMEOUT

    def test_custom_max_concurrent(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=3)
        assert chain._semaphore._initial_value == 3  # type: ignore[attr-defined]

    def test_record_failover_returns_false_when_semaphore_saturated(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=1)
        acquired = chain._semaphore.acquire(blocking=False)
        assert acquired

        try:
            result = chain.record_failover("p", "f1", "err")
            assert result is False, "should drop event when semaphore is saturated"
        finally:
            chain._semaphore.release()

    def test_record_failover_returns_true_on_success(self):
        chain = ModelFailoverChain("p")
        result = chain.record_failover("p", "f1", "timeout")
        assert result is True
        assert len(chain.get_failover_events()) == 1

    def test_semaphore_released_after_exception(self):
        chain = ModelFailoverChain("p", max_concurrent_failovers=1)
        acquired = chain._semaphore.acquire(blocking=False)
        assert acquired

        try:
            result = chain.record_failover("p", "f1", "err")
            assert result is False
        finally:
            chain._semaphore.release()

        assert chain.record_failover("p", "f1", "err") is True


class TestExceptionContext:
    def test_custom_exception_type_stored(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "f1", "timeout", exception_type="httpx.TimeoutException")
        e = chain.get_failover_events()[0]
        assert e["exception_type"] == "httpx.TimeoutException"

    def test_exception_type_defaults_to_unknown(self):
        chain = ModelFailoverChain("p")
        chain.record_failover("p", "f1", "timeout")
        assert chain.get_failover_events()[0]["exception_type"] == "unknown"

    def test_attempt_counter_increments_monotonically(self):
        chain = ModelFailoverChain("p", ["f1", "f2", "f3"])
        chain.record_failover("p", "f1", "e1")
        chain.record_failover("f1", "f2", "e2")
        chain.record_failover("f2", "f3", "e3")
        attempts = [e["attempt"] for e in chain.get_failover_events()]
        assert attempts == [1, 2, 3], f"attempts must be monotonic, got {attempts}"

    def test_timestamp_is_recent(self):
        chain = ModelFailoverChain("p")
        before = time.time()
        chain.record_failover("p", "f1", "err")
        after = time.time()
        ts = chain.get_failover_events()[0]["timestamp"]
        assert before - 1 <= ts <= after + 1, f"timestamp {ts} not in [{before}, {after}]"


class TestEventsLockReadGuards:
    def test_get_failover_events_holds_lock_during_copy(self):
        chain = ModelFailoverChain("p")
        num_threads = 10
        calls_each = 50
        expected = num_threads * calls_each

        errors: list[Exception] = []

        def writer(tid: int) -> None:
            try:
                for i in range(calls_each):
                    chain.record_failover(f"p-{tid}", f"f-{tid}-{i}", f"e-{tid}-{i}")
            except Exception as exc:
                errors.append(exc)

        write_threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_threads)]
        for t in write_threads:
            t.start()

        for t in write_threads:
            t.join()

        assert errors == [], f"writers raised: {errors}"
        events = chain.get_failover_events()
        assert len(events) == expected, f"expected {expected}, got {len(events)}"
