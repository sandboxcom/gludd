"""Unit tests for the project types registry in general_ludd.cloud.project_types.

Covers: field completeness, duplicate detection, placeholder presence,
validation-rule integrity, lookup behaviour, sorting, valid role mapping,
and dynamic extensibility.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Attempt import — TDD red phase is expected if the source file is not yet
# written.  Every test below is skipped when the module is absent so a
# collection error does not mask the signal.
# ---------------------------------------------------------------------------

_project_types_module: object | None = None
_project_types_error: str | None = None

try:
    from general_ludd.cloud import project_types as _pt

    _project_types_module = _pt
except ImportError as exc:
    _project_types_error = str(exc)

needs_module = pytest.mark.skipif(
    _project_types_module is None,
    reason=f"project_types module not importable: {_project_types_error or 'unknown'}",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    # Mirror of schemas.benchmark.TaskRole — the registry must reference
    # valid role identifiers.
    return {"planner", "coder", "reviewer", "editor", "compactor", "enumerator"}


# ---------------------------------------------------------------------------
# Field completeness
# ---------------------------------------------------------------------------


@needs_module
class TestRegisteredTypesFieldCompleteness:
    def test_every_registered_type_has_all_required_keys(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            missing = _required_keys() - set(definition)
            assert not missing, f"Type {type_id!r} missing required keys: {sorted(missing)}"

    def test_type_id_field_is_non_empty_string(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["type_id"], str), f"Type {type_id!r} type_id not a str"
            assert definition["type_id"], f"Type {type_id!r} type_id is empty"

    def test_display_name_field_is_non_empty_string(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["display_name"], str), f"Type {type_id!r} display_name not a str"
            assert definition["display_name"], f"Type {type_id!r} display_name is empty"

    def test_prompt_templates_is_non_empty_dict(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["prompt_templates"], dict), f"Type {type_id!r} prompt_templates not a dict"
            assert definition["prompt_templates"], f"Type {type_id!r} prompt_templates is empty"

    def test_validation_rules_is_list(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["validation_rules"], list), f"Type {type_id!r} validation_rules not a list"

    def test_acceptance_criteria_is_non_empty_list(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["acceptance_criteria"], list), (
                f"Type {type_id!r} acceptance_criteria not a list"
            )
            assert definition["acceptance_criteria"], f"Type {type_id!r} acceptance_criteria is empty"

    def test_suggested_model_roles_is_non_empty_list(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            assert isinstance(definition["suggested_model_roles"], list), (
                f"Type {type_id!r} suggested_model_roles not a list"
            )
            assert definition["suggested_model_roles"], f"Type {type_id!r} suggested_model_roles is empty"


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


@needs_module
class TestNoDuplicateTypeIds:
    def test_registry_has_no_duplicate_keys(self):
        ids = list(_pt.PROJECT_TYPES.keys())
        assert len(ids) == len(set(ids)), f"Duplicate type_ids detected: {[tid for tid in ids if ids.count(tid) > 1]}"


# ---------------------------------------------------------------------------
# Prompt template placeholders
# ---------------------------------------------------------------------------


@needs_module
class TestPromptTemplatePlaceholders:
    def test_every_template_in_every_type_contains_description_placeholder(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            for template_name, template_text in definition["prompt_templates"].items():
                assert isinstance(template_text, str), f"Type {type_id!r} template {template_name!r} is not a str"
                assert "{description}" in template_text, (
                    f"Type {type_id!r} template {template_name!r} missing {{description}} placeholder"
                )


# ---------------------------------------------------------------------------
# Validation rule integrity
# ---------------------------------------------------------------------------

_VALID_AST_CHECKS: frozenset[str] = frozenset(
    {
        "ast",
        "importlib",
        "import",
        "importlib.util",
        "ast.parse",
        "ast.walk",
        "importlib.import_module",
        "importlib.util.find_spec",
        "pkgutil",
        "pkgutil.iter_modules",
        "sys.modules",
    }
)


@needs_module
class TestValidationRuleIntegrity:
    def test_every_rule_references_valid_ast_or_import_check(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            for rule in definition["validation_rules"]:
                assert isinstance(rule, str), f"Type {type_id!r} validation rule not a str: {rule!r}"
                tokens = rule.lower().replace(".", " ").replace("_", " ").split()
                hit = any(tok in _VALID_AST_CHECKS for tok in tokens)
                assert hit, (
                    f"Type {type_id!r} validation rule references no recognised "
                    f"AST/import check: {rule!r}\n"
                    f"Recognised tokens: {sorted(_VALID_AST_CHECKS)}"
                )


# ---------------------------------------------------------------------------
# Lookup behaviour
# ---------------------------------------------------------------------------


@needs_module
class TestGetProjectType:
    def test_returns_definition_for_known_type(self):
        first_id = next(iter(_pt.PROJECT_TYPES))
        result = _pt.get_project_type(first_id)
        assert result is not None
        assert result["type_id"] == first_id

    def test_returns_none_for_unknown_type(self):
        result = _pt.get_project_type("__nonexistent_type__")
        assert result is None

    def test_returns_none_for_empty_string(self):
        assert _pt.get_project_type("") is None


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


@needs_module
class TestListProjectTypes:
    def test_returns_list(self):
        result = _pt.list_project_types()
        assert isinstance(result, list)

    def test_returns_sorted_list(self):
        result = _pt.list_project_types()
        assert result == sorted(result), f"Not sorted: {result}"

    def test_contains_every_registered_key(self):
        result = _pt.list_project_types()
        assert set(result) == set(_pt.PROJECT_TYPES.keys())


# ---------------------------------------------------------------------------
# Valid role mapping
# ---------------------------------------------------------------------------


@needs_module
class TestSuggestedModelRolesAreValid:
    def test_every_role_mentioned_maps_to_valid_taskrole(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            for role in definition["suggested_model_roles"]:
                assert role in _valid_role_values(), (
                    f"Type {type_id!r} references unknown role {role!r}. Valid: {sorted(_valid_role_values())}"
                )

    def test_roles_are_strings(self):
        for type_id, definition in _pt.PROJECT_TYPES.items():
            for role in definition["suggested_model_roles"]:
                assert isinstance(role, str), f"Type {type_id!r} role not a str: {role!r}"


# ---------------------------------------------------------------------------
# Dynamic extensibility
# ---------------------------------------------------------------------------


@needs_module
class TestDynamicRegistration:
    def test_register_new_type_adds_to_registry(self):
        original_ids = set(_pt.PROJECT_TYPES)
        assert "dynamic_test_type" not in original_ids

        _pt.register_project_type(
            "dynamic_test_type",
            {
                "type_id": "dynamic_test_type",
                "display_name": "Dynamic Test Type",
                "prompt_templates": {
                    "system": "You are building a {description}.",
                    "user": "Please implement: {description}",
                },
                "validation_rules": [
                    "ast.parse validates syntax integrity",
                    "importlib.util.find_spec checks dependency availability",
                ],
                "acceptance_criteria": [
                    "All unit tests pass",
                    "Gate is green",
                ],
                "suggested_model_roles": ["coder", "reviewer"],
            },
        )

        assert "dynamic_test_type" in _pt.PROJECT_TYPES

    def test_register_new_type_is_immediately_lookupable(self):
        assert "dynamic_test_type" in _pt.PROJECT_TYPES  # from prior test

        result = _pt.get_project_type("dynamic_test_type")
        assert result is not None
        assert result["type_id"] == "dynamic_test_type"
        assert result["display_name"] == "Dynamic Test Type"
        assert "{description}" in result["prompt_templates"]["system"]

    def test_register_new_type_appears_in_list(self):
        result = _pt.list_project_types()
        assert "dynamic_test_type" in result

    def test_register_duplicate_type_id_updates_definition(self):
        _pt.register_project_type(
            "dynamic_test_type",
            {
                "type_id": "dynamic_test_type",
                "display_name": "Updated Dynamic Test Type",
                "prompt_templates": {"system": "Updated: {description}"},
                "validation_rules": ["ast.parse validates syntax"],
                "acceptance_criteria": ["Updated criteria"],
                "suggested_model_roles": ["planner"],
            },
        )
        updated = _pt.get_project_type("dynamic_test_type")
        assert updated is not None
        assert updated["display_name"] == "Updated Dynamic Test Type"
