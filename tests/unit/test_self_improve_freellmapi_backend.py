"""Native Gludd backend for one authenticated FreeLLMAPI catalog candidate."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.models.freellmapi_candidates import FreeModelCandidateSeed
from general_ludd.models.freellmapi_catalog import CatalogLimits
from general_ludd.models.freellmapi_profiles import (
    FreeModelProbeProfile,
    build_freellmapi_probe_profiles,
    catalog_free_tier_identity,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse
from general_ludd.self_improve import freellmapi_backend as freellmapi_backend_module
from general_ludd.self_improve.azure_backend import (
    ApprovedCandidatePrompt,
    CandidateBackendAccounting,
    CandidatePromptApprovalError,
    CandidateResponse,
)
from general_ludd.self_improve.freellmapi_backend import (
    FreeLLMAPIBackendTrace,
    FreeLLMAPICandidateBackend,
    FreeLLMAPITraceEvent,
    build_freellmapi_candidate_backend,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.model_candidates import (
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    BoundedCandidateSession,
    CatalogFreeTierCandidateIdentity,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_PROMPT = "approved source that must not appear in traces"
_PROVIDER_DETAIL = "provider leaked credential and endpoint"


class _Gateway:
    """Small recording seam with the existing ModelGateway call shape."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, list[dict[str, str]], dict[str, object]]] = []
        self.close_calls = 0

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> ModelResponse:
        self.calls.append((profile_id, messages, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return cast(ModelResponse, self.result)

    def close(self) -> None:
        self.close_calls += 1


class _ProviderError(Exception):
    def __init__(self, status_code: int | None = None) -> None:
        super().__init__(_PROVIDER_DETAIL)
        self.status_code = status_code


class RateLimitError(Exception):
    """Name-compatible fake for gateway provider classification."""


class _ExplodingStatusError(Exception):
    @property
    def status_code(self) -> int:
        raise RuntimeError(_PROVIDER_DETAIL)


class _LeakyBackendError(BackendInfrastructureError):
    def __str__(self) -> str:
        return _PROVIDER_DETAIL


class _ExplodingBackendError(BackendInfrastructureError):
    def __init__(self) -> None:
        RuntimeError.__init__(self, _PROVIDER_DETAIL)

    @property
    def failure(self) -> BackendFailure:
        raise RuntimeError(_PROVIDER_DETAIL)


class _InvalidBackendError(BackendInfrastructureError):
    def __init__(self) -> None:
        RuntimeError.__init__(self, _PROVIDER_DETAIL)
        self.failure = cast(BackendFailure, "invalid")


class _SecretsResolver:
    def resolve(self, _alias_name: str) -> str | None:
        return None


def _binding(
    *,
    model_id: str = "meta-llama/llama-3.3-70b-instruct:free",
) -> FreeModelProbeProfile:
    candidate = FreeModelCandidateSeed(
        platform="openrouter",
        model_id=model_id,
        display_name="Free test model",
        intelligence_rank=1,
        speed_rank=2,
        size_label="70B",
        limits=CatalogLimits(rpm=20, rpd=200, tpm=40_000, tpd=None),
        monthly_token_budget=None,
        context_window=32_768,
        supports_vision=False,
        supports_tools=True,
        catalog_version="2026.09.16",
        catalog_payload_sha256="a" * 64,
        quirk_slugs=(),
    )
    return build_freellmapi_probe_profiles((candidate,))[0]


def _response(
    *,
    content: str = "proposal text",
    input_tokens: object = 11,
    output_tokens: object = 7,
    total_tokens: object = 18,
    model_name: str | None = None,
    cost_estimate: float = 0.0,
) -> ModelResponse:
    binding = _binding()
    return ModelResponse(
        content=content,
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        cost_estimate=cost_estimate,
        model_name=model_name or binding.profile.model_name,
    )


def _approved_prompt(tmp_path: Path) -> ApprovedCandidatePrompt:
    source = tmp_path / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        CandidatePromptApprovalError,
    )
    return ApprovedCandidatePrompt.approve(
        prompt=_PROMPT,
        source_paths=("src/approved.py",),
        policy_guard=guard,
    )


def _approved_envelope(tmp_path: Path) -> tuple[ApprovedCandidatePrompt, ManagedCandidateProposalEnvelope]:
    source = tmp_path / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        CandidatePromptApprovalError,
    )
    envelope = ManagedCandidateProposalEnvelope(
        request_text=_PROMPT,
        request_contract_json='{"contract":"trusted"}',
        response_instruction="Return the exact approved JSON object.",
        response_schema_json=(
            '{"additionalProperties":false,"properties":{"ok":{"const":true,'
            '"type":"boolean"}},"required":["ok"],"type":"object"}'
        ),
        protocol_digest="c" * 64,
        sampling_digest="d" * 64,
    )
    return (
        ApprovedCandidatePrompt.approve_envelope(
            envelope=envelope,
            source_paths=("src/approved.py",),
            policy_guard=guard,
        ),
        envelope,
    )


