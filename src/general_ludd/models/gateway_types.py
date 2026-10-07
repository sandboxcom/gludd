"""Stable data contracts, limits, and exceptions for the model gateway.

``general_ludd.models.gateway`` re-exports these names so existing callers keep
their historical import path.  This module deliberately owns no provider
construction or retry orchestration.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from general_ludd.pricing_intel.catalog import PricingCatalog

from pydantic import BaseModel, Field, field_validator, model_validator

from general_ludd.models.timeout_detector import TimeoutEvent

logger = logging.getLogger("general_ludd.models.gateway")

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


def _default_provider_request_timeout(timeout_seconds: float | None = None) -> httpx.Timeout:
    """Return a caller-shortenable, gateway-owned provider deadline.

    The scalar per-call value is applied component-wise so it can shorten any
    of the gateway defaults, but can never widen them.  Keeping construction in
    this helper also means callers cannot replace the structured timeout with an
    arbitrary provider kwarg.
    """
    if timeout_seconds is None:
        return httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a finite positive number")
    timeout = float(timeout_seconds)
    return httpx.Timeout(
        connect=min(10.0, timeout),
        read=min(60.0, timeout),
        write=min(60.0, timeout),
        pool=min(10.0, timeout),
    )


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


@runtime_checkable
class _RuntimeModelGateway(Protocol):
    """A dynamically owned model route behind one stable profile identity."""

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse: ...

    def call_model_stream(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Iterator[object]: ...


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
