"""Privacy-gated OpenAI-compatible backend for one Azure Container App.

The backend is bound to an immutable app revision, image digest, model revision,
and HTTPS origin.  It probes only ``/v1/models`` and posts only to
``/v1/chat/completions``; there is no provider routing or fallback surface.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

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
    ContainerAppTraceEvent,
    HTTPClient,
    discard_trace,
    emit_failure,
    emit_trace,
    new_httpx_client,
    probe_model,
    request_json,
    validated_chat_response,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)


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
    ) -> str:
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
            return request._reveal_after_recheck()
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
        request_number: int,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> object:
        """Post one deterministic, non-streaming vLLM request."""
        payload = {
            "model": self._identity.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": 0,
            "stream": False,
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
                    request_number=request_number,
                    failure=error.failure,
                ),
            )
            raise

    def _accept_generation(
        self,
        response_payload: object,
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
                    request_number=request_number,
                    failure=failure,
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
        prompt = self._approved_prompt(
            request,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
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
                request_number=request_number,
            ),
        )
        self._requests_started += 1
        response_payload = self._invoke_generation(
            prompt,
            request_number,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout,
        )
        return self._accept_generation(
            response_payload,
            request_number,
            max_output_tokens=max_output_tokens,
        )

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
    if not callable(selected_sink):
        raise ValueError("trace sink must be callable")
    try:
        probe_model(
            selected_client,
            identity,
            float(discovery_timeout_seconds),
            selected_sink,
            initial=True,
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
