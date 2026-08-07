"""Tests for project type registry — SoftwareGenerator project type definitions."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_ludd.cloud.project_types import (
    PROJECT_TYPE_REGISTRY,
    ProjectType,
    available_type_ids,
    get_project_type,
    register_project_type,
)


class TestProjectTypeDataclass:
    def test_default_construction(self):
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

    def test_full_construction(self):
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

    def test_immutable(self):
        pt = ProjectType(type_id="test", display_name="Test", default_entry_point="main.py")
        with pytest.raises(FrozenInstanceError):
            pt.type_id = "changed"
        assert pt.type_id == "test"


class TestRegistry:
    def test_all_expected_types_registered(self):
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

    def test_every_type_has_entry_point(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.default_entry_point, f"{type_id} missing default_entry_point"

    def test_every_type_has_display_name(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.display_name, f"{type_id} missing display_name"

    def test_every_type_has_prompt_templates(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert pt.prompt_template_planner, f"{type_id} missing planner template"
            assert pt.prompt_template_coder, f"{type_id} missing coder template"

    def test_every_type_has_validation_rules(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.validation_rules, list), f"{type_id} validation_rules not a list"
            assert len(pt.validation_rules) > 0, f"{type_id} has no validation rules"

    def test_every_type_has_acceptance_criteria(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.acceptance_criteria, list), f"{type_id} acceptance_criteria not a list"
            assert len(pt.acceptance_criteria) > 0, f"{type_id} has no acceptance criteria"

    def test_game_backward_compat_entry_point(self):
        pt = PROJECT_TYPE_REGISTRY["game"]
        assert pt.default_entry_point == "game.py"

    def test_type_ids_are_slugs(self):
        for type_id in PROJECT_TYPE_REGISTRY:
            assert type_id == type_id.lower().replace(" ", "_"), f"Invalid slug: {type_id}"

    def test_output_structures_are_dicts(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.output_structure, dict), f"{type_id} output_structure not a dict"

    def test_suggested_model_roles_are_dicts(self):
        for type_id, pt in PROJECT_TYPE_REGISTRY.items():
            assert isinstance(pt.suggested_model_roles, dict), f"{type_id} model_roles not a dict"

    def test_count_at_least_12(self):
        assert len(PROJECT_TYPE_REGISTRY) >= 12


class TestAvailableTypeIds:
    def test_returns_sorted_list(self):
        ids = available_type_ids()
        assert isinstance(ids, list)
        assert ids == sorted(ids)

    def test_contains_game(self):
        assert "game" in available_type_ids()


class TestGetProjectType:
    def test_known_type(self):
        pt = get_project_type("game")
        assert pt.type_id == "game"
        assert pt.display_name is not None

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError):
            get_project_type("nonexistent_type")


class TestRegisterProjectType:
    def test_register_new_custom_type(self):
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

    def test_register_overwrite_existing(self):
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
