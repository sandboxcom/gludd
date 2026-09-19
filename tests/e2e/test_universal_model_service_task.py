"""Hermetic universal-task E2E from accelerator evidence to gated result."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from general_ludd.execution.model_service_planner import (
    ModelServiceInventorySnapshot,
    UniversalModelServicePlanner,
)
from general_ludd.execution.universal_task import (
    AdapterDecision,
    CandidateAssessment,
    ExecutionTarget,
    TaskStatus,
    UniversalTaskExecutor,
    UniversalTaskRequest,
)
from general_ludd.hardware.accelerator_topology import (
    AcceleratorCost,
    AcceleratorTopology,
    DistributionMode,
    PartitioningMode,
    RunnerCapabilities,
    TopologyConstraints,
)
from general_ludd.hardware.accelerator_types import (
    AcceleratorKind,
    AcceleratorLocation,
    AcceleratorResource,
)
from general_ludd.hardware.model_runner_launch import vllm_launch_profile
from general_ludd.hardware.model_service_rightsizing import (
    InferenceWorkloadDemand,
    ModelVariantEvidence,
    RunnerSizingEvidence,
)
from general_ludd.scheduling.scheduler import WorkItem

_GIB = 1024**3


def _snapshot() -> ModelServiceInventorySnapshot:
    resource = AcceleratorResource(
        kind=AcceleratorKind.GPU,
        location=AcceleratorLocation.CLOUD,
        backend="cuda",
        model="observed-azure-vm-gpu",
        vendor="nvidia",
        resource_key="azure:e2e-pool",
        total_count=4,
        available_count=4,
        memory_gb=24.0,
        source="provider-inventory",
    )
    pool = AcceleratorTopology(
        resource=resource,
        provider="azure",
        region="eastus",
        zone="observed-zone",
        host_count=1,
        devices_per_host=4,
        max_devices_per_workload=4,
        intra_host_interconnect="observed-nvlink",
        intra_host_interconnect_group_size=4,
        cross_host_interconnect=None,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        memory_isolated=True,
        cost=AcceleratorCost(100_000, 1),
        facts_attested=True,
    )
    return ModelServiceInventorySnapshot(
        variants=(
            ModelVariantEvidence(
                variant_id="model:revision:q4",
                model_id="owner/model@immutable-revision",
                architecture="decoder-transformer",
                quantization="q4_k_m",
                weight_bytes=36 * _GIB,
                runtime_overhead_bytes=_GIB,
                kv_cache_bytes_per_token=0,
                max_context_tokens=16_384,
                tensor_parallel_divisor=4,
                allowed_device_kinds=frozenset({"gpu"}),
                supported_runner_ids=frozenset({"vllm-e2e"}),
                quality_millis=930,
                facts_attested=True,
            ),
        ),
        runner_sizing=(
            RunnerSizingEvidence(
                runner_id="vllm-e2e",
                max_batch_size_per_replica=2,
                max_context_tokens=16_384,
                max_output_tokens=4_096,
                max_data_parallel_replicas=2,
                observed_p95_latency_millis=1_000,
                facts_attested=True,
            ),
        ),
        pools=(pool,),
        runner_capabilities=(
            RunnerCapabilities(
                runner_id="vllm-e2e",
                supported_device_kinds=frozenset({"gpu"}),
                supported_backends=frozenset({"cuda"}),
                supported_partitioning=frozenset(
                    {PartitioningMode.WHOLE_DEVICE}
                ),
                distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
                supported_model_parallel_sizes=frozenset({1, 2}),
                supported_tensor_parallel_sizes=frozenset({1, 2}),
                supported_pipeline_parallel_sizes=frozenset({1}),
                tensor_parallel_interconnects=frozenset({"observed-nvlink"}),
                pipeline_parallel_interconnects=frozenset(),
                cross_host_interconnects=frozenset(),
                max_hosts=1,
                max_data_parallel_replicas=2,
            ),
        ),
        constraints=TopologyConstraints(
            allowed_providers=frozenset({"azure"}),
            allowed_regions=frozenset({"eastus"}),
            max_hourly_cost_microusd=1_000_000,
            max_total_devices=4,
            memory_reserve_millis=100,
        ),
        launch_profiles=(
            vllm_launch_profile(
                runner_id="vllm-e2e",
                source_revision="vllm-e2e-observed",
                facts_attested=True,
            ),
        ),
    )


class _Gateway:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def call_model(
        self,
        profile_id: str,
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> SimpleNamespace:
        assert profile_id == "gateway-e2e"
        assert messages[0]["role"] == "user"
        self.options = kwargs
        return SimpleNamespace(
            content='{"artifact": "validated"}',
            cost_estimate=0.25,
        )


class _Scheduler:
    def __init__(self) -> None:
        self.resources: frozenset[str] = frozenset()

    def plan(self, items: list[WorkItem]) -> list[list[str]]:
        self.resources = items[0].resources
        return [[items[0].id]]


class _RouteInventory:
    def discover_hardware(self) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(
                sku="observed-azure-vm-gpu",
                provider="azure",
                approved=True,
            ),
        )


class _CapabilityAdapter:
    def __init__(self, capability: str) -> None:
        self.capability = capability

    def build_messages(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> list[dict[str, str]]:
        del target
        return [{"role": "user", "content": request.instruction}]

    def preflight(
        self,
        request: UniversalTaskRequest,
        target: ExecutionTarget,
    ) -> AdapterDecision:
        del request, target
        return AdapterDecision(True, evidence={"domain_preflight": "passed"})

    def parse_candidate(self, content: str) -> object:
        return content

    def assess_candidate(
        self,
        request: UniversalTaskRequest,
        candidate: object,
        tool_runner: object | None,
    ) -> CandidateAssessment:
        del request, candidate, tool_runner
        return CandidateAssessment(True, evidence={"domain_gate": "passed"})


@pytest.mark.parametrize(
    "capability",
    ("chemistry.design", "embedded.firmware", "self_improve.proposal"),
)
def test_universal_task_e2e_uses_one_right_sized_service_plan(
    capability: str,
) -> None:
    snapshot = _snapshot()
    gateway = _Gateway()
    scheduler = _Scheduler()
    service_planner = UniversalModelServicePlanner(
        lambda _request, _target: snapshot
    )
    target = ExecutionTarget(
        profile_id="gateway-e2e",
        provider="azure",
        accelerator_sku="observed-azure-vm-gpu",
        capabilities=frozenset({capability}),
        allowed_data_classifications=frozenset({"public"}),
        estimated_cost_usd=0.25,
        healthy=True,
        health_evidence="live-route-health",
        capability_evidence="domain-quality-evidence",
        cost_evidence="provider-price-evidence",
        privacy_evidence="public-data-policy",
        offline=False,
        model_runner_id="vllm-e2e",
    )
    executor = UniversalTaskExecutor(
        gateway=gateway,
        scheduler=scheduler,
        accelerator_planner=_RouteInventory(),
        target_source=lambda: (target,),
        model_service_planner=service_planner,
    )
    request = UniversalTaskRequest(
        task_id=f"task:{capability}",
        capability=capability,
        instruction="Produce one capability-owned, validated artifact.",
        budget_usd=1.0,
        resources=frozenset({"workspace:e2e"}),
        model_workload=InferenceWorkloadDemand(
            workload_id=f"workload:{capability}",
            input_tokens=4_096,
            output_tokens=1_024,
            concurrent_sequences=3,
            minimum_quality_millis=900,
            max_p95_latency_millis=2_000,
        ),
    )

    result = executor.execute(request, _CapabilityAdapter(capability))

    assert result.status is TaskStatus.SUCCEEDED
    payload = gateway.options["model_service_plan"]
    assert isinstance(payload, dict)
    selection = payload["selection"]
    launch = payload["launch"]
    assert isinstance(selection, dict)
    assert isinstance(launch, dict)
    assert selection["topology"]["model_parallel_devices"] == 2  # type: ignore[index]
    assert selection["topology"]["data_parallel_replicas"] == 2  # type: ignore[index]
    assert launch["replica_count"] == 2
    assert launch["devices_per_replica"] == 2
    assert launch["command"][-6:] == [
        "--max-num-seqs",
        "2",
        "--tensor-parallel-size",
        "2",
        "--pipeline-parallel-size",
        "1",
    ]
    assert scheduler.resources == frozenset(
        {
            "workspace:e2e",
            "accelerator:observed-azure-vm-gpu",
            "accelerator-resource:azure:e2e-pool",
            "model-runner:vllm-e2e",
        }
    )
    assert result.evidence["model_service"] == payload
    assert result.evidence["domain_gate"] == "passed"
