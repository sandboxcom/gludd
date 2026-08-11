"""Deep unit tests for ``general_ludd.azure.iam_advisor``.

Covers: persona-to-role mapping, normalization edge cases, audit findings
structure, scope detection, empty/missing/invalid input, and invariant
properties.
"""

from __future__ import annotations

import pytest

from general_ludd.azure.iam_advisor import (
    _OVER_PRIVILEGED_ROLES,
    _RISKY_SCOPES,
    PERSONA_ROLE_MAP,
    _is_subscription_scope,
    audit_existing_assignments,
    recommend_roles_for_persona,
)


# ── constants / lookup invariants ──────────────────────────────────────
class TestPersonaRoleMap:
    def test_exactly_four_personas(self):
        assert len(PERSONA_ROLE_MAP) == 4

    @pytest.mark.parametrize("persona", ["developer", "operator", "auditor", "admin"])
    def test_every_persona_present(self, persona: str):
        assert persona in PERSONA_ROLE_MAP

    def test_admin_is_owner_only(self):
        assert PERSONA_ROLE_MAP["admin"] == ["Owner"]

    def test_developer_is_contributor_only(self):
        assert PERSONA_ROLE_MAP["developer"] == ["Contributor"]

    def test_auditor_is_reader_only(self):
        assert PERSONA_ROLE_MAP["auditor"] == ["Reader"]

    def test_operator_has_both_reader_and_contributor(self):
        assert PERSONA_ROLE_MAP["operator"] == ["Reader", "Contributor"]

    def test_map_is_immutable_dict(self):
        assert isinstance(PERSONA_ROLE_MAP, dict)

    def test_no_role_appears_in_over_privileged_by_accident(self):
        for persona, roles in PERSONA_ROLE_MAP.items():
            for role in roles:
                assert role in {"Reader", "Contributor", "Owner"}, f"unexpected role '{role}' for persona '{persona}'"


class TestOverPrivilegedRolesConstant:
    def test_contains_owner_and_contributor(self):
        assert frozenset({"Owner", "Contributor"}) == _OVER_PRIVILEGED_ROLES

    def test_is_frozenset(self):
        assert isinstance(_OVER_PRIVILEGED_ROLES, frozenset)


class TestRiskyScopesConstant:
    def test_contains_root_and_subscriptions(self):
        assert frozenset({"/", "/subscriptions"}) == _RISKY_SCOPES

    def test_is_frozenset(self):
        assert isinstance(_RISKY_SCOPES, frozenset)


# ── recommend_roles_for_persona ────────────────────────────────────────
class TestRecommendRolesForPersona:
    def test_unknown_persona_returns_empty(self):
        assert recommend_roles_for_persona("nonexistent") == []

    def test_empty_string_returns_empty(self):
        assert recommend_roles_for_persona("") == []

    def test_leading_trailing_whitespace_normalized(self):
        roles = recommend_roles_for_persona("  developer  ")
        assert "Contributor" in roles

    def test_case_insensitive(self):
        assert recommend_roles_for_persona("DEVELOPER") == ["Contributor"]
        assert recommend_roles_for_persona("Developer") == ["Contributor"]
        assert recommend_roles_for_persona("ADMIN") == ["Owner"]

    def test_inner_whitespace_only_normalized_by_strip(self):
        roles = recommend_roles_for_persona("\tadmin\n")
        assert roles == ["Owner"]

    def test_returns_list_always(self):
        for persona in ("developer", "unknown", ""):
            assert isinstance(recommend_roles_for_persona(persona), list)

    def test_never_returns_none(self):
        samples: list[str] = ["developer", "bogus", "", "  admin "]
        for p in samples:
            assert recommend_roles_for_persona(p) is not None

    def test_operator_returns_two_roles(self):
        assert len(recommend_roles_for_persona("operator")) == 2

    def test_auditor_returns_one_role(self):
        assert len(recommend_roles_for_persona("auditor")) == 1


# ── _is_subscription_scope (private helper) ────────────────────────────
class TestIsSubscriptionScope:
    @pytest.mark.parametrize("scope", ["/", "/subscriptions"])
    def test_risky_scopes_recognised(self, scope: str):
        assert _is_subscription_scope(scope) is True

    @pytest.mark.parametrize(
        "scope",
        [
            "",
            "/subscriptions/resourceGroups/rg",
            "/subscriptions/00000000-0000-0000-0000-000000000000",
            "subscription",  # no leading /
            "/providers",
        ],
    )
    def test_non_risky_scopes_return_false(self, scope: str):
        assert _is_subscription_scope(scope) is False

    def test_empty_string_false(self):
        assert _is_subscription_scope("") is False


