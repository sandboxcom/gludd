"""Privacy-gated native backend for one FreeLLMAPI catalog candidate.

FreeLLMAPI contributes only authenticated discovery evidence.  This adapter
binds that evidence to one disabled Gludd ``ModelProfile`` and invokes it only
through the existing ``ModelGateway``.  It owns no router, fallback, transport,
server, persistence, or ambient candidate registration.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from general_ludd.models.freellmapi_profiles import (
    FreeModelProbeProfile,
    SecretsResolver,
    build_freellmapi_probe_gateway,
    catalog_free_tier_identity,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse
from general_ludd.self_improve.azure_backend import (
    ApprovedCandidatePrompt,
    CandidateBackendAccounting,
    CandidatePromptApprovalError,
    CandidateResponse,
    censor_candidate_backend_error,
)
from general_ludd.self_improve.model_candidates import (
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    CatalogFreeTierCandidateIdentity,
)

_MAX_PROVIDER_TOKENS: Final = 100_000_000
_MAX_TIMEOUT_SECONDS: Final = 3_600.0

class FreeLLMAPITraceEvent(StrEnum):
    """Content-free transitions for one explicitly admitted catalog trial."""

    IDENTITY_DRIFT = "freellmapi_identity_drift"
    APPROVAL_BLOCKED = "freellmapi_approval_blocked"
    REQUEST_STARTED = "freellmapi_request_started"
    RESPONSE_ACCEPTED = "freellmapi_response_accepted"
    REQUEST_FAILED = "freellmapi_request_failed"


@dataclass(frozen=True, slots=True)
class FreeLLMAPIBackendTrace:
    """One content-free native-gateway backend transition."""

    event: FreeLLMAPITraceEvent
    candidate_digest: str
    envelope_digest: str | None = None
    request_number: int = 0
    failure: BackendFailure | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def _discard_trace(_trace: FreeLLMAPIBackendTrace) -> None:
    return


def _emit(
    trace_sink: Callable[[FreeLLMAPIBackendTrace], None],
    trace: FreeLLMAPIBackendTrace,
) -> None:
    try:
        trace_sink(trace)
    except Exception:
        raise BackendInfrastructureError(BackendFailure.INTERNAL) from None


def _emit_failure(
    trace_sink: Callable[[FreeLLMAPIBackendTrace], None],
    trace: FreeLLMAPIBackendTrace,
) -> None:
    try:
        _emit(trace_sink, trace)
    except BackendInfrastructureError:
        return


def _validated_token_count(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_PROVIDER_TOKENS
    ):
        raise ValueError
    return value


class FreeLLMAPICandidateBackend:
    """One exact disabled native profile with no routing or fallback surface."""

    def __init__(
        self,
        *,
        binding: FreeModelProbeProfile,
        identity: CatalogFreeTierCandidateIdentity,
        gateway: ModelGateway,
        trace_sink: Callable[[FreeLLMAPIBackendTrace], None] | None = None,
    ) -> None:
        """Bind authenticated catalog evidence to one owned native gateway."""
        expected_identity = catalog_free_tier_identity(binding)
        if (
            type(identity) is not CatalogFreeTierCandidateIdentity
            or identity.identity_digest != expected_identity.identity_digest
        ):
            raise ValueError("identity must exactly match the FreeLLMAPI binding")
        if not callable(getattr(gateway, "call_model", None)) or not callable(
            getattr(gateway, "close", None)
        ):
            raise ValueError("gateway must expose ModelGateway call and close methods")
        selected_sink = _discard_trace if trace_sink is None else trace_sink
        if not callable(selected_sink):
            raise ValueError("trace_sink must be callable")
        self._binding = binding
        self._identity = identity
        self._gateway = gateway
        self._trace_sink = selected_sink
        self._profile_snapshot = binding.profile.model_copy(deep=True)
        self._profile_id = binding.profile.model_profile_id
        self._model_name = binding.profile.model_name
        self._max_response_bytes = binding.profile.max_response_bytes
        self._max_profile_output_tokens = binding.profile.max_output_tokens
        self._closed = False
        self._requests_started = 0
        self._responses_received = 0
        self._responses_accepted = 0
        self._requests_failed = 0
        self._provider_input_tokens = 0
        self._provider_output_tokens = 0
        self._provider_total_tokens = 0

    @property
    def candidate_identity(self) -> CatalogFreeTierCandidateIdentity:
        """Return the exact signed-catalog row bound at construction."""
        return self._identity

    @property
    def accounting(self) -> CandidateBackendAccounting:
        """Return content-free cumulative request and provider-token evidence."""
        return CandidateBackendAccounting(
            requests_started=self._requests_started,
            responses_received=self._responses_received,
            responses_accepted=self._responses_accepted,
            requests_failed=self._requests_failed,
            provider_input_tokens=self._provider_input_tokens,
            provider_output_tokens=self._provider_output_tokens,
            provider_total_tokens=self._provider_total_tokens,
        )

    def _recheck_binding(self) -> None:
        try:
            current = catalog_free_tier_identity(self._binding)
        except Exception:
            current = None
        if (
            current is None
            or current.identity_digest != self._identity.identity_digest
            or self._binding.profile != self._profile_snapshot
        ):
            _emit(
                self._trace_sink,
                FreeLLMAPIBackendTrace(
                    event=FreeLLMAPITraceEvent.IDENTITY_DRIFT,
                    candidate_digest=self._identity.identity_digest,
                ),
            )
            raise BackendPolicyError(BackendPolicyFailure.IDENTITY_DRIFT)

    def _approved_generation(
        self,
        request: ApprovedCandidatePrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> tuple[list[dict[str, str]], dict[str, object] | None, str | None]:
        """Recheck binding and privacy before revealing transport-safe content."""
        if self._closed:
            raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or not 1
            <= max_output_tokens
            <= min(_MAX_PROVIDER_TOKENS, self._max_profile_output_tokens)
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0.0 < float(timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("FreeLLMAPI generation limits are invalid")
        if not isinstance(request, ApprovedCandidatePrompt):
            raise CandidatePromptApprovalError
        self._recheck_binding()
        try:
            envelope = request._reveal_envelope_after_recheck()
            if envelope is None:
                prompt, response_instruction, response_schema_json = (
                    request._reveal_generation_after_recheck()
                )
                envelope_digest = None
            else:
                prompt = envelope.request_text
                response_instruction = envelope.response_instruction
                response_schema_json = envelope.response_schema_json
                envelope_digest = envelope.envelope_digest
        except CandidatePromptApprovalError:
            _emit(
                self._trace_sink,
                FreeLLMAPIBackendTrace(
                    event=FreeLLMAPITraceEvent.APPROVAL_BLOCKED,
                    candidate_digest=self._identity.identity_digest,
                ),
            )
            raise

        messages = [{"role": "user", "content": prompt}]
        model_kwargs: dict[str, object] | None = None
        if response_instruction is not None:
            if response_schema_json is None:
                raise BackendInfrastructureError(BackendFailure.INTERNAL)
            messages.insert(0, {"role": "system", "content": response_instruction})
            model_kwargs = {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gludd_proposal_batch",
                        "schema": json.loads(response_schema_json),
                        "strict": True,
                    },
                }
            }
        return messages, model_kwargs, envelope_digest

    def _accepted_response(
        self,
        response: object,
        *,
        max_output_tokens: int,
    ) -> CandidateResponse:
        if not isinstance(response, ModelResponse):
            raise ValueError
        usage = response.usage_metadata
        input_tokens = _validated_token_count(usage.get("input_tokens"))
        output_tokens = _validated_token_count(usage.get("output_tokens"))
        total_tokens = _validated_token_count(usage.get("total_tokens"))
        cost = response.cost_estimate
        if (
            type(response.content) is not str
            or not response.content.strip()
            or len(response.content.encode("utf-8")) > self._max_response_bytes
            or output_tokens > max_output_tokens
            or input_tokens + output_tokens != total_tokens
            or response.model_name != self._model_name
            or isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or float(cost) != 0.0
        ):
            raise ValueError
        return CandidateResponse(
            text=response.content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def generate(
        self,
        request: ApprovedCandidatePrompt,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> CandidateResponse:
        """Recheck privacy and invoke the exact disabled profile once."""
        messages, model_kwargs, envelope_digest = self._approved_generation(
            request,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        request_number = self._requests_started + 1
        _emit(
            self._trace_sink,
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.REQUEST_STARTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
            ),
        )
        self._requests_started += 1
        try:
            if model_kwargs is None:
                response = self._gateway.call_model(
                    self._profile_id,
                    messages,
                    requested_max_output_tokens=max_output_tokens,
                    timeout_seconds=float(timeout_seconds),
                    max_tokens=max_output_tokens,
                    temperature=0,
                )
            else:
                response = self._gateway.call_model(
                    self._profile_id,
                    messages,
                    requested_max_output_tokens=max_output_tokens,
                    timeout_seconds=float(timeout_seconds),
                    max_tokens=max_output_tokens,
                    temperature=0,
                    model_kwargs=model_kwargs,
                )
        except Exception as error:
            self._requests_failed += 1
            censored = censor_candidate_backend_error(error)
            _emit_failure(
                self._trace_sink,
                FreeLLMAPIBackendTrace(
                    event=FreeLLMAPITraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    envelope_digest=envelope_digest,
                    request_number=request_number,
                    failure=censored.failure,
                ),
            )
            raise censored from None

        self._responses_received += 1
        try:
            accepted = self._accepted_response(
                response,
                max_output_tokens=max_output_tokens,
            )
        except Exception:
            self._requests_failed += 1
            failure = BackendFailure.INVALID_RESPONSE
            _emit_failure(
                self._trace_sink,
                FreeLLMAPIBackendTrace(
                    event=FreeLLMAPITraceEvent.REQUEST_FAILED,
                    candidate_digest=self._identity.identity_digest,
                    envelope_digest=envelope_digest,
                    request_number=request_number,
                    failure=failure,
                ),
            )
            raise BackendInfrastructureError(failure) from None

        self._responses_accepted += 1
        self._provider_input_tokens += accepted.input_tokens
        self._provider_output_tokens += accepted.output_tokens
        self._provider_total_tokens += accepted.total_tokens
        _emit(
            self._trace_sink,
            FreeLLMAPIBackendTrace(
                event=FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
                candidate_digest=self._identity.identity_digest,
                envelope_digest=envelope_digest,
                request_number=request_number,
                input_tokens=accepted.input_tokens,
                output_tokens=accepted.output_tokens,
                total_tokens=accepted.total_tokens,
            ),
        )
        return accepted

    def close(self) -> None:
        """Idempotently close the sole gateway owned by this backend."""
        if self._closed:
            return
        self._closed = True
        try:
            self._gateway.close()
        except Exception:
            raise BackendInfrastructureError(BackendFailure.INTERNAL) from None

    def __enter__(self) -> FreeLLMAPICandidateBackend:
        """Return this backend as an owned context resource."""
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        """Close the owned gateway when the context exits."""
        self.close()


def build_freellmapi_candidate_backend(
    binding: FreeModelProbeProfile,
    *,
    secrets_manager: SecretsResolver,
    trace_sink: Callable[[FreeLLMAPIBackendTrace], None] | None = None,
) -> FreeLLMAPICandidateBackend:
    """Build one isolated gateway-backed catalog trial without routing it."""
    identity = catalog_free_tier_identity(binding)
    selected_sink = _discard_trace if trace_sink is None else trace_sink
    if not callable(selected_sink):
        raise ValueError("trace_sink must be callable")
    gateway = build_freellmapi_probe_gateway(
        binding,
        secrets_manager=secrets_manager,
    )
    try:
        return FreeLLMAPICandidateBackend(
            binding=binding,
            identity=identity,
            gateway=gateway,
            trace_sink=selected_sink,
        )
    except BaseException:
        with suppress(Exception):
            gateway.close()
        raise


__all__ = (
    "FreeLLMAPIBackendTrace",
    "FreeLLMAPICandidateBackend",
    "FreeLLMAPITraceEvent",
    "build_freellmapi_candidate_backend",
)
