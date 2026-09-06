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
    """Require exactly one create for the approved app and immutable model."""
    if not isinstance(policy, AzureContainerAppLiveProofPolicy):
        raise ValueError("policy must be an AzureContainerAppLiveProofPolicy")
    try:
        format_version = _member(plan, "format_version")
        if format_version not in {"1.0", "1.1", "1.2"}:
            raise ValueError
        changes = _member(plan, "resource_changes")
        if not isinstance(changes, list) or len(changes) != 1:
            raise ValueError
        resource = changes[0]
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
        change = _member(resource, "change")
        if _member(change, "actions") != ["create"] or _member(change, "before") is not None:
            raise ValueError
        after = _member(change, "after")
        expected_after = {
            "type": "Microsoft.App/containerApps@2025-01-01",
            "name": policy.app_name,
            "parent_id": policy.resource_group_id,
            "location": policy.location,
        }
        if any(_member(after, key) != value for key, value in expected_after.items()):
            raise ValueError
        body = _member(after, "body")
        properties = _member(body, "properties")
        if (
            _member(properties, "managedEnvironmentId") != policy.environment_id
            or _member(properties, "workloadProfileName")
            != policy.workload_profile_name
        ):
            raise ValueError
        configuration = _member(properties, "configuration")
        if (
            _member(configuration, "activeRevisionsMode") != "Single"
            or not isinstance(_member(configuration, "ingress"), Mapping)
        ):
            raise ValueError
        ingress = _member(configuration, "ingress")
        expected_ingress = {
            "external": True,
            "allowInsecure": False,
            "targetPort": 8000,
            "transport": "auto",
        }
        if any(_member(ingress, key) != value for key, value in expected_ingress.items()):
            raise ValueError
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
        template = _member(properties, "template")
        containers = _member(template, "containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError
        container = containers[0]
        if _member(container, "image") != policy.container_image:
            raise ValueError
        arguments = _member(container, "args")
        _required_argument(arguments, "--model", policy.model_name)
        _required_argument(arguments, "--revision", policy.model_revision)
        _required_argument(arguments, "--tokenizer-revision", policy.model_revision)
    except Exception:
        raise AzureContainerAppLiveProofError(
            AzureContainerAppLiveProofFailure.PLAN_SCOPE
        ) from None


__all__ = ("audit_containerapp_plan",)
