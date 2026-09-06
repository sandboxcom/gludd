"""Wide lifecycle contracts for Gludd-owned Azure Container Apps environments."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, cast

import pytest

from general_ludd.infra.azure_containerapp_environment_lifecycle import (
    AzureContainerAppEnvironmentRuntime,
    AzureEnvironmentLifecycleError,
    AzureEnvironmentLifecyclePolicy,
    AzureEnvironmentProfile,
    EnvironmentLifecycleDisposition,
    EnvironmentLifecycleEvent,
    EnvironmentLifecycleTrace,
    audit_environment_plan,
    ensure_azure_containerapp_environment,
    release_azure_containerapp_environment,
)
from general_ludd.infra.azure_containerapp_gpu import A100_PROFILE, T4_PROFILE

_SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
_OWNER = "b" * 64
_PLAN = "c" * 64
_SECRET = "azure-secret-must-never-render"


def _t4() -> AzureEnvironmentProfile:
    return AzureEnvironmentProfile(
        profile_name="gpu-t4",
        workload_profile_type=T4_PROFILE.workload_profile_type,
    )


def _a100() -> AzureEnvironmentProfile:
    return AzureEnvironmentProfile(
        profile_name="gpu-a100",
        workload_profile_type=A100_PROFILE.workload_profile_type,
    )


def _policy(**overrides: object) -> AzureEnvironmentLifecyclePolicy:
    values: dict[str, object] = {
        "subscription_id": _SUBSCRIPTION,
        "resource_group": "gludd-models-eastus",
        "environment_name": "gludd-gpu-bbbbbbbbbbbb",
        "location": "eastus",
        "profiles": (_t4(),),
        "owner_digest": _OWNER,
        "plan_digest": _PLAN,
        "expires_at_utc": "2026-09-06T19:00:00Z",
        "teardown_when_idle": True,
    }
    values.update(overrides)
    return AzureEnvironmentLifecyclePolicy(**cast(Any, values))


def _document(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    profiles: tuple[AzureEnvironmentProfile, ...] | None = None,
    state: str = "Succeeded",
) -> dict[str, object]:
    active_profiles = policy.profiles if profiles is None else profiles
    return {
        "id": policy.environment_id,
        "name": policy.environment_name,
        "type": "Microsoft.App/managedEnvironments",
        "location": policy.location,
        "tags": policy.ownership_tags,
        "properties": {
            "provisioningState": state,
            "workloadProfiles": [
                {
                    "name": profile.profile_name,
                    "workloadProfileType": profile.workload_profile_type,
                }
                for profile in active_profiles
            ],
        },
    }


def _plan_payload(
    policy: AzureEnvironmentLifecyclePolicy,
    actions: list[str],
) -> dict[str, object]:
    return {
        "format_version": "1.2",
        "terraform_version": "1.14.0",
        "resource_changes": [
            {
                "address": "module.environment.azapi_resource.managed_environment",
                "mode": "managed",
                "type": "azapi_resource",
                "name": "managed_environment",
                "provider_name": "registry.terraform.io/azure/azapi",
                "change": {
                    "actions": actions,
                    "before": None,
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
        ],
    }


class _Runtime:
    def __init__(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        document: object | None,
        plan_actions: list[str] | None = None,
        after_apply: object | None = None,
        apps: tuple[str, ...] = (),
        fail_at: str | None = None,
        partial_apply: bool = False,
    ) -> None:
        self.initial_policy = policy
        self.document = document
        self.plan_actions = plan_actions or (["create"] if document is None else ["no-op"])
        self.after_apply = after_apply
        self.apps = apps
        self.fail_at = fail_at
        self.partial_apply = partial_apply
        self.calls: list[str] = []
        self.policies: list[AzureEnvironmentLifecyclePolicy] = []

    def _call(self, phase: str) -> None:
        self.calls.append(phase)
        if self.fail_at == phase:
            raise RuntimeError(f"{_SECRET}:{phase}")

    def read_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
        *,
        expect_absent: bool,
    ) -> object | None:
        del expect_absent
        self.policies.append(policy)
        self._call("read")
        return copy.deepcopy(self.document)

    def plan(self, policy: AzureEnvironmentLifecyclePolicy) -> object:
        self.policies.append(policy)
        self._call("plan")
        return _plan_payload(policy, self.plan_actions)

    def apply(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        self.policies.append(policy)
        self.calls.append("apply")
        if self.partial_apply:
            self.document = copy.deepcopy(self.after_apply or _document(policy))
        if self.fail_at == "apply":
            raise RuntimeError(f"{_SECRET}:apply")
        self.document = copy.deepcopy(self.after_apply or _document(policy))

    def list_environment_apps(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> tuple[str, ...]:
        self.policies.append(policy)
        self._call("list-apps")
        return self.apps

    def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
        self.policies.append(policy)
        self._call("destroy")
        self.document = None


def test_absent_environment_is_planned_applied_and_independently_verified() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=None)
    traces: list[EnvironmentLifecycleTrace] = []

    result = ensure_azure_containerapp_environment(
        policy,
        runtime=runtime,
        trace_sink=traces.append,
    )

    assert result.disposition is EnvironmentLifecycleDisposition.CREATED
    assert result.environment_id == policy.environment_id
    assert result.effective_profiles == policy.profiles
    assert runtime.calls == ["read", "plan", "apply", "read"]
    assert [trace.event for trace in traces] == [
        EnvironmentLifecycleEvent.INSPECTION_STARTED,
        EnvironmentLifecycleEvent.ENVIRONMENT_ABSENT,
        EnvironmentLifecycleEvent.PLAN_STARTED,
        EnvironmentLifecycleEvent.PLAN_AUDITED,
        EnvironmentLifecycleEvent.APPLY_STARTED,
        EnvironmentLifecycleEvent.APPLY_SUCCEEDED,
        EnvironmentLifecycleEvent.READINESS_VERIFIED,
    ]


def test_existing_owned_environment_is_reused_through_a_noop_plan() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=_document(policy))

    result = ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.REUSED
    assert runtime.calls == ["read", "plan", "apply", "read"]


def test_missing_profile_is_added_without_removing_existing_owned_profile() -> None:
    requested = _policy(profiles=(_a100(),))
    runtime = _Runtime(
        requested,
        document=_document(requested, profiles=(_t4(),)),
        plan_actions=["update"],
    )

    result = ensure_azure_containerapp_environment(requested, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.RECONCILED
    assert result.effective_profiles == (_a100(), _t4())
    planned_policy = runtime.policies[runtime.calls.index("plan")]
    assert planned_policy.profiles == (_a100(), _t4())
    assert cast(dict[str, object], runtime.document)["properties"] == {
        "provisioningState": "Succeeded",
        "workloadProfiles": [
            {
                "name": "gpu-a100",
                "workloadProfileType": A100_PROFILE.workload_profile_type,
            },
            {
                "name": "gpu-t4",
                "workloadProfileType": T4_PROFILE.workload_profile_type,
            },
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document["tags"].update({"gludd-owner-digest": "d" * 64}),
        lambda document: document.update(id="/subscriptions/wrong"),
        lambda document: document.update(name="other-environment"),
        lambda document: document.update(type="Microsoft.Compute/virtualMachines"),
        lambda document: document.update(location="westus"),
        lambda document: document["properties"].update(provisioningState="Failed"),
        lambda document: document["properties"].update(workloadProfiles="unsafe"),
    ],
)
def test_unowned_or_incompatible_environment_refuses_before_terraform_plan(
    mutation: Any,
) -> None:
    policy = _policy()
    document = _document(policy)
    mutation(document)
    runtime = _Runtime(policy, document=document)

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert captured.value.phase == "ownership"
    assert runtime.calls == ["read"]
    assert _SECRET not in repr(captured.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["resource_changes"].append(
            copy.deepcopy(plan["resource_changes"][0])
        ),
        lambda plan: plan["resource_changes"][0].update(address="other.resource"),
        lambda plan: plan["resource_changes"][0].update(mode="data"),
        lambda plan: plan["resource_changes"][0].update(type="azurerm_resource_group"),
        lambda plan: plan["resource_changes"][0]["change"].update(actions=["delete"]),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            name="other"
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            parent_id="/subscriptions/other"
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            type="Microsoft.Network/virtualNetworks@2025-01-01"
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            tags={}
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"]["body"].update(
            properties={}
        ),
    ],
)
def test_plan_audit_rejects_every_scope_ownership_or_profile_widening(
    mutation: Any,
) -> None:
    policy = _policy()
    plan = _plan_payload(policy, ["create"])
    mutation(plan)

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        audit_environment_plan(plan, policy, existed_before=False)

    assert captured.value.phase == "plan"


@pytest.mark.parametrize(
    ("existed_before", "actions"),
    [
        (False, ["no-op"]),
        (False, ["update"]),
        (True, ["create"]),
        (True, ["delete", "create"]),
    ],
)
def test_plan_action_must_match_observed_environment_state(
    existed_before: bool,
    actions: list[str],
) -> None:
    policy = _policy()

    with pytest.raises(AzureEnvironmentLifecycleError, match="plan"):
        audit_environment_plan(
            _plan_payload(policy, actions),
            policy,
            existed_before=existed_before,
        )


def test_partial_new_apply_failure_runs_terraform_recovery_and_absence_proof() -> None:
    policy = _policy()
    runtime = _Runtime(
        policy,
        document=None,
        fail_at="apply",
        partial_apply=True,
    )

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert captured.value.phase == "apply"
    assert runtime.calls == [
        "read",
        "plan",
        "apply",
        "read",
        "list-apps",
        "destroy",
        "read",
    ]
    assert runtime.document is None
    assert _SECRET not in repr(captured.value)


def test_post_apply_profile_drift_is_recovered_and_reported_as_readiness() -> None:
    policy = _policy(profiles=(_a100(),))
    runtime = _Runtime(
        policy,
        document=None,
        after_apply=_document(policy, profiles=(_t4(),)),
    )

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert captured.value.phase == "readiness"
    assert runtime.calls == [
        "read",
        "plan",
        "apply",
        "read",
        "read",
        "list-apps",
        "destroy",
        "read",
    ]
    assert runtime.document is None


def test_partial_create_recovery_must_prove_absence_not_merely_return() -> None:
    policy = _policy()
    app = f"{policy.resource_group_id}/providers/Microsoft.App/containerApps/gludd-app"
    runtime = _Runtime(
        policy,
        document=None,
        fail_at="apply",
        partial_apply=True,
        apps=(app,),
    )

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert captured.value.phase == "cleanup"
    assert runtime.document is not None
    assert "destroy" not in runtime.calls


def test_plan_failure_never_runs_cleanup_because_no_mutation_started() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=None, fail_at="plan")

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert captured.value.phase == "plan"
    assert runtime.calls == ["read", "plan"]


def test_failed_reconciliation_never_deletes_a_preexisting_environment() -> None:
    policy = _policy(profiles=(_a100(),))
    runtime = _Runtime(
        policy,
        document=_document(policy, profiles=(_t4(),)),
        plan_actions=["update"],
        fail_at="apply",
        partial_apply=True,
    )

    with pytest.raises(AzureEnvironmentLifecycleError, match="apply"):
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert runtime.calls == ["read", "plan", "apply"]
    assert runtime.document is not None


def test_idle_owned_environment_is_destroyed_then_proven_absent() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=_document(policy))
    traces: list[EnvironmentLifecycleTrace] = []

    result = release_azure_containerapp_environment(
        policy,
        runtime=runtime,
        trace_sink=traces.append,
    )

    assert result.disposition is EnvironmentLifecycleDisposition.DESTROYED
    assert runtime.calls == ["read", "list-apps", "destroy", "read"]
    assert traces[-1].event is EnvironmentLifecycleEvent.ABSENCE_VERIFIED


def test_environment_with_any_live_app_is_retained_without_destroy() -> None:
    policy = _policy()
    app = f"{policy.resource_group_id}/providers/Microsoft.App/containerApps/gludd-runner-a"
    runtime = _Runtime(policy, document=_document(policy), apps=(app,))

    result = release_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.RETAINED
    assert result.active_app_count == 1
    assert runtime.calls == ["read", "list-apps"]


def test_teardown_policy_can_retain_owned_environment_without_listing_apps() -> None:
    policy = _policy(teardown_when_idle=False)
    runtime = _Runtime(policy, document=_document(policy))

    result = release_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.RETAINED
    assert runtime.calls == ["read"]


def test_already_absent_environment_is_a_successful_idempotent_release() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=None)

    result = release_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.ABSENT
    assert runtime.calls == ["read"]


def test_release_refuses_unowned_environment_and_invalid_app_inventory() -> None:
    policy = _policy()
    unowned = _document(policy)
    cast(dict[str, str], unowned["tags"])["gludd-owner-digest"] = "d" * 64
    runtime = _Runtime(policy, document=unowned)
    with pytest.raises(AzureEnvironmentLifecycleError, match="ownership"):
        release_azure_containerapp_environment(policy, runtime=runtime)
    assert runtime.calls == ["read"]

    runtime = _Runtime(policy, document=_document(policy))
    runtime.apps = cast(Any, ["not-an-immutable-tuple"])
    with pytest.raises(AzureEnvironmentLifecycleError, match="inventory"):
        release_azure_containerapp_environment(policy, runtime=runtime)
    assert runtime.calls == ["read", "list-apps"]


def test_destroy_failure_or_remaining_environment_is_terminal_and_censored() -> None:
    policy = _policy()
    failing = _Runtime(policy, document=_document(policy), fail_at="destroy")
    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        release_azure_containerapp_environment(policy, runtime=failing)
    assert captured.value.phase == "destroy"
    assert _SECRET not in repr(captured.value)

    class StickyRuntime(_Runtime):
        def destroy(self, policy: AzureEnvironmentLifecyclePolicy) -> None:
            self.policies.append(policy)
            self._call("destroy")

    sticky = StickyRuntime(policy, document=_document(policy))
    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        release_azure_containerapp_environment(policy, runtime=sticky)
    assert captured.value.phase == "absence"


def test_lifecycle_traces_contain_counts_and_digests_but_no_resource_names() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=_document(policy))
    traces: list[EnvironmentLifecycleTrace] = []

    release_azure_containerapp_environment(
        policy,
        runtime=runtime,
        trace_sink=traces.append,
    )

    assert all(trace.operation_digest == policy.operation_digest for trace in traces)
    assert "gludd-gpu" not in repr(traces)
    assert _SUBSCRIPTION not in repr(traces)
    assert _OWNER not in repr(traces)


def test_trace_failure_stops_before_mutation_and_cleanup_trace_failure_is_terminal() -> None:
    policy = _policy()

    def broken(_trace: EnvironmentLifecycleTrace) -> None:
        raise RuntimeError(_SECRET)

    runtime = _Runtime(policy, document=None)
    with pytest.raises(AzureEnvironmentLifecycleError, match="trace"):
        ensure_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=broken,
        )
    assert runtime.calls == []

    runtime = _Runtime(policy, document=_document(policy))
    with pytest.raises(AzureEnvironmentLifecycleError, match="trace"):
        release_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=broken,
        )
    assert runtime.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subscription_id", "not-a-uuid"),
        ("resource_group", "../other"),
        ("environment_name", "other/environment"),
        ("location", "East US"),
        ("profiles", ()),
        ("profiles", (_t4(), _t4())),
        ("owner_digest", "short"),
        ("plan_digest", "short"),
        ("expires_at_utc", "tomorrow"),
        ("teardown_when_idle", "yes"),
    ],
)
def test_policy_rejects_ambiguous_or_unbounded_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(_policy(), **cast(Any, {field: value}))


def test_profile_rejects_unrecognized_name_type_pairs() -> None:
    with pytest.raises(ValueError, match="profile"):
        AzureEnvironmentProfile(
            profile_name="gpu-t4",
            workload_profile_type=A100_PROFILE.workload_profile_type,
        )


def test_public_boundaries_require_typed_policy_runtime_and_sink() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=None)
    assert isinstance(runtime, AzureContainerAppEnvironmentRuntime)

    with pytest.raises(ValueError, match="policy"):
        ensure_azure_containerapp_environment(cast(Any, object()), runtime=runtime)
    with pytest.raises(ValueError, match="runtime"):
        ensure_azure_containerapp_environment(policy, runtime=cast(Any, object()))
    with pytest.raises(ValueError, match="trace_sink"):
        ensure_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=cast(Any, object()),
        )
    with pytest.raises(ValueError, match="plan"):
        audit_environment_plan(
            cast(Any, []),
            policy,
            existed_before=False,
        )
