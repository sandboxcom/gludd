"""Adversarial timing and lifecycle tests for the v2 debounce primitives."""

from __future__ import annotations

import asyncio
import gc
import logging
import weakref
from collections.abc import Callable, Coroutine, Iterable, MutableSet
from typing import Any

import pytest

from general_ludd.util import debounce_v2 as debounce_module
from general_ludd.util.async_lifecycle import cancel_and_drain_tasks
from general_ludd.util.debounce_v2 import AsyncDebounceV2, DebounceV2


class JumpClock:
    """Controllable clock that can model broken backwards observations."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta

    def set(self, now: float) -> None:
        """Set an absolute time, including a backwards discontinuity."""
        self.now = now


def test_burst_coalesces_latest_payload_at_exact_quiet_boundary() -> None:
    """Every call moves the trailing edge and only the latest payload fires."""
    calls: list[str] = []
    clock = JumpClock()
    debounce = DebounceV2(calls.append, wait=1.0, clock=clock)

    debounce("first")
    clock.advance(0.9)
    debounce("middle")
    clock.advance(0.9)
    debounce("latest")
    clock.set(2.799)
    debounce._tick()
    assert calls == []
    clock.set(2.8)
    debounce._tick()
    assert calls == ["latest"]


def test_new_leading_burst_resets_quiet_and_max_wait_epochs() -> None:
    """A fresh leading edge must not inherit the prior burst's max-wait cap."""
    calls: list[str] = []
    clock = JumpClock()
    debounce = DebounceV2(
        calls.append,
        wait=1.0,
        leading=True,
        trailing=True,
        max_wait=2.0,
        clock=clock,
    )

    debounce("leading-one")
    clock.set(1.1)
    debounce("leading-two")
    clock.set(1.2)
    debounce("trailing-two")
    clock.set(2.0)
    debounce._tick()
    assert calls == ["leading-one", "leading-two"]
    clock.set(2.2)
    debounce._tick()
    assert calls == ["leading-one", "leading-two", "trailing-two"]


def test_backwards_clock_jump_cannot_move_deadline_earlier() -> None:
    """A broken clock observation cannot violate the established quiet window."""
    calls: list[str] = []
    clock = JumpClock(100.0)
    debounce = DebounceV2(calls.append, wait=5.0, clock=clock)

    debounce("first")
    clock.set(90.0)
    debounce("after-jump")
    clock.set(95.0)
    debounce._tick()
    assert calls == []
    clock.set(105.0)
    debounce._tick()
    assert calls == ["after-jump"]


def test_reentrant_leading_callback_preserves_nested_trailing_call() -> None:
    """Outer leading bookkeeping cannot erase work queued by its callback."""
    calls: list[int] = []
    clock = JumpClock()
    debounce: DebounceV2

    def callback(value: int) -> None:
        calls.append(value)
        if value == 1:
            debounce(2)

    debounce = DebounceV2(
        callback,
        wait=1.0,
        leading=True,
        trailing=True,
        clock=clock,
    )
    debounce(1)
    assert debounce.pending
    clock.set(1.0)
    debounce._tick()
    assert calls == [1, 2]


def test_trailing_callback_exception_clears_state_for_handoff() -> None:
    """A failed synchronous callback cannot poison the next quiet window."""
    calls: list[str] = []
    clock = JumpClock()

    def callback(value: str) -> None:
        calls.append(value)
        if value == "bad":
            raise RuntimeError("callback failed")

    debounce = DebounceV2(callback, wait=1.0, clock=clock)
    debounce("bad")
    clock.set(1.0)
    with pytest.raises(RuntimeError, match="callback failed"):
        debounce._tick()
    assert not debounce.pending

    debounce("good")
    clock.set(2.0)
    debounce._tick()
    assert calls == ["bad", "good"]


