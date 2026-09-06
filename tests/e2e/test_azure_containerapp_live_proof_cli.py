"""End-to-end CLI tests for hermetic and injected-live Azure GPU proofs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from scripts import azure_containerapp_live_proof as live_cli
from scripts.azure_containerapp_live_proof import main

from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofPolicy,
)
from general_ludd.infra.azure_containerapp_make_runtime import (
    MakeRuntimeEvent,
    MakeRuntimeState,
)
from general_ludd.infra.azure_containerapp_preflight import PreflightTrace
from general_ludd.self_improve.azure_backend import AzureCandidateResponse
from general_ludd.self_improve.azure_containerapp_backend import (
    ContainerAppBackendTrace,
    ContainerAppTraceEvent,
)
from general_ludd.self_improve.model_candidates import AzureContainerAppCandidateIdentity

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
PROMPT_TEXT = "Suggest one deterministic edge-case test for a public Python function."


def _argv(project: Path, *, live: int, acknowledgement: str = "") -> list[str]:
    return [
        "--auth-file",
        str(project / "absent-auth.json"),
        "--subscription-id",
        SUBSCRIPTION,
        "--resource-group",
        "gludd-models-eastus",
        "--environment",
        "gludd-gpu-environment",
        "--workload-profile-name",
        "gpu-t4",
        "--location",
        "eastus",
        "--allowed-cidr",
        "203.0.113.7/32",
        "--max-cost-usd",
        "5",
        "--ttl-minutes",
        "60",
        "--live",
        str(live),
        "--acknowledgement",
        acknowledgement,
        "--project-root",
        str(project),
        "--source-path",
        "src/public.py",
    ]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "application"
    source = project / "src" / "public.py"
    source.parent.mkdir(parents=True)
    source.write_text("def public(value: str) -> str:\n    return value.strip()\n")
    return project


def _live_inputs(
    project: Path,
) -> tuple[Any, AzureContainerAppLiveProofPolicy, Any]:
    args = live_cli._parser().parse_args(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        )
    )
    return (
        args,
        live_cli._policy(args, app_name="gludd-vllm-proof-abc123abc123"),
        live_cli._requirement(),
    )


def _plan(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "module.vllm_server.azapi_resource.vllm",
                "mode": "managed",
                "type": "azapi_resource",
                "name": "vllm",
                "provider_name": "registry.terraform.io/azure/azapi",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {
                        "type": "Microsoft.App/containerApps@2025-01-01",
                        "name": policy.app_name,
                        "parent_id": policy.resource_group_id,
                        "location": policy.location,
                        "body": {
                            "properties": {
                                "managedEnvironmentId": policy.environment_id,
                                "workloadProfileName": policy.workload_profile_name,
                                "configuration": {
                                    "activeRevisionsMode": "Single",
                                    "ingress": {
                                        "external": True,
                                        "allowInsecure": False,
                                        "targetPort": 8000,
                                        "transport": "auto",
                                        "ipSecurityRestrictions": [
                                            {
                                                "action": "Allow",
                                                "description": "Exact Gludd live-proof caller",
                                                "ipAddressRange": policy.allowed_cidr,
                                                "name": "gludd-live-proof-client",
                                            }
                                        ],
                                    },
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
                            }
                        },
                    },
                },
            }
        ],
    }


class _Runtime:
    def __init__(self, *, fail_work: bool = False, remains: bool = False) -> None:
        self.policy: AzureContainerAppLiveProofPolicy | None = None
        self.calls: list[str] = []
        self.fail_work = fail_work
        self.remains = remains

    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        self.policy = policy
        self.calls.append("plan")
        return _plan(policy)

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        assert policy is self.policy
        self.calls.append("preflight")

    def apply(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppDeploymentEvidence:
        assert policy is self.policy
        self.calls.append("apply")
        return AzureContainerAppDeploymentEvidence(
            resource_id=policy.expected_resource_id,
            cleanup_resource_id=policy.expected_resource_id,
            endpoint=(
                f"https://{policy.app_name}.kindstone.eastus."
                "azurecontainerapps.io"
            ),
            revision_name=f"{policy.app_name}--0000007",
            workload_profile_type=policy.workload_profile_type,
        )

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        assert policy is self.policy
        self.calls.append("destroy")

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        assert policy is self.policy
        self.calls.append("exists")
        return self.remains


class _Backend:
    def __init__(
        self,
        identity: AzureContainerAppCandidateIdentity,
        runtime: _Runtime,
    ) -> None:
        self.candidate_identity = identity
        self.runtime = runtime
        self.calls = 0
        self.close_calls = 0

    def generate(
        self,
        request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        del request, max_output_tokens, timeout_seconds
        self.calls += 1
        if self.runtime.fail_work:
            raise RuntimeError("private remote failure")
        return AzureCandidateResponse(
            text="Exercise the empty-string branch.",
            input_tokens=12,
            output_tokens=7,
            total_tokens=19,
        )

    def close(self) -> None:
        self.close_calls += 1


class _Resources:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.backend: _Backend | None = None
        self.close_calls = 0

    def backend_factory(
        self,
        identity: AzureContainerAppCandidateIdentity,
    ) -> _Backend:
        self.backend = _Backend(identity, self.runtime)
        return self.backend

    def close(self) -> None:
        self.close_calls += 1


def test_hermetic_dry_run_needs_no_credential_or_live_factory(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)

    result = main(
        _argv(project, live=0),
        live_resources_factory=lambda *_args: (_ for _ in ()).throw(
            AssertionError("live resources must not be created")
        ),
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "azure_containerapp_dry_run_completed" in captured.out
    assert "deployment_created=false" in captured.out
    assert SUBSCRIPTION not in captured.out + captured.err
    assert PROMPT_TEXT not in captured.out + captured.err


def test_injected_live_cli_runs_one_request_then_verified_cleanup(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    resources = _Resources(_Runtime())

    result = main(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        ),
        live_resources_factory=lambda *_args: resources,
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 0
    assert resources.runtime.calls == [
        "plan",
        "preflight",
        "apply",
        "destroy",
        "exists",
    ]
    assert resources.backend is not None
    assert resources.backend.calls == 1
    assert resources.backend.close_calls == 1
    assert resources.close_calls == 1
    assert "work_completed=true" in captured.out
    assert "cleanup_verified=true" in captured.out
    assert "Exercise the empty-string branch" not in captured.out
    assert SUBSCRIPTION not in captured.out + captured.err


def test_remote_failure_is_censored_but_still_cleans_up(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    resources = _Resources(_Runtime(fail_work=True))

    result = main(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        ),
        live_resources_factory=lambda *_args: resources,
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 2
    assert resources.runtime.calls[-2:] == ["destroy", "exists"]
    assert resources.close_calls == 1
    assert "private remote failure" not in captured.out + captured.err


def test_cleanup_presence_is_terminal_and_never_claimed_complete(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    resources = _Resources(_Runtime(remains=True))

    result = main(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        ),
        live_resources_factory=lambda *_args: resources,
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "cleanup_verified=true" not in captured.out


def test_project_private_source_blocks_before_live_resource_construction(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    policy_dir = project / ".gludd"
    policy_dir.mkdir()
    (policy_dir / "self-improve-policy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_access": "public",
                "private_paths": ["src/public.py"],
                "public_paths": [],
            }
        ),
        encoding="utf-8",
    )
    called = False

    def factory(*_args: object) -> _Resources:
        nonlocal called
        called = True
        return _Resources(_Runtime())

    result = main(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        ),
        live_resources_factory=factory,
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 2
    assert called is False
    assert "src/public.py" not in captured.out + captured.err
    assert "return value.strip" not in captured.out + captured.err


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (None, False),
        ([], False),
        ({}, False),
        ({"properties": None}, False),
        (
            {
                "properties": {
                    "provisioningState": "Updating",
                    "latestReadyRevisionName": "proof--0000001",
                }
            },
            False,
        ),
        ({"properties": {"provisioningState": "Succeeded"}}, False),
        (
            {
                "properties": {
                    "provisioningState": "Succeeded",
                    "latestReadyRevisionName": "proof--0000001",
                }
            },
            True,
        ),
    ],
)
def test_ready_requires_succeeded_state_and_exact_revision(
    document: object | None,
    expected: bool,
) -> None:
    assert live_cli._ready(document) is expected


@pytest.mark.parametrize("method", ["preflight", "apply", "destroy", "exists"])
def test_hermetic_runtime_refuses_every_live_operation(method: str) -> None:
    runtime = live_cli._HermeticRuntime()
    policy = cast(AzureContainerAppLiveProofPolicy, object())

    with pytest.raises(RuntimeError, match=r"dry-run .* is unreachable"):
        getattr(runtime, method)(policy)


def test_credential_client_uses_fixed_public_cloud_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClientSecretCredential:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def get_token(self, *scopes: str) -> object:
            return SimpleNamespace(token="unused", scopes=scopes)

        def close(self) -> None:
            captured["closed"] = True

    identity_module = ModuleType("azure.identity")
    identity_module.ClientSecretCredential = FakeClientSecretCredential
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)
    credentials = AzureAcceleratorCredentials(
        client_id="client-id",
        client_secret="unit-secret",
        subscription_id=SUBSCRIPTION,
        tenant_id="tenant-id",
    )

    client = live_cli._credential_client(credentials)
    client.close()

    assert captured == {
        "tenant_id": "tenant-id",
        "client_id": "client-id",
        "client_secret": "unit-secret",
        "authority": "login.microsoftonline.com",
        "disable_instance_discovery": True,
        "retry_total": 0,
        "closed": True,
    }


def test_default_live_resources_wire_preflight_polling_backend_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    credentials = AzureAcceleratorCredentials(
        client_id="client-id",
        client_secret="unit-secret",
        subscription_id=SUBSCRIPTION,
        tenant_id="tenant-id",
    )
    lifecycle: list[str] = []
    token_scopes: list[tuple[str, ...]] = []
    transport_arguments: list[tuple[str, dict[str, object]]] = []
    preflight_checks: list[dict[str, object]] = []
    runtime_arguments: dict[str, object] = {}
    backend_arguments: dict[str, object] = {}
    sleep_seconds: list[float] = []
    ready_document = {
        "properties": {
            "provisioningState": "Succeeded",
            "latestReadyRevisionName": f"{policy.app_name}--0000007",
        }
    }
    app_documents: list[object | None] = [
        {"properties": {"provisioningState": "Updating"}},
        ready_document,
        ready_document,
        None,
    ]

    class FakeCredential:
        def get_token(self, *scopes: str) -> object:
            token_scopes.append(scopes)
            return SimpleNamespace(token="unit-token")

        def close(self) -> None:
            lifecycle.append("credential.close")

    credential = FakeCredential()

    class FakeEnvironmentTransport:
        def __init__(self, **kwargs: object) -> None:
            transport_arguments.append(("environment", dict(kwargs)))

        def close(self) -> None:
            lifecycle.append("environment.close")

    class FakeAppTransport:
        def __init__(self, **kwargs: object) -> None:
            transport_arguments.append(("app", dict(kwargs)))

        def get_json(self, token: str) -> object | None:
            assert token == "unit-token"
            return app_documents.pop(0)

        def close(self) -> None:
            lifecycle.append("app.close")

    class FakePreflight:
        def __init__(
            self,
            active_credential: object,
            active_transport: object,
            *,
            trace_sink: Any,
        ) -> None:
            assert active_credential is credential
            assert isinstance(active_transport, FakeEnvironmentTransport)
            trace_sink(
                PreflightTrace(
                    phase="authentication_started",
                    location="eastus",
                    profile_name="gpu-t4",
                )
            )

        def check(self, **kwargs: object) -> None:
            preflight_checks.append(dict(kwargs))

    class FakeRuntime:
        def __init__(self, **kwargs: object) -> None:
            runtime_arguments.update(kwargs)
            kwargs["trace_sink"](
                MakeRuntimeEvent(
                    phase="init",
                    state=MakeRuntimeState.STARTED,
                    operation_digest="a" * 64,
                )
            )

    backend = object()

    def build_backend(
        identity: AzureContainerAppCandidateIdentity,
        *,
        discovery_timeout_seconds: float,
        trace_sink: Any,
    ) -> object:
        backend_arguments.update(
            identity=identity,
            discovery_timeout_seconds=discovery_timeout_seconds,
        )
        trace_sink(
            ContainerAppBackendTrace(event=ContainerAppTraceEvent.DISCOVERY_STARTED)
        )
        return backend

    monkeypatch.setattr(
        live_cli,
        "load_azure_accelerator_credentials",
        lambda path, *, expected_subscription_id: (
            credentials
            if (path, expected_subscription_id)
            == (str(project / "absent-auth.json"), SUBSCRIPTION)
            else (_ for _ in ()).throw(AssertionError("unexpected credential request"))
        ),
    )
    monkeypatch.setattr(live_cli, "_credential_client", lambda value: credential)
    monkeypatch.setattr(live_cli, "HttpxARMJSONTransport", FakeEnvironmentTransport)
    monkeypatch.setattr(live_cli, "HttpxContainerAppARMTransport", FakeAppTransport)
    monkeypatch.setattr(live_cli, "AzureContainerAppReadOnlyPreflight", FakePreflight)
    monkeypatch.setattr(live_cli, "AzureContainerAppMakeRuntime", FakeRuntime)
    monkeypatch.setattr(live_cli, "build_azure_containerapp_candidate_backend", build_backend)
    monkeypatch.setattr(live_cli.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        live_cli.time,
        "sleep",
        lambda seconds: sleep_seconds.append(seconds),
    )

    resources = live_cli._default_live_resources(args, policy, requirement)
    runtime_arguments["preflight_check"](policy, requirement)
    assert runtime_arguments["read_app"](policy, False) == ready_document
    assert runtime_arguments["read_app"](policy, True) is None
    timeout_clock = iter((0.0, 901.0))
    app_documents.append({"properties": {"provisioningState": "Updating"}})
    monkeypatch.setattr(live_cli.time, "monotonic", lambda: next(timeout_clock))
    assert runtime_arguments["read_app"](policy, False) == {
        "properties": {"provisioningState": "Updating"}
    }

    identity = AzureContainerAppCandidateIdentity(
        endpoint=(
            f"https://{policy.app_name}.kindstone.eastus.azurecontainerapps.io"
        ),
        resource_id=policy.expected_resource_id,
        revision_name=f"{policy.app_name}--0000007",
        image_digest=policy.container_image.rsplit("@", maxsplit=1)[1],
        model_name=policy.model_name,
        model_revision=policy.model_revision,
        workload_profile_type=policy.workload_profile_type,
    )
    assert resources.backend_factory(identity) is backend
    resources.close()
    resources.close()

    output = capsys.readouterr().out
    assert "AZURE_CONTAINERAPP_PREFLIGHT_TRACE" in output
    assert "AZURE_CONTAINERAPP_MAKE_TRACE" in output
    assert "AZURE_CONTAINERAPP_BACKEND_TRACE" in output
    assert "AZURE_CONTAINERAPP_ARM_TRACE phase=readiness state=heartbeat" in output
    assert "AZURE_CONTAINERAPP_ARM_TRACE phase=absence state=heartbeat" in output
    assert "unit-token" not in output
    assert "unit-secret" not in output
    assert transport_arguments == [
        (
            "environment",
            {
                "subscription_id": SUBSCRIPTION,
                "resource_group": policy.resource_group,
                "environment_name": policy.environment_name,
            },
        ),
        (
            "app",
            {
                "subscription_id": SUBSCRIPTION,
                "resource_group": policy.resource_group,
                "app_name": policy.app_name,
            },
        ),
    ]
    assert preflight_checks == [
        {
            "subscription_id": SUBSCRIPTION,
            "resource_group": policy.resource_group,
            "environment_name": policy.environment_name,
            "workload_profile_name": policy.workload_profile_name,
            "location": policy.location,
            "requirement": requirement,
        }
    ]
    assert runtime_arguments["repo_root"] == live_cli._REPO_ROOT
    assert runtime_arguments["work_root"] == live_cli._WORK_ROOT
    assert runtime_arguments["credentials"] is credentials
    assert runtime_arguments["requirement"] is requirement
    assert token_scopes == [(live_cli.ARM_SCOPE,)] * 5
    assert sleep_seconds == [10.0, 10.0]
    assert backend_arguments == {
        "identity": identity,
        "discovery_timeout_seconds": 120.0,
    }
    assert lifecycle == ["app.close", "environment.close", "credential.close"]


def test_default_resource_construction_failure_closes_every_created_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    credentials = AzureAcceleratorCredentials(
        client_id="client-id",
        client_secret="unit-secret",
        subscription_id=SUBSCRIPTION,
        tenant_id="tenant-id",
    )
    lifecycle: list[str] = []

    class FakeCredential:
        def close(self) -> None:
            lifecycle.append("credential.close")

    class FakeEnvironmentTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            lifecycle.append("environment.close")
            raise RuntimeError("private environment close failure")

    class FakeAppTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            lifecycle.append("app.close")

    monkeypatch.setattr(
        live_cli,
        "load_azure_accelerator_credentials",
        lambda *_args, **_kwargs: credentials,
    )
    monkeypatch.setattr(live_cli, "_credential_client", lambda _value: FakeCredential())
    monkeypatch.setattr(live_cli, "HttpxARMJSONTransport", FakeEnvironmentTransport)
    monkeypatch.setattr(live_cli, "HttpxContainerAppARMTransport", FakeAppTransport)
    monkeypatch.setattr(
        live_cli,
        "AzureContainerAppMakeRuntime",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("constructor failed")),
    )

    with pytest.raises(RuntimeError, match="constructor failed"):
        live_cli._default_live_resources(args, policy, requirement)

    assert lifecycle == ["app.close", "environment.close", "credential.close"]


def test_default_resource_cleanup_is_idempotent_and_reports_any_close_failure() -> None:
    lifecycle: list[str] = []

    class Client:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def close(self) -> None:
            lifecycle.append(self.name)
            if self.fail:
                raise RuntimeError("private close detail")

    resources = live_cli._DefaultResources(
        runtime=cast(Any, object()),
        credential=cast(Any, Client("credential")),
        environment_transport=cast(Any, Client("environment", fail=True)),
        app_transport=cast(Any, Client("app")),
    )

    with pytest.raises(RuntimeError, match="Azure live resource cleanup failed"):
        resources.close()
    resources.close()

    assert lifecycle == ["app", "environment", "credential"]


def test_live_cli_treats_client_cleanup_failure_as_terminal_and_censors_detail(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)

    class CleanupFailResources(_Resources):
        def close(self) -> None:
            super().close()
            raise RuntimeError("private client cleanup detail")

    resources = CleanupFailResources(_Runtime())

    result = main(
        _argv(
            project,
            live=1,
            acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
        ),
        live_resources_factory=lambda *_args: resources,
        token_hex=lambda _count: "abc123abc123",
    )

    captured = capsys.readouterr()
    assert result == 2
    assert resources.close_calls == 1
    assert "reason=client-cleanup" in captured.err
    assert "private client cleanup detail" not in captured.out + captured.err


def test_public_make_target_is_ci_safe_and_contract_tracked() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/build.yml").read_text(encoding="utf-8")
    contract = json.loads(
        (root / "config/make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "azure-containerapp-live-proof:" in makefile
    assert "scripts/azure_containerapp_live_proof.py" in makefile
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE" in makefile
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "azure-containerapp-live-proof"
    )
    assert entry["make_variables"] == [
        "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE",
        "AZURE_CONTAINERAPP_LIVE_PROOF_SUBSCRIPTION_ID",
        "AZURE_CONTAINERAPP_LIVE_PROOF_RESOURCE_GROUP",
        "AZURE_CONTAINERAPP_LIVE_PROOF_ENVIRONMENT",
        "AZURE_CONTAINERAPP_LIVE_PROOF_WORKLOAD_PROFILE_NAME",
        "AZURE_CONTAINERAPP_LIVE_PROOF_LOCATION",
        "AZURE_CONTAINERAPP_LIVE_PROOF_ALLOWED_CIDR",
        "AZURE_CONTAINERAPP_LIVE_PROOF_MAX_COST_USD",
        "AZURE_CONTAINERAPP_LIVE_PROOF_TTL_MINUTES",
        "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE",
        "AZURE_CONTAINERAPP_LIVE_PROOF_ACKNOWLEDGEMENT",
        "AZURE_CONTAINERAPP_LIVE_PROOF_PROJECT_ROOT",
        "AZURE_CONTAINERAPP_LIVE_PROOF_SOURCE_PATH",
    ]
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE=0" in entry["behavior"]
    assert "Azure Container App proof contract (hermetic)" in workflow
    assert "make azure-containerapp-live-proof" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE=0" in workflow
    assert "secrets.AZURE" not in workflow


def test_azure_containerapp_coverage_has_one_local_and_hosted_contract() -> None:
    """The branch-aware Azure profile must run identically locally and in GHA."""
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    workflow = (root / ".github/workflows/build.yml").read_text(encoding="utf-8")
    contract = json.loads(
        (root / "config/make_target_contract.json").read_text(encoding="utf-8")
    )

    assert "test-azure-containerapp-coverage:" in makefile
    recipe = makefile.split("test-azure-containerapp-coverage:", 1)[1].split(
        "\n\n", 1
    )[0]
    assert "coverage-files" in recipe
    assert "config/coverage_azure_containerapp.ini" in recipe
    assert "COVERAGE_AGGREGATE_MIN=85" in recipe
    assert "COVERAGE_PER_FILE_MIN=75" in recipe
    entry = next(
        target
        for target in contract["targets"]
        if target["name"] == "test-azure-containerapp-coverage"
    )
    assert entry == {
        "name": "test-azure-containerapp-coverage",
        "make_variables": [],
        "behavior": "make test-azure-containerapp-coverage",
    }
    assert "Azure Container App coverage (hermetic)" in workflow
    hosted = workflow.split("Azure Container App coverage (hermetic)", 1)[1].split(
        "- name:", 1
    )[0]
    assert "matrix.python-version == '3.11'" in hosted
    assert "make test-azure-containerapp-coverage" in hosted
    assert "secrets.AZURE" not in hosted
