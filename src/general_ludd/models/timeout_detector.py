"""Model timeout detection, health tracking, and retry with failover.

Handles these timeout/failure categories from LLM providers:
- CONNECTION_TIMEOUT: server unreachable (ConnectTimeout, PoolTimeout, ConnectError)
- READ_TIMEOUT: connected but no response (ReadTimeout, WriteTimeout, TimeoutError)
- RATE_LIMITED: 429 Too Many Requests
- CONTEXT_LENGTH: prompt exceeds model context window (400 with context error)
- PROVIDER_ERROR: 500/502/503 server errors
- AUTH_ERROR: 401/403 auth failures
- UNKNOWN: unclassified errors

Retry strategy:
- CONNECTION_TIMEOUT: retry with exponential backoff, failover after max_retries
- READ_TIMEOUT: retry with exponential backoff, failover after max_retries
- RATE_LIMITED: retry after Retry-After header (or exponential backoff)
- PROVIDER_ERROR: retry with backoff, failover quickly
- CONTEXT_LENGTH: no retry (won't help), raise immediately
- AUTH_ERROR: no retry (credential issue), raise immediately
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class TimeoutKind(enum.Enum):
    CONNECTION_TIMEOUT = "connection_timeout"
    READ_TIMEOUT = "read_timeout"
    RATE_LIMITED = "rate_limited"
    CONTEXT_LENGTH = "context_length"
    PROVIDER_ERROR = "provider_error"
    AUTH_ERROR = "auth_error"
    UNKNOWN = "unknown"


_CONTEXT_LENGTH_PATTERNS = (
    "context_length_exceeded",
    "maximum context length",
    "context length",
    "too many tokens",
    "reduces the length",
    "input is too long",
    "exceeds the maximum",
)

_AUTH_ERROR_CODES = frozenset({401, 403})

_RETRYABLE_SERVER_CODES = frozenset({500, 502, 503, 529})


@dataclass
class TimeoutEvent:
    model_id: str
    kind: TimeoutKind
    timestamp: float
    duration_s: float


@dataclass
class RetryDecision:
    should_retry: bool = False
    should_failover: bool = False
    wait_seconds: float = 0.0
    reason: str = ""


class TimeoutClassifier:
    _KIND_BY_HTTPX_TYPE: ClassVar[dict[type, TimeoutKind]] = {}

    @classmethod
    def _build_type_map(cls) -> dict[type, TimeoutKind]:
        if cls._KIND_BY_HTTPX_TYPE:
            return cls._KIND_BY_HTTPX_TYPE
        import httpx

        cls._KIND_BY_HTTPX_TYPE = {
            httpx.ConnectTimeout: TimeoutKind.CONNECTION_TIMEOUT,
            httpx.PoolTimeout: TimeoutKind.CONNECTION_TIMEOUT,
            httpx.ConnectError: TimeoutKind.CONNECTION_TIMEOUT,
            httpx.ReadTimeout: TimeoutKind.READ_TIMEOUT,
            httpx.WriteTimeout: TimeoutKind.READ_TIMEOUT,
        }
        return cls._KIND_BY_HTTPX_TYPE

    @classmethod
    def classify(
        cls,
        exc: BaseException,
        *,
        response_body: str | None = None,
    ) -> TimeoutKind:
        import httpx

        type_map = cls._build_type_map()
        for exc_type, kind in type_map.items():
            if isinstance(exc, exc_type):
                return kind

        if isinstance(exc, TimeoutError):
            return TimeoutKind.READ_TIMEOUT

        if isinstance(exc, httpx.HTTPStatusError):
            return cls._classify_http_error(exc, response_body=response_body)

        return TimeoutKind.UNKNOWN

    @classmethod
    def _classify_http_error(
        cls,
        exc: BaseException,
        *,
        response_body: str | None = None,
    ) -> TimeoutKind:
        import httpx

        assert isinstance(exc, httpx.HTTPStatusError)
        code = exc.response.status_code

        if code == 429:
            return TimeoutKind.RATE_LIMITED

        if code in _AUTH_ERROR_CODES:
            return TimeoutKind.AUTH_ERROR

        if code in _RETRYABLE_SERVER_CODES:
            return TimeoutKind.PROVIDER_ERROR

        if code == 400:
            body = (response_body or str(exc) or "").lower()
            for pattern in _CONTEXT_LENGTH_PATTERNS:
                if pattern in body:
                    return TimeoutKind.CONTEXT_LENGTH
            return TimeoutKind.AUTH_ERROR

        return TimeoutKind.UNKNOWN


class ModelHealthTracker:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        max_event_history: int = 100,
    ) -> None:
        self.__failure_threshold = failure_threshold
        self.__cooldown_seconds = cooldown_seconds
        self.__max_history = max_event_history
        self.__consecutive: dict[str, int] = {}
        self.__total: dict[str, int] = {}
        self.__last_failure: dict[str, TimeoutEvent] = {}
        self.__history: dict[str, list[TimeoutEvent]] = {}

    @property
    def _failure_threshold(self) -> int:
        return self.__failure_threshold

    @property
    def _cooldown_seconds(self) -> float:
        return self.__cooldown_seconds

    @property
    def _max_event_history(self) -> int:
        return self.__max_history

    @property
    def _consecutive(self) -> dict[str, int]:
        return self.__consecutive

    @property
    def _total(self) -> dict[str, int]:
        return self.__total

    @property
    def _last_failure(self) -> dict[str, TimeoutEvent]:
        return self.__last_failure

    @property
    def _history(self) -> dict[str, list[TimeoutEvent]]:
        return self.__history

    def record_event(self, event: TimeoutEvent) -> None:
        mid = event.model_id
        self._consecutive[mid] = self._consecutive.get(mid, 0) + 1
        self._total[mid] = self._total.get(mid, 0) + 1
        self._last_failure[mid] = event
        history = self._history.setdefault(mid, [])
        history.append(event)
        if len(history) > self._max_event_history:
            del history[: len(history) - self._max_event_history]

    def record_success(self, model_id: str) -> None:
        self._consecutive[model_id] = 0

    def is_healthy(self, model_id: str) -> bool:
        consecutive = self._consecutive.get(model_id, 0)
        if consecutive < self._failure_threshold:
            return True

        non_retryable = {
            TimeoutKind.RATE_LIMITED,
            TimeoutKind.AUTH_ERROR,
            TimeoutKind.CONTEXT_LENGTH,
        }
        last = self._last_failure.get(model_id)
        if last is not None and last.kind in non_retryable:
            return True

        if last is not None:
            elapsed = time.monotonic() - last.timestamp
            if elapsed >= self._cooldown_seconds:
                self._consecutive[model_id] = 0
                return True

        return False

    def get_health(self, model_id: str) -> dict[str, Any]:
        last = self._last_failure.get(model_id)
        return {
            "model_id": model_id,
            "healthy": self.is_healthy(model_id),
            "consecutive_failures": self._consecutive.get(model_id, 0),
            "total_failures": self._total.get(model_id, 0),
            "last_failure_kind": last.kind.value if last else None,
            "last_failure_at": last.timestamp if last else None,
        }


class TimeoutRetryPolicy:
    def __init__(
        self,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        failover_after_retries: int = 3,
    ) -> None:
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._failover_after = failover_after_retries

    def decide(
        self,
        kind: TimeoutKind,
        attempt: int,
        *,
        retry_after_seconds: float | None = None,
    ) -> RetryDecision:
        if kind in (TimeoutKind.AUTH_ERROR, TimeoutKind.CONTEXT_LENGTH):
            return RetryDecision(
                should_retry=False,
                reason=f"{kind.value} is not retryable",
            )

        if attempt > self._max_retries:
            return RetryDecision(
                should_retry=False,
                should_failover=True,
                reason=f"max retries ({self._max_retries}) exhausted",
            )

        if attempt >= self._failover_after:
            wait = self._compute_backoff(kind, attempt, retry_after_seconds)
            return RetryDecision(
                should_retry=False,
                should_failover=True,
                wait_seconds=wait,
                reason=f"failover triggered after {attempt} attempts",
            )

        wait = self._compute_backoff(kind, attempt, retry_after_seconds)

        return RetryDecision(
            should_retry=True,
            wait_seconds=wait,
            reason=f"retrying {kind.value} after {wait:.1f}s",
        )

    def _compute_backoff(
        self,
        kind: TimeoutKind,
        attempt: int,
        retry_after: float | None,
    ) -> float:
        if kind == TimeoutKind.RATE_LIMITED and retry_after is not None:
            return max(retry_after, 1.0)

        jitter = 0.5 + (attempt * 0.1)
        base = self._base_backoff * (2 ** (attempt - 1)) * jitter

        if kind == TimeoutKind.CONNECTION_TIMEOUT:
            base *= 2.0

        if kind == TimeoutKind.RATE_LIMITED:
            base = max(base, 1.0)

        return float(min(base, self._max_backoff))
