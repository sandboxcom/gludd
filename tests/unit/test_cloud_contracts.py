"""Deep tests for cloud IAM data contracts — dataclasses, factories, methods,
edge cases, equality, serialization, and integration between contract types.
"""

from __future__ import annotations

import dataclasses

import pytest

from general_ludd.cloud.contracts import (
    CloudFunction,
    CloudRoleDefinition,
    PersonaRoleMap,
    ValidationResult,
)

# ── CloudRoleDefinition ───────────────────────────────────────────────────────


class TestCloudRoleDefinitionDefaults:
    def test_default_lists_are_empty_and_independent(self) -> None:
        r1 = CloudRoleDefinition(provider="aws", name="r", description="d")
        r2 = CloudRoleDefinition(provider="aws", name="r", description="d")
        assert r1.actions is not r2.actions
        assert r1.not_actions is not r2.not_actions
        assert r1.data_actions is not r2.data_actions
        assert r1.not_data_actions is not r2.not_data_actions
        assert r1.assignable_scopes is not r2.assignable_scopes

    def test_mutation_is_not_shared(self) -> None:
        r1 = CloudRoleDefinition(provider="aws", name="r", description="d")
        r2 = CloudRoleDefinition(provider="aws", name="r", description="d")
        r1.actions.append("s3:GetObject")
        assert r2.actions == []

    def test_empty_provider_and_name_allowed(self) -> None:
        r = CloudRoleDefinition(provider="", name="", description="")
        assert r.provider == ""
        assert r.name == ""

    def test_special_characters_in_name(self) -> None:
        r = CloudRoleDefinition(
            provider="azure",
            name="Custom Role (Dev) — v2.0",
            description="Allow read on */* with conditions",
        )
        assert r.name == "Custom Role (Dev) — v2.0"
        assert "(" in r.name
        assert "—" in r.name


class TestCloudRoleDefinitionEquality:
    def test_identical_instances_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d")
        b = CloudRoleDefinition(provider="aws", name="r", description="d")
        assert a == b

    def test_different_provider_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d")
        b = CloudRoleDefinition(provider="gcp", name="r", description="d")
        assert a != b

    def test_different_name_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="x", description="d")
        b = CloudRoleDefinition(provider="aws", name="y", description="d")
        assert a != b

    def test_different_actions_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d", actions=["a1"])
        b = CloudRoleDefinition(provider="aws", name="r", description="d", actions=["a2"])
        assert a != b

    def test_different_not_actions_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d", not_actions=["na1"])
        b = CloudRoleDefinition(provider="aws", name="r", description="d", not_actions=["na2"])
        assert a != b

    def test_different_data_actions_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d", data_actions=["da1"])
        b = CloudRoleDefinition(provider="aws", name="r", description="d", data_actions=["da2"])
        assert a != b

    def test_different_assignable_scopes_not_equal(self) -> None:
        a = CloudRoleDefinition(provider="aws", name="r", description="d", assignable_scopes=["/sub1"])
        b = CloudRoleDefinition(provider="aws", name="r", description="d", assignable_scopes=["/sub2"])
        assert a != b

    def test_not_hashable(self) -> None:
        r = CloudRoleDefinition(provider="aws", name="r", description="d")
        with pytest.raises(TypeError):
            hash(r)