def _backend(
    gateway: _Gateway,
    *,
    binding: FreeModelProbeProfile | None = None,
    identity: CatalogFreeTierCandidateIdentity | None = None,
    trace_sink: Callable[[FreeLLMAPIBackendTrace], None] | None = None,
) -> FreeLLMAPICandidateBackend:
    selected_binding = binding or _binding()
    return FreeLLMAPICandidateBackend(
        binding=selected_binding,
        identity=identity or catalog_free_tier_identity(selected_binding),
        gateway=cast(ModelGateway, gateway),
        trace_sink=trace_sink,
    )


def _budget() -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=1,
        max_input_tokens=100,
        max_output_tokens=50,
        max_total_tokens=150,
        max_cost_microusd=0,
        timeout_seconds=4.5,
    )


def test_candidate_backend_calls_only_bound_disabled_profile_once(
    tmp_path: Path,
) -> None:
    gateway = _Gateway(_response())
    traces: list[FreeLLMAPIBackendTrace] = []
    binding = _binding()
    backend = _backend(gateway, binding=binding, trace_sink=traces.append)

    result = backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=50,
        timeout_seconds=4.5,
    )

    assert result == CandidateResponse(
        text="proposal text",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )
    assert binding.profile.enabled is False
    assert binding.profile.fallback_profiles == []
    assert gateway.calls == [
        (
            binding.profile.model_profile_id,
            [{"role": "user", "content": _PROMPT}],
            {
                "requested_max_output_tokens": 50,
                "timeout_seconds": 4.5,
                "max_tokens": 50,
                "temperature": 0,
            },
        )
    ]
    assert backend.accounting == CandidateBackendAccounting(
        requests_started=1,
        responses_received=1,
        responses_accepted=1,
        requests_failed=0,
        provider_input_tokens=11,
        provider_output_tokens=7,
        provider_total_tokens=18,
    )
    assert [trace.event for trace in traces] == [
        FreeLLMAPITraceEvent.REQUEST_STARTED,
        FreeLLMAPITraceEvent.RESPONSE_ACCEPTED,
    ]
    assert _PROMPT not in repr(traces)
    assert traces[-1].candidate_digest == backend.candidate_identity.identity_digest
    assert traces[-1].total_tokens == 18


def test_builder_uses_existing_single_profile_gateway_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    gateway = _Gateway(_response())
    resolver = _SecretsResolver()
    calls: list[tuple[FreeModelProbeProfile, object]] = []

    def build_gateway(
        selected: FreeModelProbeProfile,
        *,
        secrets_manager: object,
    ) -> ModelGateway:
        calls.append((selected, secrets_manager))
        return cast(ModelGateway, gateway)

    monkeypatch.setattr(
        freellmapi_backend_module,
        "build_freellmapi_probe_gateway",
        build_gateway,
    )

    backend = build_freellmapi_candidate_backend(
        binding,
        secrets_manager=resolver,
    )
    backend.close()

    assert calls == [(binding, resolver)]
    assert backend.candidate_identity == catalog_free_tier_identity(binding)
    assert gateway.close_calls == 1