@pytest.mark.asyncio
async def test_cancelled_generation_with_equal_payload_cannot_fire_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task identity, not equal arguments, fences a cancelled generation."""
    calls: list[str] = []
    first_sleep_started = asyncio.Event()
    release_first_sleep = asyncio.Event()
    sleep_calls = 0
    real_sleep = asyncio.sleep

    async def controlled_sleep(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            first_sleep_started.set()
            try:
                await release_first_sleep.wait()
            except asyncio.CancelledError:
                await release_first_sleep.wait()
        else:
            await real_sleep(60.0)

    async def callback(value: str) -> None:
        calls.append(value)

    monkeypatch.setattr(
        "general_ludd.util.debounce_v2.asyncio.sleep",
        controlled_sleep,
    )
    debounce = AsyncDebounceV2(callback, wait=60.0)
    debounce("same")
    await first_sleep_started.wait()
    debounce("same")
    release_first_sleep.set()
    await real_sleep(0)
    await real_sleep(0)
    try:
        assert calls == []
    finally:
        await debounce.aclose()


@pytest.mark.asyncio
async def test_async_callback_exception_is_observed_and_next_burst_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Background callback failure is observed without blocking ZDD handoff."""
    calls: list[str] = []
    loop = asyncio.get_running_loop()
    unhandled: list[dict[str, Any]] = []
    previous_handler = loop.get_exception_handler()

    async def callback(value: str) -> None:
        calls.append(value)
        if value == "bad":
            raise RuntimeError("async callback failed")

    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    caplog.set_level(logging.ERROR, logger=debounce_module.__name__)
    debounce = AsyncDebounceV2(callback, wait=0.0)
    try:
        debounce("bad")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        debounce("good")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await debounce.aclose()
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert calls == ["bad", "good"]
    assert not unhandled
    assert "async callback failed" in caplog.text


class _CancellationResistantDebounce(AsyncDebounceV2):
    """Keep the first superseded timer alive until the test releases it."""

    def __init__(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        wait: float,
        *,
        started: asyncio.Queue[int],
        release_old: asyncio.Event,
        current_stopped: asyncio.Event,
    ) -> None:
        super().__init__(fn, wait)
        self.started = started
        self.release_old = release_old
        self.current_stopped = current_stopped
        self.run_count = 0

    async def _run_after(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        delay: float,
    ) -> None:
        del args, kwargs, delay
        self.run_count += 1
        run_number = self.run_count
        self.started.put_nowait(run_number)
        if run_number == 1:
            while not self.release_old.is_set():
                try:
                    await self.release_old.wait()
                except asyncio.CancelledError:
                    continue
            return
        try:
            await asyncio.Future()
        finally:
            self.current_stopped.set()


@pytest.mark.asyncio
async def test_aclose_drains_superseded_tasks_and_prevents_reopen() -> None:
    """Shutdown owns every timer generation and leaves no post-close intake."""
    started: asyncio.Queue[int] = asyncio.Queue()
    release_old = asyncio.Event()
    current_stopped = asyncio.Event()

    async def callback(_value: int) -> None:
        return None

    debounce = _CancellationResistantDebounce(
        callback,
        wait=60.0,
        started=started,
        release_old=release_old,
        current_stopped=current_stopped,
    )
    debounce(1)
    assert await started.get() == 1
    debounce(2)
    assert await started.get() == 2
    close_task = asyncio.create_task(debounce.aclose())
    await current_stopped.wait()
    await asyncio.sleep(0)
    try:
        assert not close_task.done(), "shutdown lost ownership of a superseded timer"
    finally:
        release_old.set()
        await close_task
        await asyncio.sleep(0)

    assert not debounce.pending
    with pytest.raises(RuntimeError, match="closed"):
        debounce(3)


@pytest.mark.asyncio
async def test_completed_timer_releases_payload_and_task_reference() -> None:
    """Normal completion must not retain a payload through the finished task."""

    class Payload:
        pass

    seen = asyncio.Event()

    async def callback(_payload: Payload) -> None:
        seen.set()

    debounce = AsyncDebounceV2(callback, wait=0.0)
    payload = Payload()
    payload_ref = weakref.ref(payload)
    debounce(payload)
    del payload
    await seen.wait()
    await asyncio.sleep(0)
    gc.collect()

    assert debounce._task is None
    assert debounce._pending_args is None
    assert payload_ref() is None
    await debounce.aclose()


@pytest.mark.asyncio
async def test_concurrent_debounce_keys_remain_independent() -> None:
    """Bursts for independent keys cannot cancel or overwrite one another."""
    calls: list[tuple[str, int]] = []

    async def callback(key: str, value: int) -> None:
        calls.append((key, value))

    alpha = AsyncDebounceV2(callback, wait=0.0)
    beta = AsyncDebounceV2(callback, wait=0.0)
    alpha("alpha", 1)
    beta("beta", 1)
    alpha("alpha", 2)
    beta("beta", 2)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.gather(alpha.aclose(), beta.aclose())

    assert sorted(calls) == [("alpha", 2), ("beta", 2)]