class TestCloudRoleDefinitionFullConstruction:
    def test_all_fields_set(self) -> None:
        r = CloudRoleDefinition(
            provider="azure",
            name="Storage Reader",
            description="Read-only access to blob storage",
            actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            not_actions=["Microsoft.Storage/storageAccounts/delete"],
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read/action"],
            not_data_actions=["Microsoft.Storage/storageAccounts/read/action"],
            assignable_scopes=["/subscriptions/abc-123"],
        )
        assert r.provider == "azure"
        assert r.name == "Storage Reader"
        assert len(r.actions) == 1
        assert len(r.not_actions) == 1
        assert len(r.data_actions) == 1
        assert len(r.not_data_actions) == 1
        assert len(r.assignable_scopes) == 1

    def test_many_actions_preserved(self) -> None:
        actions = [f"s3:Action{i}" for i in range(100)]
        r = CloudRoleDefinition(
            provider="aws",
            name="bulk",
            description="x",
            actions=actions,
        )
        assert r.actions == actions
        assert len(r.actions) == 100

    def test_multiple_scopes(self) -> None:
        scopes = ["/subscriptions/a", "/subscriptions/b", "/management-groups/c"]
        r = CloudRoleDefinition(
            provider="azure",
            name="multi-scope",
            description="x",
            assignable_scopes=scopes,
        )
        assert r.assignable_scopes == scopes


class TestCloudRoleDefinitionSerialization:
    def test_asdict_includes_all_fields(self) -> None:
        r = CloudRoleDefinition(
            provider="aws",
            name="reader",
            description="read only",
            actions=["s3:GetObject"],
            not_actions=["s3:DeleteObject"],
            data_actions=[],
            not_data_actions=[],
            assignable_scopes=["arn:aws:iam::123:role/test"],
        )
        d = dataclasses.asdict(r)
        assert d["provider"] == "aws"
        assert d["name"] == "reader"
        assert d["actions"] == ["s3:GetObject"]
        assert d["not_actions"] == ["s3:DeleteObject"]
        assert d["assignable_scopes"] == ["arn:aws:iam::123:role/test"]

    def test_asdict_default_list_is_empty(self) -> None:
        r = CloudRoleDefinition(provider="aws", name="r", description="d")
        d = dataclasses.asdict(r)
        assert d["actions"] == []
        assert d["not_actions"] == []
        assert d["data_actions"] == []
        assert d["assignable_scopes"] == []

    def test_fields_match_expected(self) -> None:
        field_names = {f.name for f in dataclasses.fields(CloudRoleDefinition)}
        assert field_names == {
            "provider",
            "name",
            "description",
            "actions",
            "not_actions",
            "data_actions",
            "not_data_actions",
            "assignable_scopes",
        }


class TestCloudRoleDefinitionSubstitution:
    """Verifies CloudRoleDefinition can substitute where contract expects it."""

    def test_instance_passed_to_function_accepting_provider(self) -> None:
        def get_provider(role: CloudRoleDefinition) -> str:
            return role.provider

        role = CloudRoleDefinition(provider="gcp", name="r", description="d")
        assert get_provider(role) == "gcp"

    def test_instance_as_dict_key_by_attribute(self) -> None:
        roles = [
            CloudRoleDefinition(provider="aws", name="s3-reader", description="d"),
            CloudRoleDefinition(provider="gcp", name="storage-viewer", description="d"),
        ]
        by_provider = {r.provider: r for r in roles}
        assert by_provider["aws"].name == "s3-reader"
        assert by_provider["gcp"].name == "storage-viewer"


# ── CloudFunction ─────────────────────────────────────────────────────────────


class TestCloudFunctionDefaults:
    def test_required_denial_defaults_to_empty(self) -> None:
        fn = CloudFunction(provider="aws", name="list_buckets", category="storage", risk_level="low")
        assert fn.required_denial == ""

    def test_instances_with_same_fields_equal(self) -> None:
        a = CloudFunction(provider="aws", name="fn", category="cat", risk_level="low")
        b = CloudFunction(provider="aws", name="fn", category="cat", risk_level="low")
        assert a == b

    def test_instances_with_different_denial_not_equal(self) -> None:
        a = CloudFunction(
            provider="aws",
            name="fn",
            category="cat",
            risk_level="low",
            required_denial="act:Deny1",
        )
        b = CloudFunction(
            provider="aws",
            name="fn",
            category="cat",
            risk_level="low",
            required_denial="act:Deny2",
        )
        assert a != b

    def test_not_hashable(self) -> None:
        fn = CloudFunction(provider="aws", name="fn", category="cat", risk_level="low")
        with pytest.raises(TypeError):
            hash(fn)


