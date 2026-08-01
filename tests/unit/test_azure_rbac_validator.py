"""Unit tests for ``general_ludd.azure.rbac_validator``."""

from __future__ import annotations

import re

from general_ludd.azure.rbac_validator import (
    AZURE_BUILTIN_ROLES,
    AZURE_RESOURCE_PROVIDERS,
    KNOWN_RBAC_ACTIONS,
    PROVIDER_OPERATIONS,
    SECRET_ACTION_PATTERNS,
    SECURITY_CRITICAL_OPS,
    check_security_critical_denials,
    generate_role_definition,
    validate_action_string,
    validate_against_azure_schema,
)


class TestKnownRbacActions:
    def test_is_nonempty_frozenset(self):
        assert isinstance(KNOWN_RBAC_ACTIONS, frozenset)
        assert len(KNOWN_RBAC_ACTIONS) > 0

    def test_contains_compute_actions(self):
        assert "Microsoft.Compute/virtualMachines/read" in KNOWN_RBAC_ACTIONS
        assert "Microsoft.Compute/virtualMachines/write" in KNOWN_RBAC_ACTIONS

    def test_contains_network_actions(self):
        assert "Microsoft.Network/virtualNetworks/read" in KNOWN_RBAC_ACTIONS

    def test_contains_app_actions(self):
        assert "Microsoft.App/containerApps/read" in KNOWN_RBAC_ACTIONS


class TestValidateActionString:
    def test_valid_compute_read(self):
        ok, msg = validate_action_string("Microsoft.Compute/virtualMachines/read")
        assert ok is True
        assert msg == "ok"

    def test_valid_network_write(self):
        ok, _msg = validate_action_string("Microsoft.Network/virtualNetworks/write")
        assert ok is True

    def test_valid_app_read(self):
        ok, _msg = validate_action_string("Microsoft.App/containerApps/read")
        assert ok is True

    def test_rejects_list_action_suffix(self):
        ok, msg = validate_action_string("Microsoft.Compute/virtualMachines/list/action")
        assert ok is False
        assert "forbidden suffix" in msg

    def test_rejects_listkeys_action_suffix(self):
        ok, msg = validate_action_string("Microsoft.Storage/storageAccounts/listkeys/action")
        assert ok is False
        assert "forbidden suffix" in msg

    def test_rejects_wrong_module_prefix(self):
        ok, msg = validate_action_string("Azure.Compute/virtualMachines/read")
        assert ok is False
        assert "malformed" in msg

    def test_rejects_bare_string(self):
        ok, msg = validate_action_string("read")
        assert ok is False
        assert "malformed" in msg

    def test_rejects_empty_string(self):
        ok, msg = validate_action_string("")
        assert ok is False
        assert "empty" in msg

    def test_valid_start_action(self):
        ok, _msg = validate_action_string("Microsoft.Compute/virtualMachines/start/action")
        assert ok is True

    def test_valid_query_action(self):
        ok, _msg = validate_action_string("Microsoft.OperationalInsights/workspaces/query/action")
        assert ok is True

    def test_accepts_sharedkeys_read(self):
        """sharedKeys/read is valid for OperationalInsights — not flagged by secret pattern check."""
        ok, msg = validate_action_string("Microsoft.OperationalInsights/workspaces/sharedKeys/read")
        assert ok is True
        assert msg == "ok"

    def test_rejects_listcredentials_read(self):
        ok, msg = validate_action_string("Microsoft.ContainerRegistry/registries/listCredentials/read")
        assert ok is False
        assert "/action instead of /read" in msg

    def test_rejects_listsecrets_read(self):
        ok, msg = validate_action_string("Microsoft.App/containerApps/listSecrets/read")
        assert ok is False
        assert "/action instead of /read" in msg

    def test_rejects_keys_read(self):
        ok, msg = validate_action_string("Microsoft.KeyVault/vaults/keys/read")
        assert ok is False
        assert "/action instead of /read" in msg

    def test_accepts_listcredentials_action(self):
        ok, _msg = validate_action_string("Microsoft.ContainerRegistry/registries/listCredentials/action")
        assert ok is True

    def test_accepts_listsecrets_action(self):
        ok, _msg = validate_action_string("Microsoft.App/containerApps/listSecrets/action")
        assert ok is True


