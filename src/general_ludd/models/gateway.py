"""Model gateway for LangChain provider management."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, cast

import httpx
import tenacity

if TYPE_CHECKING:
    from general_ludd.pricing_intel.catalog import PricingCatalog

from pydantic import BaseModel, Field, field_validator, model_validator

from general_ludd.events.types import ModelAddedEvent, ModelRemovedEvent
from general_ludd.models.cost_router import CostAwareRouter, ModelRoute
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.response_cache import _make_cache_key
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    _OVERLOAD_KINDS,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutRetryPolicy,
)
from general_ludd.observability.langsmith_tracer import LangSmithTracer
from general_ludd.observability.token_cost import default_token_tracker
from general_ludd.security.sanitize import sanitize_error_message

logger = logging.getLogger(__name__)

# Default TTL (seconds) for cached model responses. LLM outputs are
# non-deterministic and time-sensitive, so entries must expire rather than
# live forever. Configurable per-gateway via response_cache_ttl_seconds.
DEFAULT_RESPONSE_CACHE_TTL_SECONDS = 3600

# D-30 phase-one buffered payload limits. Token limits already live on
# ``ModelProfile``; these byte/tool defaults add independent memory-amplification
# bounds without changing the configured model context windows.
DEFAULT_MAX_REQUEST_BYTES = 1_048_576
DEFAULT_MAX_INPUT_TOKENS = 120_000
DEFAULT_MAX_RESPONSE_BYTES = 4_194_304
DEFAULT_MAX_OUTPUT_TOKENS = 8_000
DEFAULT_MAX_TOOL_CALLS = 64
DEFAULT_MAX_CUMULATIVE_REQUEST_BYTES = DEFAULT_MAX_REQUEST_BYTES
DEFAULT_MAX_CUMULATIVE_INPUT_TOKENS = DEFAULT_MAX_INPUT_TOKENS
DEFAULT_MAX_CUMULATIVE_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES
DEFAULT_MAX_CUMULATIVE_OUTPUT_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS
DEFAULT_MAX_CUMULATIVE_TOOL_CALLS = DEFAULT_MAX_TOOL_CALLS
DEFAULT_MAX_PROVIDER_ATTEMPTS = 16
DEFAULT_MAX_STREAM_BYTES = DEFAULT_MAX_RESPONSE_BYTES
DEFAULT_MAX_STREAM_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS
DEFAULT_MAX_STREAM_CHUNKS = 8192
DEFAULT_MAX_STREAM_SECONDS = 300
DEFAULT_MAX_STREAM_IDLE_SECONDS = 60
DEFAULT_MAX_STREAM_DECOMPRESSION_RATIO = 100

PayloadStage = Literal["request", "response"]
PayloadDimension = Literal[
    "bytes",
    "tokens",
    "tool_calls",
    "provider_attempts",
    "chunks",
    "duration_seconds",
    "idle_seconds",
    "decompression_ratio",
]
PayloadSource = Literal["gateway", "provider", "cache"]


def _default_provider_request_timeout() -> httpx.Timeout:
    """Return the gateway-owned deadline for non-streaming provider clients."""
    return httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


def _positive_profile_limit(profile: object, field_name: str, default: int) -> int:
    """Read a positive profile limit, retaining safe defaults for legacy stubs."""
    value = getattr(profile, field_name, default)
    return value if type(value) is int and value > 0 else default


def _coerce_token_count(value: object) -> int:
    """Coerce a provider-supplied token count into a safe, billable int.

    Provider usage metadata is fully untrusted. We:
    - reject bool (isinstance(True, int) is True) so it counts as 0, not 1;
    - reject non-numeric values (count as 0);
    - clamp at >= 0 so a negative count cannot produce a negative cost (which
      would CREDIT the budget guard and bypass the run-budget ceiling).
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        # NaN/Inf guard: int(float("nan")) raises ValueError and
        # int(float("inf")) raises OverflowError, which would crash
        # _invoke_and_bill at billing time on hostile/buggy provider usage
        # metadata. Treat any non-finite count as 0 (un-billable).
        if not math.isfinite(value):
            return 0
        return max(0, int(value))
    return 0


class _SecretsResolver(Protocol):
    def resolve(self, alias_name: str) -> str | None: ...


class _HealthTrackerProtocol(Protocol):
    def is_healthy(self, model_id: str, *, admit_probe: bool = ...) -> bool: ...
    def record_success(self, model_id: str) -> None: ...
    def record_event(self, event: TimeoutEvent) -> None: ...


