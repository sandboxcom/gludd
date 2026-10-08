"""Production debounce and throttle compatibility primitives.

The public classes in this module retain the original small API while sharing
the maintained edge, clock, cancellation, and max-wait state machine from
``debounce_v2``.  Synchronous callers may inject a monotonic-compatible clock
for deterministic tests; asynchronous callers use the running event loop.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from general_ludd.util.debounce_v2 import AsyncDebounceV2, DebounceV2

F = TypeVar("F", bound=Callable[..., Any])
AF = TypeVar("AF", bound=Callable[..., Coroutine[Any, Any, Any]])


class Debouncer(DebounceV2):
    """Debounce a callable on either the trailing or leading edge."""

    def __init__(
        self,
        fn: F,
        wait: float,
        *,
        leading: bool = False,
        max_wait: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize a leading-only or trailing-only debouncer."""
        super().__init__(
            fn,
            wait,
            leading=leading,
            trailing=not leading,
            max_wait=max_wait,
            clock=clock,
        )


class Throttle:
    """Rate-limit a callable with configurable leading and trailing edges."""

    def __init__(
        self,
        fn: F,
        wait: float,
        *,
        leading: bool = True,
        trailing: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize an independently clocked throttle state machine."""
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("wait must be finite and >= 0")
        if not leading and not trailing:
            raise ValueError("at least one of leading/trailing must be True")
        self._fn: Callable[..., Any] = fn
        self._wait = float(wait)
        self._leading = leading
        self._trailing = trailing
        self._clock = clock
        self._last_fired: float | None = None
        self._pending_args: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self._timer: float | None = None

    @property
    def pending(self) -> bool:
        """Return whether a trailing invocation is queued."""
        return self._pending_args is not None

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Record a call and invoke or queue it according to edge settings."""
        now = self._clock()
        elapsed = float("inf") if self._last_fired is None else now - self._last_fired
        if self._leading and self._pending_args is None and elapsed >= self._wait:
            self._last_fired = now
            self._timer = None
            self._fn(*args, **kwargs)
            return

        if not self._trailing:
            return
        self._pending_args = (args, kwargs)
        if self._timer is None:
            if self._last_fired is None or now >= self._last_fired + self._wait:
                self._timer = now + self._wait
            else:
                self._timer = self._last_fired + self._wait

    def _tick(self) -> None:
        """Invoke a due trailing call using its logical deadline timestamp."""
        if self._pending_args is None or self._timer is None:
            return
        now = self._clock()
        if now < self._timer:
            return
        args, kwargs = self._pending_args
        fired_at = self._timer
        self._pending_args = None
        self._timer = None
        self._last_fired = fired_at
        self._fn(*args, **kwargs)

    def drive(self, until: float) -> None:
        """Advance an injected simulated clock and process one due callback."""
        advance = getattr(self._clock, "advance", None)
        if callable(advance):
            advance(until - self._clock())
            self._tick()

    def cancel(self) -> None:
        """Discard a queued trailing invocation without resetting rate state."""
        self._pending_args = None
        self._timer = None

    def reset(self) -> None:
        """Clear queued work and permit the next leading invocation."""
        self.cancel()
        self._last_fired = None


class AsyncDebouncer(AsyncDebounceV2):
    """Debounce an async callable on the trailing edge."""

    def __init__(self, fn: AF, wait: float) -> None:
        """Initialize a trailing async debouncer."""
        super().__init__(fn, wait, leading=False, trailing=True)


__all__ = ["AsyncDebouncer", "Debouncer", "Throttle"]
