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

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


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
        # Re-entrant lock guards the check-and-record sequence so concurrent
        # charges in one window cannot collectively race past the cap (#3).
        # Re-entrant because try_charge() calls window_spend()/record() which
        # also acquire the lock.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def cap_configured(self) -> bool:
        """True when a positive spend cap is configured.

        A non-positive ``limit_usd`` (<= 0) means "unlimited" — there is no cap
        to enforce, so fail-closed semantics (refusing unknown costs) do not
        apply.  A positive limit means a cap exists and unknown costs must be
        refused (fail closed).
        """
        return self._limit_usd > 0.0

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
            cost_usd:   Amount spent in USD.  Must be a finite, non-negative
                        value; negative or non-finite values are programming
                        errors (or abuse attempts) and raise ``ValueError``
                        fail-closed rather than silently poisoning the window
                        sum or deflating the cap.
            kind:       Resource kind (e.g. ``"token"``, ``"infra"``).
            at:         Override the record timestamp (useful in tests).
                        Defaults to the injected clock's current value.
            model:      Optional model identifier (informational, not used in
                        rolling-window math).
            project_id: Optional project scope (informational).

        Raises:
            ValueError: If ``cost_usd`` is negative or non-finite (NaN / inf).
        """
        if not isinstance(cost_usd, (int, float)):
            return
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError(
                f"SpendLimiter.record(): cost_usd must be a finite non-negative value, "
                f"got {cost_usd!r}. Negative or non-finite costs are not permitted."
            )
        ts = at if at is not None else self._clock()
        with self._lock:
            self._records.append((ts, cost_usd))

    def try_charge(
        self,
        cost_usd: float | None,
        *,
        kind: str,
        at: float | None = None,
        model: str | None = None,
        project_id: str | None = None,
        **extra: Any,
    ) -> bool:
        """Atomically check the cap and, if the charge fits, record it.

        This is the enforcement entry point that closes the bypasses:

        * #1 (inert limiter): every accepted charge is RECORDED against the
          window in the same critical section as the check, so the rolling
          spend actually grows and the cap can trip.
        * #3 (concurrent overshoot): the check-and-record runs under a lock, so
          two concurrent charges can never both observe the same headroom and
          both commit — their combined spend can never exceed the cap.
        * #4 (silent fail-open): when ``cost_usd`` is ``None`` (unknown cost)
          and a cap is configured, the charge is REFUSED — no record is made
          and the caller must defer.  Only when no cap is configured
          (``cap_configured`` is False) is an unknown cost allowed through.

        Args:
            cost_usd:   Amount to charge in USD, or ``None`` if the cost could
                        not be determined.
            kind:       Resource kind (e.g. ``"token"``, ``"infra"``).
            at:         Override the record timestamp (useful in tests).
            model:      Optional model identifier (informational).
            project_id: Optional project scope (informational).

        Returns:
            True if the charge was accepted and recorded; False if it was
            refused (cap would be exceeded, or unknown cost under a cap).
        """
        with self._lock:
            if cost_usd is None:
                # Unknown cost: fail CLOSED when a cap is configured.
                if self.cap_configured:
                    logger.warning(
                        "SpendLimiter: refusing charge of UNKNOWN cost under a "
                        "configured cap (limit=%.6f USD) — failing closed.",
                        self._limit_usd,
                    )
                    return False
                # No cap -> nothing to enforce; allow (and do not record an
                # unknown amount).
                return True
            if self.would_exceed(cost_usd, now=at):
                return False
            self.record(
                cost_usd,
                kind=kind,
                at=at,
                model=model,
                project_id=project_id,
                **extra,
            )
            return True

    def snapshot(self) -> list[tuple[float, float]]:
        """Return a serializable copy of the in-window records.

        Persist this across a daemon restart and pass it back to ``restore`` so
        accumulated spend SURVIVES the restart (#2) — otherwise a restart resets
        the window to zero and the cap can be evaded by restarting.

        Returns:
            A list of ``(timestamp, cost_usd)`` tuples.
        """
        with self._lock:
            return list(self._records)

    def restore(self, records: list[tuple[float, float]] | None) -> None:
        """Reload previously-snapshotted records into this limiter.

        Records outside the current window are pruned lazily on the next
        ``window_spend()`` call, so restoring stale records after a long
        downtime is safe.

        Args:
            records: A list of ``(timestamp, cost_usd)`` tuples produced by
                     ``snapshot`` (or an equivalent persisted form).  ``None``
                     or empty is a no-op.
        """
        if not records:
            return
        with self._lock:
            valid = []
            for ts, c in records:
                if not isinstance(ts, (int, float)) or not isinstance(c, (int, float)):
                    continue
                ts_f, c_f = float(ts), float(c)
                if not math.isfinite(c_f) or c_f < 0:
                    logger.warning(
                        "SpendLimiter.restore: dropping record with invalid cost %r (ts=%r) — "
                        "negative or non-finite costs are not permitted.",
                        c_f,
                        ts_f,
                    )
                    continue
                if not math.isfinite(ts_f):
                    logger.warning(
                        "SpendLimiter.restore: dropping record with non-finite timestamp %r — skipping.",
                        ts_f,
                    )
                    continue
                valid.append((ts_f, c_f))
            self._records.extend(valid)

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
        with self._lock:
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
        # Fail CLOSED on a non-finite cost (#71). NaN compares False against
        # every limit (``nan > x`` is False), so a naive ``spent + projected >
        # limit`` check would wave a NaN/garbage cost straight through —
        # effectively unlimited spend. A non-finite projected cost cannot be
        # proven to fit the cap, so treat it as exceeding (defer/refuse).
        if not isinstance(projected_usd, (int, float)):
            return True
        if not math.isfinite(projected_usd):
            logger.warning(
                "SpendLimiter: non-finite projected cost (%r) treated as "
                "OVER-LIMIT — failing closed.",
                projected_usd,
            )
            return True
        spent = self.window_spend(now=now)
        return spent + projected_usd > self._limit_usd
