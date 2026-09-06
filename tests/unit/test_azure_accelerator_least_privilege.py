"""Least-privilege contract for Gludd's Azure Container Apps deployer."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import render_azure_accelerator_auth_args as subject

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION_ID = "11111111-2222-3333-4444-555555555555"
RESOURCE_GROUP = "gludd-models-eastus"
SP_NAME = "gludd accelerator 20260905"
ROLE_NAME = "General Ludd Accelerator Deployer"
RESOURCE_GROUP_SCOPE = (
    f"/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
)

EXPECTED_ACTIONS = frozenset(
    {
        "Microsoft.App/managedEnvironments/read",
        "Microsoft.App/managedEnvironments/join/action",
        "Microsoft.App/managedEnvironments/usages/read",
        "Microsoft.App/managedEnvironments/workloadProfileStates/read",
        "Microsoft.App/containerApps/read",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/containerApps/delete",
        "Microsoft.App/containerApps/revisions/read",
        "Microsoft.App/locations/containerAppOperationResults/read",
        "Microsoft.App/locations/containerAppOperationStatuses/read",
    }
)


def _cli_role() -> dict[str, object]:
    path = ROOT / "config" / "infra" / "azure-iam-policy.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _rest_role() -> dict[str, object]:
    path = ROOT / "config" / "infra" / "azure-iam-policy-cli.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_both_role_formats_grant_only_runtime_container_app_operations() -> None:
    cli = _cli_role()
    rest = _rest_role()["properties"]
    permission = rest["permissions"][0]

    assert frozenset(cli["Actions"]) == EXPECTED_ACTIONS
    assert frozenset(permission["actions"]) == EXPECTED_ACTIONS
    assert cli["NotActions"] == []
    assert permission["notActions"] == []
    assert cli["DataActions"] == []
    assert permission["dataActions"] == []


def test_role_has_no_secret_admin_provider_or_infrastructure_permissions() -> None:
    actions = {action.casefold() for action in _cli_role()["Actions"]}
    forbidden_fragments = (
        "listsecrets",
        "listcredentials",
        "sharedkeys",
        "microsoft.authorization/",
        "microsoft.cognitiveservices/",
        "microsoft.compute/",
        "microsoft.containerregistry/",
        "microsoft.insights/",
        "microsoft.network/",
        "microsoft.operationalinsights/",
        "microsoft.resources/deployments/",
        "resourcegroups/write",
        "resourcegroups/delete",
        "managedenvironments/write",
        "managedenvironments/delete",
        "/register/action",
    )

    assert not {
        fragment
        for fragment in forbidden_fragments
        if any(fragment in action for action in actions)
    }


def test_role_definition_and_identity_assignment_are_resource_group_scoped() -> None:
    role_arguments = subject.build_role_arguments(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
    )
    role = json.loads(role_arguments[4])
    auth_arguments = subject.build_auth_arguments(
        subscription_id=SUBSCRIPTION_ID,
        resource_group=RESOURCE_GROUP,
        service_principal_name=SP_NAME,
    )

    assert role["Name"] == ROLE_NAME
    assert role["AssignableScopes"] == [RESOURCE_GROUP_SCOPE]
    assert auth_arguments[auth_arguments.index("--scopes") + 1] == RESOURCE_GROUP_SCOPE
    assert f"/subscriptions/{SUBSCRIPTION_ID}" not in (
        value
        for index, value in enumerate(auth_arguments)
        if index != auth_arguments.index("--scopes") + 1
    )


def test_runtime_role_retains_only_cleanup_as_a_destructive_operation() -> None:
    destructive = {
        action
        for action in EXPECTED_ACTIONS
        if action.casefold().endswith(("/delete", "/action"))
    }

    assert destructive == {
        "Microsoft.App/managedEnvironments/join/action",
        "Microsoft.App/containerApps/delete",
    }
