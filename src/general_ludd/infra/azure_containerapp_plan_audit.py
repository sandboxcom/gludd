"""Strict Terraform plan allowlist for one app-only Azure live proof."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from general_ludd.infra.azure_containerapp_live_types import (
    AzureContainerAppLiveProofError,
    AzureContainerAppLiveProofFailure,
    AzureContainerAppLiveProofPolicy,
)


def _member(mapping: object, key: str) -> object:
    if not isinstance(mapping, Mapping) or key not in mapping:
        raise ValueError
    return mapping[key]


def _required_argument(arguments: object, marker: str, value: str) -> None:
    if (
        not isinstance(arguments, Sequence)
        or isinstance(arguments, (str, bytes))
        or any(not isinstance(item, str) for item in arguments)
    ):
        raise ValueError
    values = cast(Sequence[str], arguments)
    indexes = [index for index, item in enumerate(values) if item == marker]
    if len(indexes) != 1 or indexes[0] + 1 >= len(values):
        raise ValueError
    if values[indexes[0] + 1] != value:
        raise ValueError


def audit_containerapp_plan(
    plan: object,
    policy: AzureContainerAppLiveProofPolicy,
) -> None:
    """Require one remote app create and at most one inert policy artifact."""
    if not isinstance(policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("policy must be an AzureContainerAppLiveProofPolicy")
    stage = "shape"
    try:
        stage = "format"
        format_version = _member(plan, "format_version")
        if format_version not in {"1.0", "1.1", "1.2"}:
            raise ValueError
        stage = "change_count"
        changes = _member(plan, "resource_changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= 2:
            raise ValueError
        remote_changes: list[object] = []
        policy_changes: list[object] = []
        for candidate in changes:
            resource_type = _member(candidate, "type")
            if resource_type == "azapi_resource":
                remote_changes.append(candidate)
            elif resource_type == "terraform_data":
                policy_changes.append(candidate)
            else:
                raise ValueError
        if len(remote_changes) != 1 or len(policy_changes) > 1:
            raise ValueError
        if policy_changes:
            stage = "cost_policy_identity"
            policy_resource = policy_changes[0]
            expected_policy_fields = {
                "address": (
                    "module.gpu_cost_watchdog.terraform_data.gpu_cost_watchdog"
                ),
                "mode": "managed",
                "type": "terraform_data",
                "name": "gpu_cost_watchdog",
                "provider_name": "terraform.io/builtin/terraform",
            }
            if any(
                _member(policy_resource, key) != value
                for key, value in expected_policy_fields.items()
            ):
                raise ValueError
            stage = "cost_policy_action"
            policy_change = _member(policy_resource, "change")
            if (
                _member(policy_change, "actions") != ["create"]
                or _member(policy_change, "before") is not None
            ):
                raise ValueError
        stage = "resource_identity"
        resource = remote_changes[0]
        expected_resource_fields = {
            "address": "module.vllm_server.azapi_resource.vllm",
            "mode": "managed",
            "type": "azapi_resource",
            "name": "vllm",
            "provider_name": "registry.terraform.io/azure/azapi",
        }
        if any(
            _member(resource, key) != value
            for key, value in expected_resource_fields.items()
        ):
            raise ValueError
        stage = "action"
        change = _member(resource, "change")
        if _member(change, "actions") != ["create"] or _member(change, "before") is not None:
            raise ValueError
        stage = "resource_scope"
        after = _member(change, "after")
        expected_after = {
            "type": "Microsoft.App/containerApps@2025-01-01",
            "name": policy.app_name,
            "parent_id": policy.resource_group_id,
            "location": policy.location,
        }
        if any(_member(after, key) != value for key, value in expected_after.items()):
            raise ValueError
        stage = "environment_binding"
        body = _member(after, "body")
        properties = _member(body, "properties")
        if (
            _member(properties, "managedEnvironmentId") != policy.environment_id
            or _member(properties, "workloadProfileName")
            != policy.workload_profile_name
        ):
            raise ValueError
        stage = "configuration"
        configuration = _member(properties, "configuration")
        if (
            _member(configuration, "activeRevisionsMode") != "Single"
            or not isinstance(_member(configuration, "ingress"), Mapping)
        ):
            raise ValueError
        stage = "ingress"
        ingress = _member(configuration, "ingress")
        expected_ingress = {
            "external": True,
            "allowInsecure": False,
            "targetPort": 8000,
            "transport": "auto",
        }
        if any(_member(ingress, key) != value for key, value in expected_ingress.items()):
            raise ValueError
        stage = "network_restriction"
        restrictions = _member(ingress, "ipSecurityRestrictions")
        if not isinstance(restrictions, list) or len(restrictions) != 1:
            raise ValueError
        restriction = restrictions[0]
        expected_restriction = {
            "action": "Allow",
            "description": "Exact Gludd live-proof caller",
            "ipAddressRange": policy.allowed_cidr,
            "name": "gludd-live-proof-client",
        }
        if any(
            _member(restriction, key) != value
            for key, value in expected_restriction.items()
        ):
            raise ValueError
        stage = "container"
        template = _member(properties, "template")
        containers = _member(template, "containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError
        stage = "image"
        container = containers[0]
        if _member(container, "image") != policy.container_image:
            raise ValueError
        stage = "arguments"
        arguments = _member(container, "args")
        _required_argument(arguments, "--model", policy.model_name)
        _required_argument(arguments, "--revision", policy.model_revision)
        _required_argument(arguments, "--tokenizer-revision", policy.model_revision)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.PLAN_SCOPE,
            detail=stage,
        ) from None


__all__ = ("audit_containerapp_plan",)
