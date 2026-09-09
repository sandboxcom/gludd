"""Wide lifecycle tests for the bounded Azure Container App live proof."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.azure_containerapp_live_proof import (
    LIVE_PROOF_ACKNOWLEDGEMENT,
    AzureContainerAppDeploymentEvidence,
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
    LiveProofEvent,
    LiveProofTrace,
    audit_containerapp_plan,
    run_azure_containerapp_live_proof,
)
from general_ludd.infra.azure_containerapp_live_trace import build_live_proof_trace
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

_SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
_IMAGE_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"vllm/vllm-openai@{_IMAGE_DIGEST}"
_PROMPT = "Review this public Python function and identify one edge case."


def _budget() -> BackendCallBudget:
    return BackendCallBudget(
        max_calls=1,
        max_input_tokens=128,
        max_output_tokens=64,
        max_total_tokens=192,
        max_cost_microusd=500_000,
        timeout_seconds=30.0,
    )


def _policy(*, live: bool = True, **overrides: object) -> AzureContainerAppLiveProofPolicy:
    values: dict[str, object] = {
        "subscription_id": _SUBSCRIPTION,
        "resource_group": "gludd-models-eastus",
        "environment_name": "gludd-gpu-environment",
        "workload_profile_name": "gpu-t4",
        "workload_profile_type": "Consumption-GPU-NC8as-T4",
        "location": "eastus",
        "app_name": "gludd-vllm-proof-abc123",
        "allowed_cidr": "203.0.113.7/32",
        "container_image": _IMAGE,
        "model_name": _MODEL,
        "model_revision": _MODEL_REVISION,
        "max_cost_usd": 5.0,
        "ttl_minutes": 60,
        "call_budget": _budget(),
        "estimated_request_cost_microusd": 250_000,
        "live": live,
        "acknowledgement": LIVE_PROOF_ACKNOWLEDGEMENT if live else None,
    }
    values.update(overrides)
    return AzureContainerAppLiveProofPolicy(**cast(Any, values))


def test_trace_builder_keeps_usage_but_never_response_content() -> None:
    """Keep operational accounting observable without leaking model output."""
    response = AzureCandidateResponse(
        text="private response",
        input_tokens=3,
        output_tokens=5,
        total_tokens=8,
    )

    trace = build_live_proof_trace(
        LiveProofEvent.WORK_REQUEST_SUCCEEDED,
        _policy(),
        candidate_digest="a" * 64,
        response=response,
    )

    assert (trace.input_tokens, trace.output_tokens, trace.total_tokens) == (3, 5, 8)
    assert "private response" not in repr(trace)


def _plan(policy: AzureContainerAppLiveProofPolicy) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.14.0",
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


def _watchdog_change() -> dict[str, object]:
    return {
        "address": "module.gpu_cost_watchdog.terraform_data.gpu_cost_watchdog",
        "mode": "managed",
        "type": "terraform_data",
        "name": "gpu_cost_watchdog",
        "provider_name": "terraform.io/builtin/terraform",
        "change": {
            "actions": ["create"],
            "before": None,
            "after": {"input": {"cloud": "azure"}},
        },
    }


def test_plan_audit_allows_only_the_exact_inert_cost_policy_artifact() -> None:
    policy = _policy(live=False)
    plan = _plan(policy)
    cast(list[object], plan["resource_changes"]).insert(0, _watchdog_change())

    audit_containerapp_plan(plan, policy)

    unsafe_mutations: tuple[tuple[str, object], ...] = (
        ("address", "module.foreign.terraform_data.gpu_cost_watchdog"),
        ("provider_name", "registry.terraform.io/hashicorp/external"),
        ("name", "foreign"),
    )
    for key, value in unsafe_mutations:
        candidate = copy.deepcopy(plan)
        watchdog = cast(list[dict[str, Any]], candidate["resource_changes"])[0]
        watchdog[key] = value
        with pytest.raises(AzureContainerAppLiveProofError):
            audit_containerapp_plan(candidate, policy)

    candidate = copy.deepcopy(plan)
    watchdog = cast(list[dict[str, Any]], candidate["resource_changes"])[0]
    cast(dict[str, Any], watchdog["change"])["actions"] = ["create", "delete"]
    with pytest.raises(AzureContainerAppLiveProofError):
        audit_containerapp_plan(candidate, policy)


def _evidence(
    policy: AzureContainerAppLiveProofPolicy,
    **overrides: str,
) -> AzureContainerAppDeploymentEvidence:
    values = {
        "resource_id": policy.expected_resource_id,
        "cleanup_resource_id": policy.expected_resource_id,
        "endpoint": (
            "https://gludd-vllm-proof-abc123.kindstone.eastus."
            "azurecontainerapps.io"
        ),
        "revision_name": "gludd-vllm-proof-abc123--0000007",
        "workload_profile_type": policy.workload_profile_type,
    }
    values.update(overrides)
    return AzureContainerAppDeploymentEvidence(**values)


class _Runtime:
    def __init__(
        self,
        policy: AzureContainerAppLiveProofPolicy,
        *,
        plan: object | None = None,
        evidence: AzureContainerAppDeploymentEvidence | None = None,
        fail_at: str | None = None,
        remains: bool = False,
    ) -> None:
        self.policy = policy
        self.plan_payload = _plan(policy) if plan is None else plan
        self.evidence = evidence or _evidence(policy)
        self.fail_at = fail_at
        self.remains = remains
        self.calls: list[str] = []

    def _call(self, phase: str) -> None:
        self.calls.append(phase)
        if self.fail_at == phase:
            raise RuntimeError(f"private provider detail at {phase}")

    def plan(self, policy: AzureContainerAppLiveProofPolicy) -> object:
        assert policy is self.policy
        self._call("plan")
        return self.plan_payload

    def preflight(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        assert policy is self.policy
        self._call("preflight")

    def apply(
        self,
        policy: AzureContainerAppLiveProofPolicy,
    ) -> AzureContainerAppDeploymentEvidence:
        assert policy is self.policy
        self._call("apply")
        return self.evidence

    def destroy(self, policy: AzureContainerAppLiveProofPolicy) -> None:
        assert policy is self.policy
        self._call("destroy")

    def exists(self, policy: AzureContainerAppLiveProofPolicy) -> bool:
        assert policy is self.policy
        self._call("exists")
        return self.remains


class _Backend:
    def __init__(
        self,
        identity: AzureContainerAppCandidateIdentity,
        *,
        failure: Exception | None = None,
        close_failure: bool = False,
    ) -> None:
        self.candidate_identity = identity
        self.failure = failure
        self.close_failure = close_failure
        self.calls: list[tuple[object, int, float]] = []
        self.close_calls = 0

    def generate(
        self,
        request: object,
        *,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> AzureCandidateResponse:
        self.calls.append((request, max_output_tokens, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        return AzureCandidateResponse(
            text="The empty-input branch needs a test.",
            input_tokens=17,
            output_tokens=9,
            total_tokens=26,
        )

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failure:
            raise RuntimeError("private close detail")


def _approved(tmp_path: Path) -> AzureApprovedPrompt:
    source = tmp_path / "src" / "public.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def public(value: str) -> str:\n    return value.strip()\n")
    guard = SelfImproveRuntimePolicyGuard.load(
        tmp_path,
        lambda _event: None,
        AzurePromptApprovalError,
    )
    return AzureApprovedPrompt.approve(
        prompt=_PROMPT,
        source_paths=("src/public.py",),
        policy_guard=guard,
    )


def test_dry_run_audits_exact_plan_without_credentials_or_mutation(tmp_path: Path) -> None:
    policy = _policy(live=False)
    runtime = _Runtime(policy)
    builds: list[AzureContainerAppCandidateIdentity] = []
    traces: list[LiveProofTrace] = []

    result = run_azure_containerapp_live_proof(
        policy,
        runtime=runtime,
        approved_prompt=_approved(tmp_path),
        backend_factory=lambda identity: (
            builds.append(identity) or _Backend(identity)
        ),
        trace_sink=traces.append,
    )

    assert runtime.calls == ["plan"]
    assert builds == []
    assert result.plan_audited is True
    assert result.deployment_created is False
    assert result.work_completed is False
    assert result.cleanup_verified is False
    assert [trace.event for trace in traces] == [
        LiveProofEvent.POLICY_VALIDATED,
        LiveProofEvent.PLAN_STARTED,
        LiveProofEvent.PLAN_AUDITED,
        LiveProofEvent.DRY_RUN_COMPLETED,
    ]


def test_live_run_does_one_work_request_and_always_removes_only_the_app(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runtime = _Runtime(policy)
    backend_holder: list[_Backend] = []
    traces: list[LiveProofTrace] = []

    def build(identity: AzureContainerAppCandidateIdentity) -> _Backend:
        backend = _Backend(identity)
        backend_holder.append(backend)
        return backend

    result = run_azure_containerapp_live_proof(
        policy,
        runtime=runtime,
        approved_prompt=_approved(tmp_path),
        backend_factory=build,
        trace_sink=traces.append,
    )

    assert runtime.calls == ["plan", "preflight", "apply", "destroy", "exists"]
    backend = backend_holder[0]
    assert len(backend.calls) == 1
    assert backend.calls[0][1:] == (64, 30.0)
    assert backend.close_calls == 1
    assert result.response_text == "The empty-input branch needs a test."
    assert result.deployment_created is True
    assert result.work_completed is True
    assert result.cleanup_verified is True
    assert result.candidate_identity_digest == backend.candidate_identity.identity_digest
    assert result.input_tokens == 17
    assert result.output_tokens == 9
    assert [trace.event for trace in traces][-5:] == [
        LiveProofEvent.BACKEND_CLOSED,
        LiveProofEvent.DESTROY_STARTED,
        LiveProofEvent.DESTROY_SUCCEEDED,
        LiveProofEvent.ABSENCE_VERIFIED,
        LiveProofEvent.COMPLETED,
    ]
    rendered = repr(traces) + repr(result)
    assert _PROMPT not in rendered
    assert _SUBSCRIPTION not in rendered
    assert policy.expected_resource_id not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_cost_usd": 5.01},
        {"max_cost_usd": 0.0},
        {"ttl_minutes": 61},
        {"ttl_minutes": 0},
        {"live": cast(Any, 1)},
        {"acknowledgement": "yes"},
        {"call_budget": BackendCallBudget(2, 128, 64, 384, 500_000, 30.0)},
        {"estimated_request_cost_microusd": 500_001},
        {"container_image": "vllm/vllm-openai:latest"},
        {"app_name": "UPPERCASE"},
        {"allowed_cidr": "0.0.0.0/0"},
        {"allowed_cidr": "2001:db8::1/128"},
    ],
)
def test_policy_rejects_unsafe_unbounded_or_mutable_authority(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _policy(**overrides)


def test_policy_allows_only_zero_or_one_minimum_replica() -> None:
    """Represent either an idle app or one actively claimed model runner."""
    assert _policy(min_replicas=0).min_replicas == 0
    assert _policy(min_replicas=1).min_replicas == 1
    for invalid in (-1, 2, True):
        with pytest.raises(ValueError, match="min_replicas"):
            _policy(min_replicas=invalid)


def test_dry_run_rejects_mutation_acknowledgement() -> None:
    with pytest.raises(ValueError, match="acknowledgement"):
        _policy(live=False, acknowledgement=LIVE_PROOF_ACKNOWLEDGEMENT)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("resource_changes",), []),
        (("resource_changes", "append"), "extra"),
        (("resource_changes", 0, "address"), "azapi_resource.other"),
        (("resource_changes", 0, "mode"), "data"),
        (("resource_changes", 0, "type"), "azurerm_resource_group"),
        (("resource_changes", 0, "provider_name"), "registry.terraform.io/hashicorp/azurerm"),
        (("resource_changes", 0, "change", "actions"), ["delete"]),
        (("resource_changes", 0, "change", "before"), {}),
        (("resource_changes", 0, "change", "after", "name"), "other-app"),
        (("resource_changes", 0, "change", "after", "parent_id"), "/wrong"),
        (("resource_changes", 0, "change", "after", "location"), "westus"),
        (
            ("resource_changes", 0, "change", "after", "body", "properties", "managedEnvironmentId"),
            "/wrong",
        ),
        (
            ("resource_changes", 0, "change", "after", "body", "properties", "workloadProfileName"),
            "other-profile",
        ),
        (
            (
                "resource_changes",
                0,
                "change",
                "after",
                "body",
                "properties",
                "template",
                "containers",
                0,
                "image",
            ),
            "vllm/vllm-openai:latest",
        ),
        (
            (
                "resource_changes",
                0,
                "change",
                "after",
                "body",
                "properties",
                "configuration",
                "ingress",
                "allowInsecure",
            ),
            True,
        ),
        (
            (
                "resource_changes",
                0,
                "change",
                "after",
                "body",
                "properties",
                "configuration",
                "ingress",
                "targetPort",
            ),
            8001,
        ),
        (
            (
                "resource_changes",
                0,
                "change",
                "after",
                "body",
                "properties",
                "configuration",
                "ingress",
                "ipSecurityRestrictions",
                0,
                "ipAddressRange",
            ),
            "0.0.0.0/0",
        ),
    ],
)
def test_plan_audit_rejects_every_scope_or_provenance_widening(
    path: tuple[object, ...],
    value: object,
) -> None:
    policy = _policy(live=False)
    plan = copy.deepcopy(_plan(policy))
    if path == ("resource_changes", "append"):
        cast(list[object], plan["resource_changes"]).append(
            copy.deepcopy(cast(list[object], plan["resource_changes"])[0])
        )
    else:
        cursor: Any = plan
        for component in path[:-1]:
            cursor = cursor[component]
        cursor[path[-1]] = value

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        audit_containerapp_plan(plan, policy)

    assert captured.value.failure is AzureContainerAppLiveProofFailure.PLAN_SCOPE


def test_plan_refusal_trace_exposes_only_the_fixed_audit_stage(tmp_path: Path) -> None:
    policy = _policy(live=False)
    plan = _plan(policy)
    resources = cast(list[dict[str, Any]], plan["resource_changes"])
    after = cast(dict[str, Any], resources[0]["change"])["after"]
    cast(dict[str, Any], after)["name"] = "foreign-app"
    traces: list[LiveProofTrace] = []

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=_Runtime(policy, plan=plan),
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity),
            trace_sink=traces.append,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.PLAN_SCOPE
    assert captured.value.detail == "resource_scope"
    assert traces[-1].event is LiveProofEvent.FAILED
    assert traces[-1].failure_detail == "resource_scope"


def test_preflight_failure_stops_before_paid_mutation(tmp_path: Path) -> None:
    policy = _policy()
    runtime = _Runtime(policy, fail_at="preflight")

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.PREFLIGHT
    assert runtime.calls == ["plan", "preflight"]
    assert "private provider detail" not in str(captured.value)


def test_partial_apply_failure_still_runs_app_only_cleanup(tmp_path: Path) -> None:
    policy = _policy()
    runtime = _Runtime(policy, fail_at="apply")

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.APPLY
    assert runtime.calls == ["plan", "preflight", "apply", "destroy", "exists"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_id": "/wrong"},
        {"cleanup_resource_id": "/wrong"},
        {"endpoint": "https://other.azurecontainerapps.io"},
        {"revision_name": "other-app--0000008"},
        {"workload_profile_type": "Consumption-GPU-NC24-A100"},
    ],
)
def test_deployment_evidence_mismatch_fails_closed_and_cleans_up(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    policy = _policy()
    runtime = _Runtime(policy, evidence=_evidence(policy, **overrides))
    builds: list[AzureContainerAppCandidateIdentity] = []

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: (
                builds.append(identity) or _Backend(identity)
            ),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.DEPLOYMENT_EVIDENCE
    assert builds == []
    assert runtime.calls[-2:] == ["destroy", "exists"]


def test_backend_or_request_failure_is_terminal_but_cleanup_still_runs(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runtime = _Runtime(policy)
    backend = _Backend(
        _evidence(policy).candidate_identity(policy),
        failure=BackendInfrastructureError(BackendFailure.TIMEOUT),
    )

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda _identity: backend,
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.WORK_REQUEST
    assert backend.close_calls == 1
    assert runtime.calls[-2:] == ["destroy", "exists"]


@pytest.mark.parametrize(
    ("fail_at", "remains"),
    [("destroy", False), (None, True), ("exists", False)],
)
def test_cleanup_failure_overrides_success_and_is_censored(
    tmp_path: Path,
    fail_at: str | None,
    remains: bool,
) -> None:
    policy = _policy()
    runtime = _Runtime(policy, fail_at=fail_at, remains=remains)

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.CLEANUP
    assert "private" not in str(captured.value)


def test_invalid_prompt_or_trace_sink_fails_before_paid_mutation(tmp_path: Path) -> None:
    policy = _policy()
    runtime = _Runtime(policy)
    with pytest.raises(ValueError, match="approved_prompt"):
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=cast(Any, _PROMPT),
            backend_factory=lambda identity: _Backend(identity),
        )
    assert runtime.calls == []

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity),
            trace_sink=lambda _trace: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    assert captured.value.failure is AzureContainerAppLiveProofFailure.TRACE
    assert runtime.calls == []


def test_backend_close_failure_cannot_skip_infrastructure_cleanup(tmp_path: Path) -> None:
    policy = _policy()
    runtime = _Runtime(policy)

    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=_approved(tmp_path),
            backend_factory=lambda identity: _Backend(identity, close_failure=True),
        )

    assert captured.value.failure is AzureContainerAppLiveProofFailure.CLEANUP
    assert runtime.calls[-2:] == ["destroy", "exists"]


def test_plan_audit_rejects_untyped_policy_and_missing_root_fields() -> None:
    """Fail before trusting a plan whose policy or root object is ambiguous."""
    policy = _policy(live=False)

    with pytest.raises(ValueError, match="policy"):
        audit_containerapp_plan(_plan(policy), cast(Any, object()))
    with pytest.raises(AzureContainerAppLiveProofError) as captured:
        audit_containerapp_plan({}, policy)

    assert captured.value.failure is AzureContainerAppLiveProofFailure.PLAN_SCOPE


def test_live_proof_rejects_untyped_effect_boundaries_before_runtime(
    tmp_path: Path,
) -> None:
    """Reject each public effect boundary before plan, trace, or provider work."""
    policy = _policy()
    runtime = _Runtime(policy)
    approved = _approved(tmp_path)

    with pytest.raises(ValueError, match="policy"):
        run_azure_containerapp_live_proof(
            cast(Any, object()),
            runtime=runtime,
            approved_prompt=approved,
            backend_factory=lambda identity: _Backend(identity),
        )
    with pytest.raises(ValueError, match="runtime"):
        run_azure_containerapp_live_proof(
            policy,
            runtime=cast(Any, object()),
            approved_prompt=approved,
            backend_factory=lambda identity: _Backend(identity),
        )
    with pytest.raises(ValueError, match="backend_factory"):
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=approved,
            backend_factory=cast(Any, object()),
        )

    assert runtime.calls == []


def test_plan_and_discovered_identity_failures_are_censored_and_non_destructive(
    tmp_path: Path,
) -> None:
    """Censor plan errors and reject a backend that drifts from deployment evidence."""
    policy = _policy()
    approved = _approved(tmp_path)
    plan_runtime = _Runtime(policy, fail_at="plan")
    with pytest.raises(AzureContainerAppLiveProofError) as plan_failure:
        run_azure_containerapp_live_proof(
            policy,
            runtime=plan_runtime,
            approved_prompt=approved,
            backend_factory=lambda identity: _Backend(identity),
        )
    assert plan_failure.value.failure is AzureContainerAppLiveProofFailure.PLAN_SCOPE
    assert plan_runtime.calls == ["plan"]

    runtime = _Runtime(policy)
    expected = _evidence(policy).candidate_identity(policy)
    drifted = AzureContainerAppCandidateIdentity(
        endpoint=expected.endpoint,
        resource_id=expected.resource_id,
        revision_name=f"{policy.app_name}--drifted",
        image_digest=expected.image_digest,
        model_name=expected.model_name,
        model_revision=expected.model_revision,
        workload_profile_type=expected.workload_profile_type,
    )
    backend = _Backend(drifted)
    with pytest.raises(AzureContainerAppLiveProofError) as discovery_failure:
        run_azure_containerapp_live_proof(
            policy,
            runtime=runtime,
            approved_prompt=approved,
            backend_factory=lambda _identity: backend,
        )

    assert discovery_failure.value.failure is AzureContainerAppLiveProofFailure.DISCOVERY
    assert backend.close_calls == 1
    assert runtime.calls[-2:] == ["destroy", "exists"]
