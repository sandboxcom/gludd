"""Provider-neutral model-runner launch rendering tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import cast

import pytest

from general_ludd.hardware.accelerator_topology import (
    DistributionMode,
    ModelRunnerDemand,
    PartitioningMode,
    TopologyPlan,
)
from general_ludd.hardware.model_runner_launch import (
    LaunchTarget,
    LaunchValue,
    RunnerLaunchBinding,
    RunnerLaunchError,
    RunnerLaunchPlan,
    RunnerLaunchProfile,
    llama_cpp_launch_profile,
    ollama_launch_profile,
    render_model_runner_launch,
    vllm_launch_profile,
)
from general_ludd.hardware.model_service_rightsizing import DerivedModelDemand
from general_ludd.hardware.model_service_selection import ModelServiceSelection

_GIB = 1024**3


def test_focused_coverage_profile_measures_launch_contract() -> None:
    profile = Path("config/coverage_model_runner_launch.ini").read_text()

    assert "*/src/general_ludd/hardware/model_runner_launch.py" in profile


def _selection(
    *,
    runner_id: str = "vllm-observed",
    distribution: DistributionMode = DistributionMode.EXPLICIT_PARALLEL,
    model_parallel_devices: int = 8,
    tensor_parallel_size: int | None = 4,
    pipeline_parallel_size: int | None = 2,
    device_kind: str = "gpu",
) -> ModelServiceSelection:
    topology_demand = ModelRunnerDemand(
        model_id="owner/model@0123456789abcdef",
        allowed_device_kinds=frozenset({device_kind}),
        weight_bytes=16 * _GIB,
        runtime_overhead_bytes=_GIB,
        kv_cache_bytes_per_token=0,
        context_tokens=8_192,
        concurrent_sequences=4,
        data_parallel_replicas=3,
        tensor_parallel_divisor=8,
        allow_shared_accelerator=False,
    )
    demand = DerivedModelDemand(
        variant_id="model:revision:q4",
        runner_id=runner_id,
        architecture="decoder-transformer",
        quantization="q4_k_m",
        quality_millis=925,
        context_tokens=8_192,
        output_tokens=2_048,
        batch_size_per_replica=4,
        data_parallel_replicas=3,
        topology_demand=topology_demand,
    )
    topology = TopologyPlan(
        resource_key="dynamic:pool:0",
        provider="azure",
        region="west",
        device_kind=device_kind,
        device_vendor="observed-vendor",
        device_model="observed-model",
        runner_id=runner_id,
        partitioning=PartitioningMode.WHOLE_DEVICE,
        distribution_mode=distribution,
        model_parallel_devices=model_parallel_devices,
        data_parallel_replicas=3,
        tensor_parallel_size=tensor_parallel_size,
        pipeline_parallel_size=pipeline_parallel_size,
        host_count=1,
        total_devices=model_parallel_devices * 3,
        per_device_required_bytes=3 * _GIB,
        per_device_usable_bytes=4 * _GIB,
        billable_allocation_units=model_parallel_devices * 3,
        estimated_hourly_cost_microusd=2_400_000,
    )
    return ModelServiceSelection(demand=demand, topology=topology)


def _vllm_plan() -> RunnerLaunchPlan:
    return render_model_runner_launch(
        selection=_selection(),
        profile=vllm_launch_profile(
            runner_id="vllm-observed",
            source_revision="observed",
            facts_attested=True,
        ),
    )


def test_vllm_renders_only_explicit_attested_parallelism() -> None:
    plan = render_model_runner_launch(
        selection=_selection(),
        profile=vllm_launch_profile(
            runner_id="vllm-observed",
            source_revision="vllm-cli-observed-2026-09-19",
            facts_attested=True,
        ),
    )

    assert plan.command == (
        "vllm",
        "serve",
        "owner/model@0123456789abcdef",
        "--max-model-len",
        "8192",
        "--max-num-seqs",
        "4",
        "--tensor-parallel-size",
        "4",
        "--pipeline-parallel-size",
        "2",
    )
    assert plan.environment == ()
    assert plan.request_options == (("max_tokens", 2_048),)
    assert plan.replica_count == 3
    assert plan.devices_per_replica == 8


def test_llama_cpp_renders_runner_managed_multi_device_contract() -> None:
    selection = _selection(
        runner_id="llamacpp-observed",
        distribution=DistributionMode.RUNNER_MANAGED,
        model_parallel_devices=2,
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
    )

    plan = render_model_runner_launch(
        selection=selection,
        profile=llama_cpp_launch_profile(
            runner_id="llamacpp-observed",
            source_revision="llama-server-observed-2026-09-19",
            facts_attested=True,
        ),
    )

    assert plan.command == (
        "llama-server",
        "--n-gpu-layers",
        "all",
        "--split-mode",
        "layer",
        "--model",
        "owner/model@0123456789abcdef",
        "--ctx-size",
        "8192",
        "--parallel",
        "4",
        "--n-predict",
        "2048",
    )
    assert plan.devices_per_replica == 2


def test_ollama_renders_server_environment_and_request_options() -> None:
    selection = _selection(
        runner_id="ollama-observed",
        distribution=DistributionMode.RUNNER_MANAGED,
        model_parallel_devices=4,
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
    )

    plan = render_model_runner_launch(
        selection=selection,
        profile=ollama_launch_profile(
            runner_id="ollama-observed",
            source_revision="ollama-docs-observed-2026-09-19",
            facts_attested=True,
        ),
    )

    assert plan.command == ("ollama", "serve")
    assert plan.environment == (
        ("OLLAMA_CONTEXT_LENGTH", "8192"),
        ("OLLAMA_NUM_PARALLEL", "4"),
    )
    assert plan.request_options == (
        ("model", "owner/model@0123456789abcdef"),
        ("num_ctx", 8_192),
        ("num_predict", 2_048),
    )
    assert plan.devices_per_replica == 4


def test_custom_profile_supports_unknown_future_runner_without_core_branch() -> None:
    selection = _selection(
        runner_id="future-runner",
        distribution=DistributionMode.RUNNER_MANAGED,
        model_parallel_devices=2,
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
        device_kind="future-accelerator",
    )
    profile = RunnerLaunchProfile(
        runner_id="future-runner",
        adapter_id="future-compatible-adapter",
        source_revision="future-runner-observed-v1",
        executable=("future-runner", "serve"),
        fixed_arguments=("--safe-mode",),
        bindings=(
            RunnerLaunchBinding(
                source=LaunchValue.MODEL_ID,
                target=LaunchTarget.POSITIONAL_ARGUMENT,
            ),
            RunnerLaunchBinding(
                source=LaunchValue.MODEL_PARALLEL_DEVICES,
                target=LaunchTarget.ARGUMENT,
                name="--mesh-size",
            ),
            RunnerLaunchBinding(
                source=LaunchValue.DEVICE_KIND,
                target=LaunchTarget.ENVIRONMENT,
                name="FUTURE_DEVICE_KIND",
            ),
            RunnerLaunchBinding(
                source=LaunchValue.OUTPUT_TOKENS,
                target=LaunchTarget.REQUEST_OPTION,
                name="max_output",
            ),
        ),
        distribution_mode=DistributionMode.RUNNER_MANAGED,
        facts_attested=True,
    )

    plan = render_model_runner_launch(selection=selection, profile=profile)

    assert plan.command == (
        "future-runner",
        "serve",
        "--safe-mode",
        "owner/model@0123456789abcdef",
        "--mesh-size",
        "2",
    )
    assert plan.environment == (("FUTURE_DEVICE_KIND", "future-accelerator"),)
    assert plan.request_options == (("max_output", 2_048),)


def test_plan_serialization_is_exact_and_frozen() -> None:
    plan = render_model_runner_launch(
        selection=_selection(),
        profile=vllm_launch_profile(
            runner_id="vllm-observed",
            source_revision="vllm-cli-observed-2026-09-19",
            facts_attested=True,
        ),
    )

    assert plan.to_dict() == {
        "schema_version": 1,
        "runner_id": "vllm-observed",
        "adapter_id": "vllm-cli",
        "source_revision": "vllm-cli-observed-2026-09-19",
        "variant_id": "model:revision:q4",
        "model_id": "owner/model@0123456789abcdef",
        "quantization": "q4_k_m",
        "distribution_mode": "explicit_parallel",
        "command": list(plan.command),
        "environment": {},
        "request_options": {"max_tokens": 2_048},
        "replica_count": 3,
        "devices_per_replica": 8,
    }
    with pytest.raises(FrozenInstanceError):
        plan.command = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("profile", "reason"),
    [
        (
            vllm_launch_profile(
                runner_id="vllm-observed",
                source_revision="observed",
                facts_attested=False,
            ),
            "launch_profile_unattested",
        ),
        (
            vllm_launch_profile(
                runner_id="other-runner",
                source_revision="observed",
                facts_attested=True,
            ),
            "runner_identity_mismatch",
        ),
        (
            replace(
                vllm_launch_profile(
                    runner_id="vllm-observed",
                    source_revision="observed",
                    facts_attested=True,
                ),
                distribution_mode=DistributionMode.RUNNER_MANAGED,
            ),
            "distribution_mode_mismatch",
        ),
    ],
)
def test_rendering_fails_closed_on_unattested_or_mismatched_profile(
    profile: RunnerLaunchProfile,
    reason: str,
) -> None:
    with pytest.raises(RunnerLaunchError) as caught:
        render_model_runner_launch(selection=_selection(), profile=profile)

    assert caught.value.reason_code == reason
    assert str(caught.value) == f"runner launch rendering refused: {reason}"


def test_missing_topology_value_fails_closed() -> None:
    selection = _selection(
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
    )

    with pytest.raises(RunnerLaunchError) as caught:
        render_model_runner_launch(
            selection=selection,
            profile=vllm_launch_profile(
                runner_id="vllm-observed",
                source_revision="observed",
                facts_attested=True,
            ),
        )

    assert caught.value.reason_code == "launch_value_unavailable"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RunnerLaunchBinding(
            source=cast(LaunchValue, "bad"),
            target=LaunchTarget.ARGUMENT,
            name="--flag",
        ),
        lambda: RunnerLaunchBinding(
            source=LaunchValue.MODEL_ID,
            target=cast(LaunchTarget, "bad"),
            name="--flag",
        ),
        lambda: RunnerLaunchBinding(
            source=LaunchValue.MODEL_ID,
            target=LaunchTarget.POSITIONAL_ARGUMENT,
            name="--model",
        ),
        lambda: RunnerLaunchBinding(
            source=LaunchValue.MODEL_ID,
            target=LaunchTarget.ARGUMENT,
        ),
        lambda: RunnerLaunchProfile(
            runner_id="runner",
            adapter_id="adapter",
            source_revision="revision",
            executable=cast(tuple[str, ...], []),
            fixed_arguments=(),
            bindings=(),
            distribution_mode=DistributionMode.RUNNER_MANAGED,
            facts_attested=True,
        ),
        lambda: RunnerLaunchProfile(
            runner_id="runner",
            adapter_id="adapter",
            source_revision="revision",
            executable=("runner",),
            fixed_arguments=(),
            bindings=cast(tuple[RunnerLaunchBinding, ...], []),
            distribution_mode=DistributionMode.RUNNER_MANAGED,
            facts_attested=True,
        ),
        lambda: replace(
            ollama_launch_profile(
                runner_id="runner",
                source_revision="revision",
                facts_attested=True,
            ),
            bindings=(
                RunnerLaunchBinding(
                    source=LaunchValue.MODEL_ID,
                    target=LaunchTarget.REQUEST_OPTION,
                    name="model",
                ),
                RunnerLaunchBinding(
                    source=LaunchValue.QUANTIZATION,
                    target=LaunchTarget.REQUEST_OPTION,
                    name="model",
                ),
            ),
        ),
    ],
)
def test_launch_contracts_reject_mutable_ambiguous_or_invalid_shapes(
    factory: object,
) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "call",
    [
        lambda: render_model_runner_launch(
            selection=cast(ModelServiceSelection, object()),
            profile=vllm_launch_profile(
                runner_id="vllm-observed",
                source_revision="observed",
                facts_attested=True,
            ),
        ),
        lambda: render_model_runner_launch(
            selection=_selection(),
            profile=cast(RunnerLaunchProfile, object()),
        ),
    ],
)
def test_renderer_rejects_wrong_contract_types(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # type: ignore[operator]


def test_direct_launch_plan_rejects_duplicate_output_names() -> None:
    with pytest.raises(ValueError):
        RunnerLaunchPlan(
            runner_id="runner",
            adapter_id="adapter",
            source_revision="revision",
            variant_id="variant",
            model_id="model",
            quantization="q4",
            distribution_mode=DistributionMode.RUNNER_MANAGED,
            command=("runner",),
            environment=(("DUPLICATE", "1"), ("DUPLICATE", "2")),
            request_options=(),
            replica_count=1,
            devices_per_replica=1,
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RunnerLaunchBinding(
            LaunchValue.MODEL_ID,
            LaunchTarget.ARGUMENT,
            "model",
        ),
        lambda: RunnerLaunchBinding(
            LaunchValue.MODEL_ID,
            LaunchTarget.ENVIRONMENT,
            "lowercase",
        ),
        lambda: RunnerLaunchBinding(
            LaunchValue.MODEL_ID,
            LaunchTarget.REQUEST_OPTION,
            "bad option",
        ),
        lambda: replace(
            vllm_launch_profile(
                runner_id="runner",
                source_revision="revision",
                facts_attested=True,
            ),
            bindings=(
                RunnerLaunchBinding(
                    LaunchValue.MODEL_ID,
                    LaunchTarget.POSITIONAL_ARGUMENT,
                ),
            )
            * 257,
        ),
        lambda: replace(
            vllm_launch_profile(
                runner_id="runner",
                source_revision="revision",
                facts_attested=True,
            ),
            distribution_mode=cast(DistributionMode, "bad"),
        ),
        lambda: replace(
            vllm_launch_profile(
                runner_id="runner",
                source_revision="revision",
                facts_attested=True,
            ),
            facts_attested=1,
        ),
        lambda: replace(_vllm_plan(), runner_id=""),
        lambda: replace(
            _vllm_plan(),
            distribution_mode=cast(DistributionMode, "bad"),
        ),
        lambda: replace(_vllm_plan(), command=("bad\ncommand",)),
        lambda: replace(_vllm_plan(), command=("runner",) * 257),
        lambda: replace(
            _vllm_plan(),
            environment=cast(tuple[tuple[str, str], ...], []),
        ),
        lambda: replace(
            _vllm_plan(),
            request_options=cast(
                tuple[tuple[str, str | int], ...],
                (("only-name",),),
            ),
        ),
        lambda: replace(
            _vllm_plan(),
            request_options=cast(
                tuple[tuple[str, str | int], ...],
                (("bad-value", object()),),
            ),
        ),
        lambda: replace(_vllm_plan(), replica_count=True),
        lambda: replace(_vllm_plan(), devices_per_replica=0),
    ],
)
def test_profile_and_plan_boundaries_reject_unsafe_values(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]
