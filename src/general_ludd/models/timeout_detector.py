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
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

logger = logging.getLogger(__name__)


class TimeoutKind(enum.Enum):
    CONNECTION_TIMEOUT = "connection_timeout"
    READ_TIMEOUT = "read_timeout"
    RATE_LIMITED = "rate_limited"
    CONTEXT_LENGTH = "context_length"
    PROVIDER_ERROR = "provider_error"
    AUTH_ERROR = "auth_error"
    INVALID_REQUEST = "invalid_request"
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

        # OpenAI-SDK-wrapped errors. The openai client (used by langchain_openai,
        # which is how the openai-compatible providers — including z.ai — are
        # wired) does NOT raise raw httpx exceptions: it wraps a connection
        # failure as openai.APIConnectionError, a timeout as openai.APITimeoutError,
        # and a 4xx/5xx as openai.APIStatusError (with .status_code + .response).
        # Without this branch every such failure classified as UNKNOWN and the
        # gateway's retry/failover path never fired for the real provider path.
        openai_kind = cls._classify_openai_error(exc, response_body=response_body)
        if openai_kind is not None:
            return openai_kind

        return TimeoutKind.UNKNOWN

    @classmethod
    def _classify_openai_error(
        cls,
        exc: BaseException,
        *,
        response_body: str | None = None,
    ) -> TimeoutKind | None:
        """Classify an openai-SDK exception, or return None if not one.

        Imports openai lazily so the dependency stays optional. APITimeoutError
        is a subclass of APIConnectionError, so order the checks timeout-first.
        APIStatusError carries .status_code + an httpx.Response, which we route
        through the same status-code logic used for raw httpx.HTTPStatusError so
        the retry/failover decision is identical regardless of which layer
        surfaced the error.
        """
        try:
            import openai
        except Exception:  # pragma: no cover - openai always present in practice
            return None

        if isinstance(exc, openai.APITimeoutError):
            return TimeoutKind.READ_TIMEOUT
        if isinstance(exc, openai.APIConnectionError):
            return TimeoutKind.CONNECTION_TIMEOUT
        if isinstance(exc, openai.APIStatusError):
            import httpx

            response = getattr(exc, "response", None)
            status_code = getattr(exc, "status_code", None)
            if isinstance(response, httpx.Response):
                wrapped = httpx.HTTPStatusError(
                    str(exc), request=response.request, response=response
                )
                return cls._classify_http_error(wrapped, response_body=response_body)
            # No usable response object but we still have a status code: map it
            # with the same code-based rules.
            if isinstance(status_code, int):
                if status_code == 429:
                    return TimeoutKind.RATE_LIMITED
                if status_code in _AUTH_ERROR_CODES:
                    return TimeoutKind.AUTH_ERROR
                if status_code in _RETRYABLE_SERVER_CODES:
                    return TimeoutKind.PROVIDER_ERROR
                if status_code == 400:
                    return TimeoutKind.INVALID_REQUEST
            return TimeoutKind.UNKNOWN
        return None

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
            # A generic 400 (malformed request, bad param, invalid schema) is a
            # non-auth client error. Labeling it AUTH_ERROR would poison health
            # metrics and misdirect the operator.
            return TimeoutKind.INVALID_REQUEST

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
        # Single-flight half-open probe bookkeeping: maps model_id → monotonic
        # admission timestamp for probes admitted in the current cooldown window.
        # Cleared on record_success (probe succeeded) or record_event (probe
        # re-armed the breaker). Storing the timestamp instead of a bare set
        # lets is_healthy() auto-expire a leaked slot (caller raised before
        # record_event/record_success) after one cooldown window, preventing
        # the breaker from wedging permanently open. Guarded by __lock.
        self.__probe_in_flight: dict[str, float] = {}
        # RLock (re-entrant) so internal helpers that re-acquire the lock — and
        # get_health(), which calls is_healthy() — do not self-deadlock.
        self.__lock = threading.RLock()

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
        # Lock the ENTIRE read-modify-write: a bare `d[k] = d.get(k,0)+1` is not
        # atomic under threads, so concurrent failures were lost-updating the
        # consecutive counter and the breaker could never reach its threshold.
        with self.__lock:
            self._consecutive[mid] = self._consecutive.get(mid, 0) + 1
            self._total[mid] = self._total.get(mid, 0) + 1
            self._last_failure[mid] = event
            history = self._history.setdefault(mid, [])
            history.append(event)
            if len(history) > self._max_event_history:
                del history[: len(history) - self._max_event_history]
            # A new failure re-arms the breaker: a previously-admitted half-open
            # probe must NOT keep the gate "claimed". If this event WAS the
            # failed probe, clearing the flag re-opens the breaker so a future
            # cooldown can admit a fresh probe (rather than the model being
            # stuck either permanently open or permanently probe-claimed).
            self.__probe_in_flight.pop(mid, None)

    def record_success(self, model_id: str) -> None:
        with self.__lock:
            self._consecutive[model_id] = 0
            self.__probe_in_flight.pop(model_id, None)

    def is_healthy(self, model_id: str, *, admit_probe: bool = True) -> bool:
        # The whole body is locked so the check-and-reset is atomic: previously
        # this read _consecutive and (in the cooldown branch) reset it to 0 as a
        # side effect of a *read*, unlocked — every concurrent caller raced past
        # the cooldown gate and a thundering herd hit the recovering provider.
        #
        # admit_probe=False lets status polls (get_health) inspect health
        # WITHOUT consuming the single half-open probe slot.
        with self.__lock:
            consecutive = self._consecutive.get(model_id, 0)
            if consecutive < self._failure_threshold:
                return True

            # RATE_LIMITED is intentionally NOT here: a 429'd model IS retryable
            # after a cooldown and MUST be backed off, so it must flow into the
            # cooldown branch below and be reported unhealthy until it clears.
            non_retryable = {
                TimeoutKind.AUTH_ERROR,
                TimeoutKind.CONTEXT_LENGTH,
            }
            last = self._last_failure.get(model_id)
            if last is not None and last.kind in non_retryable:
                return True

            if last is not None:
                elapsed = time.monotonic() - last.timestamp
                if elapsed >= self._cooldown_seconds:
                    # Cooldown elapsed: admit exactly ONE half-open probe per
                    # cooldown window. The probe branch does NOT reset
                    # _consecutive to 0 — the breaker stays OPEN until either a
                    # record_success() (probe healthy) clears it, or a
                    # record_event() (probe failed) re-arms it. This prevents
                    # the stampede where every concurrent caller's is_healthy()
                    # returned True and reset the counter.
                    if not admit_probe:
                        # A pure status poll: report "would admit a probe" as
                        # healthy without consuming the slot.
                        return True
                    # Expire leaked probe slots: if the caller raised before
                    # record_event/record_success the slot was never cleared.
                    # Any slot older than the cooldown window is stale — drop
                    # it now so the breaker can admit a fresh probe.
                    _now = time.monotonic()
                    stale = [
                        mid
                        for mid, ts in self.__probe_in_flight.items()
                        if _now - ts >= self._cooldown_seconds
                    ]
                    for mid in stale:
                        del self.__probe_in_flight[mid]
                    if model_id in self.__probe_in_flight:
                        # Another caller already holds this window's probe slot.
                        return False
                    self.__probe_in_flight[model_id] = _now
                    return True

            return False

    def get_health(self, model_id: str) -> dict[str, object]:
        with self.__lock:
            last = self._last_failure.get(model_id)
            return {
                "model_id": model_id,
                # admit_probe=False: a status poll must never consume the probe.
                "healthy": self.is_healthy(model_id, admit_probe=False),
                "consecutive_failures": self._consecutive.get(model_id, 0),
                "total_failures": self._total.get(model_id, 0),
                "last_failure_kind": last.kind.value if last else None,
                "last_failure_at": last.timestamp if last else None,
            }


