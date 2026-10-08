"""Tests for project type registry — SoftwareGenerator project type definitions."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.cloud.project_types import (
    PROJECT_TYPE_REGISTRY,
    VALID_PROJECT_TYPES,
    VALIDATION_RULES,
    ProjectSpec,
    ProjectType,
    _check_importable,
    _validate_rule,
    available_type_ids,
    get_project_type,
    register_project_type,
    resolve_model_profile,
    validate_project_against_rules,
    validate_project_type,
)
from general_ludd.schemas.benchmark import TaskRole


class TestProjectTypeDataclass:
    def test_default_construction(self) -> None:
        pt = ProjectType(
            type_id="test_type",
            display_name="Test Type",
            default_entry_point="main.py",
        )
        assert pt.type_id == "test_type"
        assert pt.display_name == "Test Type"
        assert pt.default_entry_point == "main.py"
        assert pt.output_structure == {}
        assert pt.validation_rules == []
        assert pt.prompt_template_planner == ""
        assert pt.prompt_template_coder == ""
        assert pt.acceptance_criteria == []
        assert pt.suggested_model_roles == {}

    def test_full_construction(self) -> None:
        pt = ProjectType(
            type_id="cli_tool",
            display_name="CLI Tool",
            default_entry_point="cli.py",
            output_structure={"cli.py": "Command-line entry point"},
            validation_rules=["ast_valid", "importable", "has_main"],
            prompt_template_planner="Plan a CLI tool: {context}",
            prompt_template_coder="Write a CLI tool that: {context}",
            acceptance_criteria=["--help works", "runs without args"],
            suggested_model_roles={"planner": "reasoning", "coder": "coding"},
        )
        assert pt.output_structure == {"cli.py": "Command-line entry point"}
        assert pt.validation_rules == ["ast_valid", "importable", "has_main"]
        assert pt.prompt_template_planner == "Plan a CLI tool: {context}"
        assert pt.acceptance_criteria == ["--help works", "runs without args"]
        assert pt.suggested_model_roles == {"planner": "reasoning", "coder": "coding"}

    def test_immutable(self) -> None:
        pt = ProjectType(type_id="test", display_name="Test", default_entry_point="main.py")
        with pytest.raises(FrozenInstanceError):
            pt.__setattr__("type_id", "changed")
        assert pt.type_id == "test"


class TestRegistry:
    def test_all_expected_types_registered(self) -> None:
        expected = {
            "game",
            "website",
            "scraper",
            "database_schema",
            "cli_tool",
            "api_server",
            "word_processor",
            "kernel_module",
            "data_pipeline",
            "chatbot",
            "desktop_app",
            "test_suite",
        }
        registered = set(PROJECT_TYPE_REGISTRY)
        missing = expected - registered
        assert not missing, f"Missing types: {missing}"

    def test_every_type_has_entry_point(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.default_entry_point, f"{type_id} missing default_entry_point"

    def test_every_type_has_display_name(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.display_name, f"{type_id} missing display_name"

    def test_every_type_has_prompt_templates(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.prompt_template_planner, f"{type_id} missing planner template"
            assert pt.prompt_template_coder, f"{type_id} missing coder template"

    def test_every_type_has_validation_rules(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.validation_rules, list), f"{type_id} validation_rules not a list"
            assert len(pt.validation_rules) > 0, f"{type_id} has no validation rules"

    def test_every_type_has_acceptance_criteria(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.acceptance_criteria, list), f"{type_id} acceptance_criteria not a list"
            assert len(pt.acceptance_criteria) > 0, f"{type_id} has no acceptance criteria"

    def test_game_backward_compat_entry_point(self) -> None:
        pt = PROJECT_TYPE_REGISTRY["game"]
        assert pt.default_entry_point == "game.py"

    def test_type_ids_are_slugs(self) -> None:
        for type_id in PROJECT_TYPE_REGISTRY:
            assert type_id == type_id.lower().replace(" ", "_"), f"Invalid slug: {type_id}"

    def test_output_structures_are_dicts(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.output_structure, dict), f"{type_id} output_structure not a dict"

    def test_suggested_model_roles_are_dicts(self) -> None:
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.suggested_model_roles, dict), f"{type_id} model_roles not a dict"

    def test_count_at_least_12(self) -> None:
        assert len(PROJECT_TYPE_REGISTRY) >= 12


class TestAvailableTypeIds:
    def test_returns_sorted_list(self) -> None:
        ids = available_type_ids()
        assert isinstance(ids, list)
        assert ids == sorted(ids)

    def test_contains_game(self) -> None:
        assert "game" in available_type_ids()


class TestGetProjectType:
    def test_known_type(self) -> None:
        pt = get_project_type("game")
        assert pt.type_id == "game"
        assert pt.display_name is not None

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(KeyError):
            get_project_type("nonexistent_type")


class TestRegisterProjectType:
    def test_register_new_custom_type(self) -> None:
        custom = ProjectType(
            type_id="custom_plugin",
            display_name="Custom Plugin",
            default_entry_point="plugin.py",
            output_structure={"plugin.py": "Plugin entry point"},
            validation_rules=["ast_valid"],
            prompt_template_planner="Plan: {context}",
            prompt_template_coder="Code: {context}",
            acceptance_criteria=["loads successfully"],
            suggested_model_roles={"coder": "coding"},
        )
        register_project_type(custom)
        assert "custom_plugin" in PROJECT_TYPE_REGISTRY
        retrieved = PROJECT_TYPE_REGISTRY["custom_plugin"]
        assert retrieved is custom

    def test_register_overwrite_existing(self) -> None:
        original = PROJECT_TYPE_REGISTRY["game"]
        overwrite = ProjectType(
            type_id="game",
            display_name="Modified Game",
            default_entry_point="game2.py",
            output_structure={"game2.py": "Modified"},
            validation_rules=["ast_valid"],
            prompt_template_planner="Plan: {context}",
            prompt_template_coder="Code: {context}",
            acceptance_criteria=["runs"],
            suggested_model_roles={},
        )
        register_project_type(overwrite)
        assert PROJECT_TYPE_REGISTRY["game"] is overwrite
        PROJECT_TYPE_REGISTRY["game"] = original


# ── ProjectSpec ────────────────────────────────────────────────────────────────


class TestProjectSpec:
    def test_default_construction(self) -> None:
        spec = ProjectSpec(project_type="game")
        assert spec.project_type == "game"
        assert spec.name == ""
        assert spec.description == ""
        assert spec.prompt_template == ""

    def test_role_defaults_from_game_type(self) -> None:
        spec = ProjectSpec(project_type="game")
        assert len(spec.roles) == 3
        assert "planner" in [r.value for r in spec.roles]

    def test_role_defaults_from_web_type(self) -> None:
        spec = ProjectSpec(project_type="web")
        assert len(spec.roles) == 2
        assert "coder" in [r.value for r in spec.roles]

    def test_role_defaults_from_cli_type(self) -> None:
        spec = ProjectSpec(project_type="cli")
        assert len(spec.roles) == 2

    def test_role_defaults_from_library_type(self) -> None:
        spec = ProjectSpec(project_type="library")
        assert len(spec.roles) == 1
        assert "coder" in [r.value for r in spec.roles]

    def test_role_defaults_for_unknown_type_are_empty(self) -> None:
        spec = ProjectSpec(project_type="nonexistent")
        assert spec.roles == ()

    def test_explicit_roles_override_defaults(self) -> None:
        custom_roles = (TaskRole.CODER,)
        spec = ProjectSpec(project_type="game", roles=custom_roles)
        assert spec.roles is custom_roles

    def test_tech_stack_defaults_from_game(self) -> None:
        spec = ProjectSpec(project_type="game")
        assert spec.tech_stack == ("pygame", "python")

    def test_tech_stack_defaults_from_web(self) -> None:
        spec = ProjectSpec(project_type="web")
        assert spec.tech_stack == ("html", "css", "javascript")

    def test_explicit_tech_stack_overrides_defaults(self) -> None:
        custom = ("rust", "wasm")
        spec = ProjectSpec(project_type="game", tech_stack=custom)
        assert spec.tech_stack == custom

    def test_empty_tuple_triggers_defaults(self) -> None:
        spec = ProjectSpec(project_type="game", tech_stack=())
        assert spec.tech_stack == ("pygame", "python")

    def test_non_empty_tech_stack_stays(self) -> None:
        spec = ProjectSpec(project_type="game", tech_stack=("rust",))
        assert spec.tech_stack == ("rust",)

    def test_all_fields_explicit(self) -> None:
        spec = ProjectSpec(
            project_type="custom",
            name="My Project",
            description="A custom project",
            prompt_template="Build {name}",
            roles=(TaskRole.CODER, TaskRole.REVIEWER),
            tech_stack=("python", "fastapi"),
        )
        assert spec.name == "My Project"
        assert spec.description == "A custom project"
        assert spec.prompt_template == "Build {name}"
        assert spec.roles == (TaskRole.CODER, TaskRole.REVIEWER)
        assert spec.tech_stack == ("python", "fastapi")

    def test_equality_same_fields(self) -> None:
        a = ProjectSpec(project_type="game")
        b = ProjectSpec(project_type="game")
        assert a == b

    def test_equality_different_type(self) -> None:
        a = ProjectSpec(project_type="game")
        b = ProjectSpec(project_type="web")
        assert a != b

    def test_equality_different_name(self) -> None:
        a = ProjectSpec(project_type="game", name="A")
        b = ProjectSpec(project_type="game", name="B")
        assert a != b


# ── ValidateProjectAgainstRules ────────────────────────────────────────────────


class TestValidateProjectAgainstRules:
    def test_empty_rules_passes(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=[],
        )
        assert validate_project_against_rules("x = 1", pt) is True

    def test_ast_valid_with_valid_code(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["ast_valid"],
        )
        assert validate_project_against_rules("x = 1\ny = 2\n", pt) is True

    def test_ast_valid_with_syntax_error(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["ast_valid"],
        )
        assert validate_project_against_rules("x =", pt) is False

    def test_no_syntax_errors_with_valid_code(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["no_syntax_errors"],
        )
        assert validate_project_against_rules("def f(): pass\n", pt) is True

    def test_no_syntax_errors_with_invalid_code(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["no_syntax_errors"],
        )
        assert validate_project_against_rules("def f(", pt) is False

    def test_has_entry_point_non_empty(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["has_entry_point"],
        )
        assert validate_project_against_rules("print('hello')", pt) is True

    def test_has_entry_point_empty(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["has_entry_point"],
        )
        assert validate_project_against_rules("", pt) is False

    def test_has_entry_point_whitespace_only(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["has_entry_point"],
        )
        assert validate_project_against_rules("   \n  ", pt) is False

    def test_importable_with_valid_module(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["importable"],
        )
        assert validate_project_against_rules("def hello(): return 'world'\n", pt) is True

    def test_importable_with_invalid_module(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["importable"],
        )
        assert validate_project_against_rules("raise RuntimeError('boom')\n", pt) is False

    def test_unknown_rule_name_passes(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["some_future_rule"],
        )
        assert validate_project_against_rules("anything", pt) is True

    def test_mixed_rules_all_pass(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["ast_valid", "has_entry_point", "no_syntax_errors"],
        )
        assert validate_project_against_rules("x = 42\n", pt) is True

    def test_mixed_rules_one_fails(self) -> None:
        pt = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="test.py",
            validation_rules=["ast_valid", "has_entry_point", "no_syntax_errors"],
        )
        assert validate_project_against_rules("", pt) is False

    def test_all_registered_types_have_good_code(self) -> None:
        """Every registered type's prompt_template_coder is not code to validate,
        but the validation rules should produce valid/invalid results deterministically."""
        for _type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.validation_rules, list)
            has_valid = validate_project_against_rules("x = 1", pt)
            has_invalid = validate_project_against_rules("", pt)
            assert isinstance(has_valid, bool)
            assert isinstance(has_invalid, bool)


# ── _validate_rule ─────────────────────────────────────────────────────────────


class TestValidateRule:
    def test_ast_valid_true(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("x = 1", "ast_valid", pt) is True

    def test_ast_valid_false(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("x =", "ast_valid", pt) is False

    def test_no_syntax_errors_true(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("pass", "no_syntax_errors", pt) is True

    def test_no_syntax_errors_false(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("break", "no_syntax_errors", pt) is False

    def test_has_entry_point_true(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("x", "has_entry_point", pt) is True

    def test_has_entry_point_false(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("", "has_entry_point", pt) is False

    def test_unknown_rule_true(self) -> None:
        pt = ProjectType(type_id="t", display_name="T", default_entry_point="t.py")
        assert _validate_rule("anything", "nonexistent_rule", pt) is True


# ── _check_importable ──────────────────────────────────────────────────────────


class TestCheckImportable:
    def test_valid_code_returns_true(self) -> None:
        assert _check_importable("def f(): return 42\n") is True

    def test_syntax_error_returns_false(self) -> None:
        assert _check_importable("def f(:") is False

    def test_import_error_returns_false(self) -> None:
        assert _check_importable("import nonexistent_module_xyz_12345\n") is False

    def test_empty_code_returns_true(self) -> None:
        assert _check_importable("") is True


# ── resolve_model_profile ─────────────────────────────────────────────────────


class TestResolveModelProfile:
    def test_game_profile(self) -> None:
        profile = resolve_model_profile("game")
        assert profile == {"planner": "reasoning", "coder": "coding", "reviewer": "reasoning"}

    def test_web_profile(self) -> None:
        profile = resolve_model_profile("web")
        assert profile == {"planner": "reasoning", "coder": "coding"}

    def test_cli_profile(self) -> None:
        profile = resolve_model_profile("cli")
        assert profile == {"planner": "reasoning", "coder": "coding", "reviewer": "reasoning"}

    def test_library_profile(self) -> None:
        profile = resolve_model_profile("library")
        assert profile == {"coder": "coding"}

    def test_unknown_type_returns_empty(self) -> None:
        profile = resolve_model_profile("nonexistent")
        assert profile == {}


# ── validate_project_type ──────────────────────────────────────────────────────


class TestValidateProjectType:
    def test_valid_type_does_not_raise(self) -> None:
        validate_project_type("game")

    def test_valid_cli_type(self) -> None:
        validate_project_type("cli")

    def test_valid_web_type(self) -> None:
        validate_project_type("web")

    def test_valid_library_type(self) -> None:
        validate_project_type("library")

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown project type"):
            validate_project_type("nonexistent")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown project type"):
            validate_project_type(None)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown project type"):
            validate_project_type("")

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValueError, match="Unknown project type"):
            validate_project_type("Game")


# ── VALID_PROJECT_TYPES ────────────────────────────────────────────────────────


class TestValidProjectTypes:
    def test_is_tuple(self) -> None:
        assert isinstance(VALID_PROJECT_TYPES, tuple)

    def test_contains_expected_types(self) -> None:
        assert "game" in VALID_PROJECT_TYPES
        assert "web" in VALID_PROJECT_TYPES
        assert "cli" in VALID_PROJECT_TYPES
        assert "library" in VALID_PROJECT_TYPES

    def test_all_members_are_strings(self) -> None:
        for v in VALID_PROJECT_TYPES:
            assert isinstance(v, str)


# ── VALIDATION_RULES ───────────────────────────────────────────────────────────


class TestValidationRules:
    def test_is_dict(self) -> None:
        assert isinstance(VALIDATION_RULES, dict)

    def test_game_has_required_imports(self) -> None:
        required_imports = VALIDATION_RULES["game"]["required_imports"]
        assert isinstance(required_imports, list)
        assert "pygame" in required_imports

    def test_web_has_required_elements(self) -> None:
        required_elements = VALIDATION_RULES["web"]["required_elements"]
        assert isinstance(required_elements, list)
        assert "html" in required_elements

    def test_cli_has_exit_codes(self) -> None:
        assert "exit_codes" in VALIDATION_RULES["cli"]
        assert VALIDATION_RULES["cli"]["exit_codes"] == [0, 1, 2]

    def test_library_requires_public_api(self) -> None:
        assert VALIDATION_RULES["library"]["require_public_api"] is True


# ── ProjectType validate callable field ────────────────────────────────────────


class TestProjectTypeValidateCallable:
    def test_can_attach_callable(self) -> None:
        pt = ProjectType(
            type_id="validated",
            display_name="Validated",
            default_entry_point="v.py",
            validate=lambda code: "error" not in code,
        )
        assert pt.validate is not None
        assert callable(pt.validate)

    def test_validate_defaults_to_none(self) -> None:
        pt = ProjectType(type_id="x", display_name="X", default_entry_point="x.py")
        assert pt.validate is None

    def test_custom_validate_applied(self) -> None:
        pt = ProjectType(
            type_id="custom_validator",
            display_name="CV",
            default_entry_point="cv.py",
            validate=lambda code: len(code) > 0,
        )
        validator = pt.validate
        assert validator is not None
        assert validator("hello") is True
        assert validator("") is False

    def test_validate_can_raise(self) -> None:
        def strict_validate(code: str) -> bool:
            if not code.strip():
                raise ValueError("empty")
            return True

        pt = ProjectType(
            type_id="strict",
            display_name="Strict",
            default_entry_point="s.py",
            validate=strict_validate,
        )
        validator = pt.validate
        assert validator is not None
        assert validator("ok") is True
        with pytest.raises(ValueError, match="empty"):
            validator("   ")


# ── _BASE_DEFINITIONS integrity ────────────────────────────────────────────────


class TestBaseDefinitionsIntegrity:
    def test_game_definition_completeness(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        game = _BASE_DEFINITIONS["game"]
        required = [
            "type_id",
            "display_name",
            "description",
            "output_extension",
            "required_imports",
            "default_entry_point",
            "output_structure",
            "validation_rules",
            "prompt_template_planner",
            "prompt_template_coder",
            "acceptance_criteria",
            "suggested_model_roles",
            "token_budget_estimate",
        ]
        for key in required:
            assert key in game, f"Missing key {key} in game definition"

    def test_game_prompt_contains_pygame(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        coder = _BASE_DEFINITIONS["game"]["prompt_template_coder"]
        assert "pygame" in coder.lower()
        assert "game.py" in coder

    def test_website_definition_has_three_output_files(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        website = _BASE_DEFINITIONS["website"]
        assert len(website["output_structure"]) == 3
        assert "index.html" in website["output_structure"]
        assert "style.css" in website["output_structure"]
        assert "script.js" in website["output_structure"]

    def test_kernel_module_has_makefile(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        km = _BASE_DEFINITIONS["kernel_module"]
        assert "Makefile" in km["output_structure"]
        assert "obj-m" in km["prompt_template_coder"]

    def test_desktop_app_uses_tkinter(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        da = _BASE_DEFINITIONS["desktop_app"]
        assert "tkinter" in da["prompt_template_coder"].lower()

    def test_data_pipeline_has_pandas(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        dp = _BASE_DEFINITIONS["data_pipeline"]
        assert dp["required_imports"] == ["pandas"]
        rules = dp["validation_rules"]
        assert any("extract" in r for r in rules)
        assert any("transform" in r for r in rules)
        assert any("load" in r for r in rules)

    def test_api_server_has_health_check(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        api = _BASE_DEFINITIONS["api_server"]
        assert "health" in api["prompt_template_coder"].lower()
        assert "cors" in api["prompt_template_coder"].lower()

    def test_word_processor_has_file_io(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        wp = _BASE_DEFINITIONS["word_processor"]
        assert "has_file_io" in wp["validation_rules"]

    def test_scraper_has_requirements_txt(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        sc = _BASE_DEFINITIONS["scraper"]
        assert "requirements.txt" in sc["output_structure"]
        assert "beautifulsoup4" in sc["required_imports"]

    def test_chatbot_has_exit_command(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        cb = _BASE_DEFINITIONS["chatbot"]
        assert "has_exit_command" in cb["validation_rules"]
        assert "/exit" in cb["prompt_template_coder"]

    def test_test_suite_uses_pytest(self) -> None:
        from general_ludd.cloud.project_types import _BASE_DEFINITIONS

        ts = _BASE_DEFINITIONS["test_suite"]
        assert "test_main.py" in ts["default_entry_point"]
        assert "conftest.py" in ts["output_structure"]
        assert "pytest" in ts["prompt_template_coder"].lower()
