"""Unit tests for ``general_ludd.azure.iam_advisor``."""

from __future__ import annotations

from general_ludd.azure.iam_advisor import (
    PERSONA_ROLE_MAP,
    audit_existing_assignments,
    recommend_roles_for_persona,
)


class TestPersonaRoleMap:
    def test_has_all_four_personas(self):
        assert len(PERSONA_ROLE_MAP) == 4
        for persona in ("developer", "operator", "auditor", "admin"):
            assert persona in PERSONA_ROLE_MAP, f"missing persona {persona}"

    def test_admin_maps_to_owner(self):
        assert "Owner" in PERSONA_ROLE_MAP["admin"]

    def test_developer_maps_to_contributor(self):
        assert "Contributor" in PERSONA_ROLE_MAP["developer"]

    def test_auditor_maps_to_reader(self):
        assert "Reader" in PERSONA_ROLE_MAP["auditor"]


class TestRecommendRolesForPersona:
    def test_developer_returns_nonempty(self):
        roles = recommend_roles_for_persona("developer")
        assert isinstance(roles, list)
        assert len(roles) > 0

    def test_operator_returns_nonempty(self):
        roles = recommend_roles_for_persona("operator")
        assert len(roles) > 0

    def test_auditor_returns_nonempty(self):
        roles = recommend_roles_for_persona("auditor")
        assert len(roles) > 0

    def test_admin_returns_nonempty(self):
        roles = recommend_roles_for_persona("admin")
        assert len(roles) > 0

    def test_unknown_persona_returns_empty(self):
        roles = recommend_roles_for_persona("nonexistent")
        assert roles == []

    def test_whitespace_is_stripped(self):
        roles = recommend_roles_for_persona("  developer  ")
        assert len(roles) > 0


class TestAuditExistingAssignments:
    def test_flags_owner_at_subscription_scope(self):
        assignments = [
            {"persona": "admin", "role": "Owner", "scope": "/"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 1
        assert findings[0]["over_privileged"] is True

    def test_flags_contributor_at_subscriptions(self):
        assignments = [
            {"persona": "dev", "role": "Contributor", "scope": "/subscriptions"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 1
        assert findings[0]["over_privileged"] is True

    def test_reader_not_flagged(self):
        assignments = [
            {"persona": "auditor", "role": "Reader", "scope": "/"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 0

    def test_owner_at_resource_group_not_flagged_by_default(self):
        assignments = [
            {"persona": "dev", "role": "Owner", "scope": "/subscriptions/x/resourceGroups/rg"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 0

    def test_mixed_assignments(self):
        assignments = [
            {"persona": "admin", "role": "Owner", "scope": "/"},
            {"persona": "auditor", "role": "Reader", "scope": "/"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 1
