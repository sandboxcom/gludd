"""Unit tests for the GCP IAM policy validator script.

Validates the validate_gcp_iam_policy.py script's core validation functions
in isolation and via module import.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import validate_gcp_iam_policy as v  # noqa: E402


class TestLoadGcpRolesVaryingFixtures:
    """Test that load_gcp_roles handles various fixture shapes."""

    def test_load_standard_roles(self) -> None:
        roles = v.load_gcp_roles()
        assert isinstance(roles, dict)
        assert len(roles) >= 4

    def test_load_roles_is_dict_of_dicts(self) -> None:
        roles = v.load_gcp_roles()
        for name, role_def in roles.items():
            assert isinstance(name, str)
            assert isinstance(role_def, dict)
            assert "roles" in role_def


class TestAllPersonasPresent:
    """Verify exactly the 4 required personas are in the YAML."""

    def test_all_four_personas_defined(self) -> None:
        roles = v.load_gcp_roles()
        actual = frozenset(roles.keys())
        assert v.REQUIRED_PERSONAS.issubset(actual)

    def test_validate_personas_clean(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_personas(roles)
        assert errors == []


class TestNoForbiddenRoles:
    """No persona uses roles/owner or roles/editor."""

    def test_no_owner_or_editor(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_no_forbidden_roles(roles)
        assert errors == []

    def test_forbidden_roles_detected(self) -> None:
        roles = {
            "bad_persona": {
                "roles": ["roles/owner", "roles/compute.viewer"],
                "description": "Should be flagged",
            },
        }
        errors = v.validate_no_forbidden_roles(roles)
        assert len(errors) == 1
        assert "roles/owner" in errors[0]

    def test_editor_also_forbidden(self) -> None:
        roles = {
            "bad_editor": {
                "roles": ["roles/editor"],
                "description": "Should be flagged",
            },
        }
        errors = v.validate_no_forbidden_roles(roles)
        assert len(errors) == 1
        assert "roles/editor" in errors[0]


class TestCustomRoleValidation:
    """gluddComputeOperator must exist and be safe."""

    def test_custom_role_permissions_loaded(self) -> None:
        perms = v.load_custom_role_permissions()
        assert isinstance(perms, set)
        assert len(perms) > 10
        assert "compute.instances.insert" in perms
        assert "compute.instances.delete" in perms

    def test_no_setmetadata_in_custom_role(self) -> None:
        perms = v.load_custom_role_permissions()
        for forbidden in v.FORBIDDEN_CUSTOM_PERMISSIONS:
            assert forbidden not in perms, f"gluddComputeOperator must NOT include '{forbidden}'"

    def test_validate_custom_role_clean(self) -> None:
        perms = v.load_custom_role_permissions()
        errors = v.validate_custom_role(perms)
        assert errors == []

    def test_validate_custom_role_detects_setmetadata(self) -> None:
        perms = {"compute.instances.insert", "compute.instances.setMetadata"}
        errors = v.validate_custom_role(perms)
        assert len(errors) >= 1
        assert any("setMetadata" in e for e in errors)


class TestCelConditionSyntax:
    """CEL conditions must be syntactically valid expressions."""

    def test_cel_syntax_clean(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_cel_syntax(roles)
        assert errors == []

    def test_cel_unbalanced_parens_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona",
                "roles": ["roles/compute.viewer"],
                "conditions": [
                    {
                        "title": "Bad condition",
                        "expression": "resource.type == 'Instance' && (resource.name || (resource.zone",
                    },
                ],
            },
        }
        errors = v.validate_cel_syntax(roles)
        assert len(errors) >= 1

    def test_cel_trailing_operator_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona",
                "roles": ["roles/compute.viewer"],
                "conditions": [
                    {
                        "title": "Bad trailing",
                        "expression": "resource.type == 'Instance' ||",
                    },
                ],
            },
        }
        errors = v.validate_cel_syntax(roles)
        assert len(errors) >= 1
        assert any("trailing" in e for e in errors)


class TestResourceUriValidation:
    """Resource URI patterns must be valid GCP format."""

    def test_resource_uris_clean(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_resource_uris(roles)
        assert errors == []

    def test_unknown_resource_type_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona",
                "roles": ["roles/compute.viewer"],
                "conditions": [
                    {
                        "title": "Bad type",
                        "expression": 'resource.type == "invalid.service.com/FakeResource"',
                    },
                ],
            },
        }
        errors = v.validate_resource_uris(roles)
        assert len(errors) >= 1


class TestNoWildcardResource:
    """Conditions must not use bare '*' as resource."""

    def test_no_wildcard_in_real_config(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_no_wildcard_resource(roles)
        assert errors == []

    def test_wildcard_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona",
                "roles": ["roles/compute.viewer"],
                "conditions": [
                    {
                        "title": "Wildcard",
                        "expression": 'resource.name == "*"',
                    },
                ],
            },
        }
        errors = v.validate_no_wildcard_resource(roles)
        assert len(errors) >= 1


class TestSecretManagerScoping:
    """Secret Manager permissions must be namespaced."""

    def test_runtime_execution_has_secret_scope(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_secret_manager_scoping(roles)
        assert errors == []

    def test_missing_secret_condition_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona with un-scoped secrets",
                "roles": ["roles/secretmanager.secretAccessor"],
                "conditions": [],
            },
        }
        errors = v.validate_secret_manager_scoping(roles)
        assert len(errors) >= 1
        assert any("namespaced" in e.lower() for e in errors)

    def test_secret_without_namespace_prefix_detected(self) -> None:
        roles = {
            "test": {
                "description": "Scoped but no namespace",
                "roles": ["roles/secretmanager.secretAccessor"],
                "conditions": [
                    {
                        "title": "Limit secret type only",
                        "expression": 'resource.type == "secretmanager.googleapis.com/Secret"',
                    },
                ],
            },
        }
        errors = v.validate_secret_manager_scoping(roles)
        assert len(errors) >= 1
        assert any("prefix" in e.lower() for e in errors)


class TestMonitorPermissions:
    """Monitor persona must have monitoring, logging, and billing read."""

    def test_monitor_has_required_permissions(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_monitor_permissions(roles)
        assert errors == []

    def test_monitor_has_monitoring_viewer(self) -> None:
        roles = v.load_gcp_roles()
        monitor = roles.get("monitor", {})
        bindings = monitor.get("roles", [])
        assert any(b.startswith("roles/monitoring.") for b in bindings)

    def test_monitor_no_write_roles(self) -> None:
        """Simulate a monitor with a write role and verify it is flagged."""
        roles = {
            "monitor": {
                "description": "Read-only monitoring observer",
                "roles": [
                    "roles/monitoring.admin",
                    "roles/logging.viewer",
                    "roles/billing.viewer",
                ],
            },
        }
        errors = v.validate_monitor_permissions(roles)
        assert len(errors) >= 1
        assert any("admin" in e.lower() or "write" in e.lower() for e in errors)


class TestDescriptionValidation:
    """Each persona must have an adequate description."""

    def test_all_descriptions_adequate(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_descriptions(roles)
        assert errors == []

    def test_short_description_detected(self) -> None:
        roles = {
            "test": {
                "description": "Short",
                "roles": ["roles/compute.viewer"],
            },
        }
        errors = v.validate_descriptions(roles)
        assert len(errors) == 1


class TestRoleBindingsFormat:
    """All role bindings must start with 'roles/' or 'projects/'."""

    def test_bindings_have_valid_prefix(self) -> None:
        roles = v.load_gcp_roles()
        errors = v.validate_role_bindings_format(roles)
        assert errors == []

    def test_invalid_prefix_detected(self) -> None:
        roles = {
            "test": {
                "description": "Test persona with bad role prefix",
                "roles": ["organizations/12345/roles/something"],
            },
        }
        errors = v.validate_role_bindings_format(roles)
        assert len(errors) == 1


class TestScriptRunsClean:
    """The full script should run without errors against real config."""

    def test_main_exits_zero(self) -> None:
        import subprocess

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "validate_gcp_iam_policy.py")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"

    def test_main_output_has_pass_lines(self) -> None:
        import subprocess

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "validate_gcp_iam_policy.py")],
            capture_output=True,
            text=True,
        )
        assert "PASS:" in result.stdout
        assert "PASS: All GCP IAM policy validations passed" in result.stdout
