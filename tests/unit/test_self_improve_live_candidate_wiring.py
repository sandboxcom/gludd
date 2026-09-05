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
from general_ludd.self_improve.codex_comparison import CodexReference, ProposalManifest
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
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    GeneratedProposal,
    ManagedSelfImproveRunner,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.model_candidates import (
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
    def __init__(self) -> None:
        self.close_calls = 0
        self.generate_calls = 0

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
            text="unused",
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


def test_runtime_factory_is_default_off_and_explicit_wiring_stays_lazy(
    tmp_path: Path,
) -> None:
    azure_builds: list[AzureOpenAIConfig] = []
    default_runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
    )
    wired_runner = build_managed_self_improve_runner(
        tmp_path,
        root_runner=cast(object, object()),
        live_candidate_policy=_mixed_policy(),
        azure_backend_factory=lambda config: (
            azure_builds.append(config) or _AzureBackend()
        ),
    )

    assert default_runner.live_candidate_wiring_enabled is False
    assert wired_runner.live_candidate_wiring_enabled is True
    assert azure_builds == []
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
