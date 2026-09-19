"""Render immutable model-service selections into attested runner contracts."""

from __future__ import annotations

from general_ludd.hardware.accelerator_topology import DistributionMode
from general_ludd.hardware.model_runner_launch_contracts import (
    LaunchTarget,
    LaunchValue,
    RunnerLaunchBinding,
    RunnerLaunchError,
    RunnerLaunchPlan,
    RunnerLaunchProfile,
)
from general_ludd.hardware.model_service_selection import ModelServiceSelection


def _launch_value(
    selection: ModelServiceSelection,
    source: LaunchValue,
) -> str | int:
    demand = selection.demand
    topology = selection.topology
    values: dict[LaunchValue, str | int | None] = {
        LaunchValue.MODEL_ID: demand.topology_demand.model_id,
        LaunchValue.ARCHITECTURE: demand.architecture,
        LaunchValue.QUANTIZATION: demand.quantization,
        LaunchValue.CONTEXT_TOKENS: demand.context_tokens,
        LaunchValue.OUTPUT_TOKENS: demand.output_tokens,
        LaunchValue.BATCH_SIZE: demand.batch_size_per_replica,
        LaunchValue.MODEL_PARALLEL_DEVICES: topology.model_parallel_devices,
        LaunchValue.TENSOR_PARALLEL_SIZE: topology.tensor_parallel_size,
        LaunchValue.PIPELINE_PARALLEL_SIZE: topology.pipeline_parallel_size,
        LaunchValue.DEVICE_KIND: topology.device_kind,
    }
    value = values[source]
    if value is None:
        raise RunnerLaunchError("launch_value_unavailable")
    return value


def render_model_runner_launch(
    *,
    selection: ModelServiceSelection,
    profile: RunnerLaunchProfile,
) -> RunnerLaunchPlan:
    """Render tokenized process, environment, and request desired state."""
    if not isinstance(selection, ModelServiceSelection):
        raise ValueError("selection must be ModelServiceSelection")
    if not isinstance(profile, RunnerLaunchProfile):
        raise ValueError("profile must be RunnerLaunchProfile")
    if not profile.facts_attested:
        raise RunnerLaunchError("launch_profile_unattested")
    if (
        selection.demand.runner_id != profile.runner_id
        or selection.topology.runner_id != profile.runner_id
    ):
        raise RunnerLaunchError("runner_identity_mismatch")
    if selection.topology.distribution_mode is not profile.distribution_mode:
        raise RunnerLaunchError("distribution_mode_mismatch")

    command = [*profile.executable, *profile.fixed_arguments]
    environment: list[tuple[str, str]] = []
    request_options: list[tuple[str, str | int]] = []
    for binding in profile.bindings:
        value = _launch_value(selection, binding.source)
        if binding.target is LaunchTarget.POSITIONAL_ARGUMENT:
            command.append(str(value))
        elif binding.target is LaunchTarget.ARGUMENT:
            command.extend((binding.name or "", str(value)))
        elif binding.target is LaunchTarget.ENVIRONMENT:
            environment.append((binding.name or "", str(value)))
        else:
            request_options.append((binding.name or "", value))

    return RunnerLaunchPlan(
        runner_id=profile.runner_id,
        adapter_id=profile.adapter_id,
        source_revision=profile.source_revision,
        variant_id=selection.demand.variant_id,
        model_id=selection.demand.topology_demand.model_id,
        quantization=selection.demand.quantization,
        distribution_mode=profile.distribution_mode,
        command=tuple(command),
        environment=tuple(environment),
        request_options=tuple(request_options),
        replica_count=selection.demand.data_parallel_replicas,
        devices_per_replica=selection.topology.model_parallel_devices,
    )


