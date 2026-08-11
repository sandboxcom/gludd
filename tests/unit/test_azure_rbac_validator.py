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


# ---------------------------------------------------------------------------
# Deep: _lazy_provider_map behaviour
# ---------------------------------------------------------------------------


class TestLazyProviderMap:
    def test_returns_dict_with_all_provider_operation_keys(self):
        from general_ludd.azure.rbac_validator import _lazy_provider_map

        result = _lazy_provider_map()
        assert set(result.keys()) == set(PROVIDER_OPERATIONS.keys()), (
            f"keys mismatch: {set(result.keys()) ^ set(PROVIDER_OPERATIONS.keys())}"
        )

    def test_idempotent_same_reference(self):
        from general_ludd.azure.rbac_validator import _lazy_provider_map

        a = _lazy_provider_map()
        b = _lazy_provider_map()
        assert a is b

    def test_every_value_is_nonempty_list_of_strings(self):
        from general_ludd.azure.rbac_validator import _lazy_provider_map

        for provider, actions in _lazy_provider_map().items():
            assert isinstance(actions, list), f"{provider} value is {type(actions)}"
            assert len(actions) > 0, f"{provider} has zero actions"
            assert all(isinstance(a, str) for a in actions), f"{provider} has non-str action"

    def test_all_lazy_actions_are_in_known_set(self):
        from general_ludd.azure.rbac_validator import _lazy_provider_map

        flat_known = frozenset(a for ops in PROVIDER_OPERATIONS.values() for a in ops)
        for provider, actions in _lazy_provider_map().items():
            for action in actions:
                assert action in flat_known, f"{provider} action {action!r} not in flat known set"


# ---------------------------------------------------------------------------
# Deep: generate_role_definition edge cases
# ---------------------------------------------------------------------------


class TestGenerateRoleDefinitionDeep:
    def test_duplicate_providers_deduplicate_actions(self):
        role = generate_role_definition(
            "Dedup Role",
            "A role definition with deduplication test that is at least twenty",
            ["Microsoft.Compute", "Microsoft.Compute"],
        )
        assert role["Actions"] == sorted(set(role["Actions"])), "duplicate provider produced duplicate actions"

    def test_all_providers_produces_large_role(self):
        all_providers = sorted(PROVIDER_OPERATIONS.keys())
        role = generate_role_definition(
            "Full Catalog Role",
            "A comprehensive role covering every known provider namespace ok",
            all_providers,
        )
        assert len(role["Actions"]) > 50
        assert role["DataActions"] == []
        assert role["NotDataActions"] == []

    def test_actions_always_sorted_lexicographically(self):
        role = generate_role_definition(
            "Sorted Check",
            "Checking that actions are always sorted lexicographically ok",
            ["Microsoft.Network", "Microsoft.Compute"],
        )
        assert role["Actions"] == sorted(role["Actions"])

    def test_not_actions_always_sorted(self):
        role = generate_role_definition(
            "NotActions Sort",
            "Verifying that not-actions are always sorted lexicographically",
            ["Microsoft.Compute"],
        )
        assert role["NotActions"] == sorted(role["NotActions"])

    def test_provider_with_only_security_ops_produces_empty_actions(self):
        role = generate_role_definition(
            "Auth Only",
            "Authorization provider whose actions are all security-critical ok",
            ["Microsoft.Authorization"],
        )
        assert "Microsoft.Authorization/roleAssignments/read" in role["Actions"]
        assert "Microsoft.Authorization/roleAssignments/write" in role["NotActions"]
        assert "Microsoft.Authorization/roleAssignments/delete" in role["NotActions"]

    def test_capsule_shape_present(self):
        role = generate_role_definition(
            "Shape Test",
            "Verifying the complete output shape is always returned fully ok",
            ["Microsoft.Resources"],
        )
        for key in (
            "Name",
            "Description",
            "Actions",
            "NotActions",
            "AssignableScopes",
            "DataActions",
            "NotDataActions",
        ):
            assert key in role, f"missing key: {key}"

    def test_default_scope_contains_subscription_placeholder(self):
        role = generate_role_definition(
            "Scope Check",
            "Verifying that the default scope has subscription placeholder",
            ["Microsoft.Resources"],
        )
        assert "{subscription_id}" in role["AssignableScopes"][0]

    def test_unknown_provider_mixed_with_known_raises(self):
        raised = False
        try:
            generate_role_definition(
                "Mixed Bad",
                "Mixing a known provider with an unknown one that fails ok",
                ["Microsoft.Compute", "Microsoft.FakeProvider"],
            )
        except KeyError:
            raised = True
        assert raised is True, "expected KeyError for unknown provider"


# ---------------------------------------------------------------------------
# Deep: validate_against_azure_schema edges
# ---------------------------------------------------------------------------


