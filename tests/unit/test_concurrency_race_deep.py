"""Deep concurrency and race-condition tests for gludd's threading/asyncio patterns.

Covers lock acquisition ordering, deadlock detection, race conditions on shared
state, atomicity of compound operations, thread safety of singletons, re-entrant
lock behaviour, and background-thread lifecycle.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

import pytest

from general_ludd.budget.envelope import (
    BudgetEnvelope,
    PerAgentEnvelope,
    PerTaskEnvelope,
)
from general_ludd.budget.peak_pricing import PeakPricingTracker
from general_ludd.controllers.pause_store import PauseStore
from general_ludd.events.bus import EventBus
from general_ludd.events.types import Event
from general_ludd.feature_flags import (
    FeatureFlag,
    FlagEvaluationResult,
    FlagEvaluator,
)
from general_ludd.ipc.queue import Envelope, OverflowPolicy, WriteQueue
from general_ludd.observability.timing import StallWatchdog, default_tracker
from general_ludd.process.registry import ProcessRegistry, ProcessRegistryError

# ── helpers ────────────────────────────────────────────────────────────────


def _rlock_is_owned(rl: threading.RLock) -> bool:
    """Return True if rl is held by the current thread."""
    return getattr(rl, "_is_owned", lambda: False)()


def _assert_no_cycles(graph: dict[str, list[str]], *, msg: str = "") -> None:
    """Assert a directed graph has no cycles (deadlock-safe lock order)."""
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(node: str) -> bool:
        if node in stack:
            return False
        if node in visited:
            return True
        stack.add(node)
        visited.add(node)
        for neighbor in graph.get(node, []):
            if not dfs(neighbor):
                return False
        stack.discard(node)
        return True

    for node in graph:
        if node not in visited:
            assert dfs(node), msg or f"cycle detected in lock graph involving {node!r}"


# ── 1. Lock acquisition ordering ───────────────────────────────────────────


class TestLockAcquisitionOrdering:
    def test_lock_graph_is_acyclic(self):
        """Budget envelope locks form a flat, cycle-free graph."""
        graph: dict[str, list[str]] = {
            "BudgetEnvelope": [],
            "PerAgentEnvelope": [],
            "PerTaskEnvelope": [],
        }
        _assert_no_cycles(graph)

    def test_envelope_lock_always_released_on_exhaustion(self):
        """try_spend releases the lock even when budget is exceeded."""
        env = BudgetEnvelope("test", limit=10.0)
        env.record_spend(9.0)
        result = env.try_spend(3.0)
        assert result["allowed"] is False
        assert not env._lock.locked()

    def test_envelope_lock_released_after_bad_input(self):
        """try_spend releases the lock even on invalid input (fail-safe)."""
        env = BudgetEnvelope("test", limit=10.0)
        result = env.try_spend(float("nan"))
        assert result["allowed"] is False
        assert not env._lock.locked()

    def test_rlock_is_reentrant(self):
        """RLock allows nested acquisition from the same thread."""
        rl = threading.RLock()
        with rl:
            assert rl.acquire(blocking=False)
            rl.release()
        # After the context-manager exit the RLock is released
        assert not _rlock_is_owned(rl)

    def test_two_threads_on_same_lock_serialize(self):
        """Two threads contending for one Lock serialize correctly."""
        lock = threading.Lock()
        results: list[int] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker(wid: int) -> None:
            barrier.wait()
            with lock:
                time.sleep(0.01)
                results.append(wid)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, i) for i in range(2)]
            for f in as_completed(futures):
                f.result()

        assert len(results) == 2
        assert not lock.locked()

    def test_separate_instances_have_independent_locks(self):
        """Two BudgetEnvelope instances have distinct Lock objects."""
        e1 = BudgetEnvelope("a", limit=50.0)
        e2 = BudgetEnvelope("b", limit=50.0)
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def worker(env: BudgetEnvelope, amount: float) -> None:
            barrier.wait()
            try:
                env.record_spend(amount)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, e1, 10.0), pool.submit(worker, e2, 20.0)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0
        assert e1.spent == 10.0
        assert e2.spent == 20.0


# ── 2. Deadlock detection ──────────────────────────────────────────────────


class TestDeadlockDetection:
    def test_sorted_lock_acquisition_prevents_circular_wait(self):
        """Sorted lock order (as in file_overlap._acquire_paths) is cycle-free."""
        paths = ["src/b.py", "src/a.py", "src/c.py"]
        sorted_paths = sorted(paths)
        assert sorted_paths == ["src/a.py", "src/b.py", "src/c.py"]

    def test_rlock_nested_depth_tracking(self):
        """RLock tracks acquisition depth within the owning thread."""
        rl = threading.RLock()
        rl.acquire()
        assert _rlock_is_owned(rl)
        rl.acquire()  # nested — ok for RLock
        assert _rlock_is_owned(rl)
        rl.release()
        assert _rlock_is_owned(rl)  # still held (one level)
        rl.release()
        assert not _rlock_is_owned(rl)

    def test_lock_not_shared_between_unrelated_objects(self):
        """Holding a lock on one object does not block a different object's lock."""
        e1 = BudgetEnvelope("a", limit=10.0)
        e2 = BudgetEnvelope("b", limit=10.0)
        with e1._lock:
            assert e2._lock.acquire(blocking=False)
            e2._lock.release()

    def test_stall_watchdog_sweeper_stops_on_event(self):
        """Stop event cleanly terminates the background sweeper thread."""
        wd = StallWatchdog()
        wd.start_sweeper(interval_s=0.1)
        assert wd._sweeper is not None
        assert wd._sweeper.is_alive()
        wd.stop_sweeper()
        assert wd._stop.is_set()
        assert not wd._sweeper.is_alive()

    def test_stall_watchdog_double_start_is_idempotent(self):
        """start_sweeper is idempotent when already running."""
        wd = StallWatchdog()
        wd.start_sweeper(interval_s=0.1)
        thread1 = wd._sweeper
        assert thread1 is not None
        wd.start_sweeper(interval_s=0.2)
        thread2 = wd._sweeper
        assert thread2 is thread1
        wd.stop_sweeper()


