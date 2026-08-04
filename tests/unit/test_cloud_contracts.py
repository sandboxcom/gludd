"""Tests for cloud IAM data contracts — dataclasses."""

from __future__ import annotations

from general_ludd.cloud.contracts import (
    CloudFunction,
    CloudRoleDefinition,
    PersonaRoleMap,
    ValidationResult,
)


class TestCloudRoleDefinition:
    def test_default_construction(self):
        role = CloudRoleDefinition(provider="aws", name="monitor", description="read-only")
        assert role.provider == "aws"
        assert role.name == "monitor"
        assert role.description == "read-only"
        assert role.actions == []
        assert role.not_actions == []
        assert role.data_actions == []
        assert role.not_data_actions == []
        assert role.assignable_scopes == []

    def test_full_construction(self):
        role = CloudRoleDefinition(
            provider="azure",
            name="reader",
            description="can read",
            actions=["Microsoft.Compute/*/read"],
            not_actions=["Microsoft.Compute/virtualMachines/delete"],
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            not_data_actions=[],
            assignable_scopes=["/subscriptions/sub-id"],
        )
        assert len(role.actions) == 1
        assert len(role.not_actions) == 1
        assert len(role.data_actions) == 1
        assert len(role.assignable_scopes) == 1

    def test_equality(self):
        a = CloudRoleDefinition(provider="aws", name="r", description="d")
        b = CloudRoleDefinition(provider="aws", name="r", description="d")
        assert a == b

    def test_inequality(self):
        a = CloudRoleDefinition(provider="aws", name="r", description="d")
        b = CloudRoleDefinition(provider="aws", name="r2", description="d")
        assert a != b


class TestCloudFunction:
    def test_default_construction(self):
        fn = CloudFunction(provider="gcp", name="read_buckets", category="storage", risk_level="low")
        assert fn.provider == "gcp"
        assert fn.name == "read_buckets"
        assert fn.category == "storage"
        assert fn.risk_level == "low"
        assert fn.required_denial == ""

    def test_with_denial(self):
        fn = CloudFunction(
            provider="aws",
            name="delete_bucket",
            category="storage",
            risk_level="high",
            required_denial="s3:DeleteBucket",
        )
        assert fn.risk_level == "high"
        assert fn.required_denial == "s3:DeleteBucket"


class TestPersonaRoleMap:
    def test_default_construction(self):
        pmap = PersonaRoleMap(persona="developer", provider="aws")
        assert pmap.persona == "developer"
        assert pmap.provider == "aws"
        assert pmap.assignments == []
        assert pmap.roles() == []
        assert pmap.scopes() == []

    def test_with_assignments(self):
        pmap = PersonaRoleMap(
            persona="admin",
            provider="azure",
            assignments=[
                ("contributor", "/subscriptions/sub", True),
                ("reader", "/subscriptions/sub", True),
            ],
        )
        assert pmap.roles() == ["contributor", "reader"]
        assert pmap.scopes() == ["/subscriptions/sub", "/subscriptions/sub"]

    def test_builtin_flag_ignored_by_roles(self):
        pmap = PersonaRoleMap(
            persona="ops",
            provider="gcp",
            assignments=[
                ("viewer", "org", True),
                ("custom_role", "project/xyz", False),
            ],
        )
        assert pmap.roles() == ["viewer", "custom_role"]


class TestValidationResult:
    def test_success(self):
        result = ValidationResult(status="valid")
        assert result.status == "valid"
        assert result.errors == []
        assert result.warnings == []
        assert result.provider == ""

    def test_with_errors_and_warnings(self):
        result = ValidationResult(
            status="invalid",
            errors=["action not found"],
            warnings=["deprecated action used"],
            provider="aws",
        )
        assert result.status == "invalid"
        assert result.errors == ["action not found"]
        assert result.warnings == ["deprecated action used"]
        assert result.provider == "aws"
