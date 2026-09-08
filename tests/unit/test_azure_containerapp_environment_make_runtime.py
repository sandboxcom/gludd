"""Concrete Terraform runtime tests for Gludd-owned Azure environments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import general_ludd.infra.azure_containerapp_environment_make_runtime as runtime_module
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
)
from general_ludd.infra.azure_containerapp_environment_make_runtime import (
    AzureContainerAppEnvironmentMakeRuntime,
    AzureContainerAppEnvironmentTerraformMaterializer,
    AzureContainerAppEnvironmentTerraformRuntime,
    AzureContainerAppMakeRuntimeError,
    MakeRuntimeEvent,
    MakeRuntimeState,
)
from general_ludd.infra.azure_containerapp_environment_materializer import (
    verify_existing_state_boundary,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    AzureContainerAppTerraformPhaseError,
    TerraformRuntimeState,
    TerraformUIEvent,
)

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
SECRET = "never-render-this-environment-secret"


def test_environment_runtime_has_no_make_or_shell_provisioning_dependency() -> None:
    source = Path(runtime_module.__file__).read_text(encoding="utf-8")

    assert "general_ludd.commands.make" not in source
    assert "MakeRunner" not in source
    assert "subprocess" not in source


def _profile(name: str = "gpu-t4") -> AzureEnvironmentProfile:
    return AzureEnvironmentProfile(
        profile_name=name,
        workload_profile_type=(
            "Consumption-GPU-NC8as-T4"
            if name == "gpu-t4"
            else "Consumption-GPU-NC24-A100"
        ),
    )


def _policy(**overrides: object) -> AzureEnvironmentLifecyclePolicy:
    values: dict[str, object] = {
        "subscription_id": SUBSCRIPTION,
        "resource_group": "gludd-models-eastus",
        "environment_name": "gludd-gpu-environment",
        "location": "eastus",
        "profiles": (_profile(),),
        "owner_digest": "a" * 64,
        "plan_digest": "b" * 64,
        "expires_at_utc": "2026-09-06T20:00:00Z",
    }
    values.update(overrides)
    return AzureEnvironmentLifecyclePolicy(**cast(Any, values))


def _credentials(**overrides: object) -> AzureAcceleratorCredentials:
    values: dict[str, object] = {
        "client_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "client_secret": SECRET,
        "subscription_id": SUBSCRIPTION,
        "tenant_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    }
    values.update(overrides)
    return AzureAcceleratorCredentials(**cast(Any, values))


def _expected_after(policy: AzureEnvironmentLifecyclePolicy) -> dict[str, object]:
    return {
        "type": "Microsoft.App/managedEnvironments@2025-07-01",
        "name": policy.environment_name,
        "parent_id": policy.resource_group_id,
        "location": policy.location,
        "tags": policy.ownership_tags,
        "body": {
            "properties": {
                "workloadProfiles": [
                    {
                        "name": profile.profile_name,
                        "workloadProfileType": profile.workload_profile_type,
                    }
                    for profile in policy.profiles
                ]
            }
        },
    }


def _plan(
    policy: AzureEnvironmentLifecyclePolicy,
    action: str = "create",
) -> dict[str, object]:
    return {
        "resource_changes": [
            {
                "address": "module.environment.azapi_resource.managed_environment",
                "mode": "managed",
                "type": "azapi_resource",
                "name": "managed_environment",
                "provider_name": "registry.terraform.io/azure/azapi",
                "change": {
                    "actions": [action],
                    "after": _expected_after(policy),
                    "after_unknown": {},
                },
            }
        ]
    }


class _Materializer:
    def __init__(self, *, wrong_destination: bool = False) -> None:
        self.calls: list[tuple[AzureEnvironmentLifecyclePolicy, Path]] = []
        self.wrong_destination = wrong_destination

    def materialize(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        destination: str | Path,
    ) -> Path:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            (path / name).write_text("# fake\n", encoding="utf-8")
        self.calls.append((policy, path))
        return path.parent if self.wrong_destination else path


class _Runner:
    def __init__(
        self,
        materializer: _Materializer,
        *,
        fail_phase: str | None = None,
        action: str = "create",
    ) -> None:
        self.materializer = materializer
        self.fail_phase = fail_phase
        self.action = action
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        *,
        phase: str,
        terraform_dir: str | Path,
        plan_file: str | Path,
        json_file: str | Path,
        allowed_root: str | Path,
        environment: dict[str, str],
        timeout_seconds: int,
        progress: object,
    ) -> None:
        cast(Any, progress)(phase, TerraformRuntimeState.STARTED, 0)
        self.calls.append(
            {
                "phase": phase,
                "terraform_dir": Path(terraform_dir),
                "plan_file": Path(plan_file),
                "json_file": Path(json_file),
                "allowed_root": Path(allowed_root),
                "environment": dict(environment),
                "timeout_seconds": timeout_seconds,
            }
        )
        if phase == "show-plan":
            policy = self.materializer.calls[-1][0]
            Path(json_file).write_text(
                json.dumps(_plan(policy, self.action)),
                encoding="utf-8",
            )
        if phase == self.fail_phase:
            cast(Any, progress)(phase, TerraformRuntimeState.FAILED, 0)
            raise AzureContainerAppTerraformPhaseError(phase)
        cast(Any, progress)(phase, TerraformRuntimeState.SUCCEEDED, 0)


def _runtime(
    tmp_path: Path,
    *,
    materializer: _Materializer | None = None,
    runner: _Runner | None = None,
    credentials: AzureAcceleratorCredentials | None = None,
    read_environment: object | None = None,
    list_apps: object | None = None,
    trace_sink: object | None = None,
) -> tuple[AzureContainerAppEnvironmentMakeRuntime, _Materializer, _Runner]:
    active_materializer = materializer or _Materializer()
    active_runner = runner or _Runner(active_materializer)
    runtime = AzureContainerAppEnvironmentTerraformRuntime(
        work_root=tmp_path / "gludd-azure-containerapp-live-proof",
        credentials=credentials or _credentials(),
        read_environment=cast(
            Any,
            read_environment
            or (lambda _policy, _expect_absent: {"kind": "environment"}),
        ),
        list_environment_apps=cast(
            Any,
            list_apps or (lambda _policy: ()),
        ),
        terraform_executor=active_runner,
        terraform_materializer=active_materializer,
        trace_sink=cast(Any, trace_sink or (lambda _event: None)),
    )
    return runtime, active_materializer, active_runner


def test_runtime_materializes_and_runs_terraform_directly(
    tmp_path: Path,
) -> None:
    traces: list[MakeRuntimeEvent] = []
    policy = _policy()
    runtime, materializer, runner = _runtime(tmp_path, trace_sink=traces.append)

    assert runtime.plan(policy) == _plan(policy)
    runtime.apply(policy)
    runtime.destroy(policy)

    phases = [call["phase"] for call in runner.calls]
    assert phases == [
        "init",
        "validate",
        "plan",
        "show-plan",
        "apply",
        "destroy",
    ]
    assert all(
        cast(dict[str, str], call["environment"])["ARM_CLIENT_SECRET"] == SECRET
        for call in runner.calls
    )
    assert all("make" not in repr(call["phase"]).casefold() for call in runner.calls)
    assert materializer.calls == [(policy, materializer.calls[0][1])]
    marker = materializer.calls[0][1] / ".gludd-azure-containerapp-live-proof.json"
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["operation_digest"].startswith(materializer.calls[0][1].name)
    assert oct(marker.stat().st_mode & 0o777) == "0o600"
    assert [event.state for event in traces] == [
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
        MakeRuntimeState.STARTED,
        MakeRuntimeState.SUCCEEDED,
    ]
    assert SECRET not in repr(traces)


def test_environment_destroy_allows_azure_managed_deletion_to_finish(
    tmp_path: Path,
) -> None:
    runtime, _materializer, runner = _runtime(tmp_path)

    runtime.destroy(_policy())

    assert runner.calls[0]["phase"] == "destroy"
    assert runner.calls[0]["timeout_seconds"] == 1_800


def test_environment_runtime_forwards_machine_ui_and_exact_azure_state(
    tmp_path: Path,
) -> None:
    traces: list[MakeRuntimeEvent] = []
    policy = _policy()
    document = {
        "properties": {"provisioningState": "InfrastructureSetupInProgress"}
    }
    runtime = AzureContainerAppEnvironmentTerraformRuntime(
        work_root=tmp_path / "gludd-azure-containerapp-live-proof",
        credentials=_credentials(),
        read_environment=lambda _policy, _expect_absent: document,
        list_environment_apps=lambda _policy: (),
        trace_sink=traces.append,
    )
    runtime.read_environment(policy, expect_absent=False)

    assert traces[-1] == MakeRuntimeEvent(
        phase="azure-resource-state",
        state=MakeRuntimeState.HEARTBEAT,
        operation_digest=policy.operation_digest,
        target="azure-resource-manager",
        event_source="azure_resource_manager",
        resource_type="microsoft.app/managedenvironments",
        action="read",
        provisioning_state="infrastructure-setup-in-progress",
    )

    cast(Any, runtime._executor)._telemetry_sink(
        TerraformUIEvent(
            phase="apply",
            state=TerraformRuntimeState.HEARTBEAT,
            resource_type="azapi_resource",
            action="create",
            event_kind="apply_progress",
            elapsed_seconds=91,
        )
    )

    assert traces[-1] == MakeRuntimeEvent(
        phase="apply",
        state=MakeRuntimeState.HEARTBEAT,
        operation_digest=policy.operation_digest,
        elapsed_seconds=91,
        event_source="opentofu_ui",
        resource_type="azapi_resource",
        action="create",
        event_kind="apply_progress",
    )
    assert SECRET not in repr(traces)


def test_runtime_reuses_one_state_root_across_safe_desired_state_reconciliation(
    tmp_path: Path,
) -> None:
    first = _policy()
    second = _policy(
        profiles=(_profile(), _profile("gpu-a100")),
        plan_digest="c" * 64,
        expires_at_utc="2026-09-06T21:00:00Z",
    )
    runtime, materializer, _runner = _runtime(tmp_path)

    runtime.plan(first)
    runtime.apply(first)
    runtime.plan(second)
    runtime.apply(second)

    assert len(materializer.calls) == 2
    assert materializer.calls[0][1] == materializer.calls[1][1]
    assert materializer.calls[0][1].name == first.state_digest[:24]
    assert first.state_digest == second.state_digest
    assert first.operation_digest != second.operation_digest


@pytest.mark.parametrize(
    "override",
    [
        {"environment_name": "other-gludd-environment"},
        {"resource_group": "other-gludd-group"},
        {"owner_digest": "d" * 64},
    ],
)
def test_runtime_rejects_state_identity_drift_before_another_make_call(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    runtime, _materializer, runner = _runtime(tmp_path)
    runtime.plan(_policy())
    before = len(runner.calls)

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-identity"):
        runtime.plan(_policy(**override))

    assert len(runner.calls) == before


def test_apply_requires_the_exact_latest_audited_policy(tmp_path: Path) -> None:
    first = _policy()
    changed = _policy(plan_digest="c" * 64)
    runtime, _materializer, runner = _runtime(tmp_path)
    runtime.plan(first)
    before = len(runner.calls)

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="policy-drift"):
        runtime.apply(changed)

    assert len(runner.calls) == before


@pytest.mark.parametrize("phase", ["init", "validate", "plan", "show-plan", "apply", "destroy"])
def test_every_terraform_phase_failure_is_censored(
    tmp_path: Path,
    phase: str,
) -> None:
    materializer = _Materializer()
    runner = _Runner(materializer, fail_phase=phase)
    runtime, _materializer, _runner = _runtime(
        tmp_path,
        materializer=materializer,
        runner=runner,
    )
    policy = _policy()

    with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
        if phase in {"init", "validate", "plan", "show-plan"}:
            runtime.plan(policy)
        elif phase == "apply":
            runner.fail_phase = None
            runtime.plan(policy)
            runner.fail_phase = phase
            runtime.apply(policy)
        else:
            runtime.destroy(policy)

    assert captured.value.phase == phase
    assert SECRET not in repr(captured.value)


def test_read_boundaries_are_forwarded_exactly_and_censored(tmp_path: Path) -> None:
    policy = _policy()
    reads: list[tuple[AzureEnvironmentLifecyclePolicy, bool]] = []
    inventories: list[AzureEnvironmentLifecyclePolicy] = []
    runtime, _materializer, _runner = _runtime(
        tmp_path,
        read_environment=lambda active, absent: reads.append((active, absent)) or None,
        list_apps=lambda active: inventories.append(active) or ("one",),
    )

    assert runtime.read_environment(policy, expect_absent=True) is None
    assert runtime.list_environment_apps(policy) == ("one",)
    assert reads == [(policy, True)]
    assert inventories == [policy]

    def fail(*_args: object) -> object:
        raise RuntimeError(f"private Azure output {SECRET}")

    failing, _materializer, _runner = _runtime(
        tmp_path / "fail",
        read_environment=fail,
        list_apps=fail,
    )
    for operation in (
        lambda: failing.read_environment(policy, expect_absent=False),
        lambda: failing.list_environment_apps(policy),
    ):
        with pytest.raises(AzureContainerAppMakeRuntimeError) as captured:
            operation()
        assert SECRET not in repr(captured.value)


def test_default_materializer_writes_only_reviewed_assets_and_public_tfvars(
    tmp_path: Path,
) -> None:
    policy = _policy(profiles=(_profile(), _profile("gpu-a100")))
    destination = tmp_path / policy.state_digest[:24]
    materializer = AzureContainerAppEnvironmentTerraformMaterializer()

    assert materializer.materialize(policy, destination) == destination.resolve()

    assert sorted(path.name for path in destination.iterdir()) == [
        "main.tf",
        "modules",
        "outputs.tf",
        "terraform.tfvars.json",
        "variables.tf",
    ]
    main = (destination / "main.tf").read_text(encoding="utf-8")
    assert 'source = "./modules/azure-container-app-environment"' in main
    assert "../../modules" not in main
    assert "backend" not in main.casefold()
    tfvars_path = destination / "terraform.tfvars.json"
    tfvars = json.loads(tfvars_path.read_text(encoding="utf-8"))
    assert tfvars == {
        "environment_name": policy.environment_name,
        "resource_group_id": policy.resource_group_id,
        "region": policy.location,
        "workload_profiles": [
            {
                "profile_name": profile.profile_name,
                "workload_profile_type": profile.workload_profile_type,
            }
            for profile in policy.profiles
        ],
        "owner_digest": policy.owner_digest,
        "plan_digest": policy.plan_digest,
        "expires_at_utc": policy.expires_at_utc,
    }
    assert oct(tfvars_path.stat().st_mode & 0o777) == "0o600"
    rendered = "\n".join(
        path.read_text(encoding="utf-8")
        for path in destination.rglob("*")
        if path.is_file()
    )
    assert SECRET not in rendered
    assert "client_secret" not in rendered.casefold()


def test_default_materializer_preserves_owned_state_during_reconciliation(
    tmp_path: Path,
) -> None:
    first = _policy()
    second = _policy(plan_digest="c" * 64)
    destination = tmp_path / first.state_digest[:24]
    materializer = AzureContainerAppEnvironmentTerraformMaterializer()
    materializer.materialize(first, destination)
    state = destination / "terraform.tfstate"
    state.write_text("owned-state", encoding="utf-8")

    materializer.materialize(second, destination)

    assert state.read_text(encoding="utf-8") == "owned-state"
    tfvars = json.loads(
        (destination / "terraform.tfvars.json").read_text(encoding="utf-8")
    )
    assert tfvars["plan_digest"] == second.plan_digest


def test_constructor_and_materializer_boundaries_fail_closed(tmp_path: Path) -> None:
    policy = _policy()
    wrong = _Materializer(wrong_destination=True)
    runtime, _materializer, runner = _runtime(tmp_path, materializer=wrong)
    with pytest.raises(AzureContainerAppMakeRuntimeError, match="materialize"):
        runtime.plan(policy)
    assert runner.calls == []

    cases: list[tuple[str, object]] = [
        ("credentials", object()),
        ("read_environment", object()),
        ("list_environment_apps", object()),
        ("trace_sink", object()),
        ("heartbeat_seconds", 0),
        ("heartbeat_seconds", True),
        ("heartbeat_seconds", 61),
    ]
    for name, value in cases:
        arguments: dict[str, object] = {
            "work_root": tmp_path / name,
            "credentials": _credentials(),
            "read_environment": lambda _policy, _absent: None,
            "list_environment_apps": lambda _policy: (),
            "heartbeat_seconds": 15,
        }
        arguments[name] = value
        with pytest.raises(ValueError):
            AzureContainerAppEnvironmentMakeRuntime(**cast(Any, arguments))


def test_materializer_rejects_untyped_policy_and_missing_reviewed_assets(
    tmp_path: Path,
) -> None:
    materializer = AzureContainerAppEnvironmentTerraformMaterializer()
    with pytest.raises(AzureContainerAppMakeRuntimeError, match="policy"):
        materializer.materialize(cast(Any, object()), tmp_path / "untyped")

    missing_assets = AzureContainerAppEnvironmentTerraformMaterializer(
        tmp_path / "missing-assets"
    )
    with pytest.raises(AzureContainerAppMakeRuntimeError, match="assets"):
        missing_assets.materialize(_policy(), tmp_path / "missing-output")


def test_state_boundary_accepts_only_absent_or_empty_unowned_directories(
    tmp_path: Path,
) -> None:
    digest = _policy().state_digest
    verify_existing_state_boundary(tmp_path / "absent", digest)

    empty = tmp_path / "empty"
    empty.mkdir()
    verify_existing_state_boundary(empty, digest)

    regular_file = tmp_path / "regular-file"
    regular_file.write_text("not a state directory", encoding="utf-8")
    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
        verify_existing_state_boundary(regular_file, digest)


def test_subscription_mismatch_fails_before_materialization_or_make(tmp_path: Path) -> None:
    runtime, materializer, runner = _runtime(
        tmp_path,
        credentials=_credentials(
            subscription_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        ),
    )

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="credentials"):
        runtime.plan(_policy())

    assert materializer.calls == []
    assert runner.calls == []


@pytest.mark.parametrize(
    "existing_payload",
    [
        None,
        {"operation_digest": "f" * 64, "protocol": "gludd-azure-containerapp-live-proof-v1"},
        {"operation_digest": "a" * 64, "protocol": "untrusted"},
        {"operation_digest": "a" * 64},
    ],
    ids=("missing-marker", "wrong-owner", "wrong-protocol", "malformed-marker"),
)
def test_existing_state_requires_the_exact_private_ownership_marker(
    tmp_path: Path,
    existing_payload: dict[str, str] | None,
) -> None:
    policy = _policy()
    work_root = tmp_path / "gludd-azure-containerapp-live-proof"
    state_root = work_root / policy.state_digest[:24]
    state_root.mkdir(parents=True)
    (state_root / "terraform.tfstate").write_text("{}", encoding="utf-8")
    if existing_payload is not None:
        marker = state_root / ".gludd-azure-containerapp-live-proof.json"
        marker.write_text(json.dumps(existing_payload), encoding="utf-8")
        marker.chmod(0o600)
    runtime, materializer, runner = _runtime(tmp_path)

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
        runtime.plan(policy)

    assert materializer.calls == []
    assert runner.calls == []


def test_exact_existing_marker_allows_safe_state_reuse(tmp_path: Path) -> None:
    policy = _policy()
    work_root = tmp_path / "gludd-azure-containerapp-live-proof"
    state_root = work_root / policy.state_digest[:24]
    state_root.mkdir(parents=True)
    marker = state_root / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text(
        json.dumps(
            {
                "operation_digest": policy.state_digest,
                "protocol": "gludd-azure-containerapp-live-proof-v1",
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o600)
    (state_root / "terraform.tfstate").write_text("{}", encoding="utf-8")
    runtime, materializer, runner = _runtime(tmp_path)

    assert runtime.plan(policy) == _plan(policy)

    assert materializer.calls[0][1] == state_root
    assert len(runner.calls) == 4


def test_group_readable_existing_marker_is_rejected(tmp_path: Path) -> None:
    policy = _policy()
    state_root = (
        tmp_path
        / "gludd-azure-containerapp-live-proof"
        / policy.state_digest[:24]
    )
    state_root.mkdir(parents=True)
    marker = state_root / ".gludd-azure-containerapp-live-proof.json"
    marker.write_text(
        json.dumps(
            {
                "operation_digest": policy.state_digest,
                "protocol": "gludd-azure-containerapp-live-proof-v1",
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o640)
    runtime, materializer, runner = _runtime(tmp_path)

    with pytest.raises(AzureContainerAppMakeRuntimeError, match="state-ownership"):
        runtime.plan(policy)

    assert materializer.calls == []
    assert runner.calls == []


def test_long_terraform_phase_emits_content_free_heartbeats(tmp_path: Path) -> None:
    materializer = _Materializer()

    class SlowRunner(_Runner):
        def run(self, *args: object, **kwargs: object) -> None:
            progress = cast(Any, kwargs["progress"])
            progress(str(kwargs["phase"]), TerraformRuntimeState.HEARTBEAT, 1)
            super().run(*args, **kwargs)

    runner = SlowRunner(materializer)
    traces: list[MakeRuntimeEvent] = []
    runtime = AzureContainerAppEnvironmentMakeRuntime(
        work_root=tmp_path / "gludd-azure-containerapp-live-proof",
        credentials=_credentials(),
        read_environment=lambda _policy, _absent: None,
        list_environment_apps=lambda _policy: (),
        terraform_executor=runner,
        terraform_materializer=materializer,
        trace_sink=traces.append,
        heartbeat_seconds=0.002,
    )

    runtime.plan(_policy())

    assert any(event.state is MakeRuntimeState.HEARTBEAT for event in traces)
    assert all(event.target == "terraform" for event in traces)
    assert SECRET not in repr(traces)


def test_runner_and_trace_exceptions_are_censored(tmp_path: Path) -> None:
    materializer = _Materializer()

    class RaisingRunner(_Runner):
        def run(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"private provider output {SECRET}")

    traces: list[MakeRuntimeEvent] = []
    runner = RaisingRunner(materializer)
    runtime, _materializer, _runner = _runtime(
        tmp_path,
        materializer=materializer,
        runner=runner,
        trace_sink=traces.append,
    )
    with pytest.raises(AzureContainerAppMakeRuntimeError) as runner_failure:
        runtime.plan(_policy())
    assert runner_failure.value.phase == "init"
    assert traces[-1].state is MakeRuntimeState.FAILED
    assert SECRET not in repr(runner_failure.value)

    failing_trace, _materializer, runner = _runtime(
        tmp_path / "trace",
        trace_sink=lambda _event: (_ for _ in ()).throw(
            RuntimeError(f"private trace {SECRET}")
        ),
    )
    with pytest.raises(AzureContainerAppMakeRuntimeError) as trace_failure:
        failing_trace.plan(_policy())
    assert trace_failure.value.phase == "trace"
    assert runner.calls == []
    assert SECRET not in repr(trace_failure.value)
