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
    """An async cyclic barrier with synchronous abort and reset controls.

    Each generation has distinct filling and draining phases. A task cannot
    enter the next generation until every task from the released generation
    has left, matching the lifecycle of :class:`asyncio.Barrier` while
    preserving this project's synchronous ``abort()`` and ``reset()`` API.
    """

    def __init__(self, parties: int, *, default_timeout: float | None = None) -> None:
        if parties < 0:
            raise ValueError("parties must be >= 0")
        self._parties = parties
        self._default_timeout = default_timeout
        self._waiters = 0
        self._broken = False
        self._exception: BaseException | None = None
        self._event = asyncio.Event()
        self._drained = asyncio.Event()
        self._drained.set()
        self._draining = False
        self._generation = 0
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
        try:
            if effective_timeout is None:
                await self._wait_for_generation()
            else:
                async with asyncio.timeout(effective_timeout):
                    await self._wait_for_generation()
        except TimeoutError as exc:
            timeout_error = BarrierTimeout(
                f"Barrier wait timed out after {effective_timeout}s"
            )
            self._set_broken(timeout_error)
            raise timeout_error from exc
        except asyncio.CancelledError:
            self._set_broken(asyncio.CancelledError("Barrier waiter cancelled"))
            raise

    async def _wait_for_generation(self) -> None:
        while self._draining and not self._broken:
            await self._drained.wait()

        self._raise_if_broken()
        if self._parties == 0:
            return

        event = self._event
        self._waiters += 1
        try:
            if self._waiters == self._parties:
                self._draining = True
                self._drained.clear()
                self._release_all()
            await event.wait()
            self._raise_if_broken()
        finally:
            self._waiters -= 1
            if self._draining and self._waiters == 0:
                if not self._broken:
                    self._generation += 1
                    self._event = asyncio.Event()
                self._draining = False
                self._drained.set()

    def _raise_if_broken(self) -> None:
        if not self._broken:
            return
        if isinstance(self._exception, BarrierAborted):
            raise BarrierAborted(str(self._exception)) from self._exception
        raise BarrierBroken("Barrier is broken and cannot be waited on") from self._exception

    def abort(self) -> None:
        self._set_broken(BarrierAborted("Barrier aborted"))

    def reset(self) -> None:
        if self._waiters > 0:
            raise RuntimeError("Cannot reset barrier with active waiters")
        self._broken = False
        self._exception = None
        self._event = asyncio.Event()
        self._drained.set()
        self._draining = False
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
        self._drained.set()

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
        if effective_timeout is None:
            await self._event.wait()
            return
        try:
            async with asyncio.timeout(effective_timeout):
                await self._event.wait()
        except TimeoutError as exc:
            raise BarrierTimeout(
                f"WaitGroup wait timed out after {effective_timeout}s"
            ) from exc

    def __repr__(self) -> str:
        return f"WaitGroup(counter={self._counter}, default_timeout={self._default_timeout})"
