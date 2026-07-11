"""Thread-safety tests for ModelFailoverChain.record_failover.

These tests verify that concurrent calls from multiple threads do not
corrupt the failover event list (lost events, torn writes, or races).
"""

from __future__ import annotations

import threading

from general_ludd.models.failover import ModelFailoverChain


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
            assert sorted(event.keys()) == ["error", "from", "to"], (
                f"unexpected keys in event: {event}"
            )
            assert isinstance(event["from"], str)
            assert isinstance(event["to"], str)
            assert isinstance(event["error"], str)