class TestCloudFunctionRiskLevels:
    def test_low_risk(self) -> None:
        fn = CloudFunction(provider="gcp", name="read", category="data", risk_level="low")
        assert fn.risk_level == "low"

    def test_high_risk_with_denial(self) -> None:
        fn = CloudFunction(
            provider="azure",
            name="delete_vm",
            category="compute",
            risk_level="high",
            required_denial="Microsoft.Compute/virtualMachines/delete",
        )
        assert fn.risk_level == "high"
        assert fn.required_denial != ""


class TestCloudFunctionSerialization:
    def test_asdict_all_fields(self) -> None:
        fn = CloudFunction(
            provider="aws",
            name="delete_bucket",
            category="storage",
            risk_level="high",
            required_denial="s3:DeleteBucket",
        )
        d = dataclasses.asdict(fn)
        assert d["provider"] == "aws"
        assert d["name"] == "delete_bucket"
        assert d["category"] == "storage"
        assert d["risk_level"] == "high"
        assert d["required_denial"] == "s3:DeleteBucket"

    def test_fields_match_expected(self) -> None:
        field_names = {f.name for f in dataclasses.fields(CloudFunction)}
        assert field_names == {"provider", "name", "category", "risk_level", "required_denial"}


# ── PersonaRoleMap ────────────────────────────────────────────────────────────


class TestPersonaRoleMapDefaults:
    def test_default_assignments_empty(self) -> None:
        pmap = PersonaRoleMap(persona="dev", provider="aws")
        assert pmap.assignments == []

    def test_default_factory_independent_instances(self) -> None:
        a = PersonaRoleMap(persona="dev", provider="aws")
        b = PersonaRoleMap(persona="dev", provider="aws")
        assert a.assignments is not b.assignments

    def test_not_hashable(self) -> None:
        pmap = PersonaRoleMap(persona="dev", provider="aws")
        with pytest.raises(TypeError):
            hash(pmap)


class TestPersonaRoleMapRoles:
    def test_empty_assignments_returns_empty(self) -> None:
        pmap = PersonaRoleMap(persona="dev", provider="aws")
        assert pmap.roles() == []

    def test_single_assignment(self) -> None:
        pmap = PersonaRoleMap(
            persona="admin",
            provider="azure",
            assignments=[("Contributor", "/subscriptions/sub", True)],
        )
        assert pmap.roles() == ["Contributor"]

    def test_multiple_roles(self) -> None:
        pmap = PersonaRoleMap(
            persona="ops",
            provider="gcp",
            assignments=[
                ("roles/viewer", "org", True),
                ("roles/storage.admin", "project/x", False),
                ("roles/compute.admin", "project/x", False),
            ],
        )
        assert pmap.roles() == [
            "roles/viewer",
            "roles/storage.admin",
            "roles/compute.admin",
        ]

    def test_builtin_flag_included_but_not_in_role_list(self) -> None:
        pmap = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[
                ("AdminAccess", "*", True),
                ("ReadOnlyAccess", "*", True),
                ("CustomDeny", "arn:...", False),
            ],
        )
        roles = pmap.roles()
        assert roles == ["AdminAccess", "ReadOnlyAccess", "CustomDeny"]

    def test_same_role_different_scopes_still_listed(self) -> None:
        pmap = PersonaRoleMap(
            persona="multi",
            provider="azure",
            assignments=[
                ("Reader", "/subscriptions/a", False),
                ("Reader", "/subscriptions/b", False),
            ],
        )
        assert pmap.roles() == ["Reader", "Reader"]


