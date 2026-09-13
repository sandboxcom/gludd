"""End-to-end CLI tests for hermetic and injected-live Azure GPU proofs."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from scripts import azure_containerapp_live_proof as live_cli
from scripts.azure_containerapp_live_proof import main

from general_ludd.azure.accelerator_credential_source import (
    AzureAcceleratorCredentialLease,
)
from general_ludd.azure.accelerator_credentials import AzureAcceleratorCredentials
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
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
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendFailure,
    BackendInfrastructureError,
)

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


def _environment_plan(
    policy: AzureEnvironmentLifecyclePolicy,
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
                    "actions": ["create"],
                    "after": {
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
                                        "workloadProfileType": (
                                            profile.workload_profile_type
                                        ),
                                    }
                                    for profile in policy.profiles
                                ]
                            }
                        },
                    },
                    "after_unknown": {},
                },
            }
        ]
    }


class _EnvironmentRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.document: object | None = None

    def read_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        expect_absent: bool,
    ) -> object | None:
        del policy, expect_absent
        self.calls.append("read")
        return self.document

    def plan(self, policy: AzureEnvironmentLifecyclePolicy) -> object:
        self.calls.append("plan")
        return _environment_plan(policy)

    def apply(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        self.calls.append("apply")
        self.document = {
            "id": policy.environment_id,
            "name": policy.environment_name,
            "type": "Microsoft.App/managedEnvironments",
            "location": policy.location,
            "tags": policy.ownership_tags,
            "properties": {
                "provisioningState": "Succeeded",
                "workloadProfiles": [
                    {"name": "Consumption", "workloadProfileType": "Consumption"},
                    *[
                        {
                            "name": profile.profile_name,
                            "workloadProfileType": profile.workload_profile_type,
                        }
                        for profile in policy.profiles
                    ],
                ],
            },
        }

    def list_environment_apps(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        del policy
        self.calls.append("inventory")
        return ()

    def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        del policy
        self.calls.append("destroy")
        self.document = None


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
        self.environment_runtime = _EnvironmentRuntime()
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


def test_environment_policy_binds_owner_without_exposing_project_path(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    args, app_policy, _requirement = _live_inputs(project)
    del args, _requirement
    guard = live_cli.SelfImproveRuntimePolicyGuard.load(
        project,
        lambda _event: None,
        live_cli.AzurePromptApprovalError,
    )

    policy = live_cli._environment_policy(
        app_policy,
        guard=guard,
        project_root=project,
        now=datetime(2026, 9, 6, 19, 5, 7, 987654, tzinfo=UTC),
    )
    repeated = live_cli._environment_policy(
        app_policy,
        guard=guard,
        project_root=project,
        now=datetime(2026, 9, 6, 19, 5, 7, 987654, tzinfo=UTC),
    )

    assert policy == repeated
    assert policy.profiles[0].profile_name == app_policy.workload_profile_name
    assert policy.profiles[0].workload_profile_type == app_policy.workload_profile_type
    assert policy.plan_digest == app_policy.operation_digest
    assert policy.expires_at_utc == "2026-09-06T20:05:07Z"
    assert len(policy.owner_digest) == 64
    assert str(project) not in repr(policy)

    with pytest.raises(ValueError, match="timezone-aware"):
        live_cli._environment_policy(
            app_policy,
            guard=guard,
            project_root=project,
            now=datetime(2026, 9, 6, 19, 5, 7),
        )


@pytest.mark.parametrize(
    "source",
    [
        object(),
        SimpleNamespace(acquire=lambda: None),
    ],
    ids=("missing-acquire-and-release", "missing-release"),
)
def test_openbao_factory_rejects_incomplete_lease_boundaries(source: object) -> None:
    with pytest.raises(ValueError, match="exactly revoke"):
        live_cli.build_openbao_live_resources_factory(cast(Any, source))


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
    assert resources.environment_runtime.calls == [
        "read",
        "plan",
        "apply",
        "read",
        "read",
        "inventory",
        "destroy",
        "read",
    ]
    assert resources.backend is not None
    assert resources.backend.calls == 1
    assert resources.backend.close_calls == 1
    assert resources.close_calls == 1
    assert "work_completed=true" in captured.out
    assert "cleanup_verified=true" in captured.out
    assert "environment_managed=true" in captured.out
    assert "AZURE_CONTAINERAPP_ENVIRONMENT_LIFECYCLE_TRACE" in captured.out
    assert "Exercise the empty-string branch" not in captured.out
    assert SUBSCRIPTION not in captured.out + captured.err


def test_injected_live_cli_can_retain_zero_cost_environment_for_retry(
    tmp_path: Path,
    capsys: Any,
) -> None:
    project = _project(tmp_path)
    resources = _Resources(_Runtime())
    argv = _argv(
        project,
        live=1,
        acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
    )
    argv.extend(
        [
            "--idle-retention-preset",
            "zero_cost_only",
            "--idle-retention-seconds",
            "3600",
        ]
    )

    result = main(
        argv,
        live_resources_factory=lambda *_args: resources,
        token_hex=lambda _count: "abc123abc123",
        now=lambda: datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )

    captured = capsys.readouterr()
    assert result == 0
    assert resources.runtime.calls[-2:] == ["destroy", "exists"]
    assert resources.environment_runtime.calls[-2:] == ["read", "inventory"]
    assert resources.environment_runtime.document is not None
    assert "AZURE_CONTAINERAPP_RETENTION_TRACE" in captured.out
    assert "retention_preset=zero_cost_only" in captured.out


def test_live_policy_auto_cidr_reuses_bounded_public_ipv4_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    argv = _argv(
        project,
        live=1,
        acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
    )
    argv[argv.index("203.0.113.7/32")] = "auto"
    calls: list[str] = []

    def discover() -> str:
        calls.append("discover")
        return "8.8.8.8/32"

    monkeypatch.setattr(
        live_cli,
        "resolve_public_ipv4_cidr",
        discover,
        raising=False,
    )
    args = live_cli._parser().parse_args(argv)

    policy = live_cli._policy(args, app_name="gludd-vllm-proof-abc123abc123")

    assert policy.allowed_cidr == "8.8.8.8/32"
    assert policy.min_replicas == 1
    assert calls == ["discover"]


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


def test_cli_preserves_fixed_preflight_failure_detail_without_provider_text(
    tmp_path: Path,
    capsys: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    resources = _Resources(_Runtime())

    def fail_with_safe_detail(*_args: object, **_kwargs: object) -> None:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.PREFLIGHT,
            detail="runtime_sizing",
        )

    monkeypatch.setattr(
        live_cli,
        "run_owned_azure_containerapp_live_proof",
        fail_with_safe_detail,
    )

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
    assert "reason=preflight" in captured.err
    assert "detail=runtime_sizing" in captured.err
    assert "private" not in captured.err


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


@pytest.mark.parametrize(
    "credentials",
    [
        object(),
        AzureAcceleratorCredentials(
            client_id="99999999-8888-7777-6666-555555555555",
            client_secret="unit-secret",
            subscription_id="22222222-3333-4444-5555-666666666666",
            tenant_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
    ],
    ids=("wrong-type", "wrong-subscription"),
)
def test_default_live_resources_rejects_unapproved_credentials_before_clients(
    tmp_path: Path,
    credentials: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        live_cli,
        "build_azure_containerapp_runtime_resources",
        lambda **_kwargs: pytest.fail("resource construction must remain unreachable"),
    )

    with pytest.raises(ValueError, match="approved subscription"):
        live_cli._default_live_resources(
            args,
            policy,
            requirement,
            environment_policy,
            credentials=cast(Any, credentials),
        )


def test_default_live_resources_bootstraps_owned_group_before_runtime_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    credentials = AzureAcceleratorCredentials(
        client_id="client-id",
        client_secret="unit-secret",
        subscription_id=SUBSCRIPTION,
        tenant_id="tenant-id",
    )
    calls: list[tuple[str, object]] = []
    resources = object()

    monkeypatch.setattr(
        live_cli,
        "load_azure_accelerator_credentials",
        lambda path, *, expected_subscription_id: credentials,
    )

    def bootstrap(group_policy: object, active_credentials: object, **_kwargs: object) -> None:
        assert active_credentials is credentials
        calls.append(("resource-group", group_policy))

    monkeypatch.setattr(
        live_cli,
        "ensure_azure_resource_group",
        bootstrap,
        raising=False,
    )
    monkeypatch.setattr(
        live_cli,
        "build_azure_containerapp_runtime_resources",
        lambda **_kwargs: calls.append(("runtime", policy)) or resources,
    )

    observed = live_cli._default_live_resources(
        args,
        policy,
        requirement,
        environment_policy=environment_policy,
    )

    assert observed is resources
    assert [name for name, _value in calls] == ["resource-group", "runtime"]
    group_policy = calls[0][1]
    assert group_policy.subscription_id == SUBSCRIPTION
    assert group_policy.resource_group == policy.resource_group
    assert group_policy.location == policy.location
    assert group_policy.owner_digest == environment_policy.owner_digest


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
    environment_runtime_arguments: dict[str, object] = {}
    backend_arguments: dict[str, object] = {}
    sdk_arguments: list[tuple[str, object]] = []
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
        ready_document,
        None,
    ]
    revision_documents = [
        {
            "properties": {
                "active": True,
                "replicas": 0,
                "healthState": "None",
                "provisioningState": "Provisioning",
                "runningState": "Processing",
            }
        },
        {
            "properties": {
                "active": True,
                "replicas": 1,
                "healthState": "Healthy",
                "provisioningState": "Provisioned",
                "runningState": "Running",
            }
        },
    ]
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    environment_documents: list[object | None] = [
        None,
        {"properties": {"provisioningState": "Updating"}},
        {"properties": {"provisioningState": "Succeeded"}},
        {"properties": {"provisioningState": "Succeeded"}},
        None,
    ]
    expected_app_id = policy.expected_resource_id
    foreign_app_id = (
        f"{policy.resource_group_id}/providers/Microsoft.App/containerApps/foreign-app"
    )
    environment_inventories: list[tuple[str, ...]] = [
        (expected_app_id,),
        (),
        (foreign_app_id,),
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

        def get_revision_json(self, token: str, revision_name: str) -> object:
            assert token == "unit-token"
            assert revision_name == f"{policy.app_name}--0000007"
            return revision_documents.pop(0)

        def close(self) -> None:
            lifecycle.append("app.close")

    class FakeLifecycleTransport:
        def __init__(self, **kwargs: object) -> None:
            transport_arguments.append(("lifecycle", dict(kwargs)))

        def get_environment(self, token: str) -> object | None:
            assert token == "unit-token"
            return environment_documents.pop(0)

        def list_environment_app_ids(self, token: str) -> tuple[str, ...]:
            assert token == "unit-token"
            return environment_inventories.pop(0)

        def close(self) -> None:
            lifecycle.append("lifecycle.close")

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

    class FakeEnvironmentRuntime:
        def __init__(self, **kwargs: object) -> None:
            environment_runtime_arguments.update(kwargs)
            kwargs["trace_sink"](
                MakeRuntimeEvent(
                    phase="init",
                    state=MakeRuntimeState.STARTED,
                    operation_digest="b" * 64,
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

    sdk_client = SimpleNamespace(name="shared-container-apps-sdk-client")

    def build_sdk_client(active_credential: object, subscription_id: str) -> object:
        sdk_arguments.append(("client", (active_credential, subscription_id)))
        return sdk_client

    def build_sdk_views(*, client: object, policy: object) -> object:
        sdk_arguments.append(("views", {"client": client, "policy": policy}))
        return SimpleNamespace(
            preflight=FakeEnvironmentTransport(
                subscription_id=SUBSCRIPTION,
                resource_group=cast(Any, policy).resource_group,
                environment_name=cast(Any, policy).environment_name,
            ),
            lifecycle=FakeLifecycleTransport(
                subscription_id=SUBSCRIPTION,
                resource_group=cast(Any, policy).resource_group,
                environment_name=cast(Any, policy).environment_name,
            ),
            app=FakeAppTransport(
                subscription_id=SUBSCRIPTION,
                resource_group=cast(Any, policy).resource_group,
                app_name=cast(Any, policy).app_name,
            ),
        )

    monkeypatch.setattr(live_cli, "build_container_apps_sdk_client", build_sdk_client)
    monkeypatch.setattr(live_cli, "AzureContainerAppsSDKReadTransports", build_sdk_views)
    monkeypatch.setattr(live_cli, "AzureContainerAppReadOnlyPreflight", FakePreflight)
    monkeypatch.setattr(live_cli, "AzureContainerAppTerraformRuntime", FakeRuntime)
    monkeypatch.setattr(
        live_cli,
        "AzureContainerAppEnvironmentTerraformRuntime",
        FakeEnvironmentRuntime,
    )
    monkeypatch.setattr(live_cli, "build_azure_containerapp_candidate_backend", build_backend)
    monkeypatch.setattr(
        live_cli,
        "ensure_azure_resource_group",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(live_cli.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        live_cli.time,
        "sleep",
        lambda seconds: sleep_seconds.append(seconds),
    )

    resources = live_cli._default_live_resources(
        args,
        policy,
        requirement,
        environment_policy,
    )
    runtime_arguments["preflight_check"](policy, requirement)
    assert runtime_arguments["read_app"](policy, False) == ready_document
    assert runtime_arguments["read_app"](policy, True) is None
    assert environment_runtime_arguments["read_environment"](
        environment_policy, False
    ) is None
    assert environment_runtime_arguments["read_environment"](
        environment_policy, False
    ) == {"properties": {"provisioningState": "Succeeded"}}
    assert environment_runtime_arguments["read_environment"](
        environment_policy, True
    ) is None
    assert environment_runtime_arguments["list_environment_apps"](
        environment_policy
    ) == ()
    assert environment_runtime_arguments["list_environment_apps"](
        environment_policy
    ) == (foreign_app_id,)
    timeout_clock = iter((0.0, 901.0))
    app_documents.append({"properties": {"provisioningState": "Updating"}})
    monkeypatch.setattr(live_cli.time, "monotonic", lambda: next(timeout_clock))
    with pytest.raises(BackendInfrastructureError) as timeout:
        runtime_arguments["read_app"](policy, False)
    assert timeout.value.failure is BackendFailure.TIMEOUT

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
    assert "AZURE_CONTAINERAPP_TERRAFORM_TRACE" in output
    assert "AZURE_CONTAINERAPP_ENVIRONMENT_TERRAFORM_TRACE" in output
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
            "lifecycle",
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
    assert sdk_arguments == [
        ("client", (credential, SUBSCRIPTION)),
        ("views", {"client": sdk_client, "policy": policy}),
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
    assert "repo_root" not in runtime_arguments
    assert runtime_arguments["work_root"] == live_cli._WORK_ROOT
    assert runtime_arguments["credentials"] is credentials
    assert runtime_arguments["requirement"] is requirement
    assert "repo_root" not in environment_runtime_arguments
    assert (
        environment_runtime_arguments["work_root"]
        == live_cli._ENVIRONMENT_WORK_ROOT
    )
    assert environment_runtime_arguments["credentials"] is credentials
    assert token_scopes == [(live_cli.ARM_SCOPE,)] * 14
    assert sleep_seconds == [10.0] * 6
    assert backend_arguments == {
        "identity": identity,
        "discovery_timeout_seconds": 120.0,
    }
    assert lifecycle == [
        "app.close",
        "lifecycle.close",
        "environment.close",
        "credential.close",
    ]


def test_default_resource_construction_failure_closes_every_created_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
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

    class FakeLifecycleTransport:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            lifecycle.append("lifecycle.close")

    monkeypatch.setattr(
        live_cli,
        "load_azure_accelerator_credentials",
        lambda *_args, **_kwargs: credentials,
    )
    monkeypatch.setattr(live_cli, "_credential_client", lambda _value: FakeCredential())

    def build_sdk_views(**_kwargs: object) -> object:
        return SimpleNamespace(
            preflight=FakeEnvironmentTransport(),
            lifecycle=FakeLifecycleTransport(),
            app=FakeAppTransport(),
        )

    monkeypatch.setattr(
        live_cli,
        "build_container_apps_sdk_client",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(live_cli, "AzureContainerAppsSDKReadTransports", build_sdk_views)
    monkeypatch.setattr(
        live_cli,
        "AzureContainerAppTerraformRuntime",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("constructor failed")),
    )
    monkeypatch.setattr(
        live_cli,
        "ensure_azure_resource_group",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="constructor failed"):
        live_cli._default_live_resources(
            args,
            policy,
            requirement,
            environment_policy,
        )

    assert lifecycle == [
        "app.close",
        "lifecycle.close",
        "environment.close",
        "credential.close",
    ]


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
        environment_runtime=cast(Any, object()),
        credential=cast(Any, Client("credential")),
        environment_transport=cast(Any, Client("environment", fail=True)),
        lifecycle_transport=cast(Any, Client("lifecycle")),
        app_transport=cast(Any, Client("app")),
        credential_release=lambda: lifecycle.append("lease.release"),
    )

    with pytest.raises(RuntimeError, match="Azure live resource cleanup failed"):
        resources.close()
    resources.close()

    assert lifecycle == [
        "app",
        "lifecycle",
        "environment",
        "credential",
        "lease.release",
    ]


def test_openbao_resource_factory_acquires_one_lease_and_releases_it_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    credentials = AzureAcceleratorCredentials(
        client_id="33333333-4444-4555-8666-777777777777",
        client_secret="dynamic-secret-never-render",
        subscription_id=SUBSCRIPTION,
        tenant_id="22222222-3333-4444-8555-666666666666",
    )
    lease = AzureAcceleratorCredentialLease(
        credentials=credentials,
        lease_duration_seconds=600,
        renewable=False,
        _lease_id="azure/creds/gludd-accelerator/unit-lease",
    )
    lifecycle: list[str] = []
    captured: dict[str, object] = {}

    class Source:
        def acquire(self) -> AzureAcceleratorCredentialLease:
            lifecycle.append("lease.acquire")
            return lease

        def release(self, released: AzureAcceleratorCredentialLease) -> None:
            assert released is lease
            lifecycle.append("lease.release")

    class Resources:
        def close(self) -> None:
            lifecycle.append("resources.close")
            cast(Any, captured["credential_release"])()

    def build_resources(
        active_args: object,
        active_policy: object,
        active_requirement: object,
        active_environment_policy: object,
        *,
        credentials: object,
        credential_release: object,
    ) -> Resources:
        captured.update(
            args=active_args,
            policy=active_policy,
            requirement=active_requirement,
            environment_policy=active_environment_policy,
            credentials=credentials,
            credential_release=credential_release,
        )
        return Resources()

    monkeypatch.setattr(live_cli, "_default_live_resources", build_resources)

    resources = live_cli.build_openbao_live_resources_factory(cast(Any, Source()))(
        args,
        policy,
        requirement,
        environment_policy,
    )
    resources.close()

    assert captured["args"] is args
    assert captured["policy"] is policy
    assert captured["requirement"] is requirement
    assert captured["environment_policy"] is environment_policy
    assert captured["credentials"] is credentials
    assert lifecycle == ["lease.acquire", "resources.close", "lease.release"]


def test_openbao_resource_factory_revokes_lease_when_construction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    args, policy, requirement = _live_inputs(project)
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    credentials = AzureAcceleratorCredentials(
        client_id="33333333-4444-4555-8666-777777777777",
        client_secret="dynamic-secret-never-render",
        subscription_id=SUBSCRIPTION,
        tenant_id="22222222-3333-4444-8555-666666666666",
    )
    lease = AzureAcceleratorCredentialLease(
        credentials=credentials,
        lease_duration_seconds=600,
        renewable=False,
        _lease_id="azure/creds/gludd-accelerator/unit-lease",
    )
    releases: list[object] = []

    class Source:
        def acquire(self) -> AzureAcceleratorCredentialLease:
            return lease

        def release(self, released: AzureAcceleratorCredentialLease) -> None:
            releases.append(released)

    monkeypatch.setattr(
        live_cli,
        "_default_live_resources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private construction detail")
        ),
    )

    factory = live_cli.build_openbao_live_resources_factory(cast(Any, Source()))
    with pytest.raises(RuntimeError, match="private construction detail"):
        factory(args, policy, requirement, environment_policy)

    assert releases == [lease]


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
        "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE",
        "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_FILE",
        "AZURE_CONTAINERAPP_LIVE_PROOF_FEDERATED_TOKEN_FILE",
        "AZURE_CONTAINERAPP_LIVE_PROOF_CLIENT_ID",
        "AZURE_CONTAINERAPP_LIVE_PROOF_TENANT_ID",
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
        "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET",
        "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_SECONDS",
    ]
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_AUTH_MODE=file" in entry["behavior"]
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE=0" in entry["behavior"]
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET=always_destroy" in entry[
        "behavior"
    ]
    assert "Azure Container App proof contract (hermetic)" in workflow
    assert "make azure-containerapp-live-proof" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_LIVE=0" in workflow
    assert "AZURE_CONTAINERAPP_LIVE_PROOF_RETENTION_PRESET=always_destroy" in workflow
    assert "secrets.AZURE" not in workflow


def test_cli_builds_workload_identity_from_explicit_federated_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    token_file = tmp_path / "github-oidc.jwt"
    token_file.write_text("header.payload.signature", encoding="ascii")
    token_file.chmod(0o600)
    args, policy, requirement = _live_inputs(project)
    del args
    workload_args = live_cli._parser().parse_args(
        [
            *_argv(
                project,
                live=1,
                acknowledgement="DEPLOY_ONE_CONTAINER_APP_AND_DESTROY",
            )[2:],
            "--federated-token-file",
            str(token_file),
            "--azure-client-id",
            "33333333-4444-5555-6666-777777777777",
            "--azure-tenant-id",
            "22222222-3333-4444-5555-666666666666",
        ]
    )
    environment_policy = live_cli._environment_policy(
        policy,
        guard=cast(Any, SimpleNamespace(expected_digest="d" * 64)),
        project_root=project,
        now=datetime(2026, 9, 6, 19, 0, tzinfo=UTC),
    )
    expected_credentials = object()
    observed: dict[str, object] = {}

    def build(**kwargs: object) -> object:
        observed.update(kwargs)
        return expected_credentials

    monkeypatch.setattr(live_cli, "build_azure_workload_identity", build)
    monkeypatch.setattr(
        live_cli,
        "ensure_azure_resource_group",
        lambda _policy, credentials, **_kwargs: observed.update(
            resource_group_credentials=credentials
        ),
    )
    monkeypatch.setattr(
        live_cli,
        "build_azure_containerapp_runtime_resources",
        lambda **kwargs: observed.update(runtime_credentials=kwargs["credentials"])
        or object(),
    )

    result = live_cli._default_live_resources(
        workload_args,
        policy,
        requirement,
        environment_policy,
    )

    assert result is not None
    assert observed["client_id"] == "33333333-4444-5555-6666-777777777777"
    assert observed["tenant_id"] == "22222222-3333-4444-5555-666666666666"
    assert observed["subscription_id"] == SUBSCRIPTION
    assert observed["federated_token_file"] == str(token_file)
    assert observed["expected_subscription_id"] == SUBSCRIPTION
    assert observed["resource_group_credentials"] is expected_credentials
    assert observed["runtime_credentials"] is expected_credentials


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
    assert "tests/unit/test_azure_accelerator_openbao.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_lifecycle.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_document.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_operations.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_retention.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_make_runtime.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_runtime_types.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_state.py" in recipe
    assert "tests/unit/test_azure_containerapp_environment_terraform.py" in recipe
    assert "tests/unit/test_azure_idle_retention.py" in recipe
    assert "tests/unit/test_azure_containerapp_owned_lifecycle.py" in recipe
    assert "tests/unit/test_azure_containerapp_runtime_resources.py" in recipe
    assert "tests/unit/test_azure_containerapp_sdk.py" in recipe
    assert "tests/unit/test_azure_containerapp_terraform_executor.py" in recipe
    assert "tests/unit/test_azure_accelerator_role.py" in recipe
    assert "tests/unit/test_azure_containerapp_topology.py" in recipe
    assert "tests/unit/test_azure_containerapp_tfvars.py" in recipe
    assert "tests/unit/test_deployment_telemetry.py" in recipe
    assert "tests/unit/test_provider_auth.py" in recipe
    assert "tests/unit/test_self_improve_azure_containerapp_bootstrap.py" in recipe
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
    coverage_config = (
        root / "config/coverage_azure_containerapp.ini"
    ).read_text(encoding="utf-8")
    assert "azure/accelerator_credential_source.py" in coverage_config
    assert "azure/accelerator_role.py" in coverage_config
    assert "azure_containerapp_environment_lifecycle.py" in coverage_config
    assert "azure_containerapp_environment_document.py" in coverage_config
    assert "azure_containerapp_environment_operations.py" in coverage_config
    assert "azure_containerapp_environment_retention.py" in coverage_config
    assert "azure_containerapp_environment_make_runtime.py" in coverage_config
    assert "azure_containerapp_environment_runtime_types.py" in coverage_config
    assert "azure_containerapp_environment_state.py" in coverage_config
    assert "azure_containerapp_owned_candidate.py" in coverage_config
    assert "azure_containerapp_owned_lifecycle.py" in coverage_config
    assert "azure_containerapp_runtime_resources.py" in coverage_config
    assert "azure_containerapp_sdk.py" in coverage_config
    assert "azure_containerapp_terraform_executor.py" in coverage_config
    assert "azure_containerapp_topology.py" in coverage_config
    assert "azure_idle_retention.py" in coverage_config
    assert "self_improve/azure_containerapp_bootstrap.py" in coverage_config
