"""WebResilience — timeout + tenacity retry + per-host circuit breaker.

A thin REUSE of ``models/timeout_detector`` (the audited LLM-gateway resilience
primitives), re-keyed by HOST instead of model_id. No new breaker class:

  * breaker = ``ModelHealthTracker`` keyed by host (single-flight half-open probe
    comes for free).
  * policy  = ``TimeoutRetryPolicy`` (backoff math).
  * classify= ``TimeoutClassifier`` (httpx exc / status -> TimeoutKind).

``run(host, fn)`` drives ``tenacity.Retrying`` with backoff delegated to the
policy in ``before_sleep`` (NOT tenacity's wait), and DOES NOT also record a
breaker failure in ``before_sleep`` — that is the gateway's documented
double-count trap. Non-retryable kinds (auth/4xx/invalid) surface immediately.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx
import tenacity

from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutKind,
    TimeoutRetryPolicy,
)
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.safe_fetch import SafeFetchError
from general_ludd.web.types import WebError

T = TypeVar("T")

#: Kinds that are deterministic / client-side and must NEVER be retried.
_NON_RETRYABLE_KINDS = frozenset(
    {
        TimeoutKind.AUTH_ERROR,
        TimeoutKind.CONTEXT_LENGTH,
        TimeoutKind.INVALID_REQUEST,
    }
)


class CircuitOpenError(Exception):
    """Raised when the per-host breaker is open — short-circuits before the net."""

    def __init__(self, host: str) -> None:
        super().__init__(f"circuit open for host {host!r}")
        self.host = host


def classify_exception(exc: BaseException, *, body: str | None = None) -> TimeoutKind:
    """Classify an exception into a :class:`TimeoutKind` for the web layer.

    A :class:`SafeFetchError` (SSRF/redirect) is deterministic and non-retryable;
    everything else delegates to the audited :class:`TimeoutClassifier`.
    """
    if isinstance(exc, SafeFetchError):
        return TimeoutKind.INVALID_REQUEST  # deterministic, never retried
    return TimeoutClassifier.classify(exc, response_body=body)


def is_retryable(exc: BaseException) -> bool:
    """True iff ``exc`` is a transient failure worth retrying.

    SSRF/redirect blocks and auth/4xx/invalid-request are NEVER retried; connect/
    read timeouts, 5xx and 429 ARE.
    """
    if isinstance(exc, SafeFetchError):
        return False
    kind = classify_exception(exc)
    if kind in _NON_RETRYABLE_KINDS:
        return False
    return kind != TimeoutKind.UNKNOWN or isinstance(
        exc, httpx.TransportError | TimeoutError | OSError
    )


def web_error_for(exc: BaseException) -> WebError:
    """Map an exception to the structured :class:`WebError` for the tool layer."""
    if isinstance(exc, SafeFetchError):
        return exc.error
    if isinstance(exc, CircuitOpenError):
        return WebError.CIRCUIT_OPEN
    if isinstance(exc, tenacity.RetryError):
        return WebError.RETRY_EXHAUSTED
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return WebError.OFFLINE
    if isinstance(exc, OSError):  # socket.gaierror is an OSError subclass
        return WebError.OFFLINE
    kind = classify_exception(exc)
    return {
        TimeoutKind.CONNECTION_TIMEOUT: WebError.OFFLINE,
        TimeoutKind.READ_TIMEOUT: WebError.TIMEOUT,
        TimeoutKind.RATE_LIMITED: WebError.HTTP_4XX,
        TimeoutKind.AUTH_ERROR: WebError.HTTP_4XX,
        TimeoutKind.INVALID_REQUEST: WebError.HTTP_4XX,
        TimeoutKind.CONTEXT_LENGTH: WebError.HTTP_4XX,
        TimeoutKind.PROVIDER_ERROR: WebError.HTTP_5XX,
    }.get(kind, WebError.RETRY_EXHAUSTED)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Pull a Retry-After (seconds form) off an httpx 429/503 if present."""
    if isinstance(exc, httpx.HTTPStatusError):
        ra = exc.response.headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except ValueError:
                return None
    return None


class WebResilience:
    """Per-host breaker + retry/backoff wrapper around a fetch callable."""

    def __init__(self, policy: WebPolicy = DEFAULT_POLICY) -> None:
        self._policy = policy
        self._breaker = ModelHealthTracker(
            failure_threshold=policy.breaker_failure_threshold,
            cooldown_seconds=policy.breaker_cooldown_seconds,
        )
        self._retry_policy = TimeoutRetryPolicy(
            max_retries=policy.max_attempts,
            base_backoff_seconds=policy.base_backoff_seconds,
            max_backoff_seconds=policy.max_backoff_seconds,
            failover_after_retries=policy.max_attempts,
        )
        #: Test/observability seam: replaced with a no-op in tests so backoff
        #: does not actually sleep.
        self._sleep: Callable[[float], None] = time.sleep

    @property
    def breaker(self) -> ModelHealthTracker:
        return self._breaker

    def run(self, host: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` under the breaker + retry policy, keyed by ``host``.

        Raises :class:`CircuitOpenError` immediately if the breaker is open.
        Re-raises the last exception (``reraise=True``) on exhaustion so the tool
        layer maps it to a structured result.
        """
        if not self._breaker.is_healthy(host):
            raise CircuitOpenError(host)

        attempt_state = {"n": 0}

        def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
            # Backoff is delegated to the policy (NOT tenacity's wait). We do NOT
            # record a breaker failure here — that is done once per real failure
            # in the call wrapper below, avoiding the documented double-count.
            outcome = retry_state.outcome
            if outcome is None:
                return
            exc = outcome.exception()
            if exc is None:
                return
            attempt = retry_state.attempt_number
            kind = classify_exception(exc)
            wait = self._retry_policy._compute_backoff(
                kind, attempt, _retry_after_seconds(exc)
            )
            if wait > 0:
                self._sleep(wait)

        def _wrapped() -> T:
            attempt_state["n"] += 1
            t0 = time.monotonic()
            try:
                result = fn()
            except BaseException as exc:
                # Record a breaker failure exactly once per real failed attempt
                # (here), for retryable transient failures only. Deterministic
                # blocks (SSRF/redirect/auth) do not poison host health.
                if isinstance(exc, Exception) and is_retryable(exc):
                    self._breaker.record_event(
                        TimeoutEvent(
                            model_id=host,
                            kind=classify_exception(exc),
                            timestamp=time.monotonic(),
                            duration_s=time.monotonic() - t0,
                        )
                    )
                raise
            self._breaker.record_success(host)
            return result

        retryer = tenacity.Retrying(
            retry=tenacity.retry_if_exception(is_retryable),
            wait=tenacity.wait_none(),
            stop=tenacity.stop_after_attempt(max(1, self._policy.max_attempts)),
            before_sleep=_before_sleep,
            reraise=True,
        )
        return retryer(_wrapped)