def test_backend_preserves_approved_structured_envelope(tmp_path: Path) -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)
    approved, envelope = _approved_envelope(tmp_path)

    backend.generate(approved, max_output_tokens=20, timeout_seconds=3.0)

    _profile_id, messages, kwargs = gateway.calls[0]
    assert messages == [
        {"role": "system", "content": envelope.response_instruction},
        {"role": "user", "content": envelope.request_text},
    ]
    assert kwargs["model_kwargs"] == {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "gludd_proposal_batch",
                "schema": {
                    "additionalProperties": False,
                    "properties": {"ok": {"const": True, "type": "boolean"}},
                    "required": ["ok"],
                    "type": "object",
                },
                "strict": True,
            },
        }
    }


def test_session_requires_explicit_external_opt_in_before_gateway_call(
    tmp_path: Path,
) -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)
    session = BoundedCandidateSession(
        backend,
        _budget(),
        azure_enabled=False,
        external_enabled=False,
    )

    with pytest.raises(BackendPolicyError) as captured:
        session.generate(
            _approved_prompt(tmp_path),
            input_tokens=10,
            max_output_tokens=20,
            estimated_cost_microusd=0,
        )

    assert captured.value.failure is BackendPolicyFailure.EXTERNAL_OPT_IN_REQUIRED
    assert gateway.calls == []


def test_session_forwards_existing_budget_bounds_when_opted_in(tmp_path: Path) -> None:
    gateway = _Gateway(_response())
    session = BoundedCandidateSession(
        _backend(gateway),
        _budget(),
        azure_enabled=False,
        external_enabled=True,
    )

    session.generate(
        _approved_prompt(tmp_path),
        input_tokens=10,
        max_output_tokens=20,
        estimated_cost_microusd=0,
    )

    assert gateway.calls[0][2]["requested_max_output_tokens"] == 20
    assert gateway.calls[0][2]["timeout_seconds"] == 4.5


def test_prompt_capability_is_rechecked_before_gateway_observes_content(
    tmp_path: Path,
) -> None:
    gateway = _Gateway(_response())
    traces: list[FreeLLMAPIBackendTrace] = []
    backend = _backend(gateway, trace_sink=traces.append)
    prompt = _approved_prompt(tmp_path)
    object.__setattr__(prompt, "approval_digest", "b" * 64)

    with pytest.raises(CandidatePromptApprovalError):
        backend.generate(prompt, max_output_tokens=20, timeout_seconds=3.0)

    assert gateway.calls == []
    assert [trace.event for trace in traces] == [
        FreeLLMAPITraceEvent.APPROVAL_BLOCKED
    ]
    assert _PROMPT not in repr(traces)


