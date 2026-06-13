"""Model gateway for LangChain provider management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import tenacity
from pydantic import BaseModel, Field, field_validator

from general_ludd.events.types import ModelAddedEvent, ModelRemovedEvent
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.response_cache import _make_cache_key
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import TimeoutClassifier, TimeoutEvent, TimeoutRetryPolicy

logger = logging.getLogger(__name__)


class _SecretsResolver(Protocol):
    def resolve(self, alias_name: str) -> str | None: ...



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
        if v < 0:
            raise ValueError("must be non-negative")
        return v


@dataclass
class ModelResponse:
    content: str
    usage_metadata: dict[str, Any] = field(default_factory=dict)
    cost_estimate: float = 0.0
    model_name: str = ""
    raw_response: Any = None


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
        self._health_tracker = health_tracker

    def get_profile(self, profile_id: str) -> ModelProfile | None:
        return self._profiles.get(profile_id)

    def is_available(self, profile_id: str) -> bool:
        profile = self._profiles.get(profile_id)
        return profile is not None and profile.enabled

    def check_budget(
        self, profile_id: str, estimated_cost: float, budget_remaining: float
    ) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return False
        if estimated_cost > budget_remaining:
            return False
        return not (profile.api_metered and estimated_cost > profile.run_budget_usd)

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
            raise ValueError(
                f"Call to '{profile_id}' rejected: over budget "
                f"(estimated={estimated_cost}, remaining={budget_remaining}, "
                f"profile_budget={profile.run_budget_usd}"
            )

        if self._response_cache is not None:
            cache_key = _make_cache_key(profile_id, messages, **kwargs)
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for profile=%s key=%s", profile_id, cache_key[:12])
                return ModelResponse(**cached)

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

        init_kwargs: dict[str, Any] = {"model": profile.model_name}
        if api_key:
            init_kwargs["api_key"] = api_key
        if profile.api_base_alias and self._secrets:
            base_url = self._secrets.resolve(profile.api_base_alias)
            if base_url:
                init_kwargs["base_url"] = base_url
        init_kwargs.update(kwargs)

        chat_model = provider_cls(**init_kwargs)

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

        cost = 0.0
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        if isinstance(input_tokens, (int, float)) and isinstance(output_tokens, (int, float)):
            cost = input_tokens * profile.cost_per_input_token + output_tokens * profile.cost_per_output_token

        logger.debug(
            "Model call complete: profile=%s, input_tokens=%s, output_tokens=%s, cost=%.6f",
            profile_id,
            input_tokens,
            output_tokens,
            cost,
        )

        if self._budget_guard is not None:
            self._budget_guard.record_spend(cost)

        if self._metrics_collector is not None and self._metrics_agent_id:
            self._metrics_collector.record_model_call(
                agent_id=self._metrics_agent_id,
                model_id=profile_id,
                input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else 0,
                output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else 0,
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
        )

        if self._response_cache is not None:
            cache_key = _make_cache_key(profile_id, messages, **kwargs)
            self._response_cache.set(cache_key, {
                "content": response.content,
                "usage_metadata": response.usage_metadata,
                "cost_estimate": response.cost_estimate,
                "model_name": response.model_name,
            })

        return response

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

        from general_ludd.models.timeout_detector import TimeoutKind

        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError(f"Profile '{profile_id}' not found")

        tracker = self._health_tracker
        policy = TimeoutRetryPolicy(
            max_retries=max_retries,
            base_backoff_seconds=base_backoff_seconds,
        )

        # If primary is already unhealthy, skip straight to fallbacks.
        if tracker is not None and not tracker.is_healthy(profile_id):
            fallback_ids = list(profile.fallback_profiles)
            for fb_id in fallback_ids:
                if tracker.is_healthy(fb_id):
                    return self.call_model(fb_id, messages, **kwargs)
            if fallback_ids:
                return self.call_model(fallback_ids[0], messages, **kwargs)

        _retryable_exc_types = (
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
            TimeoutError,
        )

        _attempt_counter: list[int] = [0]
        _last_exc: list[BaseException | None] = [None]

        def _is_retryable(exc: BaseException) -> bool:
            """Tenacity retry predicate: True → retry, False → re-raise."""
            if not isinstance(exc, _retryable_exc_types):
                return False
            kind = TimeoutClassifier.classify(exc)
            # Non-retryable kinds: immediate re-raise.
            if kind in (TimeoutKind.AUTH_ERROR, TimeoutKind.CONTEXT_LENGTH):
                return False
            decision = policy.decide(kind, _attempt_counter[0])
            return bool(decision.should_retry)

        def _before_sleep(retry_state: tenacity.RetryCallState) -> None:
            """Record health tracker event and perform policy-computed sleep."""
            exc = retry_state.outcome.exception() if retry_state.outcome else None
            if exc is not None and isinstance(exc, _retryable_exc_types):
                kind = TimeoutClassifier.classify(exc)
                if tracker is not None:
                    tracker.record_event(TimeoutEvent(
                        model_id=profile_id,
                        kind=kind,
                        timestamp=_time.monotonic(),
                        duration_s=0.0,
                    ))
                wait_s = policy._compute_backoff(kind, _attempt_counter[0], None)
                if wait_s > 0:
                    _time.sleep(wait_s)

        # failover_after_retries == 3: stop retrying primary after 3 attempts.
        failover_after = policy._failover_after

        _exhausted = False
        try:
            for attempt in tenacity.Retrying(
                retry=tenacity.retry_if_exception(_is_retryable),
                wait=tenacity.wait_none(),
                stop=tenacity.stop_after_attempt(failover_after),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with attempt:
                    _attempt_counter[0] = attempt.retry_state.attempt_number
                    try:
                        result = self.call_model(profile_id, messages, **kwargs)
                        if tracker is not None:
                            tracker.record_success(profile_id)
                        return result
                    except _retryable_exc_types as exc:
                        _last_exc[0] = exc
                        kind = TimeoutClassifier.classify(exc)
                        if kind in (TimeoutKind.AUTH_ERROR, TimeoutKind.CONTEXT_LENGTH):
                            # Non-retryable: record and re-raise immediately.
                            if tracker is not None:
                                tracker.record_event(TimeoutEvent(
                                    model_id=profile_id,
                                    kind=kind,
                                    timestamp=_time.monotonic(),
                                    duration_s=0.0,
                                ))
                            raise
                        raise
        except _retryable_exc_types as exc:
            # Tenacity exhausted retries on primary and re-raised last exception.
            _last_exc[0] = exc
            _exhausted = True

        if not _exhausted:
            # Should not reach here (return or raise should happen above).
            return None  # type: ignore[return-value]

        # Tenacity exhausted (failover_after attempts tried on primary) → walk fallbacks.
        fallback_ids = list(profile.fallback_profiles)
        for fb_id in fallback_ids:
            try:
                return self.call_model(fb_id, messages, **kwargs)
            except Exception:
                continue

        last = _last_exc[0]
        if last is not None:
            raise last from None
        raise RuntimeError(f"call_model_with_retry: all attempts failed for profile '{profile_id}'")

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
        except (ValueError, ImportError):
            return None

    def call_model_with_fallback(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        *,
        fallback_profiles: list[str] | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        result = self._try_call_model(profile_id, messages, **kwargs)
        if result is not None:
            return result

        fallback_ids: list[str] = fallback_profiles or []
        if not fallback_ids:
            profile = self._profiles.get(profile_id)
            if profile is not None:
                fallback_ids = list(profile.fallback_profiles)

        for fb_id in fallback_ids:
            result = self._try_call_model(fb_id, messages, **kwargs)
            if result is not None:
                return result

        raise ValueError(
            f"All profiles in fallback chain failed for '{profile_id}'"
        )

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
        if self._event_bus:
            self._event_bus.publish(ModelAddedEvent(model_id=model_id, profile=profile.model_dump()))
        if self._hooks:
            self._hooks.fire("on_model_added", {"model_id": model_id, "profile": profile.model_dump()})
        if self._broadcaster:
            try:
                self._broadcaster.broadcast_model_update("add", model_id, profile.model_dump())
            except Exception as exc:
                logger.warning("Worker broadcast failed for model add: %s", exc)
        return profile

    def remove_profile(self, model_id: str) -> None:
        self._profiles.pop(model_id, None)
        if self._event_bus:
            self._event_bus.publish(ModelRemovedEvent(model_id=model_id))
        if self._hooks:
            self._hooks.fire("on_model_removed", {"model_id": model_id})
        if self._broadcaster:
            try:
                self._broadcaster.broadcast_model_update("remove", model_id, {})
            except Exception as exc:
                logger.warning("Worker broadcast failed for model remove: %s", exc)
