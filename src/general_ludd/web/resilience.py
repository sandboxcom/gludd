"""Retry classification glue for the web toolkit.

A thin adapter that REUSES the proven LLM-flavoured timeout taxonomy in
:class:`general_ludd.models.timeout_detector.TimeoutClassifier` rather than
re-rolling a second copy.  Isolating the coupling here means that if that module
is refactored only :func:`is_retryable` has to change.

RETRY:  CONNECTION_TIMEOUT, READ_TIMEOUT, RATE_LIMITED (429), PROVIDER_ERROR
        (500/502/503/529).
DO NOT: AUTH_ERROR (401/403), INVALID_REQUEST (400), CONTEXT_LENGTH, and the
        synthetic SSRF/redirect/robots failures (never retry into an internal
        target — those raise :class:`NonRetryableWebError`, which is excluded).
"""

from __future__ import annotations

from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutKind

#: TimeoutKinds that justify a retry-with-backoff.
_RETRYABLE_KINDS = frozenset(
    {
        TimeoutKind.CONNECTION_TIMEOUT,
        TimeoutKind.READ_TIMEOUT,
        TimeoutKind.RATE_LIMITED,
        TimeoutKind.PROVIDER_ERROR,
    }
)


class WebFetchError(Exception):
    """Base for the toolkit's own synthetic fetch failures."""


class NonRetryableWebError(WebFetchError):
    """A terminal failure that must NEVER be retried (SSRF/redirect/robots/4xx).

    Carrying the structured reason lets the fetch loop convert the exhausted
    attempt into the right :class:`~general_ludd.web.results.WebError` without
    retrying into an internal target.
    """

    def __init__(self, reason: str, *, kind: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


class RetryableWebError(WebFetchError):
    """A transient failure (e.g. a retryable HTTP status surfaced as an exc)."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def is_retryable(exc: BaseException) -> bool:
    """Return True iff ``exc`` is a transient error worth retrying.

    Our own :class:`NonRetryableWebError` is always terminal; our
    :class:`RetryableWebError` is always transient.  Everything else (httpx
    transport/status errors, stdlib TimeoutError) defers to the shared
    :class:`TimeoutClassifier`.
    """
    if isinstance(exc, NonRetryableWebError):
        return False
    if isinstance(exc, RetryableWebError):
        return True
    try:
        kind = TimeoutClassifier.classify(exc)
    except Exception:  # pragma: no cover - classifier itself must never decide-by-raise
        return False
    return kind in _RETRYABLE_KINDS