def vllm_launch_profile(
    *, runner_id: str, source_revision: str, facts_attested: bool
) -> RunnerLaunchProfile:
    """Build the observed vLLM CLI and OpenAI-request binding profile."""
    return RunnerLaunchProfile(
        runner_id=runner_id,
        adapter_id="vllm-cli",
        source_revision=source_revision,
        executable=("vllm", "serve"),
        fixed_arguments=(),
        bindings=(
            RunnerLaunchBinding(
                LaunchValue.MODEL_ID,
                LaunchTarget.POSITIONAL_ARGUMENT,
            ),
            RunnerLaunchBinding(
                LaunchValue.CONTEXT_TOKENS,
                LaunchTarget.ARGUMENT,
                "--max-model-len",
            ),
            RunnerLaunchBinding(
                LaunchValue.BATCH_SIZE,
                LaunchTarget.ARGUMENT,
                "--max-num-seqs",
            ),
            RunnerLaunchBinding(
                LaunchValue.TENSOR_PARALLEL_SIZE,
                LaunchTarget.ARGUMENT,
                "--tensor-parallel-size",
            ),
            RunnerLaunchBinding(
                LaunchValue.PIPELINE_PARALLEL_SIZE,
                LaunchTarget.ARGUMENT,
                "--pipeline-parallel-size",
            ),
            RunnerLaunchBinding(
                LaunchValue.OUTPUT_TOKENS,
                LaunchTarget.REQUEST_OPTION,
                "max_tokens",
            ),
        ),
        distribution_mode=DistributionMode.EXPLICIT_PARALLEL,
        facts_attested=facts_attested,
    )


def llama_cpp_launch_profile(
    *, runner_id: str, source_revision: str, facts_attested: bool
) -> RunnerLaunchProfile:
    """Build the observed llama-server runner-managed binding profile."""
    return RunnerLaunchProfile(
        runner_id=runner_id,
        adapter_id="llama-cpp-server-cli",
        source_revision=source_revision,
        executable=("llama-server",),
        fixed_arguments=("--n-gpu-layers", "all", "--split-mode", "layer"),
        bindings=(
            RunnerLaunchBinding(
                LaunchValue.MODEL_ID,
                LaunchTarget.ARGUMENT,
                "--model",
            ),
            RunnerLaunchBinding(
                LaunchValue.CONTEXT_TOKENS,
                LaunchTarget.ARGUMENT,
                "--ctx-size",
            ),
            RunnerLaunchBinding(
                LaunchValue.BATCH_SIZE,
                LaunchTarget.ARGUMENT,
                "--parallel",
            ),
            RunnerLaunchBinding(
                LaunchValue.OUTPUT_TOKENS,
                LaunchTarget.ARGUMENT,
                "--n-predict",
            ),
        ),
        distribution_mode=DistributionMode.RUNNER_MANAGED,
        facts_attested=facts_attested,
    )


def ollama_launch_profile(
    *, runner_id: str, source_revision: str, facts_attested: bool
) -> RunnerLaunchProfile:
    """Build the observed Ollama server and request binding profile."""
    return RunnerLaunchProfile(
        runner_id=runner_id,
        adapter_id="ollama-server-api",
        source_revision=source_revision,
        executable=("ollama", "serve"),
        fixed_arguments=(),
        bindings=(
            RunnerLaunchBinding(
                LaunchValue.CONTEXT_TOKENS,
                LaunchTarget.ENVIRONMENT,
                "OLLAMA_CONTEXT_LENGTH",
            ),
            RunnerLaunchBinding(
                LaunchValue.BATCH_SIZE,
                LaunchTarget.ENVIRONMENT,
                "OLLAMA_NUM_PARALLEL",
            ),
            RunnerLaunchBinding(
                LaunchValue.MODEL_ID,
                LaunchTarget.REQUEST_OPTION,
                "model",
            ),
            RunnerLaunchBinding(
                LaunchValue.CONTEXT_TOKENS,
                LaunchTarget.REQUEST_OPTION,
                "num_ctx",
            ),
            RunnerLaunchBinding(
                LaunchValue.OUTPUT_TOKENS,
                LaunchTarget.REQUEST_OPTION,
                "num_predict",
            ),
        ),
        distribution_mode=DistributionMode.RUNNER_MANAGED,
        facts_attested=facts_attested,
    )


__all__ = (
    "LaunchTarget",
    "LaunchValue",
    "RunnerLaunchBinding",
    "RunnerLaunchError",
    "RunnerLaunchPlan",
    "RunnerLaunchProfile",
    "llama_cpp_launch_profile",
    "ollama_launch_profile",
    "render_model_runner_launch",
    "vllm_launch_profile",
)
