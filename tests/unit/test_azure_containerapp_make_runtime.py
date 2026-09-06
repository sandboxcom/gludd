"""Make-mediated Azure Container App runtime and provenance tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import general_ludd.infra.azure_containerapp_make_runtime as runtime_module
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.commands.make import MakeResult
from general_ludd.infra.azure_containerapp_gpu import ModelServingRequirement
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    AzureContainerAppMakeRuntime,
    AzureContainerAppMakeRuntimeError,
    MakeRuntimeEvent,
    MakeRuntimeState,
)
from general_ludd.infra.compute import GPUType
from general_ludd.self_improve.model_candidates import BackendCallBudget

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
IMAGE = "vllm/vllm-openai@sha256:" + "a" * 64
SECRET = "never-render-this-client-secret"


def _policy(**overrides: object) -> AzureContainerAppLiveProofPolicy:
    values: dict[str, object] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": "gludd-models-eastus",
        "environment_name": "gludd-gpu-environment",
        "workload_profile_name": "gpu-t4",
        "workload_profile_type": "Consumption-GPU-NC8as-T4",
        "location": "eastus",
        "app_name": "gludd-vllm-proof-abc123",
        "allowed_cidr": "203.0.113.7/32",
        "container_image": IMAGE,
        "model_name": MODEL,
        "model_revision": REVISION,
        "max_cost_usd": 5.0,
        "ttl_minutes": 60,
        "call_budget": BackendCallBudget(1, 128, 64, 192, 500_000, 30.0),
        "estimated_request_cost_microusd": 250_000,
        "live": True,
        "acknowledgement": LIVE_PROOF_ACKNOWLEDGEMENT,
    }
    values.update(overrides)
    return AzureContainerAppLiveProofPolicy(**cast(Any, values))


def _requirement() -> ModelServingRequirement:
    return ModelServingRequirement(
        model_id=MODEL,
        revision=REVISION,
        parameter_count=500_000_000,
        weight_bits=16,
        kv_cache_mib=2048,
        runtime_overhead_mib=3072,
    )


def _credentials() -> AzureAcceleratorCredentials:
    return AzureAcceleratorCredentials(
        client_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        client_secret=SECRET,
        subscription_id=SUBSCRIPTION,
        tenant_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )


def _plan(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "resource_changes": [{"name": policy.app_name}],
    }


def _outputs(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    endpoint = (
        "https://gludd-vllm-proof-abc123.kindstone.eastus."
        "azurecontainerapps.io"
    )

    def output(value: str) -> dict[str, object]:
        return {"sensitive": False, "type": "string", "value": value}

    return {
        "instance_id": output(policy.expected_resource_id),
        "base_url": output(endpoint),
        "instance_ip": output(policy.expected_resource_id),
        "endpoint_url": output(endpoint),
        "cleanup_boundary": output(policy.expected_resource_id),
        "workload_profile_type": output(policy.workload_profile_type),
        "revision_name": output(f"{policy.app_name}--0000007"),
    }


def _app_document(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    return {
        "id": policy.expected_resource_id,
        "name": policy.app_name,
        "type": "Microsoft.App/containerApps",
        "location": policy.location,
        "properties": {
            "provisioningState": "Succeeded",
            "latestReadyRevisionName": f"{policy.app_name}--0000007",
            "workloadProfileName": policy.workload_profile_name,
            "configuration": {
                "ingress": {
                    "fqdn": (
                        "gludd-vllm-proof-abc123.kindstone.eastus."
                        "azurecontainerapps.io"
                    )
                }
            },
            "template": {
                "containers": [
                    {
                        "image": policy.container_image,
                        "args": [
                            "--model",
                            policy.model_name,
                            "--revision",
                            policy.model_revision,
                            "--tokenizer-revision",
                            policy.model_revision,
                        ],
                    }
                ]
            },
        },
    }


class _Materializer:
    def __init__(self) -> None:
        self.calls: list[tuple[object, Path, str]] = []

    def materialize(
        self,
        config: object,
        destination: str | Path,
        *,
        deployment_name: str,
    ) -> Path:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        self.calls.append((config, path, deployment_name))
        return path


class _Runner:
    def __init__(
        self,
        policy: AzureContainerAppLiveProofPolicy,
        *,
        fail_phase: str | None = None,
        output_payload: object | None = None,
    ) -> None:
        self.policy = policy
        self.fail_phase = fail_phase
        self.output_payload = _outputs(policy) if output_payload is None else output_payload
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []
        self.destroyed = False

    def run(
        self,
        target: str,
        *,
        extra_args: list[str] | None = None,
        timeout_s: int | None = None,
        env_extra: dict[str, str] | None = None,
        stream: bool = False,
        stream_callback: object | None = None,
    ) -> MakeResult:
        del timeout_s, stream, stream_callback
        args = list(extra_args or [])
        environment = dict(env_extra or {})
        self.calls.append((target, args, environment))
        variables = dict(argument.split("=", 1) for argument in args)
        phase = variables["AZURE_CONTAINERAPP_TF_PHASE"]
        if phase == "show-plan":
            Path(variables["AZURE_CONTAINERAPP_TF_JSON_FILE"]).write_text(
                json.dumps(_plan(self.policy)),
                encoding="utf-8",
            )
        if phase == "output":
            Path(variables["AZURE_CONTAINERAPP_TF_JSON_FILE"]).write_text(
                json.dumps(self.output_payload),
                encoding="utf-8",
            )
        if phase == "destroy":
            self.destroyed = True
        failed = phase == self.fail_phase
        return MakeResult(
            target=target,
            exit_code=2 if failed else 0,
            success=not failed,
            duration_s=0.01,
            stdout_tail=f"provider output {SECRET}" if failed else "",
            stderr_tail=f"provider error {SECRET}" if failed else "",
        )


def test_runtime_materializes_and_invokes_only_make_with_secret_environment(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runner = _Runner(policy)
    materializer = _Materializer()
    preflights: list[tuple[object, object]] = []
    traces: list[MakeRuntimeEvent] = []

    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=runner,
        terraform_generator=materializer,
        preflight_check=lambda active, requirement: preflights.append(
            (active, requirement)
        ),
        read_app=lambda _active, _expect_absent: (
            None if runner.destroyed else _app_document(policy)
        ),
        trace_sink=traces.append,
    )

    assert runtime.plan(policy) == _plan(policy)
    runtime.preflight(policy)
    evidence = runtime.apply(policy)
    runtime.destroy(policy)
    assert runtime.exists(policy) is False

    assert [call[1][0] for call in runner.calls] == [
        "AZURE_CONTAINERAPP_TF_PHASE=init",
        "AZURE_CONTAINERAPP_TF_PHASE=validate",
        "AZURE_CONTAINERAPP_TF_PHASE=plan",
        "AZURE_CONTAINERAPP_TF_PHASE=show-plan",
        "AZURE_CONTAINERAPP_TF_PHASE=apply",
        "AZURE_CONTAINERAPP_TF_PHASE=output",
        "AZURE_CONTAINERAPP_TF_PHASE=destroy",
    ]
    assert all(call[0] == "azure-containerapp-terraform-phase" for call in runner.calls)
    assert all(call[2]["ARM_CLIENT_SECRET"] == SECRET for call in runner.calls)
    assert all(SECRET not in repr(call[1]) for call in runner.calls)
    assert preflights == [(policy, _requirement())]
    config, _destination, deployment_name = materializer.calls[0]
    assert cast(Any, config).gpu_type is GPUType.T4
    assert cast(Any, config).allowed_cidr == policy.allowed_cidr
    assert cast(Any, config).spot is False
    assert deployment_name == "proof-abc123"
    marker_path = _destination / ".gludd-azure-containerapp-live-proof.json"
    assert json.loads(marker_path.read_text(encoding="utf-8")) == {
        "operation_digest": policy.operation_digest,
        "protocol": "gludd-azure-containerapp-live-proof-v1",
    }
    assert oct(marker_path.stat().st_mode & 0o777) == "0o600"
    assert evidence.resource_id == policy.expected_resource_id
    assert evidence.cleanup_resource_id == policy.expected_resource_id
    assert evidence.revision_name == f"{policy.app_name}--0000007"
    assert SECRET not in repr(traces)
    assert SECRET not in repr(evidence)


@pytest.mark.parametrize("phase", ["init", "validate", "plan", "show-plan"])
def test_plan_phase_failures_are_fixed_context_and_never_render_provider_output(
    tmp_path: Path,
    phase: str,
) -> None:
    policy = _policy()
    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=_Runner(policy, fail_phase=phase),
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda _policy, _expect_absent: None,
    )

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime.plan(policy)

    assert captured.value.phase == phase
    assert SECRET not in repr(captured.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("revision_name"),
        lambda payload: payload["cleanup_boundary"].update(value="/wrong"),
        lambda payload: payload["base_url"].update(value="https://attacker.invalid"),
        lambda payload: payload["workload_profile_type"].update(value="other"),
        lambda payload: payload["revision_name"].update(sensitive=True),
    ],
)
def test_apply_rejects_mutated_terraform_outputs_before_endpoint_use(
    tmp_path: Path,
    mutation: Any,
) -> None:
    policy = _policy()
    outputs = _outputs(policy)
    mutation(outputs)
    reads: list[object] = []
    runner = _Runner(policy, output_payload=outputs)
    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=runner,
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda active, _expect_absent: reads.append(active),
    )
    runtime.plan(policy)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime.apply(policy)

    assert captured.value.phase == "output"
    assert reads == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(id="/wrong"),
        lambda document: document["properties"].update(provisioningState="Failed"),
        lambda document: document["properties"].update(latestReadyRevisionName="other--1"),
        lambda document: document["properties"]["template"]["containers"][0].update(
            image="vllm/vllm-openai:latest"
        ),
    ],
)
def test_apply_rejects_mutated_arm_deployment_evidence(
    tmp_path: Path,
    mutation: Any,
) -> None:
    policy = _policy()
    document = _app_document(policy)
    mutation(document)
    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=_Runner(policy),
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda _policy, _expect_absent: document,
    )
    runtime.plan(policy)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime.apply(policy)

    assert captured.value.phase == "deployment-evidence"


def test_runtime_rejects_policy_drift_before_another_make_invocation(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runner = _Runner(policy)
    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=runner,
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda _policy, _expect_absent: _app_document(policy),
    )
    runtime.plan(policy)
    calls_before = len(runner.calls)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime.apply(_policy(allowed_cidr="203.0.113.8/32"))

    assert captured.value.phase == "policy-drift"
    assert len(runner.calls) == calls_before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("credentials", object(), "credentials"),
        ("requirement", object(), "requirement"),
        ("preflight_check", object(), "read boundaries"),
        ("read_app", object(), "read boundaries"),
        ("trace_sink", object(), "trace_sink"),
        ("heartbeat_seconds", True, "heartbeat_seconds"),
        ("heartbeat_seconds", 0, "heartbeat_seconds"),
        ("heartbeat_seconds", 61, "heartbeat_seconds"),
        ("heartbeat_seconds", "1", "heartbeat_seconds"),
    ],
)
def test_constructor_rejects_ambiguous_runtime_boundaries(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    policy = _policy()
    arguments: dict[str, object] = {
        "repo_root": Path.cwd(),
        "work_root": tmp_path / "gludd-azure-live-proof",
        "credentials": _credentials(),
        "requirement": _requirement(),
        "make_runner": _Runner(policy),
        "terraform_generator": _Materializer(),
        "preflight_check": lambda _policy, _requirement: None,
        "read_app": lambda _policy, _expect_absent: None,
        "trace_sink": lambda _event: None,
        "heartbeat_seconds": 15.0,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        AzureContainerAppMakeRuntime(**cast(Any, arguments))


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"\xff",
        b'{"value":1,"value":2}',
        b"x" * (2 * 1024 * 1024 + 1),
    ],
    ids=("malformed", "invalid-utf8", "duplicate-fields", "oversized"),
)
def test_bounded_json_reader_rejects_malformed_duplicate_or_oversized_input(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "provider.json"
    path.write_bytes(payload)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime_module._read_bounded_json(path, phase="provider-output")

    assert captured.value.phase == "provider-output"


def test_bounded_json_reader_rejects_missing_and_non_regular_inputs(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    for path in (tmp_path / "missing.json", directory):
        with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
            runtime_module._read_bounded_json(path, phase="provider-output")
        assert captured.value.phase == "provider-output"


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        "--model expected",
        [object()],
        [],
        ["--model"],
        ["--model", "wrong"],
        ["--model", "expected", "--model", "expected"],
    ],
)
def test_required_argument_rejects_non_exact_argv(arguments: object) -> None:
    with pytest.raises(ValueError):
        runtime_module._required_argument(arguments, "--model", "expected")


@pytest.mark.parametrize(
    "output",
    [
        [],
        {},
        {"sensitive": True, "type": "string", "value": "value"},
        {"sensitive": False, "type": "number", "value": "value"},
        {"sensitive": False, "type": "string", "value": ""},
        {"sensitive": False, "type": "string", "value": 1},
    ],
)
def test_output_value_requires_public_nonempty_string_metadata(output: object) -> None:
    with pytest.raises((KeyError, ValueError)):
        runtime_module._output_value({"result": output}, "result")


def test_mapping_and_member_reject_non_objects_and_missing_members() -> None:
    with pytest.raises(ValueError):
        runtime_module._mapping([])
    with pytest.raises(ValueError):
        runtime_module._member({}, "missing")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(name="other-app"),
        lambda document: document.update(type=7),
        lambda document: document.update(type="Microsoft.App/managedEnvironments"),
        lambda document: document.update(location=7),
        lambda document: document.update(location="westus"),
        lambda document: document["properties"].update(workloadProfileName="other"),
        lambda document: document["properties"]["configuration"].update(ingress={}),
        lambda document: document["properties"]["configuration"]["ingress"].update(
            fqdn=7
        ),
        lambda document: document["properties"]["configuration"]["ingress"].update(
            fqdn="attacker.invalid"
        ),
        lambda document: document["properties"]["template"].update(containers={}),
        lambda document: document["properties"]["template"].update(containers=[]),
        lambda document: document["properties"]["template"].update(
            containers=[{}, {}]
        ),
        lambda document: document["properties"]["template"]["containers"][0].update(
            args="--model unsafe"
        ),
        lambda document: document["properties"]["template"]["containers"][0].update(
            args=["--model", MODEL]
        ),
    ],
)
def test_app_evidence_rejects_every_identity_ingress_and_argument_widening(
    mutation: Any,
) -> None:
    policy = _policy()
    document = _app_document(policy)
    mutation(document)
    evidence = runtime_module._deployment_outputs(_outputs(policy), policy)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime_module._validate_app_document(document, policy, evidence)

    assert captured.value.phase == "deployment-evidence"


def test_runtime_internal_boundaries_fail_closed_with_fixed_phases(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=_Runner(policy),
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda _policy, _expect_absent: None,
        trace_sink=lambda _event: (_ for _ in ()).throw(RuntimeError("private")),
    )

    with pytest.raises(AzureContainerAppMakeRuntimeError) as not_planned:
        runtime._paths()
    assert not_planned.value.phase == "not-planned"
    with pytest.raises(AzureContainerAppMakeRuntimeError) as invalid_policy:
        runtime._bind(cast(Any, object()))
    assert invalid_policy.value.phase == "policy"
    with pytest.raises(AzureContainerAppMakeRuntimeError) as trace_failure:
        runtime._emit("plan", MakeRuntimeState.STARTED)
    assert trace_failure.value.phase == "trace"


def test_runtime_rejects_credential_sizing_name_and_materializer_drift(
    tmp_path: Path,
) -> None:
    cases: list[tuple[AzureContainerAppLiveProofPolicy, object, object, str]] = [
        (
            _policy(subscription_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            _requirement(),
            _Materializer(),
            "credentials",
        ),
        (
            _policy(workload_profile_type="Consumption-GPU-NC24-A100"),
            _requirement(),
            _Materializer(),
            "sizing",
        ),
        (_policy(app_name="other-proof"), _requirement(), _Materializer(), "app-name"),
    ]

    class WrongDestinationMaterializer(_Materializer):
        def materialize(
            self,
            config: object,
            destination: str | Path,
            *,
            deployment_name: str,
        ) -> Path:
            super().materialize(
                config,
                destination,
                deployment_name=deployment_name,
            )
            return Path(destination).parent

    cases.append((_policy(), _requirement(), WrongDestinationMaterializer(), "materialize"))

    for index, (policy, requirement, materializer, phase) in enumerate(cases):
        runtime = AzureContainerAppMakeRuntime(
            repo_root=Path.cwd(),
            work_root=tmp_path / f"gludd-azure-live-proof-{index}",
            credentials=_credentials(),
            requirement=cast(Any, requirement),
            make_runner=_Runner(policy),
            terraform_generator=cast(Any, materializer),
            preflight_check=lambda _policy, _requirement: None,
            read_app=lambda _policy, _expect_absent: None,
        )
        with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
            runtime.plan(policy)
        assert captured.value.phase == phase


@pytest.mark.parametrize(
    ("operation", "expected_phase"),
    [("preflight", "preflight"), ("apply", "deployment-evidence"), ("exists", "absence")],
)
def test_runtime_censors_external_read_boundary_failures(
    tmp_path: Path,
    operation: str,
    expected_phase: str,
) -> None:
    policy = _policy()

    def fail(*_args: object) -> object:
        raise RuntimeError(f"private provider output {SECRET}")

    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=_Runner(policy),
        terraform_generator=_Materializer(),
        preflight_check=cast(Any, fail),
        read_app=cast(Any, fail),
    )
    if operation == "apply":
        runtime.plan(policy)

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        getattr(runtime, operation)(policy)

    assert captured.value.phase == expected_phase
    assert SECRET not in repr(captured.value)


def test_runner_exception_emits_failed_trace_and_is_censored(tmp_path: Path) -> None:
    policy = _policy()
    traces: list[MakeRuntimeEvent] = []

    class RaisingRunner(_Runner):
        def run(self, *_args: object, **_kwargs: object) -> MakeResult:
            raise RuntimeError(f"private provider output {SECRET}")

    runtime = AzureContainerAppMakeRuntime(
        repo_root=Path.cwd(),
        work_root=tmp_path / "gludd-azure-live-proof",
        credentials=_credentials(),
        requirement=_requirement(),
        make_runner=RaisingRunner(policy),
        terraform_generator=_Materializer(),
        preflight_check=lambda _policy, _requirement: None,
        read_app=lambda _policy, _expect_absent: None,
        trace_sink=traces.append,
    )

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        runtime.plan(policy)

    assert captured.value.phase == "init"
    assert traces[-1].state is MakeRuntimeState.FAILED
    assert SECRET not in repr(captured.value)
