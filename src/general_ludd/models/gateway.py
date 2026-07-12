"""Model gateway for LangChain provider management."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import tenacity
from pydantic import BaseModel, Field, field_validator

from general_ludd.events.types import ModelAddedEvent, ModelRemovedEvent
from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.response_cache import _make_cache_key
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    _OVERLOAD_KINDS,
    TimeoutClassifier,
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
    def is_healthy(self, model_id: str, admit_probe: bool = ...) -> bool: ...
    def record_success(self, model_id: str) -> None: ...
    def record_event(self, event: object) -> None: ...


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

    def record_failover(
        self, from_profile: str, to_profile: str, error: str = ""
    ) -> None: ...


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


class ModelProfile(BaseModel):
    model_profile_id: str
    role_names: list[str] = Field(default_factory=list)
    provider: str = "openai"
    provider_package: str = "langchain-openai"
    provider_class_hint: str = "ChatOpenAI"
    model_name: str = ""
    api_base_alias: str | None = None
    credential_alias: str | None = None
    context_window: int = 128000
    max_input_tokens: int = 120000
    max_output_tokens: int = 8000
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

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("model_profile_id must not be empty")
        return v

    @field_validator("context_window", "max_input_tokens", "max_output_tokens", "fallback_max_concurrency")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @field_validator("cost_per_input_token", "cost_per_output_token", "run_budget_usd")
    @classmethod
    def _non_negative_float(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("must be finite non-negative")
        return v


@dataclass
class ModelResponse:
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


def _attach_correlation_id(
    response: ModelResponse, correlation_id: str | None
) -> ModelResponse:
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
        new_args = tuple(
            arg.replace(url, "[REDACTED_URL]") if isinstance(arg, str) else arg
            for arg in exc.args
        )
        exc.args = new_args
    except Exception:
        pass


def _enrich_all_down_message(
    exc: BaseException, attempts: list[dict[str, str]]
) -> None:
    """Rewrite ``exc``'s message in place to enumerate every attempted
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

        normalized.append({
            "id": call_id or "",
            "type": "function",
            "function": {"name": name, "arguments": args},
        })

    return normalized or None


