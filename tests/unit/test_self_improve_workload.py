"""Cross-backend SelfImprovementWorkload orchestration tests.

All cloud providers are exercised through fakes; no live Azure or FreeLLM
resources are contacted. The workload discover->predict->execute->evaluate->learn
pipeline is verified end-to-end across local, Azure Container Apps, Azure VM/VMSS,
and catalog free-tier candidates.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from general_ludd.models.candidate_identity import CatalogFreeTierCandidateIdentity
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    AzureFoundryAPIFamily,
    AzureFoundryCandidateIdentity,
    AzureVmVmssCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
    CandidateBackend,
    LocalGGUFCandidateIdentity,
    ModelCandidateIdentity,
    ModelCandidateProvider,
)
from general_ludd.self_improve.workload import (
    SelfImprovementWorkload,
    WorkloadCandidate,
    WorkloadExecution,
    WorkloadResult,
    WorkloadTodoStore,
)

_LOCAL_IDENTITY = LocalGGUFCandidateIdentity(
    model_id="qwen2.5-coder-1.5b",
    repo_id="bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
    filename="Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
    revision="a" * 40,
    artifact_sha256="b" * 64,
)

_FOUNDRY_IDENTITY = AzureFoundryCandidateIdentity(
    endpoint="https://project.services.ai.azure.com/models",
    api_family=AzureFoundryAPIFamily.MODEL_INFERENCE,
    deployment="reviewer-green",
    api_version="2024-05-01-preview",
    model_version="2024-08-06",
    etag='W/"deployment-revision-7"',
)

_CONTAINERAPP_IDENTITY = AzureContainerAppCandidateIdentity(
    endpoint="https://gludd-vllm-proof.kindstone-1234.eastus.azurecontainerapps.io",
    resource_id=(
        "/subscriptions/12345678-1234-1234-1234-123456789abc/"
        "resourceGroups/gludd-models-eastus/providers/Microsoft.App/"
        "containerApps/gludd-vllm-proof"
    ),
    revision_name="gludd-vllm-proof--0000007",
    image_digest="sha256:" + "c" * 64,
    model_name="Qwen/Qwen2.5-0.5B-Instruct",
    model_revision="d" * 40,
    workload_profile_type="Consumption-GPU-NC8as-T4",
)

_VM_IDENTITY = AzureVmVmssCandidateIdentity(
    endpoint="https://gludd-vllm-vm.eastus.cloudapp.azure.com",
    resource_id=(
        "/subscriptions/12345678-1234-1234-1234-123456789abc/"
        "resourceGroups/gludd-models-eastus/providers/Microsoft.Compute/"
        "virtualMachines/gludd-vllm-vm"
    ),
    instance_id="0",
    image_urn="canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest",
    model_name="Qwen/Qwen2.5-0.5B-Instruct",
    model_revision="e" * 40,
    vm_size="Standard_NC6s_v3",
)

_VMSS_IDENTITY = AzureVmVmssCandidateIdentity(
    endpoint="https://gludd-vllm-vmss.eastus.cloudapp.azure.com",
    resource_id=(
        "/subscriptions/12345678-1234-1234-1234-123456789abc/"
        "resourceGroups/gludd-models-eastus/providers/Microsoft.Compute/"
        "virtualMachineScaleSets/gludd-vllm-vmss"
    ),
    instance_id="3",
    image_urn="canonical:0001-com-ubuntu-server-jammy:22_04-lts:latest",
    model_name="Qwen/Qwen2.5-0.5B-Instruct",
    model_revision="e" * 40,
    vm_size="Standard_NC6s_v3",
)

_FREE_TIER_IDENTITY = CatalogFreeTierCandidateIdentity(
    platform="openrouter",
    model_id="qwen/qwen3-coder:free",
    catalog_version="2026.09.01",
    catalog_payload_sha256="f" * 64,
)


@dataclass(frozen=True)
class _FakeBackend:
    candidate_identity: ModelCandidateIdentity
    response: str = "proposal:ok"
    failure: Exception | None = None
    calls: list[tuple[str, int, float]] | None = None

    def generate(
        self,
        request: str,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        if self.calls is not None:
            self.calls.append((request, max_output_tokens, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        return f"{self.response}:{request}"


class _FakeRegistry:
    def __init__(self, *backends: CandidateBackend[str, str]) -> None:
        self._backends = backends

    def discover(self) -> tuple[CandidateBackend[str, str], ...]:
        return self._backends


def _budget() -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=4,
        max_input_tokens=2_000,
        max_output_tokens=1_000,
        max_total_tokens=4_000,
        max_cost_microusd=50_000,
        timeout_seconds=30.0,
    )


def test_vm_vmss_identity_is_typed_secret_free_and_stable() -> None:
    identity = _VM_IDENTITY

    assert identity.provider is ModelCandidateProvider.AZURE_VM_VMSS
    assert len(identity.identity_digest) == 64
    assert identity.identity_digest == _VM_IDENTITY.identity_digest
    assert "cloudapp.azure.com" not in identity.identity_digest
    assert "credential" not in repr(identity).casefold()


def test_vm_vmss_evidence_identity_is_stable_across_instance_redeployment() -> None:
    original = _VM_IDENTITY
    redeployed = AzureVmVmssCandidateIdentity(
        endpoint="https://gludd-vllm-vm-new.eastus.cloudapp.azure.com",
        resource_id=original.resource_id.replace("gludd-vllm-vm", "gludd-vllm-vm-new"),
        instance_id="1",
        image_urn=original.image_urn,
        model_name=original.model_name,
        model_revision=original.model_revision,
        vm_size=original.vm_size,
    )

    assert redeployed.identity_digest != original.identity_digest
    assert redeployed.evidence_identity_digest == original.evidence_identity_digest


def test_vm_vmss_identity_rejects_noncanonical_input() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AzureVmVmssCandidateIdentity(
            endpoint="http://gludd-vllm-vm.eastus.cloudapp.azure.com",
            resource_id=_VM_IDENTITY.resource_id,
            instance_id="0",
            image_urn=_VM_IDENTITY.image_urn,
            model_name=_VM_IDENTITY.model_name,
            model_revision=_VM_IDENTITY.model_revision,
            vm_size=_VM_IDENTITY.vm_size,
        )

    with pytest.raises(ValueError, match="resource_id"):
        AzureVmVmssCandidateIdentity(
            endpoint=_VM_IDENTITY.endpoint,
            resource_id="/subscriptions/wrong",
            instance_id="0",
            image_urn=_VM_IDENTITY.image_urn,
            model_name=_VM_IDENTITY.model_name,
            model_revision=_VM_IDENTITY.model_revision,
            vm_size=_VM_IDENTITY.vm_size,
        )

    with pytest.raises(ValueError, match="image_urn"):
        AzureVmVmssCandidateIdentity(
            endpoint=_VM_IDENTITY.endpoint,
            resource_id=_VM_IDENTITY.resource_id,
            instance_id="0",
            image_urn="bad-urn",
            model_name=_VM_IDENTITY.model_name,
            model_revision=_VM_IDENTITY.model_revision,
            vm_size=_VM_IDENTITY.vm_size,
        )

    with pytest.raises(ValueError, match="model_revision"):
        AzureVmVmssCandidateIdentity(
            endpoint=_VM_IDENTITY.endpoint,
            resource_id=_VM_IDENTITY.resource_id,
            instance_id="0",
            image_urn=_VM_IDENTITY.image_urn,
            model_name=_VM_IDENTITY.model_name,
            model_revision="main",
            vm_size=_VM_IDENTITY.vm_size,
        )

    with pytest.raises(ValueError, match="vm_size"):
        AzureVmVmssCandidateIdentity(
            endpoint=_VM_IDENTITY.endpoint,
            resource_id=_VM_IDENTITY.resource_id,
            instance_id="0",
            image_urn=_VM_IDENTITY.image_urn,
            model_name=_VM_IDENTITY.model_name,
            model_revision=_VM_IDENTITY.model_revision,
            vm_size="",
        )


def test_workload_candidate_wraps_backend_identity() -> None:
    backend = _FakeBackend(_LOCAL_IDENTITY)
    candidate = WorkloadCandidate(backend.candidate_identity, backend)

    assert candidate.identity is _LOCAL_IDENTITY
    assert candidate.backend is backend


def test_workload_execution_exposes_outcome() -> None:
    execution = WorkloadExecution(_LOCAL_IDENTITY, "task", "proposal:ok", True)

    assert execution.candidate_identity is _LOCAL_IDENTITY
    assert execution.request == "task"
    assert execution.response == "proposal:ok"
    assert execution.passed is True


def test_workload_discovers_candidates_from_all_registries() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    foundry_backend = _FakeBackend(_FOUNDRY_IDENTITY)
    registry = _FakeRegistry(local_backend, foundry_backend)
    workload = SelfImprovementWorkload([registry])

    candidates = workload.discover()

    assert len(candidates) == 2
    assert candidates[0].identity is _LOCAL_IDENTITY
    assert candidates[1].identity is _FOUNDRY_IDENTITY


def test_workload_predict_selects_candidates_for_task() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    free_backend = _FakeBackend(_FREE_TIER_IDENTITY)
    registry = _FakeRegistry(local_backend, free_backend)
    workload = SelfImprovementWorkload([registry])

    candidates = workload.discover()
    predicted = workload.predict("fix bug", candidates)

    assert len(predicted) == 2


def test_workload_executes_local_candidate_without_azure_opt_in() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    registry = _FakeRegistry(local_backend)
    workload = SelfImprovementWorkload([registry], budget=_budget())

    candidates = workload.discover()
    executions = workload.execute("task-one", candidates)

    assert len(executions) == 1
    assert executions[0].response == "proposal:ok:task-one"
    assert executions[0].passed is True


def test_workload_blocks_azure_candidate_without_opt_in() -> None:
    vm_backend = _FakeBackend(_VM_IDENTITY)
    registry = _FakeRegistry(vm_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=False,
        external_enabled=False,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 1
    assert executions[0].response == ""
    assert executions[0].passed is False


def test_workload_allows_azure_candidate_with_opt_in() -> None:
    vm_backend = _FakeBackend(_VM_IDENTITY)
    registry = _FakeRegistry(vm_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 1
    assert executions[0].response == "proposal:ok:task"
    assert executions[0].passed is True


def test_workload_allows_external_candidate_with_opt_in() -> None:
    free_backend = _FakeBackend(_FREE_TIER_IDENTITY)
    registry = _FakeRegistry(free_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=False,
        external_enabled=True,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 1
    assert executions[0].passed is True


def test_workload_evaluates_executions_into_summary() -> None:
    executions = (
        WorkloadExecution(_LOCAL_IDENTITY, "t", "proposal:ok", True),
        WorkloadExecution(_VM_IDENTITY, "t", "", False),
    )
    workload = SelfImprovementWorkload([])
    evaluation = workload.evaluate(executions)

    assert evaluation["total"] == 2
    assert evaluation["passed"] == 1
    assert evaluation["pass_rate"] == 0.5


def test_workload_learn_records_evaluation() -> None:
    evaluation = {"total": 2, "passed": 1, "pass_rate": 0.5}
    workload = SelfImprovementWorkload([])
    learned = workload.learn(evaluation)

    assert len(learned) == 1
    assert learned[0]["status"] == "recorded"
    assert learned[0]["evaluation"] is evaluation


def test_workload_runs_full_pipeline_across_all_backends() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    foundry_backend = _FakeBackend(_FOUNDRY_IDENTITY)
    containerapp_backend = _FakeBackend(_CONTAINERAPP_IDENTITY)
    vm_backend = _FakeBackend(_VM_IDENTITY)
    vmss_backend = _FakeBackend(_VMSS_IDENTITY)
    free_backend = _FakeBackend(_FREE_TIER_IDENTITY)

    registry = _FakeRegistry(
        local_backend,
        foundry_backend,
        containerapp_backend,
        vm_backend,
        vmss_backend,
        free_backend,
    )
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        external_enabled=True,
    )

    result = workload.run("cross-backend task")

    assert isinstance(result, WorkloadResult)
    assert result.phase == "completed"
    assert len(result.candidates) == 6
    assert len(result.executions) == 6
    assert result.evaluation["total"] == 6
    assert result.evaluation["passed"] == 6
    assert result.evaluation["pass_rate"] == 1.0
    assert len(result.learned_outcomes) == 1
    providers = {c.identity.provider for c in result.candidates}
    assert providers == {
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_FOUNDRY,
        ModelCandidateProvider.AZURE_CONTAINER_APP,
        ModelCandidateProvider.AZURE_VM_VMSS,
        ModelCandidateProvider.CATALOG_FREE_TIER,
    }


def test_workload_censors_backend_failure_without_leaking_secrets() -> None:
    secret = "AZURE-VM-SECRET-DO-NOT-LEAK"
    vm_backend = _FakeBackend(
        _VM_IDENTITY,
        failure=RuntimeError(secret),
    )
    registry = _FakeRegistry(vm_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 1
    assert executions[0].response == ""
    assert executions[0].passed is False
    assert secret not in repr(executions)
    assert secret not in str(executions)


def test_workload_propagates_typed_backend_failure() -> None:
    failure = BackendInfrastructureError(BackendFailure.TIMEOUT)
    vm_backend = _FakeBackend(_VM_IDENTITY, failure=failure)
    registry = _FakeRegistry(vm_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 1
    assert executions[0].passed is False


def test_workload_custom_evaluator_and_learner_are_used() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    registry = _FakeRegistry(local_backend)
    calls: list[str] = []

    def evaluator(executions: Sequence[WorkloadExecution]) -> dict[str, Any]:
        calls.append("evaluate")
        return {"custom": True}

    def learner(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        calls.append("learn")
        return [{"learned": evaluation}]

    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        evaluator=evaluator,
        learner=learner,
    )

    result = workload.run("task")

    assert result.evaluation == {"custom": True}
    assert result.learned_outcomes == [{"learned": {"custom": True}}]
    assert calls == ["evaluate", "learn"]


def test_workload_requires_callable_evaluator_and_learner() -> None:
    with pytest.raises(ValueError, match="evaluator"):
        SelfImprovementWorkload([], evaluator="not-callable")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="learner"):
        SelfImprovementWorkload([], learner="not-callable")  # type: ignore[arg-type]


def test_workload_rejects_non_registry_sequence() -> None:
    with pytest.raises(ValueError, match="registries"):
        SelfImprovementWorkload("not-a-sequence")  # type: ignore[arg-type]


class _SleepingFakeBackend:
    candidate_identity: ModelCandidateIdentity

    def __init__(self, identity: ModelCandidateIdentity, delay: float = 0.1) -> None:
        self.candidate_identity = identity
        self.delay = delay

    def generate(
        self,
        request: str,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> str:
        import time

        time.sleep(self.delay)
        return f"proposal:ok:{request}"


def test_workload_executes_candidates_concurrently() -> None:
    delay = 0.15
    local_backend = _SleepingFakeBackend(_LOCAL_IDENTITY, delay=delay)
    vm_backend = _SleepingFakeBackend(_VM_IDENTITY, delay=delay)
    containerapp_backend = _SleepingFakeBackend(_CONTAINERAPP_IDENTITY, delay=delay)
    free_backend = _SleepingFakeBackend(_FREE_TIER_IDENTITY, delay=delay)
    registry = _FakeRegistry(local_backend, vm_backend, containerapp_backend, free_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        external_enabled=True,
    )

    import time

    start = time.monotonic()
    executions = workload.execute("concurrent task", workload.discover())
    elapsed = time.monotonic() - start

    assert len(executions) == 4
    # Concurrent execution of four ~0.15s tasks should take < 0.4s total,
    # whereas sequential execution would take ~0.6s.
    assert elapsed < 0.4
    assert all(execution.passed for execution in executions)
    assert {execution.response for execution in executions} == {"proposal:ok:concurrent task"}


def test_workload_protocol_envelope_is_consistent_across_backends() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    vm_backend = _FakeBackend(_VM_IDENTITY)
    free_backend = _FakeBackend(_FREE_TIER_IDENTITY)
    registry = _FakeRegistry(local_backend, vm_backend, free_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        external_enabled=True,
    )

    executions = workload.execute("envelope task", workload.discover())

    assert len(executions) == 3
    for execution in executions:
        assert execution.envelope is not None
        assert execution.envelope.protocol == "gludd-self-improve-workload-v1"
        assert execution.envelope.request == "envelope task"
        assert execution.envelope.response.startswith("proposal:ok")
        assert isinstance(execution.envelope.metadata, dict)
        assert "provider" in execution.envelope.metadata
        assert "evidence_identity_digest" in execution.envelope.metadata


def test_workload_model_and_infrastructure_evidence_are_separated() -> None:
    vm_backend = _FakeBackend(_VM_IDENTITY)
    registry = _FakeRegistry(vm_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
    )

    executions = workload.execute("evidence task", workload.discover())

    assert len(executions) == 1
    execution = executions[0]
    assert execution.candidate_identity is _VM_IDENTITY
    assert execution.evidence_identity_digest == _VM_IDENTITY.evidence_identity_digest
    assert execution.envelope is not None
    assert execution.envelope.metadata["evidence_identity_digest"] == _VM_IDENTITY.evidence_identity_digest


def test_workload_evidence_identity_is_stable_across_vm_redeployment() -> None:
    redeployed_identity = AzureVmVmssCandidateIdentity(
        endpoint="https://gludd-vllm-vm-new.eastus.cloudapp.azure.com",
        resource_id=_VM_IDENTITY.resource_id.replace("gludd-vllm-vm", "gludd-vllm-vm-new"),
        instance_id="1",
        image_urn=_VM_IDENTITY.image_urn,
        model_name=_VM_IDENTITY.model_name,
        model_revision=_VM_IDENTITY.model_revision,
        vm_size=_VM_IDENTITY.vm_size,
    )
    original_backend = _FakeBackend(_VM_IDENTITY)
    redeployed_backend = _FakeBackend(redeployed_identity)
    registry = _FakeRegistry(original_backend, redeployed_backend)
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
    )

    executions = workload.execute("task", workload.discover())

    assert len(executions) == 2
    assert executions[0].candidate_identity is _VM_IDENTITY
    assert executions[1].candidate_identity is redeployed_identity
    assert (
        executions[0].evidence_identity_digest
        == executions[1].evidence_identity_digest
        == _VM_IDENTITY.evidence_identity_digest
    )


class _FakeTodoStore(WorkloadTodoStore):
    def __init__(self) -> None:
        self.claims: list[tuple[str, str]] = []
        self.releases: list[str] = []
        self._counter = 0

    def claim(self, identity: ModelCandidateIdentity) -> str:
        self._counter += 1
        claim_id = f"claim-{identity.provider.value}-{self._counter}"
        self.claims.append((claim_id, identity.identity_digest))
        return claim_id

    def release(self, claim_id: str) -> None:
        self.releases.append(claim_id)


def test_workload_claims_and_releases_todos_atomically() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    vm_backend = _FakeBackend(_VM_IDENTITY)
    registry = _FakeRegistry(local_backend, vm_backend)
    todo_store = _FakeTodoStore()
    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        todo_store=todo_store,
    )

    executions = workload.execute("todo task", workload.discover())

    assert len(executions) == 2
    assert len(todo_store.claims) == 2
    assert len(todo_store.releases) == 2
    # Every claim must have a matching release.
    assert {claim_id for claim_id, _ in todo_store.claims} == set(todo_store.releases)
    # Claims must happen before execution and releases after.
    for execution in executions:
        assert execution.claim_id is not None
        assert execution.claim_id in todo_store.releases


def test_workload_releases_todo_and_calls_cleanup_on_failure() -> None:
    secret = "AZURE-SECRET-DO-NOT-LEAK"
    vm_backend = _FakeBackend(_VM_IDENTITY, failure=RuntimeError(secret))
    registry = _FakeRegistry(vm_backend)
    todo_store = _FakeTodoStore()
    cleanup_calls: list[WorkloadExecution] = []

    def cleanup(execution: WorkloadExecution) -> None:
        cleanup_calls.append(execution)

    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        todo_store=todo_store,
        cleanup_callbacks=[cleanup],
    )

    executions = workload.execute("failing task", workload.discover())

    assert len(executions) == 1
    assert executions[0].passed is False
    assert executions[0].claim_id is not None
    assert len(todo_store.claims) == 1
    assert len(todo_store.releases) == 1
    assert todo_store.claims[0][0] == todo_store.releases[0]
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0] is executions[0]
    assert secret not in repr(cleanup_calls)
    assert secret not in str(cleanup_calls)


def test_workload_cleanup_callbacks_run_for_every_execution() -> None:
    local_backend = _FakeBackend(_LOCAL_IDENTITY)
    vm_backend = _FakeBackend(_VM_IDENTITY)
    free_backend = _FakeBackend(_FREE_TIER_IDENTITY)
    registry = _FakeRegistry(local_backend, vm_backend, free_backend)
    cleanup_calls: list[ModelCandidateProvider] = []

    def cleanup(execution: WorkloadExecution) -> None:
        cleanup_calls.append(execution.candidate_identity.provider)

    workload = SelfImprovementWorkload(
        [registry],
        budget=_budget(),
        azure_enabled=True,
        external_enabled=True,
        cleanup_callbacks=[cleanup],
    )

    executions = workload.execute("cleanup task", workload.discover())

    assert len(executions) == 3
    assert len(cleanup_calls) == 3
    assert set(cleanup_calls) == {
        ModelCandidateProvider.LOCAL_GGUF,
        ModelCandidateProvider.AZURE_VM_VMSS,
        ModelCandidateProvider.CATALOG_FREE_TIER,
    }


def test_workload_rejects_non_callable_cleanup_callbacks() -> None:
    with pytest.raises(ValueError, match="cleanup"):
        SelfImprovementWorkload(
            [],
            cleanup_callbacks=["not-callable"],  # type: ignore[list-item]
        )