def test_profile_identity_is_rechecked_before_prompt_reveal(tmp_path: Path) -> None:
    gateway = _Gateway(_response())
    binding = _binding()
    backend = _backend(gateway, binding=binding)
    binding.profile.enabled = True

    with pytest.raises(BackendPolicyError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert gateway.calls == []


def test_constructor_rejects_mismatched_catalog_identity() -> None:
    first = _binding()
    second = _binding(model_id="other/free-model")

    with pytest.raises(ValueError, match="identity"):
        _backend(
            _Gateway(_response()),
            binding=first,
            identity=catalog_free_tier_identity(second),
        )


def test_constructor_rejects_malformed_gateway_or_trace_sink() -> None:
    binding = _binding()
    identity = catalog_free_tier_identity(binding)

    with pytest.raises(ValueError, match="gateway"):
        FreeLLMAPICandidateBackend(
            binding=binding,
            identity=identity,
            gateway=cast(ModelGateway, object()),
        )
    with pytest.raises(ValueError, match="trace_sink"):
        FreeLLMAPICandidateBackend(
            binding=binding,
            identity=identity,
            gateway=cast(ModelGateway, _Gateway(_response())),
            trace_sink=cast(Callable[[FreeLLMAPIBackendTrace], None], object()),
        )


@pytest.mark.parametrize(
    ("response", "max_output_tokens"),
    [
        (_response(content=""), 20),
        (_response(input_tokens=True), 20),
        (_response(output_tokens=21, total_tokens=32), 20),
        (_response(total_tokens=19), 20),
        (_response(model_name="other-model"), 20),
        (_response(cost_estimate=0.01), 20),
    ],
)
def test_invalid_gateway_response_is_censored_and_not_accounted(
    tmp_path: Path,
    response: ModelResponse,
    max_output_tokens: int,
) -> None:
    gateway = _Gateway(response)
    traces: list[FreeLLMAPIBackendTrace] = []
    backend = _backend(gateway, trace_sink=traces.append)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=max_output_tokens,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert backend.accounting.responses_received == 1
    assert backend.accounting.responses_accepted == 0
    assert backend.accounting.requests_failed == 1
    assert traces[-1].event is FreeLLMAPITraceEvent.REQUEST_FAILED
    assert traces[-1].failure is BackendFailure.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_ProviderError(429), BackendFailure.RATE_LIMITED),
        (_ProviderError(503), BackendFailure.UNAVAILABLE),
        (TimeoutError(_PROVIDER_DETAIL), BackendFailure.TIMEOUT),
        (ImportError(_PROVIDER_DETAIL), BackendFailure.UNAVAILABLE),
        (RateLimitError(_PROVIDER_DETAIL), BackendFailure.RATE_LIMITED),
        (_ExplodingStatusError(_PROVIDER_DETAIL), BackendFailure.INTERNAL),
        (_LeakyBackendError(BackendFailure.AUTHENTICATION), BackendFailure.AUTHENTICATION),
        (_ExplodingBackendError(), BackendFailure.INTERNAL),
        (_InvalidBackendError(), BackendFailure.INTERNAL),
        (_ProviderError(), BackendFailure.INTERNAL),
    ],
)
def test_gateway_failures_are_typed_censored_and_content_free(
    tmp_path: Path,
    error: BaseException,
    expected: BackendFailure,
) -> None:
    traces: list[FreeLLMAPIBackendTrace] = []
    backend = _backend(_Gateway(error), trace_sink=traces.append)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert type(captured.value) is BackendInfrastructureError
    assert captured.value.failure is expected
    assert _PROVIDER_DETAIL not in str(captured.value)
    assert _PROVIDER_DETAIL not in repr(traces)
    assert [trace.event for trace in traces] == [
        FreeLLMAPITraceEvent.REQUEST_STARTED,
        FreeLLMAPITraceEvent.REQUEST_FAILED,
    ]


@pytest.mark.parametrize(
    ("max_output_tokens", "timeout_seconds"),
    [(0, 1.0), (True, 1.0), (1, 0.0), (1, float("inf"))],
)
def test_invalid_generation_bounds_fail_before_reveal_or_call(
    tmp_path: Path,
    max_output_tokens: Any,
    timeout_seconds: Any,
) -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)
    prompt = _approved_prompt(tmp_path)

    with pytest.raises(ValueError, match="limits"):
        backend.generate(
            prompt,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )

    assert gateway.calls == []


