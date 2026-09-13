"""End-to-end ownership tests around environment, app, work, and teardown."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra import azure_containerapp_owned_lifecycle as owned_lifecycle
from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleEvent,
    EnvironmentLifecycleTrace,
)
from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    LiveProofTrace,
)
from general_ludd.infra.azure_containerapp_owned_candidate import (
    AzureContainerAppOwnedCandidateFactory,
    OwnedCandidateLifecycleError,
    OwnedCandidateLifecycleEvent,
    OwnedCandidateLifecycleTrace,
    owned_candidate_deployment_digest,
)
from general_ludd.infra.azure_containerapp_owned_lifecycle import (
    run_owned_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_idle_retention import (
    AzureIdleRetentionPolicy,
    AzureRetentionPreset,
    AzureRetentionTrace,
    AzureRetentionTraceEvent,
)
from general_ludd.self_improve.azure_backend import (
    AzureApprovedPrompt,
    AzureCandidateResponse,
    AzurePromptApprovalError,
)
from general_ludd.self_improve.model_candidates import (
    AzureContainerAppCandidateIdentity,
    BackendCallBudget,
    BackendFailure,
    BackendInfrastructureError,
)
from general_ludd.self_improve.private_policy import SelfImproveRuntimePolicyGuard

SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
IMAGE = "vllm/vllm-openai@sha256:" + "a" * 64
SECRET = "private-provider-payload"
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _app_policy(*, live: bool = True, **overrides: object) -> AzureContainerAppLiveProofPolicy:
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
        "live": live,
        "acknowledgement": LIVE_PROOF_ACKNOWLEDGEMENT if live else None,
    }
    values.update(overrides)
    return AzureContainerAppLiveProofPolicy(**cast(Any, values))


def _environment_policy(
    app_policy: AzureContainerAppLiveProofPolicy,
    **overrides: object,
) -> AzureEnvironmentLifecyclePolicy:
    values: dict[str, object] = {
        "subscription_id": app_policy.subscription_id,
        "resource_group": app_policy.resource_group,
        "environment_name": app_policy.environment_name,
        "location": app_policy.location,
        "profiles": (
            AzureEnvironmentProfile(
                app_policy.workload_profile_name,
                app_policy.workload_profile_type,
            ),
        ),
        "owner_digest": "b" * 64,
        "plan_digest": app_policy.operation_digest,
        "expires_at_utc": "2026-09-06T21:00:00Z",
    }
    values.update(overrides)
    return AzureEnvironmentLifecyclePolicy(**cast(Any, values))


def _idle_retention_policy() -> AzureIdleRetentionPolicy:
    return AzureIdleRetentionPolicy(
        preset=AzureRetentionPreset.ZERO_COST_ONLY,
        max_idle_hourly_cost_microusd=0,
        max_idle_monthly_cost_microusd=0,
        max_retention_cost_microusd=0,
        max_retention_seconds=3_600,
        max_price_age_seconds=31_536_000,
        max_latency_age_seconds=86_400,
        max_cost_per_saved_hour_microusd=0,
    )


def _environment_document(policy: AzureEnvironmentLifecyclePolicy) -> dict[str, object]:
    return {
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


def _environment_plan(policy: AzureEnvironmentLifecyclePolicy) -> dict[str, object]:
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
                                        "workloadProfileType": profile.workload_profile_type,
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


def _app_plan(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
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
                    "after_unknown": {},
                },
            }
        ],
    }


class _EnvironmentRuntime:
    def __init__(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        events: list[str],
        *,
        fail_at: str | None = None,
        remaining_apps: tuple[str, ...] = (),
    ) -> None:
        self.policy = policy
        self.events = events
        self.fail_at = fail_at
        self.remaining_apps = remaining_apps
        self.document: object | None = None

    def _call(self, phase: str) -> None:
        self.events.append(f"environment:{phase}")
        if self.fail_at == phase:
            raise RuntimeError(f"{SECRET}:{phase}")

    def read_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        expect_absent: bool,
    ) -> object | None:
        del expect_absent
        assert policy.environment_id == self.policy.environment_id
        self._call("read")
        return self.document

    def plan(self, policy: AzureEnvironmentLifecyclePolicy) -> object:
        self._call("plan")
        return _environment_plan(policy)

    def apply(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        self._call("apply")
        self.document = _environment_document(policy)

    def list_environment_apps(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        del policy
        self._call("inventory")
        return self.remaining_apps

    def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        del policy
        self._call("destroy")
        self.document = None


class _AppRuntime:
    def __init__(
        self,
        policy: AzureContainerAppLiveProofPolicy,
        events: list[str],
        *,
        fail_at: str | None = None,
        remains: bool = False,
    ) -> None:
        self.policy = policy
        self.events = events
        self.fail_at = fail_at
        self.remains = remains

    def _call(self, phase: str) -> None:
        self.events.append(f"app:{phase}")
        if self.fail_at == phase:
            raise RuntimeError(f"{SECRET}:{phase}")

    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        self._call("plan")
        return _app_plan(policy)

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        del policy
        self._call("preflight")

    def apply(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppDeploymentEvidence:
        self._call("apply")
        return AzureContainerAppDeploymentEvidence(
            resource_id=policy.expected_resource_id,
            cleanup_resource_id=policy.expected_resource_id,
            endpoint=(
                "https://gludd-vllm-proof-abc123.kindstone.eastus."
                "azurecontainerapps.io"
            ),
            revision_name=f"{policy.app_name}--0000007",
            workload_profile_type=policy.workload_profile_type,
        )

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        del policy
        self._call("destroy")

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        del policy
        self._call("exists")
        return self.remains


class _Backend:
    def __init__(self, identity: AzureContainerAppCandidateIdentity) -> None:
        self.candidate_identity = identity
        self.closed = False

    def generate(
        self,
        _request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        del max_output_tokens, timeout_seconds
        return AzureCandidateResponse("safe result", 7, 3, 10)

    def close(self) -> None:
        self.closed = True


def _approved(tmp_path: Path) -> AzureApprovedPrompt:
    source = tmp_path / "src" / "public.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def public() -> int:\n    return 1\n", encoding="utf-8")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        AzurePromptApprovalError,
    )
    return AzureApprovedPrompt.approve(
        prompt="Suggest one deterministic public test.",
        source_paths=("src/public.py",),
        policy_guard=guard,
    )


def test_owned_candidate_factory_bootstraps_on_demand_and_tears_down_on_close() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    app_runtime = _AppRuntime(app_policy, events)
    backend_delegate: _Backend | None = None
    traces: list[OwnedCandidateLifecycleTrace] = []

    def build_backend(identity: AzureContainerAppCandidateIdentity) -> _Backend:
        nonlocal backend_delegate
        backend_delegate = _Backend(identity)
        events.append("backend:open")
        return backend_delegate

    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=app_runtime,
        backend_factory=build_backend,
        resource_release=lambda: events.append("resources:close"),
        trace_sink=traces.append,
    )

    backend = factory()
    response = backend.generate(object(), max_output_tokens=8, timeout_seconds=3.0)
    backend.close()
    backend.close()

    with pytest.raises(OwnedCandidateLifecycleError, match="closed"):
        backend.generate(object(), max_output_tokens=8, timeout_seconds=3.0)

    assert response.text == "safe result"
    assert backend_delegate is not None and backend_delegate.closed is True
    assert factory.active is False
    assert events == [
        "environment:read",
        "environment:plan",
        "environment:apply",
        "environment:read",
        "app:plan",
        "app:preflight",
        "app:apply",
        "backend:open",
        "app:destroy",
        "app:exists",
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
        "resources:close",
    ]
    assert [trace.event for trace in traces] == [
        OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRE_STARTED,
        OwnedCandidateLifecycleEvent.ENVIRONMENT_ACQUIRED,
        OwnedCandidateLifecycleEvent.APP_PLAN_STARTED,
        OwnedCandidateLifecycleEvent.APP_PLAN_AUDITED,
        OwnedCandidateLifecycleEvent.APP_PREFLIGHT_STARTED,
        OwnedCandidateLifecycleEvent.APP_PREFLIGHT_SUCCEEDED,
        OwnedCandidateLifecycleEvent.APP_APPLY_STARTED,
        OwnedCandidateLifecycleEvent.APP_APPLIED,
        OwnedCandidateLifecycleEvent.BACKEND_ACQUIRED,
        OwnedCandidateLifecycleEvent.BACKEND_CLOSE_STARTED,
        OwnedCandidateLifecycleEvent.BACKEND_CLOSED,
        OwnedCandidateLifecycleEvent.APP_DESTROY_STARTED,
        OwnedCandidateLifecycleEvent.APP_DESTROYED,
        OwnedCandidateLifecycleEvent.APP_ABSENCE_VERIFIED,
        OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASE_STARTED,
        OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASED,
        OwnedCandidateLifecycleEvent.RESOURCES_RELEASED,
    ]
    assert all(trace.operation_digest == factory.deployment_digest for trace in traces)


def test_owned_candidate_retains_only_zero_cost_environment_with_bounded_plan() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    ticks = iter((10.0, 974.0, 1_000.0, 1_240.0))
    traces: list[OwnedCandidateLifecycleTrace] = []
    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=_AppRuntime(app_policy, events),
        backend_factory=_Backend,
        resource_release=lambda: events.append("resources:close"),
        trace_sink=traces.append,
        idle_retention_policy=_idle_retention_policy(),
        expected_next_demand_seconds=1_800,
        now=lambda: NOW,
        monotonic=lambda: next(ticks),
    )

    backend = factory()
    backend.close()

    assert "environment:destroy" not in events
    assert events[-3:] == [
        "environment:read",
        "environment:inventory",
        "resources:close",
    ]
    retention = [
        trace
        for trace in traces
        if trace.event is OwnedCandidateLifecycleEvent.ENVIRONMENT_RETENTION_PLANNED
    ]
    assert len(retention) == 1
    assert retention[0].retention_seconds == 1_800
    assert retention[0].retention_hourly_cost_microusd == 0
    assert retention[0].retention_plan_digest is not None
    assert app_policy.min_replicas == 0


def test_retention_clock_failure_falls_back_to_verified_environment_destroy() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    ticks = iter((10.0, 974.0, 1_000.0, 1_240.0))

    def broken_clock() -> datetime:
        raise RuntimeError(SECRET)

    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=_AppRuntime(app_policy, events),
        backend_factory=_Backend,
        resource_release=lambda: events.append("resources:close"),
        idle_retention_policy=_idle_retention_policy(),
        now=broken_clock,
        monotonic=lambda: next(ticks),
    )

    backend = factory()
    backend.close()

    assert environment_runtime.document is None
    assert events[-3:] == [
        "environment:destroy",
        "environment:read",
        "resources:close",
    ]


def test_owned_candidate_factory_cleans_every_paid_resource_after_discovery_failure() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    app_runtime = _AppRuntime(app_policy, events)

    def fail_backend(_identity: AzureContainerAppCandidateIdentity) -> _Backend:
        raise RuntimeError(SECRET)

    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=app_runtime,
        backend_factory=fail_backend,
        resource_release=lambda: events.append("resources:close"),
    )

    with pytest.raises(OwnedCandidateLifecycleError, match="backend") as captured:
        factory()

    assert SECRET not in repr(captured.value)
    assert factory.active is False
    assert events[-7:] == [
        "app:destroy",
        "app:exists",
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
        "resources:close",
    ]


def test_owned_candidate_preserves_typed_apply_failure_after_cleanup() -> None:
    """Cleanup censorship must retain the fixed backend failure classification."""
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)

    class TimedOutAppRuntime(_AppRuntime):
        def apply(
            self,
            policy: AzureContainerAppLiveProofPolicy,
        ) -> AzureContainerAppDeploymentEvidence:
            del policy
            self._call("apply")
            raise BackendInfrastructureError(BackendFailure.TIMEOUT)

    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=_EnvironmentRuntime(environment_policy, events),
        app_runtime=TimedOutAppRuntime(app_policy, events),
        backend_factory=_Backend,
        resource_release=lambda: events.append("resources:close"),
    )

    with pytest.raises(OwnedCandidateLifecycleError) as captured:
        factory()

    assert captured.value.operation == "apply"
    assert captured.value.failure is BackendFailure.TIMEOUT
    assert events[-7:] == [
        "app:destroy",
        "app:exists",
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
        "resources:close",
    ]


def test_owned_candidate_factory_never_destroys_environment_while_app_may_remain() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(
        environment_policy,
        events,
        remaining_apps=(app_policy.expected_resource_id,),
    )
    app_runtime = _AppRuntime(app_policy, events, fail_at="destroy")
    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=app_runtime,
        backend_factory=_Backend,
        resource_release=lambda: events.append("resources:close"),
    )

    backend = factory()
    with pytest.raises(OwnedCandidateLifecycleError, match="cleanup") as captured:
        backend.close()

    assert SECRET not in repr(captured.value)
    assert "environment:destroy" not in events
    assert events[-1] == "resources:close"


def test_owned_candidate_factory_is_single_use_and_configuration_bound() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=_EnvironmentRuntime(environment_policy, events),
        app_runtime=_AppRuntime(app_policy, events),
        backend_factory=_Backend,
    )

    backend = factory()
    with pytest.raises(OwnedCandidateLifecycleError, match="active"):
        factory()
    backend.close()
    with pytest.raises(OwnedCandidateLifecycleError, match="closed"):
        factory()

    assert len(factory.deployment_digest) == 64


def test_owned_candidate_factory_rejects_every_ambiguous_public_boundary() -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    app_runtime = _AppRuntime(app_policy, events)

    with pytest.raises(ValueError, match="invalid boundary"):
        owned_candidate_deployment_digest(cast(Any, object()), environment_policy)
    with pytest.raises(ValueError, match="invalid boundary"):
        owned_candidate_deployment_digest(app_policy, cast(Any, object()))

    invalid_values: tuple[dict[str, object], ...] = (
        {"app_policy": _app_policy(live=False)},
        {"environment_policy": object()},
        {"environment_runtime": object()},
        {"app_runtime": object()},
        {"backend_factory": object()},
        {"resource_release": object()},
        {"trace_sink": object()},
    )
    base: dict[str, object] = {
        "app_policy": app_policy,
        "environment_policy": environment_policy,
        "environment_runtime": environment_runtime,
        "app_runtime": app_runtime,
        "backend_factory": _Backend,
        "resource_release": None,
        "trace_sink": lambda _event: None,
    }

    for invalid in invalid_values:
        with pytest.raises(ValueError):
            AzureContainerAppOwnedCandidateFactory(
                **cast(Any, {**base, **invalid})
            )

    assert events == []


def test_owned_lifecycle_creates_environment_runs_work_then_destroys_everything(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    app_runtime = _AppRuntime(app_policy, events)
    environment_traces: list[EnvironmentLifecycleTrace] = []
    app_traces: list[LiveProofTrace] = []

    result = run_owned_azure_containerapp_live_proof(
        app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=app_runtime,
        approved_prompt=_approved(tmp_path),
        backend_factory=_Backend,
        environment_trace_sink=environment_traces.append,
        app_trace_sink=app_traces.append,
    )

    assert result.work_completed is True
    assert result.cleanup_verified is True
    assert environment_runtime.document is None
    assert events == [
        "environment:read",
        "environment:plan",
        "environment:apply",
        "environment:read",
        "app:plan",
        "app:preflight",
        "app:apply",
        "app:destroy",
        "app:exists",
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
    ]
    assert environment_traces
    assert app_traces
    assert SECRET not in repr(environment_traces + app_traces)


def test_owned_lifecycle_can_retain_only_the_empty_zero_cost_environment(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    environment_traces: list[EnvironmentLifecycleTrace] = []
    retention_traces: list[AzureRetentionTrace] = []
    ticks = iter((10.0, 974.0))

    result = run_owned_azure_containerapp_live_proof(
        app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=_AppRuntime(app_policy, events),
        approved_prompt=_approved(tmp_path),
        backend_factory=_Backend,
        environment_trace_sink=environment_traces.append,
        idle_retention_policy=_idle_retention_policy(),
        expected_next_demand_seconds=1_800,
        now=lambda: NOW,
        monotonic=lambda: next(ticks),
        retention_trace_sink=retention_traces.append,
    )

    assert result.cleanup_verified is True
    assert environment_runtime.document is not None
    assert "environment:destroy" not in events
    retained = [
        trace
        for trace in environment_traces
        if trace.event is EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED
    ]
    assert len(retained) == 1
    assert retained[0].retention_seconds_remaining == 1_800
    assert retained[0].retention_hourly_cost_microusd == 0
    assert [trace.event for trace in retention_traces] == [
        AzureRetentionTraceEvent.EVALUATION_STARTED,
        AzureRetentionTraceEvent.PLAN_SELECTED,
    ]


def test_preflight_failure_can_preserve_safe_environment_for_bounded_retry(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    ticks = iter((10.0, 974.0))

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=_AppRuntime(app_policy, events, fail_at="preflight"),
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
            idle_retention_policy=_idle_retention_policy(),
            expected_next_demand_seconds=1_800,
            now=lambda: NOW,
            monotonic=lambda: next(ticks),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.PREFLIGHT
    assert environment_runtime.document is not None
    assert "environment:destroy" not in events


def test_retention_planning_failure_falls_back_to_verified_destroy(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    ticks = iter((10.0, 974.0))

    def broken_now() -> datetime:
        raise RuntimeError(SECRET)

    result = run_owned_azure_containerapp_live_proof(
        app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=_AppRuntime(app_policy, events),
        approved_prompt=_approved(tmp_path),
        backend_factory=_Backend,
        idle_retention_policy=_idle_retention_policy(),
        now=broken_now,
        monotonic=lambda: next(ticks),
    )

    assert result.cleanup_verified is True
    assert environment_runtime.document is None
    assert events[-2:] == ["environment:destroy", "environment:read"]


@pytest.mark.parametrize("app_failure", ["plan", "preflight", "apply"])
def test_every_app_failure_still_releases_the_owned_environment(
    tmp_path: Path,
    app_failure: str,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)
    app_runtime = _AppRuntime(app_policy, events, fail_at=app_failure)

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=app_runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is not AzureContainerAppLiveProofFailure.CLEANUP
    assert environment_runtime.document is None
    assert events[-4:] == [
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
    ]
    assert SECRET not in repr(captured.value)


def test_environment_creation_failure_is_typed_and_never_starts_app_work(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(
        environment_policy,
        events,
        fail_at="plan",
    )
    app_runtime = _AppRuntime(app_policy, events)

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=app_runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.ENVIRONMENT
    assert all(not event.startswith("app:") for event in events)
    assert SECRET not in repr(captured.value)


def test_existing_environment_survives_failed_acquisition_plan() -> None:
    events: list[str] = []
    lifecycle_traces: list[OwnedCandidateLifecycleTrace] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(
        environment_policy,
        events,
        fail_at="plan",
    )
    environment_runtime.document = _environment_document(environment_policy)

    factory = AzureContainerAppOwnedCandidateFactory(
        app_policy=app_policy,
        environment_policy=environment_policy,
        environment_runtime=environment_runtime,
        app_runtime=_AppRuntime(app_policy, events),
        backend_factory=_Backend,
        resource_release=lambda: events.append("resources:close"),
        trace_sink=lifecycle_traces.append,
    )

    with pytest.raises(OwnedCandidateLifecycleError, match="environment"):
        factory()

    assert environment_runtime.document is not None
    assert "environment:destroy" not in events
    assert OwnedCandidateLifecycleEvent.ENVIRONMENT_RELEASE_STARTED not in {
        trace.event for trace in lifecycle_traces
    }


def test_environment_retained_due_to_remaining_app_is_terminal_cleanup_failure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(
        environment_policy,
        events,
        remaining_apps=(app_policy.expected_resource_id,),
    )

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=_AppRuntime(app_policy, events),
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.CLEANUP
    assert environment_runtime.document is not None
    assert "environment:destroy" not in events


@pytest.mark.parametrize(
    "environment_overrides",
    [
        {"environment_name": "other-environment"},
        {"resource_group": "other-resource-group"},
        {"location": "westus3"},
        {
            "profiles": (
                AzureEnvironmentProfile(
                    "gpu-a100", "Consumption-GPU-NC24-A100"
                ),
            )
        },
    ],
)
def test_mismatched_environment_authority_refuses_before_any_effect(
    tmp_path: Path,
    environment_overrides: dict[str, object],
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy, **environment_overrides)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=_AppRuntime(app_policy, events),
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.POLICY
    assert events == []


def test_dry_run_preserves_zero_environment_mutation(tmp_path: Path) -> None:
    events: list[str] = []
    app_policy = _app_policy(live=False)
    environment_policy = _environment_policy(app_policy)

    result = run_owned_azure_containerapp_live_proof(
        app_policy,
        environment_policy=environment_policy,
        environment_runtime=_EnvironmentRuntime(environment_policy, events),
        app_runtime=_AppRuntime(app_policy, events),
        approved_prompt=_approved(tmp_path),
        backend_factory=_Backend,
    )

    assert result.plan_audited is True
    assert result.deployment_created is False
    assert events == ["app:plan"]


@pytest.mark.parametrize(
    "invalid_boundary",
    [
        "app_policy",
        "environment_policy",
        "environment_runtime",
        "app_runtime",
        "environment_trace_sink",
        "app_trace_sink",
    ],
)
def test_untyped_effect_boundaries_fail_before_any_operation(
    tmp_path: Path,
    invalid_boundary: str,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    values: dict[str, object] = {
        "app_policy": app_policy,
        "environment_policy": environment_policy,
        "environment_runtime": _EnvironmentRuntime(environment_policy, events),
        "app_runtime": _AppRuntime(app_policy, events),
        "approved_prompt": _approved(tmp_path),
        "backend_factory": _Backend,
        "environment_trace_sink": lambda _event: None,
        "app_trace_sink": lambda _event: None,
    }
    values[invalid_boundary] = object()

    with pytest.raises(ValueError):
        run_owned_azure_containerapp_live_proof(**cast(Any, values))

    assert events == []


def test_environment_inventory_failure_is_terminal_cleanup_failure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(
        environment_policy,
        events,
        fail_at="inventory",
    )

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=_AppRuntime(app_policy, events),
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.CLEANUP
    assert environment_runtime.document is not None
    assert SECRET not in repr(captured.value)


@pytest.mark.parametrize("app_outcome", ["unexpected-error", "missing-result"])
def test_unexpected_app_orchestration_outcomes_still_release_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_outcome: str,
) -> None:
    events: list[str] = []
    app_policy = _app_policy()
    environment_policy = _environment_policy(app_policy)
    environment_runtime = _EnvironmentRuntime(environment_policy, events)

    def unexpected_app_outcome(*_args: object, **_kwargs: object) -> Any:
        if app_outcome == "unexpected-error":
            raise RuntimeError(SECRET)
        return None

    monkeypatch.setattr(
        owned_lifecycle,
        "run_azure_containerapp_live_proof",
        unexpected_app_outcome,
    )

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_owned_azure_containerapp_live_proof(
            app_policy,
            environment_policy=environment_policy,
            environment_runtime=environment_runtime,
            app_runtime=_AppRuntime(app_policy, events),
            approved_prompt=_approved(tmp_path),
            backend_factory=_Backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.POLICY
    assert environment_runtime.document is None
    assert events[-4:] == [
        "environment:read",
        "environment:inventory",
        "environment:destroy",
        "environment:read",
    ]
    assert SECRET not in repr(captured.value)