class TestPersonaRoleMapScopes:
    def test_empty_assignments_returns_empty(self) -> None:
        pmap = PersonaRoleMap(persona="dev", provider="aws")
        assert pmap.scopes() == []

    def test_single_scope(self) -> None:
        pmap = PersonaRoleMap(
            persona="admin",
            provider="azure",
            assignments=[("Reader", "/subscriptions/sub", True)],
        )
        assert pmap.scopes() == ["/subscriptions/sub"]

    def test_multiple_scopes(self) -> None:
        pmap = PersonaRoleMap(
            persona="ops",
            provider="gcp",
            assignments=[
                ("r1", "projects/proj-a", True),
                ("r2", "projects/proj-b", False),
                ("r3", "organizations/org", True),
            ],
        )
        assert pmap.scopes() == [
            "projects/proj-a",
            "projects/proj-b",
            "organizations/org",
        ]

    def test_scopes_order_matches_assignments(self) -> None:
        pmap = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[
                ("role-z", "scope-3", True),
                ("role-a", "scope-1", False),
                ("role-m", "scope-2", True),
            ],
        )
        assert pmap.scopes() == ["scope-3", "scope-1", "scope-2"]

    def test_builtin_ignored_in_scope_extraction(self) -> None:
        pmap = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[
                ("r1", "scope1", True),
                ("r2", "scope2", False),
            ],
        )
        scopes = pmap.scopes()
        assert scopes == ["scope1", "scope2"]

    def test_empty_string_scopes_preserved(self) -> None:
        pmap = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "", True)],
        )
        assert pmap.scopes() == [""]


class TestPersonaRoleMapEquality:
    def test_identical_maps_equal(self) -> None:
        a = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "s1", True), ("r2", "s2", False)],
        )
        b = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "s1", True), ("r2", "s2", False)],
        )
        assert a == b

    def test_different_persona_not_equal(self) -> None:
        a = PersonaRoleMap(persona="dev", provider="aws", assignments=[])
        b = PersonaRoleMap(persona="ops", provider="aws", assignments=[])
        assert a != b

    def test_different_assignments_not_equal(self) -> None:
        a = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "s1", True)],
        )
        b = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r2", "s2", True)],
        )
        assert a != b

    def test_different_builtin_flag_not_equal(self) -> None:
        a = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "s1", True)],
        )
        b = PersonaRoleMap(
            persona="dev",
            provider="aws",
            assignments=[("r1", "s1", False)],
        )
        assert a != b


class TestPersonaRoleMapSerialization:
    def test_asdict_preserves_tuples(self) -> None:
        pmap = PersonaRoleMap(
            persona="admin",
            provider="azure",
            assignments=[
                ("Contributor", "/subscriptions/a", True),
                ("Reader", "/subscriptions/b", False),
            ],
        )
        d = dataclasses.asdict(pmap)
        assert d["persona"] == "admin"
        assert d["provider"] == "azure"
        assert len(d["assignments"]) == 2
        assert d["assignments"][0] == ("Contributor", "/subscriptions/a", True)

    def test_fields_match_expected(self) -> None:
        field_names = {f.name for f in dataclasses.fields(PersonaRoleMap)}
        assert field_names == {"persona", "provider", "assignments"}


class TestPersonaRoleMapLargeAssignments:
    def test_roles_and_scopes_with_1000_assignments(self) -> None:
        assignments = [(f"role-{i}", f"scope-{i}", i % 2 == 0) for i in range(1000)]
        pmap = PersonaRoleMap(persona="bulk", provider="aws", assignments=assignments)
        assert len(pmap.roles()) == 1000
        assert len(pmap.scopes()) == 1000
        assert pmap.roles()[0] == "role-0"
        assert pmap.roles()[999] == "role-999"
        assert pmap.scopes()[0] == "scope-0"
        assert pmap.scopes()[999] == "scope-999"


# ── ValidationResult ──────────────────────────────────────────────────────────