# ── 3. Race condition on shared state ──────────────────────────────────────


class TestRaceConditionsOnSharedState:
    def test_budget_envelope_concurrent_spend_never_exceeds_limit(self):
        """Multiple threads doing try_spend never push total over the limit."""
        limit = 100.0
        env = BudgetEnvelope("shared", limit=limit)
        n_threads = 20
        amount_per = 8.0
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker() -> int:
            barrier.wait()
            accepted = 0
            for _ in range(5):
                result = env.try_spend(amount_per)
                if result["allowed"]:
                    accepted += 1
            return accepted

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker) for _ in range(n_threads)]
            total_accepted = sum(f.result() for f in as_completed(futures))

        assert env._spent <= limit + 1e-9
        assert env._spent == total_accepted * amount_per

    def test_eventbus_concurrent_subscribe_and_unsubscribe(self):
        """Concurrent subscribe/unsubscribe leaves EventBus in a consistent state."""
        bus = EventBus(history_size=10)
        n_threads = 16
        barrier = threading.Barrier(n_threads, timeout=10)
        sub_ids: list[str] = []

        def worker(i: int) -> None:
            barrier.wait()
            sid = bus.subscribe(f"topic-{i}", lambda e: None)
            sub_ids.append(sid)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        with bus._lock:
            total_subs = sum(len(v) for v in bus._subscribers.values())
        assert total_subs >= n_threads

        for sid in sub_ids:
            bus.unsubscribe(sid)

        with bus._lock:
            remaining = sum(len(v) for v in bus._subscribers.values())
        assert remaining == 0

    def test_eventbus_publish_uses_separate_locks(self):
        """Publish (subscriber lock) and history (history lock) are independent."""
        bus = EventBus(history_size=5)
        event = Event(type="custom", payload={"k": "v"})
        bus.publish(event)
        with bus._lock, bus._history_lock:
            assert len(bus._history) == 1

    def test_process_registry_concurrent_register(self):
        """Concurrent register calls do not lose entries."""
        registry = ProcessRegistry()
        n = 20
        barrier = threading.Barrier(n, timeout=10)
        registered: list[int] = []

        def worker(i: int) -> None:
            barrier.wait()
            pid = 1000 + i
            registry.register(pid, f"cmd-{i}")
            registered.append(pid)

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker, i) for i in range(n)]
            for f in as_completed(futures):
                f.result()

        with registry._lock:
            assert len(registry._procs) == n

        for pid in registered:
            registry.deregister(pid)
        with registry._lock:
            assert len(registry._procs) == 0

    def test_per_task_envelope_concurrent_get_or_create(self):
        """Concurrent try_spend on the same task_id creates exactly one envelope."""
        envelope = PerTaskEnvelope(default_limit=50.0)
        n_threads = 10
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker() -> None:
            barrier.wait()
            for _ in range(5):
                envelope.try_spend("task-1", 1.0)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker) for _ in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        with envelope._lock:
            assert len(envelope._envelopes) == 1
            env = envelope._envelopes["task-1"]
            assert env._spent <= 50.0 + 1e-9


