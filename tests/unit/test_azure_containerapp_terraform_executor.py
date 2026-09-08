"""Direct, observable Terraform execution for Gludd-owned Azure resources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, Any

import pytest

from general_ludd.infra.azure_containerapp_terraform_executor import (
    AzureContainerAppTerraformPhaseError,
    AzureContainerAppTerraformPhaseExecutor,
    TerraformRuntimeState,
    TerraformUIEvent,
    terraform_process_environment,
)

SECRET = "executor-secret-that-must-stay-private"


class _Clock:
    def __init__(self, step: float = 0.6) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.value
        self.value += self.step
        return value


class _Process:
    def __init__(
        self,
        argv: list[str],
        *,
        stdout: IO[str] | int | None = None,
        returncode: int = 0,
        polls_before_exit: int = 0,
        output: str = '{"safe":true}',
        **kwargs: object,
    ) -> None:
        self.argv = argv
        self.kwargs = kwargs
        self.returncode = returncode
        self.polls_before_exit = polls_before_exit
        self.polls = 0
        self.terminated = False
        self.killed = False
        if hasattr(stdout, "write"):
            stdout.write(output)
            stdout.flush()

    def poll(self) -> int | None:
        self.polls += 1
        if self.polls <= self.polls_before_exit:
            return None
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _owned_root(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    allowed_root = tmp_path / "gludd-azure-containerapp-environments"
    terraform_dir = allowed_root / ("a" * 24)
    terraform_dir.mkdir(parents=True)
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        (terraform_dir / name).write_text("# reviewed\n", encoding="utf-8")
    (terraform_dir / ".gludd-azure-containerapp-live-proof.json").write_text(
        json.dumps(
            {
                "protocol": "gludd-azure-containerapp-live-proof-v1",
                "operation_digest": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return (
        allowed_root,
        terraform_dir,
        terraform_dir / "gludd.tfplan",
        terraform_dir / "gludd.plan.json",
    )


def _environment() -> dict[str, str]:
    return {
        "ARM_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
        "ARM_CLIENT_SECRET": SECRET,
        "ARM_SUBSCRIPTION_ID": "22222222-2222-4222-8222-222222222222",
        "ARM_TENANT_ID": "33333333-3333-4333-8333-333333333333",
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }


def test_process_environment_is_minimal_and_credentials_override_parent() -> None:
    parent = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "HTTPS_PROXY": "https://proxy.invalid",
        "UNRELATED_PROJECT_SECRET": "must-not-cross-boundary",
        "ARM_CLIENT_SECRET": "stale-parent-secret",
        "TF_LOG": "TRACE",
    }

    result = terraform_process_environment(
        _environment(),
        parent_environment=parent,
    )

    assert result["PATH"] == "/safe/bin"
    assert result["HOME"] == "/safe/home"
    assert result["HTTPS_PROXY"] == "https://proxy.invalid"
    assert result["ARM_CLIENT_SECRET"] == SECRET
    assert "UNRELATED_PROJECT_SECRET" not in result
    assert "TF_LOG" not in result


def test_executor_runs_direct_list_argv_with_exact_environment_and_traces(
    tmp_path: Path,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    plan_file.write_bytes(b"saved-plan")
    processes: list[_Process] = []
    events: list[tuple[str, TerraformRuntimeState, int]] = []

    def factory(argv: list[str], **kwargs: object) -> _Process:
        process = _Process(argv, polls_before_exit=2, **cast_kwargs(kwargs))
        processes.append(process)
        return process

    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/tofu",
        process_factory=factory,
        heartbeat_seconds=0.5,
        poll_seconds=0.1,
        monotonic=_Clock(),
        sleeper=lambda _seconds: None,
    )
    executor.run(
        phase="show-plan",
        terraform_dir=terraform_dir,
        plan_file=plan_file,
        json_file=json_file,
        allowed_root=allowed_root,
        environment=_environment(),
        timeout_seconds=30,
        progress=lambda phase, state, elapsed: events.append(
            (phase, state, elapsed)
        ),
    )

    assert len(processes) == 1
    process = processes[0]
    assert process.argv == [
        "/opt/gludd/bin/tofu",
        "show",
        "-json",
        str(plan_file),
    ]
    assert process.kwargs["shell"] is False
    assert process.kwargs["env"] == _environment()
    assert "make" not in " ".join(process.argv).casefold()
    assert [state for _phase, state, _elapsed in events] == [
        TerraformRuntimeState.STARTED,
        TerraformRuntimeState.HEARTBEAT,
        TerraformRuntimeState.HEARTBEAT,
        TerraformRuntimeState.SUCCEEDED,
    ]
    assert json.loads(json_file.read_text(encoding="utf-8")) == {"safe": True}
    assert oct(json_file.stat().st_mode & 0o777) == "0o600"
    assert SECRET not in repr(events)


def test_apply_streams_only_allowlisted_machine_ui_resource_facts(
    tmp_path: Path,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    plan_file.write_bytes(b"saved-plan")
    ui_events: list[TerraformUIEvent] = []
    process_events = (
        {
            "@message": f"provider version includes {SECRET}",
            "type": "version",
            "ui": "1.2",
        },
        {
            "@message": f"creating address {SECRET}",
            "type": "apply_start",
            "hook": {
                "resource": {
                    "addr": f"module.{SECRET}.azapi_resource.environment",
                    "resource_type": "azapi_resource",
                    "resource_name": SECRET,
                },
                "action": "create",
            },
        },
        {
            "@message": f"still creating {SECRET}",
            "type": "apply_progress",
            "hook": {
                "resource": {
                    "addr": f"module.{SECRET}.azapi_resource.environment",
                    "resource_type": "azapi_resource",
                },
                "action": "create",
                "elapsed_seconds": 7,
                "id_value": SECRET,
            },
        },
        {
            "@message": f"creation complete {SECRET}",
            "type": "apply_complete",
            "hook": {
                "resource": {
                    "addr": f"module.{SECRET}.azapi_resource.environment",
                    "resource_type": "azapi_resource",
                },
                "action": "create",
                "elapsed_seconds": 11,
                "id_value": SECRET,
            },
        },
        {
            "type": "diagnostic",
            "diagnostic": {"detail": SECRET},
        },
    )
    output = "\n".join(json.dumps(event) for event in process_events)
    output += f"\nnot-json-{SECRET}\n"
    processes: list[_Process] = []
    emission_polls: list[int] = []

    def factory(argv: list[str], **kwargs: object) -> _Process:
        process = _Process(argv, output=output, polls_before_exit=2, **cast_kwargs(kwargs))
        processes.append(process)
        return process

    def record_ui_event(event: TerraformUIEvent) -> None:
        ui_events.append(event)
        emission_polls.append(processes[0].polls)

    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/tofu",
        process_factory=factory,
        telemetry_sink=record_ui_event,
        heartbeat_seconds=0.5,
        poll_seconds=0.1,
        monotonic=_Clock(),
        sleeper=lambda _seconds: None,
    )

    executor.run(
        phase="apply",
        terraform_dir=terraform_dir,
        plan_file=plan_file,
        json_file=json_file,
        allowed_root=allowed_root,
        environment=_environment(),
        timeout_seconds=30,
    )

    assert "-json" in processes[0].argv
    assert [
        (event.phase, event.state, event.resource_type, event.action, event.elapsed_seconds)
        for event in ui_events
    ] == [
        ("apply", TerraformRuntimeState.STARTED, "azapi_resource", "create", 0),
        ("apply", TerraformRuntimeState.HEARTBEAT, "azapi_resource", "create", 7),
        ("apply", TerraformRuntimeState.SUCCEEDED, "azapi_resource", "create", 11),
    ]
    assert SECRET not in repr(ui_events)
    assert emission_polls
    assert max(emission_polls) <= 2


def test_executor_times_out_terminates_and_emits_only_censored_failure(
    tmp_path: Path,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    processes: list[_Process] = []
    events: list[tuple[str, TerraformRuntimeState, int]] = []

    def factory(argv: list[str], **kwargs: object) -> _Process:
        process = _Process(argv, polls_before_exit=100, **cast_kwargs(kwargs))
        processes.append(process)
        return process

    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/terraform",
        process_factory=factory,
        heartbeat_seconds=0.5,
        poll_seconds=0.1,
        monotonic=_Clock(),
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(AzureContainerAppTerraformPhaseError) as captured:
        executor.run(
            phase="destroy",
            terraform_dir=terraform_dir,
            plan_file=plan_file,
            json_file=json_file,
            allowed_root=allowed_root,
            environment=_environment(),
            timeout_seconds=1,
            progress=lambda phase, state, elapsed: events.append(
                (phase, state, elapsed)
            ),
        )

    assert captured.value.phase == "destroy"
    assert processes[0].terminated is True
    assert events[-1][1] is TerraformRuntimeState.FAILED
    assert SECRET not in repr(captured.value)
    assert SECRET not in repr(events)


def test_executor_refuses_unowned_paths_and_invalid_environment_before_process(
    tmp_path: Path,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    outside = tmp_path / ("b" * 24)
    outside.mkdir()
    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/tofu",
        process_factory=lambda *_args, **_kwargs: pytest.fail(
            "unowned input must not start a subprocess"
        ),
    )

    for directory, environment in (
        (outside, _environment()),
        (terraform_dir, {**_environment(), "BAD\x00KEY": "value"}),
        (terraform_dir, {**_environment(), "BAD": "value\x00suffix"}),
    ):
        with pytest.raises(AzureContainerAppTerraformPhaseError):
            executor.run(
                phase="destroy",
                terraform_dir=directory,
                plan_file=(directory / plan_file.name),
                json_file=(directory / json_file.name),
                allowed_root=allowed_root,
                environment=environment,
                timeout_seconds=30,
                progress=lambda _phase, _state, _elapsed: None,
            )


def test_nonzero_provider_exit_never_surfaces_provider_or_secret_text(
    tmp_path: Path,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/tofu",
        process_factory=lambda argv, **kwargs: _Process(
            argv,
            returncode=17,
            **cast_kwargs(kwargs),
        ),
    )

    with pytest.raises(AzureContainerAppTerraformPhaseError) as captured:
        executor.run(
            phase="destroy",
            terraform_dir=terraform_dir,
            plan_file=plan_file,
            json_file=json_file,
            allowed_root=allowed_root,
            environment=_environment(),
            timeout_seconds=30,
            progress=lambda _phase, _state, _elapsed: None,
        )

    assert repr(captured.value) == (
        "AzureContainerAppTerraformPhaseError('Azure Container App Terraform "
        "phase failed: destroy')"
    )
    assert SECRET not in repr(captured.value)


@pytest.mark.parametrize(
    ("provider_output", "failure_class"),
    [
        ("ERROR CODE: AuthorizationFailed", "authorization"),
        ("MissingSubscriptionRegistration", "provider-registration"),
        ("ResourceGroupNotFound", "resource-group-not-found"),
        ("QuotaExceeded", "quota"),
        ("InvalidWorkloadProfileType", "workload-profile"),
        ("LocationNotAvailableForResourceType", "location"),
        ("unclassified provider failure", "provider"),
    ],
)
def test_nonzero_provider_exit_emits_only_a_fixed_failure_class(
    tmp_path: Path,
    provider_output: str,
    failure_class: str,
) -> None:
    allowed_root, terraform_dir, plan_file, json_file = _owned_root(tmp_path)
    plan_file.write_bytes(b"saved-plan")
    events: list[tuple[str, TerraformRuntimeState, int]] = []
    executor = AzureContainerAppTerraformPhaseExecutor(
        binary_resolver=lambda: "/opt/gludd/bin/tofu",
        process_factory=lambda argv, **kwargs: _Process(
            argv,
            returncode=1,
            output=f"{provider_output} {SECRET}",
            **cast_kwargs(kwargs),
        ),
    )

    with pytest.raises(AzureContainerAppTerraformPhaseError) as captured:
        executor.run(
            phase="apply",
            terraform_dir=terraform_dir,
            plan_file=plan_file,
            json_file=json_file,
            allowed_root=allowed_root,
            environment=_environment(),
            timeout_seconds=30,
            progress=lambda phase, state, elapsed: events.append(
                (phase, state, elapsed)
            ),
        )

    assert captured.value.failure_class == failure_class
    assert events[-1][0] == f"apply:{failure_class}"
    assert events[-1][1] is TerraformRuntimeState.FAILED
    assert SECRET not in repr(captured.value)
    assert SECRET not in repr(events)


def cast_kwargs(values: dict[str, object]) -> dict[str, Any]:
    return values
