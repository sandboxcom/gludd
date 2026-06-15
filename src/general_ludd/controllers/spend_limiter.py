"""Rolling-window soft spend limiter for the daemon dispatch path.

SpendLimiter enforces a SOFT cap — it is checked BEFORE a model/infra call
so a dispatch whose projected cost fits within the remaining budget proceeds;
once remaining <= 0, any positive projected cost defers the dispatch.  It is
never used to hard-abort an in-flight call.

Design notes
------------
* The ``clock`` parameter is a zero-argument callable that returns a
  monotonic timestamp (float).  Inject a fake clock in tests; production uses
  ``time.monotonic`` by default.
* Spend records are kept in memory as a list of ``(timestamp, cost_usd)``
  tuples.  Old records (older than ``window_seconds``) are pruned lazily on
  every ``window_spend()`` call.

# TODO(integration): wire SpendLimiter.would_exceed() into the dispatch path
# before model calls so that every dispatch checks the rolling budget prior to
# executing a model/infra action.  The check should look like:
#
#   projected = token_cost_usd(model, est_in_tokens, est_out_tokens)
#   if spend_limiter.would_exceed(projected):
#       logger.warning("Spend limiter: deferring dispatch, window=%s remaining=%s",
#                      spend_limiter.window_spend(), spend_limiter.remaining())
#       return  # defer/skip
#   # ... execute model call ...
#   spend_limiter.record(actual_cost, kind="token", model=model)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


class SpendLimiter:
    """Rolling-window soft spend cap.

    Args:
        limit_usd:       Maximum spend allowed within the rolling window (USD).
        window_seconds:  Width of the rolling window in seconds.
        clock:           Callable returning the current monotonic time (float).
                         Defaults to ``time.monotonic``.  Inject a fake clock
                         in tests for deterministic behaviour.
    """

    def __init__(
        self,
        limit_usd: float,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._limit_usd = limit_usd
        self._window_seconds = window_seconds
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        # Each record: (timestamp_float, cost_usd_float)
        self._records: list[tuple[float, float]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        cost_usd: float,
        *,
        kind: str,
        at: float | None = None,
        model: str | None = None,
        project_id: str | None = None,
        **_extra: Any,
    ) -> None:
        """Record a spend event.

        Args:
            cost_usd:   Amount spent in USD.
            kind:       Resource kind (e.g. ``"token"``, ``"infra"``).
            at:         Override the record timestamp (useful in tests).
                        Defaults to the injected clock's current value.
            model:      Optional model identifier (informational, not used in
                        rolling-window math).
            project_id: Optional project scope (informational).
        """
        ts = at if at is not None else self._clock()
        self._records.append((ts, cost_usd))

    def window_spend(self, now: float | None = None) -> float:
        """Sum of all spend within the rolling window ending at ``now``.

        Pruning: records older than ``(now - window_seconds)`` are dropped
        in-place so the list stays bounded.

        Args:
            now: Override the current time (uses clock by default).

        Returns:
            Total USD spent within the window.
        """
        if now is None:
            now = self._clock()
        cutoff = now - self._window_seconds
        # Prune in-place: keep records where ts >= cutoff
        self._records = [(ts, c) for ts, c in self._records if ts >= cutoff]
        return sum(c for _, c in self._records)

    def remaining(self, now: float | None = None) -> float:
        """Remaining budget within the current window.

        Never returns a negative value.

        Args:
            now: Override the current time (uses clock by default).

        Returns:
            USD remaining until the limit is reached (0.0 if already exceeded).
        """
        spent = self.window_spend(now=now)
        return max(0.0, self._limit_usd - spent)

    def would_exceed(self, projected_usd: float, now: float | None = None) -> bool:
        """Return True if dispatching a call with ``projected_usd`` cost would
        push the window spend above the limit.

        The "roughly met, not exceeded" semantics:
          * ``window_spend + projected_usd > limit``  →  True (defer)
          * ``window_spend + projected_usd <= limit`` →  False (allow)

        A projected cost of 0.0 never triggers deferral even when the window
        is exactly at the limit.

        Args:
            projected_usd: Estimated cost of the pending dispatch (USD).
            now:           Override the current time (uses clock by default).

        Returns:
            True when the dispatch should be deferred/skipped.
        """
        spent = self.window_spend(now=now)
        return spent + projected_usd > self._limit_usd