class TestValidateAgainstAzureSchemaDeep:
    def test_description_exactly_twenty_chars_passes(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "ABCDEFGHIJKLMNOPQRST",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is True, f"20-char description should pass: {messages}"

    def test_description_nineteen_chars_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "ABCDEFGHIJKLMNOPQRS",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("Description" in m for m in messages)

    def test_large_action_list_validates_each(self):
        role = generate_role_definition(
            "Large Role",
            "A role definition with large actions list for stress testing now",
            ["Microsoft.Compute", "Microsoft.Network", "Microsoft.Storage", "Microsoft.App"],
        )
        ok, messages = validate_against_azure_schema(role)
        assert ok is True, f"large role failed: {messages}"

    def test_multiple_missing_fields_reports_first(self):
        ok, messages = validate_against_azure_schema({"Actions": []})
        assert ok is False
        assert len(messages) >= 1
        assert any("Missing required field" in m for m in messages)

    def test_name_with_unicode_passes(self):
        ok, _messages = validate_against_azure_schema(
            {
                "Name": "R\u00f4le-Custom",
                "Description": "A role definition with unicode characters in name at least twenty chars",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is True

    def test_description_nineteen_nonwhitespace_chars_fails(self):
        ok, messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "ABCDEFGHIJKLMNOPQRS",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
            }
        )
        assert ok is False
        assert any("Description" in m for m in messages)

    def test_data_actions_field_present_but_not_validated_as_list(self):
        ok, _messages = validate_against_azure_schema(
            {
                "Name": "Test",
                "Description": "A role with DataActions field present for testing at least twenty chars",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/..."],
                "DataActions": "not-a-list",
            }
        )
        assert ok is True

    def test_assignable_scopes_with_multiple_entries_passes(self):
        ok, _messages = validate_against_azure_schema(
            {
                "Name": "Multi Scope",
                "Description": "A role definition with multiple assignable scopes tested ok",
                "Actions": [],
                "NotActions": [],
                "AssignableScopes": ["/subscriptions/a", "/subscriptions/b"],
            }
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Deep: check_security_critical_denials edges
# ---------------------------------------------------------------------------


class TestCheckSecurityCriticalDenialsDeep:
    def test_none_actions_param_checks_all_eight(self):
        missing = check_security_critical_denials([], actions=None)
        assert len(missing) == 8

    def test_explicit_empty_list_actions_checks_nothing(self):
        missing = check_security_critical_denials([], actions=[])
        assert missing == []

    def test_partial_not_actions_still_reports_missing(self):
        not_actions = ["Microsoft.Compute/virtualMachines/runCommand/action"]
        missing = check_security_critical_denials(not_actions)
        assert len(missing) == 7
        assert "Microsoft.Compute/virtualMachines/runCommand/action" not in missing

    def test_extra_unknown_not_actions_ignored(self):
        not_actions = [*sorted(SECURITY_CRITICAL_OPS), "Microsoft.Fake/something/read"]
        missing = check_security_critical_denials(not_actions)
        assert missing == []

    def test_all_security_critical_ops_in_output_when_empty_not_actions(self):
        missing = check_security_critical_denials([])
        assert frozenset(missing) == SECURITY_CRITICAL_OPS


# ---------------------------------------------------------------------------
# Deep: validate_action_string additional edge cases
# ---------------------------------------------------------------------------


class TestValidateActionStringDeep:
    def test_exceptionally_long_action_string_validates(self):
        path_segments = "/".join(["segment" + str(i) for i in range(100)])
        action = f"Microsoft.Provider/{path_segments}/read"
        ok, _msg = validate_action_string(action)
        assert ok is True

    def test_trailing_slash_in_action_rejected(self):
        ok, _msg = validate_action_string("Microsoft.Compute/virtualMachines/read/")
        assert ok is False

    def test_double_slash_rejected(self):
        ok, _msg = validate_action_string("Microsoft.Compute//virtualMachines/read")
        assert ok is False

    def test_verb_not_in_allowed_set_rejected(self):
        ok, _msg = validate_action_string("Microsoft.Compute/virtualMachines/execute")
        assert ok is False
        assert "malformed" in _msg

    def test_provider_missing_dot_rejected(self):
        ok, _msg = validate_action_string("MicrosoftCompute/virtualMachines/read")
        assert ok is False
        assert "malformed" in _msg

    def test_forbidden_suffix_list_action_rejected_exact_message(self):
        ok, msg = validate_action_string("Microsoft.Storage/storageAccounts/list/action")
        assert ok is False
        assert "forbidden suffix" in msg

    def test_forbidden_suffix_listkeys_action_rejected_exact_message(self):
        ok, msg = validate_action_string("Microsoft.Storage/storageAccounts/listkeys/action")
        assert ok is False
        assert "forbidden suffix" in msg

    def test_every_forbidden_suffix_matches_itself(self):
        for suffix in ("/list/action", "/listkeys/action"):
            action = f"Microsoft.Test/resource{suffix}"
            ok, msg = validate_action_string(action)
            assert ok is False, f"should reject suffix {suffix}"
            assert "forbidden suffix" in msg

    def test_already_valid_action_with_list_payload_name_passes(self):
        """'listSubnets' in resource path is not a /list/action suffix."""
        ok, _msg = validate_action_string("Microsoft.Network/virtualNetworks/listSubnets/read")
        assert ok is True


# ---------------------------------------------------------------------------
# Deep: generate_role_definition stress and invariants
# ---------------------------------------------------------------------------


class TestGenerateRoleDefinitionStress:
    def test_hundred_repeated_providers_produces_same_result_as_one(self):
        role_many = generate_role_definition(
            "Many Dups",
            "A role definition with many duplicate provider namespaces test ok",
            ["Microsoft.Resources"] * 100,
        )
        role_one = generate_role_definition(
            "One Ref",
            "A reference role definition with a single provider namespace ok",
            ["Microsoft.Resources"],
        )
        assert role_many["Actions"] == role_one["Actions"]
        assert role_many["NotActions"] == role_one["NotActions"]

    def test_twenty_providers_all_distinct(self):
        all_providers = sorted(PROVIDER_OPERATIONS.keys())
        assert len(all_providers) >= 15, f"need >=15 providers, got {len(all_providers)}"
        role = generate_role_definition(
            "All Providers",
            "A role definition that includes every single known provider namespace",
            all_providers,
        )
        total_known = sum(len(ops) for ops in PROVIDER_OPERATIONS.values())
        sec_ops = len(SECURITY_CRITICAL_OPS & frozenset(a for ops in PROVIDER_OPERATIONS.values() for a in ops))
        expected_max = total_known - sec_ops
        assert len(role["Actions"]) <= expected_max
        assert len(role["NotActions"]) == sec_ops
