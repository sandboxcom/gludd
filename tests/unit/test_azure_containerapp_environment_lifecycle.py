"""Wide lifecycle contracts for Gludd-owned Azure Container Apps environments."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from general_ludd.infra.azure_idle_retention import (
    AzureIdleCostEvidence,
    AzureIdleRetentionPlan,
    AzureIdleRetentionPolicy,
    AzureProvisioningLatencyEvidence,
    AzureRetentionEvidenceSource,
    AzureRetentionLayer,
    AzureRetentionLayerKind,
    AzureRetentionPreset,
    plan_azure_idle_retention,
)

_SUBSCRIPTION = "12345678-1234-1234-1234-123456789abc"
_OWNER = "b" * 64
_PLAN = "c" * 64
_SECRET = "azure-secret-must-never-render"
_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


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
    }
    values.update(overrides)
    return AzureEnvironmentLifecyclePolicy(**cast(Any, values))


def _retention_plan(
    policy: AzureEnvironmentLifecyclePolicy,
    *,
    scope_digest: str | None = None,
    expected_next_demand_seconds: int = 1_800,
) -> AzureIdleRetentionPlan:
    zero_cost = AzureIdleCostEvidence(
        hourly_cost_microusd=0,
        observed_at=_NOW,
        source=AzureRetentionEvidenceSource.AZURE_BILLING_CONTRACT,
    )
    latency = AzureProvisioningLatencyEvidence(
        p50_seconds=18,
        p95_seconds=964,
        sample_count=2,
        observed_at=_NOW,
    )
    return plan_azure_idle_retention(
        (
            AzureRetentionLayer(
                kind=AzureRetentionLayerKind.MANAGED_ENVIRONMENT,
                idle_cost=zero_cost,
                provisioning_latency=latency,
            ),
        ),
        policy=AzureIdleRetentionPolicy(
            preset=AzureRetentionPreset.ZERO_COST_ONLY,
            max_idle_hourly_cost_microusd=0,
            max_idle_monthly_cost_microusd=0,
            max_retention_cost_microusd=0,
            max_retention_seconds=3_600,
            max_price_age_seconds=86_400,
            max_latency_age_seconds=86_400,
            max_cost_per_saved_hour_microusd=0,
        ),
        scope_digest=scope_digest or policy.operation_digest,
        now=_NOW,
        runnable_todo_count=0,
        expected_next_demand_seconds=expected_next_demand_seconds,
    )


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


def _provider_v2_plan_payload(
    policy: AzureEnvironmentLifecyclePolicy,
) -> dict[str, object]:
    plan = _plan_payload(policy, ["create"])
    resources = cast(list[dict[str, object]], plan["resource_changes"])
    change = cast(dict[str, object], resources[0]["change"])
    after = cast(dict[str, object], change["after"])
    after.update(
        {
            "create_headers": None,
            "create_query_parameters": None,
            "delete_headers": None,
            "delete_query_parameters": None,
            "id": None,
            "identity": None,
            "ignore_body_changes": None,
            "ignore_casing": False,
            "ignore_missing_property": True,
            "ignore_null_property": False,
            "ignore_other_items_in_list": None,
            "list_unique_id_property": None,
            "locks": None,
            "output": None,
            "read_headers": None,
            "read_query_parameters": None,
            "replace_triggers_external_values": None,
            "replace_triggers_refs": None,
            "response_export_values": [
                "id",
                "name",
                "properties.provisioningState",
                "properties.workloadProfiles",
                "tags",
            ],
            "retry": None,
            "schema_validation_enabled": True,
            "sensitive_body_version": None,
            "timeouts": None,
            "update_headers": None,
            "update_query_parameters": None,
        }
    )
    change.update(
        {
            "after_unknown": {"id": True, "output": True},
            "after_sensitive": {},
            "before_sensitive": False,
            "replace_paths": [],
        }
    )
    return plan


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


class _ImportingRuntime(_Runtime):
    def import_existing_environment(
        self,
        policy: AzureEnvironmentLifecyclePolicy,
    ) -> None:
        self.policies.append(policy)
        self._call("import")


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


def test_existing_owned_environment_is_imported_before_planning_when_supported() -> None:
    policy = _policy()
    runtime = _ImportingRuntime(policy, document=_document(policy))

    result = ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.REUSED
    assert runtime.calls == ["read", "import", "plan", "apply", "read"]


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


def test_platform_consumption_profile_is_accepted_but_not_managed_by_gludd() -> None:
    policy = _policy()
    document = _document(policy)
    properties = cast(dict[str, object], document["properties"])
    profiles = cast(list[object], properties["workloadProfiles"])
    profiles.insert(
        0,
        {"name": "Consumption", "workloadProfileType": "Consumption"},
    )
    runtime = _Runtime(policy, document=document)

    result = ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.REUSED
    planned_policy = runtime.policies[runtime.calls.index("plan")]
    assert planned_policy.profiles == (_t4(),)


def test_platform_only_environment_can_be_reconciled_with_required_gpu_profile() -> None:
    policy = _policy()
    document = _document(policy)
    properties = cast(dict[str, object], document["properties"])
    properties["workloadProfiles"] = [
        {"name": "Consumption", "workloadProfileType": "Consumption"}
    ]
    runtime = _Runtime(policy, document=document, plan_actions=["update"])

    result = ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.RECONCILED
    assert result.effective_profiles == (_t4(),)


@pytest.mark.parametrize(
    "profile",
    [
        {"name": "Consumption", "workloadProfileType": "Dedicated-D4"},
        {"name": "foreign", "workloadProfileType": "Consumption"},
        {"name": "foreign", "workloadProfileType": "D4"},
    ],
)
def test_unrecognized_observed_profiles_refuse_before_terraform(
    profile: dict[str, str],
) -> None:
    policy = _policy()
    document = _document(policy)
    properties = cast(dict[str, object], document["properties"])
    properties["workloadProfiles"] = [profile]
    runtime = _Runtime(policy, document=document)

    with pytest.raises(AzureEnvironmentLifecycleError, match="ownership"):
        ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert runtime.calls == ["read"]


def test_state_digest_is_stable_only_for_the_same_owner_and_resource() -> None:
    policy = _policy()

    assert policy.state_digest == _policy(
        profiles=(_a100(),),
        plan_digest="d" * 64,
        expires_at_utc="2026-09-06T21:00:00Z",
    ).state_digest
    assert policy.state_digest != _policy(owner_digest="e" * 64).state_digest
    assert policy.state_digest != _policy(environment_name="other-environment").state_digest


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


def test_inspection_failure_trace_classifies_tags_without_provider_content() -> None:
    policy = _policy()
    document = _document(policy)
    cast(dict[str, object], document["tags"])["gludd-owner-digest"] = "d" * 64
    runtime = _Runtime(policy, document=document)
    events: list[EnvironmentLifecycleTrace] = []

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=events.append,
        )

    assert captured.value.phase == "ownership"
    assert captured.value.reason == "tags"
    assert events[-1].event is EnvironmentLifecycleEvent.INSPECTION_FAILED
    assert events[-1].failure_reason == "tags"
    assert _SECRET not in repr(events)


def test_inspection_failure_trace_identifies_the_mismatched_identity_field() -> None:
    policy = _policy()
    document = _document(policy)
    document["location"] = "westus"
    runtime = _Runtime(policy, document=document)
    events: list[EnvironmentLifecycleTrace] = []

    with pytest.raises(AzureEnvironmentLifecycleError) as captured:
        ensure_azure_containerapp_environment(
            policy,
            runtime=runtime,
            trace_sink=events.append,
        )

    assert captured.value.reason == "identity_location"
    assert events[-1].failure_reason == "identity_location"


def test_provider_formatted_location_is_the_same_environment_identity() -> None:
    policy = _policy()
    document = _document(policy)
    document["location"] = "East US"
    runtime = _Runtime(policy, document=document, plan_actions=["no-op"])

    result = ensure_azure_containerapp_environment(policy, runtime=runtime)

    assert result.disposition is EnvironmentLifecycleDisposition.REUSED


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


def test_plan_audit_accepts_documented_azapi_v2_provider_bookkeeping() -> None:
    policy = _policy()

    assert (
        audit_environment_plan(
            _provider_v2_plan_payload(policy),
            policy,
            existed_before=False,
        )
        is True
    )


def test_plan_audit_accepts_opentofu_nested_false_metadata_shapes() -> None:
    """OpenTofu 1.15 emits structural false/empty sensitivity metadata."""
    policy = _policy()
    plan = _provider_v2_plan_payload(policy)
    resource_changes = cast(list[dict[str, object]], plan["resource_changes"])
    change = cast(dict[str, object], resource_changes[0]["change"])
    provider_shape = {
        "body": {"properties": {"workloadProfiles": [{}]}},
        "identity": [],
        "response_export_values": [False, False, False, False, False],
        "tags": {},
    }
    change["after_sensitive"] = copy.deepcopy(provider_shape)
    change["after_unknown"] = {
        **provider_shape,
        "id": True,
        "output": True,
    }

    assert audit_environment_plan(plan, policy, existed_before=False) is True


def test_plan_audit_accepts_exact_opentofu_resource_identity_metadata() -> None:
    policy = _policy()
    plan = _provider_v2_plan_payload(policy)
    resources = cast(list[dict[str, object]], plan["resource_changes"])
    change = cast(dict[str, object], resources[0]["change"])
    change["actions"] = ["update"]
    identity = {
        "id": policy.environment_id,
        "type": "Microsoft.App/managedEnvironments@2025-07-01",
    }
    change["before_identity"] = copy.deepcopy(identity)
    change["after_identity"] = copy.deepcopy(identity)

    assert audit_environment_plan(plan, policy, existed_before=True) is True


@pytest.mark.parametrize(
    "identity",
    (
        {"id": "/subscriptions/foreign", "type": None},
        {
            "id": "/subscriptions/foreign",
            "type": "Microsoft.App/managedEnvironments@2025-07-01",
        },
        {
            "id": "expected",
            "type": "Microsoft.App/managedEnvironments@2024-03-01",
        },
        {"id": "expected", "type": "Microsoft.Network/virtualNetworks@2025-01-01"},
        {"id": "expected"},
    ),
)
def test_plan_audit_rejects_foreign_resource_identity_metadata(
    identity: dict[str, object],
) -> None:
    policy = _policy()
    plan = _provider_v2_plan_payload(policy)
    resources = cast(list[dict[str, object]], plan["resource_changes"])
    change = cast(dict[str, object], resources[0]["change"])
    change["actions"] = ["update"]
    candidate = copy.deepcopy(identity)
    if candidate.get("id") == "expected":
        candidate["id"] = policy.environment_id
    change["before_identity"] = candidate
    change["after_identity"] = copy.deepcopy(candidate)

    with pytest.raises(AzureEnvironmentLifecycleError, match="plan"):
        audit_environment_plan(plan, policy, existed_before=True)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            sensitive_body={"properties": {"private": _SECRET}}
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            identity=[{"type": "SystemAssigned"}]
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            create_headers={"Authorization": _SECRET}
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            update_query_parameters={"unsafe": ["true"]}
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            locks=["/subscriptions/other"]
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            response_export_values=["*"]
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            schema_validation_enabled=False
        ),
        lambda plan: plan["resource_changes"][0]["change"]["after"].update(
            future_write_channel={"target": "/subscriptions/other"}
        ),
        lambda plan: plan["resource_changes"][0]["change"].update(
            after_unknown={"sensitive_body": True}
        ),
        lambda plan: plan["resource_changes"][0]["change"].update(
            after_sensitive={"sensitive_body": True}
        ),
        lambda plan: plan["resource_changes"][0]["change"].update(
            after_sensitive={"body": {"properties": {"secret": True}}}
        ),
        lambda plan: plan["resource_changes"][0]["change"].update(
            after_unknown={"body": {"properties": {"unreviewed": True}}}
        ),
        lambda plan: plan["resource_changes"][0]["change"].update(
            importing={"id": "/subscriptions/other"}
        ),
    ],
)
def test_plan_audit_rejects_hidden_azapi_mutation_or_exposure_channels(
    mutation: Any,
) -> None:
    policy = _policy()
    plan = _provider_v2_plan_payload(policy)
    mutation(plan)

    with pytest.raises(AzureEnvironmentLifecycleError, match="plan"):
        audit_environment_plan(plan, policy, existed_before=False)


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


def test_bounded_retention_plan_keeps_idle_environment_after_empty_inventory() -> None:
    policy = _policy()
    runtime = _Runtime(policy, document=_document(policy))
    traces: list[EnvironmentLifecycleTrace] = []

    result = release_azure_containerapp_environment(
        policy,
        runtime=runtime,
        retention_plan=_retention_plan(policy),
        now=_NOW,
        trace_sink=traces.append,
    )

    assert result.disposition is EnvironmentLifecycleDisposition.RETAINED
    assert result.active_app_count == 0
    assert runtime.calls == ["read", "list-apps"]
    assert traces[-1].event is EnvironmentLifecycleEvent.ENVIRONMENT_RETAINED
    assert traces[-1].retention_plan_digest == _retention_plan(policy).plan_digest
    assert traces[-1].retention_seconds_remaining == 1_800


def test_expired_or_foreign_retention_plan_cannot_bypass_owned_destroy() -> None:
    policy = _policy()
    expired = _Runtime(policy, document=_document(policy))

    result = release_azure_containerapp_environment(
        policy,
        runtime=expired,
        retention_plan=_retention_plan(policy),
        now=_NOW + timedelta(seconds=1_800),
    )

    assert result.disposition is EnvironmentLifecycleDisposition.DESTROYED
    assert expired.calls == ["read", "list-apps", "destroy", "read"]

    foreign = _Runtime(policy, document=_document(policy))
    with pytest.raises(AzureEnvironmentLifecycleError, match="retention"):
        release_azure_containerapp_environment(
            policy,
            runtime=foreign,
            retention_plan=_retention_plan(policy, scope_digest="d" * 64),
            now=_NOW,
        )
    assert foreign.calls == []


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
