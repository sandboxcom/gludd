"""Unit tests for azure-iam-policy.json and the validate_azure_iam_policy.py script.

Verifies JSON validity, required fields, RBAC action format, forbidden suffix
patterns, security-critical denials, subscription scopes, and no duplicates.
Also tests the validate_action_format function directly.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "config" / "infra"
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

from validate_azure_iam_policy import validate_action_format  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policy() -> dict:
    path = INFRA_DIR / "azure-iam-policy.json"
    assert path.exists(), f"Missing: {path}"
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def all_actions(policy: dict) -> list[str]:
    return policy.get("Actions", []) + policy.get("NotActions", [])


# ---------------------------------------------------------------------------
# JSON validity and required fields
# ---------------------------------------------------------------------------


class TestPolicyStructure:
    """Policy JSON is valid and has all required Azure RBAC fields."""

    REQUIRED_FIELDS = frozenset({"Name", "Description", "Actions", "NotActions", "AssignableScopes"})

    def test_is_valid_json(self, policy: dict) -> None:
        assert isinstance(policy, dict)

    def test_has_all_required_fields(self, policy: dict) -> None:
        missing = self.REQUIRED_FIELDS - set(policy.keys())
        assert not missing, f"Missing required fields: {missing}"

    def test_name_is_non_empty_string(self, policy: dict) -> None:
        name = policy.get("Name")
        assert isinstance(name, str) and len(name) > 0

    def test_description_is_non_empty_string(self, policy: dict) -> None:
        desc = policy.get("Description")
        assert isinstance(desc, str) and len(desc) > 0

    def test_actions_is_non_empty_list(self, policy: dict) -> None:
        actions = policy.get("Actions")
        assert isinstance(actions, list) and len(actions) > 0

    def test_not_actions_is_list(self, policy: dict) -> None:
        not_actions = policy.get("NotActions")
        assert isinstance(not_actions, list)

    def test_assignable_scopes_is_non_empty_list(self, policy: dict) -> None:
        scopes = policy.get("AssignableScopes")
        assert isinstance(scopes, list) and len(scopes) > 0


# ---------------------------------------------------------------------------
# RBAC action format validation
# ---------------------------------------------------------------------------


class TestActionFormat:
    """All action strings must match the Azure RBAC format Microsoft.Provider/resourceType/action."""

    ACTION_PATTERN = re.compile(r"^Microsoft\.\w+/([\w.]+/)*(read|write|delete|action)$")

    def test_all_actions_match_rbac_format(self, all_actions: list[str]) -> None:
        bad = [a for a in all_actions if not self.ACTION_PATTERN.match(a)]
        assert not bad, f"Actions with invalid RBAC format: {bad}"

    def test_no_forbidden_list_action_suffix(self, all_actions: list[str]) -> None:
        bad = [a for a in all_actions if re.search(r"/list/action$", a, re.IGNORECASE)]
        assert not bad, f"Actions with /list/action suffix: {bad}"

    def test_no_forbidden_get_action_suffix(self, all_actions: list[str]) -> None:
        bad = [a for a in all_actions if re.search(r"/get/action$", a, re.IGNORECASE)]
        assert not bad, f"Actions with /get/action suffix: {bad}"

    def test_no_forbidden_create_action_suffix(self, all_actions: list[str]) -> None:
        bad = [a for a in all_actions if re.search(r"/create/action$", a, re.IGNORECASE)]
        assert not bad, f"Actions with /create/action suffix: {bad}"

    def test_no_forbidden_update_action_suffix(self, all_actions: list[str]) -> None:
        bad = [a for a in all_actions if re.search(r"/update/action$", a, re.IGNORECASE)]
        assert not bad, f"Actions with /update/action suffix: {bad}"


# ---------------------------------------------------------------------------
# No duplicate actions
# ---------------------------------------------------------------------------


class TestNoDuplicateActions:
    """No action string may appear more than once across Actions + NotActions."""

    def test_no_duplicate_actions(self, all_actions: list[str]) -> None:
        seen: dict[str, int] = {}
        for a in all_actions:
            seen[a] = seen.get(a, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        assert not dups, f"Duplicate actions (count > 1): {dups}"


# ---------------------------------------------------------------------------
# Security-critical denials in NotActions
# ---------------------------------------------------------------------------


class TestSecurityCriticalDenials:
    """All 8 security-critical actions must be denied in NotActions."""

    REQUIRED_DENIALS = frozenset(
        {
            "Microsoft.Compute/virtualMachines/runCommand/action",
            "Microsoft.Compute/virtualMachines/runCommands/read",
            "Microsoft.Compute/virtualMachines/runCommands/write",
            "Microsoft.Compute/virtualMachines/runCommands/delete",
            "Microsoft.Authorization/roleAssignments/write",
            "Microsoft.Authorization/roleAssignments/delete",
            "Microsoft.Authorization/roleDefinitions/write",
            "Microsoft.Authorization/roleDefinitions/delete",
        }
    )

    def test_all_8_security_denials_present(self, policy: dict) -> None:
        denied = frozenset(policy.get("NotActions", []))
        missing = self.REQUIRED_DENIALS - denied
        assert not missing, f"NotActions missing security-critical denials: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Data plane actions
# ---------------------------------------------------------------------------


class TestDataActionsEmpty:
    """DataActions and NotDataActions must be empty for an Azure RBAC role."""

    def test_data_actions_is_empty(self, policy: dict) -> None:
        assert policy.get("DataActions") == [], f"DataActions must be empty, got: {policy.get('DataActions')}"

    def test_not_data_actions_is_empty(self, policy: dict) -> None:
        assert policy.get("NotDataActions") == [], f"NotDataActions must be empty, got: {policy.get('NotDataActions')}"


# ---------------------------------------------------------------------------
# Assignable scopes
# ---------------------------------------------------------------------------


class TestAssignableScopes:
    """AssignableScopes must contain at least one subscription-level entry."""

    def test_has_subscription_level_scope(self, policy: dict) -> None:
        scopes = policy.get("AssignableScopes", [])
        has_sub = any(
            "{subscription_id}" in s or re.match(r"^/subscriptions/[0-9a-f-]+", s) or s == "/" for s in scopes
        )
        assert has_sub, f"No subscription-level scope found in: {scopes}"


# ---------------------------------------------------------------------------
# validate_action_format function from the script
# ---------------------------------------------------------------------------


class TestValidateActionFormatFunction:
    """Direct tests of validate_azure_iam_policy.validate_action_format()."""

    def test_valid_read_action_returns_no_errors(self) -> None:
        errors = validate_action_format("Microsoft.Compute/virtualMachines/read")
        assert errors == []

    def test_valid_write_action_returns_no_errors(self) -> None:
        errors = validate_action_format("Microsoft.Network/virtualNetworks/write")
        assert errors == []

    def test_valid_delete_action_returns_no_errors(self) -> None:
        errors = validate_action_format("Microsoft.Resources/resourceGroups/delete")
        assert errors == []

    def test_valid_nested_resource_action_returns_no_errors(self) -> None:
        errors = validate_action_format("Microsoft.Network/virtualNetworks/subnets/join/action")
        assert errors == []

    def test_list_action_suffix_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.Compute/virtualMachines/list/action")
        assert len(errors) > 0
        assert any("use /read instead of /list/action" in e.lower() for e in errors)

    def test_get_action_suffix_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.Compute/virtualMachines/get/action")
        assert len(errors) > 0
        assert any("use /read instead of /get/action" in e.lower() for e in errors)

    def test_create_action_suffix_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.Compute/virtualMachines/create/action")
        assert len(errors) > 0
        assert any("use /write instead of /create/action" in e.lower() for e in errors)

    def test_update_action_suffix_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.Compute/virtualMachines/update/action")
        assert len(errors) > 0
        assert any("use /write instead of /update/action" in e.lower() for e in errors)

    def test_invalid_module_name_is_rejected(self) -> None:
        errors = validate_action_format("NotMicrosoft.Something/something/read")
        assert len(errors) > 0

    def test_missing_module_is_rejected(self) -> None:
        errors = validate_action_format("read")
        assert len(errors) > 0

    def test_actual_policy_actions_all_pass(self, all_actions: list[str]) -> None:
        for action in all_actions:
            errors = validate_action_format(action)
            assert errors == [], f"Action '{action}' should be valid: {errors}"


# ---------------------------------------------------------------------------
# Secret / key / credential patterns — /read on secret ops should fail
# ---------------------------------------------------------------------------


class TestSecretActionPatterns:
    """Secret/key/credential operations using /read should be flagged by validate_action_format."""

    def test_sharedkeys_read_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.OperationalInsights/workspaces/sharedKeys/read")
        assert len(errors) > 0
        assert any("/action instead of /read" in e.lower() for e in errors)

    def test_listcredentials_read_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.ContainerRegistry/registries/listCredentials/read")
        assert len(errors) > 0
        assert any("/action instead of /read" in e.lower() for e in errors)

    def test_listsecrets_read_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.App/containerApps/listSecrets/read")
        assert len(errors) > 0
        assert any("/action instead of /read" in e.lower() for e in errors)

    def test_keys_read_is_rejected(self) -> None:
        errors = validate_action_format("Microsoft.KeyVault/vaults/keys/read")
        assert len(errors) > 0
        assert any("/action instead of /read" in e.lower() for e in errors)

    def test_listcredentials_action_passes(self) -> None:
        errors = validate_action_format("Microsoft.ContainerRegistry/registries/listCredentials/action")
        assert errors == []

    def test_listsecrets_action_passes(self) -> None:
        errors = validate_action_format("Microsoft.App/containerApps/listSecrets/action")
        assert errors == []


# ---------------------------------------------------------------------------
# Provider operations cross-reference (warn-only — Azure adds new actions)
# ---------------------------------------------------------------------------


class TestProviderOperationsCrossReference:
    """Validate script should cross-reference actions against PROVIDER_OPERATIONS."""

    def test_imports_known_actions(self) -> None:
        from general_ludd.azure.rbac_validator import all_known_actions

        known = all_known_actions()
        assert isinstance(known, frozenset)
        assert len(known) > 50