class TestCheckSecurityCriticalDenials:
    def test_all_present_returns_empty(self):
        all_ops = sorted(SECURITY_CRITICAL_OPS)
        missing = check_security_critical_denials(all_ops)
        assert missing == []

    def test_missing_one_returns_it(self):
        not_actions = [
            op for op in sorted(SECURITY_CRITICAL_OPS) if op != "Microsoft.Compute/virtualMachines/runCommand/action"
        ]
        missing = check_security_critical_denials(not_actions)
        assert len(missing) == 1
        assert "Microsoft.Compute/virtualMachines/runCommand/action" in missing

    def test_empty_not_actions_returns_all_eight(self):
        missing = check_security_critical_denials([])
        assert len(missing) == len(SECURITY_CRITICAL_OPS)


class TestSecretActionPatterns:
    def test_dict_is_nonempty(self):
        assert isinstance(SECRET_ACTION_PATTERNS, dict)
        assert len(SECRET_ACTION_PATTERNS) > 0

    def test_sharedkeys_not_in_secret_patterns(self):
        """sharedKeys/read is NOT in SECRET_ACTION_PATTERNS — it is valid for OperationalInsights."""
        assert r"/sharedKeys/read$" not in SECRET_ACTION_PATTERNS

    def test_contains_listcredentials_pattern(self):
        assert r"/listCredentials/read$" in SECRET_ACTION_PATTERNS

    def test_contains_listsecrets_pattern(self):
        assert r"/listSecrets/read$" in SECRET_ACTION_PATTERNS

    def test_all_patterns_compile(self):
        for pat, msg in SECRET_ACTION_PATTERNS.items():
            compiled = re.compile(pat, re.IGNORECASE)
            test_str = "Microsoft.SomeProvider/resource" + pat.replace("\\", "").replace("$", "").replace("^", "")
            assert compiled.search(test_str), f"pattern {pat} didn't match {test_str!r}"
            assert msg and isinstance(msg, str)


class TestAzureBuiltinRoles:
    def test_is_nonempty_dict(self):
        assert isinstance(AZURE_BUILTIN_ROLES, dict)
        assert len(AZURE_BUILTIN_ROLES) >= 20

    def test_contains_contributor(self):
        assert "Contributor" in AZURE_BUILTIN_ROLES
        assert AZURE_BUILTIN_ROLES["Contributor"] == "b24988ac-6180-42a0-ab88-20f7382dd24c"

    def test_contains_owner(self):
        assert "Owner" in AZURE_BUILTIN_ROLES

    def test_contains_reader(self):
        assert "Reader" in AZURE_BUILTIN_ROLES

    def test_contains_acr_pull_push(self):
        assert "AcrPull" in AZURE_BUILTIN_ROLES
        assert "AcrPush" in AZURE_BUILTIN_ROLES

    def test_all_guids_are_valid_format(self):
        guid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for name, guid in AZURE_BUILTIN_ROLES.items():
            assert guid_re.match(guid), f"{name} GUID '{guid}' is not valid"


class TestAzureResourceProviders:
    def test_is_nonempty_dict(self):
        assert isinstance(AZURE_RESOURCE_PROVIDERS, dict)
        assert len(AZURE_RESOURCE_PROVIDERS) >= 10

    def test_contains_core_providers(self):
        assert "Microsoft.Compute" in AZURE_RESOURCE_PROVIDERS
        assert "Microsoft.Network" in AZURE_RESOURCE_PROVIDERS
        assert "Microsoft.Storage" in AZURE_RESOURCE_PROVIDERS
        assert "Microsoft.ContainerRegistry" in AZURE_RESOURCE_PROVIDERS
        assert "Microsoft.App" in AZURE_RESOURCE_PROVIDERS


