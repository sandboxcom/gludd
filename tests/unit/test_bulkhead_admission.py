"""Deterministic worker/backlog admission regressions for thread bulkheads."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from general_ludd.resilience.bulkhead import BulkheadRejectedError, ThreadPoolBulkhead


@contextmanager
def _hold_workers(
    monkeypatch: pytest.MonkeyPatch, max_workers: int, max_queue_size: int
) -> Iterator[tuple[ThreadPoolBulkhead, threading.Event]]:
    """Hold actual executor workers before they can dequeue any work."""
    release_workers = threading.Event()

    def executor(max_workers: int, thread_name_prefix: str = "") -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
            initializer=release_workers.wait,
        )

    monkeypatch.setattr("general_ludd.resilience.bulkhead.ThreadPoolExecutor", executor)
    bulkhead = ThreadPoolBulkhead(max_workers=max_workers, max_queue_size=max_queue_size)
    try:
        yield bulkhead, release_workers
    finally:
        release_workers.set()
        bulkhead.shutdown()


@pytest.mark.parametrize(("max_workers", "max_queue_size"), [(1, 1), (2, 1), (1, 0), (2, 0), (1, 3)])
def test_admission_reserves_workers_before_backlog(
    monkeypatch: pytest.MonkeyPatch, max_workers: int, max_queue_size: int
) -> None:
    with _hold_workers(monkeypatch, max_workers, max_queue_size) as (bulkhead, release_workers):
        assert bulkhead.queue_size == 0
        assert not bulkhead.is_saturated
        futures = [bulkhead.submit(str, index, blocking=False) for index in range(max_workers)]
        assert bulkhead.queue_size == 0
        futures.extend(
            bulkhead.submit(str, index, blocking=False)
            for index in range(max_workers, max_workers + max_queue_size)
        )
        assert bulkhead.queue_size == max_queue_size
        assert bulkhead.is_saturated
        with pytest.raises(BulkheadRejectedError, match="admission queue full"):
            bulkhead.submit(str, "overflow", blocking=False)
        assert bulkhead.rejected_count == 1
        release_workers.set()
        assert [future.result(timeout=5) for future in futures] == [
            str(index) for index in range(max_workers + max_queue_size)
        ]


@contextmanager
def _busy_bulkhead(
    max_queue_size: int,
) -> Iterator[tuple[ThreadPoolBulkhead, Future[bool], threading.Event]]:
    """Own one blocked running task and always release it before shutdown."""
    started = threading.Event()
    release_worker = threading.Event()
    bulkhead = ThreadPoolBulkhead(max_workers=1, max_queue_size=max_queue_size)

    def occupy_worker() -> bool:
        started.set()
        return release_worker.wait()

    future = bulkhead.submit(occupy_worker, blocking=False)
    try:
        assert started.wait(timeout=5)
        yield bulkhead, future, release_worker
    finally:
        release_worker.set()
        bulkhead.shutdown()


@pytest.mark.parametrize("saturated", [False, True])
def test_negative_timed_admission_preserves_argument_validation(
    monkeypatch: pytest.MonkeyPatch, saturated: bool,
) -> None:
    """The former Queue.put contract rejects negative waits before admission."""
    with _hold_workers(monkeypatch, 1, 1) as (bulkhead, _release_workers):
        if saturated:
            bulkhead.submit(str, "worker", blocking=False)
            bulkhead.submit(str, "backlog", blocking=False)
        queue_size = bulkhead.queue_size
        with pytest.raises(ValueError, match="non-negative"):
            bulkhead.submit(str, "invalid", blocking=True, timeout=-0.1)
        assert bulkhead.queue_size == queue_size
        assert bulkhead.rejected_count == 0


def test_cancelled_backlog_keeps_capacity_until_dequeue() -> None:
    ran = threading.Event()
    with _busy_bulkhead(max_queue_size=1) as (bulkhead, running, release_worker):
        cancelled = bulkhead.submit(ran.set, blocking=False)
        assert cancelled.cancel()
        assert bulkhead.queue_size == 1
        assert bulkhead.is_saturated
        for _ in range(3):
            with pytest.raises(BulkheadRejectedError, match="admission queue full"):
                bulkhead.submit(str, "overflow", blocking=False)
        assert bulkhead.rejected_count == 3
        release_worker.set()
        assert running.result(timeout=5)
    assert cancelled.cancelled()
    assert not ran.is_set()
    assert bulkhead.queue_size == 0
    assert not bulkhead.is_saturated


def test_running_future_cannot_be_cancelled() -> None:
    with _busy_bulkhead(max_queue_size=0) as (_bulkhead, running, _release_worker):
        assert running.running()
        assert not running.cancel()


def test_completion_callback_can_reuse_worker_reservation() -> None:
    callback_finished = threading.Event()
    next_futures: list[Future[str]] = []
    errors: list[BaseException] = []
    with _busy_bulkhead(max_queue_size=0) as (bulkhead, running, release_worker):

        def reuse_slot(_future: Future[bool]) -> None:
            try:
                next_futures.append(bulkhead.submit(str, "next", blocking=False))
            except BaseException as exc:
                errors.append(exc)
            finally:
                callback_finished.set()

        running.add_done_callback(reuse_slot)
        release_worker.set()
        assert running.result(timeout=5)
        assert callback_finished.wait(timeout=5)
        assert not errors
        assert len(next_futures) == 1
        assert next_futures[0].result(timeout=5) == "next"


@pytest.mark.parametrize("failure", [ValueError("task failed"), KeyboardInterrupt("task failed")])
def test_task_failure_reclaims_worker_before_publishing_exception(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    callback_finished = threading.Event()
    next_futures: list[Future[str]] = []
    errors: list[BaseException] = []

    def fail() -> str:
        raise failure

    with _hold_workers(monkeypatch, 1, 0) as (bulkhead, release_workers):
        failed = bulkhead.submit(fail, blocking=False)

        def reuse_slot(_future: Future[str]) -> None:
            try:
                next_futures.append(bulkhead.submit(str, "recovered", blocking=False))
            except BaseException as exc:
                errors.append(exc)
            finally:
                callback_finished.set()

        failed.add_done_callback(reuse_slot)
        release_workers.set()
        with pytest.raises(type(failure), match="task failed"):
            failed.result(timeout=5)
        assert callback_finished.wait(timeout=5)
        assert not errors
        assert len(next_futures) == 1
        assert next_futures[0].result(timeout=5) == "recovered"


def test_saturated_timed_admission_rejects_once() -> None:
    with _busy_bulkhead(max_queue_size=1) as (bulkhead, _running, _release_worker):
        bulkhead.submit(str, "backlog", blocking=False)
        with pytest.raises(BulkheadRejectedError, match="admission queue full"):
            bulkhead.submit(str, "overflow", blocking=True, timeout=0)
        assert bulkhead.rejected_count == 1
        assert bulkhead.queue_size == 1


def test_timed_admission_resumes_on_worker_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    waited = threading.Event()
    owner = threading.current_thread()
    original_wait = threading.Condition.wait
    with _busy_bulkhead(max_queue_size=1) as (bulkhead, _running, release_worker):
        bulkhead.submit(str, "backlog", blocking=False)

        def release_on_wait(condition: threading.Condition, timeout: float | None = None) -> bool:
            if threading.current_thread() is owner:
                waited.set()
                release_worker.set()
            return original_wait(condition, timeout)

        monkeypatch.setattr(threading.Condition, "wait", release_on_wait)
        accepted = bulkhead.submit(str, "resumed", blocking=True, timeout=5)
        assert waited.is_set()
        assert accepted.result(timeout=5) == "resumed"


def test_concurrent_producers_share_one_bounded_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    with _hold_workers(monkeypatch, 2, 2) as (bulkhead, release_workers):
        start = threading.Barrier(7)
        accepted: list[Future[str]] = []
        rejected: list[BulkheadRejectedError] = []
        outcomes_lock = threading.Lock()

        def submit(index: int) -> None:
            start.wait(timeout=5)
            try:
                future = bulkhead.submit(str, index, blocking=False)
            except BulkheadRejectedError as exc:
                with outcomes_lock:
                    rejected.append(exc)
            else:
                with outcomes_lock:
                    accepted.append(future)

        producers = [
            threading.Thread(target=submit, args=(index,), name=f"gludd-bulkhead-test-producer-{index}")
            for index in range(6)
        ]
        for producer in producers:
            producer.start()
        start.wait(timeout=5)
        for producer in producers:
            producer.join(timeout=5)
            assert not producer.is_alive()
        assert len(accepted) == 4
        assert len(rejected) == 2
        assert bulkhead.queue_size == 2
        assert bulkhead.rejected_count == 2
        release_workers.set()
        assert len({future.result(timeout=5) for future in accepted}) == 4


@pytest.mark.parametrize("failure", [RuntimeError("executor refused"), KeyboardInterrupt("executor refused")])
def test_synchronous_submit_failure_reclaims_reservation(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    with _hold_workers(monkeypatch, 1, 1) as (bulkhead, release_workers):
        original_submit = bulkhead._executor.submit

        def refuse(_fn: object, *args: object, **kwargs: object) -> Future[object]:
            raise failure

        monkeypatch.setattr(bulkhead._executor, "submit", refuse)
        with pytest.raises(type(failure), match="executor refused"):
            bulkhead.submit(str, "refused", blocking=False)
        assert bulkhead.queue_size == 0
        assert not bulkhead.is_saturated
        assert bulkhead.rejected_count == 0
        monkeypatch.setattr(bulkhead._executor, "submit", original_submit)
        futures = [bulkhead.submit(str, value, blocking=False) for value in ("worker", "backlog")]
        assert bulkhead.queue_size == 1
        assert bulkhead.is_saturated
        release_workers.set()
        assert [future.result(timeout=5) for future in futures] == ["worker", "backlog"]


def test_shutdown_wakes_waiting_submitter(monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []
    original_wait = threading.Condition.wait

    def observe_wait(condition: threading.Condition, timeout: float | None = None) -> bool:
        if threading.current_thread() is submitter:
            waiting.set()
        return original_wait(condition, timeout)

    with _busy_bulkhead(max_queue_size=1) as (bulkhead, _running, _release_worker):
        queued = bulkhead.submit(str, "backlog", blocking=False)

        def submit_when_available() -> None:
            try:
                bulkhead.submit(str, "waiting", blocking=True, timeout=5)
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        submitter = threading.Thread(target=submit_when_available, name="gludd-bulkhead-test-submitter")
        monkeypatch.setattr(threading.Condition, "wait", observe_wait)
        submitter.start()
        try:
            assert waiting.wait(timeout=5)
            bulkhead.shutdown(wait=False)
            assert finished.wait(timeout=5)
            assert len(errors) == 1
            assert isinstance(errors[0], BulkheadRejectedError)
            assert "shut down" in str(errors[0])
            assert not queued.done()
        finally:
            _release_worker.set()
            submitter.join(timeout=5)
        assert not submitter.is_alive()


def test_shutdown_wait_false_drains_and_wait_true_joins_workers() -> None:
    with _busy_bulkhead(max_queue_size=1) as (bulkhead, running, release_worker):
        queued = bulkhead.submit(threading.current_thread, blocking=False)
        bulkhead.shutdown(wait=False)
        assert not running.done()
        assert not queued.done()
        with pytest.raises(BulkheadRejectedError, match="shut down"):
            bulkhead.submit(str, "late", blocking=False)
        release_worker.set()
        assert running.result(timeout=5)
        worker = queued.result(timeout=5)
        bulkhead.shutdown(wait=True)
        assert not worker.is_alive()
        assert worker.name.startswith("gludd-bulkhead-")
        assert bulkhead.queue_size == 0
