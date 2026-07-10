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

Integration: would_exceed() is wired into the dispatch path via
``make_spend_guarded_executor`` (daemon_wiring) and consulted in
``EventLoop`` before every model/infra call.

Flush watermark (SPD-1)
------------------------
Internally every recorded charge is stamped with a monotonically increasing
``_seq`` counter.  This is purely an in-process bookkeeping detail — it is
NOT part of the public 3-tuple record shape returned by :meth:`snapshot` /
accepted by :meth:`restore` (which daemon.py persists/reloads verbatim).

The counter backs a periodic-flush watermark API consumed by a separate
EventLoop phase (not part of this module) that persists in-memory records to
``spend_records`` via ``SpendRepository.add()``:

* :meth:`unflushed_records` returns records recorded since the last flush.
* :meth:`mark_flushed` advances the watermark once those records are durably
  written.
* :meth:`restore` seeds the watermark PAST every ingested record so the
  first post-restart flush never re-INSERTs rows that were already persisted
  before the restart (the critical dedupe property).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from general_ludd.infra.pricing import token_cost_usd as _static_token_cost_usd

if TYPE_CHECKING:
    from general_ludd.pricing_intel.catalog import PricingCatalog

logger = logging.getLogger(__name__)

# Provider-candidate hints inferred from a model_id prefix. The catalog is
# queried with these first (most specific source), then any remaining
# registered provider slugs as a backstop.
_PREFIX_PROVIDER_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude-", ("anthropic", "openrouter")),
    ("gpt-", ("openai", "openrouter")),
)


