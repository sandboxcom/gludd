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
    all_known_actions,
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


class TestAllKnownActions:
    def test_returns_frozenset_nonempty(self):
        actions = all_known_actions()
        assert isinstance(actions, frozenset)
        assert len(actions) > 0

    def test_equals_provider_operations_flat_set(self):
        flat = frozenset(a for ops in PROVIDER_OPERATIONS.values() for a in ops)
        assert all_known_actions() == flat

    def test_every_action_passes_validation(self):
        for action in all_known_actions():
            ok, msg = validate_action_string(action)
            if not ok:
                assert "/action instead of /read" in msg, (
                    f"Known action failed validation with unexpected reason: {action} — {msg}"
                )

    def test_is_idempotent(self):
        a = all_known_actions()
        b = all_known_actions()
        assert a is b


class TestValidateActionStringEdgeCases:
    def test_none_input_rejected(self):
        ok, msg = validate_action_string(None)
        assert ok is False
        assert "empty" in msg

    def test_integer_input_rejected(self):
        ok, msg = validate_action_string(42)
        assert ok is False
        assert "empty" in msg

    def test_bool_input_rejected(self):
        ok, msg = validate_action_string(True)
        assert ok is False
        assert "empty" in msg

    def test_whitespace_only_rejected(self):
        ok, msg = validate_action_string("   ")
        assert ok is False
        assert "malformed" in msg

    def test_secrets_read_flagged(self):
        ok, msg = validate_action_string("Microsoft.KeyVault/vaults/secrets/read")
        assert ok is False
        assert "/action instead of /read" in msg

    def test_fully_qualified_resource_type(self):
        ok, _msg = validate_action_string("Microsoft.Network/virtualNetworks/subnets/join/action")
        assert ok is True

    def test_three_level_resource_type(self):
        ok, _msg = validate_action_string("Microsoft.Resources/deployments/operations/read")
        assert ok is True

    def test_deeply_nested_resource_type_passes(self):
        ok, _msg = validate_action_string("Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read")
        assert ok is True


class TestCheckSecurityCriticalDenialsActionsParam:
    def test_actions_param_filters_only_relevant(self):
        relevant = [
            "Microsoft.Compute/virtualMachines/runCommand/action",
            "Microsoft.Compute/virtualMachines/read",
        ]
        missing = check_security_critical_denials([], actions=relevant)
        assert missing == ["Microsoft.Compute/virtualMachines/runCommand/action"]

    def test_actions_param_no_security_ops_returns_empty(self):
        safe_actions = [
            "Microsoft.Compute/virtualMachines/read",
            "Microsoft.Network/virtualNetworks/read",
        ]
        missing = check_security_critical_denials([], actions=safe_actions)
        assert missing == []

    def test_actions_param_with_partial_not_actions(self):
        actions = [
            "Microsoft.Compute/virtualMachines/runCommand/action",
            "Microsoft.Authorization/roleAssignments/write",
        ]
        not_actions = ["Microsoft.Compute/virtualMachines/runCommand/action"]
        missing = check_security_critical_denials(not_actions, actions=actions)
        assert missing == ["Microsoft.Authorization/roleAssignments/write"]

    def test_no_actions_param_checks_all_eight(self):
        missing = check_security_critical_denials([])
        assert len(missing) == 8
        assert all(op in SECURITY_CRITICAL_OPS for op in missing)


class TestGenerateRoleDefinitionEdges:
    def test_empty_providers_produces_empty_actions(self):
        role = generate_role_definition(
            "Empty Providers",
            "Description with at least twenty characters for the minimum length check",
            [],
        )
        assert role["Name"] == "Empty Providers"
        assert role["Actions"] == []
        assert role["NotActions"] == []

    def test_auth_provider_moves_security_to_not_actions(self):
        role = generate_role_definition(
            "Auth Role",
            "A role definition with authorization provider at least twenty chars",
            ["Microsoft.Authorization"],
        )
        assert "Microsoft.Authorization/roleAssignments/read" in role["Actions"]
        assert "Microsoft.Authorization/roleAssignments/write" in role["NotActions"]
        assert "Microsoft.Authorization/roleAssignments/delete" not in role["Actions"]
        assert "Microsoft.Authorization/roleAssignments/delete" in role["NotActions"]

    def test_custom_scope_is_preserved(self):
        scope = "/subscriptions/abc-123/resourceGroups/my-rg"
        role = generate_role_definition(
            "Scoped Role",
            "A role with a custom scope value of at least twenty characters",
            ["Microsoft.Resources"],
            scope=scope,
        )
        assert role["AssignableScopes"] == [scope]

    def test_all_providers(self):
        role = generate_role_definition(
            "Full Role",
            "This description for a full provider role is at least twenty chars long",
            ["Microsoft.Compute", "Microsoft.Network", "Microsoft.Storage", "Microsoft.App"],
        )
        assert role["Name"] == "Full Role"
        assert len(role["Actions"]) > 10
        assert isinstance(role["DataActions"], list)
        assert isinstance(role["NotDataActions"], list)


