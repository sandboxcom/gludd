"""Bounded streaming and streamed failover orchestration for the model gateway."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Protocol, cast

import tenacity

if TYPE_CHECKING:
    from general_ludd.models.gateway_types import (
        _BudgetGuardProtocol,
        _HealthTrackerProtocol,
        _MetricsCollectorProtocol,
        _PauseControllerProtocol,
        _RuntimeModelGateway,
        _SecretsResolver,
    )
    from general_ludd.observability.langsmith_tracer import LangSmithTracer

from general_ludd.models.gateway_types import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MAX_STREAM_BYTES,
    DEFAULT_MAX_STREAM_CHUNKS,
    DEFAULT_MAX_STREAM_DECOMPRESSION_RATIO,
    DEFAULT_MAX_STREAM_IDLE_SECONDS,
    DEFAULT_MAX_STREAM_SECONDS,
    DEFAULT_MAX_STREAM_TOKENS,
    DEFAULT_MAX_TOOL_CALLS,
    BudgetExceededError,
    CallCancelledError,
    CircuitBreakerOpenError,
    ModelPausedError,
    ModelProfile,
    PayloadLimitError,
    SSRFRejectionError,
    StreamLimitError,
    _coerce_token_count,
    _enrich_all_down_message,
    _extract_retry_after_seconds,
    _is_healthy_with_timeout,
    _positive_profile_limit,
    _redact_url_in_exception,
    _RequestPayloadBudget,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.timeout_detector import (
    _NON_RETRYABLE_KINDS,
    _OVERLOAD_KINDS,
    TimeoutClassifier,
    TimeoutRetryPolicy,
)
from general_ludd.security.sanitize import sanitize_error_message


class _TokenTracker(Protocol):
    def record(self, work_type: str, input_tokens: int, output_tokens: int) -> None: ...


def _facade_default_token_tracker() -> _TokenTracker:
    """Resolve the historical monkeypatch seam from the public facade."""
    from general_ludd.models import gateway

    return gateway.default_token_tracker()


def _facade_logger() -> logging.Logger:
    """Resolve the historical logger seam from the public facade."""
    from general_ludd.models import gateway

    return gateway.logger


class GatewayStreamingMixin:
    """Provider streaming, retry, and fallback behavior mixed into ModelGateway."""

    if TYPE_CHECKING:
        _profiles: dict[str, ModelProfile]
        _registry: ProviderRegistry | None
        _pause_controller: _PauseControllerProtocol | None
        _health_tracker: _HealthTrackerProtocol | None
        _secrets: _SecretsResolver | None
        _budget_guard: _BudgetGuardProtocol | None
        _metrics_collector: _MetricsCollectorProtocol | None
        _metrics_agent_id: str | None
        _langsmith_tracer: LangSmithTracer | None
        _stream_wire_byte_counter: Callable[[object], int] | None
        _max_fallback_depth: int

        def _runtime_route(self, profile_id: str) -> _RuntimeModelGateway | None: ...

        def _enforce_request_limits(
            self,
            profile: ModelProfile,
            profile_id: str,
            messages: list[dict[str, str]],
            kwargs: dict[str, Any],
        ) -> tuple[int, int]: ...

        def check_budget(
            self,
            profile_id: str,
            estimated_cost: float,
            budget_remaining: float,
            *,
            messages: list[dict[str, str]] | None = ...,
            requested_max_output_tokens: int | None = ...,
        ) -> bool:
            """Return whether the host gateway budget permits a call."""
            ...

        def _resolver_for_project(self, project_id: str | None) -> _SecretsResolver | None: ...

        def _stream_provider_semaphore(self, profile_id: str) -> threading.Semaphore: ...

        def record_timeout_on_failure(self, profile_id: str, exc: BaseException) -> None:
            """Record host-side health data for a failed provider call."""
            ...

        @staticmethod
        def _response_token_count(usage: dict[str, object], response_bytes: int) -> tuple[int, str]: ...

        def _apply_billing_rate(self, base_cost: float) -> tuple[float, str, float]: ...

        @staticmethod
        def _empty_response_error(profile_id: str) -> Exception: ...

        def _record_failover(
            self,
            from_profile: str,
            to_profile: str,
            error: str,
            *,
            exception_type: str | None = ...,
        ) -> None: ...

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
        runtime = self._runtime_route(profile_id)
        if runtime is not None:
            yield from runtime.call_model_stream(
                profile_id,
                messages,
                estimated_cost=estimated_cost,
                budget_remaining=budget_remaining,
                requested_max_output_tokens=requested_max_output_tokens,
                tools=tools,
                project_id=project_id,
                **kwargs,
            )
            return
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
            _facade_logger().warning(
                "Ignoring caller-supplied base_url for streamed profile=%s",
                profile_id,
            )
        if caller_api_key is not None:
            _facade_logger().warning(
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
                    _facade_logger().debug(
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
        _facade_default_token_tracker().record(work_type, input_tokens, output_tokens)
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
        return cast(list[object], coro)

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
            return cast(list[object], coro)
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