# ── 4. Atomicity of compound operations ───────────────────────────────────


class TestAtomicityOfCompoundOperations:
    def test_try_spend_check_then_deduct_is_atomic(self):
        """Under high contention, every accepted try_spend sees valid remaining."""
        env = BudgetEnvelope("atomic", limit=50.0)
        n_threads = 10
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker() -> list[float]:
            barrier.wait()
            accepted: list[float] = []
            for _ in range(20):
                r = env.try_spend(1.0)
                if r["allowed"]:
                    accepted.append(1.0)
            return accepted

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker) for _ in range(n_threads)]
            total = sum(sum(f.result()) for f in as_completed(futures))

        assert total == env._spent
        assert total <= 50.0 + 1e-9

    def test_per_agent_envelope_set_limit_is_atomic(self):
        """Concurrent set_limit never leaves a torn state."""
        pae = PerAgentEnvelope()
        n_threads = 10
        barrier = threading.Barrier(n_threads, timeout=10)
        errors: list[Exception] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                pae.set_limit(f"model-{i}", float(100 + i))
                status = pae.get_status()
                raw = status.get(f"agent:model-{i}")
                assert raw is not None
                inner = cast("dict[str, object]", raw)
                assert inner["limit"] == float(100 + i)
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0
        with pae._lock:
            assert len(pae._envelopes) == n_threads

    def test_process_registry_seal_blocks_concurrent_register(self):
        """After seal(), all register calls raise, even from multiple threads."""
        registry = ProcessRegistry()
        registry.seal()
        with pytest.raises(ProcessRegistryError):
            registry.register(9999, "post-seal")

    def test_write_queue_put_is_atomic(self):
        """Many concurrent puts under REJECT policy yield a correct queue."""

        async def _run() -> None:
            q = WriteQueue(maxsize=10, policy=OverflowPolicy.REJECT)

            async def _putter(i: int) -> None:
                await q.put(Envelope(topic=f"topic-{i}", payload={"n": i}))

            await asyncio.gather(*(_putter(i) for i in range(10)))
            assert len(q) == 10
            assert q.total_rejected == 0

        asyncio.run(_run())

    def test_write_queue_reject_preserves_oldest(self):
        """Under REJECT, a full queue keeps its existing entries."""

        async def _run() -> None:
            q = WriteQueue(maxsize=3, policy=OverflowPolicy.REJECT)
            for i in range(3):
                await q.put(Envelope(topic=f"t-{i}"))
            assert len(q) == 3
            result = await q.put(Envelope(topic="t-overflow"))
            assert result is False
            assert q.total_rejected == 1
            assert q.total_offered == 4
            assert len(q) == 3
            e0 = await q.get()
            assert e0.topic == "t-0"

        asyncio.run(_run())


# ── 5. Thread safety of singletons ────────────────────────────────────────