class SpendLimiter:
    """Rolling-window soft spend cap.

    Args:
        limit_usd:       Maximum spend allowed within the rolling window (USD).
        window_seconds:  Width of the rolling window in seconds.
        clock:           Callable returning the current monotonic time (float).
                         Defaults to ``time.monotonic``.  Inject a fake clock
                         in tests for deterministic behaviour.
        catalog:         Optional :class:`~general_ludd.pricing_intel.PricingCatalog`
                         used as the PRIMARY source for
                         :meth:`token_cost_usd`.  When the catalog returns no
                         price for a model (or is omitted), the static table in
                         :mod:`general_ludd.infra.pricing` is used as a
                         fallback so cost projection never breaks due to a
                         missing live price.
    """

    def __init__(
        self,
        limit_usd: float,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
        catalog: PricingCatalog | None = None,
    ) -> None:
        self._limit_usd = limit_usd
        self._window_seconds = window_seconds
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic
        self._catalog = catalog
        # Each record: (seq_int, timestamp_float, cost_usd_float, project_id_or_None).
        # ``seq`` is an internal-only monotonic stamp (SPD-1 flush watermark) —
        # it is NEVER exposed via the public 3-tuple shape used by snapshot()/
        # restore()/daemon.py; see module docstring "Flush watermark (SPD-1)".
        self._records: list[tuple[int, float, float, str | None]] = []
        # Monotonic counter stamped on every recorded charge (record() calls,
        # including those ingested via restore()). Never decreases.
        self._seq: int = 0
        # Watermark: records with seq <= _last_flushed_seq are considered
        # already durably persisted and are excluded from unflushed_records().
        # restore() advances this past every ingested record so a post-restart
        # flush never re-inserts rows that were already in the DB.
        self._last_flushed_seq: int = 0
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

    # ------------------------------------------------------------------
    # Cost projection
    # ------------------------------------------------------------------

    def token_cost_usd(
        self,
        model: str,
        in_tokens: int,
        out_tokens: int,
        *,
        provider: str | None = None,
    ) -> float:
        """USD cost for a model call, PricingCatalog primary, static fallback.

        Resolution order:

        1. If a :class:`~general_ludd.pricing_intel.PricingCatalog` is
           configured, query it via ``model_price(provider, model_id)``.  The
           provider is taken from the explicit ``provider`` arg when given;
           otherwise it is inferred from the model_id prefix (e.g.
           ``claude-*`` -> ``anthropic``) and then backfilled by every
           registered provider slug.  The first non-``None`` ``ModelPrice``
           wins.
        2. On any miss, catalog error, or when no catalog is configured, fall
           back to :func:`general_ludd.infra.pricing.token_cost_usd` (the
           static ``PRICING`` table).  This guarantees cost projection never
           breaks because a live price is unavailable.

        Args:
            model:      Model identifier (e.g. ``"claude-3-5-sonnet-20241022"``).
            in_tokens:  Number of input/prompt tokens.
            out_tokens: Number of output/completion tokens.
            provider:   Optional explicit provider slug (e.g. ``"anthropic"``).
                        When omitted, provider inference is used.

        Returns:
            Total cost in USD as a float.
        """
        price = self._catalog_model_price(model, provider=provider)
        if price is not None:
            inp_per_1k, out_per_1k = price
            return inp_per_1k * (in_tokens / 1000.0) + out_per_1k * (out_tokens / 1000.0)
        return _static_token_cost_usd(model, in_tokens, out_tokens)

    def _catalog_model_price(
        self,
        model: str,
        *,
        provider: str | None = None,
    ) -> tuple[float, float] | None:
        """Return ``(input_usd_per_1k, output_usd_per_1k)`` from the catalog.

        Returns ``None`` when no catalog is configured, no provider has the
        model, or the catalog raises.  All exceptions are swallowed so the
        caller falls back to the static table — a missing live price must
        never break cost projection.
        """
        catalog = self._catalog
        if catalog is None:
            return None
        candidates = self._provider_candidates(model, provider=provider)
        for slug in candidates:
            try:
                price = catalog.model_price(slug, model)
            except Exception as exc:
                logger.warning(
                    "SpendLimiter: catalog.model_price(%s, %s) raised %s; "
                    "trying next provider / static fallback.",
                    slug,
                    model,
                    exc,
                )
                continue
            if price is not None:
                return (price.input_usd_per_1k, price.output_usd_per_1k)
        return None

    def _provider_candidates(
        self,
        model: str,
        *,
        provider: str | None,
    ) -> tuple[str, ...]:
        """Order the provider slugs to try for ``model``.

        Explicit ``provider`` wins outright.  Otherwise prefix hints are
        placed first (deduplicated), then any other registered slugs follow
        so every provider gets a chance before falling back to the static
        table.
        """
        if provider is not None:
            return (provider,)
        hints: list[str] = []
        model_lower = model.lower()
        for prefix, slugs in _PREFIX_PROVIDER_HINTS:
            if model_lower.startswith(prefix):
                for slug in slugs:
                    if slug not in hints:
                        hints.append(slug)
        # Backfill with every registered provider slug so unknown prefixes
        # and hint misses still consult the full catalog before fallback.
        try:
            registered = self._catalog.provider_slugs() if self._catalog else []
        except Exception:
            registered = []
        for slug in registered:
            if slug not in hints:
                hints.append(slug)
        return tuple(hints)

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
            self._seq += 1
            self._records.append((self._seq, ts, cost_usd, project_id))

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

    def snapshot(self) -> list[tuple[float, float, str | None]]:
        """Return a serializable copy of the in-window records.

        Persist this across a daemon restart and pass it back to ``restore`` so
        accumulated spend SURVIVES the restart (#2) — otherwise a restart resets
        the window to zero and the cap can be evaded by restarting.

        Returns:
            A list of ``(timestamp, cost_usd, project_id)`` tuples.
        """
        with self._lock:
            return [(ts, c, pid) for _seq, ts, c, pid in self._records]

    def restore(self, records: list[tuple[float, float]] | list[tuple[float, float, str | None]] | None) -> None:
        """Reload previously-snapshotted records into this limiter.

        Records outside the current window are pruned lazily on the next
        ``window_spend()`` call, so restoring stale records after a long
        downtime is safe.

        Accepts both 2-tuple ``(ts, cost)`` (old snapshot format) and
        3-tuple ``(ts, cost, project_id)`` (current format).  Two-tuples are
        upgraded to ``(ts, cost, None)`` on ingest.

        Invalid records are DROPPED (not silently accepted) to prevent
        cap-evasion attacks:

        * Negative or non-finite ``cost_usd`` values would deflate the
          rolling window total, effectively lifting the cap.  These are
          logged and dropped.
        * Future timestamps survive ``window_spend()`` pruning indefinitely
          (their cutoff never arrives), so they could ghost-inflate the
          window.  Future timestamps are clamped to ``now`` so they are
          treated as current-window spend.

        Args:
            records: A list of ``(timestamp, cost_usd)`` or
                     ``(timestamp, cost_usd, project_id)`` tuples produced by
                     ``snapshot`` (or an equivalent persisted form).  ``None``
                     or empty is a no-op.

        Security notes:
            * Negative or non-finite ``cost_usd`` values are silently dropped —
              they would deflate ``window_spend()`` and enable cap evasion.
            * Timestamps in the future (ts > now) are clamped to now — a
              far-future timestamp creates a record that never expires from the
              rolling window, allowing an attacker to peg window spend high
              indefinitely as a DoS against legitimate dispatches.

        Flush watermark (SPD-1):
            Every ingested record is stamped with a fresh ``_seq`` and the
            flush watermark (``_last_flushed_seq``) is advanced past it. These
            records were, by construction, already durably persisted (they
            came from a prior snapshot/DB read) — so the first post-restart
            flush must never re-INSERT them.  Without this, ``restore()``
            followed by a flush cycle would duplicate every restored row.
        """
        if not records:
            return
        now = self._clock()
        with self._lock:
            valid = []
            for rec in records:
                if len(rec) == 2:
                    ts, c = rec
                    pid: str | None = None
                elif len(rec) == 3:
                    ts, c, pid = rec
                else:
                    continue
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
                # Clamp future timestamps to now so a restored record with a
                # far-future ts cannot live in the window indefinitely.
                if ts_f > now:
                    logger.warning(
                        "SpendLimiter.restore: clamping future timestamp %r to now=%r.",
                        ts_f,
                        now,
                    )
                    ts_f = now
                if pid is not None and not isinstance(pid, str):
                    pid = None
                self._seq += 1
                valid.append((self._seq, ts_f, c_f, pid))
            self._records.extend(valid)
            if valid:
                # seq is assigned in strictly increasing order above, so the
                # last-appended record carries the highest seq of this batch.
                self._last_flushed_seq = max(self._last_flushed_seq, valid[-1][0])

    # ------------------------------------------------------------------
    # Flush watermark (SPD-1) — consumed by a separate EventLoop phase that
    # periodically persists in-memory records to the ``spend_records`` table
    # via SpendRepository.add(). This module does no I/O itself.
    # ------------------------------------------------------------------

    def unflushed_records(self) -> list[tuple[int, float, float, str | None]]:
        """Return records recorded since the last :meth:`mark_flushed` call.

        Read-only: does not prune, mutate, or advance the watermark. Safe to
        call repeatedly / speculatively before a flush actually commits.

        Returns:
            A list of ``(seq, timestamp, cost_usd, project_id)`` tuples, in
            ascending ``seq`` order (append order), for every record with
            ``seq > self._last_flushed_seq``. Empty when nothing is pending
            or no cap/records exist.
        """
        with self._lock:
            last = self._last_flushed_seq
            return [rec for rec in self._records if rec[0] > last]

    def mark_flushed(self, upto_seq: int) -> None:
        """Advance the flush watermark to ``upto_seq`` (monotonic, no-op on regress).

        Call this only AFTER the corresponding records have been durably
        written (e.g. the DB transaction committed). A lower or equal
        ``upto_seq`` than the current watermark is ignored — the watermark
        must never move backwards, which would cause already-flushed records
        to be re-flushed (duplicate INSERTs).

        Args:
            upto_seq: The highest ``seq`` that has been durably persisted.
        """
        with self._lock:
            if upto_seq > self._last_flushed_seq:
                self._last_flushed_seq = upto_seq

    def window_spend(
        self,
        now: float | None = None,
        *,
        project_id: str | None = None,
    ) -> float:
        """Sum of all spend within the rolling window ending at ``now``.

        Pruning: records older than ``(now - window_seconds)`` are dropped
        in-place so the list stays bounded.

        Args:
            now:        Override the current time (uses clock by default).
            project_id: If provided, filter to only records for this project.

        Returns:
            Total USD spent within the window.
        """
        if now is None:
            now = self._clock()
        cutoff = now - self._window_seconds
        with self._lock:
            # Prune in-place: keep records where ts >= cutoff
            self._records = [
                (seq, ts, c, pid) for seq, ts, c, pid in self._records if ts >= cutoff
            ]
            if project_id is not None:
                return sum(c for _, _, c, pid in self._records if pid == project_id)
            return sum(c for _, _, c, _ in self._records)

    def project_spend(self, project_id: str, now: float | None = None) -> float:
        """Convenience: spend for a single project within the window.

        Args:
            project_id: Project to query.
            now:        Override the current time (uses clock by default).

        Returns:
            Total USD spent by ``project_id`` within the window.
        """
        return self.window_spend(now=now, project_id=project_id)

    def project_breakdown(self, now: float | None = None) -> dict[str, float]:
        """Return per-project spend totals within the current window.

        Args:
            now: Override the current time (uses clock by default).

        Returns:
            Dict mapping ``project_id`` → USD spent.  Records without a
            ``project_id`` are grouped under the key ``""`` (empty string).
        """
        if now is None:
            now = self._clock()
        cutoff = now - self._window_seconds
        with self._lock:
            self._records = [
                (seq, ts, c, pid) for seq, ts, c, pid in self._records if ts >= cutoff
            ]
            breakdown: dict[str, float] = {}
            for _, _, c, pid in self._records:
                key = pid or ""
                breakdown[key] = breakdown.get(key, 0.0) + c
            return breakdown

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

    def spend_in_last_seconds(
        self,
        seconds: float,
        now: float | None = None,
    ) -> float:
        """Sum of spend in the last ``seconds`` seconds (does not prune).

        Args:
            seconds: Lookback window in seconds.
            now:     Override the current time (uses clock by default).

        Returns:
            Total USD spent in the lookback window.
        """
        if now is None:
            now = self._clock()
        cutoff = now - seconds
        with self._lock:
            return sum(c for _seq, ts, c, _pid in self._records if ts >= cutoff)

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