@pytest.mark.asyncio
async def test_aclose_transfers_superseded_and_current_timers_to_native_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close must transfer both live generations to the real drain owner."""
    batches: list[tuple[asyncio.Task[Any], ...]] = []
    real_drain = cancel_and_drain_tasks

    async def observe_drain(
        tasks: Iterable[asyncio.Task[Any]],
        *,
        registry: MutableSet[asyncio.Task[Any]] | None = None,
    ) -> None:
        batch = tuple(tasks)
        batches.append(batch)
        await real_drain(batch, registry=registry)

    monkeypatch.setattr(debounce_module, "cancel_and_drain_tasks", observe_drain)
    started: asyncio.Queue[int] = asyncio.Queue()
    release_old = asyncio.Event()
    current_stopped = asyncio.Event()

    async def callback(_value: int) -> None:
        return None

    debounce = _CancellationResistantDebounce(
        callback,
        wait=60.0,
        started=started,
        release_old=release_old,
        current_stopped=current_stopped,
    )
    debounce(1)
    assert await started.get() == 1
    superseded = debounce._task
    assert superseded is not None
    debounce(2)
    assert await started.get() == 2
    current = debounce._task
    assert current is not None and current is not superseded
    close_task = asyncio.create_task(debounce.aclose())
    await current_stopped.wait()
    try:
        transferred = {task for batch in batches for task in batch}
        assert superseded in transferred
        assert current in transferred
        assert not superseded.done()
        assert not close_task.done()
    finally:
        release_old.set()
        await close_task
        await asyncio.sleep(0)

    assert superseded.done() and current.done()
    assert not debounce.pending


@pytest.fixture
def _guard_native_drain_against_self(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop an invalid self-transfer before asyncio recursively gathers itself."""
    real_drain = cancel_and_drain_tasks

    async def guarded_drain(
        tasks: Iterable[asyncio.Task[Any]],
        *,
        registry: MutableSet[asyncio.Task[Any]] | None = None,
    ) -> None:
        snapshot = tuple(tasks)
        assert asyncio.current_task() not in snapshot, (
            "aclose transferred its calling callback to the native drain"
        )
        await real_drain(snapshot, registry=registry)

    monkeypatch.setattr(debounce_module, "cancel_and_drain_tasks", guarded_drain)


@pytest.mark.asyncio
@pytest.mark.parametrize("leading", [True, False], ids=["leading", "trailing"])
async def test_callback_can_close_itself_without_losing_live_task_ownership(
    leading: bool, _guard_native_drain_against_self: None,
) -> None:
    """Self-close stops intake and lets its still-owned callback finish normally."""
    closed = asyncio.Event()
    release_callback = asyncio.Event()

    async def callback() -> None:
        await debounce.aclose()
        closed.set()
        await release_callback.wait()

    debounce = AsyncDebounceV2(
        callback, wait=0.0, leading=leading, trailing=not leading,
    )
    debounce()
    task = next(iter(debounce._leading_tasks)) if leading else debounce._task
    assert task is not None
    waiter = asyncio.create_task(closed.wait())
    try:
        done, _pending = await asyncio.wait(
            (task, waiter), timeout=1.0, return_when=asyncio.FIRST_COMPLETED,
        )
        if task.done():
            task.result()
        assert waiter in done, "callback close did not return within its bound"
        assert not task.cancelling()
        assert task in debounce._leading_tasks | debounce._trailing_tasks
        with pytest.raises(RuntimeError, match="closed"):
            debounce()
        release_callback.set()
        await task
        await asyncio.sleep(0)
        assert not debounce._leading_tasks | debounce._trailing_tasks
        assert not debounce.pending
    finally:
        release_callback.set()
        waiter.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)
        await debounce.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("leading", [True, False], ids=["leading", "trailing"])