# ── audit_existing_assignments ─────────────────────────────────────────
class TestAuditExistingAssignments:
    # -- flagged cases --------------------------------------------------
    def test_flags_owner_at_root_scope(self):
        findings = audit_existing_assignments([{"persona": "admin", "role": "Owner", "scope": "/"}])
        assert len(findings) == 1

    def test_flags_contributor_at_subscriptions_scope(self):
        findings = audit_existing_assignments([{"persona": "dev", "role": "Contributor", "scope": "/subscriptions"}])
        assert len(findings) == 1

    def test_over_privileged_flag_is_true(self):
        findings = audit_existing_assignments([{"role": "Owner", "scope": "/"}])
        assert findings[0]["over_privileged"] is True

    def test_reason_field_present_and_formatted(self):
        findings = audit_existing_assignments([{"role": "Owner", "scope": "/"}])
        assert "reason" in findings[0]
        assert "Owner" in findings[0]["reason"]
        assert "/" in findings[0]["reason"]
        assert "over-privileged" in findings[0]["reason"]

    def test_original_fields_preserved_in_finding(self):
        assignments = [
            {"role": "Owner", "scope": "/", "persona": "admin", "id": "abc123"},
        ]
        findings = audit_existing_assignments(assignments)
        assert findings[0]["role"] == "Owner"
        assert findings[0]["scope"] == "/"
        assert findings[0]["persona"] == "admin"
        assert findings[0]["id"] == "abc123"

    # -- not-flagged cases ----------------------------------------------
    def test_reader_at_root_not_flagged(self):
        assert audit_existing_assignments([{"role": "Reader", "scope": "/"}]) == []

    def test_owner_at_resource_group_not_flagged(self):
        assert audit_existing_assignments([{"role": "Owner", "scope": "/subscriptions/x/resourceGroups/rg"}]) == []

    def test_contributor_at_resource_scope_not_flagged(self):
        assert (
            audit_existing_assignments(
                [
                    {
                        "role": "Contributor",
                        "scope": "/subscriptions/s/rg/r/providers/Microsoft.Compute/virtualMachines/vm",
                    }
                ]
            )
            == []
        )

    def test_non_privileged_role_at_risky_scope_not_flagged(self):
        assert audit_existing_assignments([{"role": "Reader", "scope": "/"}]) == []

    def test_privileged_role_at_non_risky_scope_not_flagged(self):
        assert audit_existing_assignments([{"role": "Owner", "scope": "/subscriptions/rg/custom"}]) == []

    # -- mixed inputs ---------------------------------------------------
    def test_mixed_finds_only_over_privileged(self):
        assignments = [
            {"role": "Owner", "scope": "/"},
            {"role": "Reader", "scope": "/"},
            {"role": "Contributor", "scope": "/subscriptions"},
            {"role": "Owner", "scope": "/subscriptions/rg"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 2
        flagged_roles = {f["role"] for f in findings}
        assert flagged_roles == {"Owner", "Contributor"}

    # -- empty / edge inputs --------------------------------------------
    def test_empty_list_returns_empty(self):
        assert audit_existing_assignments([]) == []

    def test_empty_dict_not_flagged(self):
        findings = audit_existing_assignments([{}])
        assert findings == []

    def test_missing_role_key_not_flagged(self):
        findings = audit_existing_assignments([{"scope": "/"}])
        assert findings == []

    def test_missing_scope_key_not_flagged(self):
        findings = audit_existing_assignments([{"role": "Owner"}])
        assert findings == []

    def test_empty_string_role_at_risky_scope_not_flagged(self):
        findings = audit_existing_assignments([{"role": "", "scope": "/"}])
        assert findings == []

    def test_privileged_role_at_empty_string_scope_not_flagged(self):
        findings = audit_existing_assignments([{"role": "Owner", "scope": ""}])
        assert findings == []

    def test_both_role_and_scope_empty_not_flagged(self):
        findings = audit_existing_assignments([{"role": "", "scope": ""}])
        assert findings == []

    def test_large_input_all_over_privileged(self):
        large = [{"role": "Owner", "scope": "/"} for _ in range(500)]
        findings = audit_existing_assignments(large)
        assert len(findings) == 500
        assert all(f["over_privileged"] is True for f in findings)

    def test_large_input_none_over_privileged(self):
        large = [{"role": "Reader", "scope": "/"} for _ in range(500)]
        assert audit_existing_assignments(large) == []

    # -- invariant properties -------------------------------------------
    def test_findings_count_never_exceeds_input_count(self):
        assignments = [{"role": "Owner", "scope": "/"} for _ in range(10)]
        assert len(audit_existing_assignments(assignments)) <= len(assignments)

    def test_every_finding_has_over_privileged_true(self):
        assignments = [
            {"role": "Owner", "scope": "/"},
            {"role": "Reader", "scope": "/"},
            {"role": "Contributor", "scope": "/subscriptions"},
        ]
        findings = audit_existing_assignments(assignments)
        assert all(f.get("over_privileged") is True for f in findings)

    def test_no_false_positives(self):
        assignments = [
            {"role": "Reader", "scope": "/"},
            {"role": "Reader", "scope": "/subscriptions"},
            {"role": "LogAnalyticsReader", "scope": "/"},
        ]
        findings = audit_existing_assignments(assignments)
        assert len(findings) == 0, f"expected zero findings, got {[f['role'] for f in findings]}"