def test_raw_request_is_denied_before_gateway_call() -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)

    with pytest.raises(CandidatePromptApprovalError):
        backend.generate(
            cast(ApprovedCandidatePrompt, object()),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert gateway.calls == []


def test_missing_structured_schema_fails_closed_before_gateway_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)
    approved = _approved_prompt(tmp_path)
    monkeypatch.setattr(
        ApprovedCandidatePrompt,
        "_reveal_envelope_after_recheck",
        lambda _self: None,
    )
    monkeypatch.setattr(
        ApprovedCandidatePrompt,
        "_reveal_generation_after_recheck",
        lambda _self: (_PROMPT, "structured response", None),
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(approved, max_output_tokens=20, timeout_seconds=3.0)

    assert captured.value.failure is BackendFailure.INTERNAL
    assert gateway.calls == []


def test_non_model_gateway_result_is_censored_as_invalid_response(
    tmp_path: Path,
) -> None:
    backend = _backend(_Gateway(object()))

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE


def test_trace_sink_failure_blocks_call_without_exposing_content(tmp_path: Path) -> None:
    gateway = _Gateway(_response())

    def broken_sink(_trace: FreeLLMAPIBackendTrace) -> None:
        raise RuntimeError(_PROVIDER_DETAIL)

    backend = _backend(gateway, trace_sink=broken_sink)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendFailure.INTERNAL
    assert _PROVIDER_DETAIL not in str(captured.value)
    assert gateway.calls == []


def test_failure_trace_sink_error_never_masks_provider_failure(tmp_path: Path) -> None:
    events: list[FreeLLMAPITraceEvent] = []

    def failing_only_at_terminal(trace: FreeLLMAPIBackendTrace) -> None:
        events.append(trace.event)
        if trace.event is FreeLLMAPITraceEvent.REQUEST_FAILED:
            raise RuntimeError(_PROVIDER_DETAIL)

    backend = _backend(
        _Gateway(_ProviderError(429)),
        trace_sink=failing_only_at_terminal,
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendFailure.RATE_LIMITED
    assert events == [
        FreeLLMAPITraceEvent.REQUEST_STARTED,
        FreeLLMAPITraceEvent.REQUEST_FAILED,
    ]


def test_backend_owns_idempotent_gateway_closure_and_context_manager() -> None:
    gateway = _Gateway(_response())
    backend = _backend(gateway)

    with backend as entered:
        assert entered is backend
    backend.close()

    assert gateway.close_calls == 1
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            cast(ApprovedCandidatePrompt, object()),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )
    assert captured.value.failure is BackendFailure.UNAVAILABLE


def test_gateway_close_failure_is_censored_and_still_idempotent() -> None:
    class _FailingGateway(_Gateway):
        def close(self) -> None:
            super().close()
            raise RuntimeError(_PROVIDER_DETAIL)

    gateway = _FailingGateway(_response())
    backend = _backend(gateway)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.close()
    backend.close()

    assert captured.value.failure is BackendFailure.INTERNAL
    assert _PROVIDER_DETAIL not in str(captured.value)
    assert gateway.close_calls == 1


def test_profile_snapshot_cannot_drift_after_backend_binding(tmp_path: Path) -> None:
    gateway = _Gateway(_response())
    binding = _binding()
    backend = _backend(gateway, binding=binding)
    binding.profile.max_response_bytes += 1

    with pytest.raises(BackendPolicyError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=20,
            timeout_seconds=3.0,
        )

    assert captured.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert gateway.calls == []


def test_builder_rejects_noncallable_sink_before_allocating_gateway() -> None:
    with pytest.raises(ValueError, match="trace_sink"):
        build_freellmapi_candidate_backend(
            _binding(),
            secrets_manager=_SecretsResolver(),
            trace_sink=cast(Callable[[FreeLLMAPIBackendTrace], None], object()),
        )


def test_builder_closes_gateway_when_backend_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingCloseGateway(_Gateway):
        def close(self) -> None:
            super().close()
            raise RuntimeError(_PROVIDER_DETAIL)

    gateway = _FailingCloseGateway(_response())
    monkeypatch.setattr(
        freellmapi_backend_module,
        "build_freellmapi_probe_gateway",
        lambda _binding, *, secrets_manager: cast(ModelGateway, gateway),
    )

    def reject_backend(**_kwargs: object) -> FreeLLMAPICandidateBackend:
        raise ValueError("construction rejected")

    monkeypatch.setattr(
        freellmapi_backend_module,
        "FreeLLMAPICandidateBackend",
        reject_backend,
    )

    with pytest.raises(ValueError, match="construction rejected"):
        build_freellmapi_candidate_backend(
            _binding(),
            secrets_manager=_SecretsResolver(),
        )

    assert gateway.close_calls == 1