def _is_healthy_with_timeout(
    tracker: _HealthTrackerProtocol,
    profile_id: str,
    *,
    timeout: float = 5.0,
) -> bool:
    result: list[bool] = [False]
    exc_info: list[BaseException | None] = [None]

    def _check() -> None:
        try:
            result[0] = tracker.is_healthy(profile_id)
        except Exception as exc:  # pragma: no cover - defensive
            exc_info[0] = exc
            result[0] = False

    t = threading.Thread(target=_check, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return False
    return result[0]


class _BudgetGuardProtocol(Protocol):
    def record_spend(self, cost: float) -> None: ...


class _PauseControllerProtocol(Protocol):
    def is_paused(self, scope: str, target_id: str) -> bool: ...


class _ResponseCacheProtocol(Protocol):
    def get(self, cache_key: str) -> dict[str, object] | None: ...
    def set(self, cache_key: str, response: dict[str, object], *, expire: float | None = ...) -> None: ...


class _MetricsCollectorProtocol(Protocol):
    def record_model_call(
        self,
        agent_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        success: bool,
        cost_per_input_token: float,
        cost_per_output_token: float,
        error: str | None = None,
    ) -> None: ...

    def record_failover(self, from_profile: str, to_profile: str, error: str = "") -> None: ...


class _EventBusProtocol(Protocol):
    def publish(self, event: object) -> None: ...


class _HookSystemProtocol(Protocol):
    def fire(self, name: str, payload: dict[str, object]) -> None: ...


class _WorkerBroadcasterProtocol(Protocol):
    def broadcast_model_update(self, action: str, model_id: str, payload: dict[str, object]) -> None: ...


class BudgetExceededError(ValueError):
    """Raised when a call is rejected by the budget gate (D-24 fix)."""


class SSRFRejectionError(ValueError):
    """Raised when an api_base_alias URL is rejected by the SSRF egress guard.

    Subclasses ValueError so existing ``except ValueError`` callers still catch
    it, but is a distinct type so the fail-open ``except (ValueError,
    ImportError): return None`` in ``_try_call_model`` can re-raise it (F-E fix)
    rather than silently falling through to the next fallback profile and
    masking the egress block.
    """


class ModelPausedError(Exception):
    """Raised when a model call is blocked because the profile or its project is paused.

    NOT a subclass of ValueError (unlike BudgetExceededError / SSRFRejectionError)
    so the fallback-chain exception handlers do NOT treat it as retryable — a paused
    model must not trigger a failover to the next profile in the chain (which would
    silently bypass the pause). The outer call_model_with_fallback / call_model_by_role
    walkers must check is_instance this type and re-raise immediately.
    """


class CircuitBreakerOpenError(Exception):
    """Raised when ALL models in a fallback chain have open circuit breakers.

    Not a subclass of ValueError (mirrors ModelPausedError) so the
    ``except (ValueError, ImportError)`` in ``_try_call_model`` does not
    silently swallow it — a fully-open circuit must propagate, not fall through
    to the next fallback.
    """


class PayloadLimitError(Exception):
    """Typed, payload-free rejection raised by model gateway hard limits.

    The exception intentionally carries only bounded scalar diagnostics. Model
    content and tool arguments are never copied into the message, logs, traces,
    metrics, cache, or persistence on this path.
    """

    def __init__(
        self,
        *,
        profile_id: str,
        stage: PayloadStage,
        dimension: PayloadDimension,
        actual: int,
        limit: int,
        source: PayloadSource,
        count_source: str,
    ) -> None:
        """Create a bounded diagnostic without retaining rejected payload data."""
        self.profile_id = profile_id
        self.stage = stage
        self.dimension = dimension
        self.actual = actual
        self.limit = limit
        self.source = source
        self.count_source = count_source
        super().__init__(
            "model payload limit exceeded: "
            f"profile={profile_id!r}, stage={stage}, dimension={dimension}, "
            f"actual={actual}, limit={limit}, source={source}, count_source={count_source}"
        )


class CumulativePayloadLimitError(PayloadLimitError):
    """Typed rejection when one logical request exhausts its shared budget.

    Retries and fallback hops retain the same private ledger. The error remains
    a ``PayloadLimitError`` for existing fail-closed propagation while giving
    callers a distinct type for request-wide cancellation/rejection handling.
    """


class StreamLimitError(PayloadLimitError):
    """Typed, payload-free rejection for an in-progress provider stream."""


class CallCancelledError(Exception):
    """Raised when a buffered model call is cancelled before provider invocation.

    Carries only the profile_id so operators can distinguish which call was
    cancelled. Distinct from PayloadLimitError (size-based) and not a subclass
    of ValueError (not retryable — a cancelled call must not trigger failover).
    """

    def __init__(self, profile_id: str) -> None:
        """Create a cancellation carrying only the affected profile identifier."""
        self.profile_id = profile_id
        super().__init__(f"call to profile={profile_id!r} cancelled before provider invocation")


@dataclass
class _RequestPayloadBudget:
    """Thread-safe accounting shared by every provider hop of one request."""

    max_request_bytes: int
    max_input_tokens: int
    max_response_bytes: int
    max_output_tokens: int
    max_tool_calls: int
    max_provider_attempts: int
    request_bytes: int = 0
    input_tokens: int = 0
    response_bytes: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    provider_attempts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_profile(cls, profile: ModelProfile) -> _RequestPayloadBudget:
        """Build a finite ledger from the initiating profile's configuration."""
        return cls(
            max_request_bytes=_positive_profile_limit(
                profile,
                "max_cumulative_request_bytes",
                DEFAULT_MAX_CUMULATIVE_REQUEST_BYTES,
            ),
            max_input_tokens=_positive_profile_limit(
                profile,
                "max_cumulative_input_tokens",
                DEFAULT_MAX_CUMULATIVE_INPUT_TOKENS,
            ),
            max_response_bytes=_positive_profile_limit(
                profile,
                "max_cumulative_response_bytes",
                DEFAULT_MAX_CUMULATIVE_RESPONSE_BYTES,
            ),
            max_output_tokens=_positive_profile_limit(
                profile,
                "max_cumulative_output_tokens",
                DEFAULT_MAX_CUMULATIVE_OUTPUT_TOKENS,
            ),
            max_tool_calls=_positive_profile_limit(
                profile,
                "max_cumulative_tool_calls",
                DEFAULT_MAX_CUMULATIVE_TOOL_CALLS,
            ),
            max_provider_attempts=_positive_profile_limit(
                profile,
                "max_provider_attempts",
                DEFAULT_MAX_PROVIDER_ATTEMPTS,
            ),
        )

    @staticmethod
    def _reject(
        *,
        profile_id: str,
        stage: PayloadStage,
        dimension: PayloadDimension,
        actual: int,
        limit: int,
    ) -> None:
        raise CumulativePayloadLimitError(
            profile_id=profile_id,
            stage=stage,
            dimension=dimension,
            actual=actual,
            limit=limit,
            source="gateway",
            count_source="request_wide_cumulative",
        )

    def reserve_provider_attempt(
        self,
        profile_id: str,
        *,
        request_bytes: int,
        input_tokens: int,
    ) -> None:
        """Atomically reserve one outbound attempt before provider construction."""
        with self._lock:
            next_attempts = self.provider_attempts + 1
            next_bytes = self.request_bytes + request_bytes
            next_tokens = self.input_tokens + input_tokens
            if next_attempts > self.max_provider_attempts:
                self._reject(
                    profile_id=profile_id,
                    stage="request",
                    dimension="provider_attempts",
                    actual=next_attempts,
                    limit=self.max_provider_attempts,
                )
            if next_bytes > self.max_request_bytes:
                self._reject(
                    profile_id=profile_id,
                    stage="request",
                    dimension="bytes",
                    actual=next_bytes,
                    limit=self.max_request_bytes,
                )
            if next_tokens > self.max_input_tokens:
                self._reject(
                    profile_id=profile_id,
                    stage="request",
                    dimension="tokens",
                    actual=next_tokens,
                    limit=self.max_input_tokens,
                )
            self.provider_attempts = next_attempts
            self.request_bytes = next_bytes
            self.input_tokens = next_tokens

    def reserve_response(
        self,
        profile_id: str,
        *,
        response_bytes: int,
        output_tokens: int,
        tool_calls: int,
    ) -> None:
        """Atomically account a buffered response before any side effect."""
        with self._lock:
            next_bytes = self.response_bytes + response_bytes
            next_tokens = self.output_tokens + output_tokens
            next_tool_calls = self.tool_calls + tool_calls
            if next_bytes > self.max_response_bytes:
                self._reject(
                    profile_id=profile_id,
                    stage="response",
                    dimension="bytes",
                    actual=next_bytes,
                    limit=self.max_response_bytes,
                )
            if next_tokens > self.max_output_tokens:
                self._reject(
                    profile_id=profile_id,
                    stage="response",
                    dimension="tokens",
                    actual=next_tokens,
                    limit=self.max_output_tokens,
                )
            if next_tool_calls > self.max_tool_calls:
                self._reject(
                    profile_id=profile_id,
                    stage="response",
                    dimension="tool_calls",
                    actual=next_tool_calls,
                    limit=self.max_tool_calls,
                )
            self.response_bytes = next_bytes
            self.output_tokens = next_tokens
            self.tool_calls = next_tool_calls


class ModelProfile(BaseModel):
    """Validated provider, budget, payload, and fallback configuration."""

    model_profile_id: str
    role_names: list[str] = Field(default_factory=list)
    provider: str = "openai"
    provider_package: str = "langchain-openai"
    provider_class_hint: str = "ChatOpenAI"
    model_name: str = ""
    api_base_alias: str | None = None
    credential_alias: str | None = None
    context_window: int = 128000
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    max_cumulative_request_bytes: int = DEFAULT_MAX_CUMULATIVE_REQUEST_BYTES
    max_cumulative_input_tokens: int = DEFAULT_MAX_CUMULATIVE_INPUT_TOKENS
    max_cumulative_response_bytes: int = DEFAULT_MAX_CUMULATIVE_RESPONSE_BYTES
    max_cumulative_output_tokens: int = DEFAULT_MAX_CUMULATIVE_OUTPUT_TOKENS
    max_cumulative_tool_calls: int = DEFAULT_MAX_CUMULATIVE_TOOL_CALLS
    max_provider_attempts: int = DEFAULT_MAX_PROVIDER_ATTEMPTS
    max_stream_bytes: int = DEFAULT_MAX_STREAM_BYTES
    max_stream_tokens: int = DEFAULT_MAX_STREAM_TOKENS
    max_stream_chunks: int = DEFAULT_MAX_STREAM_CHUNKS
    max_stream_seconds: int = DEFAULT_MAX_STREAM_SECONDS
    max_stream_idle_seconds: int = DEFAULT_MAX_STREAM_IDLE_SECONDS
    max_stream_decompression_ratio: int = DEFAULT_MAX_STREAM_DECOMPRESSION_RATIO
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0
    api_metered: bool = True
    run_budget_usd: float = 200.0
    enabled: bool = False
    resource_profile: str = "ai_heavy"
    roles: list[str] = Field(default_factory=list)
    latency_class: str | None = None
    quality_class: str | None = None
    fallback_profiles: list[str] = Field(default_factory=list)
    max_failover_retries: int = 3
    probe_enabled: bool = False
    # Anti-thundering-herd cap (test 13a / docs/audit/FAILOVER_GAPS.md
    # fallback-concurrency-limit): bounds how many callers may be in-flight to
    # THIS profile at once when it is acting as a fallback target. Without
    # this, an open primary circuit routed every concurrent caller straight to
    # the secondary with no cap, so a primary outage could cascade into a
    # secondary outage. The primary's OWN half-open probe already has a
    # separate, unrelated single-flight guard (ModelHealthTracker); this field
    # only gates the fallback-fan-out path (_call_fallback).
    fallback_max_concurrency: int = 2
    stream_provider_max_concurrency: int = 1

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("model_profile_id must not be empty")
        return v

    @field_validator(
        "context_window",
        "max_request_bytes",
        "max_input_tokens",
        "max_response_bytes",
        "max_output_tokens",
        "max_tool_calls",
        "max_cumulative_request_bytes",
        "max_cumulative_input_tokens",
        "max_cumulative_response_bytes",
        "max_cumulative_output_tokens",
        "max_cumulative_tool_calls",
        "max_provider_attempts",
        "max_stream_bytes",
        "max_stream_tokens",
        "max_stream_chunks",
        "max_stream_seconds",
        "max_stream_idle_seconds",
        "max_stream_decompression_ratio",
        "fallback_max_concurrency",
        "stream_provider_max_concurrency",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @field_validator("cost_per_input_token", "cost_per_output_token")
    @classmethod
    def _non_negative_float(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("must be finite non-negative")
        return v

    @staticmethod
    def seed_token_rates_from_catalog(
        provider: str,
        model_name: str,
        catalog: PricingCatalog | None = None,
    ) -> tuple[float, float]:
        """Query the pricing catalog for per-token rates for a given provider+model.

        Returns ``(cost_per_input_token, cost_per_output_token)`` where each value
        is in USD-per-token (the catalog stores USD-per-1K and we divide by 1000).

        When ``catalog`` is None or the lookup misses, returns ``(0.0, 0.0)`` —
        callers must NOT treat zero as "free" but as "unpriced" and may fall back
        to operator-configured rates.
        """
        if catalog is None:
            return 0.0, 0.0
        try:
            price = catalog.model_price(provider, model_name)
        except Exception:
            return 0.0, 0.0
        if price is None:
            return 0.0, 0.0
        if not isinstance(getattr(price, "input_usd_per_1k", None), (int, float)):
            return 0.0, 0.0
        if not isinstance(getattr(price, "output_usd_per_1k", None), (int, float)):
            return 0.0, 0.0
        inp_per_token = float(price.input_usd_per_1k) / 1000.0
        out_per_token = float(price.output_usd_per_1k) / 1000.0
        return inp_per_token, out_per_token

    @field_validator("run_budget_usd")
    @classmethod
    def _non_negative_budget_float(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("must be finite non-negative")
        return v

    @model_validator(mode="after")
    def _reject_zero_cost_for_enabled_metered(self) -> ModelProfile:
        if self.enabled and self.api_metered:
            if self.cost_per_input_token == 0.0 and self.cost_per_output_token == 0.0:
                raise ValueError(
                    "enabled + api_metered profile must have non-zero cost: "
                    f"profile_id={self.model_profile_id} has zero cost "
                    "per input AND output tokens"
                )
            if self.cost_per_input_token == 0.0:
                raise ValueError(
                    "enabled + api_metered profile must have non-zero cost: "
                    f"profile_id={self.model_profile_id} has zero cost per input token"
                )
            if self.cost_per_output_token == 0.0:
                raise ValueError(
                    "enabled + api_metered profile must have non-zero cost: "
                    f"profile_id={self.model_profile_id} has zero cost per output token"
                )
        return self


@dataclass
class ModelResponse:
    """Normalized provider response with usage, cost, and tool-call metadata."""

    content: str
    usage_metadata: dict[str, object] = field(default_factory=dict)
    cost_estimate: float = 0.0
    model_name: str = ""
    raw_response: object = None
    # Tool/function calls the model requested, NORMALIZED to the OpenAI-nested
    # shape the tool-call loop consumes: each item is
    #   {"id": str, "type": "function",
    #    "function": {"name": str, "arguments": <json-string>}}
    # This field is the bridge that makes the MCP tool-call loop functional. The
    # provider's tool_calls live on raw_response (a LangChain AIMessage, whose
    # .tool_calls is the FLAT {"name","args","id","type"} shape, or a raw OpenAI
    # message with the nested shape). Before this field existed, _invoke_and_bill
    # dropped them on the floor — only content/raw_response were kept — so
    # ToolCallLoop's `getattr(response, "tool_calls", None)` was always None and
    # NO tool was ever dispatched in production. _extract_tool_calls normalizes
    # either provider shape into the nested shape and we store it here.
    tool_calls: list[dict[str, object]] | None = None
    # Correlation ID threaded through call_model_with_retry(correlation_id=...)
    # so a request that crosses provider boundaries during failover (primary ->
    # secondary -> ...) can still be traced as ONE logical operation. None when
    # the caller did not supply one (the overwhelming majority of calls today).
    correlation_id: str | None = None


def _attach_correlation_id(response: ModelResponse, correlation_id: str | None) -> ModelResponse:
    """Stamp ``correlation_id`` onto ``response`` when the caller supplied one.

    A tiny helper so every return point in ``call_model_with_retry`` (primary
    success, fallback success, single-fallback-probe success) consistently
    surfaces the caller's correlation ID without duplicating the `if` at each
    call site. See docs/audit/FAILOVER_GAPS.md (correlation-id-propagation).
    """
    if correlation_id is not None:
        response.correlation_id = correlation_id
    return response


def _redact_url_in_exception(exc: BaseException, url: str) -> None:
    """Redact a specific resolved URL from an exception's args in-place.

    C.6 hardening: provider error messages (httpx.ConnectError,
    httpx.HTTPStatusError, etc.) can embed the literal resolved base_url,
    e.g. "Connection refused to https://actual-proxy.internal/v1/chat".
    This replaces every occurrence with ``[REDACTED_URL]`` so the internal
    endpoint is never exposed to the caller via the exception trace.
    Safe on any exception type; a no-op when ``url`` is empty.
    """
    if not url:
        return
    try:
        new_args = tuple(arg.replace(url, "[REDACTED_URL]") if isinstance(arg, str) else arg for arg in exc.args)
        exc.args = new_args
    except Exception:
        pass


def _enrich_all_down_message(exc: BaseException, attempts: list[dict[str, str]]) -> None:
    """Enumerate attempted providers in an exception message.

    Rewrite ``exc``'s message in place to enumerate every attempted
    provider profile and why each one failed — WITHOUT changing the
    exception's type. Callers that pattern-match on the concrete exception
    type (e.g. ``httpx.HTTPStatusError``) keep seeing exactly that type; only
    ``str(exc)`` changes, so operators (and any HTTP layer) see the full
    all-providers-down picture instead of only the last provider's status.
    A no-op when ``attempts`` is empty (nothing to enrich with).
    See docs/audit/FAILOVER_GAPS.md (structured-all-down-error).
    """
    if not attempts:
        return
    ids = ", ".join(a["profile_id"] for a in attempts)
    detail = "; ".join(f"{a['profile_id']} ({a['reason']})" for a in attempts)
    message = f"all providers down [{ids}]: {detail}"
    try:
        exc.args = (message, *exc.args[1:])
    except Exception:  # pragma: no cover - defensive; args is always settable
        logger.debug("could not enrich all-providers-down exception", exc_info=True)


def _extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Parse the ``Retry-After`` header (seconds form) from a 429 response.

    Per RFC 7231 the value may be either a delta-seconds integer or an HTTP-date.
    Only the integer form is honored here; an HTTP-date or a missing/garbage
    header returns ``None`` so the caller falls back to exponential backoff.
    The header is read from the ``httpx.Response`` attached to an
    ``httpx.HTTPStatusError`` or an ``openai.APIStatusError`` (whose
    ``.response`` is itself an ``httpx.Response``).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # pragma: no cover - defensive against odd header objects
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _extract_tool_calls(raw_response: object) -> list[dict[str, object]] | None:
    """Normalize a provider response's tool calls into the nested OpenAI shape.

    The MCP ToolCallLoop reads each tool call as ``tc["function"]["name"]`` /
    ``tc["function"]["arguments"]`` (a JSON string) / ``tc["id"]`` — the OpenAI
    *nested* shape. But the live provider here is ``langchain_openai.ChatOpenAI``,
    whose AIMessage exposes ``.tool_calls`` in the LangChain *flat* shape
    ``{"name", "args": dict, "id", "type"}``. A raw OpenAI SDK message instead
    carries the nested shape (objects or dicts). This helper accepts EITHER and
    returns the nested shape, so the loop dispatches regardless of which provider
    produced the response.

    Returns None when the response carries no tool calls (the common case), so
    ``ModelResponse.tool_calls`` stays falsy and the loop returns content.
    """
    import json as _json

    raw_calls = getattr(raw_response, "tool_calls", None)
    # Defensive: only a real list/tuple of tool calls is meaningful. Anything
    # else (None, a MagicMock auto-attr from a stubbed provider, a scalar) is
    # treated as "no tool calls" — this helper runs on EVERY billed call and must
    # never raise into the billing path on an unexpected provider shape.
    if not isinstance(raw_calls, (list, tuple)) or not raw_calls:
        return None

    normalized: list[dict[str, object]] = []
    for tc in raw_calls:
        # Support both dict-shaped tool calls (LangChain flat, or already-nested)
        # and OpenAI SDK objects (with .id / .function.name / .function.arguments).
        if isinstance(tc, dict):
            fn = tc.get("function")
            if isinstance(fn, dict) and "name" in fn:
                # Already nested OpenAI shape.
                name = fn.get("name", "")
                args = fn.get("arguments", "{}")
                call_id = tc.get("id", "")
            else:
                # LangChain flat shape: {"name", "args": dict, "id", "type"}.
                name = tc.get("name", "")
                args = tc.get("args", {})
                call_id = tc.get("id", "")
        else:
            # OpenAI SDK object: tc.id, tc.function.name, tc.function.arguments.
            call_id = getattr(tc, "id", "") or ""
            fn_obj = getattr(tc, "function", None)
            name = getattr(fn_obj, "name", "") or ""
            args = getattr(fn_obj, "arguments", "{}")

        # The loop json.loads() the arguments, so always hand it a JSON string.
        if not isinstance(args, str):
            try:
                args = _json.dumps(args)
            except (TypeError, ValueError):
                args = "{}"

        if not name:
            # A tool call with no resolvable name cannot be dispatched; skip it
            # rather than emit a call the loop would reject as unregistered.
            continue

        normalized.append(
            {
                "id": call_id or "",
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
        )

    return normalized or None


class _LimitedChatModel:
    """LangChain runnable wrapper that enforces payload limits on every invoke.

    ``ModelGateway.get_chat_model`` returns instances of this class so callers
    that use the raw LangChain runnable (instead of ``call_model``) still get
    bounded request/response size enforcement.  It does NOT bill, cache, or
    record metrics — those remain on the ``call_model`` / ``call_model_stream``
    paths.
    """

    def __init__(
        self,
        inner: object,
        *,
        profile: ModelProfile,
        profile_id: str,
        enforce_request: Callable[[list[dict[str, str]], dict[str, Any]], tuple[int, int]],
    ) -> None:
        self._inner = inner
        self._profile = profile
        self._profile_id = profile_id
        self._enforce_request = enforce_request

    def invoke(self, messages: list[dict[str, str]], **kwargs: object) -> object:
        self._enforce_request(messages, dict(kwargs))
        raw_response = self._inner.invoke(messages, **kwargs)  # type: ignore[attr-defined]
        content = str(getattr(raw_response, "content", str(raw_response)))
        usage_obj = getattr(raw_response, "usage_metadata", {}) or {}
        usage = usage_obj if isinstance(usage_obj, dict) else {}
        raw_tool_calls = getattr(raw_response, "tool_calls", None)
        raw_tool_call_count = len(raw_tool_calls) if isinstance(raw_tool_calls, (list, tuple)) else 0
        tool_calls = _extract_tool_calls(raw_response)
        ModelGateway._enforce_response_limits(
            ModelGateway.__new__(ModelGateway),
            self._profile,
            self._profile_id,
            content=content,
            usage=usage,
            raw_tool_call_count=raw_tool_call_count,
            tool_calls=tool_calls,
            source="provider",
        )
        return raw_response

    def stream(self, messages: list[dict[str, str]], **kwargs: object) -> object:
        self._enforce_request(messages, dict(kwargs))
        return self._inner.stream(messages, **kwargs)  # type: ignore[attr-defined]

    def bind_tools(self, tools: list[dict[str, object]]) -> _LimitedChatModel:
        inner = self._inner
        if hasattr(inner, "bind_tools"):
            inner = inner.bind_tools(tools)
        return _LimitedChatModel(
            inner,
            profile=self._profile,
            profile_id=self._profile_id,
            enforce_request=self._enforce_request,
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class ModelGateway:
    """Route bounded model requests across providers with fail-closed controls."""

    def __init__(
        self,
        profiles: list[ModelProfile] | dict[str, ModelProfile] | None = None,
        provider_registry: ProviderRegistry | None = None,
        secrets_manager: _SecretsResolver | None = None,
        budget_guard: _BudgetGuardProtocol | None = None,
        router: ModelRouter | None = None,
        cost_router: CostAwareRouter | None = None,
        event_bus: _EventBusProtocol | None = None,
        hook_system: _HookSystemProtocol | None = None,
        worker_broadcaster: _WorkerBroadcasterProtocol | None = None,
        metrics_collector: _MetricsCollectorProtocol | None = None,
        metrics_agent_id: str | None = None,
        response_cache: _ResponseCacheProtocol | None = None,
        response_cache_ttl_seconds: int = DEFAULT_RESPONSE_CACHE_TTL_SECONDS,
        health_tracker: _HealthTrackerProtocol | None = None,
        pause_controller: _PauseControllerProtocol | None = None,
        langsmith_tracer: LangSmithTracer | None = None,
        max_fallback_depth: int = 3,
        request_token_counter: Callable[[ModelProfile, list[dict[str, str]]], int] | None = None,
        stream_wire_byte_counter: Callable[[object], int] | None = None,
        billing_clock: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        """Create a gateway and assume ownership of any injected response cache."""
        self._profiles: dict[str, ModelProfile] = {}
        if profiles:
            src = profiles.values() if isinstance(profiles, dict) else profiles
            for p in src:
                self._profiles[p.model_profile_id] = p
        self._registry = provider_registry
        self._secrets = secrets_manager
        self._budget_guard = budget_guard
        self._router = router
        self._cost_router = cost_router
        self._event_bus = event_bus
        self._hooks = hook_system
        self._broadcaster = worker_broadcaster
        self._metrics_collector = metrics_collector
        self._metrics_agent_id = metrics_agent_id
        self._response_cache = response_cache
        self._response_cache_ttl_seconds = response_cache_ttl_seconds
        self._closed = False
        self._health_tracker = health_tracker
        self._pause_controller = pause_controller
        self._langsmith_tracer = langsmith_tracer
        self._max_fallback_depth = max_fallback_depth
        self._request_token_counter = request_token_counter
        self._stream_wire_byte_counter = stream_wire_byte_counter
        self._billing_clock = billing_clock or (lambda: datetime.datetime.now(datetime.UTC))
        # Gateway-wide failover event log (not tied to any single profile's
        # own chain config): every hop walked by _walk_fallbacks is recorded
        # here for audit/debugging, independent of whether a metrics collector
        # is configured. See docs/audit/FAILOVER_GAPS.md (failover-metrics-facets).
        self._failover_log = ModelFailoverChain(primary_profile="_gateway")
        # Per-profile fallback concurrency semaphores (test 13a /
        # docs/audit/FAILOVER_GAPS.md fallback-concurrency-limit), created
        # lazily on first use and sized from ModelProfile.fallback_max_concurrency.
        self._fallback_semaphores: dict[str, threading.Semaphore] = {}
        self._fallback_semaphore_lock = threading.Lock()
        # Per-profile stream provider serialization: at most one caller per
        # profile_id can construct and start a streaming provider at a time.
        # Sized from ModelProfile.stream_provider_max_concurrency (default 1).
        self._stream_provider_semaphores: dict[str, threading.Semaphore] = {}
        self._stream_provider_semaphore_lock = threading.Lock()
        # Per-cache-key single-flight locks: under concurrency, N identical
        # cache misses would all call the provider (cache stampede). We serialize
        # identical misses on a per-key lock so only the first does the provider
        # call and the rest re-read the now-populated cache. _cache_key_locks is
        # itself guarded by _cache_key_locks_guard.
        #
        # Eviction: each entry is reference-counted via _cache_key_lock_refs.
        # Threads that need a key increment the ref under the guard before
        # blocking on the per-key lock; when they release it they decrement and
        # delete the entry if the count reaches zero.  This keeps the dict
        # bounded to ≤ (number of in-flight cache-key waiters) entries rather
        # than growing without bound across the lifetime of the process.
        self._cache_key_locks: dict[str, threading.Lock] = {}
        self._cache_key_lock_refs: dict[str, int] = {}
        self._cache_key_locks_guard = threading.Lock()

    def __enter__(self) -> ModelGateway:
        """Return this gateway for deterministic context-managed ownership."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release owned resources when leaving a managed gateway scope."""
        self.close()

    def __del__(self) -> None:
        """Best-effort cleanup when a caller drops an unmanaged gateway."""
        with contextlib.suppress(Exception):
            self.close()

    def close(self) -> None:
        """Release resources owned by the gateway exactly once."""
        if self._closed:
            return
        cache = self._response_cache
        close = getattr(cache, "close", None)
        if callable(close):
            close()
        self._closed = True

    def _apply_billing_rate(self, base_cost: float) -> tuple[float, str, float]:
        """Apply one peak-pricing snapshot to a completed model call.

        Sampling the injectable clock once keeps the charged multiplier, rate
        label, and savings ledger consistent even when a call completes on a
        peak-window boundary. The production default remains the current UTC
        wall clock; tests and replay tools can provide a deterministic clock.
        """
        from general_ludd.budget.peak_pricing import (
            PeakPricingTracker,
            current_rate_multiplier,
            is_peak,
        )

        now = self._billing_clock()
        peak = is_peak(now)
        multiplier = current_rate_multiplier(now)
        effective_cost = base_cost * multiplier
        if not peak:
            PeakPricingTracker.singleton().record_call(base_cost, effective_cost)
        return effective_cost, "peak" if peak else "off-peak", multiplier

    def _cache_key_lock(self, cache_key: str) -> threading.Lock:
        """Return the process-local single-flight lock for a cache key.

        The caller MUST pair every call with a matching ``_cache_key_unref``
        in a ``finally`` block so the ref-count (and, when it reaches zero,
        the dict entry) are cleaned up even if the provider call raises.
        """
        with self._cache_key_locks_guard:
            lock = self._cache_key_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._cache_key_locks[cache_key] = lock
            self._cache_key_lock_refs[cache_key] = self._cache_key_lock_refs.get(cache_key, 0) + 1
            return lock

    def _cache_key_unref(self, cache_key: str) -> None:
        """Decrement the ref-count for *cache_key*; evict when it reaches zero.

        Must be called from a ``finally`` block that matches every
        ``_cache_key_lock`` call so eviction is guaranteed even on exceptions.
        """
        with self._cache_key_locks_guard:
            count = self._cache_key_lock_refs.get(cache_key, 0) - 1
            if count <= 0:
                self._cache_key_lock_refs.pop(cache_key, None)
                self._cache_key_locks.pop(cache_key, None)
            else:
                self._cache_key_lock_refs[cache_key] = count

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        """Return one configured profile, or None when it is unknown."""
        return self._profiles.get(profile_id)

    @staticmethod
    def _request_utf8_bytes(
        messages: list[dict[str, str]],
        request_kwargs: dict[str, Any],
    ) -> tuple[int, int]:
        """Return exact bytes for buffered structured request components.

        ``ensure_ascii=False`` is essential: escaped JSON length is not UTF-8
        wire length for non-ASCII input. Sorted keys make the accounting stable
        across otherwise equivalent dict insertion orders. Phase one counts the
        message envelope plus the structured bodies the gateway deliberately
        forwards (tool schemas, guided generation, and ``extra_body``); transport
        headers and provider-added framing are outside this logical boundary.
        """
        message_bytes = len(
            json.dumps(
                messages,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        body_keys = (
            "tools",
            "extra_body",
            "guided_json",
            "guided_regex",
            "guided_choice",
            "guided_grammar",
            "guided_whitespace_pattern",
        )
        structured_body = {key: request_kwargs[key] for key in body_keys if key in request_kwargs}
        if not structured_body:
            return message_bytes, 0
        body_bytes = len(
            json.dumps(
                structured_body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        return message_bytes + body_bytes, body_bytes

    def _request_tokens(
        self,
        profile: ModelProfile,
        messages: list[dict[str, str]],
        request_bytes: int,
        structured_body_bytes: int,
    ) -> tuple[int, str]:
        """Count request tokens without making a provider/network call.

        Operators may inject a model-specific local tokenizer. A counter is
        trusted only when it returns an exact non-boolean, non-negative integer.
        Missing, failing, or malformed counters fail closed to one token per
        UTF-8 byte, a deliberately conservative fallback for common byte-level
        model tokenizers.
        """
        counter = self._request_token_counter
        if counter is not None:
            try:
                counted = counter(profile, messages)
            except Exception:
                logger.warning(
                    "Configured request token counter failed for profile=%s; using conservative UTF-8 byte count",
                    profile.model_profile_id,
                )
            else:
                if type(counted) is int and counted >= 0:
                    return counted + structured_body_bytes, (
                        "configured_token_counter"
                        if structured_body_bytes == 0
                        else "configured_token_counter+structured_body_utf8_conservative"
                    )
                logger.warning(
                    "Configured request token counter returned an invalid count for profile=%s; "
                    "using conservative UTF-8 byte count",
                    profile.model_profile_id,
                )
        return request_bytes, "utf8_bytes_conservative"

    def _enforce_request_limits(
        self,
        profile: ModelProfile,
        profile_id: str,
        messages: list[dict[str, str]],
        request_kwargs: dict[str, Any],
    ) -> tuple[int, int]:
        request_bytes, structured_body_bytes = self._request_utf8_bytes(messages, request_kwargs)
        max_request_bytes = _positive_profile_limit(profile, "max_request_bytes", DEFAULT_MAX_REQUEST_BYTES)
        if request_bytes > max_request_bytes:
            raise PayloadLimitError(
                profile_id=profile_id,
                stage="request",
                dimension="bytes",
                actual=request_bytes,
                limit=max_request_bytes,
                source="gateway",
                count_source="compact_json_utf8",
            )
        input_tokens, count_source = self._request_tokens(
            profile,
            messages,
            request_bytes,
            structured_body_bytes,
        )
        max_input_tokens = _positive_profile_limit(profile, "max_input_tokens", DEFAULT_MAX_INPUT_TOKENS)
        if input_tokens > max_input_tokens:
            raise PayloadLimitError(
                profile_id=profile_id,
                stage="request",
                dimension="tokens",
                actual=input_tokens,
                limit=max_input_tokens,
                source="gateway",
                count_source=count_source,
            )
        return request_bytes, input_tokens

    @staticmethod
    def _response_utf8_bytes(
        content: str,
        tool_calls: list[dict[str, object]] | None,
    ) -> int:
        """Return exact retained UTF-8 bytes for text and normalized tools."""
        total = len(content.encode("utf-8"))
        if tool_calls:
            total += len(
                json.dumps(
                    tool_calls,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
        return total

    @staticmethod
    def _response_token_count(usage: dict[str, object], response_bytes: int) -> tuple[int, str]:
        """Use internally consistent LangChain usage metadata or fail closed."""
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        counts = (input_tokens, output_tokens, total_tokens)
        if all(type(value) is int and value >= 0 for value in counts):
            input_count = cast(int, input_tokens)
            output_count = cast(int, output_tokens)
            total_count = cast(int, total_tokens)
            if total_count == input_count + output_count:
                return output_count, "provider_usage_metadata"
        return response_bytes, "utf8_bytes_conservative"

    def _enforce_response_limits(
        self,
        profile: ModelProfile,
        profile_id: str,
        *,
        content: str,
        usage: dict[str, object],
        raw_tool_call_count: int,
        tool_calls: list[dict[str, object]] | None,
        source: Literal["provider", "cache"],
    ) -> tuple[int, int]:
        max_tool_calls = _positive_profile_limit(profile, "max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
        if raw_tool_call_count > max_tool_calls:
            raise PayloadLimitError(
                profile_id=profile_id,
                stage="response",
                dimension="tool_calls",
                actual=raw_tool_call_count,
                limit=max_tool_calls,
                source=source,
                count_source="provider_tool_call_list",
            )
        response_bytes = self._response_utf8_bytes(content, tool_calls)
        max_response_bytes = _positive_profile_limit(profile, "max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES)
        if response_bytes > max_response_bytes:
            raise PayloadLimitError(
                profile_id=profile_id,
                stage="response",
                dimension="bytes",
                actual=response_bytes,
                limit=max_response_bytes,
                source=source,
                count_source="retained_content_utf8",
            )
        output_tokens, count_source = self._response_token_count(usage, response_bytes)
        max_output_tokens = _positive_profile_limit(profile, "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
        if output_tokens > max_output_tokens:
            raise PayloadLimitError(
                profile_id=profile_id,
                stage="response",
                dimension="tokens",
                actual=output_tokens,
                limit=max_output_tokens,
                source=source,
                count_source=count_source,
            )
        return response_bytes, output_tokens

    def _cached_response(
        self,
        profile: ModelProfile,
        profile_id: str,
        cached: dict[str, object],
        request_payload_budget: _RequestPayloadBudget,
    ) -> ModelResponse:
        content = str(cached.get("content", ""))
        usage_obj = cached.get("usage_metadata", {})
        usage = usage_obj if isinstance(usage_obj, dict) else {}
        tool_calls_obj = cached.get("tool_calls")
        tool_calls = cast(list[dict[str, object]] | None, tool_calls_obj) if isinstance(tool_calls_obj, list) else None
        response_bytes, output_tokens = self._enforce_response_limits(
            profile,
            profile_id,
            content=content,
            usage=usage,
            raw_tool_call_count=len(tool_calls) if tool_calls is not None else 0,
            tool_calls=tool_calls,
            source="cache",
        )
        request_payload_budget.reserve_response(
            profile_id,
            response_bytes=response_bytes,
            output_tokens=output_tokens,
            tool_calls=len(tool_calls) if tool_calls is not None else 0,
        )
        return ModelResponse(**cast(dict[str, Any], cached))

    _TASK_MODEL_PREFERENCES: ClassVar[dict[str, list[str]]] = {
        "code": ["deepseek-coder", "glm-4", "deepseek-v3", "qwen2.5-coder-7b"],
        "ansible": ["qwen2.5-coder", "qwen2.5-coder-7b", "qwen2.5"],
        "general": ["default", "deepseek-v3", "qwen2.5"],
        "game": ["claude", "qwen2.5", "qwen2.5-coder", "qwen2.5-coder-7b"],
    }

    _DEFAULT_MODEL_PREFERENCE: ClassVar[list[str]] = ["deepseek-v3", "qwen2.5", "qwen2.5-coder-7b"]

    def route_for_task(self, task_kind: str) -> str:
        """Select the best model profile for a given task kind.

        Returns the profile_id of the first available matching profile,
        falling back through preferences then to any available profile.

        Task kinds:
        - ``"code"`` → DeepSeek-Coder or GLM-4 (best at coding tasks)
        - ``"ansible"`` → Qwen2.5-Coder (good at YAML/Python mixed)
        - ``"general"`` → default model
        - ``"game"`` → Claude or Qwen (good at creative tasks)
        - Anything else → default fallback chain
        """
        kind = task_kind.lower()
        preferences = self._TASK_MODEL_PREFERENCES.get(kind, self._DEFAULT_MODEL_PREFERENCE)

        for pref_name in preferences:
            profile_id = self._best_profile_for(pref_name)
            if profile_id is not None:
                return profile_id

        for profile_id, profile in self._profiles.items():
            if profile.enabled:
                return profile_id

        raise ValueError(f"No enabled profile available for task_kind='{task_kind}' (preferences: {preferences})")

    def route_for_task_with_cost(
        self,
        task_kind: str,
        budget_remaining: float | None = None,
        *,
        now: datetime.datetime | None = None,
    ) -> str:
        """Select the cheapest capable model using cost-aware routing.

        Delegates to ``CostAwareRouter.route_by_cost`` when wired, then maps
        the returned ``model_id`` (``provider/model_name``) to a gateway
        ``profile_id``. Falls back to ``route_for_task`` when the cost router
        is not configured.

        Args:
            task_kind: Task type (``code``, ``ansible``, ``general``, ``game``).
            budget_remaining: Explicit remaining budget override (USD). When
                omitted and a ``cost_tracker`` is wired, reads from the tracker.
            now: Override wall clock for peak/off-peak determination (tests).

        Returns:
            The best ``profile_id`` within budget, accounting for peak pricing.
        """
        if self._cost_router is None:
            return self.route_for_task(task_kind)

        route = asyncio.run(
            self._cost_router.route_by_cost(
                task_kind,
                budget_remaining=budget_remaining,
                now=now,
            )
        )
        return self._map_cost_route_to_profile(route, task_kind)

    def call_model_cost_aware(
        self,
        task_kind: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        now: datetime.datetime | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Route via cost-aware model selection, verify budget, then call.

        Steps:
        1. Route to cheapest capable model via ``CostAwareRouter.route_by_cost``.
        2. Confirm projected cost is within budget via ``CostAwareRouter.check_budget``.
        3. Delegate to ``call_model`` with the resolved profile.

        Falls back to ``route_for_task`` → ``call_model`` when the cost router
        is not configured.
        """
        if self._cost_router is None:
            profile_id = self.route_for_task(task_kind)
            return self.call_model(
                profile_id,
                messages,
                estimated_cost=estimated_cost,
                budget_remaining=budget_remaining,
                **kwargs,
            )

        route = asyncio.run(
            self._cost_router.route_by_cost(
                task_kind,
                budget_remaining=budget_remaining,
                now=now,
            )
        )

        budget_result = self._cost_router.check_budget(route.estimated_cost)
        if not cast(bool, budget_result.get("allowed", False)):
            reason = cast(str, budget_result.get("reason", "budget guard denied"))
            raise BudgetExceededError(
                f"Cost-aware routing for '{task_kind}' rejected by budget guard: "
                f"{reason} (estimated_cost={route.estimated_cost})"
            )

        profile_id = self._map_cost_route_to_profile(route, task_kind)
        return self.call_model(
            profile_id,
            messages,
            estimated_cost=max(estimated_cost, route.estimated_cost),
            budget_remaining=budget_remaining,
            **kwargs,
        )

    def _map_cost_route_to_profile(self, route: ModelRoute, task_kind: str) -> str:
        """Map a ``ModelRoute.model_id`` to a gateway ``profile_id``.

        The ``model_id`` format is ``provider/model_name`` (e.g.
        ``openai/gpt-4o-mini``). We match against profile entries on both
        provider prefix and model name substring, then fall back to
        ``route_for_task`` when no direct match exists.
        """
        provider, _, model_name = route.model_id.partition("/")
        provider_lower = provider.lower()
        model_lower = model_name.lower()

        best: str | None = None
        best_score = -1
        for pid, profile in self._profiles.items():
            if not profile.enabled:
                continue
            score = 0
            pid_lower = pid.lower()
            prof_model_lower = profile.model_name.lower()
            if provider_lower and provider_lower in pid_lower:
                score += 3
            if model_lower and model_lower in pid_lower:
                score += 2
            if model_lower and model_lower in prof_model_lower:
                score += 1
            if score > best_score:
                best_score = score
                best = pid

        if best is not None:
            return best
        return self.route_for_task(task_kind)

    def _best_profile_for(self, name_hint: str) -> str | None:
        """Find the best enabled profile matching a name hint.

        Matches case-insensitively against profile_id and model_name.
        Returns the first enabled match or None.
        """
        hint_lower = name_hint.lower()
        for profile_id, profile in self._profiles.items():
            if not profile.enabled:
                continue
            if hint_lower in profile_id.lower():
                return profile_id
            if hint_lower in profile.model_name.lower():
                return profile_id
        return None

    def get_chat_model(
        self,
        profile_id: str,
        *,
        tools: list[dict[str, object]] | None = None,
        project_id: str | None = None,
        messages: list[dict[str, str]] | None = None,
    ) -> object:
        """Return a LangChain chat model for use with langgraph agents.

        Constructs the provider with the same credential + SSRF guards as
        ``_invoke_and_bill``, optionally binds tools, and returns a
        ``_LimitedChatModel`` wrapper whose ``.invoke()`` applies the
        profile's payload byte/token/tool-call limits.  Callers that already
        supply ``messages`` at construction time get the request-byte and
        token pre-check applied immediately; otherwise the limits are
        enforced on each ``.invoke()`` call.
        """
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")
        provider_name = profile.provider
        registry = self._registry
        if registry is not None and not registry.is_installed(provider_name):
            registry.install_provider(provider_name)
            raise ImportError(
                f"Provider '{provider_name}' is not installed. A dependency update todo has been created."
            )
        job_secrets = self._resolver_for_project(str(project_id) if project_id else None)
        api_key: str | None = None
        if job_secrets and profile.credential_alias:
            api_key = job_secrets.resolve(profile.credential_alias)
        if registry is not None:
            provider_cls = registry.get_provider_class(provider_name)
        else:
            raise ValueError(f"No provider registry configured for '{profile_id}'")
        init_kwargs: dict[str, object] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        base_url: str | None = None
        _local = (
            os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"
            or profile_id.lower().startswith("local-")
            or profile_id.lower().startswith("ollama-")
        )
        if profile.api_base_alias and job_secrets:
            base_url = job_secrets.resolve(profile.api_base_alias)
        if not base_url and _local:
            base_url = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
        if base_url:
            if _local:
                init_kwargs["base_url"] = base_url
            else:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise SSRFRejectionError(
                        f"SSRF guard: refusing blocked api_base_alias URL (redacted) for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url
        init_kwargs["request_timeout"] = _default_provider_request_timeout()
        chat_model = provider_cls(**init_kwargs)
        if tools:
            if hasattr(chat_model, "bind_tools"):
                chat_model = chat_model.bind_tools(tools)
                logger.debug(
                    "Tools bound for profile=%s (%d tool(s))",
                    profile_id,
                    len(tools),
                )
            else:
                logger.warning(
                    "Provider class %s does not support bind_tools — tools=%r will be ignored for profile=%s",
                    type(chat_model).__name__,
                    tools,
                    profile_id,
                )
        from functools import partial

        enforce_request: Callable[[list[dict[str, str]], dict[str, Any]], tuple[int, int]]
        enforce_request = partial(
            self._enforce_request_limits,
            profile,
            profile_id,
        )
        if messages is not None:
            enforce_request(messages, {})
        return _LimitedChatModel(
            chat_model,
            profile=profile,
            profile_id=profile_id,
            enforce_request=enforce_request,
        )

    def is_available(self, profile_id: str) -> bool:
        """Return whether a known model profile is enabled for routing."""
        profile = self._profiles.get(profile_id)
        return profile is not None and profile.enabled

    def check_budget(
        self,
        profile_id: str,
        estimated_cost: float,
        budget_remaining: float,
        *,
        messages: list[dict[str, str]] | None = None,
        requested_max_output_tokens: int | None = None,
    ) -> bool:
        """Fail closed unless the server-estimated call fits every budget."""
        profile = self._profiles.get(profile_id)
        if profile is None:
            return False
        # NaN/Inf guard (fail-closed on poison, but inf budget = unlimited).
        # `x > NaN` is always False in Python, so a NaN budget_remaining would
        # silently pass every cost comparison — treat NaN as 0.0 (no budget).
        # But +inf is the LEGITIMATE default sentinel meaning "no budget limit"
        # (unlimited): it must be preserved so callers that omit budget_remaining
        # are not spuriously rejected (no finite cost exceeds inf). Only NaN is
        # clamped here. A non-finite estimated_cost (NaN OR Inf) is treated as
        # float("inf") so it can never slip under any finite cap — a NaN/Inf cost
        # must REJECT, not pass.
        if math.isnan(budget_remaining):
            budget_remaining = 0.0
        if not math.isfinite(estimated_cost):
            return False
        # D-21: do NOT trust the caller-provided estimated_cost. Re-estimate
        # server-side from the actual messages + the profile's price rates, and
        # use the MAX of (caller claim, server estimate) for the budget decision
        # so a buggy / malicious caller cannot under-report cost to slip past
        # the gate. If no messages are supplied, fall back to the caller value
        # (preserves backward compatibility for callers that pre-computed cost).
        #
        # requested_max_output_tokens (D-21 over-conservatism fix) is threaded
        # into estimate_cost so a call that caps its output tokens is estimated
        # at its real (smaller) cost instead of the worst-case max_output_tokens.
        # estimate_cost min()s it against profile.max_output_tokens, so the
        # estimate stays upper-bounded and the under-report protection holds.
        server_cost = (
            self.estimate_cost(profile, messages, requested_max_output_tokens) if messages is not None else None
        )
        if server_cost is not None and isinstance(server_cost, (int, float)) and math.isfinite(server_cost):
            effective_cost = max(estimated_cost, server_cost)
        else:
            effective_cost = estimated_cost
        effective_budget = budget_remaining
        if profile.api_metered:
            effective_budget = min(effective_budget, profile.run_budget_usd)
        return effective_cost <= effective_budget

    @staticmethod
    def estimate_cost(
        profile: ModelProfile,
        messages: list[dict[str, str]] | None,
        requested_max_output_tokens: int | None = None,
    ) -> float:
        """Server-side cost re-estimation independent of the caller.

        Approximates input tokens from the message content (~4 chars/token, a
        standard rough heuristic). Multiplies both legs by the profile's price
        rates. Returns 0.0 when messages is empty/None.

        Output leg (D-21 over-conservatism fix): by default the estimate assumes
        the model may emit up to its ``max_output_tokens`` (worst case). This is
        deliberately pessimistic so a caller cannot under-report cost to slip
        past the budget gate. But it ALSO meant every metered call on a
        low-``run_budget_usd`` deployment was rejected at the per-profile cap
        (line ~345) against a worst-case figure of e.g. 8000 tokens, even when
        the real call requested only a handful of output tokens — a silent
        self-DoS on legitimate work.

        When ``requested_max_output_tokens`` is provided, the output leg uses
        ``min(requested_max_output_tokens, profile.max_output_tokens)`` so a call
        that genuinely caps its output gets a realistic (smaller) estimate. The
        ``min`` keeps the estimate UPPER-BOUNDED by the profile's capacity:
        security is preserved because a caller can never request MORE output than
        the profile permits, so it can never use this path to under-estimate
        below what the model could actually emit under the profile's own cap.
        Omitting the argument (or passing a non-positive value) preserves the
        EXACT prior behavior (worst-case ``profile.max_output_tokens``), so the
        existing D-21 rejection tests still hold.
        """
        if not messages:
            return 0.0
        input_chars = 0
        for m in messages:
            content = str(m.get("content", "") or "") if isinstance(m, dict) else str(getattr(m, "content", "") or "")
            input_chars += len(content)
        approx_input_tokens = input_chars // 4
        if requested_max_output_tokens is not None and requested_max_output_tokens > 0:
            approx_output_tokens = min(requested_max_output_tokens, profile.max_output_tokens)
        else:
            approx_output_tokens = profile.max_output_tokens
        return approx_input_tokens * profile.cost_per_input_token + approx_output_tokens * profile.cost_per_output_token

    def list_profiles(self) -> list[ModelProfile]:
        """Return the currently configured model profiles."""
        return list(self._profiles.values())

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        requested_max_output_tokens: int | None = None,
        cancellation_event: threading.Event | None = None,
        _skip_health_check: bool = False,
        _request_payload_budget: _RequestPayloadBudget | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Invoke one profile after enforcing cancellation, payload, and budget limits."""
        if cancellation_event is not None and cancellation_event.is_set():
            raise CallCancelledError(profile_id)

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        if self._pause_controller is not None and self._pause_controller.is_paused("model", profile_id):
            raise ModelPausedError(f"Model profile '{profile_id}' is paused — call refused")

        if (
            not _skip_health_check
            and self._health_tracker is not None
            and not self._health_tracker.is_healthy(profile_id, admit_probe=False)
        ):
            raise CircuitBreakerOpenError(f"Profile '{profile_id}' circuit is open; refusing call")

        # D-30 phase one: reject bounded buffered requests before budget/cache
        # activity and, critically, before constructing or invoking a provider.
        request_bytes, input_tokens = self._enforce_request_limits(profile, profile_id, messages, kwargs)
        request_payload_budget = _request_payload_budget or _RequestPayloadBudget.from_profile(profile)

        # requested_max_output_tokens (D-21 over-conservatism fix): when a caller
        # knows it will cap the model's output (e.g. the /admin/models/call
        # `max_tokens` field), thread it into the budget gate so the call is
        # estimated at its real cost instead of the worst-case max_output_tokens.
        # It is NOT forwarded to the provider via **kwargs (the gateway controls
        # per-call token limits through profile config today); it only sharpens
        # the budget estimate. estimate_cost min()s it against the profile cap so
        # the under-report protection is preserved.
        if not self.check_budget(
            profile_id,
            estimated_cost,
            budget_remaining,
            messages=messages,
            requested_max_output_tokens=requested_max_output_tokens,
        ):
            raise BudgetExceededError(
                f"Call to '{profile_id}' rejected: over budget "
                f"(estimated={estimated_cost}, remaining={budget_remaining}, "
                f"profile_budget={profile.run_budget_usd}"
            )

        cache_key: str | None = None
        if self._response_cache is not None:
            cache_key = _make_cache_key(profile_id, messages, model_name=profile.model_name, **kwargs)
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for profile=%s key=%s", profile_id, cache_key[:12])
                return self._cached_response(
                    profile,
                    profile_id,
                    cached,
                    request_payload_budget,
                )

        # Cache stampede single-flight: under concurrency N identical misses
        # would otherwise all hit the provider. Serialize identical misses on a
        # per-key lock; inside the lock we re-read the cache (double-checked
        # locking) so only the first miss does the provider call and the rest
        # serve the now-populated entry.
        if cache_key is not None and self._response_cache is not None:
            lock = self._cache_key_lock(cache_key)
            try:
                with lock:
                    cached = self._response_cache.get(cache_key)
                    if cached is not None:
                        logger.debug(
                            "Cache hit (single-flight) profile=%s key=%s",
                            profile_id,
                            cache_key[:12],
                        )
                        return self._cached_response(
                            profile,
                            profile_id,
                            cached,
                            request_payload_budget,
                        )
                    return self._invoke_and_bill(
                        profile,
                        profile_id,
                        messages,
                        cache_key,
                        request_payload_budget=request_payload_budget,
                        request_bytes=request_bytes,
                        input_tokens=input_tokens,
                        **kwargs,
                    )
            finally:
                self._cache_key_unref(cache_key)

        return self._invoke_and_bill(
            profile,
            profile_id,
            messages,
            None,
            request_payload_budget=request_payload_budget,
            request_bytes=request_bytes,
            input_tokens=input_tokens,
            **kwargs,
        )

    @staticmethod
    def _stream_content_encoding(chunk: object) -> str:
        """Return a normalized provider-declared content encoding, if exposed."""
        metadata = getattr(chunk, "response_metadata", None)
        if not isinstance(metadata, dict):
            return ""
        encoding = metadata.get("content_encoding") or metadata.get("content-encoding")
        headers = metadata.get("headers")
        if not encoding and isinstance(headers, dict):
            encoding = headers.get("content-encoding")
        return str(encoding or "").strip().lower()

    @staticmethod
    def _stream_chunk_payload(chunk: object) -> tuple[str, int, int]:
        """Return retained text, decoded bytes, and raw tool-fragment count."""
        content_obj = getattr(chunk, "content", "")
        if isinstance(content_obj, str):
            content = content_obj
        else:
            content = json.dumps(
                content_obj,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
        decoded_bytes = len(content.encode("utf-8"))
        raw_tool_calls = getattr(chunk, "tool_calls", None)
        raw_tool_call_count = 0
        if isinstance(raw_tool_calls, (list, tuple)) and raw_tool_calls:
            raw_tool_call_count = len(raw_tool_calls)
            decoded_bytes += len(
                json.dumps(
                    raw_tool_calls,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            )
        return content, decoded_bytes, raw_tool_call_count

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        requested_max_output_tokens: int | None = None,
        tools: list[dict[str, object]] | None = None,
        project_id: str | None = None,
        **kwargs: Any,
    ) -> Iterator[object]:
        """Yield a bounded provider stream and finalize accounting on exhaustion.

        The iterator is closed on every terminal path, including a caller closing
        this generator early. Cache lookup/write is intentionally absent: a
        partially delivered stream is not an atomic cache value. Billing and all
        success side effects happen only after clean upstream exhaustion.
        """
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")
        if self._pause_controller is not None and self._pause_controller.is_paused("model", profile_id):
            raise ModelPausedError(f"Model profile '{profile_id}' is paused — call refused")
        if self._health_tracker is not None and not self._health_tracker.is_healthy(profile_id, admit_probe=False):
            raise CircuitBreakerOpenError(f"Profile '{profile_id}' circuit is open; refusing call")

        request_kwargs = dict(kwargs)
        if tools:
            request_kwargs["tools"] = tools
        request_bytes, input_tokens_for_limit = self._enforce_request_limits(
            profile,
            profile_id,
            messages,
            request_kwargs,
        )
        if not self.check_budget(
            profile_id,
            estimated_cost,
            budget_remaining,
            messages=messages,
            requested_max_output_tokens=requested_max_output_tokens,
        ):
            raise BudgetExceededError(
                f"Call to '{profile_id}' rejected: over budget "
                f"(estimated={estimated_cost}, remaining={budget_remaining}, "
                f"profile_budget={profile.run_budget_usd}"
            )

        request_payload_budget = _RequestPayloadBudget.from_profile(profile)
        request_payload_budget.reserve_provider_attempt(
            profile_id,
            request_bytes=request_bytes,
            input_tokens=input_tokens_for_limit,
        )

        provider_name = profile.provider
        registry = self._registry
        if registry is not None and not registry.is_installed(provider_name):
            registry.install_provider(provider_name)
            raise ImportError(
                f"Provider '{provider_name}' is not installed. A dependency update todo has been created."
            )
        if registry is None:
            raise ValueError(f"No provider registry configured for '{profile_id}'")
        provider_cls = registry.get_provider_class(provider_name)

        job_secrets = self._resolver_for_project(str(project_id) if project_id else None)
        api_key: str | None = None
        if job_secrets and profile.credential_alias:
            api_key = job_secrets.resolve(profile.credential_alias)

        init_kwargs: dict[str, object] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        resolved_base_url = ""
        base_url: str | None = None
        _local = (
            os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"
            or profile_id.lower().startswith("local-")
            or profile_id.lower().startswith("ollama-")
        )
        if profile.api_base_alias and job_secrets:
            base_url = job_secrets.resolve(profile.api_base_alias)
        if not base_url and _local:
            base_url = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
        if base_url:
            resolved_base_url = base_url
            if _local:
                init_kwargs["base_url"] = base_url
            else:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise SSRFRejectionError(
                        f"SSRF guard: refusing blocked api_base_alias URL (redacted) for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url

        provider_kwargs = dict(kwargs)
        work_type_obj = provider_kwargs.pop("work_type", "unknown")
        work_type = str(work_type_obj) if work_type_obj else "unknown"
        caller_base_url = provider_kwargs.pop("base_url", None)
        caller_api_key = provider_kwargs.pop("api_key", None)
        provider_kwargs.pop("request_timeout", None)
        provider_kwargs.pop("timeout", None)
        if caller_base_url is not None:
            logger.warning(
                "Ignoring caller-supplied base_url for streamed profile=%s",
                profile_id,
            )
        if caller_api_key is not None:
            logger.warning(
                "Ignoring caller-supplied api_key for streamed profile=%s",
                profile_id,
            )

        extra_body_obj = provider_kwargs.pop("extra_body", {})
        extra_body = dict(extra_body_obj) if isinstance(extra_body_obj, dict) else {}
        for key in (
            "guided_json",
            "guided_regex",
            "guided_choice",
            "guided_grammar",
            "guided_whitespace_pattern",
        ):
            value = provider_kwargs.pop(key, None)
            if value is not None:
                extra_body[key] = value
        if extra_body:
            provider_kwargs["extra_body"] = extra_body

        import httpx as _httpx

        stream_seconds = _positive_profile_limit(
            profile,
            "max_stream_seconds",
            DEFAULT_MAX_STREAM_SECONDS,
        )
        idle_seconds = _positive_profile_limit(
            profile,
            "max_stream_idle_seconds",
            DEFAULT_MAX_STREAM_IDLE_SECONDS,
        )
        transport_wait = float(min(stream_seconds, idle_seconds))
        init_kwargs["request_timeout"] = _httpx.Timeout(
            connect=min(10.0, transport_wait),
            read=transport_wait,
            write=min(60.0, transport_wait),
            pool=min(10.0, transport_wait),
        )
        init_kwargs.update(provider_kwargs)

        sem = self._stream_provider_semaphore(profile_id)
        if not sem.acquire(timeout=10.0):
            raise RuntimeError(
                f"Stream provider construction for '{profile_id}' timed out "
                f"(all {profile.stream_provider_max_concurrency} slot(s) occupied)"
            )
        try:
            chat_model = provider_cls(**init_kwargs)
            if tools:
                if not hasattr(chat_model, "bind_tools"):
                    raise ValueError(f"Provider for profile '{profile_id}' does not support streamed tools")
                chat_model = chat_model.bind_tools(tools)

            try:
                upstream = iter(chat_model.stream(messages))
            except Exception as exc:
                _redact_url_in_exception(exc, resolved_base_url)
                self.record_timeout_on_failure(profile_id, exc)
                raise
        finally:
            sem.release()

        max_stream_bytes = min(
            _positive_profile_limit(
                profile,
                "max_stream_bytes",
                DEFAULT_MAX_STREAM_BYTES,
            ),
            _positive_profile_limit(
                profile,
                "max_response_bytes",
                DEFAULT_MAX_RESPONSE_BYTES,
            ),
            request_payload_budget.max_response_bytes,
        )
        max_stream_tokens = min(
            _positive_profile_limit(
                profile,
                "max_stream_tokens",
                DEFAULT_MAX_STREAM_TOKENS,
            ),
            _positive_profile_limit(
                profile,
                "max_output_tokens",
                DEFAULT_MAX_OUTPUT_TOKENS,
            ),
            request_payload_budget.max_output_tokens,
        )
        max_stream_chunks = _positive_profile_limit(
            profile,
            "max_stream_chunks",
            DEFAULT_MAX_STREAM_CHUNKS,
        )
        max_decompression_ratio = _positive_profile_limit(
            profile,
            "max_stream_decompression_ratio",
            DEFAULT_MAX_STREAM_DECOMPRESSION_RATIO,
        )
        max_tool_calls = _positive_profile_limit(
            profile,
            "max_tool_calls",
            DEFAULT_MAX_TOOL_CALLS,
        )

        total_bytes = 0
        total_wire_bytes = 0
        total_chunks = 0
        total_tool_calls = 0
        full_content: list[str] = []
        latest_usage: dict[str, object] = {}
        started_at = time.monotonic()
        last_chunk_at = started_at
        completed = False
        try:
            for chunk in upstream:
                now = time.monotonic()
                elapsed = now - started_at
                idle_elapsed = now - last_chunk_at
                # When both limits expire at the same chunk boundary, report
                # the inter-chunk idle breach: it is the more specific cause.
                if idle_elapsed > idle_seconds:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="idle_seconds",
                        actual=max(idle_seconds + 1, math.ceil(idle_elapsed)),
                        limit=idle_seconds,
                        source="provider",
                        count_source="monotonic_clock",
                    )
                if elapsed > stream_seconds:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="duration_seconds",
                        actual=max(stream_seconds + 1, math.ceil(elapsed)),
                        limit=stream_seconds,
                        source="provider",
                        count_source="monotonic_clock",
                    )
                last_chunk_at = now

                try:
                    chunk_content, chunk_bytes, chunk_tool_calls = self._stream_chunk_payload(chunk)
                except Exception as exc:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="bytes",
                        actual=max_stream_bytes + 1,
                        limit=max_stream_bytes,
                        source="provider",
                        count_source="unserializable_stream_chunk",
                    ) from exc

                next_chunks = total_chunks + 1
                next_bytes = total_bytes + chunk_bytes
                next_tool_calls = total_tool_calls + chunk_tool_calls
                if next_chunks > max_stream_chunks:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="chunks",
                        actual=next_chunks,
                        limit=max_stream_chunks,
                        source="provider",
                        count_source="provider_stream_chunks",
                    )
                if next_bytes > max_stream_bytes:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="bytes",
                        actual=next_bytes,
                        limit=max_stream_bytes,
                        source="provider",
                        count_source="retained_stream_utf8",
                    )
                if next_tool_calls > max_tool_calls:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="tool_calls",
                        actual=next_tool_calls,
                        limit=max_tool_calls,
                        source="provider",
                        count_source="provider_stream_tool_fragments",
                    )

                usage_obj = getattr(chunk, "usage_metadata", None)
                if isinstance(usage_obj, dict) and usage_obj:
                    latest_usage = usage_obj
                output_tokens_for_limit, token_source = self._response_token_count(
                    latest_usage,
                    next_bytes,
                )
                if output_tokens_for_limit > max_stream_tokens:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="tokens",
                        actual=output_tokens_for_limit,
                        limit=max_stream_tokens,
                        source="provider",
                        count_source=token_source,
                    )

                if self._stream_wire_byte_counter is not None:
                    try:
                        wire_delta = self._stream_wire_byte_counter(chunk)
                    except Exception as exc:
                        raise StreamLimitError(
                            profile_id=profile_id,
                            stage="response",
                            dimension="decompression_ratio",
                            actual=max_decompression_ratio + 1,
                            limit=max_decompression_ratio,
                            source="provider",
                            count_source="invalid_wire_byte_counter",
                        ) from exc
                    if type(wire_delta) is not int or wire_delta < 0 or (chunk_bytes > 0 and wire_delta == 0):
                        raise StreamLimitError(
                            profile_id=profile_id,
                            stage="response",
                            dimension="decompression_ratio",
                            actual=max_decompression_ratio + 1,
                            limit=max_decompression_ratio,
                            source="provider",
                            count_source="invalid_wire_byte_counter",
                        )
                    ratio_source = "configured_wire_byte_counter"
                else:
                    encoding = self._stream_content_encoding(chunk)
                    if encoding not in {"", "identity"}:
                        raise StreamLimitError(
                            profile_id=profile_id,
                            stage="response",
                            dimension="decompression_ratio",
                            actual=max_decompression_ratio + 1,
                            limit=max_decompression_ratio,
                            source="provider",
                            count_source="compressed_wire_bytes_unavailable",
                        )
                    wire_delta = chunk_bytes
                    ratio_source = "identity_encoding"
                next_wire_bytes = total_wire_bytes + wire_delta
                decompression_ratio = math.ceil(next_bytes / next_wire_bytes) if next_wire_bytes > 0 else 0
                if decompression_ratio > max_decompression_ratio:
                    raise StreamLimitError(
                        profile_id=profile_id,
                        stage="response",
                        dimension="decompression_ratio",
                        actual=decompression_ratio,
                        limit=max_decompression_ratio,
                        source="provider",
                        count_source=ratio_source,
                    )

                total_chunks = next_chunks
                total_bytes = next_bytes
                total_wire_bytes = next_wire_bytes
                total_tool_calls = next_tool_calls
                full_content.append(chunk_content)
                yield chunk
            elapsed = time.monotonic() - started_at
            if elapsed > stream_seconds:
                raise StreamLimitError(
                    profile_id=profile_id,
                    stage="response",
                    dimension="duration_seconds",
                    actual=max(stream_seconds + 1, math.ceil(elapsed)),
                    limit=stream_seconds,
                    source="provider",
                    count_source="monotonic_clock",
                )
            completed = True
        except PayloadLimitError:
            raise
        except Exception as exc:
            _redact_url_in_exception(exc, resolved_base_url)
            self.record_timeout_on_failure(profile_id, exc)
            raise
        finally:
            close = getattr(upstream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug(
                        "Provider stream close failed for profile=%s",
                        profile_id,
                    )

        if not completed:
            return
        output_tokens_for_limit, _ = self._response_token_count(
            latest_usage,
            total_bytes,
        )
        request_payload_budget.reserve_response(
            profile_id,
            response_bytes=total_bytes,
            output_tokens=output_tokens_for_limit,
            tool_calls=total_tool_calls,
        )
        if not "".join(full_content).strip() and total_tool_calls == 0:
            empty_exc = self._empty_response_error(profile_id)
            self.record_timeout_on_failure(profile_id, empty_exc)
            raise empty_exc

        input_tokens = _coerce_token_count(
            latest_usage.get("input_tokens", latest_usage.get("prompt_tokens", input_tokens_for_limit))
        )
        output_tokens = _coerce_token_count(
            latest_usage.get(
                "output_tokens",
                latest_usage.get("completion_tokens", output_tokens_for_limit),
            )
        )
        base_cost = input_tokens * profile.cost_per_input_token + output_tokens * profile.cost_per_output_token

        effective_cost, _rate_info, _multiplier = self._apply_billing_rate(base_cost)

        cost = effective_cost
        if self._budget_guard is not None:
            self._budget_guard.record_spend(cost)
        if self._health_tracker is not None:
            self._health_tracker.record_success(profile_id)
        if self._metrics_collector is not None and self._metrics_agent_id:
            self._metrics_collector.record_model_call(
                agent_id=self._metrics_agent_id,
                model_id=profile_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=True,
                cost_per_input_token=profile.cost_per_input_token,
                cost_per_output_token=profile.cost_per_output_token,
            )
        default_token_tracker().record(work_type, input_tokens, output_tokens)
        if self._langsmith_tracer is not None and self._langsmith_tracer.is_enabled():
            self._langsmith_tracer.trace_call(
                model_name=profile.model_name,
                messages=messages,
                response="".join(full_content),
                tokens={"input": input_tokens, "output": output_tokens},
                cost=cost,
                metadata={
                    "profile_id": profile_id,
                    "provider": provider_name,
                    "work_type": work_type,
                    "project_id": str(project_id) if project_id else "",
                    "streamed": "true",
                },
            )

    def call_model_stream_with_retry(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        correlation_id: str | None = None,
        cancellation_event: threading.Event | None = None,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        **kwargs: Any,
    ) -> list[object]:
        """Stream with tenacity retry on the primary, then walk fallback chain.

        Each retry restarts the stream from scratch (streams are not resumable).
        After exhausting retries on the primary profile, the fallback chain is
        walked, trying each fallback profile in order. On every retry and every
        fallback hop, the provider is reconstructed from scratch — credentials,
        base_url, and all init kwargs are re-resolved so a rotated secret or a
        recovered endpoint is picked up.

        ``cancellation_event`` suppresses every side effect when set before the
        first provider attempt or between retries. ``correlation_id`` is
        threaded through the walker and surfaced on the last exception.

        StreamLimitError and PayloadLimitError are never retried (they indicate
        the request itself is oversized, not a transient provider failure).

        Returns a sync Iterator so callers can iterate with ``for chunk in ...``
        or ``list(...)``. The iterator closes the upstream on any early exit.
        """
        if cancellation_event is not None and cancellation_event.is_set():
            raise CallCancelledError(profile_id)

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")
        kwargs["_request_payload_budget"] = _RequestPayloadBudget.from_profile(profile)
        if cancellation_event is not None:
            kwargs["cancellation_event"] = cancellation_event
        coro = self._call_model_stream_with_retry_async(
            profile_id,
            messages,
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            correlation_id=correlation_id,
            cancellation_event=cancellation_event,
            estimated_cost=estimated_cost,
            budget_remaining=budget_remaining,
            **kwargs,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        return coro  # type: ignore[return-value]

    async def _call_model_stream_with_retry_async(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        correlation_id: str | None = None,
        cancellation_event: threading.Event | None = None,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        tools: list[dict[str, object]] | None = None,
        project_id: str | None = None,
        **kwargs: Any,
    ) -> list[object]:
        """Async core: retry + fallback for streamed model calls."""
        import httpx

        if cancellation_event is not None and cancellation_event.is_set():
            raise CallCancelledError(profile_id)

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        tracker = self._health_tracker
        policy = TimeoutRetryPolicy(
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            # ``max_failover_retries`` counts retries after the initial stream
            # attempt. TimeoutRetryPolicy's threshold counts total attempts.
            failover_after_retries=profile.max_failover_retries + 1,
        )

        # Primary already unhealthy → skip straight to fallbacks.
        if tracker is not None and not tracker.is_healthy(profile_id):
            return await self._stream_walk_fallbacks(
                list(profile.fallback_profiles),
                messages,
                from_profile_id=profile_id,
                tools=tools,
                project_id=project_id,
                estimated_cost=estimated_cost,
                budget_remaining=budget_remaining,
                correlation_id=correlation_id,
                **kwargs,
            )

        _retryable_exc_types: tuple[type[BaseException], ...] = (
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
            TimeoutError,
            ConnectionError,
        )
        try:
            import openai as _openai

            _retryable_exc_types = (
                *_retryable_exc_types,
                _openai.APIConnectionError,
                _openai.APITimeoutError,
                _openai.APIStatusError,
            )
        except Exception:
            pass

        # Non-retryable types: stream limits, payload limits, budget, cancellation.
        _non_retryable: tuple[type[BaseException], ...] = (
            StreamLimitError,
            PayloadLimitError,
            CallCancelledError,
            CircuitBreakerOpenError,
        )

        _attempt_counter: list[int] = [0]
        _last_exc: list[BaseException | None] = [None]

        def _is_retryable(exc: BaseException) -> bool:
            if isinstance(exc, _non_retryable):
                return False
            if cancellation_event is not None and cancellation_event.is_set():
                return False
            if not isinstance(exc, _retryable_exc_types):
                return False
            kind = TimeoutClassifier.classify(exc)
            if kind in _NON_RETRYABLE_KINDS:
                return False
            if (
                kind not in _OVERLOAD_KINDS
                and tracker is not None
                and not tracker.is_healthy(profile_id, admit_probe=False)
            ):
                return False
            effective_cap = policy._overload_max_retries if kind in _OVERLOAD_KINDS else max_retries
            if _attempt_counter[0] > effective_cap:
                return False
            decision = policy.decide(kind, _attempt_counter[0])
            return bool(decision.should_retry)

        async def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if exc is not None and isinstance(exc, _retryable_exc_types):
                kind = TimeoutClassifier.classify(exc)
                is_overload = kind in _OVERLOAD_KINDS
                retry_after = _extract_retry_after_seconds(exc)
                wait_s = policy._compute_backoff(
                    kind,
                    _attempt_counter[0],
                    retry_after,
                    overload=is_overload,
                )
                if wait_s > 0:
                    await asyncio.sleep(min(wait_s, 60.0))

        _exhausted = False
        try:
            async for attempt in tenacity.AsyncRetrying(
                retry=tenacity.retry_if_exception(_is_retryable),
                wait=tenacity.wait_none(),
                stop=tenacity.stop_after_attempt(policy._overload_max_retries),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with attempt:
                    _attempt_counter[0] = attempt.retry_state.attempt_number
                    if cancellation_event is not None and cancellation_event.is_set():
                        raise CallCancelledError(profile_id)
                    try:
                        return await asyncio.to_thread(
                            lambda: list(
                                self.call_model_stream(
                                    profile_id,
                                    messages,
                                    estimated_cost=estimated_cost,
                                    budget_remaining=budget_remaining,
                                    tools=tools,
                                    project_id=project_id,
                                    **kwargs,
                                )
                            )
                        )
                    except _non_retryable:
                        raise
                    except _retryable_exc_types as exc:
                        _last_exc[0] = exc
                        self.record_timeout_on_failure(profile_id, exc)
                        kind = TimeoutClassifier.classify(exc)
                        if kind in _NON_RETRYABLE_KINDS:
                            raise
                        raise
        except _retryable_exc_types as exc:
            _last_exc[0] = exc
            _exhausted = True

        if not _exhausted:
            raise RuntimeError("stream failover path exited without return or raise")

        return await self._stream_walk_fallbacks(
            list(profile.fallback_profiles),
            messages,
            from_profile_id=profile_id,
            from_error=_last_exc[0],
            tools=tools,
            project_id=project_id,
            estimated_cost=estimated_cost,
            budget_remaining=budget_remaining,
            correlation_id=correlation_id,
            **kwargs,
        )

    async def _stream_walk_fallbacks(
        self,
        fallback_ids: list[str],
        messages: list[dict[str, str]],
        *,
        from_profile_id: str,
        from_error: BaseException | None = None,
        correlation_id: str | None = None,
        tools: list[dict[str, object]] | None = None,
        project_id: str | None = None,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        **kwargs: Any,
    ) -> list[object]:
        """Walk the fallback chain for streamed calls.

        Each fallback is attempted via ``call_model_stream``. On success, the
        stream is materialized and returned as a list. On failure, the next fallback is tried.
        Cycle-safe: already-visited profiles are skipped. Health-gated: unhealthy
        fallbacks are skipped with a timeout check.
        """
        import math as _math

        tracker = self._health_tracker
        last_exc = from_error
        attempts: list[dict[str, str]] = []
        visited: set[str] = {from_profile_id}
        queue: list[str] = list(fallback_ids)
        depth: int = 0
        prev_id: str | None = from_profile_id

        while queue:
            fb_id = queue.pop(0)
            depth += 1
            if depth > self._max_fallback_depth:
                continue
            if fb_id in visited:
                continue
            visited.add(fb_id)

            if tracker is not None and not _is_healthy_with_timeout(tracker, fb_id):
                continue

            if (
                estimated_cost > 0.0
                and not _math.isinf(estimated_cost)
                and not self.check_budget(fb_id, estimated_cost, budget_remaining, messages=messages)
            ):
                attempts.append(
                    {
                        "profile_id": fb_id,
                        "reason": (
                            f"budget exceeded before stream attempt "
                            f"(estimated={estimated_cost}, remaining={budget_remaining})"
                        ),
                    }
                )
                last_exc = BudgetExceededError(
                    f"Fallback '{fb_id}' estimated cost {estimated_cost} exceeds remaining budget {budget_remaining}"
                )
                continue

            if prev_id is not None:
                self._record_failover(
                    prev_id,
                    fb_id,
                    sanitize_error_message(str(last_exc)) if last_exc is not None else "",
                    exception_type=type(last_exc).__qualname__ if last_exc is not None else None,
                )

            try:

                def _stream_call(fb: str = fb_id) -> list[object]:
                    return list(
                        self.call_model_stream(
                            fb,
                            messages,
                            estimated_cost=estimated_cost,
                            budget_remaining=budget_remaining,
                            tools=tools,
                            project_id=project_id,
                            **kwargs,
                        )
                    )

                result = await asyncio.to_thread(_stream_call)
                return result
            except StreamLimitError:
                raise
            except PayloadLimitError:
                raise
            except BudgetExceededError:
                raise
            except SSRFRejectionError:
                raise
            except ModelPausedError:
                raise
            except CallCancelledError:
                raise
            except Exception as exc:
                self.record_timeout_on_failure(fb_id, exc)
                last_exc = exc
                attempts.append({"profile_id": fb_id, "reason": sanitize_error_message(str(exc))})
                prev_id = fb_id
                next_profile = self._profiles.get(fb_id)
                if next_profile is not None:
                    for nxt in next_profile.fallback_profiles:
                        if nxt not in visited and nxt not in queue:
                            queue.append(nxt)
                continue

        last = last_exc or from_error
        if last is not None:
            primary_reason = sanitize_error_message(str(from_error)) if from_error is not None else "unknown"
            full_attempts = [{"profile_id": from_profile_id, "reason": primary_reason}, *attempts]
            _enrich_all_down_message(last, full_attempts)
            raise RuntimeError(str(last)) from last
        raise RuntimeError(f"_stream_walk_fallbacks: all stream fallbacks failed for '{from_profile_id}'")

    def _stream_walk_fallbacks_sync(
        self,
        fallback_ids: list[str],
        messages: list[dict[str, str]],
        *,
        from_profile_id: str,
        from_error: BaseException | None = None,
        **kwargs: Any,
    ) -> list[object]:
        """Synchronous fallback walk for stream calls (no-asyncio path)."""
        try:
            asyncio.get_running_loop()
            coro = self._stream_walk_fallbacks(
                fallback_ids,
                messages,
                from_profile_id=from_profile_id,
                from_error=from_error,
                **kwargs,
            )
            return coro  # type: ignore[return-value]
        except RuntimeError:
            return asyncio.run(
                self._stream_walk_fallbacks(
                    fallback_ids,
                    messages,
                    from_profile_id=from_profile_id,
                    from_error=from_error,
                    **kwargs,
                )
            )

    def _resolver_for_project(self, project_id: str | None) -> _SecretsResolver | None:
        """Return the secrets resolver scoped to ``project_id`` when possible.

        S-1 wiring (task #25): the daemon injects a project-aware resolver
        (``daemon._LazyProjectSecrets``, exposing ``for_project``) as
        ``self._secrets``. When a job carries a ``project_id`` and that resolver
        is project-capable, return ``resolver.for_project(project_id)`` — a
        :class:`ProjectSecretsManager` that resolves ``projects/<id>/<alias>``
        first and only then falls back to the shared base, so a job can never
        read another project's scoped secret.

        Falls back to the shared base resolver (the previous, unscoped behavior)
        only when doing so cannot widen a scoped request:

        * ``project_id`` is ``None``/empty (a non-project call), or
        * the injected resolver has no ``for_project`` (e.g. a plain
          ``EnvSecretsManager`` when projects are inactive).

        If ``for_project`` rejects the id, fail closed by returning ``None`` so
        the model call cannot silently widen to shared credentials.
        """
        base = self._secrets
        if base is None or not project_id:
            return base
        for_project = getattr(base, "for_project", None)
        if not callable(for_project):
            return base
        try:
            return cast(_SecretsResolver, for_project(project_id))
        except Exception:
            logger.warning(
                "project-scoped secrets unavailable for project_id=%r; "
                "refusing to fall back to shared resolver (fail-closed)",
                project_id,
            )
            return None

    def _invoke_and_bill(
        self,
        profile: ModelProfile,
        profile_id: str,
        messages: list[dict[str, str]],
        cache_key: str | None,
        *,
        request_payload_budget: _RequestPayloadBudget,
        request_bytes: int,
        input_tokens: int,
        **kwargs: Any,
    ) -> ModelResponse:
        # Reserve all outbound dimensions atomically before provider lookup or
        # construction. Exhaustion therefore cancels the next retry/fallback hop
        # without opening credentials, allocating a client, or emitting metrics.
        request_payload_budget.reserve_provider_attempt(
            profile_id,
            request_bytes=request_bytes,
            input_tokens=input_tokens,
        )
        provider_name = profile.provider
        registry = self._registry

        if registry is not None and not registry.is_installed(provider_name):
            registry.install_provider(provider_name)
            raise ImportError(
                f"Provider '{provider_name}' is not installed. A dependency update todo has been created."
            )

        # S-1 (per-project secret isolation, task #25): when this job carries a
        # project_id and the injected resolver is project-aware (exposes
        # ``for_project``), resolve THIS job's credential/api-base aliases through
        # the project-scoped ProjectSecretsManager wrapper — which reads
        # ``projects/<id>/<alias>`` first and only then falls back to the shared
        # base — so one project can never read another project's scoped secret.
        # Pop ``project_id`` HERE (like ``work_type`` below) so it is never
        # forwarded to the provider constructor via ``init_kwargs.update(kwargs)``.
        _project_id = kwargs.pop("project_id", None)
        job_secrets = self._resolver_for_project(str(_project_id) if _project_id else None)

        api_key: str | None = None
        if job_secrets and profile.credential_alias:
            api_key = job_secrets.resolve(profile.credential_alias)

        if registry is not None:
            provider_cls = registry.get_provider_class(provider_name)
        else:
            raise ValueError(f"No provider registry configured for '{profile_id}'")

        # Pop `tools` BEFORE updating init_kwargs so it is never forwarded to
        # the LangChain provider constructor (ChatOpenAI et al. do not accept a
        # `tools=` constructor arg — passing it there raises a TypeError that is
        # silently swallowed upstream and produces zero tool-use behaviour).
        # Tools are bound to the *invocation* via chat_model.bind_tools() below.
        tools = kwargs.pop("tools", None)

        # Pop `work_type` BEFORE updating init_kwargs so it is never forwarded to
        # the LangChain provider constructor (which would raise TypeError). It is
        # the BARE task-kind string environment_advisor.classify() consumes, and
        # is used below to record real per-work_type token consumption at this
        # billing chokepoint — the single path EVERY model call flows through.
        # Coerce to a non-empty str: a caller may pass work_type=None explicitly
        # (e.g. the admin endpoint whose body lacks the field), and the `"unknown"`
        # default does NOT fire when the key is present-with-None — recording a
        # `None` key would create a dead bucket classify() can never resolve.
        _work_type = kwargs.pop("work_type", "unknown")
        work_type = str(_work_type) if _work_type else "unknown"

        init_kwargs: dict[str, object] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        base_url: str | None = None
        _local = (
            os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"
            or profile_id.lower().startswith("local-")
            or profile_id.lower().startswith("ollama-")
        )
        if profile.api_base_alias and job_secrets:
            base_url = job_secrets.resolve(profile.api_base_alias)
        if not base_url and _local:
            base_url = os.environ.get("LOCAL_MODEL_BASE_URL", "http://localhost:11434/v1")
        if base_url:
            if _local:
                init_kwargs["base_url"] = base_url
            else:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise SSRFRejectionError(
                        f"SSRF guard: refusing blocked api_base_alias URL (redacted) for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url
        _resolved_base_url: str | None = cast(str | None, init_kwargs.get("base_url"))
        extra_body: dict[str, object] = kwargs.pop("extra_body", {})
        for key in ("guided_json", "guided_regex", "guided_choice", "guided_grammar", "guided_whitespace_pattern"):
            val = kwargs.pop(key, None)
            if val is not None:
                extra_body[key] = val
        if extra_body:
            kwargs["extra_body"] = extra_body

        # C6 hardening: caller-supplied base_url / api_key in **kwargs are
        # STRIPPED and DENIED outright — they must NEVER override the alias-
        # resolved, SSRF-validated values set above. The caller has no business
        # supplying endpoint credentials; they MUST flow from the configured
        # secrets alias. Previously a "safe" caller base_url was re-validated
        # and accepted, creating a path for a caller to redirect traffic to an
        # arbitrary host as long as it passed the SSRF guard. That path is now closed.
        caller_base_url = kwargs.pop("base_url", None)
        caller_api_key = kwargs.pop("api_key", None)
        caller_request_timeout_supplied = "request_timeout" in kwargs
        caller_timeout_supplied = "timeout" in kwargs
        kwargs.pop("request_timeout", None)
        kwargs.pop("timeout", None)
        if caller_base_url is not None:
            logger.warning(
                "Ignoring caller-supplied base_url in kwargs for profile=%s "
                "(base_url must come from the configured api_base_alias only)",
                profile_id,
            )
        if caller_api_key is not None:
            logger.warning(
                "Ignoring caller-supplied api_key in kwargs for profile=%s "
                "(credentials come from the configured secrets alias only)",
                profile_id,
            )
        if caller_request_timeout_supplied or caller_timeout_supplied:
            logger.warning(
                "Ignoring caller-supplied timeout override for profile=%s "
                "(request deadlines are gateway-owned)",
                profile_id,
            )

        # C6 hardening: default httpx timeout so a hung provider never blocks a
        # thread indefinitely. The underlying LangChain ChatOpenAI passes
        # request_timeout directly to httpx.Timeout, giving us a connect cap
        # (fast failure on unreachable hosts) + a generous read cap (slow
        # streaming is expected from large-context models).
        init_kwargs["request_timeout"] = _default_provider_request_timeout()

        init_kwargs.update(kwargs)

        chat_model = provider_cls(**init_kwargs)

        # Bind tools to the invocation (LangChain pattern) when the caller
        # passed a non-empty tools list.  bind_tools() returns a new runnable
        # that adds the tool schemas to every .invoke() call — this is the only
        # supported way to pass tools through LangChain without putting them on
        # the constructor.  We guard with hasattr so a provider that doesn't
        # implement bind_tools (e.g. a custom stub) falls back gracefully to a
        # plain invoke rather than raising AttributeError.
        if tools:
            if hasattr(chat_model, "bind_tools"):
                chat_model = chat_model.bind_tools(tools)
                logger.debug(
                    "Tools bound for profile=%s (%d tool(s))",
                    profile_id,
                    len(tools),
                )
            else:
                logger.warning(
                    "Provider class %s does not support bind_tools — tools=%r will be ignored for profile=%s",
                    type(chat_model).__name__,
                    tools,
                    profile_id,
                )

        logger.debug(
            "Calling model %s (profile=%s, provider=%s) with api_key=***REDACTED***",
            profile.model_name,
            profile_id,
            provider_name,
        )

        lc_messages = messages
        try:
            raw_response = chat_model.invoke(lc_messages)
        except Exception as exc:
            _redact_url_in_exception(exc, _resolved_base_url or "")
            self.record_timeout_on_failure(profile_id, exc)
            # Surface the failure in the metrics facet too (previously ONLY the
            # health tracker recorded primary/fallback failures, so an operator
            # could not see per-profile error_count from /api/facts). Best-effort:
            # a metrics hiccup must never mask the real provider failure below.
            # See docs/audit/FAILOVER_GAPS.md (failover-metrics-facets).
            if self._metrics_collector is not None and self._metrics_agent_id:
                try:
                    self._metrics_collector.record_model_call(
                        agent_id=self._metrics_agent_id,
                        model_id=profile_id,
                        input_tokens=0,
                        output_tokens=0,
                        success=False,
                        cost_per_input_token=profile.cost_per_input_token,
                        cost_per_output_token=profile.cost_per_output_token,
                        error=sanitize_error_message(str(exc)),
                    )
                except Exception:  # pragma: no cover - metrics must never break the call
                    logger.debug(
                        "metrics_collector.record_model_call(failure) failed",
                        exc_info=True,
                    )
            raise

        content = str(getattr(raw_response, "content", str(raw_response)))
        usage_obj = getattr(raw_response, "usage_metadata", {}) or {}
        usage = usage_obj if isinstance(usage_obj, dict) else {}

        # Count the raw list BEFORE normalization so malformed/name-less calls
        # cannot evade the hard count merely because normalization drops them.
        raw_tool_calls = getattr(raw_response, "tool_calls", None)
        raw_tool_call_count = len(raw_tool_calls) if isinstance(raw_tool_calls, (list, tuple)) else 0

        # Extract tool calls BEFORE the empty-200 guard: a tool-call turn
        # legitimately has empty text content (the model's "output" IS the tool
        # request), so it must NOT be misclassified as an empty-200 soft failure.
        tool_calls = _extract_tool_calls(raw_response)

        # D-30 phase one: this is deliberately before every bill, success
        # metric, token tracker, trace and cache write. An oversized provider
        # result is observed only as this bounded typed exception.
        response_bytes, output_tokens_for_limit = self._enforce_response_limits(
            profile,
            profile_id,
            content=content,
            usage=usage,
            raw_tool_call_count=raw_tool_call_count,
            tool_calls=tool_calls,
            source="provider",
        )
        request_payload_budget.reserve_response(
            profile_id,
            response_bytes=response_bytes,
            output_tokens=output_tokens_for_limit,
            tool_calls=raw_tool_call_count,
        )

        # Empty-200 guard: some providers return HTTP 200 with empty content on
        # a soft/transient failure. Bill+cache BOTH happened AFTER this point, so
        # an empty 200 was being billed (record_spend) and (until the content
        # guard at cache-set) could leak into spend metrics. Raise a RETRYABLE
        # PROVIDER_ERROR BEFORE any record_spend so an empty 200 is never billed,
        # never metered, and never cached — and is retried/failed-over like any
        # other transient provider error. record_timeout_on_failure records it
        # as PROVIDER_ERROR (5xx) on the health tracker.
        # EXEMPTION: empty content WITH tool calls is a valid tool-call turn, not
        # a soft failure — billing it and returning it is correct.
        if not content.strip() and not tool_calls:
            empty_exc = self._empty_response_error(profile_id)
            self.record_timeout_on_failure(profile_id, empty_exc)
            raise empty_exc

        # Provider-controlled usage data: never trust it for billing.
        # - reject bool (isinstance(True, int) is True) so it counts as 0, not 1
        # - clamp at >= 0 so a negative count can never produce a negative cost
        #   (a negative cost would CREDIT the budget guard and bypass the ceiling)
        # - accept OpenAI-style key names as fallbacks so whole provider families
        #   are not silently metered at $0
        input_tokens = _coerce_token_count(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
        output_tokens = _coerce_token_count(usage.get("output_tokens", usage.get("completion_tokens", 0)))
        base_cost = input_tokens * profile.cost_per_input_token + output_tokens * profile.cost_per_output_token

        effective_cost, rate_info, multiplier = self._apply_billing_rate(base_cost)

        cost = effective_cost

        logger.debug(
            "Model call complete: profile=%s, input_tokens=%s, output_tokens=%s, "
            "base_cost=%.6f, rate=%s(x%.2f), effective_cost=%.6f",
            profile_id,
            input_tokens,
            output_tokens,
            base_cost,
            rate_info,
            multiplier,
            effective_cost,
        )

        if self._budget_guard is not None:
            self._budget_guard.record_spend(cost)

        # SUCCESS-RESET: a billed success is, by definition, a healthy call, so
        # reset the breaker's consecutive-failure counter for this profile here —
        # on EVERY entry path (direct call_model, single-flight, fallback). The
        # previous reset lived only in call_model_with_retry/_call_fallback, so a
        # plain call_model() success never cleared the breaker and a profile could
        # stay tripped after it had already recovered. record_success is the
        # inverse of the single failure recording done by record_timeout_on_failure.
        if self._health_tracker is not None:
            self._health_tracker.record_success(profile_id)

        if self._metrics_collector is not None and self._metrics_agent_id:
            self._metrics_collector.record_model_call(
                agent_id=self._metrics_agent_id,
                model_id=profile_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                success=True,
                cost_per_input_token=profile.cost_per_input_token,
                cost_per_output_token=profile.cost_per_output_token,
            )

        # Record real per-work_type token consumption at the billing chokepoint so
        # gludd LEARNS which task KINDS are token-heavy vs light across EVERY call
        # path (daemon generation, worker, ToolCallLoop, reviewer, SLM, langgraph)
        # — not just the daemon generation branch the old event_loop capture saw.
        # Cache hits return before _invoke_and_bill and empty-200s raise before the
        # record above, so both correctly record nothing. Key on the BARE work_type
        # so environment_advisor.classify(work_type) can resolve what was recorded.
        default_token_tracker().record(work_type, input_tokens, output_tokens)

        # LangSmith tracing: side-channel observability, additive and non-blocking.
        # Only fires when the tracer is configured (LANGSMITH_API_KEY +
        # LANGSMITH_PROJECT) and does not affect control flow on failure.
        if self._langsmith_tracer is not None:
            tracer = self._langsmith_tracer
            if tracer.is_enabled():
                tracer.trace_call(
                    model_name=profile.model_name,
                    messages=messages,
                    response=content,
                    tokens={"input": input_tokens, "output": output_tokens},
                    cost=cost,
                    metadata={
                        "profile_id": profile_id,
                        "provider": provider_name,
                        "work_type": work_type,
                        "project_id": str(_project_id) if _project_id else "",
                    },
                )

        response = ModelResponse(
            content=content,
            usage_metadata=dict(usage),
            cost_estimate=cost,
            model_name=profile.model_name,
            raw_response=raw_response,
            # Carry the provider's tool calls forward (normalized) so the MCP
            # ToolCallLoop can actually dispatch them. Without this they survive
            # only inside raw_response, which the loop never inspects, and the
            # entire tool-call loop is dead in production.
            tool_calls=tool_calls,
        )

        # content is non-empty (the empty-200 guard above raised otherwise), so
        # we never cache an error-shaped "successful" response here. cache_key is
        # the same key the single-flight path locked on, so the entry the next
        # waiter re-reads is exactly this one.
        #
        # NEVER cache a tool-call turn: a response carrying tool_calls is an
        # INCOMPLETE turn (the model asked to run tools; the real answer comes on
        # the next iteration after tool results are fed back). The cache stores
        # only content (tool_calls are intentionally NOT persisted), so a cached
        # tool-call turn would replay as an empty-content final answer and the
        # tool loop would silently stop dispatching. Skip caching it entirely.
        #
        # GW-2: NEVER cache a TRUNCATED turn. A response cut off at the token
        # limit (finish_reason == "length") is an incomplete answer; caching it
        # would replay the truncated text as if it were the full response on
        # every identical future request. The marker lives in the provider's
        # LangChain AIMessage metadata, not on ModelResponse, so read it there.
        finish_reason = (getattr(raw_response, "response_metadata", None) or {}).get("finish_reason")
        if (
            self._response_cache is not None
            and cache_key is not None
            and not response.tool_calls
            and finish_reason != "length"
        ):
            self._response_cache.set(
                cache_key,
                {
                    "content": response.content,
                    "usage_metadata": response.usage_metadata,
                    "cost_estimate": response.cost_estimate,
                    "model_name": response.model_name,
                },
                expire=self._response_cache_ttl_seconds,
            )

        return response

    @staticmethod
    def _empty_response_error(profile_id: str) -> Exception:
        """Build a RETRYABLE provider error for an empty-content 200 response.

        An httpx.HTTPStatusError with status 503 is used so that (a)
        TimeoutClassifier.classify() labels it PROVIDER_ERROR, and (b) the
        tenacity retry predicate in call_model_with_retry treats it as
        retryable (it is an httpx.HTTPStatusError) — so an empty 200 is retried
        and ultimately failed-over, never billed.
        """
        import httpx

        request = httpx.Request("POST", "https://empty-200.invalid/v1/chat")
        response = httpx.Response(
            503,
            request=request,
            text=f"empty 200 from provider for profile '{profile_id}'",
        )
        return httpx.HTTPStatusError(
            f"empty-content 200 response from profile '{profile_id}' "
            "(treated as a retryable provider error; not billed)",
            request=request,
            response=response,
        )

    def call_model_with_retry(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        correlation_id: str | None = None,
        cancellation_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call a profile through the bounded asynchronous retry policy."""
        if cancellation_event is not None and cancellation_event.is_set():
            raise CallCancelledError(profile_id)
        coro = self._call_model_with_retry_async(
            profile_id,
            messages,
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            correlation_id=correlation_id,
            cancellation_event=cancellation_event,
            **kwargs,
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        return coro

    async def _call_model_with_retry_async(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
        correlation_id: str | None = None,
        cancellation_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Retry a model call using tenacity with TimeoutRetryPolicy semantics.

        Retry strategy (ported from hand-rolled loop via TimeoutRetryPolicy):
        - AUTH_ERROR / CONTEXT_LENGTH: not retryable, re-raise immediately.
        - All other retryable exceptions (HTTPStatusError, TimeoutException,
          ConnectError, TimeoutError): exponential backoff with jitter per
          TimeoutRetryPolicy._compute_backoff; max_backoff=60s.
        - After failover_after_retries (3) attempts: stop retrying on the
          primary profile and walk the fallback_profiles chain.
        - Health tracker: records timeout events and checks profile health
          before attempting; unhealthy primary → skip to fallbacks immediately.

        ``cancellation_event`` (optional): if set before the first provider
        attempt or between retries, the call is rejected immediately with
        ``CallCancelledError`` — no provider construction, billing, or side
        effects occur.

        ``correlation_id`` (optional): when supplied, it is stamped onto the
        returned ``ModelResponse.correlation_id`` regardless of which profile
        in the chain ultimately served the call — so a caller can trace a
        single logical request across a primary -> secondary -> ... failover.
        It is a keyword-only, gateway-local concern and is deliberately kept
        out of ``**kwargs`` (never forwarded to the provider constructor).
        See docs/audit/FAILOVER_GAPS.md (correlation-id-propagation).
        """
        if cancellation_event is not None and cancellation_event.is_set():
            raise CallCancelledError(profile_id)

        import httpx

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")
        # One finite ledger follows this logical request through every tenacity
        # retry and every fallback hop. Internal callers cannot replace it via
        # kwargs; the initiating profile owns the request-wide configuration.
        kwargs["_request_payload_budget"] = _RequestPayloadBudget.from_profile(profile)
        if cancellation_event is not None:
            kwargs["cancellation_event"] = cancellation_event
        tracker = self._health_tracker
        policy = TimeoutRetryPolicy(
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
            failover_after_retries=profile.max_failover_retries,
        )

        # If primary is already unhealthy, skip straight to fallbacks. Each
        # fallback attempt goes through _call_fallback so its circuit is honored
        # and its success/failure is recorded on the health tracker.
        if tracker is not None and not tracker.is_healthy(profile_id):
            fallback_ids = list(profile.fallback_profiles)
            result, last_fb_exc, attempts = self._walk_fallbacks(
                fallback_ids, messages, from_profile_id=profile_id, **kwargs
            )
            if result is not None:
                return _attach_correlation_id(result, correlation_id)
            if last_fb_exc is not None:
                _enrich_all_down_message(last_fb_exc, attempts)
                raise last_fb_exc from None
            if fallback_ids:
                # All fallbacks failed is_healthy and none was attempted: probe
                # the first as a last resort (records its own success/failure).
                return _attach_correlation_id(
                    self._call_fallback(fallback_ids[0], messages, **kwargs),
                    correlation_id,
                )
            # CIRCUIT-BREAKER HOLE FIX: primary is unhealthy AND there are no
            # fallbacks — falling through to the retry loop would hammer the
            # unhealthy primary instead of failing fast. Raise the same error
            # type used when all fallbacks are exhausted so callers get a
            # consistent, informative signal.
            raise RuntimeError(
                f"call_model_with_retry: profile '{profile_id}' is unhealthy and no fallback profiles are configured"
            )

        _retryable_exc_types: tuple[type[BaseException], ...] = (
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
            TimeoutError,
        )
        # The openai SDK (used by langchain_openai, hence by z.ai and every
        # openai-compatible provider) wraps connection/timeout/status failures in
        # its OWN exception classes rather than raising raw httpx errors. Without
        # adding them here the tenacity retry predicate never matched a real
        # provider connection error, so the primary re-raised and the fallback
        # chain was never walked. TimeoutClassifier.classify already knows how to
        # categorize these (see _classify_openai_error); we only need them to be
        # recognized as candidate retryable types here. APIStatusError is included
        # so a retryable 5xx/429 surfaced via the SDK is retried/failed-over too;
        # AUTH_ERROR / CONTEXT_LENGTH / INVALID_REQUEST among them still classify
        # NON_RETRYABLE and are re-raised by _is_retryable, preserving semantics.
        try:
            import openai as _openai

            _retryable_exc_types = (
                *_retryable_exc_types,
                _openai.APIConnectionError,
                _openai.APITimeoutError,
                _openai.APIStatusError,
            )
        except Exception:  # pragma: no cover - openai always present in practice
            pass

        _attempt_counter: list[int] = [0]
        _last_exc: list[BaseException | None] = [None]
        # Cumulative-backoff cap: backoff sleep now uses asyncio.sleep so the
        # event loop is not blocked during retry waits. With overload_max_retries=10
        # and overload_max_backoff=120s the worst-case cumulative sleep is 10x120s =
        # 1200s (~20 minutes) of retries. We track total wall-clock time spent
        # sleeping and stop sleeping once the cap is exceeded, letting the retry
        # exhaust naturally (tenacity continues retrying, but immediately rather
        # than waiting). The cap is intentionally generous (300s = 5 minutes) so
        # that legitimate overload back-pressure is still respected for the first
        # few retries.
        _MAX_CUMULATIVE_SLEEP_S: float = 300.0
        _cumulative_sleep_s: list[float] = [0.0]

        def _is_retryable(exc: BaseException) -> bool:
            """Tenacity retry predicate: True → retry, False → re-raise.

            The hard cap is kind-aware: overload kinds (PROVIDER_ERROR /
            RATE_LIMITED) honor the dedicated ``overload_max_retries`` budget
            (default 10), while transient kinds honor the caller-supplied
            ``max_retries`` (default 3). A blanket ``max_retries`` cap for all
            kinds defeated the overload budget — overload retries stopped at
            attempt 4 instead of 10, never giving an overloaded provider time
            to recover.
            """
            if not isinstance(exc, _retryable_exc_types):
                return False
            kind = TimeoutClassifier.classify(exc)
            # Non-retryable kinds: immediate re-raise.
            if kind in _NON_RETRYABLE_KINDS:
                return False
            # Mid-loop circuit-open guard for transient kinds only: overload
            # kinds (PROVIDER_ERROR, RATE_LIMITED) are expected to fail
            # repeatedly while the provider recovers. The overload budget
            # (10 retries) is the appropriate breaker — the per-model
            # failure_threshold (3) tripping here would defeat it.
            if (
                kind not in _OVERLOAD_KINDS
                and tracker is not None
                and not tracker.is_healthy(profile_id, admit_probe=False)
            ):
                return False
            # Kind-aware hard cap: overload kinds use the dedicated overload
            # budget; transient kinds use the caller's max_retries.
            effective_cap = policy._overload_max_retries if kind in _OVERLOAD_KINDS else max_retries
            if _attempt_counter[0] > effective_cap:
                return False
            decision = policy.decide(kind, _attempt_counter[0])
            return bool(decision.should_retry)

        async def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
            """Perform policy-computed backoff sleep between retry attempts.

            DOUBLE-COUNT FIX: this callback used to also record a TimeoutEvent on
            the health tracker. But the failing attempt has ALREADY recorded the
            same failure exactly once: call_model -> _invoke_and_bill ->
            record_timeout_on_failure (or the empty-200 guard) fires on the
            provider exception before it propagates here. Recording it a second
            time in _before_sleep double-counted every retryable failure, so the
            breaker's consecutive counter advanced two-per-failure and tripped at
            half the real failure_threshold. We now ONLY compute and perform the
            backoff sleep here; the single source of truth for failure recording
            is record_timeout_on_failure on the call_model path.
            """
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if exc is not None and isinstance(exc, _retryable_exc_types):
                kind = TimeoutClassifier.classify(exc)
                is_overload = kind in _OVERLOAD_KINDS
                # D-23: thread the server-supplied Retry-After header through
                # to the backoff policy. Without this, a 429 carrying
                # "Retry-After: 30" was ignored — the policy fell through to
                # the fixed exponential backoff instead of honoring the
                # provider's directed wait. _compute_backoff returns
                # max(retry_after, 1.0) for RATE_LIMITED when this is set.
                retry_after = _extract_retry_after_seconds(exc)
                wait_s = policy._compute_backoff(
                    kind,
                    _attempt_counter[0],
                    retry_after,
                    overload=is_overload,
                )
                if wait_s > 0:
                    remaining_budget = _MAX_CUMULATIVE_SLEEP_S - _cumulative_sleep_s[0]
                    if remaining_budget <= 0:
                        # Cumulative cap exhausted: skip sleep, retry immediately.
                        logger.debug(
                            "backoff cap reached (%.0fs total); retrying immediately",
                            _cumulative_sleep_s[0],
                        )
                    else:
                        actual_sleep = min(wait_s, remaining_budget)
                        await asyncio.sleep(actual_sleep)
                        _cumulative_sleep_s[0] += actual_sleep

        # Overload kinds (PROVIDER_ERROR, RATE_LIMITED) use the higher retry cap
        # so tenacity doesn't stop too early on the primary before policy can
        # exhaust the overload budget.
        _exhausted = False
        try:
            async for attempt in tenacity.AsyncRetrying(
                retry=tenacity.retry_if_exception(_is_retryable),
                wait=tenacity.wait_none(),
                stop=tenacity.stop_after_attempt(policy._overload_max_retries),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with attempt:
                    _attempt_counter[0] = attempt.retry_state.attempt_number
                    try:
                        result = await asyncio.to_thread(
                            self.call_model,
                            profile_id,
                            messages,
                            _skip_health_check=True,
                            **kwargs,
                        )
                        # NOTE: record_success is already called inside
                        # _invoke_and_bill (via call_model) on every billed
                        # success. A second call here would double-count and
                        # trip the breaker at half the configured threshold.
                        return _attach_correlation_id(result, correlation_id)
                    except _retryable_exc_types as exc:
                        _last_exc[0] = exc
                        kind = TimeoutClassifier.classify(exc)
                        if kind in _NON_RETRYABLE_KINDS:
                            # Non-retryable: re-raise immediately. Do NOT record
                            # here — record_timeout_on_failure (via _invoke_and_bill)
                            # already recorded exactly one TimeoutEvent for this
                            # failure; a second record_event would double-count and
                            # trip the breaker at half the configured threshold.
                            raise
                        raise
        except _retryable_exc_types as exc:
            # Tenacity exhausted retries on primary and re-raised last exception.
            _last_exc[0] = exc
            _exhausted = True

        if not _exhausted:
            # Should not reach here (return or raise should happen above).
            raise RuntimeError("failover path exited without return or raise")

        # Tenacity exhausted (failover_after attempts tried on primary) → walk
        # fallbacks. _walk_fallbacks skips any fallback that fails is_healthy,
        # cascades into each attempted fallback's OWN fallback_profiles when it
        # also fails (so a 3+ profile chain is walked to full exhaustion, not
        # just one hop), and records success/failure for the ones it attempts
        # (Fix 3).
        fallback_ids = list(profile.fallback_profiles)
        result, last_fb_exc, attempts = await asyncio.to_thread(
            self._walk_fallbacks,
            fallback_ids,
            messages,
            from_profile_id=profile_id,
            from_error=_last_exc[0],
            **kwargs,
        )
        if result is not None:
            return _attach_correlation_id(result, correlation_id)

        last = last_fb_exc or _last_exc[0]
        if last is not None:
            # Structured all-providers-down error (test 6b /
            # docs/audit/FAILOVER_GAPS.md structured-all-down-error): enumerate
            # the primary's own exhaustion reason ahead of every fallback hop
            # attempts already recorded, then enrich the exception's message
            # in place (type is preserved) so it names every provider tried.
            primary_reason = sanitize_error_message(str(_last_exc[0])) if _last_exc[0] is not None else "unknown"
            full_attempts = [
                {"profile_id": profile_id, "reason": primary_reason},
                *attempts,
            ]
            _enrich_all_down_message(last, full_attempts)
            raise last from None
        raise RuntimeError(f"call_model_with_retry: all attempts failed for profile '{profile_id}'")

    def _fallback_semaphore(self, fb_id: str) -> threading.Semaphore:
        """Return (creating on first use) the concurrency gate for ``fb_id``.

        Sized from the profile's ``fallback_max_concurrency`` (default 2, or 2
        again for an unknown profile_id — should not happen since ``fb_id``
        always comes from a configured ``fallback_profiles`` entry, but a
        missing profile must not raise here). See docs/audit/FAILOVER_GAPS.md
        (fallback-concurrency-limit).
        """
        with self._fallback_semaphore_lock:
            sem = self._fallback_semaphores.get(fb_id)
            if sem is None:
                profile = self._profiles.get(fb_id)
                limit = profile.fallback_max_concurrency if profile is not None else 2
                sem = threading.Semaphore(max(1, limit))
                self._fallback_semaphores[fb_id] = sem
            return sem

    def _stream_provider_semaphore(self, profile_id: str) -> threading.Semaphore:
        """Return (creating on first use) the serialization gate for ``profile_id``.

        Sized from the profile's ``stream_provider_max_concurrency`` (default 1).
        A missing profile gets a semaphore of 1 (safe default).
        """
        with self._stream_provider_semaphore_lock:
            sem = self._stream_provider_semaphores.get(profile_id)
            if sem is None:
                profile = self._profiles.get(profile_id)
                limit = profile.stream_provider_max_concurrency if profile is not None else 1
                sem = threading.Semaphore(max(1, limit))
                self._stream_provider_semaphores[profile_id] = sem
            return sem

    def _call_fallback(
        self,
        fb_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Call one fallback under its bounded concurrency semaphore.

        The bare fallback-exhaustion loop used to call call_model(fb_id) with no
        is_healthy gate and never recorded the fallback's success or failure, so
        the breaker never tracked fallback health (Fix 3). call_model already
        records both failures (via record_timeout_on_failure on exception) AND
        successes (via _invoke_and_bill → health_tracker.record_success), so no
        additional record_success call is needed here — that would double-count.

        Gated by a per-profile semaphore (test 13a / fallback-concurrency-limit,
        docs/audit/FAILOVER_GAPS.md) so a primary outage cannot thundering-herd
        the fallback: at most ``fb_id``'s configured ``fallback_max_concurrency``
        callers are ever in-flight to it at once; the rest block here until a
        slot frees up.
        """
        sem = self._fallback_semaphore(fb_id)
        if not sem.acquire(timeout=5.0):
            raise RuntimeError(f"fallback capacity exhausted for '{fb_id}' (all {sem._value + 1} slots occupied)")
        try:
            result = self.call_model(fb_id, messages, **kwargs)
        finally:
            sem.release()
        return result

    def _record_failover(
        self,
        from_profile_id: str,
        to_profile_id: str,
        error: str,
        *,
        exception_type: str | None = None,
    ) -> None:
        """Best-effort observability hook fired on every fallback hop walked.

        Wires the gateway-wide ``ModelFailoverChain`` event log (audit trail +
        WARNING log line) and, when a metrics collector is configured,
        increments its global ``failover_count`` facet (surfaced via
        ``get_full_report()["model_usage"]``). A logging/metrics failure must
        never break the actual failover, so both are swallowed.
        See docs/audit/FAILOVER_GAPS.md (failover-metrics-facets).
        """
        try:
            self._failover_log.record_failover(
                from_profile_id,
                to_profile_id,
                error,
                exception_type=exception_type,
            )
        except Exception:  # pragma: no cover - defensive; must never break the call
            logger.debug("failover_log.record_failover failed", exc_info=True)
        collector = self._metrics_collector
        if collector is None:
            return
        record_failover = getattr(collector, "record_failover", None)
        if callable(record_failover):
            try:
                record_failover(from_profile_id, to_profile_id, error)
            except Exception:  # pragma: no cover - defensive; must never break the call
                logger.debug("metrics_collector.record_failover failed", exc_info=True)

    def _walk_fallbacks(
        self,
        fallback_ids: list[str],
        messages: list[dict[str, str]],
        *,
        from_profile_id: str | None = None,
        from_error: BaseException | None = None,
        **kwargs: Any,
    ) -> tuple[ModelResponse | None, BaseException | None, list[dict[str, str]]]:
        """Walk the bounded, cycle-safe fallback graph in configured order.

        Try fallbacks in order, CASCADING into each attempted fallback's own
        configured ``fallback_profiles`` when it also fails, so a 3+ profile
        chain (primary -> secondary -> tertiary -> ...) is walked to full
        exhaustion rather than stopping after one hop. Skips circuit-open
        profiles and never revisits a profile already tried in this walk
        (cycle-safe: a fallback chain that loops back to an earlier profile,
        including ``from_profile_id`` itself, is not retried).

        S.3: before attempting each fallback, pre-checks the health (with a
        timeout) AND the budget (via ``check_budget``). Over-budget and
        unhealthy fallbacks are skipped so the chain can try the next one
        rather than aborting entirely.

        Returns (response, None, attempts) on success, or
        (None, last_exc, attempts) if every reachable profile failed.
        ``attempts`` records, in call order, each profile actually attempted
        (skipped/circuit-open profiles are excluded) with its failure reason —
        used by callers to build a structured "all providers down" error
        enumerating the full chain (docs/audit/FAILOVER_GAPS.md
        structured-all-down-error). Each attempted hop is also recorded via
        ``_record_failover`` for the ``failover_count`` metrics facet
        (docs/audit/FAILOVER_GAPS.md failover-metrics-facets).
        """
        tracker = self._health_tracker
        last_exc: BaseException | None = from_error
        attempts: list[dict[str, str]] = []
        visited: set[str] = {from_profile_id} if from_profile_id else set()
        queue: list[str] = list(fallback_ids)
        prev_id = from_profile_id
        depth: int = 0
        # S.3: extract budget params for per-fallback pre-check
        estimated_cost: float = kwargs.get("estimated_cost", 0.0)
        budget_remaining: float = kwargs.get("budget_remaining", float("inf"))
        while queue:
            fb_id = queue.pop(0)
            depth += 1
            if depth > self._max_fallback_depth:
                continue
            if fb_id in visited:
                continue
            visited.add(fb_id)
            # S.3: health check with timeout (hung tracker = unhealthy)
            if tracker is not None and not _is_healthy_with_timeout(tracker, fb_id):
                continue
            # S.3: budget pre-check before attempting fallback (skip over-budget)
            if (
                estimated_cost > 0.0
                and not math.isinf(estimated_cost)
                and not self.check_budget(fb_id, estimated_cost, budget_remaining, messages=messages)
            ):
                attempts.append(
                    {
                        "profile_id": fb_id,
                        "reason": (
                            f"budget exceeded before attempt (estimated={estimated_cost}, remaining={budget_remaining})"
                        ),
                    }
                )
                last_exc = BudgetExceededError(
                    f"Fallback '{fb_id}' estimated cost {estimated_cost} exceeds remaining budget {budget_remaining}"
                )
                continue
            if prev_id is not None:
                self._record_failover(
                    prev_id,
                    fb_id,
                    sanitize_error_message(str(last_exc)) if last_exc is not None else "",
                    exception_type=type(last_exc).__qualname__ if last_exc is not None else None,
                )
            try:
                result = self._call_fallback(fb_id, messages, **kwargs)
            except PayloadLimitError:
                raise
            except BudgetExceededError:
                raise
            except SSRFRejectionError:
                # F-E: an SSRF egress rejection on the fallback path MUST
                # propagate. _try_call_model already re-raises it, but the bare
                # ``except Exception`` below would re-swallow it here and continue
                # to the next fallback, silently routing past the blocked egress.
                # Re-raise so the SSRF guard hard-stops the chain instead of
                # falling open.
                raise
            except ModelPausedError:
                raise
            except Exception as exc:  # try the next hop (this fallback's own chain)
                last_exc = exc
                attempts.append({"profile_id": fb_id, "reason": sanitize_error_message(str(exc))})
                prev_id = fb_id
                next_profile = self._profiles.get(fb_id)
                if next_profile is not None:
                    for nxt in next_profile.fallback_profiles:
                        if nxt not in visited and nxt not in queue:
                            queue.append(nxt)
                continue
            return result, None, attempts
        return None, last_exc, attempts

    def record_timeout_on_failure(
        self,
        profile_id: str,
        exc: BaseException,
    ) -> None:
        """Classify a provider failure and record it in the health tracker."""
        import time as _time

        from general_ludd.models.timeout_detector import (
            TimeoutClassifier,
            TimeoutEvent,
        )

        if self._health_tracker is None:
            return

        kind = TimeoutClassifier.classify(exc)
        self._health_tracker.record_event(
            TimeoutEvent(
                model_id=profile_id,
                kind=kind,
                timestamp=_time.monotonic(),
                duration_s=0.0,
            )
        )

    def call_model_by_role(
        self,
        role_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Resolve a strict role mapping and call its healthy model profile."""
        if self._router is None:
            raise ValueError("No router configured")
        profile_id = self._router.resolve_role(role_name, strict=True)
        if profile_id is None:
            raise ValueError(f"No profile resolved for role '{role_name}'")
        # Circuit-breaker gate: call_model itself has no health check, so role
        # callers would otherwise bypass an open circuit and hammer an unhealthy
        # provider. Fail fast instead.
        if self._health_tracker is not None and not self._health_tracker.is_healthy(profile_id):
            raise RuntimeError(
                f"profile '{profile_id}' (role '{role_name}') circuit is open; refusing to call unhealthy provider"
            )
        return self.call_model(profile_id, messages, **kwargs)

    def call_model_by_pattern(
        self,
        pattern: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Resolve a configured pattern and call its healthy model profile."""
        if self._router is None:
            raise ValueError("No router configured")
        profile_id = self._router.resolve_pattern(pattern)
        if profile_id is None:
            raise ValueError(f"No profile resolved for pattern '{pattern}'")
        # Circuit-breaker gate: call_model itself has no health check, so pattern
        # callers would otherwise bypass an open circuit and hammer an unhealthy
        # provider. Fail fast instead.
        if self._health_tracker is not None and not self._health_tracker.is_healthy(profile_id):
            raise RuntimeError(
                f"profile '{profile_id}' (pattern '{pattern}') circuit is open; refusing to call unhealthy provider"
            )
        return self.call_model(profile_id, messages, **kwargs)

    def _try_call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse | None:
        if self._health_tracker is not None and not self._health_tracker.is_healthy(profile_id):
            return None
        try:
            return self.call_model(profile_id, messages, **kwargs)
        except PayloadLimitError:
            raise
        except SSRFRejectionError:
            raise
        except BudgetExceededError:
            raise
        except ModelPausedError:
            raise
        except Exception:
            return None

    def call_model_with_fallback(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        fallback_profiles: list[str] | None = None,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        **kwargs: Any,
    ) -> ModelResponse:
        """Call a primary profile and walk its bounded fallback graph on failure."""
        profile = self._profiles.get(profile_id)
        fallback_ids: list[str] = list(fallback_profiles or [])
        if profile is None:
            # Preserve the explicit missing-primary recovery contract while still
            # requiring a configured profile to supply finite cumulative limits.
            # The first configured explicit fallback becomes the immutable policy
            # anchor; unknown fallback IDs cannot silently disable accounting.
            policy_profile = next(
                (
                    candidate
                    for fallback_id in fallback_ids
                    if (candidate := self._profiles.get(fallback_id)) is not None
                ),
                None,
            )
            if policy_profile is None:
                raise ValueError(f"Profile '{profile_id}' not found")
        else:
            policy_profile = profile
            if not fallback_ids:
                fallback_ids = list(profile.fallback_profiles)
        request_payload_budget = _RequestPayloadBudget.from_profile(policy_profile)
        # S.3: gate primary on health tracker before attempting call,
        # using _try_call_model which has its own built-in health gate
        # and properly threads budget params through to call_model.
        primary_healthy = profile is None or self._health_tracker is None or self._health_tracker.is_healthy(profile_id)
        _call_kwargs: dict[str, Any] = {
            **kwargs,
            "estimated_cost": estimated_cost,
            "budget_remaining": budget_remaining,
            "_request_payload_budget": request_payload_budget,
        }
        primary_exc: BaseException | None
        if profile is None:
            primary_exc = ValueError(f"Profile '{profile_id}' not found")
        elif primary_healthy:
            try:
                result = self._try_call_model(profile_id, messages, **_call_kwargs)
                if result is not None:
                    return result
                primary_exc = RuntimeError(f"Primary '{profile_id}' returned None (health or provider error)")
            except SSRFRejectionError:
                raise
            except BudgetExceededError:
                raise
            except ModelPausedError:
                raise
        else:
            primary_exc = None

        all_attempts: list[dict[str, str]] = []
        if primary_exc is not None:
            all_attempts.append({"profile_id": profile_id, "reason": sanitize_error_message(str(primary_exc))})

        # D-04: route fallback walk through _walk_fallbacks (health-gated)
        result, last_exc, fallback_attempts = self._walk_fallbacks(
            fallback_ids,
            messages,
            from_profile_id=profile_id,
            from_error=primary_exc,
            **_call_kwargs,
        )
        if result is not None:
            return result
        all_attempts.extend(fallback_attempts)

        # If primary was tripped AND all fallbacks open/failed -> clear error
        if not primary_healthy:
            cb_error = CircuitBreakerOpenError(f"All circuits open for fallback chain '{profile_id}'")
            _enrich_all_down_message(cb_error, all_attempts)
            raise cb_error

        if last_exc is not None:
            cb_error = CircuitBreakerOpenError(f"All profiles in fallback chain failed for '{profile_id}'")
            _enrich_all_down_message(cb_error, all_attempts)
            raise cb_error from last_exc

        cb_error = CircuitBreakerOpenError(f"All profiles in fallback chain failed for '{profile_id}'")
        _enrich_all_down_message(cb_error, all_attempts)
        raise cb_error

    def _notify_profile_change(
        self,
        event: object,
        hook_name: str,
        hook_payload: dict[str, object],
        action: str,
        model_id: str,
        broadcast_payload: dict[str, object],
    ) -> None:
        """Publish event, fire hook, and broadcast a profile add/remove notification.

        Centralises the three-step fan-out that both add_profile and
        remove_profile require, keeping the order and exception handling
        identical in both callers.
        """
        if self._event_bus:
            self._event_bus.publish(event)
        if self._hooks:
            self._hooks.fire(hook_name, hook_payload)
        if self._broadcaster:
            try:
                self._broadcaster.broadcast_model_update(action, model_id, broadcast_payload)
            except Exception:
                # Broadcaster failures may carry credentials or response fragments.
                # The profile action is sufficient for diagnosis; never log the
                # untrusted exception body at this security boundary.
                logger.warning("Worker broadcast failed for model %s", action)

    @staticmethod
    def select_cost_effective_profile(
        profiles: list[ModelProfile],
        budget_remaining: float,
    ) -> ModelProfile | None:
        """Select the most cost-effective profile within the remaining budget.

        Sorts profiles by effective cost (input + output token price) ascending
        and returns the cheapest one whose ``run_budget_usd`` is not exceeded by
        the remaining budget. If all profiles exceed the remaining budget cap,
        returns None — the caller must handle the budget-exhausted case.

        A profile with ``run_budget_usd == 0.0`` or ``api_metered == False``
        is treated as unconstrained (no per-profile cap) and is eligible if its
        token cost fits within ``budget_remaining``.
        """
        eligible = []
        for p in profiles:
            if not p.enabled:
                continue
            if p.api_metered and p.run_budget_usd > 0.0:
                if budget_remaining >= p.run_budget_usd:
                    eligible.append(p)
            else:
                eligible.append(p)

        if not eligible:
            return None

        eligible.sort(key=lambda p: p.cost_per_input_token + p.cost_per_output_token)
        return eligible[0]

    def add_profile(
        self,
        model_id: str,
        provider: str = "openai",
        model: str = "",
        api_key_env: str | None = None,
        api_base_alias: str | None = None,
        enabled: bool = True,
        **kwargs: Any,
    ) -> ModelProfile:
        """Validate, register, and broadcast one model profile."""
        profile = ModelProfile(
            model_profile_id=model_id,
            provider=provider,
            model_name=model,
            credential_alias=api_key_env,
            api_base_alias=api_base_alias,
            enabled=enabled,
            **{k: v for k, v in kwargs.items() if k in ModelProfile.model_fields},
        )
        self._profiles[model_id] = profile
        self._notify_profile_change(
            event=ModelAddedEvent(model_id=model_id, profile=profile.model_dump()),
            hook_name="on_model_added",
            hook_payload={"model_id": model_id, "profile": profile.model_dump()},
            action="add",
            model_id=model_id,
            broadcast_payload=profile.model_dump(),
        )
        return profile

    def remove_profile(self, model_id: str) -> None:
        """Remove and broadcast one model profile identifier."""
        self._profiles.pop(model_id, None)
        self._notify_profile_change(
            event=ModelRemovedEvent(model_id=model_id),
            hook_name="on_model_removed",
            hook_payload={"model_id": model_id},
            action="remove",
            model_id=model_id,
            broadcast_payload={},
        )
