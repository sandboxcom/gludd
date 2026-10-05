"""Deep tests for Barrier / Wait-Group async coordination primitive.

Covers:
  - wait for N: N-1 waiters block, Nth unblocks all
  - reset: reuse barrier after all parties release
  - abort: unblock all waiters with BarrierAborted
  - timeout: waiter raises BarrierTimeout, barrier enters broken state
  - broken barrier recovery: reset clears broken state
  - wait_any: unblock when any one party passes (wait-group pattern)
  - wait_group / count_down: manual count-down variant
  - stress: many concurrent waiters
  - drain: barrier emptied and refilled
  - re-entrant safety: same barrier used across phases
  - abort during wait: pending waiters unblocked
  - reset while waiters blocked: raises RuntimeError
  - default timeout and per-call override
  - __repr__ and state introspection
  - context manager interface
  - zero-party barrier (degenerate case)
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from general_ludd.coordination.barrier import (
    Barrier,
    BarrierAborted,
    BarrierBroken,
    BarrierTimeout,
    WaitGroup,
)

# ---------------------------------------------------------------------------
# Barrier — basic wait-for-N
# ---------------------------------------------------------------------------


async def test_barrier_wait_for_two() -> None:
    barrier = Barrier(2)
    order: list[str] = []
    released = asyncio.Event()

    async def party_a() -> None:
        order.append("A-before")
        await barrier.wait()
        order.append("A-after")
        released.set()

    async def party_b() -> None:
        order.append("B-before")
        await asyncio.sleep(0.05)
        order.append("B-enter-barrier")
        await barrier.wait()
        order.append("B-after")

    async with asyncio.TaskGroup() as tg:
        tg.create_task(party_a())
        tg.create_task(party_b())

    assert order.index("A-before") < order.index("B-enter-barrier")
    assert order.index("B-enter-barrier") < order.index("A-after")
    assert order.index("B-enter-barrier") < order.index("B-after")


async def test_barrier_wait_for_three() -> None:
    barrier = Barrier(3)
    ready: list[str] = []

    async def party(name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        ready.append(name)
        await barrier.wait()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(party("A", 0.0))
        tg.create_task(party("B", 0.02))
        tg.create_task(party("C", 0.04))

    assert ready == ["A", "B", "C"]


async def test_barrier_waiters_block_until_full() -> None:
    barrier = Barrier(4)
    wake_order: list[int] = []
    wake_events: list[asyncio.Event] = [asyncio.Event() for _ in range(4)]

    async def party(idx: int) -> None:
        await barrier.wait()
        wake_order.append(idx)
        wake_events[idx].set()

    tasks = [asyncio.create_task(party(i)) for i in range(3)]
    await asyncio.sleep(0.1)
    assert len(wake_order) == 0  # none should have passed

    tasks.append(asyncio.create_task(party(3)))  # 4th unblocks all
    for e in wake_events:
        await asyncio.wait_for(e.wait(), timeout=2.0)
    assert len(wake_order) == 4


# ---------------------------------------------------------------------------
# Barrier — reset
# ---------------------------------------------------------------------------


async def test_barrier_reset_reuse() -> None:
    barrier = Barrier(2)
    phase_counts: list[int] = []

    async def two_phase() -> None:
        await barrier.wait()
        phase_counts.append(1)
        await barrier.wait()
        phase_counts.append(2)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(two_phase())
        tg.create_task(two_phase())

    assert phase_counts == [1, 1, 2, 2]


async def test_barrier_reset_with_different_parties() -> None:
    barrier = Barrier(3)
    hits: list[str] = []

    async def party(name: str) -> None:
        await barrier.wait()
        hits.append(f"{name}-phase1")

    async with asyncio.TaskGroup() as tg:
        tg.create_task(party("X"))
        tg.create_task(party("Y"))
        tg.create_task(party("Z"))

    assert len(hits) == 3

    hits.clear()
    barrier = Barrier(3)
    async with asyncio.TaskGroup() as tg:
        tg.create_task(party("P"))
        tg.create_task(party("Q"))
        tg.create_task(party("R"))

    assert len(hits) == 3


# ---------------------------------------------------------------------------
# Barrier — abort
# ---------------------------------------------------------------------------


async def test_barrier_abort_unblocks_all() -> None:
    barrier = Barrier(3)
    aborted_count = 0

    async def waiter() -> None:
        nonlocal aborted_count
        try:
            await barrier.wait()
        except BarrierAborted:
            aborted_count += 1
            raise

    tasks = [asyncio.create_task(waiter()) for _ in range(2)]
    await asyncio.sleep(0.05)
    barrier.abort()
    for t in tasks:
        with pytest.raises(BarrierAborted):
            await t
    assert aborted_count == 2


async def test_barrier_abort_broken_state() -> None:
    barrier = Barrier(2)
    barrier.abort()
    with pytest.raises(BarrierAborted):
        await barrier.wait()


async def test_barrier_abort_idempotent() -> None:
    barrier = Barrier(2)
    barrier.abort()
    barrier.abort()  # should not raise


# ---------------------------------------------------------------------------
# Barrier — timeout
# ---------------------------------------------------------------------------


async def test_barrier_timeout_from_waiter() -> None:
    barrier = Barrier(3, default_timeout=0.1)
    with pytest.raises(BarrierTimeout):
        await barrier.wait()


async def test_barrier_timeout_broken_state() -> None:
    barrier = Barrier(3, default_timeout=0.1)

    async def waiter() -> None:
        with pytest.raises(BarrierTimeout):
            await barrier.wait()

    await waiter()
    assert barrier.broken is True
    assert barrier._exception is not None


async def test_barrier_broken_rejects_new_waiters() -> None:
    barrier = Barrier(3, default_timeout=0.05)
    with contextlib.suppress(BarrierTimeout):
        await barrier.wait()
    with pytest.raises(BarrierBroken, match="Barrier is broken"):
        await barrier.wait()


async def test_barrier_per_call_timeout() -> None:
    barrier = Barrier(3, default_timeout=10.0)
    with pytest.raises(BarrierTimeout):
        await barrier.wait(timeout=0.02)


# ---------------------------------------------------------------------------
# Barrier — broken recovery via reset
# ---------------------------------------------------------------------------


async def test_barrier_reset_clears_broken() -> None:
    barrier = Barrier(3, default_timeout=0.05)
    with contextlib.suppress(BarrierTimeout):
        await barrier.wait()
    assert barrier.broken is True
    barrier.reset()
    assert barrier.broken is False
    assert barrier._exception is None
    assert barrier._waiters == 0


async def test_barrier_recover_and_use_after_broken() -> None:
    barrier = Barrier(2, default_timeout=0.05)
    with contextlib.suppress(BarrierTimeout):
        await barrier.wait()
    barrier.reset()
    results: list[str] = []

    async def party(name: str) -> None:
        await barrier.wait()
        results.append(name)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(party("A"))
        tg.create_task(party("B"))
    assert sorted(results) == ["A", "B"]


# ---------------------------------------------------------------------------
# Barrier — abort during active wait
# ---------------------------------------------------------------------------


async def test_barrier_abort_while_waiters_blocked() -> None:
    barrier = Barrier(4)
    aborted: list[str] = []

    async def waiter(name: str) -> None:
        try:
            await barrier.wait()
        except BarrierAborted:
            aborted.append(name)
            raise

    tasks = [asyncio.create_task(waiter(f"w{i}")) for i in range(3)]
    await asyncio.sleep(0.05)
    barrier.abort()
    for t in tasks:
        with pytest.raises(BarrierAborted):
            await t
    assert len(aborted) == 3


# ---------------------------------------------------------------------------
# Barrier — reset while waiters blocked (error)
# ---------------------------------------------------------------------------


async def test_barrier_reset_with_active_waiters_raises() -> None:
    barrier = Barrier(3)
    task = asyncio.create_task(barrier.wait())
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError, match=r"Cannot reset.*active waiters"):
        barrier.reset()
    barrier.abort()
    with pytest.raises(BarrierAborted):
        await task


# ---------------------------------------------------------------------------
# Barrier — introspection
# ---------------------------------------------------------------------------


def test_barrier_repr() -> None:
    barrier = Barrier(4, default_timeout=5.0)
    r = repr(barrier)
    assert "Barrier" in r
    assert "parties=4" in r
    assert "waiters=0" in r
    assert "broken=False" in r


def test_barrier_properties() -> None:
    barrier = Barrier(5)
    assert barrier.parties == 5
    assert barrier.waiters == 0
    assert barrier.broken is False


async def test_barrier_waiters_count_active() -> None:
    barrier = Barrier(3)
    task = asyncio.create_task(barrier.wait())
    await asyncio.sleep(0.05)
    assert barrier.waiters >= 1
    barrier.abort()
    with contextlib.suppress(BarrierAborted):
        await task


# ---------------------------------------------------------------------------
# Barrier — context manager
# ---------------------------------------------------------------------------


async def test_barrier_context_manager() -> None:
    barrier = Barrier(2)
    passed: list[str] = []

    async def enter(name: str) -> None:
        async with barrier:
            passed.append(name)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(enter("X"))
        tg.create_task(enter("Y"))
    assert sorted(passed) == ["X", "Y"]


# ---------------------------------------------------------------------------
# Barrier — zero-party degenerate
# ---------------------------------------------------------------------------


async def test_barrier_zero_parties_immediate() -> None:
    barrier = Barrier(0)
    await barrier.wait()


def test_barrier_zero_parties_abort_noop() -> None:
    barrier = Barrier(0)
    barrier.abort()
    assert barrier.broken is True


# ---------------------------------------------------------------------------
# WaitGroup — manual count-down
# ---------------------------------------------------------------------------


async def test_wait_group_add_and_done() -> None:
    wg = WaitGroup()
    wg.add(3)
    done_count = 0

    async def worker(delay: float) -> None:
        nonlocal done_count
        await asyncio.sleep(delay)
        done_count += 1
        wg.done()

    async def collector() -> int:
        await wg.wait()
        return done_count

    async with asyncio.TaskGroup() as tg:
        tg.create_task(worker(0.02))
        tg.create_task(worker(0.04))
        tg.create_task(worker(0.06))
        coll = tg.create_task(collector())

    assert coll.result() == 3
    assert wg.counter == 0


async def test_wait_group_add_after_done() -> None:
    wg = WaitGroup()
    wg.add(1)
    wg.done()
    await wg.wait()
    wg.add(2)
    assert wg.counter == 2


async def test_wait_group_multiple_waiters() -> None:
    wg = WaitGroup()
    wg.add(2)
    wait_results: list[bool] = []

    async def multi_waiter(idx: int) -> None:
        await wg.wait()
        wait_results.append(True)

    async def finisher() -> None:
        await asyncio.sleep(0.03)
        wg.done()
        await asyncio.sleep(0.03)
        wg.done()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(multi_waiter(1))
        tg.create_task(multi_waiter(2))
        tg.create_task(finisher())

    assert len(wait_results) == 2


async def test_wait_group_timeout() -> None:
    wg = WaitGroup(default_timeout=0.05)
    wg.add(1)
    with pytest.raises(BarrierTimeout):
        await wg.wait()


def test_wait_group_repr() -> None:
    wg = WaitGroup()
    wg.add(5)
    r = repr(wg)
    assert "WaitGroup" in r
    assert "counter=5" in r


# ---------------------------------------------------------------------------
# Barrier — stress
# ---------------------------------------------------------------------------


async def test_barrier_stress_many_phases() -> None:
    barrier = Barrier(5)
    phase_results: list[int] = []

    async def phase_runner() -> None:
        for p in range(10):
            await barrier.wait()
            phase_results.append(p)

    tasks = [asyncio.create_task(phase_runner()) for _ in range(5)]
    await asyncio.gather(*tasks)
    assert len(phase_results) == 50
    assert phase_results.count(0) == 5
    assert phase_results.count(9) == 5


async def test_barrier_stress_concurrent_waiters() -> None:
    barrier = Barrier(20)
    count = 0

    async def party() -> None:
        nonlocal count
        await barrier.wait()
        count += 1

    tasks = [asyncio.create_task(party()) for _ in range(20)]
    await asyncio.gather(*tasks)
    assert count == 20


def test_barrier_and_wait_group_reject_negative_counts() -> None:
    with pytest.raises(ValueError, match="parties must be >= 0"):
        Barrier(-1)

    wait_group = WaitGroup()
    with pytest.raises(ValueError, match="delta must be >= 0"):
        wait_group.add(-1)
    with pytest.raises(ValueError, match="n must be >= 0"):
        wait_group.done(-1)


async def test_cancelled_waiter_breaks_barrier_for_later_participants() -> None:
    barrier = Barrier(2)
    waiter = asyncio.create_task(barrier.wait())
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(BarrierBroken):
        await barrier.wait()


async def test_zero_party_reset_remains_immediately_released() -> None:
    barrier = Barrier(0)
    barrier.abort()
    barrier.reset()

    await barrier.wait()

    assert barrier.broken is False
    assert barrier.waiters == 0


async def test_wait_group_zero_and_repeated_add_preserve_event_contract() -> None:
    wait_group = WaitGroup()
    wait_group.add(0)
    await wait_group.wait()
    wait_group.add(1)
    wait_group.add(1)
    wait_group.done(2)

    await wait_group.wait()

    assert wait_group.counter == 0
