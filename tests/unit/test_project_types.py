"""Compatibility contracts for the typed project-type registry.

Covers the live dictionary view, field completeness, prompt/rule integrity,
fail-closed lookup, sorted listing, valid roles, and both registration forms.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.cloud import project_types as _pt


def _required_keys() -> set[str]:
    return {
        "type_id",
        "display_name",
        "prompt_templates",
        "validation_rules",
        "acceptance_criteria",
        "suggested_model_roles",
    }


def _valid_role_values() -> set[str]:
    return {"planner", "coder", "reviewer", "editor", "compactor", "enumerator"}


def _dynamic_definition(display_name: str = "Dynamic Test Type") -> dict[str, Any]:
    return {
        "type_id": "dynamic_test_type",
        "display_name": display_name,
        "prompt_templates": {
            "system": "You are building a {description}.",
            "user": "Please implement: {description}",
        },
        "validation_rules": [
            "ast_valid",
            "importable",
        ],
        "acceptance_criteria": [
            "All unit tests pass",
            "Gate is green",
        ],
        "suggested_model_roles": ["coder", "reviewer"],
    }


class TestRegisteredTypesFieldCompleteness:
    def test_every_registered_type_has_all_required_keys(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            missing = _required_keys() - set(definition)
            assert not missing, f"Type {type_id!r} missing required keys: {sorted(missing)}"

    def test_type_id_field_is_non_empty_string(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            value = definition["type_id"]
            assert isinstance(value, str) and value, f"Type {type_id!r} has invalid type_id"

    def test_display_name_field_is_non_empty_string(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            value = definition["display_name"]
            assert isinstance(value, str) and value, f"Type {type_id!r} has invalid display_name"

    def test_prompt_templates_is_non_empty_dict(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            value = definition["prompt_templates"]
            assert isinstance(value, dict) and value, f"Type {type_id!r} has invalid prompt_templates"

    def test_validation_rules_is_list(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["validation_rules"], list), f"Type {type_id!r} rules are not a list"

    def test_acceptance_criteria_is_non_empty_list(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            value = definition["acceptance_criteria"]
            assert isinstance(value, list) and value, f"Type {type_id!r} has invalid acceptance criteria"

    def test_suggested_model_roles_is_non_empty_list(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            value = definition["suggested_model_roles"]
            assert isinstance(value, list) and value, f"Type {type_id!r} has invalid roles"


class TestNoDuplicateTypeIds:
    def test_registry_has_no_duplicate_keys(self) -> None:
        ids = list(_pt.PROJECT_TYPES)
        assert len(ids) == len(set(ids))


class TestPromptTemplatePlaceholders:
    def test_every_template_contains_description_placeholder(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            templates = definition["prompt_templates"]
            assert isinstance(templates, dict)
            for template_name, template_text in templates.items():
                assert isinstance(template_text, str)
                assert "{description}" in template_text, (
                    f"Type {type_id!r} template {template_name!r} lacks description placeholder"
                )


class TestValidationRuleIntegrity:
    def test_every_rule_is_a_stable_identifier(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            rules = definition["validation_rules"]
            assert isinstance(rules, list) and rules, f"Type {type_id!r} has no validation rules"
            for rule in rules:
                assert isinstance(rule, str)
                assert rule and rule.replace("_", "").isalnum(), (
                    f"Type {type_id!r} has unstable validation rule {rule!r}"
                )


class TestGetProjectType:
    def test_returns_definition_for_known_type(self) -> None:
        first_id = next(iter(_pt.PROJECT_TYPES))
        assert _pt.get_project_type(first_id).type_id == first_id

    def test_unknown_type_fails_closed(self) -> None:
        with pytest.raises(KeyError):
            _pt.get_project_type("__nonexistent_type__")

    def test_empty_string_fails_closed(self) -> None:
        with pytest.raises(KeyError):
            _pt.get_project_type("")


class TestListProjectTypes:
    def test_returns_list(self) -> None:
        assert isinstance(_pt.list_project_types(), list)

    def test_returns_sorted_list(self) -> None:
        result = _pt.list_project_types()
        assert result == sorted(result)

    def test_contains_every_registered_key(self) -> None:
        assert set(_pt.list_project_types()) == set(_pt.PROJECT_TYPES)


class TestSuggestedModelRolesAreValid:
    def test_every_role_maps_to_valid_taskrole(self) -> None:
        for type_id, definition in _pt.PROJECT_TYPES.items():
            roles = definition["suggested_model_roles"]
            assert isinstance(roles, list)
            for role in roles:
                assert role in _valid_role_values(), f"Type {type_id!r} has unknown role {role!r}"

    def test_roles_are_strings(self) -> None:
        for definition in _pt.PROJECT_TYPES.values():
            roles = definition["suggested_model_roles"]
            assert isinstance(roles, list)
            assert all(isinstance(role, str) for role in roles)


class TestDynamicRegistration:
    def test_typed_and_legacy_forms_share_one_runtime_callable(self) -> None:
        typed_id = "runtime_typed_contract"
        legacy_id = "runtime_legacy_contract"
        typed = _pt.ProjectType(
            type_id=typed_id,
            display_name="Runtime Typed Contract",
            default_entry_point="typed.py",
        )
        legacy = _dynamic_definition("Runtime Legacy Contract")
        legacy["type_id"] = legacy_id

        try:
            _pt.register_project_type(typed)
            _pt.register_project_type(legacy_id, legacy)

            assert _pt.get_project_type(typed_id) is typed
            assert _pt.get_project_type(legacy_id).display_name == "Runtime Legacy Contract"
        finally:
            _pt.PROJECT_TYPE_REGISTRY.pop(typed_id, None)
            _pt.PROJECT_TYPE_REGISTRY.pop(legacy_id, None)

    def test_register_new_type_adds_to_registry(self) -> None:
        _pt.register_project_type("dynamic_test_type", _dynamic_definition())
        assert "dynamic_test_type" in _pt.PROJECT_TYPES

    def test_register_new_type_is_immediately_lookupable(self) -> None:
        _pt.register_project_type("dynamic_test_type", _dynamic_definition())
        result = _pt.get_project_type("dynamic_test_type")
        assert result.type_id == "dynamic_test_type"
        assert result.display_name == "Dynamic Test Type"
        assert "{context}" in result.prompt_template_planner

    def test_register_new_type_appears_in_list(self) -> None:
        _pt.register_project_type("dynamic_test_type", _dynamic_definition())
        assert "dynamic_test_type" in _pt.list_project_types()

    def test_register_duplicate_type_id_updates_definition(self) -> None:
        _pt.register_project_type("dynamic_test_type", _dynamic_definition())
        _pt.register_project_type(
            "dynamic_test_type",
            {
                "type_id": "dynamic_test_type",
                "display_name": "Updated Dynamic Test Type",
                "prompt_templates": {"system": "Updated: {description}"},
                "validation_rules": ["ast_valid"],
                "acceptance_criteria": ["Updated criteria"],
                "suggested_model_roles": ["planner"],
            },
        )
        assert _pt.get_project_type("dynamic_test_type").display_name == "Updated Dynamic Test Type"


class TestLegacyRegistrationValidation:
    def test_mismatched_type_id_is_rejected_without_mutation(self) -> None:
        definition = _dynamic_definition()
        definition["type_id"] = "different"
        before = set(_pt.PROJECT_TYPES)
        with pytest.raises(ValueError):
            _pt.register_project_type("dynamic_test_type", definition)
        assert set(_pt.PROJECT_TYPES) == before

    def test_missing_display_name_is_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["display_name"] = ""
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_empty_templates_are_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["prompt_templates"] = {}
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_non_list_rules_are_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["validation_rules"] = "ast_valid"
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_non_string_criteria_are_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["acceptance_criteria"] = [1]
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_empty_roles_are_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["suggested_model_roles"] = []
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_non_string_template_is_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["prompt_templates"] = {"system": 1}
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)

    def test_empty_entry_point_is_rejected(self) -> None:
        definition = _dynamic_definition()
        definition["default_entry_point"] = ""
        with pytest.raises(TypeError):
            _pt.register_project_type("dynamic_test_type", definition)