class ModelGateway:
    def __init__(
        self,
        profiles: list[ModelProfile] | dict[str, ModelProfile] | None = None,
        provider_registry: ProviderRegistry | None = None,
        secrets_manager: _SecretsResolver | None = None,
        budget_guard: _BudgetGuardProtocol | None = None,
        router: ModelRouter | None = None,
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
    ) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        if profiles:
            src = profiles.values() if isinstance(profiles, dict) else profiles
            for p in src:
                self._profiles[p.model_profile_id] = p
        self._registry = provider_registry
        self._secrets = secrets_manager
        self._budget_guard = budget_guard
        self._router = router
        self._event_bus = event_bus
        self._hooks = hook_system
        self._broadcaster = worker_broadcaster
        self._metrics_collector = metrics_collector
        self._metrics_agent_id = metrics_agent_id
        self._response_cache = response_cache
        self._response_cache_ttl_seconds = response_cache_ttl_seconds
        self._health_tracker = health_tracker
        self._pause_controller = pause_controller
        self._langsmith_tracer = langsmith_tracer
        self._max_fallback_depth = max_fallback_depth
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
            self._cache_key_lock_refs[cache_key] = (
                self._cache_key_lock_refs.get(cache_key, 0) + 1
            )
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
        return self._profiles.get(profile_id)

    def get_chat_model(
        self,
        profile_id: str,
        *,
        tools: list[dict[str, object]] | None = None,
        project_id: str | None = None,
    ) -> object:
        """Return a LangChain chat model for use with langgraph agents.

        Constructs the provider with the same credential + SSRF guards as
        ``_invoke_and_bill``, optionally binds tools, and returns the raw
        LangChain runnable so callers (e.g. ``create_react_agent``) can
        invoke it directly.
        """
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")
        provider_name = profile.provider
        registry = self._registry
        if registry is not None and not registry.is_installed(provider_name):
            registry.install_provider(provider_name)
            raise ImportError(
                f"Provider '{provider_name}' is not installed. "
                "A dependency update todo has been created."
            )
        job_secrets = self._resolver_for_project(
            str(project_id) if project_id else None
        )
        api_key: str | None = None
        if job_secrets and profile.credential_alias:
            api_key = job_secrets.resolve(profile.credential_alias)
        if registry is not None:
            provider_cls = registry.get_provider_class(provider_name)
        else:
            raise ValueError(
                f"No provider registry configured for '{profile_id}'"
            )
        init_kwargs: dict[str, object] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        if profile.api_base_alias and job_secrets:
            base_url = job_secrets.resolve(profile.api_base_alias)
            if base_url:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise SSRFRejectionError(
                        f"SSRF guard: refusing blocked api_base_alias URL "
                        f"(redacted) for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url
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
                    "Provider class %s does not support bind_tools — "
                    "tools=%r will be ignored for profile=%s",
                    type(chat_model).__name__,
                    tools,
                    profile_id,
                )
        return chat_model

    def is_available(self, profile_id: str) -> bool:
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
            estimated_cost = float("inf")
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
            self.estimate_cost(profile, messages, requested_max_output_tokens)
            if messages is not None
            else None
        )
        if server_cost is not None and isinstance(server_cost, (int, float)) and math.isfinite(server_cost):
            effective_cost = max(estimated_cost, server_cost)
        else:
            effective_cost = estimated_cost
        if effective_cost > budget_remaining:
            return False
        return not (profile.api_metered and effective_cost > profile.run_budget_usd)

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
            approx_output_tokens = min(
                requested_max_output_tokens, profile.max_output_tokens
            )
        else:
            approx_output_tokens = profile.max_output_tokens
        return (
            approx_input_tokens * profile.cost_per_input_token
            + approx_output_tokens * profile.cost_per_output_token
        )

    def list_profiles(self) -> list[ModelProfile]:
        return list(self._profiles.values())

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        estimated_cost: float = 0.0,
        budget_remaining: float = float("inf"),
        requested_max_output_tokens: int | None = None,
        _skip_health_check: bool = False,
        **kwargs: Any,
    ) -> ModelResponse:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        if self._pause_controller is not None and self._pause_controller.is_paused("model", profile_id):
            raise ModelPausedError(
                f"Model profile '{profile_id}' is paused — call refused"
            )

        if (
            not _skip_health_check
            and self._health_tracker is not None
            and not self._health_tracker.is_healthy(profile_id, admit_probe=False)
        ):
            raise CircuitBreakerOpenError(
                f"Profile '{profile_id}' circuit is open; refusing call"
            )

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
            cache_key = _make_cache_key(
                profile_id, messages, model_name=profile.model_name, **kwargs
            )
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for profile=%s key=%s", profile_id, cache_key[:12])
                return ModelResponse(**cast(dict[str, Any], cached))

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
                        return ModelResponse(**cast(dict[str, Any], cached))
                    return self._invoke_and_bill(
                        profile, profile_id, messages, cache_key, **kwargs
                    )
            finally:
                self._cache_key_unref(cache_key)

        return self._invoke_and_bill(
            profile, profile_id, messages, None, **kwargs
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
        when — non-breaking in every case:

        * ``project_id`` is ``None``/empty (a non-project call), or
        * the injected resolver has no ``for_project`` (e.g. a plain
          ``EnvSecretsManager`` when projects are inactive), or
        * ``for_project`` rejects the id (a malformed ``project_id`` containing
          ``'/'`` or ``'..'`` raises ``ValueError``) — fail back to the base
          rather than crash the model call.
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
                "falling back to the shared resolver",
                project_id,
            )
            return base

    def _invoke_and_bill(
        self,
        profile: ModelProfile,
        profile_id: str,
        messages: list[dict[str, str]],
        cache_key: str | None,
        **kwargs: Any,
    ) -> ModelResponse:
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
        job_secrets = self._resolver_for_project(
            str(_project_id) if _project_id else None
        )

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
        if profile.api_base_alias and job_secrets:
            base_url = job_secrets.resolve(profile.api_base_alias)
            if base_url:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise SSRFRejectionError(
                        f"SSRF guard: refusing blocked api_base_alias URL "
                        f"(redacted) for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url
        _resolved_base_url: str | None = cast(
            str | None, init_kwargs.get("base_url")
        )
        extra_body: dict[str, object] = kwargs.pop("extra_body", {})
        for key in ("guided_json", "guided_regex", "guided_choice",
                      "guided_grammar", "guided_whitespace_pattern"):
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

        # C6 hardening: default httpx timeout so a hung provider never blocks a
        # thread indefinitely. The underlying LangChain ChatOpenAI passes
        # request_timeout directly to httpx.Timeout, giving us a connect cap
        # (fast failure on unreachable hosts) + a generous read cap (slow
        # streaming is expected from large-context models).
        import httpx as _httpx

        init_kwargs["request_timeout"] = _httpx.Timeout(
            connect=10.0, read=60.0, write=60.0, pool=10.0
        )

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
                    "Provider class %s does not support bind_tools — "
                    "tools=%r will be ignored for profile=%s",
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

        content = getattr(raw_response, "content", str(raw_response))
        usage = getattr(raw_response, "usage_metadata", {}) or {}

        # Extract tool calls BEFORE the empty-200 guard: a tool-call turn
        # legitimately has empty text content (the model's "output" IS the tool
        # request), so it must NOT be misclassified as an empty-200 soft failure.
        tool_calls = _extract_tool_calls(raw_response)

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
        if not str(content).strip() and not tool_calls:
            empty_exc = self._empty_response_error(profile_id)
            self.record_timeout_on_failure(profile_id, empty_exc)
            raise empty_exc

        # Provider-controlled usage data: never trust it for billing.
        # - reject bool (isinstance(True, int) is True) so it counts as 0, not 1
        # - clamp at >= 0 so a negative count can never produce a negative cost
        #   (a negative cost would CREDIT the budget guard and bypass the ceiling)
        # - accept OpenAI-style key names as fallbacks so whole provider families
        #   are not silently metered at $0
        input_tokens = _coerce_token_count(
            usage.get("input_tokens", usage.get("prompt_tokens", 0))
        )
        output_tokens = _coerce_token_count(
            usage.get("output_tokens", usage.get("completion_tokens", 0))
        )
        cost = (
            input_tokens * profile.cost_per_input_token
            + output_tokens * profile.cost_per_output_token
        )

        logger.debug(
            "Model call complete: profile=%s, input_tokens=%s, output_tokens=%s, cost=%.6f",
            profile_id,
            input_tokens,
            output_tokens,
            cost,
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
                    response=str(content),
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
            content=str(content),
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
        finish_reason = (
            getattr(raw_response, "response_metadata", None) or {}
        ).get("finish_reason")
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

        ``correlation_id`` (optional): when supplied, it is stamped onto the
        returned ``ModelResponse.correlation_id`` regardless of which profile
        in the chain ultimately served the call — so a caller can trace a
        single logical request across a primary -> secondary -> ... failover.
        It is a keyword-only, gateway-local concern and is deliberately kept
        out of ``**kwargs`` (never forwarded to the provider constructor).
        See docs/audit/FAILOVER_GAPS.md (correlation-id-propagation).
        """
        import time as _time

        import httpx

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        tracker = self._health_tracker
        policy = TimeoutRetryPolicy(
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
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
                f"call_model_with_retry: profile '{profile_id}' is unhealthy "
                "and no fallback profiles are configured"
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
        # Cumulative-backoff cap: _time.sleep is synchronous and blocks the
        # calling thread. With overload_max_retries=10 and
        # overload_max_backoff=120s the worst-case cumulative sleep is 10x120s =
        # 1200s (~20 minutes) on a single call — effectively a DoS of the thread
        # pool. We track total wall-clock time spent sleeping and stop sleeping
        # once the cap is exceeded, letting the retry exhaust naturally (tenacity
        # continues retrying, but immediately rather than waiting). The cap is
        # intentionally generous (300s = 5 minutes) so that legitimate overload
        # back-pressure is still respected for the first few retries.
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
            effective_cap = (
                policy._overload_max_retries if kind in _OVERLOAD_KINDS else max_retries
            )
            if _attempt_counter[0] > effective_cap:
                return False
            decision = policy.decide(kind, _attempt_counter[0])
            return bool(decision.should_retry)

        def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
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
                        _time.sleep(actual_sleep)
                        _cumulative_sleep_s[0] += actual_sleep

        # Overload kinds (PROVIDER_ERROR, RATE_LIMITED) use the higher retry cap
        # so tenacity doesn't stop too early on the primary before policy can
        # exhaust the overload budget.
        _exhausted = False
        try:
            for attempt in tenacity.Retrying(
                retry=tenacity.retry_if_exception(_is_retryable),
                wait=tenacity.wait_none(),
                stop=tenacity.stop_after_attempt(policy._overload_max_retries),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with attempt:
                    _attempt_counter[0] = attempt.retry_state.attempt_number
                    try:
                        result = self.call_model(
                            profile_id, messages,
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
        result, last_fb_exc, attempts = self._walk_fallbacks(
            fallback_ids, messages,
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

    def _call_fallback(
        self,
        fb_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """The bare fallback-exhaustion loop used to call call_model(fb_id) with no
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
            raise RuntimeError(
                f"fallback capacity exhausted for '{fb_id}' "
                f"(all {sem._value + 1} slots occupied)"
            )
        try:
            result = self.call_model(fb_id, messages, **kwargs)
        finally:
            sem.release()
        return result

    def _record_failover(
        self, from_profile_id: str, to_profile_id: str, error: str
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
            self._failover_log.record_failover(from_profile_id, to_profile_id, error)
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
        """Try fallbacks in order, CASCADING into each attempted fallback's own
        configured ``fallback_profiles`` when it also fails, so a 3+ profile
        chain (primary -> secondary -> tertiary -> ...) is walked to full
        exhaustion rather than stopping after one hop. Skips circuit-open
        profiles and never revisits a profile already tried in this walk
        (cycle-safe: a fallback chain that loops back to an earlier profile,
        including ``from_profile_id`` itself, is not retried).

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
        while queue:
            fb_id = queue.pop(0)
            depth += 1
            if depth > self._max_fallback_depth:
                continue
            if fb_id in visited:
                continue
            visited.add(fb_id)
            if tracker is not None and not tracker.is_healthy(fb_id):
                # Circuit open for this fallback: skip without billing/calling.
                continue
            if prev_id is not None:
                self._record_failover(
                    prev_id, fb_id, sanitize_error_message(str(last_exc)) if last_exc is not None else ""
                )
            try:
                result = self._call_fallback(fb_id, messages, **kwargs)
            except BudgetExceededError:
                # F-F: a budget rejection on the fallback path MUST propagate.
                # Previously the bare ``except Exception`` below swallowed it and
                # continued to the next fallback, spending past the per-profile
                # budget ceiling. Re-raise (mirrors _try_call_model /
                # call_model_with_fallback) so the budget gate is enforced.
                raise
            except SSRFRejectionError:
                # F-E: an SSRF egress rejection on the fallback path MUST
                # propagate. _try_call_model already re-raises it, but the bare
                # ``except Exception`` below would re-swallow it here and continue
                # to the next fallback, silently routing past the blocked egress.
                # Re-raise (mirrors BudgetExceededError) so the SSRF guard hard-
                # stops the chain instead of falling open.
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
        import time as _time

        from general_ludd.models.timeout_detector import (
            TimeoutClassifier,
            TimeoutEvent,
        )

        if self._health_tracker is None:
            return

        kind = TimeoutClassifier.classify(exc)
        self._health_tracker.record_event(TimeoutEvent(
            model_id=profile_id,
            kind=kind,
            timestamp=_time.monotonic(),
            duration_s=0.0,
        ))

    def call_model_by_role(
        self,
        role_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
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
                f"profile '{profile_id}' (role '{role_name}') circuit is open; "
                "refusing to call unhealthy provider"
            )
        return self.call_model(profile_id, messages, **kwargs)

    def call_model_by_pattern(
        self,
        pattern: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
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
                f"profile '{profile_id}' (pattern '{pattern}') circuit is open; "
                "refusing to call unhealthy provider"
            )
        return self.call_model(profile_id, messages, **kwargs)

    def _try_call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse | None:
        if (
            self._health_tracker is not None
            and not self._health_tracker.is_healthy(profile_id)
        ):
            return None
        try:
            return self.call_model(profile_id, messages, **kwargs)
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
        # D-04: gate primary on health tracker before attempting call
        primary_healthy = (
            self._health_tracker is None
            or self._health_tracker.is_healthy(profile_id)
        )
        # Thread budget params into kwargs so _try_call_model / _walk_fallbacks
        # forward them to call_model (they are explicit keyword-only params of
        # call_model_with_fallback, NOT in **kwargs).
        _call_kwargs: dict[str, Any] = {
            "estimated_cost": estimated_cost,
            "budget_remaining": budget_remaining,
            **kwargs,
        }
        if primary_healthy:
            try:
                return self.call_model(profile_id, messages, **_call_kwargs)
            except SSRFRejectionError:
                raise
            except BudgetExceededError:
                raise
            except ModelPausedError:
                raise
            except Exception as exc:
                primary_exc = exc
        else:
            primary_exc = None

        fallback_ids: list[str] = fallback_profiles or []
        if not fallback_ids:
            profile = self._profiles.get(profile_id)
            if profile is not None:
                fallback_ids = list(profile.fallback_profiles)

        all_attempts: list[dict[str, str]] = []
        if primary_exc is not None:
            all_attempts.append({"profile_id": profile_id, "reason": sanitize_error_message(str(primary_exc))})

        # D-04: route fallback walk through _walk_fallbacks (health-gated)
        result, last_exc, fallback_attempts = self._walk_fallbacks(
            fallback_ids, messages,
            from_profile_id=profile_id,
            from_error=primary_exc,
            **_call_kwargs,
        )
        if result is not None:
            return result
        all_attempts.extend(fallback_attempts)

        # If primary was tripped AND all fallbacks open/failed -> clear error
        if not primary_healthy:
            cb_error = CircuitBreakerOpenError(
                f"All circuits open for fallback chain '{profile_id}'"
            )
            _enrich_all_down_message(cb_error, all_attempts)
            raise cb_error

        if last_exc is not None:
            cb_error = CircuitBreakerOpenError(
                f"All profiles in fallback chain failed for '{profile_id}'"
            )
            _enrich_all_down_message(cb_error, all_attempts)
            raise cb_error from last_exc

        cb_error = CircuitBreakerOpenError(
            f"All profiles in fallback chain failed for '{profile_id}'"
        )
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
            except Exception as exc:
                logger.warning("Worker broadcast failed for model %s: %s", action, sanitize_error_message(str(exc)))

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

        eligible.sort(
            key=lambda p: p.cost_per_input_token + p.cost_per_output_token
        )
        return eligible[0]

    def add_profile(
        self,
        model_id: str,
        provider: str = "openai",
        model: str = "",
        api_key_env: str | None = None,
        api_base_alias: str | None = None,
        **kwargs: Any,
    ) -> ModelProfile:
        profile = ModelProfile(
            model_profile_id=model_id,
            provider=provider,
            model_name=model,
            credential_alias=api_key_env,
            api_base_alias=api_base_alias,
            enabled=True,
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
        self._profiles.pop(model_id, None)
        self._notify_profile_change(
            event=ModelRemovedEvent(model_id=model_id),
            hook_name="on_model_removed",
            hook_payload={"model_id": model_id},
            action="remove",
            model_id=model_id,
            broadcast_payload={},
        )