class TestValidateAgainstAzureSchemaEdges:
    def test_actions_not_list_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with more than twenty characters",
                "Actions": "not-a-list",
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("must be a list" in m for m in messages)

    def test_not_actions_not_list_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with more than twenty characters",
                "Actions": [],
                "NotActions": "not-a-list",
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("NotActions must be a list" in m for m in messages)

    def test_assignable_scopes_empty_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with more than twenty characters",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": [],
            }
        )
        assert ok is False
        assert any("non-empty list" in m for m in messages)

    def test_assignable_scopes_not_list_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with more than twenty characters",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": "/subscriptions/...",
            }
        )
        assert ok is False
        assert any("non-empty list" in m for m in messages)

    def test_invalid_not_action_caught(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A proper description with enough chars for min check",
                "Actions": [],
                "NotActions": ["bogus-not-action"],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("Invalid not-action" in m for m in messages)

    def test_warning_does_not_cause_failure(self):
        role = generate_role_definition(
            "Warning Role",
            "A role that triggers a warning only for at least twenty characters",
            ["Microsoft.Resources"],
        )
        role["Actions"].append("Microsoft.SomeProvider/thing/read")
        ok, messages = validate_against_azure_schema(role)
        assert ok is True
        warnings = [m for m in messages if m.startswith("WARNING:")]
        assert len(warnings) == 1

    def test_name_not_string_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": 123,
                "Description": "A proper description with enough chars for minimum length",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("Name must be" in m for m in messages)

    def test_both_actions_and_not_actions_invalid(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Double Bad",
                "Description": "A role with invalid actions and not-actions both broken",
                "Actions": ["bogus-action"],
                "NotActions": ["bogus-not-action"],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("Invalid action" in m for m in messages)
        assert any("Invalid not-action" in m for m in messages)


class TestCrossReferenceConsistency:
    def test_known_actions_are_subset_of_provider_operations(self):
        provider_flat = frozenset(a for ops in PROVIDER_OPERATIONS.values() for a in ops)
        missing = KNOWN_RBAC_ACTIONS - provider_flat
        assert missing == frozenset(), f"KNOWN_RBAC_ACTIONS has entries not in PROVIDER_OPERATIONS: {missing}"

    def test_every_known_action_validates(self):
        for action in KNOWN_RBAC_ACTIONS:
            ok, _msg = validate_action_string(action)
            assert ok is True, f"KNOWN_RBAC_ACTIONS entry failed validation: {action}"

    def test_builtin_roles_have_unique_guids(self):
        guids = list(AZURE_BUILTIN_ROLES.values())
        assert len(guids) == len(set(guids)), "AZURE_BUILTIN_ROLES has duplicate GUIDs"

    def test_resource_providers_match_provider_operations_keys(self):
        provider_keys = set(PROVIDER_OPERATIONS.keys())
        resource_providers = set(AZURE_RESOURCE_PROVIDERS.keys())
        extra_in_ops = provider_keys - resource_providers
        assert not extra_in_ops, f"PROVIDER_OPERATIONS keys not in AZURE_RESOURCE_PROVIDERS: {extra_in_ops}"

    def test_security_critical_ops_all_in_provider_operations(self):
        provider_flat = frozenset(a for ops in PROVIDER_OPERATIONS.values() for a in ops)
        missing = SECURITY_CRITICAL_OPS - provider_flat
        assert missing == frozenset(), f"SECURITY_CRITICAL_OPS has entries not in PROVIDER_OPERATIONS: {missing}"

    def test_security_critical_ops_count_is_eight(self):
        assert len(SECURITY_CRITICAL_OPS) == 8
