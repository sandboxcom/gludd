"""Behavioral tests for opt-in live managed-candidate wiring."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

import general_ludd.self_improve as self_improve_package
from general_ludd.self_improve.azure_backend import (
    AzureCandidateResponse,
    AzureCredentialReference,
    AzureCredentialSource,
    AzureOpenAIConfig,
)
from general_ludd.self_improve.candidate_classification import classify_candidate_task
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.live_candidate_wiring import (
    LIVE_CANDIDATE_WIRING_PROTOCOL,
    LiveCandidateWiringPolicy,
    LiveManagedCandidateWiring,
)
from general_ludd.self_improve.managed_candidate_assembly import (
    CandidateAssemblyError,
    CandidateAssemblyFailure,
    CandidatePrivacyState,
)
from general_ludd.self_improve.managed_candidate_routing import (
    ManagedCandidateProposalCodec,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    CapabilityEvidenceOutcomeAdapter,
    GeneratedProposal,
    ManagedSelfImproveRunner,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    BackendPolicyError,
    BackendPolicyFailure,
    LocalGGUFCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.self_improve.model_lifecycle import AcquiredModel
from general_ludd.self_improve.runtime import build_managed_self_improve_runner
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore


def _budget(*, max_output_tokens: int = 256) -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=2,
        max_input_tokens=256,
        max_output_tokens=max_output_tokens,
        max_total_tokens=1_024,
        max_cost_microusd=50_000,
        timeout_seconds=30.0,
    )


def _local_identity(*, model_id: str = "local-coder") -> LocalGGUFCandidateIdentity:
    return LocalGGUFCandidateIdentity(
        model_id=model_id,
        repo_id="example/local-coder",
        revision="a" * 40,
        filename="local-coder.Q4_K_M.gguf",
        artifact_sha256="b" * 64,
    )


def _azure_identity() -> AzureFoundryCandidateIdentity:
    return AzureFoundryCandidateIdentity(
        endpoint="https://unit-test.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        deployment="coder-deployment",
        api_version="v1",
        model_version="2026-09-01",
        etag='"immutable-etag"',
    )


def _containerapp_identity() -> AzureContainerAppCandidateIdentity:
    return AzureContainerAppCandidateIdentity(
        endpoint="https://gludd-vllm-proof.kindstone.eastus.azurecontainerapps.io",
        resource_id=(
            "/subscriptions/11111111-1111-1111-1111-111111111111/"
            "resourceGroups/unit-test-group/providers/Microsoft.App/"
            "containerApps/gludd-vllm-proof"
        ),
        revision_name="gludd-vllm-proof--0000007",
        image_digest="sha256:" + "c" * 64,
        model_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_revision="d" * 40,
        workload_profile_type="Consumption-GPU-NC8as-T4",
    )


def _azure_config(*, enabled: bool = True) -> AzureOpenAIConfig:
    return AzureOpenAIConfig(
        azure_enabled=enabled,
        endpoint="https://unit-test.openai.azure.com",
        api_family=AzureFoundryAPIFamily.AZURE_OPENAI,
        api_version="v1",
        subscription_id="11111111-1111-1111-1111-111111111111",
        resource_group="unit-test-group",
        account_name="unit-test",
        deployment="coder-deployment",
        credential=AzureCredentialReference(
            AzureCredentialSource.API_KEY_ENV,
            "AZURE_UNIT_TEST_KEY",
        ),
    )


class _LocalBackend:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.identity = _local_identity()

    @property
    def candidate_identity(self) -> LocalGGUFCandidateIdentity:
        return self.identity

    def generate(
        self,
        request: str,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        self.requests.append(request)
        assert max_output_tokens == 32
        assert timeout_seconds == 30.0
        return "local-result"


class _AzureBackend:
    def __init__(self, text: str = "unused") -> None:
        self.close_calls = 0
        self.generate_calls = 0
        self.text = text

    @property
    def candidate_identity(self) -> AzureFoundryCandidateIdentity:
        return _azure_identity()

    def generate(
        self,
        _request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        del max_output_tokens, timeout_seconds
        self.generate_calls += 1
        return AzureCandidateResponse(
            text=self.text,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def close(self) -> None:
        self.close_calls += 1


class _ContainerAppBackend:
    def __init__(self, text: str = "containerapp-result") -> None:
        self.close_calls = 0
        self.generate_calls = 0
        self.text = text

    @property
    def candidate_identity(self) -> AzureContainerAppCandidateIdentity:
        return _containerapp_identity()

    def generate(
        self,
        _request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        del max_output_tokens, timeout_seconds
        self.generate_calls += 1
        return AzureCandidateResponse(
            text=self.text,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )

    def close(self) -> None:
        self.close_calls += 1


def _mixed_policy() -> LiveCandidateWiringPolicy:
    return LiveCandidateWiringPolicy(
        local_budget=_budget(),
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        ),
        azure_config=_azure_config(),
        azure_budget=_budget(),
        azure_estimated_cost_microusd=1_000,
    )


def _three_provider_policy() -> LiveCandidateWiringPolicy:
    return LiveCandidateWiringPolicy(
        local_budget=_budget(),
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
            ModelCandidateProvider.AZURE_CONTAINER_APP,
        ),
        azure_config=_azure_config(),
        azure_budget=_budget(),
        azure_estimated_cost_microusd=1_000,
        containerapp_identity=_containerapp_identity(),
        containerapp_budget=_budget(),
        containerapp_estimated_cost_microusd=2_000,
    )


def test_live_wiring_assembles_local_foundry_and_containerapp_candidates() -> None:
    classification = classify_candidate_task("Implement one bounded public feature.")
    foundry = _AzureBackend()
    containerapp = _ContainerAppBackend()
    wiring = LiveManagedCandidateWiring(
        _three_provider_policy(),
        azure_backend_factory=lambda _config: foundry,
        containerapp_backend_factory=lambda _identity: containerapp,
    )

    with wiring.assemble(
        classification,
        expected_classification_digest=classification.classification_digest,
        local_backend=_LocalBackend(),
        privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        input_tokens=12,
        max_output_tokens=32,
    ) as candidate_set:
        assert candidate_set.assembly.providers == (
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
            ModelCandidateProvider.AZURE_CONTAINER_APP,
        )
        assert candidate_set.azure_session is not None
        assert candidate_set.containerapp_session is not None

    assert foundry.close_calls == 1
    assert containerapp.close_calls == 1
    candidate_set.close()
    assert foundry.close_calls == 1
    assert containerapp.close_calls == 1


def test_containerapp_only_remote_wiring_never_constructs_foundry_backend() -> None:
    classification = classify_candidate_task("Test one bounded public feature.")
    foundry_builds: list[AzureOpenAIConfig] = []
    containerapp = _ContainerAppBackend()
    policy = LiveCandidateWiringPolicy(
        local_budget=_budget(),
        required_providers=(
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_CONTAINER_APP,
        ),
        containerapp_identity=_containerapp_identity(),
        containerapp_budget=_budget(),
        containerapp_estimated_cost_microusd=2_000,
    )
    wiring = LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=lambda config: (
            foundry_builds.append(config) or _AzureBackend()
        ),
        containerapp_backend_factory=lambda identity: (
            containerapp if identity == _containerapp_identity() else pytest.fail()
        ),
    )

    with wiring.assemble(
        classification,
        expected_classification_digest=classification.classification_digest,
        local_backend=_LocalBackend(),
        privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        input_tokens=12,
        max_output_tokens=32,
    ) as candidate_set:
        assert candidate_set.azure_session is None
        assert candidate_set.containerapp_session is not None

    assert foundry_builds == []
    assert containerapp.close_calls == 1


def test_containerapp_policy_requires_complete_budget_and_explicit_provider() -> None:
    with pytest.raises(ValueError, match="containerapp_identity"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            containerapp_budget=_budget(),
        )
    with pytest.raises(ValueError, match="containerapp_budget"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            containerapp_identity=_containerapp_identity(),
        )
    with pytest.raises(ValueError, match="required Azure Container App"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            required_providers=(
                ModelCandidateProvider.LOCAL_GGUF,
                ModelCandidateProvider.AZURE_CONTAINER_APP,
            ),
        )
    with pytest.raises(ValueError, match="estimated cost"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            containerapp_identity=_containerapp_identity(),
            containerapp_budget=_budget(),
            containerapp_estimated_cost_microusd=50_001,
        )


def test_live_wiring_assembles_authorized_mixed_set_and_closes_azure() -> None:
    task_text = "Implement a bounded Python feature without exposing source."
    classification = classify_candidate_task(task_text)
    local = _LocalBackend()
    azure = _AzureBackend()
    events: list[dict[str, object]] = []
    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=lambda _config: azure,
        event_sink=events.append,
    )

    with wiring.assemble(
        classification,
        expected_classification_digest=classification.classification_digest,
        local_backend=local,
        privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        input_tokens=12,
        max_output_tokens=32,
    ) as candidate_set:
        assert candidate_set.assembly.protocol == "gludd-managed-candidate-assembly-v1"
        assert candidate_set.protocol == LIVE_CANDIDATE_WIRING_PROTOCOL
        assert candidate_set.assembly.providers == (
            ModelCandidateProvider.LOCAL_GGUF,
            ModelCandidateProvider.AZURE_FOUNDRY,
        )
        assert candidate_set.azure_session is not None
        assert candidate_set.local_session.generate(
            "approved-local-request",
            input_tokens=12,
            max_output_tokens=32,
            estimated_cost_microusd=0,
        ) == "local-result"

    assert local.requests == ["approved-local-request"]
    assert azure.generate_calls == 0
    assert azure.close_calls == 1
    candidate_set.close()
    assert azure.close_calls == 1
    assert [event["event"] for event in events] == [
        "self_improve_candidate_classified",
        "self_improve_managed_candidate_admitted",
        "self_improve_managed_candidate_admitted",
        "self_improve_managed_candidates_assembled",
    ]
    serialized = json.dumps(events, sort_keys=True) + repr(candidate_set.assembly)
    assert task_text not in serialized
    assert "unit-test.openai.azure.com" not in serialized
    assert "local-coder.Q4_K_M.gguf" not in serialized
    assert "AZURE_UNIT_TEST_KEY" not in serialized


def test_local_only_wiring_never_constructs_an_azure_backend() -> None:
    classification = classify_candidate_task("Implement a focused coding change.")
    azure_builds: list[AzureOpenAIConfig] = []
    policy = LiveCandidateWiringPolicy(local_budget=_budget())
    wiring = LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
    )

    with wiring.assemble(
        classification,
        expected_classification_digest=classification.classification_digest,
        local_backend=_LocalBackend(),
        privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        input_tokens=8,
        max_output_tokens=32,
    ) as candidate_set:
        assert candidate_set.assembly.providers == (
            ModelCandidateProvider.LOCAL_GGUF,
        )
        assert candidate_set.azure_session is None

    assert azure_builds == []


def test_privacy_and_local_budget_fail_before_azure_discovery() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    azure_builds: list[AzureOpenAIConfig] = []
    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
    )

    with pytest.raises(CandidateAssemblyError) as privacy_error:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.BLOCKED,
            input_tokens=8,
            max_output_tokens=32,
        )
    assert privacy_error.value.failure is CandidateAssemblyFailure.PRIVACY_INELIGIBLE

    with pytest.raises(BackendPolicyError) as budget_error:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=512,
        )
    assert budget_error.value.failure is BackendPolicyFailure.OUTPUT_TOKEN_BUDGET_EXCEEDED
    assert azure_builds == []


def test_configuration_drift_is_rejected_before_azure_discovery() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    azure_builds: list[AzureOpenAIConfig] = []
    policy = _mixed_policy()
    wiring = LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
    )
    object.__setattr__(policy, "local_budget", _budget(max_output_tokens=128))

    with pytest.raises(CandidateAssemblyError) as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value.failure is CandidateAssemblyFailure.CONFIGURATION_DRIFT
    assert azure_builds == []


def test_malformed_azure_configuration_drift_is_typed_before_discovery() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    azure_builds: list[AzureOpenAIConfig] = []
    policy = _mixed_policy()
    wiring = LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
    )
    object.__setattr__(policy, "azure_budget", None)

    with pytest.raises(CandidateAssemblyError) as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value.failure is CandidateAssemblyFailure.CONFIGURATION_DRIFT
    assert azure_builds == []


def test_local_configuration_drift_during_azure_discovery_fails_closed() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    local = _LocalBackend()
    azure = _AzureBackend()
    policy = _mixed_policy()

    def drift_during_discovery(_config: AzureOpenAIConfig) -> _AzureBackend:
        object.__setattr__(policy, "local_budget", _budget(max_output_tokens=128))
        return azure

    wiring = LiveManagedCandidateWiring(
        policy,
        azure_backend_factory=drift_during_discovery,
    )

    with pytest.raises(CandidateAssemblyError) as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=local,
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value.failure is CandidateAssemblyFailure.CONFIGURATION_DRIFT
    assert local.requests == []
    assert azure.close_calls == 1


def test_local_identity_drift_during_azure_discovery_fails_closed() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    local = _LocalBackend()
    azure = _AzureBackend()

    def drift_during_discovery(_config: AzureOpenAIConfig) -> _AzureBackend:
        local.identity = _local_identity(model_id="drifted-local-coder")
        return azure

    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=drift_during_discovery,
    )

    with pytest.raises(BackendPolicyError) as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=local,
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value.failure is BackendPolicyFailure.IDENTITY_DRIFT
    assert local.requests == []
    assert azure.close_calls == 1


def test_azure_discovery_failure_is_terminal_without_local_fallback() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    local = _LocalBackend()
    failure = BackendInfrastructureError(BackendFailure.TRANSPORT)

    def fail_discovery(_config: AzureOpenAIConfig) -> _AzureBackend:
        raise failure

    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=fail_discovery,
    )

    with pytest.raises(BackendInfrastructureError) as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=local,
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value is failure
    assert local.requests == []


def test_policy_rejects_ambiguous_or_disabled_azure_configuration() -> None:
    with pytest.raises(ValueError, match="azure_config"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_budget=_budget(),
        )
    with pytest.raises(ValueError, match="explicitly enabled"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_config=_azure_config(enabled=False),
            azure_budget=_budget(),
        )


def test_policy_rejects_invalid_provider_and_budget_boundaries() -> None:
    with pytest.raises(ValueError, match="local_budget"):
        LiveCandidateWiringPolicy(local_budget=cast(BackendCallBudget, object()))
    with pytest.raises(ValueError, match="required_providers"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            required_providers=(),
        )
    with pytest.raises(ValueError, match="requires the local provider"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            required_providers=(ModelCandidateProvider.AZURE_FOUNDRY,),
            azure_config=_azure_config(),
            azure_budget=_budget(),
        )
    with pytest.raises(ValueError, match="hard bound"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_estimated_cost_microusd=cast(int, True),
        )
    with pytest.raises(ValueError, match="AzureOpenAIConfig"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_config=cast(AzureOpenAIConfig, object()),
            azure_budget=_budget(),
        )
    with pytest.raises(ValueError, match="azure_budget"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_config=_azure_config(),
        )
    with pytest.raises(ValueError, match="estimated cost"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            azure_config=_azure_config(),
            azure_budget=_budget(),
            azure_estimated_cost_microusd=50_001,
        )


def test_wiring_constructor_rejects_untyped_boundaries() -> None:
    policy = LiveCandidateWiringPolicy(local_budget=_budget())
    with pytest.raises(ValueError, match="policy must"):
        LiveManagedCandidateWiring(cast(LiveCandidateWiringPolicy, object()))
    with pytest.raises(ValueError, match="factory must"):
        LiveManagedCandidateWiring(
            policy,
            azure_backend_factory=cast(Callable[..., object], object()),
        )
    with pytest.raises(ValueError, match="event_sink"):
        LiveManagedCandidateWiring(
            policy,
            event_sink=cast(Callable[[dict[str, object]], None], object()),
        )


def test_required_azure_provider_needs_explicit_azure_configuration() -> None:
    with pytest.raises(ValueError, match="required Azure provider"):
        LiveCandidateWiringPolicy(
            local_budget=_budget(),
            required_providers=(
                ModelCandidateProvider.LOCAL_GGUF,
                ModelCandidateProvider.AZURE_FOUNDRY,
            ),
        )


def test_event_failure_closes_discovered_azure_resource() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    azure = _AzureBackend()
    event_count = 0

    def fail_after_classification(_event: dict[str, object]) -> None:
        nonlocal event_count
        event_count += 1
        if event_count == 2:
            raise RuntimeError("hostile event sink detail")

    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=lambda _config: azure,
        event_sink=fail_after_classification,
    )

    with pytest.raises(RuntimeError, match="candidate event publication failed") as raised:
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert raised.value.__cause__ is None
    assert "hostile event sink detail" not in str(raised.value)
    assert azure.close_calls == 1


def test_invalid_azure_discovery_result_is_terminal() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=lambda _config: cast(_AzureBackend, object()),
    )

    with pytest.raises(ValueError, match="invalid backend"):
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )


def test_second_remote_discovery_failure_closes_first_backend() -> None:
    classification = classify_candidate_task("Implement a bounded coding change.")
    azure = _AzureBackend()
    wiring = LiveManagedCandidateWiring(
        _three_provider_policy(),
        azure_backend_factory=lambda _config: azure,
        containerapp_backend_factory=lambda _identity: cast(
            _ContainerAppBackend, object()
        ),
    )

    with pytest.raises(ValueError, match="invalid backend"):
        wiring.assemble(
            classification,
            expected_classification_digest=classification.classification_digest,
            local_backend=_LocalBackend(),
            privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
            input_tokens=8,
            max_output_tokens=32,
        )

    assert azure.close_calls == 1


def test_runtime_factory_is_default_off_and_explicit_wiring_stays_lazy(
    tmp_path: Path,
) -> None:
    azure_builds: list[AzureOpenAIConfig] = []
    containerapp_builds: list[AzureContainerAppCandidateIdentity] = []
    default_runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
    )
    wired_runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
        live_candidate_policy=_three_provider_policy(),
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
        containerapp_backend_factory=lambda identity: (
            containerapp_builds.append(identity) or _ContainerAppBackend()
        ),
    )

    assert default_runner.live_candidate_wiring_enabled is False
    assert wired_runner.live_candidate_wiring_enabled is True
    assert azure_builds == []
    assert containerapp_builds == []
    assert self_improve_package.LiveCandidateWiringPolicy is LiveCandidateWiringPolicy
    assert "LiveCandidateWiringPolicy" in self_improve_package.__all__


def test_runtime_factory_requires_policy_and_supports_default_discovery_factory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="requires live_candidate_policy"):
        build_managed_self_improve_runner(
            tmp_path,
            root_runner=cast(object, object()),
            azure_backend_factory=lambda _config: _AzureBackend(),
        )

    runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
        live_candidate_policy=LiveCandidateWiringPolicy(local_budget=_budget()),
    )

    assert runner.live_candidate_wiring_enabled is True
    assert runner.live_candidate_wiring is not None
    assert runner.live_candidate_wiring.policy.local_budget == _budget()


def test_runtime_factory_publishes_only_content_free_candidate_events(
    tmp_path: Path,
) -> None:
    task_text = "Implement a private-looking but approved Python feature."
    progress: list[str] = []
    azure = _AzureBackend()
    runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
        progress_sink=progress.append,
        live_candidate_policy=_mixed_policy(),
        azure_backend_factory=lambda _config: azure,
    )
    wiring = runner.live_candidate_wiring
    assert wiring is not None
    classification = classify_candidate_task(task_text)

    with wiring.assemble(
        classification,
        expected_classification_digest=classification.classification_digest,
        local_backend=_LocalBackend(),
        privacy_state=CandidatePrivacyState.APPROVED_PUBLIC,
        input_tokens=8,
        max_output_tokens=32,
    ):
        pass

    assert len(progress) == 4
    assert all(
        message.startswith("SELF_IMPROVE_MANAGED_CANDIDATE_EVENT {")
        for message in progress
    )
    serialized = "\n".join(progress)
    assert task_text not in serialized
    assert "unit-test.openai.azure.com" not in serialized
    assert "AZURE_UNIT_TEST_KEY" not in serialized
    assert azure.close_calls == 1


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.150",
        objective="Implement a bounded Python feature.",
        canonical_make_commands=(
            "make test-files TESTFILES=tests/unit/test_example.py",
        ),
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="c" * 40,
        reference_sha="d" * 40,
        changed_files=frozenset({"src/general_ludd/example.py"}),
        test_files=frozenset({"tests/unit/test_example.py"}),
        changed_lines=1,
        elapsed_seconds=1.0,
    )


def _proposal() -> ProposalManifest:
    return ProposalManifest.from_json(
        """{
          "schema_version": 1,
          "baseline_sha": "cccccccccccccccccccccccccccccccccccccccc",
          "task_id": "S83.150",
          "edits": [{
            "operation": "replace",
            "path": "src/general_ludd/example.py",
            "old_text": "return 0",
            "new_text": "return 1"
          }],
          "tests": ["tests/unit/test_example.py"],
          "make_commands": [
            "make test-files TESTFILES=tests/unit/test_example.py"
          ],
          "commit_message": "feat: improve example"
        }"""
    )


class _AcquisitionManager:
    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self.cache_root = model_path.parent
        self.releases = 0

    @contextmanager
    def acquire(self, *_args: object, **_kwargs: object) -> Iterator[AcquiredModel]:
        lease = self._model_path.parent / "model.lease"
        lease.write_text("owned", encoding="utf-8")
        try:
            yield AcquiredModel(
                path=self._model_path,
                model_id="local-coder",
                repo_id="example/local-coder",
                filename=self._model_path.name,
                resolved_revision="a" * 40,
                artifact_sha256="b" * 64,
                source="cache",
                manifest_path=self._model_path.parent / "manifest.json",
                lease_path=lease,
            )
        finally:
            lease.unlink(missing_ok=True)
            self.releases += 1


def _approved_plan(repo_root: Path, model_path: Path) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-live-candidate-wiring",
        todo_id="S83.150",
        project_id="live-candidate-wiring",
        repo_root=repo_root,
        task=_task(),
        reference=_reference(),
        prompt="bounded approved prompt",
        required_output_tokens=32,
        max_attempts=1,
        explicit_model_path=model_path,
    )


def _runner(
    wiring: LiveManagedCandidateWiring,
    generated: list[tuple[Path, str]],
) -> ManagedSelfImproveRunner:
    def generate(
        model_path: Path,
        prompt: object,
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
    ) -> ProposalManifest:
        generated.append((model_path, cast(str, prompt)))
        return _proposal()

    return ManagedSelfImproveRunner(
        proposal_generator=cast(Callable[..., ProposalManifest], generate),
        attempt_evaluator=cast(Callable[..., object], lambda *_args, **_kwargs: object()),
        live_candidate_wiring=wiring,
    )


def test_managed_runner_uses_live_assembly_before_legacy_local_generation(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    manager = _AcquisitionManager(model_path)
    generated: list[tuple[Path, str]] = []
    events: list[dict[str, object]] = []
    azure = _AzureBackend()
    wiring = LiveManagedCandidateWiring(
        _mixed_policy(),
        azure_backend_factory=lambda _config: azure,
        event_sink=events.append,
    )
    runner = _runner(wiring, generated)
    plan = _approved_plan(tmp_path, model_path)

    result = runner._generate_proposal(
        plan,
        plan.prompt,
        None,
        cast(object, manager),
        False,
        None,
        None,
    )

    assert isinstance(result, GeneratedProposal)
    assert generated == [(model_path, "bounded approved prompt")]
    assert manager.releases == 1
    assert azure.close_calls == 1
    assert events[-1]["event"] == "self_improve_managed_candidates_assembled"


def test_managed_runner_does_not_fall_back_when_azure_discovery_fails(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    manager = _AcquisitionManager(model_path)
    generated: list[tuple[Path, str]] = []

    def fail_discovery(_config: AzureOpenAIConfig) -> _AzureBackend:
        raise BackendInfrastructureError(BackendFailure.UNAVAILABLE)

    runner = _runner(
        LiveManagedCandidateWiring(
            _mixed_policy(),
            azure_backend_factory=fail_discovery,
        ),
        generated,
    )
    plan = _approved_plan(tmp_path, model_path)

    with pytest.raises(BackendInfrastructureError) as raised:
        runner._generate_proposal(
            plan,
            plan.prompt,
            None,
            cast(object, manager),
            False,
            None,
            None,
        )

    assert raised.value.failure is BackendFailure.UNAVAILABLE
    assert generated == []
    assert manager.releases == 1


def test_managed_runner_rechecks_privacy_before_any_live_discovery(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    manager = _AcquisitionManager(model_path)
    generated: list[tuple[Path, str]] = []
    azure_builds: list[AzureOpenAIConfig] = []
    runner = _runner(
        LiveManagedCandidateWiring(
            _mixed_policy(),
            azure_backend_factory=lambda config: (
                azure_builds.append(config) or _AzureBackend()
            ),
        ),
        generated,
    )
    plan = _approved_plan(tmp_path, model_path)
    policy_dir = tmp_path / ".gludd"
    policy_dir.mkdir()
    (policy_dir / "self-improve-policy.json").write_text(
        '{"schema_version":1,"default_access":"private",'
        '"private_paths":[],"public_paths":[]}',
        encoding="utf-8",
    )

    with pytest.raises(SelfImprovePolicyViolation):
        runner._generate_proposal(
            plan,
            plan.prompt,
            None,
            cast(object, manager),
            False,
            None,
            None,
        )

    assert generated == []
    assert azure_builds == []
    assert manager.releases == 0


def test_managed_routing_privacy_blocks_before_codec_models_and_evidence(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    plan = _approved_plan(tmp_path, model_path)
    policy_dir = tmp_path / ".gludd"
    policy_dir.mkdir()
    policy_dir.joinpath("self-improve-policy.json").write_text(
        '{"schema_version":1,"default_access":"public",'
        '"private_paths":["src/general_ludd/example.py"],"public_paths":[]}',
        encoding="utf-8",
    )
    model_builds: list[str] = []
    outcome_builds: list[Path] = []
    codec_builds: list[str] = []
    azure_builds: list[AzureOpenAIConfig] = []
    proposal_calls: list[str] = []
    evaluator_calls: list[str] = []
    progress: list[str] = []
    store = CapabilityEvidenceStore(str(tmp_path / "candidate-evidence.json"))
    codec = ManagedCandidateProposalCodec(
        request_text="bounded approved prompt",
        decoder=lambda text: GeneratedProposal(ProposalManifest.from_json(text)),
        protocol_digest="5" * 64,
        sampling_digest="6" * 64,
    )

    def model_manager_factory(**_kwargs: object) -> _AcquisitionManager:
        model_builds.append("built")
        return _AcquisitionManager(model_path)

    def outcome_adapter_factory(cache_root: Path) -> CapabilityEvidenceOutcomeAdapter:
        outcome_builds.append(cache_root)
        return CapabilityEvidenceOutcomeAdapter(store)

    def codec_factory(
        _prompt: object,
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
    ) -> ManagedCandidateProposalCodec[GeneratedProposal]:
        codec_builds.append("built")
        return codec

    def generate(*_args: object, **_kwargs: object) -> ProposalManifest:
        proposal_calls.append("called")
        return _proposal()

    def evaluate(*_args: object, **_kwargs: object) -> AttemptResult:
        evaluator_calls.append("called")
        raise AssertionError("private work reached evaluator")

    runner = ManagedSelfImproveRunner(
        proposal_generator=cast(Callable[..., ProposalManifest], generate),
        attempt_evaluator=cast(Callable[..., AttemptResult], evaluate),
        model_manager_factory=cast(Callable[..., object], model_manager_factory),
        outcome_adapter_factory=outcome_adapter_factory,
        progress_sink=progress.append,
        live_candidate_wiring=LiveManagedCandidateWiring(
            _mixed_policy(),
            azure_backend_factory=lambda config: (
                azure_builds.append(config) or _AzureBackend()
            ),
        ),
        remote_proposal_codec_factory=codec_factory,
    )

    with pytest.raises(SelfImprovePolicyViolation):
        runner.run(plan)

    assert model_builds == []
    assert outcome_builds == []
    assert codec_builds == []
    assert azure_builds == []
    assert proposal_calls == []
    assert evaluator_calls == []
    assert store.list_all() == []
    assert all("src/general_ludd/example.py" not in message for message in progress)


def _routed_proposal(*, new_text: str) -> ProposalManifest:
    value = json.loads(_proposal().to_json())
    value["edits"][0]["new_text"] = new_text
    return ProposalManifest.from_json(json.dumps(value))


def _candidate_evidence(*, accepted: bool) -> CandidateEvidence:
    return CandidateEvidence(
        changed_files=frozenset({"src/general_ludd/example.py"}),
        tests_passed=accepted,
        warnings=0,
        coverage_aggregate=0.9 if accepted else 0.0,
        coverage_min_file=0.8 if accepted else 0.0,
        ruff_passed=accepted,
        mypy_passed=accepted,
        docstrings_passed=accepted,
        markdown_passed=accepted,
        cleanup_passed=True,
        commit_count=1,
        worktree_clean=True,
        elapsed_seconds=0.1,
    )


def test_managed_runner_routes_real_local_and_foundry_work_by_calibrated_outcome(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    local = _routed_proposal(new_text="return 1")
    remote = _routed_proposal(new_text="return 2")
    azure = _AzureBackend(remote.to_json())
    progress: list[str] = []
    generated: list[tuple[Path, str]] = []
    store = CapabilityEvidenceStore(str(tmp_path / "candidate-evidence.json"))
    accept_local = False

    def generate(
        model: Path,
        prompt: object,
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
    ) -> ProposalManifest:
        generated.append((model, cast(str, prompt)))
        return local

    def evaluate(
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: object,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert merge is False
        proposal = cast(object, bound).proposal
        accepted = accept_local or proposal == remote
        return AttemptResult(
            comparison=ComparisonResult(
                accepted=accepted,
                score=1.0 if accepted else 0.0,
                blockers=() if accepted else ("quality",),
                changed_file_precision=1.0,
                changed_file_recall=1.0,
            ),
            evidence=_candidate_evidence(accepted=accepted),
            patch_equivalence="accepted" if accepted else "rejected",
            proposal=proposal,
            diagnostics="",
            attempt_identity_digest=expected_attempt_identity_digest,
        )

    codec = ManagedCandidateProposalCodec(
        request_text="bounded approved prompt",
        decoder=lambda text: GeneratedProposal(ProposalManifest.from_json(text)),
        protocol_digest="1" * 64,
        sampling_digest="2" * 64,
    )
    runner = ManagedSelfImproveRunner(
        proposal_generator=cast(Callable[..., ProposalManifest], generate),
        attempt_evaluator=cast(Callable[..., AttemptResult], evaluate),
        progress_sink=progress.append,
        live_candidate_wiring=LiveManagedCandidateWiring(
            _mixed_policy(),
            azure_backend_factory=lambda _config: azure,
        ),
        remote_proposal_codec_factory=lambda _prompt, _task, _reference: codec,
    )
    plan = _approved_plan(tmp_path, model_path)
    outcomes = CapabilityEvidenceOutcomeAdapter(store)

    first = runner._generate_proposal(
        plan,
        plan.prompt,
        None,
        cast(object, _AcquisitionManager(model_path)),
        False,
        None,
        None,
        outcomes=outcomes,
        attempt=1,
    )

    assert first.proposal == remote
    assert first.evaluated_result is not None
    assert first.evaluated_result.comparison.accepted is True
    assert first.selected_candidate_provider is ModelCandidateProvider.AZURE_FOUNDRY
    assert azure.generate_calls == 1
    assert generated == [(model_path, "bounded approved prompt")]
    assert len(store.list_all()) == 2

    accept_local = True
    second = runner._generate_proposal(
        plan,
        plan.prompt,
        None,
        cast(object, _AcquisitionManager(model_path)),
        False,
        None,
        None,
        outcomes=outcomes,
        attempt=1,
    )

    assert second.proposal == remote
    assert second.selected_candidate_provider is ModelCandidateProvider.AZURE_FOUNDRY
    assert azure.generate_calls == 2
    assert len(store.list_all()) == 4
    rendered = "\n".join(progress)
    assert "SELF_IMPROVE_CANDIDATE_ROUTING_EVENT" in rendered
    assert "bounded approved prompt" not in rendered
    assert "return 1" not in rendered
    assert "return 2" not in rendered


def test_managed_run_uses_containerapp_assistant_without_duplicate_evaluation(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "local-coder.Q4_K_M.gguf"
    model_path.write_bytes(b"model")
    local = _routed_proposal(new_text="return 1")
    remote = _routed_proposal(new_text="return 3")
    containerapp = _ContainerAppBackend(remote.to_json())
    manager = _AcquisitionManager(model_path)
    store = CapabilityEvidenceStore(str(tmp_path / "candidate-evidence.json"))
    evaluations: list[ProposalManifest] = []
    progress: list[str] = []

    def evaluate(
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: object,
        _attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        assert merge is False
        proposal = cast(object, bound).proposal
        evaluations.append(proposal)
        accepted = proposal == remote
        return AttemptResult(
            comparison=ComparisonResult(
                accepted=accepted,
                score=1.0 if accepted else 0.0,
                blockers=() if accepted else ("quality",),
                changed_file_precision=1.0,
                changed_file_recall=1.0,
            ),
            evidence=_candidate_evidence(accepted=accepted),
            patch_equivalence="accepted" if accepted else "rejected",
            proposal=proposal,
            diagnostics="",
            attempt_identity_digest=expected_attempt_identity_digest,
        )

    codec = ManagedCandidateProposalCodec(
        request_text="bounded approved prompt",
        decoder=lambda text: GeneratedProposal(ProposalManifest.from_json(text)),
        protocol_digest="3" * 64,
        sampling_digest="4" * 64,
    )
    runner = ManagedSelfImproveRunner(
        proposal_generator=cast(
            Callable[..., ProposalManifest],
            lambda *_args, **_kwargs: local,
        ),
        attempt_evaluator=cast(Callable[..., AttemptResult], evaluate),
        model_manager_factory=cast(Callable[..., object], lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _root: CapabilityEvidenceOutcomeAdapter(store),
        progress_sink=progress.append,
        live_candidate_wiring=LiveManagedCandidateWiring(
            LiveCandidateWiringPolicy(
                local_budget=_budget(),
                required_providers=(
                    ModelCandidateProvider.LOCAL_GGUF,
                    ModelCandidateProvider.AZURE_CONTAINER_APP,
                ),
                containerapp_identity=_containerapp_identity(),
                containerapp_budget=_budget(),
                containerapp_estimated_cost_microusd=2_000,
            ),
            containerapp_backend_factory=lambda _identity: containerapp,
        ),
        remote_proposal_codec_factory=lambda _prompt, _task, _reference: codec,
    )

    result = runner.run(_approved_plan(tmp_path, model_path))

    assert result.accepted is True
    assert result.final_result.proposal == remote
    assert evaluations == [local, remote]
    assert containerapp.generate_calls == 1
    assert containerapp.close_calls == 1
    assert manager.releases == 1
    assert len(store.list_all()) == 2
    assert any("azure_container_app" in message for message in progress)
