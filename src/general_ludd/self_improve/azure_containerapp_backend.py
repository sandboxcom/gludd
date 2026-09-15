"""Privacy-gated OpenAI-compatible backend for one Azure Container App.

The backend is bound to an immutable app revision, image digest, model revision,
and HTTPS origin.  It probes only ``/v1/models`` and posts only to
``/v1/chat/completions``; there is no provider routing or fallback surface.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from contextlib import suppress

from prometheus_client.parser import text_string_to_metric_families

from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzurePromptApprovalError,
)
from general_ludd.self_improve.azure_containerapp_transport import (
    MAX_PROVIDER_TOKENS,
    MAX_RESPONSE_BYTES,
    ContainerAppBackendAccounting,
    ContainerAppBackendTrace,
    ContainerAppResponseFailure,
    ContainerAppTraceEvent,
    HTTPClient,
    discard_trace,
    emit_failure,
    emit_trace,
    new_httpx_client,
    probe_model,
    request_json,
    request_text,
    validated_chat_response,
)
from general_ludd.self_improve.azure_containerapp_transport_types import (
    VLLMRuntimeGPUEvidence,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)

_MAX_METRICS_BYTES = 8 * 1024 * 1024
_MAX_METRIC_SAMPLES = 100_000
_METRICS_CONTENT_TYPES = frozenset({"text/plain", "application/openmetrics-text"})
_PROMPT_TOKENS = "vllm:prompt_tokens_total"
_GENERATION_TOKENS = "vllm:generation_tokens_total"
_SUCCESSFUL_REQUESTS = "vllm:request_success_total"
_ESTIMATED_FLOPS = "vllm:estimated_flops_per_gpu_total"
_REQUIRED_METRICS = frozenset(
    {_PROMPT_TOKENS, _GENERATION_TOKENS, _SUCCESSFUL_REQUESTS}
)
_SELECTED_METRICS = _REQUIRED_METRICS | {_ESTIMATED_FLOPS}
_DISCOVERY_ATTEMPT_SECONDS = 30.0
_DISCOVERY_RETRY_SECONDS = 1.0


def _vllm_metric_totals(payload: str, model_name: str) -> dict[str, float]:
    """Parse only bounded exact-model counters through prometheus-client."""
    totals: dict[str, float] = {}
    sample_count = 0
    for family in text_string_to_metric_families(payload):
        for sample in family.samples:
            sample_count += 1
            if sample_count > _MAX_METRIC_SAMPLES:
                raise ValueError
            if sample.name not in _SELECTED_METRICS:
                continue
            if sample.labels.get("model_name") != model_name:
                raise ValueError
            value = float(sample.value)
            if not math.isfinite(value) or value < 0 or value > 1e30:
                raise ValueError
            totals[sample.name] = totals.get(sample.name, 0.0) + value
    if not _REQUIRED_METRICS.issubset(totals):
        raise ValueError
    return totals


def _exact_counter(totals: dict[str, float], name: str, expected: int) -> int:
    value = totals[name]
    if not value.is_integer() or int(value) != expected:
        raise ValueError
    return int(value)


def _probe_initial_model(
    client: HTTPClient,
    identity: AzureContainerAppCandidateIdentity,
    timeout_seconds: float,
    trace_sink: Callable[[ContainerAppBackendTrace], None],
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Retry only discovery timeouts while emitting at least 30-second progress."""
    deadline = monotonic() + timeout_seconds
    first_attempt = True
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BackendInfrastructureError(BackendFailure.TIMEOUT)
        attempt_timeout = min(
            _DISCOVERY_ATTEMPT_SECONDS,
            timeout_seconds if first_attempt else remaining,
        )
        first_attempt = False
        try:
            probe_model(
                client,
                identity,
                attempt_timeout,
                trace_sink,
                initial=True,
            )
            return
        except BackendInfrastructureError as error:
            if error.failure is not BackendFailure.TIMEOUT or monotonic() >= deadline:
                raise
            emit_trace(
                trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.DISCOVERY_PENDING,
                    failure=BackendFailure.TIMEOUT,
                ),
            )
            delay = min(_DISCOVERY_RETRY_SECONDS, deadline - monotonic())
            if delay <= 0:
                raise
            sleep(delay)


