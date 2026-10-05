"""Hermetic contracts for the self-hosted Azure Container Apps model backend."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzurePromptApprovalError,
)
from general_ludd.self_improve.azure_containerapp_backend import (
    AzureContainerAppCandidateBackend,
    build_azure_containerapp_candidate_backend,
)
from general_ludd.self_improve.azure_containerapp_transport import (
    MAX_PROVIDER_TOKENS,
    validated_chat_response,
)
from general_ludd.self_improve.azure_containerapp_transport_types import (
    ContainerAppBackendAccounting,
    ContainerAppBackendTrace,
    ContainerAppResponseFailure,
    ContainerAppTraceEvent,
    VLLMRuntimeGPUEvidence,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalEnvelope,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    BoundedCandidateSession,
    CandidateBackend,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_PROMPT = "approved source text that must never appear in traces"
_ENDPOINT = "https://gludd-vllm-proof.kindstone.eastus.azurecontainerapps.io"


def _identity(**overrides: str) -> AzureContainerAppCandidateIdentity:
    values = {
        "endpoint": _ENDPOINT,
        "resource_id": (
            "/subscriptions/12345678-1234-1234-1234-123456789abc/"
            "resourceGroups/gludd-models/providers/Microsoft.App/"
            "containerApps/gludd-vllm-proof"
        ),
        "revision_name": "gludd-vllm-proof--0000007",
        "image_digest": "sha256:" + "a" * 64,
        "model_name": "Qwen/Qwen2.5-0.5B-Instruct",
        "model_revision": "b" * 40,
        "workload_profile_type": "Consumption-GPU-NC8as-T4",
    }
    values.update(overrides)
    return AzureContainerAppCandidateIdentity(**values)


def _models(model: str = "Qwen/Qwen2.5-0.5B-Instruct") -> dict[str, object]:
    return {"object": "list", "data": [{"id": model, "object": "model"}]}


def _chat(
    *,
    text: object = "bounded proposal",
    model: object = "Qwen/Qwen2.5-0.5B-Instruct",
    prompt_tokens: object = 11,
    completion_tokens: object = 7,
    total_tokens: object = 18,
) -> dict[str, object]:
    return {
        "id": "chatcmpl-proof",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def test_transport_validates_exact_bounded_chat_contract() -> None:
    response = validated_chat_response(
        _chat(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        _identity(),
        max_output_tokens=7,
    )

    assert response.text == "bounded proposal"
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert response.total_tokens == 18
    with pytest.raises(ValueError):
        validated_chat_response(
            _chat(
                prompt_tokens=MAX_PROVIDER_TOKENS + 1,
                completion_tokens=0,
                total_tokens=MAX_PROVIDER_TOKENS + 1,
            ),
            _identity(),
            max_output_tokens=7,
        )


class _Response:
    def __init__(
        self,
        payload: object = None,
        *,
        status_code: int = 200,
        content_type: str = "application/json",
        raw: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.content = raw if raw is not None else json.dumps(payload).encode("utf-8")


def _metrics(
    *,
    model: str = "Qwen/Qwen2.5-0.5B-Instruct",
    prompt_tokens: int = 11,
    generation_tokens: int = 7,
    successful_requests: int = 1,
) -> _Response:
    payload = (
        "# TYPE vllm:prompt_tokens_total counter\n"
        f'vllm:prompt_tokens_total{{model_name="{model}"}} {prompt_tokens}.0\n'
        "# TYPE vllm:generation_tokens_total counter\n"
        f'vllm:generation_tokens_total{{model_name="{model}"}} '
        f"{generation_tokens}.0\n"
        "# TYPE vllm:request_success_total counter\n"
        f'vllm:request_success_total{{finished_reason="stop",model_name="{model}"}} '
        f"{successful_requests}.0\n"
        "# TYPE vllm:estimated_flops_per_gpu_total counter\n"
        f'vllm:estimated_flops_per_gpu_total{{model_name="{model}"}} 8192.0\n'
    ).encode()
    return _Response(raw=payload, content_type="text/plain; version=0.0.4")


class _Client:
    def __init__(
        self,
        *,
        gets: tuple[object, ...] = (_models(),),
        posts: tuple[object, ...] = (_chat(),),
    ) -> None:
        self.gets = list(gets)
        self.posts = list(posts)
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []
        self.close_calls = 0

    @staticmethod
    def _next(results: list[object]) -> object:
        result = results.pop(0) if len(results) > 1 else results[0]
        if isinstance(result, BaseException):
            raise result
        return result if isinstance(result, _Response) else _Response(result)

    def get(
        self,
        path: str,
        *,
        timeout: float,
        follow_redirects: bool,
    ) -> object:
        self.get_calls.append(
            {"path": path, "timeout": timeout, "follow_redirects": follow_redirects}
        )
        return self._next(self.gets)

    def post(
        self,
        path: str,
        *,
        json: dict[str, object],
        timeout: float,
        follow_redirects: bool,
    ) -> object:
        self.post_calls.append(
            {
                "path": path,
                "json": json,
                "timeout": timeout,
                "follow_redirects": follow_redirects,
            }
        )
        return self._next(self.posts)

    def close(self) -> None:
        self.close_calls += 1


def _approved(
    tmp_path: Path,
    *,
    response_instruction: str | None = None,
    response_schema_json: str | None = None,
) -> AzureApprovedPrompt:
    source = tmp_path / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        AzurePromptApprovalError,
    )
    return AzureApprovedPrompt.approve(
        prompt=_PROMPT,
        source_paths=("src/approved.py",),
        policy_guard=guard,
        response_instruction=response_instruction,
        response_schema_json=response_schema_json,
    )


def _approved_envelope(
    tmp_path: Path,
) -> tuple[AzureApprovedPrompt, ManagedCandidateProposalEnvelope]:
    envelope = ManagedCandidateProposalEnvelope(
        request_text=_PROMPT,
        request_contract_json='{"contract":"trusted"}',
        response_instruction="Return exactly the approved response envelope.",
        response_schema_json=(
            '{"additionalProperties":false,"properties":{"ok":{"const":true,'
            '"type":"boolean"}},"required":["ok"],"type":"object"}'
        ),
        protocol_digest="c" * 64,
        sampling_digest="d" * 64,
    )
    source = tmp_path / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        AzurePromptApprovalError,
    )
    return (
        AzureApprovedPrompt.approve_envelope(
            envelope=envelope,
            source_paths=("src/approved.py",),
            policy_guard=guard,
        ),
        envelope,
    )


def _build(
    client: _Client,
    *,
    identity: AzureContainerAppCandidateIdentity | None = None,
    traces: list[ContainerAppBackendTrace] | None = None,
) -> AzureContainerAppCandidateBackend:
    records = [] if traces is None else traces
    return build_azure_containerapp_candidate_backend(
        identity or _identity(),
        client=client,
        discovery_timeout_seconds=13.0,
        trace_sink=records.append,
    )


def test_discovery_binds_one_exact_model_without_fallback() -> None:
    client = _Client()
    traces: list[ContainerAppBackendTrace] = []

    backend = _build(client, traces=traces)

    assert isinstance(backend, CandidateBackend)
    assert backend.candidate_identity == _identity()
    assert client.get_calls == [
        {"path": "/v1/models", "timeout": 13.0, "follow_redirects": False}
    ]
    assert client.post_calls == []
    assert [trace.event for trace in traces] == [
        ContainerAppTraceEvent.DISCOVERY_STARTED,
        ContainerAppTraceEvent.DISCOVERY_SUCCEEDED,
    ]
    assert all(trace.candidate_digest in {None, _identity().identity_digest} for trace in traces)


def test_initial_discovery_retries_timeout_with_visible_bounded_progress() -> None:
    client = _Client(gets=(TimeoutError(), _models()))
    traces: list[ContainerAppBackendTrace] = []
    now = [0.0]

    backend = build_azure_containerapp_candidate_backend(
        _identity(),
        client=client,
        discovery_timeout_seconds=60.0,
        trace_sink=traces.append,
        _monotonic=lambda: now[0],
        _sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert backend.candidate_identity == _identity()
    assert [trace.event for trace in traces] == [
        ContainerAppTraceEvent.DISCOVERY_STARTED,
        ContainerAppTraceEvent.DISCOVERY_FAILED,
        ContainerAppTraceEvent.DISCOVERY_PENDING,
        ContainerAppTraceEvent.DISCOVERY_STARTED,
        ContainerAppTraceEvent.DISCOVERY_SUCCEEDED,
    ]
    assert [call["timeout"] for call in client.get_calls] == [30.0, 30.0]


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (_Response({}, status_code=401), BackendFailure.AUTHENTICATION),
        (_Response({}, status_code=403), BackendFailure.AUTHORIZATION),
        (_Response({}, status_code=404), BackendFailure.NOT_FOUND),
        (_Response({}, status_code=429), BackendFailure.RATE_LIMITED),
        (_Response({}, status_code=503), BackendFailure.UNAVAILABLE),
        (_Response({}, status_code=307), BackendFailure.INVALID_RESPONSE),
        (_Response({}, content_type="text/html"), BackendFailure.INVALID_RESPONSE),
        (_Response(raw=b'{"object":"list","object":"duplicate"}'), BackendFailure.INVALID_RESPONSE),
        (_Response(raw=b"{"), BackendFailure.INVALID_RESPONSE),
        (_Response(raw=b"x" * 1_048_577), BackendFailure.INVALID_RESPONSE),
    ],
)
def test_discovery_failures_are_typed_censored_and_traced(
    response: _Response,
    failure: BackendFailure,
) -> None:
    traces: list[ContainerAppBackendTrace] = []

    with pytest.raises(BackendInfrastructureError) as captured:
        _build(_Client(gets=(response,)), traces=traces)

    assert captured.value.failure is failure
    assert _ENDPOINT not in str(captured.value)
    assert traces[-1].event is ContainerAppTraceEvent.DISCOVERY_FAILED
    assert traces[-1].failure is failure


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"object": "list", "data": []},
        {"object": "list", "data": [{"id": "other/model", "object": "model"}]},
        {"object": "list", "data": [_models()["data"][0], _models()["data"][0]]},
        {"object": "wrong", "data": _models()["data"]},
        {"object": "list", "data": "not-a-list"},
    ],
)
def test_discovery_rejects_ambiguous_or_wrong_model_inventory(payload: object) -> None:
    with pytest.raises(BackendInfrastructureError) as captured:
        _build(_Client(gets=(payload,)))

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE


def test_raw_prompt_is_blocked_before_any_remote_request() -> None:
    client = _Client()
    backend = _build(client)

    with pytest.raises(AzurePromptApprovalError):
        backend.generate(
            cast(Any, _PROMPT),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert len(client.get_calls) == 1
    assert client.post_calls == []


def test_policy_drift_is_blocked_before_model_probe_or_generation(tmp_path: Path) -> None:
    approved = _approved(tmp_path)
    client = _Client()
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(client, traces=traces)
    policy_dir = tmp_path / ".gludd"
    policy_dir.mkdir()
    policy_dir.joinpath("self-improve-policy.json").write_text(
        '{"schema_version":1,"default_access":"private",'
        '"private_paths":[],"public_paths":[]}',
        encoding="utf-8",
    )

    with pytest.raises(AzurePromptApprovalError):
        backend.generate(approved, max_output_tokens=20, timeout_seconds=4.0)

    assert len(client.get_calls) == 1
    assert client.post_calls == []
    assert traces[-1].event is ContainerAppTraceEvent.APPROVAL_BLOCKED


def test_generation_rediscovery_and_payload_are_exact_and_bounded(tmp_path: Path) -> None:
    client = _Client(gets=(_models(), _models()))
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(client, traces=traces)

    response = backend.generate(
        _approved(tmp_path),
        max_output_tokens=20,
        timeout_seconds=4.0,
    )

    assert response.text == "bounded proposal"
    assert (response.input_tokens, response.output_tokens, response.total_tokens) == (
        11,
        7,
        18,
    )
    assert client.get_calls[-1] == {
        "path": "/v1/models",
        "timeout": 4.0,
        "follow_redirects": False,
    }
    assert client.post_calls == [
        {
            "path": "/v1/chat/completions",
            "json": {
                "model": "Qwen/Qwen2.5-0.5B-Instruct",
                "messages": [{"role": "user", "content": _PROMPT}],
                "max_tokens": 20,
                "temperature": 0,
                "stream": False,
            },
            "timeout": 4.0,
            "follow_redirects": False,
        }
    ]
    assert backend.accounting == ContainerAppBackendAccounting(
        requests_started=1,
        responses_received=1,
        responses_accepted=1,
        requests_failed=0,
        provider_input_tokens=11,
        provider_output_tokens=7,
        provider_total_tokens=18,
    )
    serialized = repr(traces)
    assert _PROMPT not in serialized
    assert _ENDPOINT not in serialized
    assert [trace.event for trace in traces][-3:] == [
        ContainerAppTraceEvent.DISCOVERY_SUCCEEDED,
        ContainerAppTraceEvent.REQUEST_STARTED,
        ContainerAppTraceEvent.RESPONSE_ACCEPTED,
    ]


def test_runtime_gpu_evidence_binds_vllm_counters_to_the_exact_response() -> None:
    client = _Client(gets=(_models(), _metrics()))
    backend = _build(client)
    response = AzureCandidateResponse("private", 11, 7, 18)

    evidence = backend.attest_runtime_gpu(response, timeout_seconds=4.0)

    assert evidence == VLLMRuntimeGPUEvidence(
        candidate_digest=_identity().identity_digest,
        prompt_tokens=11,
        generation_tokens=7,
        successful_requests=1,
        estimated_flops_per_gpu=8192.0,
    )
    assert client.get_calls[-1] == {
        "path": "/metrics",
        "timeout": 4.0,
        "follow_redirects": False,
    }


def test_runtime_gpu_evidence_rejects_foreign_metrics_without_retaining_them() -> None:
    secret = "foreign-model-private-name"
    backend = _build(_Client(gets=(_models(), _metrics(model=secret))))
    response = AzureCandidateResponse("private", 11, 7, 18)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.attest_runtime_gpu(response, timeout_seconds=4.0)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert secret not in repr(captured.value)


@pytest.mark.parametrize(
    "metrics",
    [
        "vllm:prompt_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 11\n",
        (
            "vllm:prompt_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} NaN\n"
            "vllm:generation_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 7\n"
            "vllm:request_success_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 1\n"
        ),
        (
            "vllm:prompt_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 12\n"
            "vllm:generation_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 7\n"
            "vllm:request_success_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 1\n"
        ),
        (
            "vllm:prompt_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 11\n"
            "vllm:generation_tokens_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 7\n"
            "vllm:request_success_total{model_name=\"Qwen/Qwen2.5-0.5B-Instruct\"} 0\n"
        ),
    ],
)
def test_runtime_gpu_evidence_rejects_malformed_or_unbound_counters(
    metrics: str,
) -> None:
    backend = _build(
        _Client(
            gets=(
                _models(),
                _Response(raw=metrics.encode(), content_type="text/plain"),
            )
        )
    )

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.attest_runtime_gpu(
            AzureCandidateResponse("private", 11, 7, 18),
            timeout_seconds=4.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE


def test_runtime_gpu_evidence_ignores_unselected_metrics() -> None:
    response = _metrics()
    response.content += b'unrelated_counter{model_name="foreign-private"} 99\n'
    backend = _build(_Client(gets=(_models(), response)))

    evidence = backend.attest_runtime_gpu(
        AzureCandidateResponse("private", 11, 7, 18),
        timeout_seconds=4.0,
    )

    assert evidence.successful_requests == 1


@pytest.mark.parametrize("timeout", [True, 0.0, 121.0])
def test_runtime_gpu_evidence_rejects_invalid_limits_before_transport(
    timeout: object,
) -> None:
    client = _Client(gets=(_models(), _metrics()))
    backend = _build(client)

    with pytest.raises(ValueError, match="runtime GPU evidence limits"):
        backend.attest_runtime_gpu(
            AzureCandidateResponse("private", 11, 7, 18),
            timeout_seconds=cast(float, timeout),
        )

    assert len(client.get_calls) == 1


def test_generation_carries_approved_protocol_contract_without_rewriting_envelope(
    tmp_path: Path,
) -> None:
    client = _Client(gets=(_models(), _models()))
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(client, traces=traces)
    approved, envelope = _approved_envelope(tmp_path)

    backend.generate(
        approved,
        max_output_tokens=20,
        timeout_seconds=4.0,
    )

    payload = client.post_calls[0]["json"]
    assert payload["messages"] == [
        {"role": "system", "content": envelope.response_instruction},
        {"role": "user", "content": envelope.request_text},
    ]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "gludd_proposal_batch",
            "schema": json.loads(envelope.response_schema_json),
            "strict": True,
        },
    }
    assert traces[-2].event is ContainerAppTraceEvent.REQUEST_STARTED
    assert traces[-2].envelope_digest == envelope.envelope_digest
    assert traces[-1].event is ContainerAppTraceEvent.RESPONSE_ACCEPTED
    assert traces[-1].envelope_digest == envelope.envelope_digest


def test_model_inventory_drift_blocks_generation_without_fallback(tmp_path: Path) -> None:
    client = _Client(gets=(_models(), _models("other/model")))
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(client, traces=traces)

    with pytest.raises(BackendPolicyError) as captured:
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert captured.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert client.post_calls == []
    assert traces[-1].event is ContainerAppTraceEvent.IDENTITY_DRIFT


@pytest.mark.parametrize(
    "payload",
    [
        _chat(text=""),
        _chat(model="other/model"),
        _chat(prompt_tokens=True),
        _chat(completion_tokens=21, total_tokens=32),
        _chat(total_tokens=19),
        {**_chat(), "choices": []},
        {**_chat(), "choices": [_chat()["choices"][0], _chat()["choices"][0]]},
    ],
)
def test_malformed_provider_response_is_rejected_and_accounted(
    tmp_path: Path,
    payload: object,
) -> None:
    client = _Client(gets=(_models(), _models()), posts=(payload,))
    backend = _build(client)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert backend.accounting.requests_started == 1
    assert backend.accounting.responses_received == 1
    assert backend.accounting.responses_accepted == 0
    assert backend.accounting.requests_failed == 1


def test_http_rejection_emits_only_safe_status_diagnostics(tmp_path: Path) -> None:
    """A live provider rejection remains diagnosable without retaining its body."""
    traces: list[ContainerAppBackendTrace] = []
    client = _Client(
        gets=(_models(), _models()),
        posts=(_Response({}, status_code=400),),
    )
    backend = _build(client, traces=traces)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert traces[-1] == ContainerAppBackendTrace(
        ContainerAppTraceEvent.REQUEST_FAILED,
        candidate_digest=_identity().identity_digest,
        request_number=1,
        failure=BackendFailure.INVALID_RESPONSE,
        response_failure=ContainerAppResponseFailure.HTTP_STATUS,
        http_status=400,
    )


def test_vllm_context_rejection_is_classified_without_retaining_message(
    tmp_path: Path,
) -> None:
    """A recognized vLLM error becomes one fixed category, never retained text."""
    secret_canary = "provider-body-secret-that-must-not-survive"
    traces: list[ContainerAppBackendTrace] = []
    client = _Client(
        gets=(_models(), _models()),
        posts=(
            _Response(
                {
                    "object": "error",
                    "message": (
                        "This model's maximum context length is 4096 tokens. "
                        "However, you requested 6144 tokens. "
                        f"{secret_canary}"
                    ),
                    "type": "BadRequestError",
                    "param": None,
                    "code": 400,
                },
                status_code=400,
            ),
        ),
    )
    backend = _build(client, traces=traces)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=4_096,
            timeout_seconds=4.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert traces[-1].response_failure is (
        ContainerAppResponseFailure.CONTEXT_WINDOW_EXCEEDED
    )
    assert traces[-1].http_status == 400
    retained = repr((captured.value, traces))
    assert secret_canary not in retained
    assert "maximum context length" not in retained


def test_nested_vllm_context_error_with_null_parameter_is_classified(
    tmp_path: Path,
) -> None:
    """Newer vLLM envelopes and fixed token parameter names stay diagnosable."""
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(
        _Client(
            gets=(_models(), _models()),
            posts=(
                _Response(
                    {
                        "error": {
                            "message": (
                                "'max_tokens' is too large: this model's maximum "
                                "context length is 32768 tokens and the request has "
                                "30000 input tokens."
                            ),
                            "type": "BadRequestError",
                            "param": None,
                            "code": 400,
                        }
                    },
                    status_code=400,
                ),
            ),
        ),
        traces=traces,
    )

    with pytest.raises(BackendInfrastructureError):
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=4_096,
            timeout_seconds=4.0,
        )

    assert traces[-1].response_failure is (
        ContainerAppResponseFailure.CONTEXT_WINDOW_EXCEEDED
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "The tokenizer does not define a chat template; provide a chat template.",
            ContainerAppResponseFailure.CHAT_TEMPLATE_UNAVAILABLE,
        ),
        (
            "Request validation failed for a provider-controlled reason.",
            ContainerAppResponseFailure.PROVIDER_BAD_REQUEST,
        ),
    ],
)
def test_vllm_other_bad_requests_are_reduced_to_fixed_diagnostics(
    tmp_path: Path,
    message: str,
    expected: ContainerAppResponseFailure,
) -> None:
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(
        _Client(
            gets=(_models(), _models()),
            posts=(
                _Response(
                    {
                        "object": "error",
                        "message": message,
                        "type": "BadRequestError",
                        "param": None,
                        "code": 400,
                    },
                    status_code=400,
                ),
            ),
        ),
        traces=traces,
    )

    with pytest.raises(BackendInfrastructureError):
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert traces[-1].response_failure is expected
    assert message not in repr(traces)


def test_response_contract_failure_is_distinct_from_http_failure(tmp_path: Path) -> None:
    """Valid JSON with the wrong schema gets a fixed, provider-free diagnosis."""
    traces: list[ContainerAppBackendTrace] = []
    backend = _build(
        _Client(gets=(_models(), _models()), posts=({},)),
        traces=traces,
    )

    with pytest.raises(BackendInfrastructureError):
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )

    assert traces[-1].response_failure is ContainerAppResponseFailure.CHAT_CONTRACT
    assert traces[-1].http_status == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_output_tokens": 0, "timeout_seconds": 1.0},
        {"max_output_tokens": cast(Any, True), "timeout_seconds": 1.0},
        {"max_output_tokens": 1, "timeout_seconds": 0.0},
        {"max_output_tokens": 1, "timeout_seconds": cast(Any, True)},
    ],
)
def test_invalid_limits_fail_before_remote_request(
    tmp_path: Path,
    kwargs: dict[str, object],
) -> None:
    client = _Client()
    backend = _build(client)

    with pytest.raises(ValueError):
        backend.generate(_approved(tmp_path), **cast(Any, kwargs))

    assert len(client.get_calls) == 1
    assert client.post_calls == []


def test_backend_is_compatible_with_bounded_candidate_trials(tmp_path: Path) -> None:
    client = _Client(gets=(_models(), _models()))
    backend = _build(client)
    session = BoundedCandidateSession(
        backend,
        BackendCallBudget(
            max_calls=1,
            max_input_tokens=100,
            max_output_tokens=20,
            max_total_tokens=120,
            max_cost_microusd=500_000,
            timeout_seconds=4.0,
        ),
        azure_enabled=True,
    )

    response = session.generate(
        _approved(tmp_path),
        input_tokens=11,
        max_output_tokens=20,
        estimated_cost_microusd=250_000,
    )

    assert response.text == "bounded proposal"
    assert session.snapshot.calls_started == 1


def test_close_is_idempotent_and_closed_backend_cannot_call(tmp_path: Path) -> None:
    client = _Client()
    backend = _build(client)

    backend.close()
    backend.close()

    assert client.close_calls == 1
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved(tmp_path),
            max_output_tokens=20,
            timeout_seconds=4.0,
        )
    assert captured.value.failure is BackendFailure.UNAVAILABLE


def test_trace_and_accounting_values_are_frozen() -> None:
    trace = ContainerAppBackendTrace(ContainerAppTraceEvent.DISCOVERY_STARTED)
    accounting = ContainerAppBackendAccounting()

    with pytest.raises(FrozenInstanceError):
        trace.__setattr__("request_number", 2)
    with pytest.raises(FrozenInstanceError):
        accounting.__setattr__("requests_started", 2)


def test_builder_rejects_invalid_identity_client_timeout_and_sink() -> None:
    with pytest.raises(ValueError, match="identity"):
        build_azure_containerapp_candidate_backend(cast(Any, object()), client=_Client())
    with pytest.raises(ValueError, match="client"):
        build_azure_containerapp_candidate_backend(_identity(), client=cast(Any, object()))
    with pytest.raises(ValueError, match="timeout"):
        build_azure_containerapp_candidate_backend(
            _identity(), client=_Client(), discovery_timeout_seconds=0.0
        )
    with pytest.raises(ValueError, match="trace"):
        build_azure_containerapp_candidate_backend(
            _identity(), client=_Client(), trace_sink=cast(Any, object())
        )


def test_identity_replacement_remains_frozen_and_validated() -> None:
    with pytest.raises(ValueError):
        replace(_identity(), revision_name="latest")
