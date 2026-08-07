"""Tests for SoftwareGenerator — multi-project-type code generation.

Covers: instantiation, generate() delegation, ProjectSpec defaults,
validation rules, backward compat, sequential runs, model routing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.project_types import (
    VALID_PROJECT_TYPES,
    VALIDATION_RULES,
    ProjectSpec as ProjectSpecPT,
    resolve_model_profile,
    validate_project_type,
)
from general_ludd.cloud.software_generator import ProjectSpec, SoftwareGenerator
from general_ludd.models.gateway import ModelGateway, ModelResponse
from general_ludd.schemas.benchmark import TaskRole


def _make_response(content: str) -> ModelResponse:
    usage: dict[str, object] = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    return ModelResponse(content=content, usage_metadata=usage, cost_estimate=0.002, model_name="test-model")


def _make_gateway(responses: list[str] | None = None) -> ModelGateway:
    gw = MagicMock(spec=ModelGateway)
    if responses:
        call_responses = [_make_response(r) for r in responses]
        gw.call_model.side_effect = call_responses
    else:
        gw.call_model.return_value = _make_response("default output")
    return gw


def _spec(**overrides: object) -> ProjectSpec:
    defaults: dict[str, object] = {
        "name": "test",
        "project_type": "game",
        "description": "desc",
        "prompt_template": "Write a game",
    }
    defaults.update(overrides)
    return ProjectSpec(**defaults)  # type: ignore[arg-type]


# ── ProjectSpecPT defaults per type (from project_types module) ────────


class TestProjectSpecDefaults:
    def test_game_has_default_planner_coder_reviewer(self):
        spec = ProjectSpecPT(project_type="game", name="test_game", description="A test")
        assert spec.project_type == "game"
        assert spec.roles == (TaskRole.PLANNER, TaskRole.CODER, TaskRole.REVIEWER)

    def test_web_has_default_planner_coder(self):
        spec = ProjectSpecPT(project_type="web", name="test_web", description="A site")
        assert spec.project_type == "web"
        assert spec.roles == (TaskRole.PLANNER, TaskRole.CODER)

    def test_cli_has_default_coder_reviewer(self):
        spec = ProjectSpecPT(project_type="cli", name="test_cli", description="A tool")
        assert spec.project_type == "cli"
        assert spec.roles == (TaskRole.CODER, TaskRole.REVIEWER)

    def test_library_has_default_coder_only(self):
        spec = ProjectSpecPT(project_type="library", name="test_lib", description="A lib")
        assert spec.project_type == "library"
        assert spec.roles == (TaskRole.CODER,)

    def test_default_description_empty_string(self):
        spec = ProjectSpecPT(project_type="game", name="test")
        assert spec.description == ""

    def test_default_name_empty_for_game(self):
        spec = ProjectSpecPT(project_type="game")
        assert spec.name == ""

    def test_custom_roles_override_defaults(self):
        spec = ProjectSpecPT(
            project_type="game",
            name="custom",
            roles=(TaskRole.CODER,),
        )
        assert spec.roles == (TaskRole.CODER,)

    def test_tech_stack_defaults_match_type(self):
        web_spec = ProjectSpecPT(project_type="web", name="site")
        assert "html" in web_spec.tech_stack

        game_spec = ProjectSpecPT(project_type="game", name="game")
        assert "pygame" in game_spec.tech_stack

        cli_spec = ProjectSpecPT(project_type="cli", name="tool")
        assert "click" in cli_spec.tech_stack

    def test_game_spec_carries_pygame_default(self):
        spec = ProjectSpecPT(project_type="game", name="test")
        assert "pygame" in spec.tech_stack


# ── Validation rules load correctly ────────────────────────────────────


class TestValidationRules:
    def test_every_project_type_has_rules(self):
        for ptype in VALID_PROJECT_TYPES:
            assert ptype in VALIDATION_RULES, f"{ptype} missing validation rules"
            assert isinstance(VALIDATION_RULES[ptype], dict)

    def test_game_rules_require_pygame(self):
        rules = VALIDATION_RULES["game"]
        assert rules.get("required_imports") is not None
        assert "pygame" in rules["required_imports"]

    def test_web_rules_require_html_template(self):
        rules = VALIDATION_RULES["web"]
        assert rules.get("required_elements") is not None
        assert any("html" in str(el).lower() for el in rules["required_elements"])

    def test_cli_rules_require_argparse_or_click(self):
        rules = VALIDATION_RULES["cli"]
        imports = rules.get("required_imports", [])
        assert any(imp in ("argparse", "click") for imp in imports)

    def test_library_rules_require_public_api(self):
        rules = VALIDATION_RULES["library"]
        assert rules.get("require_public_api") is True or "require_public_api" in rules

    def test_unknown_type_raises_clear_error(self):
        with pytest.raises(ValueError, match="Unknown project type"):
            validate_project_type("quantum_computer")

    def test_unknown_type_message_includes_known_types(self):
        with pytest.raises(ValueError) as exc:
            validate_project_type("not_real")
        assert any(known in str(exc.value) for known in ("game", "web", "cli", "library"))


# ── SoftwareGenerator instantiation ────────────────────────────────────


class TestSoftwareGeneratorInstantiation:
    def test_constructs_with_none_gateway_succeeds_and_generate_raises(self):
        gen = SoftwareGenerator(gateway=None)
        with pytest.raises(ValueError, match="ModelGateway"):
            gen.generate(_spec())

    def test_constructs_with_gateway(self):
        gw = _make_gateway()
        gen = SoftwareGenerator(gateway=gw)
        assert gen._gateway is gw

    def test_constructs_with_task_policy(self):
        gw = _make_gateway()
        policy = MagicMock()
        gen = SoftwareGenerator(gateway=gw, task_policy=policy)
        assert gen._task_policy is policy

    def test_all_project_types_instantiatable(self):
        gw = _make_gateway()
        gen = SoftwareGenerator(gateway=gw)
        assert gen._gateway is gw
        for ptype in VALID_PROJECT_TYPES:
            spec_pt = ProjectSpecPT(project_type=ptype, name=f"test_{ptype}")
            assert spec_pt.project_type == ptype
            assert isinstance(spec_pt.tech_stack, tuple)


# ── generate() returns normalized Python string ────────────────────────


class TestGenerateReturnsString:
    def test_generate_returns_string(self):
        gw = _make_gateway(["print('hello')"])
        gen = SoftwareGenerator(gateway=gw)
        result = gen.generate(_spec(prompt_template="Write a game"))
        assert isinstance(result, str)
        assert "hello" in result or "print" in result

    def test_generate_multi_uses_pipeline(self):
        gw = _make_gateway(["DESIGN: game", "CODE: print('ok')", "REVIEW: passed"])
        gen = SoftwareGenerator(gateway=gw)
        result = gen.generate_multi(
            _spec(prompt_template="Build a game about trees"),
            model_profiles={
                TaskRole.PLANNER: "default",
                TaskRole.CODER: "default",
                TaskRole.REVIEWER: "default",
            },
        )
        assert isinstance(result, str)

    def test_generate_multi_raises_when_gateway_empty(self):
        gen = SoftwareGenerator(gateway=None)
        with pytest.raises(ValueError, match="ModelGateway"):
            gen.generate_multi(
                _spec(),
                model_profiles={
                    TaskRole.PLANNER: "p",
                    TaskRole.CODER: "c",
                    TaskRole.REVIEWER: "r",
                },
            )

    def test_prompt_template_passed_to_gateway(self):
        gw = _make_gateway(["print('ok')"])
        gen = SoftwareGenerator(gateway=gw)
        gen.generate(_spec(prompt_template="Build a platformer game"))
        gw.call_model.assert_called()
        call = gw.call_model.call_args_list[0]
        messages = call[0][1]
        assert any("platformer" in str(m) for m in messages)


# ── Backward compat: project_type="game" matches old behavior ────────


class TestBackwardCompatGame:
    def test_game_type_uses_pygame_tech_stack(self):
        spec = ProjectSpecPT(project_type="game", name="test")
        assert "pygame" in spec.tech_stack

    def test_game_spec_matches_old_GameGenerator_interface(self):
        spec = ProjectSpecPT(project_type="game", name="test", description="desc")
        assert spec.name == "test"
        assert spec.description == "desc"
        assert spec.project_type == "game"
        roles = spec.roles
        assert TaskRole.CODER in roles

    def test_game_generation_output_is_string(self):
        code = "import pygame\nprint('running')"
        gw = _make_gateway([code])
        gen = SoftwareGenerator(gateway=gw)
        result = gen.generate(_spec(prompt_template="Write a game"))
        assert isinstance(result, str)
        assert "pygame" in result


# ── Sequential runs (no state leakage) ─────────────────────────────────


class TestSequentialRuns:
    def test_two_games_in_sequence(self):
        gw = _make_gateway(["game1_code", "game2_code"])
        gen = SoftwareGenerator(gateway=gw)
        r1 = gen.generate(_spec(name="game1", description="first", prompt_template="t1"))
        r2 = gen.generate(_spec(name="game2", description="second", prompt_template="t2"))
        assert "game1_code" in r1
        assert "game2_code" in r2

    def test_different_types_in_sequence(self):
        gw = _make_gateway(
            ["from game import X", "<html>web</html>", "import click\ndef cli(): pass", "def lib(): pass"]
        )
        gen = SoftwareGenerator(gateway=gw)
        for ptype in ("game", "web", "cli", "library"):
            result = gen.generate(_spec(project_type=ptype, name=f"seq_{ptype}", prompt_template="t"))
            assert isinstance(result, str), f"{ptype} generation returned non-string"

    def test_no_context_leakage_between_runs(self):
        gw = _make_gateway(["secret_a", "secret_b"])
        gen = SoftwareGenerator(gateway=gw)
        r1 = gen.generate(_spec(name="a", description="secret_a", prompt_template="t1"))
        r2 = gen.generate(_spec(name="b", description="secret_b", prompt_template="t2"))
        assert "secret_a" in r1
        assert "secret_b" in r2


# ── Model profile routing per project type ─────────────────────────────


class TestModelProfileRouting:
    def test_game_routes_to_game_profile(self):
        profile = resolve_model_profile("game")
        assert profile is not None
        assert profile.get("planner") is not None

    def test_web_routes_to_web_profile(self):
        profile = resolve_model_profile("web")
        assert profile.get("coder") is not None

    def test_cli_routes_to_cli_profile(self):
        profile = resolve_model_profile("cli")
        assert profile is not None

    def test_library_routes_to_library_profile(self):
        profile = resolve_model_profile("library")
        assert profile is not None

    def test_unknown_type_returns_default_profile(self):
        profile = resolve_model_profile("unknown_type_xyz")
        assert profile == {} or profile is None

    def test_game_profile_has_planner_coder_reviewer_keys(self):
        profile = resolve_model_profile("game")
        for key in ("planner", "coder", "reviewer"):
            assert key in profile, f"game profile missing {key}"

    def test_web_profile_has_planner_coder_keys(self):
        profile = resolve_model_profile("web")
        assert "planner" in profile
        assert "coder" in profile

    def test_custom_model_id_passed_to_gateway(self):
        gw = _make_gateway(["custom_output"])
        gen = SoftwareGenerator(gateway=gw)
        gen.generate(_spec(prompt_template="Write a game"), model_id="my-custom-model")
        gw.call_model.assert_called()
        call = gw.call_model.call_args_list[0]
        assert call[0][0] == "my-custom-model"


# ── validate_project_type function ─────────────────────────────────────


class TestValidateProjectType:
    def test_valid_types_pass(self):
        for ptype in VALID_PROJECT_TYPES:
            validate_project_type(ptype)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            validate_project_type("")

    def test_none_raises(self):
        with pytest.raises((ValueError, TypeError)):
            validate_project_type(None)
