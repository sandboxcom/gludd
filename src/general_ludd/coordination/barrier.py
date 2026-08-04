"""Barrier and Wait-Group async coordination primitives.

:class:`Barrier` is a synchronization primitive that blocks N coroutines until
all N have called ``wait()``, then unblocks all of them simultaneously. It
supports abort (unblock with :class:`BarrierAborted`), deadline-driven timeouts,
broken-state tracking, and reset for reuse across phases.

:class:`WaitGroup` provides a manual count-down pattern: callers ``add(N)`` work
items, workers call ``done()`` as they finish, and ``wait()`` blocks until the
counter reaches zero.
"""

from __future__ import annotations

import asyncio
from typing import Any


class BarrierAborted(Exception):
    """Raised on waiters when :meth:`Barrier.abort` is called."""


class BarrierTimeout(asyncio.TimeoutError):
    """Raised when a wait exceeds the barrier's timeout."""


class BarrierBroken(Exception):
    """Raised when a waiter attempts to wait on a broken barrier."""


class Barrier:
    """An async barrier that blocks N waiters until all have arrived.

    After N waiters call ``wait()``, all N are unblocked simultaneously
    and the barrier resets for the next phase. If a waiter times out or
    ``abort()`` is called, the barrier enters a *broken* state — subsequent
    ``wait()`` calls raise :class:`BarrierBroken` until ``reset()`` is called.

    Supports both ``async with barrier:`` (context manager) and explicit
    ``await barrier.wait()``.
    """

    def __init__(self, parties: int, *, default_timeout: float | None = None) -> None:
        if parties < 0:
            raise ValueError("parties must be >= 0")
        self._parties: int = parties
        self._default_timeout: float | None = default_timeout
        self._waiters: int = 0
        self._broken: bool = False
        self._exception: BaseException | None = None
        self._event: asyncio.Event = asyncio.Event()
        self._cond: asyncio.Condition = asyncio.Condition()
        self._generation: int = 0
        if parties == 0:
            self._event.set()

    @property
    def parties(self) -> int:
        return self._parties

    @property
    def waiters(self) -> int:
        return self._waiters

    @property
    def broken(self) -> bool:
        return self._broken

    async def wait(self, *, timeout: float | None = None) -> None:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if self._broken:
            raise BarrierBroken("Barrier is broken and cannot be waited on") from self._exception

        if self._parties == 0:
            return

        if self._event.is_set():
            return

        self._waiters += 1
        generation = self._generation

        try:
            if self._waiters >= self._parties:
                self._release_all()
                return

            if effective_timeout is not None:
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=effective_timeout)
                except TimeoutError:
                    self._set_broken(BarrierTimeout(f"Barrier wait timed out after {effective_timeout}s"))
                    raise

                if self._generation == generation:
                    return
            else:
                await self._event.wait()
                if self._generation == generation:
                    return

        except asyncio.CancelledError:
            if not self._broken:
                self._set_broken(asyncio.CancelledError("Barrier waiter cancelled"))
            raise

    def abort(self) -> None:
        if self._waiters > 0 or not self._broken:
            self._set_broken(BarrierAborted("Barrier aborted"))
            if self._parties == 0:
                self._broken = True

    def reset(self) -> None:
        if self._waiters > 0:
            raise RuntimeError("Cannot reset barrier with active waiters")
        self._broken = False
        self._exception = None
        self._event.clear()
        self._waiters = 0
        self._generation += 1
        if self._parties == 0:
            self._event.set()

    def _release_all(self) -> None:
        self._event.set()

    def _set_broken(self, exc: BaseException) -> None:
        if self._broken:
            return
        self._broken = True
        self._exception = exc
        self._event.set()

    async def __aenter__(self) -> Barrier:
        await self.wait()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def __repr__(self) -> str:
        return (
            f"Barrier(parties={self._parties}, waiters={self._waiters}, "
            f"broken={self._broken}, default_timeout={self._default_timeout})"
        )


class WaitGroup:
    """An async wait-group with manual count-down.

    .. code-block:: python

        wg = WaitGroup()
        wg.add(3)
        for _ in range(3):
            asyncio.create_task(worker(wg))
        await wg.wait()  # blocks until all 3 workers call done()

    Multiple waiters can call ``wait()`` concurrently; they all unblock
    when the counter reaches zero.
    """

    def __init__(self, *, default_timeout: float | None = None) -> None:
        self._counter: int = 0
        self._default_timeout: float | None = default_timeout
        self._event: asyncio.Event = asyncio.Event()
        self._event.set()  # starts unblocked (zero items)

    @property
    def counter(self) -> int:
        return self._counter

    def add(self, delta: int = 1) -> None:
        if delta < 0:
            raise ValueError("delta must be >= 0")
        if delta == 0:
            return
        self._counter += delta
        if self._counter > 0 and self._event.is_set():
            self._event.clear()

    def done(self, n: int = 1) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self._counter = max(0, self._counter - n)
        if self._counter == 0:
            self._event.set()

    async def wait(self, *, timeout: float | None = None) -> None:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if self._counter == 0:
            return
        if effective_timeout is not None:
            await asyncio.wait_for(self._event.wait(), timeout=effective_timeout)
        else:
            await self._event.wait()

    def __repr__(self) -> str:
        return f"WaitGroup(counter={self._counter}, default_timeout={self._default_timeout})"