class AzureContainerAppCandidateBackend:
    """One exact self-hosted vLLM candidate with no fallback behavior."""

    def __init__(
        self,
        *,
        identity: AzureContainerAppCandidateIdentity,
        client: HTTPClient,
        trace_sink: Callable[[ContainerAppBackendTrace], None],
    ) -> None:
        """Bind one verified candidate identity to an owned HTTP client."""
        self._identity = identity
        self._client = client
        self._trace_sink = trace_sink
        self._closed = False
        self._requests_started = 0
        self._responses_received = 0
        self._responses_accepted = 0
        self._requests_failed = 0
        self._provider_input_tokens = 0
        self._provider_output_tokens = 0
        self._provider_total_tokens = 0

    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity:
        """Return the immutable deployment identity verified at construction."""
        return self._identity

    @property
    def accounting(self) -> ContainerAppBackendAccounting:
        """Return content-free cumulative request evidence."""
        return ContainerAppBackendAccounting(
            requests_started=self._requests_started,
            responses_received=self._responses_received,
            responses_accepted=self._responses_accepted,
            requests_failed=self._requests_failed,
            provider_input_tokens=self._provider_input_tokens,
            provider_output_tokens=self._provider_output_tokens,
            provider_total_tokens=self._provider_total_tokens,
        )

    def _approved_prompt(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> tuple[str, str | None, str | None, str | None]:
        """Validate request bounds and recheck the project-private capability."""
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        if not isinstance(request, AzureApprovedPrompt):
            raise AzurePromptApprovalError
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1 <= max_output_tokens <= MAX_PROVIDER_TOKENS
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.0 < float(timeout_seconds) <= 3_600.0
        ):
            raise ValueError("Container App generation limits are invalid")
        try:
            envelope = request._reveal_envelope_after_recheck()
            if envelope is not None:
                return (
                    envelope.request_text,
                    envelope.response_instruction,
                    envelope.response_schema_json,
                    envelope.envelope_digest,
                )
            prompt, instruction, schema = request._reveal_generation_after_recheck()
            return prompt, instruction, schema, None
        except AzurePromptApprovalError:
            emit_trace(
                self._trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.APPROVAL_BLOCKED,
                    candidate_digest=self._identity.identity_digest,
                ),
            )
            raise

    def _invoke_generation(
        self,
        prompt: str,
        response_instruction: str | None,
        response_schema_json: str | None,
        envelope_digest: str | None,
        request_number: int,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> object:
        """Post one deterministic, non-streaming vLLM request."""
        messages = [{"role": "user", "content": prompt}]
        payload: dict[str, object] = {
            "model": self._identity.model_name,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "stream": False,
        }
        if response_instruction is not None:
            if response_schema_json is None:
                raise BackendInfrastructureError(BackendFailure.INTERNAL)
            messages.insert(0, {"role": "system", "content": response_instruction})
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "gludd_proposal_batch",
                    "schema": json.loads(response_schema_json),
                    "strict": True,
                },
            }
        try:
            return request_json(
                self._client.post,
                "/v1/chat/completions",
                timeout_seconds=timeout_seconds,
                maximum_bytes=MAX_RESPONSE_BYTES,
                payload=payload,
            )
        except BackendInfrastructureError as error:
            self._requests_failed += 1
            emit_failure(
                self._trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    envelope_digest=envelope_digest,
                    request_number=request_number,
                    failure=error.failure,
                    response_failure=getattr(error, "response_failure", None),
                    http_status=getattr(error, "http_status", 0),
                ),
            )
            raise

    def _accept_generation(
        self,
        response_payload: object,
        envelope_digest: str | None,
        request_number: int,
        *,
        max_output_tokens: int,
    ) -> AzureCandidateResponse:
        """Validate one response and update exact content-free accounting."""
        self._responses_received += 1
        try:
            accepted = validated_chat_response(
                response_payload,
                self._identity,
                max_output_tokens=max_output_tokens,
            )
        except Exception:
            failure = BackendFailure.INVALID_RESPONSE
            self._requests_failed += 1
            emit_failure(
                self._trace_sink,
                ContainerAppBackendTrace(
                    ContainerAppTraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    envelope_digest=envelope_digest,
                    request_number=request_number,
                    failure=failure,
                    response_failure=ContainerAppResponseFailure.CHAT_CONTRACT,
                ),
            )
            raise BackendInfrastructureError(failure) from None
        self._responses_accepted += 1
        self._provider_input_tokens += accepted.input_tokens
        self._provider_output_tokens += accepted.output_tokens
        self._provider_total_tokens += accepted.total_tokens
        emit_trace(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.RESPONSE_ACCEPTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
                input_tokens=accepted.input_tokens,
                output_tokens=accepted.output_tokens,
                total_tokens=accepted.total_tokens,
            ),
        )
        return accepted

    def generate(
        self,
        request: AzureApprovedPrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        """Recheck privacy and model identity, then invoke exactly once."""
        prompt, response_instruction, response_schema_json, envelope_digest = (
            self._approved_prompt(
                request,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        )
        timeout = float(timeout_seconds)
        probe_model(
            self._client,
            self._identity,
            timeout,
            self._trace_sink,
            initial=False,
        )
        request_number = self._requests_started + 1
        emit_trace(
            self._trace_sink,
            ContainerAppBackendTrace(
                ContainerAppTraceEvent.REQUEST_STARTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
            ),
        )
        self._requests_started += 1
        response_payload = self._invoke_generation(
            prompt,
            response_instruction,
            response_schema_json,
            envelope_digest,
            request_number,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout,
        )
        return self._accept_generation(
            response_payload,
            envelope_digest,
            request_number,
            max_output_tokens=max_output_tokens,
        )

    def attest_runtime_gpu(
        self,
        response: AzureCandidateResponse,
        *,
        timeout_seconds: float,
    ) -> VLLMRuntimeGPUEvidence:
        """Bind exact vLLM counters to one response from the canary-gated revision."""
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        if not isinstance(response, AzureCandidateResponse) or (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0.0 < float(timeout_seconds) <= 120.0
        ):
            raise ValueError("runtime GPU evidence limits are invalid")
        try:
            payload = request_text(
                self._client.get,
                "/metrics",
                timeout_seconds=float(timeout_seconds),
                maximum_bytes=_MAX_METRICS_BYTES,
                content_types=_METRICS_CONTENT_TYPES,
            )
            totals = _vllm_metric_totals(payload, self._identity.model_name)
            prompt_tokens = _exact_counter(
                totals,
                _PROMPT_TOKENS,
                response.input_tokens,
            )
            generation_tokens = _exact_counter(
                totals,
                _GENERATION_TOKENS,
                response.output_tokens,
            )
            successful = totals[_SUCCESSFUL_REQUESTS]
            if (
                not successful.is_integer()
                or not 1 <= int(successful) <= 10_000
            ):
                raise ValueError
            flops = totals.get(_ESTIMATED_FLOPS)
            return VLLMRuntimeGPUEvidence(
                candidate_digest=self._identity.identity_digest,
                prompt_tokens=prompt_tokens,
                generation_tokens=generation_tokens,
                successful_requests=int(successful),
                estimated_flops_per_gpu=flops,
            )
        except BackendInfrastructureError:
            raise
        except Exception:
            raise BackendInfrastructureError(BackendFailure.INVALID_RESPONSE) from None

    def close(self) -> None:
        """Idempotently close the sole HTTP transport owned by this backend."""
        if self._closed:
            return
        self._closed = True
        try:
            self._client.close()
        except Exception:
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None

    def __enter__(self) -> AzureContainerAppCandidateBackend:
        """Return this backend as an owned context resource."""
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        """Close the owned HTTP transport when the context exits."""
        self.close()


def build_azure_containerapp_candidate_backend(
    identity: AzureContainerAppCandidateIdentity,
    *,
    client: HTTPClient | None = None,
    discovery_timeout_seconds: float = 30.0,
    trace_sink: Callable[[ContainerAppBackendTrace], None] | None = None,
    _monotonic: Callable[[], float] = time.monotonic,
    _sleep: Callable[[float], None] = time.sleep,
) -> AzureContainerAppCandidateBackend:
    """Probe and bind one exact Container App candidate without fallback."""
    if type(identity) is not AzureContainerAppCandidateIdentity:
        raise ValueError("identity must be an AzureContainerAppCandidateIdentity")
    if (
        isinstance(discovery_timeout_seconds, bool)
        or not isinstance(discovery_timeout_seconds, (int, float))
        or not 0.0 < float(discovery_timeout_seconds) <= 120.0
    ):
        raise ValueError("discovery timeout must be in 0..120")
    selected_client = new_httpx_client(identity) if client is None else client
    if not all(
        callable(getattr(selected_client, member, None))
        for member in ("get", "post", "close")
    ):
        raise ValueError("client must implement bounded HTTP get, post, and close")
    selected_sink = discard_trace if trace_sink is None else trace_sink
    if not all(callable(callback) for callback in (selected_sink, _monotonic, _sleep)):
        raise ValueError("trace sink must be callable")
    try:
        _probe_initial_model(
            selected_client,
            identity,
            float(discovery_timeout_seconds),
            selected_sink,
            monotonic=_monotonic,
            sleep=_sleep,
        )
    except BaseException:
        with suppress(Exception):
            selected_client.close()
        raise
    return AzureContainerAppCandidateBackend(
        identity=identity,
        client=selected_client,
        trace_sink=selected_sink,
    )


__all__ = (
    "AzureContainerAppCandidateBackend",
    "ContainerAppBackendAccounting",
    "ContainerAppBackendTrace",
    "ContainerAppTraceEvent",
    "build_azure_containerapp_candidate_backend",
)