# Kinds for which retrying is pointless (credential / input errors). Used by
# TimeoutRetryPolicy.decide and by the gateway retry predicates; kept here as
# the single source of truth so all three decision points stay in sync.
_NON_RETRYABLE_KINDS: frozenset[TimeoutKind] = frozenset(
    {
        TimeoutKind.AUTH_ERROR,
        TimeoutKind.CONTEXT_LENGTH,
        TimeoutKind.INVALID_REQUEST,
    }
)

# Overload kinds get a higher retry cap and longer backoff ceiling than the
# fast-failover path used for transient connection errors.
_OVERLOAD_KINDS: frozenset[TimeoutKind] = frozenset(
    {
        TimeoutKind.PROVIDER_ERROR,
        TimeoutKind.RATE_LIMITED,
    }
)


class TimeoutRetryPolicy:
    def __init__(
        self,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        failover_after_retries: int = 3,
        overload_max_retries: int = 10,
        overload_max_backoff_seconds: float = 120.0,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._failover_after = failover_after_retries
        self._overload_max_retries = overload_max_retries
        self._overload_max_backoff = overload_max_backoff_seconds
        # RANDOMIZED jitter (anti-thundering-herd). The backoff was previously
        # `0.5 + attempt*0.1` — a DETERMINISTIC function of `attempt`, so every
        # client that failed on the same attempt woke at the EXACT same instant
        # and re-stampeded the recovering provider. We now apply equal-jitter
        # (`exp/2 + random.uniform(0, exp/2)`) so each retry is spread across the
        # back half of its exponential window. `jitter_fn` is injectable (defaults
        # to real `random.uniform`) so tests can stub it deterministically while
        # production always gets true randomness. The contract: jitter_fn(lo, hi)
        # returns a value in [lo, hi].
        self._jitter_fn = jitter_fn

    def decide(
        self,
        kind: TimeoutKind,
        attempt: int,
        *,
        retry_after_seconds: float | None = None,
    ) -> RetryDecision:
        if kind in _NON_RETRYABLE_KINDS:
            return RetryDecision(
                should_retry=False,
                reason=f"{kind.value} is not retryable",
            )

        # Overload kinds (PROVIDER_ERROR, RATE_LIMITED) get a higher retry cap
        # and a longer backoff ceiling. They do NOT flow into the fast-failover
        # path used for transient connection errors.
        if kind in _OVERLOAD_KINDS:
            if attempt > self._overload_max_retries:
                return RetryDecision(
                    should_retry=False,
                    should_failover=True,
                    reason="overload max retries exhausted",
                )
            wait = self._compute_backoff(kind, attempt, retry_after_seconds, overload=True)
            return RetryDecision(
                should_retry=True,
                wait_seconds=wait,
                reason=f"retrying {kind.value} (overload) after {wait:.1f}s",
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
        overload: bool = False,
    ) -> float:
        if kind == TimeoutKind.RATE_LIMITED and retry_after is not None:
            return max(retry_after, 1.0)

        # Deterministic exponential component (grows 2**(attempt-1)).
        exp = self._base_backoff * (2 ** (attempt - 1))

        if kind == TimeoutKind.CONNECTION_TIMEOUT:
            exp *= 2.0

        # Equal-jitter: hold the back half deterministically and randomize the
        # front half so retries spread across [exp/2, exp] instead of all firing
        # at one deterministic instant (thundering herd). jitter_fn defaults to
        # random.uniform; tests inject a stub for reproducibility.
        half = exp / 2.0
        base = half + self._jitter_fn(0.0, half)

        if kind == TimeoutKind.RATE_LIMITED:
            base = max(base, 1.0)

        cap = self._overload_max_backoff if overload else self._max_backoff
        return float(min(base, cap))