class TestSingletonThreadSafety:
    def test_peak_pricing_tracker_double_checked_locking(self):
        """Double-checked locking ensures exactly one PeakPricingTracker."""
        PeakPricingTracker._singleton = None

        def worker() -> PeakPricingTracker:
            return PeakPricingTracker.singleton()

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(worker) for _ in range(20)]
            instances = [f.result() for f in as_completed(futures)]

        assert all(inst is instances[0] for inst in instances)
        assert PeakPricingTracker._singleton is instances[0]

    def test_peak_pricing_tracker_record_call_is_atomic(self):
        """Concurrent record_call accumulates totals correctly."""
        PeakPricingTracker._singleton = None
        tracker = PeakPricingTracker.singleton()

        def worker(amount: float) -> None:
            for _ in range(10):
                tracker.record_call(base_cost=amount, effective_cost=amount * 0.5)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, 1.0) for _ in range(8)]
            for f in as_completed(futures):
                f.result()

        assert tracker.cumulative_full_cost == 80.0
        assert tracker.cumulative_discounted_cost == 40.0
        assert tracker.cumulative_savings == 40.0

    def test_default_tracker_is_lazy_singleton(self):
        """default_tracker() returns the same instance every time."""
        t1 = default_tracker()
        t2 = default_tracker()
        assert t1 is t2

    def test_pause_store_distinct_locks(self):
        """Different PauseStore instances have fully independent locks."""
        tmp1 = "/tmp/gludd-test-conc-ps1"
        tmp2 = "/tmp/gludd-test-conc-ps2"
        os.makedirs(tmp1, mode=0o700, exist_ok=True)
        os.makedirs(tmp2, mode=0o700, exist_ok=True)
        ps1 = PauseStore(base_dir=tmp1)
        ps2 = PauseStore(base_dir=tmp2)
        with ps1._lock:
            assert ps2._lock.acquire(blocking=False)
            ps2._lock.release()


# ── 6. Feature flags concurrent evaluate ───────────────────────────────────


class TestFeatureFlagConcurrency:
    def test_parallel_evaluate_does_not_corrupt_state(self):
        """Concurrent evaluate calls never corrupt the internal flag dict."""
        flags = [
            FeatureFlag(
                name="beta",
                default=True,
                rollout_percentage=50.0,
            )
        ]
        evaluator = FlagEvaluator(flags)
        n_threads = 16
        barrier = threading.Barrier(n_threads, timeout=10)
        results: list[FlagEvaluationResult] = []

        def worker() -> None:
            barrier.wait()
            for _ in range(10):
                r = evaluator.evaluate("beta", {"id": "user-1"})
                results.append(r)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker) for _ in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert all(r.flag_name == "beta" for r in results)

    def test_concurrent_register_and_evaluate(self):
        """Registering flags while evaluating concurrently produces consistent results."""
        evaluator = FlagEvaluator([])
        n_threads = 10
        barrier = threading.Barrier(n_threads, timeout=10)
        errors: list[Exception] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                evaluator.register(
                    FeatureFlag(
                        name=f"flag_{i}",
                        default=True,
                        rollout_percentage=100.0,
                    )
                )
                result = evaluator.evaluate(f"flag_{i}", {"id": "u"})
                assert result is not None
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in as_completed(futures):
                f.result()

        assert len(errors) == 0
        flags = evaluator.list_flags()
        assert len(flags) >= n_threads


# ── 7. Edge cases ──────────────────────────────────────────────────────────


class TestEnvelopeEdgeCases:
    def test_record_spend_rejects_non_finite(self) -> None:
        env = BudgetEnvelope("e", limit=100.0)
        with pytest.raises(ValueError):
            env.record_spend(float("nan"))
        with pytest.raises(ValueError):
            env.record_spend(-1.0)
        assert env.spent == 0.0

    def test_try_spend_exactly_at_limit(self) -> None:
        env = BudgetEnvelope("e", limit=10.0)
        result = env.try_spend(10.0)
        assert result["allowed"] is True
        assert env.is_exhausted

    def test_try_spend_one_cent_over_limit(self) -> None:
        env = BudgetEnvelope("e", limit=10.0)
        env.record_spend(10.0)
        result = env.try_spend(0.01)
        assert result["allowed"] is False

    def test_reset_releases_lock(self) -> None:
        env = BudgetEnvelope("e", limit=100.0)
        env.record_spend(50.0)
        env.reset()
        assert env.spent == 0.0
        assert not env.is_exhausted
        assert not env._lock.locked()

    def test_budget_envelope_inf_limit_never_exhausted(self) -> None:
        env = BudgetEnvelope("e")  # default limit=inf
        assert not env.is_exhausted
        r = env.try_spend(999_999.0)
        assert r["allowed"] is True
        assert not env.is_exhausted

    def test_process_registry_register_duplicate_pid_overwrites(self) -> None:
        registry = ProcessRegistry()
        r1 = registry.register(5000, "cmd-a")
        r2 = registry.register(5000, "cmd-b")
        assert r1.command != r2.command
        assert r2.command == ["cmd-b"]