class TestProviderOperations:
    def test_is_nonempty_dict(self):
        assert isinstance(PROVIDER_OPERATIONS, dict)
        assert len(PROVIDER_OPERATIONS) >= 10

    def test_all_values_are_frozensets(self):
        for provider, ops in PROVIDER_OPERATIONS.items():
            assert isinstance(ops, frozenset), f"{provider} ops not a frozenset"

    def test_every_action_has_valid_format(self):
        valid_re = re.compile(r"^Microsoft\.\w+(/\w+)+(/(read|write|delete|action))$")
        for provider, ops in PROVIDER_OPERATIONS.items():
            for action in ops:
                assert valid_re.match(action), f"Invalid action format in {provider}: {action}"

    def test_contains_policy_actions(self):
        all_ops = {a for ops in PROVIDER_OPERATIONS.values() for a in ops}
        expected = [
            "Microsoft.Compute/virtualMachines/read",
            "Microsoft.Network/virtualNetworks/read",
            "Microsoft.App/containerApps/read",
            "Microsoft.App/managedEnvironments/join/action",
            "Microsoft.ContainerRegistry/registries/read",
            "Microsoft.OperationalInsights/workspaces/read",
            "Microsoft.Resources/subscriptions/resourceGroups/read",
        ]
        for action in expected:
            assert action in all_ops, f"Missing action: {action}"


class TestGenerateRoleDefinition:
    def test_basic_compute_role(self):
        role = generate_role_definition(
            "Test Compute Reader",
            "A test role for reading compute resources only — minimum twenty characters",
            ["Microsoft.Compute"],
        )
        assert role["Name"] == "Test Compute Reader"
        assert "Microsoft.Compute/virtualMachines/read" in role["Actions"]
        assert "Microsoft.Compute/virtualMachines/write" in role["Actions"]
        assert isinstance(role["NotActions"], list)
        assert len(role["AssignableScopes"]) == 1

    def test_security_ops_in_not_actions(self):
        role = generate_role_definition(
            "Test Role",
            "This role has enough characters for the description minimum length check",
            ["Microsoft.Compute"],
        )
        assert "Microsoft.Compute/virtualMachines/runCommand/action" in role["NotActions"]
        assert "Microsoft.Compute/virtualMachines/runCommands/read" in role["NotActions"]

    def test_unknown_provider_raises(self):
        with __import__("pytest").raises(KeyError):
            generate_role_definition(
                "Bad Role",
                "A role with an unknown provider — must be at least twenty chars",
                ["Microsoft.FakeProvider"],
            )

    def test_multiple_providers(self):
        role = generate_role_definition(
            "Multi Provider Role",
            "This description is at least twenty characters long so it passes validation",
            ["Microsoft.Compute", "Microsoft.Network"],
        )
        assert any("Microsoft.Compute/" in a for a in role["Actions"])
        assert any("Microsoft.Network/" in a for a in role["Actions"])


class TestValidateAgainstAzureSchema:
    def test_valid_role_passes(self):
        role = generate_role_definition(
            "Valid Role",
            "A valid role definition with enough characters for the minimum description length",
            ["Microsoft.Resources"],
        )
        ok, messages = validate_against_azure_schema(role)
        assert ok is True, f"Unexpected errors: {messages}"

    def test_missing_required_field_fails(self):
        ok, messages = validate_against_azure_schema({"Name": "Bad"})
        assert ok is False
        assert any("Missing required field" in m for m in messages)

    def test_short_name_fails(self):
        ok, _messages = validate_against_azure_schema(
            {
                "Name": "",
                "Description": "A proper description with more than twenty characters",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False

    def test_short_description_fails(self):
        ok, _messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "short",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False

    def test_invalid_action_caught(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with enough chars for min",
                "Actions": ["Microsoft.Compute/virtualMachines/list/action"],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("forbidden suffix" in m for m in messages)

    def test_missing_security_denials(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Insecure Role",
                "Description": "This role is missing critical security denials — long enough desc",
                "Actions": ["Microsoft.Compute/virtualMachines/runCommand/action"],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("missing security-critical denials" in m.lower() for m in messages)

    def test_unknown_action_warns(self):
        role = generate_role_definition(
            "Basic Role",
            "A basic role definition with plenty of description characters",
            ["Microsoft.Resources"],
        )
        role["Actions"].append("Microsoft.SomeProvider/thing/read")
        ok, messages = validate_against_azure_schema(role)
        assert ok is True
        assert any("not in known provider catalog" in m for m in messages)
