"""Model gateway for LangChain provider management."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

import tenacity
from pydantic import BaseModel, Field, field_validator

from general_ludd.events.types import ModelAddedEvent, ModelRemovedEvent
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.response_cache import _make_cache_key
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    _OVERLOAD_KINDS,
    TimeoutClassifier,
    TimeoutRetryPolicy,
)

logger = logging.getLogger(__name__)

# Default TTL (seconds) for cached model responses. LLM outputs are
# non-deterministic and time-sensitive, so entries must expire rather than
# live forever. Configurable per-gateway via response_cache_ttl_seconds.
DEFAULT_RESPONSE_CACHE_TTL_SECONDS = 3600


def _coerce_token_count(value: Any) -> int:
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
        return max(0, int(value))
    return 0


class _SecretsResolver(Protocol):
    def resolve(self, alias_name: str) -> str | None: ...



class BudgetExceededError(ValueError):
    """Raised when a call is rejected by the budget gate (D-24 fix)."""


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

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("model_profile_id must not be empty")
        return v

    @field_validator("context_window", "max_input_tokens", "max_output_tokens")
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
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    cost_estimate: float = 0.0
    model_name: str = ""
    raw_response: Any = None
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
    tool_calls: list[dict[str, Any]] | None = None


def _extract_tool_calls(raw_response: Any) -> list[dict[str, Any]] | None:
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

    normalized: list[dict[str, Any]] = []
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
        budget_guard: Any | None = None,
        router: ModelRouter | None = None,
        event_bus: Any | None = None,
        hook_system: Any | None = None,
        worker_broadcaster: Any | None = None,
        metrics_collector: Any | None = None,
        metrics_agent_id: str | None = None,
        response_cache: Any | None = None,
        response_cache_ttl_seconds: int = DEFAULT_RESPONSE_CACHE_TTL_SECONDS,
        health_tracker: Any | None = None,
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
        # Per-cache-key single-flight locks: under concurrency, N identical
        # cache misses would all call the provider (cache stampede). We serialize
        # identical misses on a per-key lock so only the first does the provider
        # call and the rest re-read the now-populated cache. _cache_key_locks is
        # itself guarded by _cache_key_locks_guard.
        self._cache_key_locks: dict[str, threading.Lock] = {}
        self._cache_key_locks_guard = threading.Lock()

    def _cache_key_lock(self, cache_key: str) -> threading.Lock:
        """Return the process-local single-flight lock for a cache key."""
        with self._cache_key_locks_guard:
            lock = self._cache_key_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._cache_key_locks[cache_key] = lock
            return lock

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        return self._profiles.get(profile_id)

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
    ) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return False
        # D-21: do NOT trust the caller-provided estimated_cost. Re-estimate
        # server-side from the actual messages + the profile's price rates, and
        # use the MAX of (caller claim, server estimate) for the budget decision
        # so a buggy / malicious caller cannot under-report cost to slip past
        # the gate. If no messages are supplied, fall back to the caller value
        # (preserves backward compatibility for callers that pre-computed cost).
        server_cost = self.estimate_cost(profile, messages) if messages is not None else None
        effective_cost = max(estimated_cost, server_cost) if server_cost is not None else estimated_cost
        if effective_cost > budget_remaining:
            return False
        return not (profile.api_metered and effective_cost > profile.run_budget_usd)

    @staticmethod
    def estimate_cost(
        profile: ModelProfile, messages: list[dict[str, str]] | None
    ) -> float:
        """Server-side cost re-estimation independent of the caller.

        Approximates input tokens from the message content (~4 chars/token, a
        standard rough heuristic) and assumes the model may emit up to its
        ``max_output_tokens``. Multiplies both legs by the profile's price rates.
        Returns 0.0 when messages is empty/None.
        """
        if not messages:
            return 0.0
        input_chars = 0
        for m in messages:
            content = str(m.get("content", "") or "") if isinstance(m, dict) else str(getattr(m, "content", "") or "")
            input_chars += len(content)
        approx_input_tokens = input_chars // 4
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
        **kwargs: Any,
    ) -> ModelResponse:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        if not self.check_budget(profile_id, estimated_cost, budget_remaining):
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
                return ModelResponse(**cached)

        # Cache stampede single-flight: under concurrency N identical misses
        # would otherwise all hit the provider. Serialize identical misses on a
        # per-key lock; inside the lock we re-read the cache (double-checked
        # locking) so only the first miss does the provider call and the rest
        # serve the now-populated entry.
        if cache_key is not None and self._response_cache is not None:
            with self._cache_key_lock(cache_key):
                cached = self._response_cache.get(cache_key)
                if cached is not None:
                    logger.debug(
                        "Cache hit (single-flight) profile=%s key=%s",
                        profile_id,
                        cache_key[:12],
                    )
                    return ModelResponse(**cached)
                return self._invoke_and_bill(
                    profile, profile_id, messages, cache_key, **kwargs
                )

        return self._invoke_and_bill(
            profile, profile_id, messages, None, **kwargs
        )

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

        api_key: str | None = None
        if self._secrets and profile.credential_alias:
            api_key = self._secrets.resolve(profile.credential_alias)

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

        init_kwargs: dict[str, Any] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        if profile.api_base_alias and self._secrets:
            base_url = self._secrets.resolve(profile.api_base_alias)
            if base_url:
                from general_ludd.security.auth import is_safe_fetch_url

                if not is_safe_fetch_url(base_url):
                    raise ValueError(
                        f"SSRF guard: refusing blocked api_base_alias URL "
                        f"{base_url!r} for profile '{profile_id}'"
                    )
                init_kwargs["base_url"] = base_url
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
            self.record_timeout_on_failure(profile_id, exc)
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
        if (
            self._response_cache is not None
            and cache_key is not None
            and not response.tool_calls
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
            result, last_fb_exc = self._walk_fallbacks(fallback_ids, messages, **kwargs)
            if result is not None:
                return result
            if last_fb_exc is not None:
                raise last_fb_exc from None
            if fallback_ids:
                # All fallbacks failed is_healthy and none was attempted: probe
                # the first as a last resort (records its own success/failure).
                return self._call_fallback(fallback_ids[0], messages, **kwargs)
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
                wait_s = policy._compute_backoff(kind, _attempt_counter[0], None, overload=is_overload)
                if wait_s > 0:
                    _time.sleep(wait_s)

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
                        result = self.call_model(profile_id, messages, **kwargs)
                        # NOTE: record_success is already called inside
                        # _invoke_and_bill (via call_model) on every billed
                        # success. A second call here would double-count and
                        # trip the breaker at half the configured threshold.
                        return result
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
            return None  # type: ignore[return-value]

        # Tenacity exhausted (failover_after attempts tried on primary) → walk
        # fallbacks. _walk_fallbacks skips any fallback that fails is_healthy and
        # records success/failure for the ones it attempts (Fix 3).
        fallback_ids = list(profile.fallback_profiles)
        result, last_fb_exc = self._walk_fallbacks(fallback_ids, messages, **kwargs)
        if result is not None:
            return result

        last = last_fb_exc or _last_exc[0]
        if last is not None:
            raise last from None
        raise RuntimeError(f"call_model_with_retry: all attempts failed for profile '{profile_id}'")

    def _call_fallback(
        self,
        fb_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse:
        """Call one fallback profile, recording its success/failure on the
        health tracker.

        The bare fallback-exhaustion loop used to call call_model(fb_id) with no
        is_healthy gate and never recorded the fallback's success or failure, so
        the breaker never tracked fallback health (Fix 3). call_model already
        records failures via record_timeout_on_failure on exception; here we add
        the success path so a healthy fallback resets its consecutive counter.
        """
        result = self.call_model(fb_id, messages, **kwargs)
        if self._health_tracker is not None:
            self._health_tracker.record_success(fb_id)
        return result

    def _walk_fallbacks(
        self,
        fallback_ids: list[str],
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> tuple[ModelResponse | None, BaseException | None]:
        """Try fallbacks in order, skipping circuit-open ones; return the first
        success (or the last exception if all attempted ones failed).

        Returns (response, None) on success, or (None, last_exc) if every
        attempted fallback raised. A fallback that fails is_healthy is skipped
        (circuit honored) and does NOT count as an attempt.
        """
        tracker = self._health_tracker
        last_exc: BaseException | None = None
        for fb_id in fallback_ids:
            if tracker is not None and not tracker.is_healthy(fb_id):
                # Circuit open for this fallback: skip without billing/calling.
                continue
            try:
                return self._call_fallback(fb_id, messages, **kwargs), None
            except Exception as exc:  # try the next fallback
                last_exc = exc
                continue
        return None, last_exc

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
        profile_id = self._router.resolve_role(role_name)
        if profile_id is None:
            raise ValueError(f"No profile resolved for role '{role_name}'")
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
        return self.call_model(profile_id, messages, **kwargs)

    def _try_call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ModelResponse | None:
        try:
            return self.call_model(profile_id, messages, **kwargs)
        except BudgetExceededError:
            # D-24: a budget rejection MUST propagate. Previously this was
            # caught by the bare ``except (ValueError, ImportError)`` below and
            # silently returned None, which call_model_with_fallback treated as
            # a non-failure — so a profile whose own run_budget_usd was exceeded
            # simply routed to a fallback with a larger budget cap, bypassing
            # the per-profile ceiling. Re-raise so the caller sees the rejection.
            raise
        except (ValueError, ImportError):
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
        # Thread the run-budget context through so fallback attempts share the
        # same budget ceiling as the primary call. Without this, call_model's
        # check_budget gate (which reads estimated_cost/budget_remaining) was
        # never reached with a real budget on the fallback path — every fallback
        # saw budget_remaining=inf and could spend past the run budget.
        kwargs.setdefault("estimated_cost", estimated_cost)
        kwargs.setdefault("budget_remaining", budget_remaining)

        # Health gate: skip circuit-open profiles rather than attempting them.
        # The primary is only tried when no tracker is configured (open by
        # default) or its circuit is healthy; each fallback below is likewise
        # gated on is_healthy before it is attempted.
        tracker = self._health_tracker

        fallback_ids: list[str] = fallback_profiles or []
        if not fallback_ids:
            profile = self._profiles.get(profile_id)
            if profile is not None:
                fallback_ids = list(profile.fallback_profiles)

        # D-22: if every model in the chain (primary + fallbacks) is circuit-open,
        # raise immediately instead of walking the loop and continuing past each
        # unhealthy entry only to raise at the end. Walking an all-unhealthy chain
        # amplifies retry storms because the caller sees a ValueError after a full
        # no-op sweep and re-invokes, repeating the same useless walk.
        if tracker is not None:
            primary_healthy = tracker.is_healthy(profile_id)
            any_healthy = primary_healthy or any(
                tracker.is_healthy(fb) for fb in fallback_ids
            )
            if not any_healthy:
                raise ValueError(
                    f"All profiles in fallback chain for '{profile_id}' are "
                    f"circuit-open; not attempting any"
                )

        last_exc: BaseException | None = None
        if tracker is None or tracker.is_healthy(profile_id):
            try:
                result = self._try_call_model(profile_id, messages, **kwargs)
            except BudgetExceededError:
                raise
            except Exception as exc:
                # D-22: see below — a provider-level failure on the primary must
                # fall through to the fallback chain, not abort the whole call.
                last_exc = exc
                result = None
            if result is not None:
                return result

        for fb_id in fallback_ids:
            if tracker is not None and not tracker.is_healthy(fb_id):
                continue
            try:
                result = self._try_call_model(fb_id, messages, **kwargs)
            except BudgetExceededError:
                # D-24: budget rejection is a hard fail — do not continue the
                # fallback chain (would bypass the per-profile spending cap).
                raise
            except Exception as exc:
                # D-22: a provider-level failure from one fallback must NOT abort
                # the whole chain. call_model already recorded the failure on the
                # health tracker (record_timeout_on_failure), so the next
                # iteration's is_healthy check will honour the freshly-opened
                # circuit. Swallow here and continue to the next fallback so the
                # is_healthy gate gets to skip the now-unhealthy model instead of
                # the caller retrying the whole chain (retry-storm amplifier).
                last_exc = exc
                continue
            if result is not None:
                return result

        if last_exc is not None:
            raise last_exc
        raise ValueError(
            f"All profiles in fallback chain failed for '{profile_id}'"
        )

    def _notify_profile_change(
        self,
        event: Any,
        hook_name: str,
        hook_payload: dict[str, Any],
        action: str,
        model_id: str,
        broadcast_payload: dict[str, Any],
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
                logger.warning("Worker broadcast failed for model %s: %s", action, exc)

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