async def test_external_close_keeps_self_closed_callback_until_cancelled_drain_finishes(
    leading: bool, _guard_native_drain_against_self: None,
) -> None:
    """External shutdown drains a self-closer and preserves caller cancellation."""
    closed = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def callback() -> None:
        await debounce.aclose()
        closed.set()
        try:
            await asyncio.Future()
        finally:
            cleanup_started.set()
            while not release_cleanup.is_set():
                try:
                    await release_cleanup.wait()
                except asyncio.CancelledError:
                    continue
            cleanup_finished.set()

    debounce = AsyncDebounceV2(
        callback, wait=0.0, leading=leading, trailing=not leading,
    )
    debounce()
    task = next(iter(debounce._leading_tasks)) if leading else debounce._task
    assert task is not None
    waiter = asyncio.create_task(closed.wait())
    closer: asyncio.Task[None] | None = None
    try:
        done, _pending = await asyncio.wait(
            (task, waiter), timeout=1.0, return_when=asyncio.FIRST_COMPLETED,
        )
        if task.done():
            task.result()
        assert waiter in done, "callback close did not return within its bound"
        closer = asyncio.create_task(debounce.aclose())
        await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
        closer.cancel()
        await asyncio.sleep(0)
        assert not closer.done()
        assert task in debounce._leading_tasks | debounce._trailing_tasks
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await closer
        assert cleanup_finished.is_set()
        assert task.done() and task.cancelled()
        assert not debounce._leading_tasks | debounce._trailing_tasks
    finally:
        release_cleanup.set()
        waiter.cancel()
        cleanup_tasks = (task, waiter) if closer is None else (task, waiter, closer)
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        await debounce.aclose()


@pytest.fixture
def _guard_native_drain_against_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check actual transfer edges before an invalid cycle reaches asyncio."""
    real_drain = cancel_and_drain_tasks
    waiting: dict[asyncio.Task[Any], tuple[asyncio.Task[Any], ...]] = {}

    def reaches(
        task: asyncio.Task[Any], target: asyncio.Task[Any], seen: set[asyncio.Task[Any]],
    ) -> bool:
        if task is target:
            return True
        if task in seen:
            return False
        seen.add(task)
        return any(reaches(child, target, seen) for child in waiting.get(task, ()))

    async def guarded_drain(
        tasks: Iterable[asyncio.Task[Any]],
        *,
        registry: MutableSet[asyncio.Task[Any]] | None = None,
    ) -> None:
        caller = asyncio.current_task()
        assert caller is not None
        snapshot = tuple(tasks)
        assert not any(reaches(task, caller, set()) for task in snapshot), (
            "aclose introduced a cycle in the actual native drain transfers"
        )
        waiting[caller] = snapshot
        try:
            await real_drain(snapshot, registry=registry)
        finally:
            waiting.pop(caller)

    monkeypatch.setattr(debounce_module, "cancel_and_drain_tasks", guarded_drain)


@pytest.mark.asyncio
async def test_cancelled_sibling_can_close_from_finally_without_joining_its_closer(
    _guard_native_drain_against_cycles: None,
) -> None:
    """A sibling's idempotent close cannot await the callback draining it."""
    started: asyncio.Queue[str] = asyncio.Queue()
    begin_close = asyncio.Event()
    first_close_returned = asyncio.Event()
    sibling_close_returned = asyncio.Event()
    callbacks: dict[str, asyncio.Task[Any]] = {}

    async def callback(label: str) -> None:
        current = asyncio.current_task()
        assert current is not None
        callbacks[label] = current
        started.put_nowait(label)
        if label == "closer":
            await begin_close.wait()
            await debounce.aclose()
            first_close_returned.set()
        else:
            try:
                await asyncio.Future()
            finally:
                await debounce.aclose()
                sibling_close_returned.set()

    debounce = AsyncDebounceV2(callback, wait=0.0, leading=True, trailing=False)
    debounce("closer")
    debounce("sibling")
    try:
        assert await asyncio.wait_for(started.get(), timeout=1.0) == "closer"
        assert await asyncio.wait_for(started.get(), timeout=1.0) == "sibling"
        begin_close.set()
        await asyncio.wait_for(first_close_returned.wait(), timeout=1.0)
        with pytest.raises(asyncio.CancelledError):
            await callbacks["sibling"]
        assert sibling_close_returned.is_set()
        await callbacks["closer"]
        await asyncio.sleep(0)
        assert not debounce._leading_tasks | debounce._trailing_tasks
        with pytest.raises(RuntimeError, match="closed"):
            debounce("late")
    finally:
        begin_close.set()
        for task in callbacks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*callbacks.values(), return_exceptions=True)
        await debounce.aclose()
