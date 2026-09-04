"""Provider-neutral self-improvement candidate contract tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from general_ludd.self_improve.model_candidates import (
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    BoundedCandidateSession,
    CandidateBackend,
    LocalGGUFCandidateIdentity,
    ModelCandidateProvider,
)


def _local_identity() -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id="qwen2.5-coder-1.5b",
        repo_id="bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        filename="Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
        revision="a" * 40,
        artifact_sha256="b" * 64,
    )


def _azure_identity() -> AzureFoundryCandidateIdentity:
    return AzureFoundryCandidateIdentity(
        endpoint="https://project.services.ai.azure.com/models",
        api_family=AzureFoundryAPIFamily.MODEL_INFERENCE,
        deployment="reviewer-green",
        api_version="2024-05-01-preview",
        model_version="2024-08-06",
        etag='W/"deployment-revision-7"',
    )


def _budget(*, max_calls: int = 2) -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=max_calls,
        max_input_tokens=2_000,
        max_output_tokens=1_000,
        max_total_tokens=4_000,
        max_cost_microusd=50_000,
        timeout_seconds=30.0,
    )


class _FakeBackend:
    def __init__(
        self,
        identity: LocalGGUFCandidateIdentity | AzureFoundryCandidateIdentity,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.candidate_identity = identity
        self.failure = failure
        self.calls: list[tuple[str, int, float]] = []

    def generate(
        self,
        request: str,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.calls.append((request, max_output_tokens, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        return f"proposal:{request}"


def test_candidate_identities_are_frozen_typed_and_secret_free() -> None:
    local = _local_identity()
    azure = _azure_identity()

    assert local.provider is ModelCandidateProvider.LOCAL_GGUF
    assert azure.provider is ModelCandidateProvider.AZURE_FOUNDRY
    assert len(local.identity_digest) == 64
    assert len(azure.identity_digest) == 64
    assert local.identity_digest == _local_identity().identity_digest
    assert azure.identity_digest == _azure_identity().identity_digest
    assert "project.services.ai.azure.com" not in azure.identity_digest
    assert "credential" not in repr(azure).lower()
    with pytest.raises(FrozenInstanceError):
        local.__setattr__("model_id", "mutated")


def test_explicit_local_identity_is_artifact_bound_without_repository_path() -> None:
    identity = LocalGGUFCandidateIdentity(
        model_id="explicit",
        filename="operator.gguf",
        artifact_sha256="A" * 64,
    )

    assert identity.repo_id is None
    assert identity.revision is None
    assert identity.artifact_sha256 == "a" * 64
    assert len(identity.identity_digest) == 64


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("model_id", lambda value: replace(value, model_id="other-model")),
        ("repo_id", lambda value: replace(value, repo_id="owner/other-repo")),
        ("filename", lambda value: replace(value, filename="other.Q4_K_M.gguf")),
        ("revision", lambda value: replace(value, revision="c" * 40)),
        (
            "artifact_sha256",
            lambda value: replace(value, artifact_sha256="d" * 64),
        ),
    ],
)
def test_local_identity_digest_binds_every_exact_artifact_field(
    field: str,
    mutate: Callable[[LocalGGUFCandidateIdentity], LocalGGUFCandidateIdentity],
) -> None:
    local = _local_identity()

    changed = mutate(local)

    assert field
    assert changed.identity_digest != local.identity_digest


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        (
            "endpoint",
            lambda value: replace(
                value,
                endpoint="https://other.services.ai.azure.com/models",
            ),
        ),
        (
            "api_family",
            lambda value: replace(
                value,
                endpoint="https://other.openai.azure.com",
                api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
                api_version="v1",
            ),
        ),
        ("deployment", lambda value: replace(value, deployment="reviewer-blue")),
        (
            "api_version",
            lambda value: replace(value, api_version="2025-01-01-preview"),
        ),
        ("model_version", lambda value: replace(value, model_version="2025-01-31")),
        ("etag", lambda value: replace(value, etag='W/"deployment-revision-8"')),
    ],
)
def test_azure_identity_digest_binds_routing_and_deployment_truth(
    field: str,
    mutate: Callable[
        [AzureFoundryCandidateIdentity],
        AzureFoundryCandidateIdentity,
    ],
) -> None:
    azure = _azure_identity()

    changed = mutate(azure)

    assert field
    assert changed.identity_digest != azure.identity_digest


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, model_id=" qwen"),
        lambda value: replace(value, repo_id="missing-owner"),
        lambda value: replace(value, repo_id="owner/../repo"),
        lambda value: replace(value, filename="../model.gguf"),
        lambda value: replace(value, filename="model.bin"),
        lambda value: replace(value, filename=" model.gguf"),
        lambda value: replace(value, revision="main"),
        lambda value: replace(value, repo_id=None),
        lambda value: replace(value, artifact_sha256="not-a-digest"),
    ],
)
def test_local_identity_rejects_ambiguous_or_mutable_artifacts(
    mutation: Callable[
        [LocalGGUFCandidateIdentity],
        LocalGGUFCandidateIdentity,
    ],
) -> None:
    with pytest.raises(ValueError):
        mutation(_local_identity())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(
            value,
            endpoint="http://project.services.ai.azure.com/models",
        ),
        lambda value: replace(
            value,
            endpoint="https://key@project.services.ai.azure.com/models",
        ),
        lambda value: replace(
            value,
            endpoint="https://project.services.ai.azure.com/models?api-key=secret",
        ),
        lambda value: replace(
            value,
            endpoint="https://project.services.ai.azure.com",
        ),
        lambda value: replace(value, endpoint="https://example.com/models"),
        lambda value: replace(value, deployment="deployment/with/path"),
        lambda value: replace(value, api_version="latest"),
        lambda value: replace(value, model_version=""),
        lambda value: replace(value, etag="line\nbreak"),
        lambda value: replace(value, etag="é"),
        lambda value: replace(value, etag="x" * 513),
        lambda value: replace(
            value,
            api_family=cast(Any, AzureFoundryAPIFamily.MODEL_INFERENCE.value),
        ),
    ],
)
def test_azure_identity_rejects_noncanonical_or_incomplete_routing(
    mutation: Callable[
        [AzureFoundryCandidateIdentity],
        AzureFoundryCandidateIdentity,
    ],
) -> None:
    with pytest.raises(ValueError):
        mutation(_azure_identity())


def test_azure_openai_family_requires_its_root_endpoint_shape() -> None:
    identity = replace(
        _azure_identity(),
        endpoint="https://project.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        api_version="v1",
    )

    assert identity.provider is ModelCandidateProvider.AZURE_FOUNDRY
    with pytest.raises(ValueError, match="endpoint"):
        replace(identity, endpoint="https://project.openai.azure.com/models")
    with pytest.raises(ValueError, match="api_version"):
        replace(identity, api_version="2024-10-21")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://PROJECT.services.ai.azure.com/models",
        "https://project.services.ai.azure.com:443/models",
        "https://project.services.ai.azure.com:invalid/models",
        "https://project.services.ai.azure.com/models#fragment",
    ],
)
def test_azure_endpoint_rejects_noncanonical_authority_or_suffix(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        replace(_azure_identity(), endpoint=endpoint)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, max_calls=0),
        lambda value: replace(value, max_calls=17),
        lambda value: replace(value, max_input_tokens=cast(Any, True)),
        lambda value: replace(value, max_output_tokens=0),
        lambda value: replace(value, max_total_tokens=999),
        lambda value: replace(value, max_cost_microusd=-1),
        lambda value: replace(value, timeout_seconds=0.0),
        lambda value: replace(value, timeout_seconds=3_601.0),
    ],
)
def test_backend_budget_rejects_unbounded_or_incoherent_values(
    mutation: Callable[[BackendCallBudget], BackendCallBudget],
) -> None:
    with pytest.raises(ValueError):
        mutation(_budget())


def test_candidate_backend_protocol_is_runtime_discoverable() -> None:
    backend = _FakeBackend(_local_identity())

    assert isinstance(backend, CandidateBackend)


def test_typed_error_constructors_reject_arbitrary_failure_text() -> None:
    with pytest.raises(ValueError, match="BackendFailure"):
        BackendInfrastructureError(cast(Any, "secret provider response"))
    with pytest.raises(ValueError, match="BackendPolicyFailure"):
        BackendPolicyError(cast(Any, "secret policy detail"))


def test_local_session_forwards_exact_call_without_azure_opt_in() -> None:
    backend = _FakeBackend(_local_identity())
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=False)

    result = session.generate(
        "task-one",
        input_tokens=500,
        max_output_tokens=300,
        estimated_cost_microusd=0,
    )

    assert result == "proposal:task-one"
    assert backend.calls == [("task-one", 300, 30.0)]
    assert session.candidate_identity == _local_identity()
    assert session.snapshot.calls_started == 1
    assert session.snapshot.reserved_tokens == 800


def test_azure_requires_explicit_opt_in_before_backend_call() -> None:
    backend = _FakeBackend(_azure_identity())
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=False)

    with pytest.raises(BackendPolicyError) as captured:
        session.generate(
            "private source must not leave this boundary",
            input_tokens=10,
            max_output_tokens=10,
            estimated_cost_microusd=10,
        )

    assert captured.value.failure is BackendPolicyFailure.AZURE_OPT_IN_REQUIRED
    assert backend.calls == []
    assert session.snapshot.calls_started == 0


def test_opted_in_azure_session_is_single_candidate_and_budget_bounded() -> None:
    backend = _FakeBackend(_azure_identity())
    session = BoundedCandidateSession(backend, _budget(max_calls=1), azure_enabled=True)

    assert session.generate(
        "bounded",
        input_tokens=1_000,
        max_output_tokens=900,
        estimated_cost_microusd=20_000,
    ) == "proposal:bounded"
    with pytest.raises(BackendPolicyError) as captured:
        session.generate(
            "must-not-fallback",
            input_tokens=1,
            max_output_tokens=1,
            estimated_cost_microusd=1,
        )

    assert captured.value.failure is BackendPolicyFailure.CALL_BUDGET_EXHAUSTED
    assert backend.calls == [("bounded", 900, 30.0)]


@pytest.mark.parametrize(
    ("kwargs", "failure"),
    [
        (
            {"input_tokens": 2_001, "max_output_tokens": 1, "estimated_cost_microusd": 0},
            BackendPolicyFailure.INPUT_TOKEN_BUDGET_EXCEEDED,
        ),
        (
            {"input_tokens": 1, "max_output_tokens": 1_001, "estimated_cost_microusd": 0},
            BackendPolicyFailure.OUTPUT_TOKEN_BUDGET_EXCEEDED,
        ),
        (
            {"input_tokens": 1, "max_output_tokens": 1, "estimated_cost_microusd": 50_001},
            BackendPolicyFailure.COST_BUDGET_EXCEEDED,
        ),
    ],
)
def test_budget_rejection_happens_before_backend_call(
    kwargs: dict[str, int],
    failure: BackendPolicyFailure,
) -> None:
    backend = _FakeBackend(_local_identity())
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=False)

    with pytest.raises(BackendPolicyError) as captured:
        session.generate("over-budget", **kwargs)

    assert captured.value.failure is failure
    assert backend.calls == []


def test_total_reserved_token_and_cost_budgets_are_conservative() -> None:
    backend = _FakeBackend(_local_identity())
    session = BoundedCandidateSession(backend, _budget(max_calls=3), azure_enabled=False)
    session.generate(
        "first",
        input_tokens=1_500,
        max_output_tokens=500,
        estimated_cost_microusd=30_000,
    )

    with pytest.raises(BackendPolicyError) as token_error:
        session.generate(
            "second",
            input_tokens=1_500,
            max_output_tokens=501,
            estimated_cost_microusd=1,
        )
    with pytest.raises(BackendPolicyError) as cost_error:
        session.generate(
            "second",
            input_tokens=1,
            max_output_tokens=1,
            estimated_cost_microusd=20_001,
        )

    assert token_error.value.failure is BackendPolicyFailure.TOTAL_TOKEN_BUDGET_EXCEEDED
    assert cost_error.value.failure is BackendPolicyFailure.COST_BUDGET_EXCEEDED
    assert backend.calls == [("first", 500, 30.0)]
    assert session.snapshot.calls_started == 1


def test_backend_identity_drift_fails_closed_before_second_call() -> None:
    backend = _FakeBackend(_local_identity())
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=False)
    backend.candidate_identity = replace(_local_identity(), artifact_sha256="c" * 64)

    with pytest.raises(BackendPolicyError) as captured:
        session.generate(
            "drifted",
            input_tokens=1,
            max_output_tokens=1,
            estimated_cost_microusd=0,
        )

    assert captured.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert backend.calls == []


def test_untyped_backend_exception_is_censored_and_consumes_one_call() -> None:
    secret = "AZURE-CREDENTIAL-DO-NOT-LEAK"
    backend = _FakeBackend(_azure_identity(), failure=RuntimeError(secret))
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=True)

    with pytest.raises(BackendInfrastructureError) as captured:
        session.generate(
            "request",
            input_tokens=10,
            max_output_tokens=20,
            estimated_cost_microusd=100,
        )

    assert captured.value.failure is BackendFailure.INTERNAL
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert session.snapshot.calls_started == 1
    assert len(backend.calls) == 1


def test_typed_backend_failure_stays_censored_and_never_falls_back() -> None:
    failure = BackendInfrastructureError(BackendFailure.RATE_LIMITED)
    backend = _FakeBackend(_azure_identity(), failure=failure)
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=True)

    with pytest.raises(BackendInfrastructureError) as captured:
        session.generate(
            "request",
            input_tokens=10,
            max_output_tokens=20,
            estimated_cost_microusd=100,
        )

    assert captured.value is failure
    assert captured.value.failure is BackendFailure.RATE_LIMITED
    assert len(backend.calls) == 1


def test_session_rejects_non_boolean_opt_in_and_nonconforming_backend() -> None:
    with pytest.raises(ValueError, match="azure_enabled"):
        BoundedCandidateSession(
            _FakeBackend(_local_identity()),
            _budget(),
            azure_enabled=cast(Any, 1),
        )
    with pytest.raises(ValueError, match="backend"):
        BoundedCandidateSession(cast(Any, object()), _budget(), azure_enabled=False)

    invalid_identity_backend = _FakeBackend(_local_identity())
    invalid_identity_backend.candidate_identity = cast(Any, object())
    with pytest.raises(ValueError, match="typed candidate identity"):
        BoundedCandidateSession(
            invalid_identity_backend,
            _budget(),
            azure_enabled=False,
        )
    with pytest.raises(ValueError, match="BackendCallBudget"):
        BoundedCandidateSession(
            _FakeBackend(_local_identity()),
            cast(Any, object()),
            azure_enabled=False,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "input_tokens": cast(Any, True),
            "max_output_tokens": 1,
            "estimated_cost_microusd": 0,
        },
        {
            "input_tokens": 0,
            "max_output_tokens": 0,
            "estimated_cost_microusd": 0,
        },
        {
            "input_tokens": 0,
            "max_output_tokens": 1,
            "estimated_cost_microusd": -1,
        },
    ],
)
def test_invalid_call_accounting_is_rejected_before_backend(
    kwargs: dict[str, int],
) -> None:
    backend = _FakeBackend(_local_identity())
    session = BoundedCandidateSession(backend, _budget(), azure_enabled=False)

    with pytest.raises(ValueError):
        session.generate("invalid", **kwargs)

    assert backend.calls == []
    assert session.snapshot.calls_started == 0
