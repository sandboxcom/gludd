"""Debounce v2 — configurable edge (leading, trailing, both) with max-wait.

``DebounceV2`` is a combined debounce/throttle primitive:
- *trailing* (default): fire after ``wait`` seconds of inactivity.
- *leading*: fire immediately on the first call; suppress subsequent calls
  within the window.
- *both*: fire on the leading edge AND hold the most recent args for a
  trailing-edge fire after the window.
- *max_wait*: guarantee at least one firing every *max_wait* seconds
  during a sustained burst (trailing and both modes).

``AsyncDebounceV2`` mirrors the API for async callables, using
``asyncio.create_task`` / ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from general_ludd.util.async_lifecycle import cancel_and_drain_tasks

F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Coroutine[Any, Any, Any]])


class DebounceV2:
    """Configurable-edge debounce / throttle."""

    def __init__(
        self,
        fn: F,
        wait: float,
        *,
        leading: bool = False,
        trailing: bool = True,
        max_wait: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize a synchronous debounce state machine."""
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("wait must be finite and >= 0")
        if max_wait is not None and (not math.isfinite(max_wait) or max_wait <= 0):
            raise ValueError("max_wait must be finite and > 0")
        if not leading and not trailing:
            raise ValueError("at least one of leading/trailing must be True")

        self._fn: Callable[..., Any] = fn
        self._wait = float(wait)
        self._leading = leading
        self._trailing = trailing
        self._max_wait = float(max_wait) if max_wait is not None else None
        self._clock: Any = clock

        self._last_leading_epoch: float = -float("inf")
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._timer: float | None = None
        self._first_call_at: float | None = None

    @property
    def pending(self) -> bool:
        """Return whether a trailing invocation is queued."""
        return self._pending_args is not None

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Record a call and invoke or queue it according to edge settings."""
        now = self._clock()

        leading_fired = False
        if self._leading and now - self._last_leading_epoch >= self._wait:
            self._last_leading_epoch = now
            self._fn(*args, **kwargs)
            leading_fired = True

        if not self._trailing:
            return

        if leading_fired:
            self._pending_args = None
            if self._first_call_at is None:
                self._first_call_at = now
            return

        self._pending_args = (args, kwargs)

        if self._first_call_at is None:
            self._first_call_at = now

        due_at = now + self._wait
        if self._max_wait is not None and self._first_call_at is not None:
            due_at = min(due_at, self._first_call_at + self._max_wait)

        if self._timer is None or due_at < self._timer:
            self._timer = due_at

    def _tick(self) -> None:
        if self._pending_args is None or self._timer is None:
            return
        now = self._clock()
        if now < self._timer:
            return
        args, kwargs = self._pending_args
        self._pending_args = None
        self._timer = None
        self._first_call_at = None
        self._fn(*args, **kwargs)

    def drive(self, until: float) -> None:
        """Advance an injected simulated clock and process one due callback."""
        if callable(getattr(self._clock, "advance", None)):
            self._clock.advance(until - self._clock())
            self._tick()

    def cancel(self) -> None:
        """Discard a queued trailing invocation."""
        self._pending_args = None
        self._timer = None
        self._first_call_at = None

    def flush(self) -> None:
        """Immediately invoke and clear a queued trailing call, if present."""
        if self._pending_args is not None:
            args, kwargs = self._pending_args
            self.cancel()
            self._fn(*args, **kwargs)

    def reset(self) -> None:
        """Clear pending work and restore leading-edge admission state."""
        self.cancel()
        self._last_leading_epoch = -float("inf")


class AsyncDebounceV2:
    """Async debounce with configurable edges.

    The caller must run inside an active event loop.  *fn* is an async
    callable that is invoked after *wait* seconds of inactivity
    (trailing), immediately (leading), or both.
    """

    def __init__(
        self,
        fn: AF,
        wait: float,
        *,
        leading: bool = False,
        trailing: bool = True,
        max_wait: float | None = None,
    ) -> None:
        """Initialize an event-loop-bound async debounce state machine."""
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("wait must be finite and >= 0")
        if max_wait is not None and (not math.isfinite(max_wait) or max_wait <= 0):
            raise ValueError("max_wait must be finite and > 0")
        if not leading and not trailing:
            raise ValueError("at least one of leading/trailing must be True")

        self._fn: Callable[..., Coroutine[Any, Any, Any]] = fn
        self._wait = float(wait)
        self._leading = leading
        self._trailing = trailing
        self._max_wait = float(max_wait) if max_wait is not None else None

        self._last_leading_epoch: float = -float("inf")
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._task: asyncio.Task[Any] | None = None
        self._leading_tasks: set[asyncio.Task[Any]] = set()
        self._first_call_at: float | None = None

    @property
    def pending(self) -> bool:
        """Return whether a trailing timer task is active."""
        t = self._task
        return t is not None and not t.done()

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Schedule leading or trailing async work on the running loop."""
        now = asyncio.get_running_loop().time()

        leading_fired = False
        if self._leading and now - self._last_leading_epoch >= self._wait:
            self._last_leading_epoch = now
            leading_task = asyncio.create_task(self._fn(*args, **kwargs))
            self._leading_tasks.add(leading_task)
            leading_task.add_done_callback(self._leading_tasks.discard)
            leading_fired = True
            if not self._trailing:
                return

        if not self._trailing:
            return

        if leading_fired:
            if self._task is not None and not self._task.done():
                self._task.cancel()
            self._task = None
            self._pending_args = None
            self._first_call_at = now
            return

        self._pending_args = (args, kwargs)

        if self._first_call_at is None:
            self._first_call_at = now

        delay = self._wait
        if self._max_wait is not None and self._first_call_at is not None:
            max_due = self._first_call_at + self._max_wait - now
            delay = min(delay, max(max_due, 0.0))

        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run_after(args, kwargs, delay))

    async def _run_after(self, args: tuple[Any, ...], kwargs: dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        if self._pending_args == (args, kwargs):
            self._pending_args = None
            self._first_call_at = None
            await self._fn(*args, **kwargs)

    def cancel(self) -> None:
        """Request cancellation of delayed trailing work."""
        if self._task is not None:
            self._task.cancel()
        self._pending_args = None
        self._first_call_at = None

    async def aclose(self) -> None:
        """Cancel and await all leading and trailing tasks owned by this debounce."""
        await cancel_and_drain_tasks(
            self._leading_tasks,
            registry=self._leading_tasks,
        )
        if self._task is not None:
            await cancel_and_drain_tasks((self._task,))
        self._task = None
        self._pending_args = None
        self._first_call_at = None

    def reset(self) -> None:
        """Cancel delayed work and restore leading-edge admission state."""
        self.cancel()
        self._last_leading_epoch = -float("inf")