class TestValidationResultDefaults:
    def test_default_status(self) -> None:
        vr = ValidationResult(status="valid")
        assert vr.status == "valid"

    def test_default_errors_warnings_empty(self) -> None:
        vr = ValidationResult(status="valid")
        assert vr.errors == []
        assert vr.warnings == []

    def test_default_provider_empty(self) -> None:
        vr = ValidationResult(status="invalid")
        assert vr.provider == ""

    def test_default_lists_are_independent(self) -> None:
        a = ValidationResult(status="valid")
        b = ValidationResult(status="valid")
        assert a.errors is not b.errors
        assert a.warnings is not b.warnings


class TestValidationResultEquality:
    def test_identical_results_equal(self) -> None:
        a = ValidationResult(status="valid", provider="aws")
        b = ValidationResult(status="valid", provider="aws")
        assert a == b

    def test_different_status_not_equal(self) -> None:
        a = ValidationResult(status="valid")
        b = ValidationResult(status="invalid")
        assert a != b

    def test_different_errors_not_equal(self) -> None:
        a = ValidationResult(status="invalid", errors=["e1"])
        b = ValidationResult(status="invalid", errors=["e2"])
        assert a != b

    def test_different_warnings_not_equal(self) -> None:
        a = ValidationResult(status="invalid", warnings=["w1"])
        b = ValidationResult(status="invalid", warnings=["w2"])
        assert a != b

    def test_different_provider_not_equal(self) -> None:
        a = ValidationResult(status="valid", provider="aws")
        b = ValidationResult(status="valid", provider="gcp")
        assert a != b

    def test_not_hashable(self) -> None:
        vr = ValidationResult(status="valid")
        with pytest.raises(TypeError):
            hash(vr)


class TestValidationResultFullConstruction:
    def test_all_fields_populated(self) -> None:
        vr = ValidationResult(
            status="invalid",
            errors=["Missing required action 's3:GetObject'"],
            warnings=["Deprecated API used: s3:GetBucketLogging"],
            provider="aws",
        )
        assert vr.status == "invalid"
        assert len(vr.errors) == 1
        assert len(vr.warnings) == 1
        assert vr.provider == "aws"

    def test_multiple_errors(self) -> None:
        errors = [f"error-{i}" for i in range(10)]
        vr = ValidationResult(status="invalid", errors=errors)
        assert vr.errors == errors
        assert len(vr.errors) == 10

    def test_multiple_warnings(self) -> None:
        warnings = [f"warning-{i}" for i in range(10)]
        vr = ValidationResult(status="valid", warnings=warnings)
        assert vr.warnings == warnings


class TestValidationResultStatusValues:
    def test_status_valid(self) -> None:
        vr = ValidationResult(status="valid")
        assert vr.status == "valid"

    def test_status_invalid(self) -> None:
        vr = ValidationResult(status="invalid")
        assert vr.status == "invalid"

    def test_status_error(self) -> None:
        vr = ValidationResult(status="error")
        assert vr.status == "error"

    def test_status_generated_with_warnings(self) -> None:
        vr = ValidationResult(status="generated_with_warnings")
        assert vr.status == "generated_with_warnings"


class TestValidationResultSerialization:
    def test_asdict_all_fields(self) -> None:
        vr = ValidationResult(
            status="invalid",
            errors=["bad-action"],
            warnings=["deprecated"],
            provider="azure",
        )
        d = dataclasses.asdict(vr)
        assert d["status"] == "invalid"
        assert d["errors"] == ["bad-action"]
        assert d["warnings"] == ["deprecated"]
        assert d["provider"] == "azure"

    def test_fields_match_expected(self) -> None:
        field_names = {f.name for f in dataclasses.fields(ValidationResult)}
        assert field_names == {"status", "errors", "warnings", "provider"}


class TestValidationResultMutation:
    def test_mutate_errors_not_shared(self) -> None:
        a = ValidationResult(status="valid")
        b = ValidationResult(status="valid")
        a.errors.append("oops")
        assert b.errors == []

    def test_mutate_warnings_not_shared(self) -> None:
        a = ValidationResult(status="valid")
        b = ValidationResult(status="valid")
        a.warnings.append("heads-up")
        assert b.warnings == []
