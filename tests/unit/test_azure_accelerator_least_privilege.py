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
SUBSCRIPTION_SCOPE = f"/subscriptions/{SUBSCRIPTION_ID}"

EXPECTED_ACTIONS = frozenset(
    {
        "Microsoft.App/managedEnvironments/read",
        "Microsoft.App/managedEnvironments/write",
        "Microsoft.App/managedEnvironments/delete",
        "Microsoft.App/managedEnvironments/join/action",
        "Microsoft.App/managedEnvironments/usages/read",
        "Microsoft.App/managedEnvironments/workloadProfileStates/read",
        "Microsoft.App/containerApps/read",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/containerApps/delete",
        "Microsoft.App/containerApps/revisions/read",
        "Microsoft.App/locations/containerAppOperationResults/read",
        "Microsoft.App/locations/containerAppOperationStatuses/read",
        "Microsoft.App/locations/managedEnvironmentOperationResults/read",
        "Microsoft.App/locations/managedEnvironmentOperationStatuses/read",
        "Microsoft.Insights/metrics/read",
        "Microsoft.Resources/subscriptions/resourceGroups/read",
        "Microsoft.Resources/subscriptions/resourceGroups/write",
    }
)


def _cli_role() -> dict[str, object]:
    path = ROOT / "config" / "infra" / "azure-iam-policy.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _rest_role() -> dict[str, object]:
    path = ROOT / "config" / "infra" / "azure-iam-policy-cli.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_both_role_formats_grant_only_owned_lifecycle_and_metrics_operations() -> None:
    cli = _cli_role()
    rest = _rest_role()["properties"]
    permission = rest["permissions"][0]

    assert frozenset(cli["Actions"]) == EXPECTED_ACTIONS
    assert frozenset(permission["actions"]) == EXPECTED_ACTIONS
    assert len(EXPECTED_ACTIONS) == 17
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
        "microsoft.network/",
        "microsoft.operationalinsights/",
        "microsoft.resources/deployments/",
        "resourcegroups/delete",
        "/register/action",
    )

    assert not {
        fragment
        for fragment in forbidden_fragments
        if any(fragment in action for action in actions)
    }
    assert {
        action
        for action in actions
        if action.startswith("microsoft.insights/")
    } == {"microsoft.insights/metrics/read"}


def test_top_level_identity_has_parent_scope_required_to_create_resource_group() -> None:
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
    assert role["AssignableScopes"] == [SUBSCRIPTION_SCOPE]
    assert auth_arguments[auth_arguments.index("--scopes") + 1] == SUBSCRIPTION_SCOPE


def test_runtime_role_retains_only_owned_lifecycle_as_mutating_operations() -> None:
    mutating = {
        action
        for action in EXPECTED_ACTIONS
        if action.casefold().endswith(("/write", "/delete", "/action"))
    }

    assert mutating == {
        "Microsoft.App/managedEnvironments/write",
        "Microsoft.App/managedEnvironments/delete",
        "Microsoft.App/managedEnvironments/join/action",
        "Microsoft.App/containerApps/write",
        "Microsoft.App/containerApps/delete",
        "Microsoft.Resources/subscriptions/resourceGroups/write",
    }
