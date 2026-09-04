"""Live Azure self-improvement backend contracts with hermetic SDK fakes."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from general_ludd.self_improve.azure_backend import (
    AZURE_AI_TOKEN_SCOPE,
    AzureApprovedPrompt,
    AzureBackendAccounting,
    AzureBackendTrace,
    AzureCandidateResponse,
    AzureCredentialReference,
    AzureCredentialSource,
    AzureOpenAICandidateBackend,
    AzureOpenAIConfig,
    AzurePromptApprovalError,
    AzureSdkFactories,
    AzureTraceEvent,
    build_azure_openai_candidate_backend,
)
from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    BoundedCandidateSession,
    CandidateBackend,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

_SUBSCRIPTION_ID = "12345678-1234-1234-1234-123456789abc"
_SECRET = "azure-key-that-must-never-leak"
_PROMPT = "approved source that must never appear in traces"
_PROVIDER_DETAIL = "provider body with tenant-secret and endpoint query"


class _SdkError(Exception):
    def __init__(self, status_code: int | None = None) -> None:
        super().__init__(_PROVIDER_DETAIL)
        self.status_code = status_code


class APITimeoutError(Exception):
    """Name-compatible fake for the optional OpenAI exception boundary."""


class APIConnectionError(Exception):
    """Name-compatible fake for the optional OpenAI exception boundary."""


class ClientAuthenticationError(Exception):
    """Name-compatible fake for the optional Azure exception boundary."""


class ServiceRequestError(Exception):
    """Name-compatible fake for the optional Azure exception boundary."""


class _ExplodingStatusError(Exception):
    @property
    def status_code(self) -> int:
        raise RuntimeError(_PROVIDER_DETAIL)


class _ExplodingEnvironment(Mapping[str, str]):
    def __getitem__(self, _key: str) -> str:
        raise RuntimeError(_PROVIDER_DETAIL)

    def __iter__(self) -> Iterator[str]:
        return iter(("AZURE_INFERENCE_CREDENTIAL",))

    def __len__(self) -> int:
        return 1


class _Closable:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.close_calls = 0
        self.failure = failure

    def close(self) -> None:
        self.close_calls += 1
        if self.failure is not None:
            raise self.failure


def _deployment(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "name": "reviewer-green",
        "etag": 'W/"deployment-revision-7"',
        "properties": SimpleNamespace(
            provisioning_state="Succeeded",
            model=SimpleNamespace(
                name="gpt-5-mini",
                version="2026-08-07",
            ),
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _response(
    text: object = "proposal text",
    *,
    input_tokens: object = 11,
    output_tokens: object = 7,
    total_tokens: object = 18,
) -> SimpleNamespace:
    return SimpleNamespace(
        output_text=text,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
    )


class _FakeDeployments:
    def __init__(self, *results: object) -> None:
        self.results = list(results or (_deployment(),))
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        *,
        resource_group_name: str,
        account_name: str,
        deployment_name: str,
        connection_timeout: float,
        read_timeout: float,
        retry_total: int,
        logging_enable: bool,
    ) -> object:
        self.calls.append(
            {
                "resource_group_name": resource_group_name,
                "account_name": account_name,
                "deployment_name": deployment_name,
                "connection_timeout": connection_timeout,
                "read_timeout": read_timeout,
                "retry_total": retry_total,
                "logging_enable": logging_enable,
            }
        )
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


class _FakeManagementClient(_Closable):
    def __init__(self, deployments: _FakeDeployments) -> None:
        super().__init__()
        self.deployments = deployments


class _FakeResponses:
    def __init__(self, *results: object) -> None:
        self.results = list(results or (_response(),))
        self.calls: list[dict[str, object]] = []

    def create(
        self,
        *,
        model: str,
        input: str,
        max_output_tokens: int,
        store: bool,
        timeout: float,
    ) -> object:
        self.calls.append(
            {
                "model": model,
                "input": input,
                "max_output_tokens": max_output_tokens,
                "store": store,
                "timeout": timeout,
            }
        )
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, BaseException):
            raise result
        return result


class _FakeOpenAIClient(_Closable):
    def __init__(self, responses: _FakeResponses) -> None:
        super().__init__()
        self.responses = responses


class _FactoryHarness:
    def __init__(
        self,
        *,
        deployments: _FakeDeployments | None = None,
        responses: _FakeResponses | None = None,
    ) -> None:
        self.credential = _Closable()
        self.deployments = deployments or _FakeDeployments()
        self.management = _FakeManagementClient(self.deployments)
        self.responses = responses or _FakeResponses()
        self.openai = _FakeOpenAIClient(self.responses)
        self.default_credential_calls = 0
        self.management_calls: list[dict[str, object]] = []
        self.token_provider_calls: list[tuple[object, str]] = []
        self.openai_calls: list[dict[str, object]] = []
        self.default_credential_failure: Exception | None = None
        self.management_failure: Exception | None = None
        self.openai_failure: Exception | None = None

    def create_default_credential(self) -> object:
        self.default_credential_calls += 1
        if self.default_credential_failure is not None:
            raise self.default_credential_failure
        return self.credential

    def create_management_client(
        self,
        *,
        credential: object,
        subscription_id: str,
        retry_total: int,
        logging_enable: bool,
    ) -> object:
        self.management_calls.append(
            {
                "credential": credential,
                "subscription_id": subscription_id,
                "retry_total": retry_total,
                "logging_enable": logging_enable,
            }
        )
        if self.management_failure is not None:
            raise self.management_failure
        return self.management

    def create_bearer_token_provider(
        self,
        *,
        credential: object,
        scope: str,
    ) -> Callable[[], str]:
        self.token_provider_calls.append((credential, scope))
        return lambda: _SECRET

    def create_openai_client(
        self,
        *,
        base_url: str,
        api_key: str | Callable[[], str],
        max_retries: int,
    ) -> object:
        self.openai_calls.append(
            {
                "base_url": base_url,
                "api_key": api_key,
                "max_retries": max_retries,
            }
        )
        if self.openai_failure is not None:
            raise self.openai_failure
        return self.openai

    def factories(self) -> AzureSdkFactories:
        return AzureSdkFactories(
            create_default_credential=self.create_default_credential,
            create_management_client=self.create_management_client,
            create_bearer_token_provider=self.create_bearer_token_provider,
            create_openai_client=self.create_openai_client,
        )


def _credential(
    source: AzureCredentialSource = AzureCredentialSource.API_KEY_ENV,
) -> AzureCredentialReference:
    return AzureCredentialReference(
        source=source,
        environment_variable=(
            "AZURE_INFERENCE_CREDENTIAL"
            if source is AzureCredentialSource.API_KEY_ENV
            else None
        ),
    )


def _config(
    *,
    credential: AzureCredentialReference | None = None,
    azure_enabled: bool = True,
) -> AzureOpenAIConfig:
    return AzureOpenAIConfig(
        azure_enabled=azure_enabled,
        endpoint="https://project.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        api_version="v1",
        subscription_id=_SUBSCRIPTION_ID,
        resource_group="gludd-eval-rg",
        account_name="project",
        deployment="reviewer-green",
        credential=credential or _credential(),
        discovery_timeout_seconds=13.0,
    )


def _approved_prompt(
    tmp_path: Path,
    *,
    prompt: str = _PROMPT,
    events: list[str] | None = None,
) -> AzureApprovedPrompt:
    source = tmp_path / "src" / "approved.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("PUBLIC = True\n", encoding="utf-8")
    policy_events = events if events is not None else []
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        policy_events.append,
        AzurePromptApprovalError,
    )
    return AzureApprovedPrompt.approve(
        prompt=prompt,
        source_paths=("src/approved.py",),
        policy_guard=guard,
    )


def _build(
    harness: _FactoryHarness,
    *,
    config: AzureOpenAIConfig | None = None,
    environment: Mapping[str, str] | None = None,
    traces: list[AzureBackendTrace] | None = None,
) -> AzureOpenAICandidateBackend:
    trace_records = traces if traces is not None else []
    return build_azure_openai_candidate_backend(
        config or _config(),
        factories=harness.factories(),
        environment=(
            {"AZURE_INFERENCE_CREDENTIAL": _SECRET}
            if environment is None
            else environment
        ),
        trace_sink=trace_records.append,
    )


def test_configuration_is_frozen_explicit_and_secret_free() -> None:
    config = _config()

    assert config.api_family is AzureFoundryAPIFamily.AZURE_OPENAI
    assert config.api_version == "v1"
    assert config.credential.source is AzureCredentialSource.API_KEY_ENV
    assert _SECRET not in repr(config)
    assert "AZURE_INFERENCE_CREDENTIAL" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.__setattr__("deployment", "other")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, azure_enabled=cast(Any, 1)),
        lambda value: replace(value, endpoint="http://project.openai.azure.com"),
        lambda value: replace(
            value,
            endpoint="https://project.openai.azure.com?api-key=secret",
        ),
        lambda value: replace(value, endpoint="https://other.openai.azure.com"),
        lambda value: replace(
            value,
            api_family=AzureFoundryAPIFamily.MODEL_INFERENCE,
        ),
        lambda value: replace(value, api_version="2024-05-01-preview"),
        lambda value: replace(value, subscription_id="not-a-subscription"),
        lambda value: replace(value, resource_group="bad/group"),
        lambda value: replace(value, resource_group="trailing."),
        lambda value: replace(value, account_name="Project"),
        lambda value: replace(value, deployment=" bad"),
        lambda value: replace(value, credential=cast(Any, object())),
        lambda value: replace(value, discovery_timeout_seconds=cast(Any, True)),
        lambda value: replace(value, discovery_timeout_seconds=0.0),
        lambda value: replace(value, discovery_timeout_seconds=121.0),
    ],
)
def test_configuration_rejects_ambiguous_or_unbounded_values(
    mutation: Callable[[AzureOpenAIConfig], AzureOpenAIConfig],
) -> None:
    with pytest.raises(ValueError):
        mutation(_config())


@pytest.mark.parametrize(
    "reference",
    [
        AzureCredentialReference(
            source=AzureCredentialSource.ENTRA_ID,
            environment_variable=None,
        ),
        AzureCredentialReference(
            source=AzureCredentialSource.API_KEY_ENV,
            environment_variable="AZURE_INFERENCE_CREDENTIAL",
        ),
    ],
)
def test_supported_credential_references_never_contain_secret_values(
    reference: AzureCredentialReference,
) -> None:
    assert _SECRET not in repr(reference)
    assert "AZURE_INFERENCE_CREDENTIAL" not in repr(reference)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "source": AzureCredentialSource.ENTRA_ID,
            "environment_variable": "AZURE_KEY",
        },
        {
            "source": AzureCredentialSource.API_KEY_ENV,
            "environment_variable": None,
        },
        {
            "source": AzureCredentialSource.API_KEY_ENV,
            "environment_variable": "actual-secret-value",
        },
        {
            "source": AzureCredentialSource.API_KEY_ENV,
            "environment_variable": "OTHER_PROVIDER_KEY",
        },
        {
            "source": cast(Any, "api_key_env"),
            "environment_variable": "AZURE_KEY",
        },
    ],
)
def test_credential_reference_rejects_values_that_are_not_secret_pointers(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        AzureCredentialReference(**cast(Any, kwargs))


def test_entra_reference_cannot_be_resolved_as_an_api_key() -> None:
    with pytest.raises(ValueError, match="not an API-key pointer"):
        _credential(AzureCredentialSource.ENTRA_ID).resolve_api_key({})


def test_secret_mapping_failure_is_censored_as_authentication() -> None:
    with pytest.raises(BackendInfrastructureError) as captured:
        _credential().resolve_api_key(_ExplodingEnvironment())

    assert captured.value.failure is BackendFailure.AUTHENTICATION
    assert _PROVIDER_DETAIL not in str(captured.value)


def test_disabled_azure_fails_before_credential_or_discovery() -> None:
    harness = _FactoryHarness()

    with pytest.raises(BackendPolicyError) as captured:
        _build(harness, config=_config(azure_enabled=False))

    assert captured.value.failure is BackendPolicyFailure.AZURE_OPT_IN_REQUIRED
    assert harness.default_credential_calls == 0
    assert harness.deployments.calls == []


def test_discovery_uses_exact_retry_free_sdk_boundary_and_immutable_identity() -> None:
    traces: list[AzureBackendTrace] = []
    harness = _FactoryHarness()

    backend = _build(harness, traces=traces)

    assert isinstance(backend, CandidateBackend)
    assert backend.candidate_identity.endpoint == "https://project.openai.azure.com"
    assert backend.candidate_identity.deployment == "reviewer-green"
    assert backend.candidate_identity.api_version == "v1"
    assert backend.candidate_identity.model_version == "2026-08-07"
    assert backend.candidate_identity.etag == 'W/"deployment-revision-7"'
    assert harness.management_calls == [
        {
            "credential": harness.credential,
            "subscription_id": _SUBSCRIPTION_ID,
            "retry_total": 0,
            "logging_enable": False,
        }
    ]
    assert harness.deployments.calls == [
        {
            "resource_group_name": "gludd-eval-rg",
            "account_name": "project",
            "deployment_name": "reviewer-green",
            "connection_timeout": 13.0,
            "read_timeout": 13.0,
            "retry_total": 0,
            "logging_enable": False,
        }
    ]
    assert harness.openai_calls == []
    assert [trace.event for trace in traces] == [
        AzureTraceEvent.DISCOVERY_STARTED,
        AzureTraceEvent.DISCOVERY_SUCCEEDED,
    ]


def test_raw_prompt_is_rejected_before_rediscovery_or_inference() -> None:
    harness = _FactoryHarness()
    backend = _build(harness)

    with pytest.raises(AzurePromptApprovalError):
        backend.generate(
            cast(Any, _PROMPT),
            max_output_tokens=100,
            timeout_seconds=5.0,
        )

    assert len(harness.deployments.calls) == 1
    assert harness.openai_calls == []
    assert harness.responses.calls == []


def test_private_path_cannot_construct_provider_request(tmp_path: Path) -> None:
    private = tmp_path / "private" / "rules.py"
    private.parent.mkdir(parents=True)
    private.write_text("SECRET_RULE = True\n", encoding="utf-8")
    policy_dir = tmp_path / ".gludd"
    policy_dir.mkdir()
    policy_dir.joinpath("self-improve-policy.json").write_text(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":["private/**"],"public_paths":[]}',
        encoding="utf-8",
    )
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        cast(Callable[[str], None], lambda _event: None),
        AzurePromptApprovalError,
    )

    with pytest.raises(AzurePromptApprovalError) as captured:
        AzureApprovedPrompt.approve(
            prompt="private business rule",
            source_paths=("private/rules.py",),
            policy_guard=guard,
        )

    assert "private" not in str(captured.value).lower()
    assert "business rule" not in str(captured.value)


def test_approval_capability_cannot_be_forged(tmp_path: Path) -> None:
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        cast(Callable[[str], None], lambda _event: None),
        AzurePromptApprovalError,
    )

    with pytest.raises(AzurePromptApprovalError):
        AzureApprovedPrompt(
            prompt=_PROMPT,
            source_paths=("src/approved.py",),
            policy_guard=guard,
            policy_digest="forged-policy",
            approval_digest="forged-approval",
            _token=object(),
        )


@pytest.mark.parametrize(
    ("prompt", "source_paths"),
    [
        (cast(Any, 42), ("src/approved.py",)),
        ("\ud800", ("src/approved.py",)),
        (" ", ("src/approved.py",)),
        (_PROMPT, cast(Any, ["src/approved.py"])),
        (_PROMPT, ()),
        (_PROMPT, ("src/approved.py", "src/approved.py")),
        (_PROMPT, (cast(Any, 42),)),
    ],
)
def test_approval_rejects_malformed_prompt_or_scope_without_detail(
    tmp_path: Path,
    prompt: str,
    source_paths: tuple[str, ...],
) -> None:
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        cast(Callable[[str], None], lambda _event: None),
        AzurePromptApprovalError,
    )

    with pytest.raises(AzurePromptApprovalError) as captured:
        AzureApprovedPrompt.approve(
            prompt=prompt,
            source_paths=source_paths,
            policy_guard=guard,
        )

    assert _PROMPT not in str(captured.value)


def test_approval_object_repr_redacts_prompt_and_source_paths(tmp_path: Path) -> None:
    request = _approved_prompt(tmp_path)

    rendered = repr(request)
    assert _PROMPT not in rendered
    assert "src/approved.py" not in rendered
    assert request.policy_digest in rendered
    assert len(request.approval_digest) == 64


def test_policy_drift_blocks_before_provider_request(tmp_path: Path) -> None:
    request = _approved_prompt(tmp_path)
    harness = _FactoryHarness()
    backend = _build(harness)
    policy_dir = tmp_path / ".gludd"
    policy_dir.mkdir()
    policy_dir.joinpath("self-improve-policy.json").write_text(
        '{"schema_version":1,"default_access":"private",'
        '"private_paths":[],"public_paths":[]}',
        encoding="utf-8",
    )

    with pytest.raises(AzurePromptApprovalError):
        backend.generate(request, max_output_tokens=100, timeout_seconds=5.0)

    assert harness.openai_calls == []
    assert harness.responses.calls == []
    assert backend.accounting == AzureBackendAccounting()


def test_approval_digest_tampering_blocks_before_provider_request(tmp_path: Path) -> None:
    request = _approved_prompt(tmp_path)
    object.__setattr__(request, "policy_digest", "forged-policy-digest")
    harness = _FactoryHarness()
    backend = _build(harness)

    with pytest.raises(AzurePromptApprovalError):
        backend.generate(request, max_output_tokens=100, timeout_seconds=5.0)

    assert harness.openai_calls == []
    assert harness.responses.calls == []
    assert backend.accounting == AzureBackendAccounting()


def test_remote_identity_drift_fails_before_secret_resolution_or_inference(
    tmp_path: Path,
) -> None:
    deployments = _FakeDeployments(
        _deployment(),
        _deployment(etag='W/"replacement"'),
    )
    harness = _FactoryHarness(deployments=deployments)
    environment_reads: list[str] = []

    class _Environment(Mapping[str, str]):
        def __init__(self, values: Mapping[str, str]) -> None:
            self._values = dict(values)

        def __getitem__(self, key: str) -> str:
            environment_reads.append(key)
            return self._values[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._values)

        def __len__(self) -> int:
            return len(self._values)

    backend = _build(
        harness,
        environment=_Environment({"AZURE_INFERENCE_CREDENTIAL": _SECRET}),
    )

    with pytest.raises(BackendPolicyError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=100,
            timeout_seconds=5.0,
        )

    assert captured.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert environment_reads == []
    assert harness.openai_calls == []
    assert harness.responses.calls == []


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (_SdkError(401), BackendFailure.AUTHENTICATION),
        (_SdkError(403), BackendFailure.AUTHORIZATION),
        (_SdkError(404), BackendFailure.NOT_FOUND),
        (_SdkError(429), BackendFailure.RATE_LIMITED),
        (_SdkError(503), BackendFailure.UNAVAILABLE),
        (APITimeoutError(_PROVIDER_DETAIL), BackendFailure.TIMEOUT),
        (APIConnectionError(_PROVIDER_DETAIL), BackendFailure.TRANSPORT),
        (ClientAuthenticationError(_PROVIDER_DETAIL), BackendFailure.AUTHENTICATION),
        (ServiceRequestError(_PROVIDER_DETAIL), BackendFailure.TRANSPORT),
        (ImportError(_PROVIDER_DETAIL), BackendFailure.UNAVAILABLE),
        (TimeoutError(_PROVIDER_DETAIL), BackendFailure.TIMEOUT),
        (RuntimeError(_PROVIDER_DETAIL), BackendFailure.INTERNAL),
        (_ExplodingStatusError(_PROVIDER_DETAIL), BackendFailure.INTERNAL),
    ],
)
def test_discovery_failures_are_classified_and_censored(
    error: Exception,
    failure: BackendFailure,
) -> None:
    harness = _FactoryHarness(deployments=_FakeDeployments(error))

    with pytest.raises(BackendInfrastructureError) as captured:
        _build(harness)

    assert captured.value.failure is failure
    assert _PROVIDER_DETAIL not in str(captured.value)
    assert captured.value.__cause__ is None
    assert harness.openai_calls == []


def test_discovery_accepts_official_camel_case_mapping_shape() -> None:
    deployment = {
        "name": "reviewer-green",
        "etag": 'W/"deployment-revision-8"',
        "properties": {
            "provisioningState": {"value": "Succeeded"},
            "model": {
                "name": "gpt-5-mini",
                "modelVersion": "2026-08-08",
            },
        },
    }
    harness = _FactoryHarness(deployments=_FakeDeployments(deployment))

    backend = _build(harness)

    assert backend.candidate_identity.model_version == "2026-08-08"
    assert backend.candidate_identity.etag == 'W/"deployment-revision-8"'


def test_discovery_failure_survives_failure_trace_sink_error() -> None:
    harness = _FactoryHarness(deployments=_FakeDeployments(TimeoutError(_PROVIDER_DETAIL)))
    traces: list[AzureBackendTrace] = []

    def trace_sink(trace: AzureBackendTrace) -> None:
        traces.append(trace)
        if trace.event is AzureTraceEvent.DISCOVERY_FAILED:
            raise RuntimeError(_SECRET)

    with pytest.raises(BackendInfrastructureError) as captured:
        build_azure_openai_candidate_backend(
            _config(),
            factories=harness.factories(),
            environment={"AZURE_INFERENCE_CREDENTIAL": _SECRET},
            trace_sink=trace_sink,
        )

    assert captured.value.failure is BackendFailure.TIMEOUT
    assert _SECRET not in str(captured.value)
    assert traces[-1].event is AzureTraceEvent.DISCOVERY_FAILED


@pytest.mark.parametrize(
    "deployment",
    [
        _deployment(name="other"),
        _deployment(etag=None),
        _deployment(etag="latest"),
        _deployment(properties=None),
        _deployment(
            properties=SimpleNamespace(
                provisioning_state="Creating",
                model=SimpleNamespace(name="gpt-5-mini", version="2026-08-07"),
            )
        ),
        _deployment(
            properties=SimpleNamespace(
                provisioning_state="Succeeded",
                model=SimpleNamespace(name="gpt-5-mini", version=None),
            )
        ),
        _deployment(
            properties={
                "provisioning_state": "Succeeded",
                "model": {"name": "gpt-5-mini", "version": "latest"},
            }
        ),
    ],
)
def test_incomplete_or_mutable_discovery_snapshot_fails_closed(
    deployment: object,
) -> None:
    harness = _FactoryHarness(deployments=_FakeDeployments(deployment))

    with pytest.raises(BackendInfrastructureError) as captured:
        _build(harness)

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert harness.openai_calls == []


def test_api_key_is_resolved_lazily_and_request_is_exact_and_non_stored(
    tmp_path: Path,
) -> None:
    traces: list[AzureBackendTrace] = []
    harness = _FactoryHarness()
    backend = _build(harness, traces=traces)
    assert harness.openai_calls == []

    result = backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=123,
        timeout_seconds=4.5,
    )

    assert result == AzureCandidateResponse(
        text="proposal text",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
    )
    assert harness.openai_calls == [
        {
            "base_url": "https://project.openai.azure.com/openai/v1/",
            "api_key": _SECRET,
            "max_retries": 0,
        }
    ]
    assert harness.responses.calls == [
        {
            "model": "reviewer-green",
            "input": _PROMPT,
            "max_output_tokens": 123,
            "store": False,
            "timeout": 4.5,
        }
    ]
    assert backend.accounting == AzureBackendAccounting(
        requests_started=1,
        responses_received=1,
        responses_accepted=1,
        requests_failed=0,
        provider_input_tokens=11,
        provider_output_tokens=7,
        provider_total_tokens=18,
    )
    assert [trace.event for trace in traces][-2:] == [
        AzureTraceEvent.REQUEST_STARTED,
        AzureTraceEvent.RESPONSE_ACCEPTED,
    ]


def test_entra_authentication_uses_official_scope_and_no_secret_string(
    tmp_path: Path,
) -> None:
    harness = _FactoryHarness()
    backend = _build(
        harness,
        config=_config(credential=_credential(AzureCredentialSource.ENTRA_ID)),
        environment={},
    )

    backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=10,
        timeout_seconds=2.0,
    )

    assert harness.token_provider_calls == [(harness.credential, AZURE_AI_TOKEN_SCOPE)]
    assert callable(harness.openai_calls[0]["api_key"])


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"AZURE_INFERENCE_CREDENTIAL": ""},
        {"AZURE_INFERENCE_CREDENTIAL": " whitespace "},
        {"AZURE_INFERENCE_CREDENTIAL": "x\nsecret"},
        {"AZURE_INFERENCE_CREDENTIAL": "x" * 16_385},
    ],
)
def test_missing_or_malformed_api_key_is_censored_before_client_construction(
    tmp_path: Path,
    environment: Mapping[str, str],
) -> None:
    harness = _FactoryHarness()
    backend = _build(harness, environment=environment)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is BackendFailure.AUTHENTICATION
    assert _SECRET not in str(captured.value)
    assert harness.openai_calls == []
    assert backend.accounting == AzureBackendAccounting()


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (_SdkError(401), BackendFailure.AUTHENTICATION),
        (_SdkError(403), BackendFailure.AUTHORIZATION),
        (_SdkError(404), BackendFailure.NOT_FOUND),
        (_SdkError(429), BackendFailure.RATE_LIMITED),
        (_SdkError(500), BackendFailure.UNAVAILABLE),
        (APITimeoutError(_PROVIDER_DETAIL), BackendFailure.TIMEOUT),
        (APIConnectionError(_PROVIDER_DETAIL), BackendFailure.TRANSPORT),
        (_SdkError(400), BackendFailure.INVALID_RESPONSE),
        (RuntimeError(_PROVIDER_DETAIL), BackendFailure.INTERNAL),
    ],
)
def test_inference_failure_has_one_accounted_request_and_no_fallback(
    tmp_path: Path,
    error: Exception,
    failure: BackendFailure,
) -> None:
    traces: list[AzureBackendTrace] = []
    harness = _FactoryHarness(responses=_FakeResponses(error))
    backend = _build(harness, traces=traces)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is failure
    assert _PROVIDER_DETAIL not in str(captured.value)
    assert len(harness.responses.calls) == 1
    assert backend.accounting.requests_started == 1
    assert backend.accounting.requests_failed == 1
    assert backend.accounting.responses_received == 0
    assert traces[-1].event is AzureTraceEvent.REQUEST_FAILED
    assert traces[-1].failure is failure


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(output_text="proposal", usage=None),
        _response(text=""),
        _response(input_tokens=True),
        _response(output_tokens=-1),
        _response(total_tokens=19),
        _response(output_tokens=101, total_tokens=112),
    ],
)
def test_invalid_response_is_accounted_but_never_accepted(
    tmp_path: Path,
    response: object,
) -> None:
    harness = _FactoryHarness(responses=_FakeResponses(response))
    backend = _build(harness)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=100,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is BackendFailure.INVALID_RESPONSE
    assert backend.accounting.requests_started == 1
    assert backend.accounting.responses_received == 1
    assert backend.accounting.responses_accepted == 0
    assert backend.accounting.requests_failed == 1


def test_repeated_successes_rediscover_and_accumulate_exact_usage(tmp_path: Path) -> None:
    responses = _FakeResponses(
        _response(input_tokens=4, output_tokens=3, total_tokens=7),
        _response("second", input_tokens=5, output_tokens=2, total_tokens=7),
    )
    harness = _FactoryHarness(responses=responses)
    backend = _build(harness)
    request = _approved_prompt(tmp_path)

    first = backend.generate(request, max_output_tokens=10, timeout_seconds=2.0)
    second = backend.generate(request, max_output_tokens=10, timeout_seconds=2.0)

    assert first.text == "proposal text"
    assert second.text == "second"
    assert len(harness.deployments.calls) == 3
    assert len(harness.openai_calls) == 1
    assert len(harness.responses.calls) == 2
    assert backend.accounting == AzureBackendAccounting(
        requests_started=2,
        responses_received=2,
        responses_accepted=2,
        requests_failed=0,
        provider_input_tokens=9,
        provider_output_tokens=5,
        provider_total_tokens=14,
    )


def test_bounded_session_retains_no_fallback_and_outer_budget_accounting(
    tmp_path: Path,
) -> None:
    harness = _FactoryHarness()
    backend = _build(harness)
    session = BoundedCandidateSession(
        backend,
        BackendCallBudget(
            max_calls=1,
            max_input_tokens=100,
            max_output_tokens=100,
            max_total_tokens=200,
            max_cost_microusd=1_000,
            timeout_seconds=9.0,
        ),
        azure_enabled=True,
    )

    result = session.generate(
        _approved_prompt(tmp_path),
        input_tokens=12,
        max_output_tokens=20,
        estimated_cost_microusd=100,
    )

    assert result.text == "proposal text"
    assert harness.responses.calls[0]["timeout"] == 9.0
    assert session.snapshot.calls_started == 1
    assert session.snapshot.reserved_tokens == 32


def test_response_and_traces_never_repr_prompt_secret_or_provider_text(
    tmp_path: Path,
) -> None:
    traces: list[AzureBackendTrace] = []
    harness = _FactoryHarness()
    backend = _build(harness, traces=traces)

    result = backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=10,
        timeout_seconds=2.0,
    )

    combined = repr((backend, result, backend.accounting, traces))
    assert _PROMPT not in combined
    assert "proposal text" not in combined
    assert _SECRET not in combined
    assert _PROVIDER_DETAIL not in combined
    assert "api-key=" not in combined


@pytest.mark.parametrize(
    ("max_output_tokens", "timeout_seconds"),
    [
        (cast(Any, True), 2.0),
        (0, 2.0),
        (100_000_001, 2.0),
        (10, cast(Any, True)),
        (10, 0.0),
        (10, 3_601.0),
    ],
)
def test_generation_rejects_invalid_limits_before_rediscovery(
    tmp_path: Path,
    max_output_tokens: int,
    timeout_seconds: float,
) -> None:
    harness = _FactoryHarness()
    backend = _build(harness)

    with pytest.raises(ValueError, match="generation limits"):
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )

    assert len(harness.deployments.calls) == 1
    assert harness.openai_calls == []


def test_malformed_openai_client_is_censored_before_request_accounting(
    tmp_path: Path,
) -> None:
    harness = _FactoryHarness()
    harness.openai = cast(Any, object())
    backend = _build(harness)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is BackendFailure.INTERNAL
    assert backend.accounting == AzureBackendAccounting()


def test_openai_client_construction_failure_is_censored_and_unaccounted(
    tmp_path: Path,
) -> None:
    harness = _FactoryHarness()
    harness.openai_failure = ClientAuthenticationError(_SECRET)
    backend = _build(harness)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is BackendFailure.AUTHENTICATION
    assert _SECRET not in str(captured.value)
    assert backend.accounting == AzureBackendAccounting()


def test_default_credential_and_management_construction_failures_are_censored() -> None:
    credential_failure = _FactoryHarness()
    credential_failure.default_credential_failure = ClientAuthenticationError(_SECRET)
    with pytest.raises(BackendInfrastructureError) as credential_error:
        _build(credential_failure)
    assert credential_error.value.failure is BackendFailure.AUTHENTICATION

    management_failure = _FactoryHarness()
    management_failure.management_failure = ServiceRequestError(_SECRET)
    with pytest.raises(BackendInfrastructureError) as management_error:
        _build(management_failure)
    assert management_error.value.failure is BackendFailure.TRANSPORT
    assert management_failure.credential.close_calls == 1


def test_close_releases_all_sdk_resources_and_prevents_more_work(tmp_path: Path) -> None:
    harness = _FactoryHarness()
    backend = _build(harness)
    backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=10,
        timeout_seconds=2.0,
    )

    backend.close()
    backend.close()

    assert harness.openai.close_calls == 1
    assert harness.management.close_calls == 1
    assert harness.credential.close_calls == 1
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )
    assert captured.value.failure is BackendFailure.UNAVAILABLE


def test_close_attempts_every_resource_and_censors_cleanup_error(tmp_path: Path) -> None:
    harness = _FactoryHarness()
    backend = _build(harness)
    backend.generate(
        _approved_prompt(tmp_path),
        max_output_tokens=10,
        timeout_seconds=2.0,
    )
    harness.openai.failure = RuntimeError(_SECRET)

    with pytest.raises(BackendInfrastructureError) as captured:
        backend.close()

    assert captured.value.failure is BackendFailure.INTERNAL
    assert _SECRET not in str(captured.value)
    assert harness.management.close_calls == 1
    assert harness.credential.close_calls == 1


def test_trace_sink_failure_blocks_effect_without_leaking_sink_text(tmp_path: Path) -> None:
    harness = _FactoryHarness()
    trace_failure = RuntimeError(_SECRET)
    initial_traces: list[AzureBackendTrace] = []
    backend = build_azure_openai_candidate_backend(
        _config(),
        factories=harness.factories(),
        environment={"AZURE_INFERENCE_CREDENTIAL": _SECRET},
        trace_sink=initial_traces.append,
    )

    def fail_trace(_trace: AzureBackendTrace) -> None:
        raise trace_failure

    backend.trace_sink = fail_trace
    with pytest.raises(BackendInfrastructureError) as captured:
        backend.generate(
            _approved_prompt(tmp_path),
            max_output_tokens=10,
            timeout_seconds=2.0,
        )

    assert captured.value.failure is BackendFailure.INTERNAL
    assert _SECRET not in str(captured.value)
    assert harness.responses.calls == []


def test_trace_sink_property_rejects_noncallable_values() -> None:
    harness = _FactoryHarness()
    traces: list[AzureBackendTrace] = []
    backend = _build(harness, traces=traces)

    assert backend.trace_sink == traces.append
    with pytest.raises(ValueError, match="trace sink"):
        backend.trace_sink = cast(Any, None)


def test_default_trace_sink_and_context_manager_release_resources() -> None:
    harness = _FactoryHarness()

    with build_azure_openai_candidate_backend(
        _config(),
        factories=harness.factories(),
        environment={"AZURE_INFERENCE_CREDENTIAL": _SECRET},
    ) as backend:
        assert backend.candidate_identity.deployment == "reviewer-green"

    assert harness.management.close_calls == 1
    assert harness.credential.close_calls == 1


def test_official_factory_bundle_is_lazy_and_complete() -> None:
    factories = AzureSdkFactories.official()

    assert callable(factories.create_default_credential)
    assert callable(factories.create_management_client)
    assert callable(factories.create_bearer_token_provider)
    assert callable(factories.create_openai_client)


def test_factory_contract_rejects_noncallable_sdk_hooks() -> None:
    harness = _FactoryHarness()
    with pytest.raises(ValueError, match="factory"):
        replace(
            harness.factories(),
            create_openai_client=cast(Any, None),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"config": cast(Any, object())},
        {"config": _config(), "factories": cast(Any, object())},
        {"config": _config(), "environment": cast(Any, object())},
        {"config": _config(), "trace_sink": cast(Any, object())},
        {"config": _config(), "trace_sink": cast(Any, 0)},
    ],
)
def test_builder_rejects_malformed_boundary_inputs(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_azure_openai_candidate_backend(**cast(Any, kwargs))


def test_builder_rejects_malformed_management_client_and_closes_credential() -> None:
    harness = _FactoryHarness()
    harness.management = cast(Any, object())

    with pytest.raises(BackendInfrastructureError) as captured:
        _build(harness)

    assert captured.value.failure is BackendFailure.INTERNAL
    assert harness.credential.close_calls == 1


def test_azure_extras_declare_maintained_management_sdk_not_retired_inference() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    optional = project["optional-dependencies"]
    expected = "azure-mgmt-cognitiveservices>=14.1.0,<15"

    assert expected in optional["azure"]
    assert expected in optional["e2e-all"]
    assert all(
        not dependency.startswith("azure-ai-inference")
        for dependencies in optional.values()
        for dependency in dependencies
    )
