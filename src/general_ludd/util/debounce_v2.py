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
import logging
import math
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from general_ludd.util.async_lifecycle import cancel_and_drain_tasks

F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Coroutine[Any, Any, Any]])

logger = logging.getLogger(__name__)


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
        self._clock = clock
        self._last_clock_at = -float("inf")

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
        now = self._now()

        leading_fired = False
        if self._leading and now - self._last_leading_epoch >= self._wait:
            self._last_leading_epoch = now
            if self._trailing:
                # Establish the new burst before invoking user code. A
                # re-entrant callback may queue trailing work that the outer
                # leading call must not erase afterward.
                self._pending_args = None
                self._timer = None
                self._first_call_at = now
            self._fn(*args, **kwargs)
            leading_fired = True

        if not self._trailing:
            return

        if leading_fired:
            return

        self._pending_args = (args, kwargs)

        if self._first_call_at is None:
            self._first_call_at = now

        due_at = now + self._wait
        if self._max_wait is not None and self._first_call_at is not None:
            due_at = min(due_at, self._first_call_at + self._max_wait)

        # Debounce is measured from the most recent call, while ``max_wait``
        # provides the only upper bound for a sustained burst.  Keeping an
        # earlier timer here turns debounce into a fixed-window throttle and
        # can publish work before the caller's quiet period has elapsed.
        self._timer = due_at

    def _tick(self) -> None:
        if self._pending_args is None or self._timer is None:
            return
        now = self._now()
        if now < self._timer:
            return
        args, kwargs = self._pending_args
        self._pending_args = None
        self._timer = None
        self._first_call_at = None
        self._fn(*args, **kwargs)

    def drive(self, until: float) -> None:
        """Advance an injected simulated clock and process one due callback."""
        target = float(until)
        if not math.isfinite(target):
            raise ValueError("drive deadline must be finite")
        advance = getattr(self._clock, "advance", None)
        if callable(advance):
            current = float(self._clock())
            if target > current:
                advance(target - current)
            self._tick()

    def _now(self) -> float:
        """Return a finite, nondecreasing observation of the injected clock."""
        observed = float(self._clock())
        if not math.isfinite(observed):
            raise ValueError("clock must return finite values")
        if observed < self._last_clock_at:
            return self._last_clock_at
        self._last_clock_at = observed
        return observed

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
        self._trailing_tasks: set[asyncio.Task[Any]] = set()
        self._leading_tasks: set[asyncio.Task[Any]] = set()
        self._first_call_at: float | None = None
        self._last_clock_at = -float("inf")
        self._closed = False

    @property
    def pending(self) -> bool:
        """Return whether a trailing timer task is active."""
        t = self._task
        return t is not None and not t.done()

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Schedule leading or trailing async work on the running loop."""
        if self._closed:
            raise RuntimeError("AsyncDebounceV2 is closed")
        now = self._now()

        leading_fired = False
        if self._leading and now - self._last_leading_epoch >= self._wait:
            self._last_leading_epoch = now
            if self._trailing:
                self._cancel_trailing_task()
                self._pending_args = None
                self._first_call_at = now
            leading_task = asyncio.create_task(self._fn(*args, **kwargs))
            self._leading_tasks.add(leading_task)
            leading_task.add_done_callback(self._on_leading_done)
            leading_fired = True
            if not self._trailing:
                return

        if not self._trailing:
            return

        if leading_fired:
            return

        self._pending_args = (args, kwargs)

        if self._first_call_at is None:
            self._first_call_at = now

        delay = self._wait
        if self._max_wait is not None and self._first_call_at is not None:
            max_due = self._first_call_at + self._max_wait - now
            delay = min(delay, max(max_due, 0.0))

        self._cancel_trailing_task()
        task = asyncio.create_task(self._run_after(args, kwargs, delay))
        self._task = task
        self._trailing_tasks.add(task)
        task.add_done_callback(self._on_trailing_done)

    async def _run_after(self, args: tuple[Any, ...], kwargs: dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        if self._task is asyncio.current_task():
            self._pending_args = None
            self._first_call_at = None
            await self._fn(*args, **kwargs)

    def _now(self) -> float:
        """Return a finite, nondecreasing event-loop clock observation."""
        observed = float(asyncio.get_running_loop().time())
        if not math.isfinite(observed):
            raise ValueError("event-loop clock must return finite values")
        if observed < self._last_clock_at:
            return self._last_clock_at
        self._last_clock_at = observed
        return observed

    def _cancel_trailing_task(self) -> None:
        """Cancel the current generation without losing drain ownership."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    def _observe_task_failure(self, completed: asyncio.Future[Any], *, edge: str) -> asyncio.Task[Any]:
        """Retrieve and log a background callback failure exactly once."""
        task = cast(asyncio.Task[Any], completed)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "AsyncDebounceV2 %s callback failed: %s",
                    edge,
                    error,
                    exc_info=(type(error), error, error.__traceback__),
                )
        return task

    def _on_leading_done(self, completed: asyncio.Future[Any]) -> None:
        """Forget one leading task after observing its terminal failure."""
        task = self._observe_task_failure(completed, edge="leading")
        self._leading_tasks.discard(task)

    def _on_trailing_done(self, completed: asyncio.Future[Any]) -> None:
        """Forget one timer generation after observing its terminal failure."""
        task = self._observe_task_failure(completed, edge="trailing")
        self._trailing_tasks.discard(task)
        if self._task is task:
            self._task = None

    def cancel(self) -> None:
        """Request cancellation of delayed trailing work."""
        self._cancel_trailing_task()
        self._pending_args = None
        self._first_call_at = None

    async def aclose(self) -> None:
        """Stop intake and drain owned tasks other than the calling callback."""
        current_task = asyncio.current_task()
        if self._closed and current_task in self._leading_tasks | self._trailing_tasks:
            # A cancelled sibling can close from finally while another owned
            # callback drains it. Joining that close would create a task cycle.
            return
        self._closed = True
        owned_tasks = tuple(
            task for task in self._leading_tasks | self._trailing_tasks
            if task is not current_task
        )
        if self._task is current_task:
            self._task = None
        else:
            self._cancel_trailing_task()
        self._pending_args = None
        self._first_call_at = None
        # A self-closing callback remains registered until its done callback;
        # another external close must still be able to cancel and drain it.
        await cancel_and_drain_tasks(owned_tasks)

    def reset(self) -> None:
        """Cancel delayed work and restore leading-edge admission state."""
        self.cancel()
        self._last_leading_epoch = -float("inf")
