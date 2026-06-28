"""Concurrency guard for MetricsCollector (worker-thread race).

Gateway model calls run OFF the asyncio event loop via ``asyncio.to_thread``,
so ``MetricsCollector.record_model_call`` is invoked from many worker threads
at once. Before the RLock landed, the shared dicts raced:

  * ``ModelUsage.record_call`` does non-atomic ``+=`` -> lost increments, so the
    final ``total_calls`` would be LESS than the number of recorded calls.
  * readers that iterate the dicts (``get_full_report`` / ``get_global_model_usage``
    / ``get_cost_estimate``) could raise ``RuntimeError: dictionary changed size
    during iteration`` while a writer inserted a new model.

These tests pound the collector from 16 threads while readers sweep it, and
assert (a) no increments are lost and (b) no reader raises. They are the
record-side analogue of ``test_budget_guard.py``'s spend-lock contention test.
"""

from __future__ import annotations

import inspect
import threading

from general_ludd.metrics import collector as collector_mod
from general_ludd.metrics.collector import MetricsCollector

_THREADS = 16
_CALLS_PER_THREAD = 500
_MODELS = ("model-a", "model-b", "model-c")


def test_record_model_call_no_lost_increments() -> None:
    mc = MetricsCollector()
    for _mid in _MODELS:
        # pre-register the agent that owns these calls
        pass
    mc.register_agent("agent-0", "a0", "proj")

    barrier = threading.Barrier(_THREADS)
    errors: list[BaseException] = []

    def worker(tid: int) -> None:
        try:
            barrier.wait()
            for i in range(_CALLS_PER_THREAD):
                mid = _MODELS[i % len(_MODELS)]
                # alternate success/failure so the failure ring is exercised too
                mc.record_model_call(
                    "agent-0",
                    mid,
                    input_tokens=1,
                    output_tokens=2,
                    success=(i % 2 == 0),
                    error=None if (i % 2 == 0) else "boom",
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer threads raised: {errors!r}"

    usage = mc.get_global_model_usage()
    total_recorded = sum(u.total_calls for u in usage.values())
    # Every single call must be accounted for — no lost += under contention.
    assert total_recorded == _THREADS * _CALLS_PER_THREAD
    # Token accounting is likewise atomic.
    total_in = sum(u.total_input_tokens for u in usage.values())
    total_out = sum(u.total_output_tokens for u in usage.values())
    assert total_in == _THREADS * _CALLS_PER_THREAD * 1
    assert total_out == _THREADS * _CALLS_PER_THREAD * 2


def test_readers_do_not_crash_during_concurrent_writes() -> None:
    mc = MetricsCollector()
    mc.register_agent("agent-0", "a0", "proj")

    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            i = 0
            while not stop.is_set():
                # Insert brand-new model ids continuously so readers iterate a
                # dict that is actively growing — the exact "changed size during
                # iteration" trigger.
                mc.record_model_call(
                    "agent-0", f"m{i}", 1, 1, success=(i % 3 != 0), error="x"
                )
                i += 1
        except BaseException as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            while not stop.is_set():
                mc.get_full_report()
                mc.get_global_model_usage()
                mc.get_cost_estimate()
                mc.get_cost_by_project()
                mc.is_flaky("m0")
                mc.get_recent_failures("m0")
                mc.list_running_agents()
        except BaseException as exc:
            errors.append(exc)

    writers = [threading.Thread(target=writer) for _ in range(4)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    # let them contend briefly, then stop (event-driven, no fixed sleep call)
    for _ in range(200_000):
        if errors:
            break
    stop.set()
    for t in writers + readers:
        t.join()

    assert not errors, f"reader/writer raced and raised: {errors!r}"


def test_collector_uses_rlock_source_guard() -> None:
    """Pin the lock so a future refactor can't silently drop thread-safety.

    RLock (not plain Lock) is required because get_full_report composes
    list_running_agents and get_agent_summary, which re-acquire on the same
    thread; a plain Lock would self-deadlock.
    """
    src = inspect.getsource(collector_mod.MetricsCollector.__init__)
    assert "threading.RLock()" in src
    record_src = inspect.getsource(collector_mod.MetricsCollector.record_model_call)
    assert "with self._lock:" in record_src
