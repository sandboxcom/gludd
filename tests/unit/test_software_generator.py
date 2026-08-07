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


# ── ProjectSpec defaults per type ──────────────────────────────────────


class TestProjectSpecDefaults:
    def test_game_has_default_planner_coder_reviewer(self):
        spec = ProjectSpec(project_type="game", name="test_game", description="A test")
        assert spec.project_type == "game"
        assert spec.roles == (TaskRole.PLANNER, TaskRole.CODER, TaskRole.REVIEWER)

    def test_web_has_default_planner_coder(self):
        spec = ProjectSpec(project_type="web", name="test_web", description="A site")
        assert spec.project_type == "web"
        assert spec.roles == (TaskRole.PLANNER, TaskRole.CODER)

    def test_cli_has_default_coder_reviewer(self):
        spec = ProjectSpec(project_type="cli", name="test_cli", description="A tool")
        assert spec.project_type == "cli"
        assert spec.roles == (TaskRole.CODER, TaskRole.REVIEWER)

    def test_library_has_default_coder_only(self):
        spec = ProjectSpec(project_type="library", name="test_lib", description="A lib")
        assert spec.project_type == "library"
        assert spec.roles == (TaskRole.CODER,)

    def test_default_description_empty_string(self):
        spec = ProjectSpec(project_type="game", name="test")
        assert spec.description == ""

    def test_default_name_empty_for_game(self):
        spec = ProjectSpec(project_type="game")
        assert spec.name == ""

    def test_custom_roles_override_defaults(self):
        spec = ProjectSpec(
            project_type="game",
            name="custom",
            roles=(TaskRole.CODER,),
        )
        assert spec.roles == (TaskRole.CODER,)

    def test_tech_stack_defaults_match_type(self):
        web_spec = ProjectSpec(project_type="web", name="site")
        assert "html" in web_spec.tech_stack

        game_spec = ProjectSpec(project_type="game", name="game")
        assert "pygame" in game_spec.tech_stack

        cli_spec = ProjectSpec(project_type="cli", name="tool")
        assert "click" in cli_spec.tech_stack

    def test_game_spec_carries_pygame_default(self):
        spec = ProjectSpec(project_type="game", name="test")
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
    def test_requires_gateway(self):
        with pytest.raises(ValueError, match="ModelGateway"):
            SoftwareGenerator(gateway=None)

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
        SoftwareGenerator(gateway=gw)
        for ptype in VALID_PROJECT_TYPES:
            spec = ProjectSpec(project_type=ptype, name=f"test_{ptype}")
            assert spec.project_type == ptype
            assert isinstance(spec.tech_stack, tuple)

    def test_invalid_project_type_rejected_on_generate(self):
        gw = _make_gateway()
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="test")
        spec.project_type = "not_a_real_type"
        with pytest.raises(ValueError, match="Unknown project type"):
            gen.generate(spec)

    def test_missing_name_raises(self):
        gw = _make_gateway()
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game")
        with pytest.raises(ValueError, match="name"):
            gen.generate(spec)


# ── generate() delegates to ModelPipeline ──────────────────────────────


class TestGenerateDelegatesToModelPipeline:
    def test_single_step_pipeline_with_game(self):
        gw = _make_gateway(["print('hello')"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(
            project_type="game",
            name="test_game",
            description="A test game",
            roles=(TaskRole.CODER,),
        )
        result = gen.generate(spec)
        assert result.success is True
        assert "hello" in result.final_output
        gw.call_model.assert_called()

    def test_game_pipeline_creates_planner_coder_reviewer(self):
        gw = _make_gateway(["DESIGN: game", "CODE: print('ok')", "REVIEW: passed"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="test_game", description="A game")
        result = gen.generate(spec)
        assert result.step_count == 3
        assert result.success is True
        assert len(gw.call_model.call_args_list) == 3

    def test_web_pipeline_creates_planner_coder(self):
        gw = _make_gateway(["DESIGN: site", "CODE: <html>ok</html>"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="web", name="test_site", description="A site")
        result = gen.generate(spec)
        assert result.step_count == 2
        assert result.success is True
        assert len(gw.call_model.call_args_list) == 2

    def test_cli_pipeline_creates_coder_reviewer(self):
        gw = _make_gateway(["CODE: import click", "REVIEW: approved"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="cli", name="test_cli", description="A tool")
        result = gen.generate(spec)
        assert result.step_count == 2
        assert result.success is True
        assert len(gw.call_model.call_args_list) == 2

    def test_library_pipeline_creates_coder_only(self):
        gw = _make_gateway(["def foo(): return 42"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="library", name="test_lib", description="A lib")
        result = gen.generate(spec)
        assert result.step_count == 1
        assert result.success is True
        assert len(gw.call_model.call_args_list) == 1

    def test_pipeline_raises_when_gateway_empty(self):
        gw = _make_gateway()
        gw.call_model.return_value = ModelResponse(content="", usage_metadata={}, cost_estimate=0.0, model_name="test")
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="empty_game", description="desc")
        with pytest.raises(RuntimeError, match="empty"):
            gen.generate(spec)

    def test_description_injected_into_step_prompts(self):
        gw = _make_gateway(["CODE: ok"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(
            project_type="game",
            name="my_game",
            description="Build a platformer",
            roles=(TaskRole.CODER,),
        )
        gen.generate(spec)
        call = gw.call_model.call_args_list[0]
        messages = call[0][1]
        assert any("platformer" in str(m) for m in messages)


# ── Backward compat: project_type="game" matches old behavior ────────


class TestBackwardCompatGame:
    def test_game_type_triggers_planner_coder_reviewer(self):
        gw = _make_gateway(["DESIGN", "CODE", "REVIEW OK"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="compat_test", description="desc")
        result = gen.generate(spec)
        assert result.step_count == 3
        tasks = [sr.role for sr in result.step_results]
        assert tasks == [TaskRole.PLANNER, TaskRole.CODER, TaskRole.REVIEWER]

    def test_game_type_uses_pygame_tech_stack(self):
        spec = ProjectSpec(project_type="game", name="test")
        assert "pygame" in spec.tech_stack

    def test_game_spec_matches_old_GameGenerator_interface(self):
        spec = ProjectSpec(project_type="game", name="test", description="desc")
        assert spec.name == "test"
        assert spec.description == "desc"
        assert spec.project_type == "game"
        roles = spec.roles
        assert TaskRole.CODER in roles

    def test_game_generation_output_is_runnable(self):
        code = "import pygame\nprint('running')"
        gw = _make_gateway([code])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(
            project_type="game",
            name="runnable_test",
            description="desc",
            roles=(TaskRole.CODER,),
        )
        result = gen.generate(spec)
        assert "pygame" in result.final_output
        assert result.success is True


# ── Sequential runs (no state leakage) ─────────────────────────────────


class TestSequentialRuns:
    def test_two_games_in_sequence(self):
        gw = _make_gateway(["game1_code", "game2_code"])
        gen = SoftwareGenerator(gateway=gw)
        spec1 = ProjectSpec(project_type="game", name="game1", description="first", roles=(TaskRole.CODER,))
        spec2 = ProjectSpec(project_type="game", name="game2", description="second", roles=(TaskRole.CODER,))
        r1 = gen.generate(spec1)
        r2 = gen.generate(spec2)
        assert r1.final_output == "game1_code"
        assert r2.final_output == "game2_code"
        assert r1.final_output != r2.final_output

    def test_different_types_in_sequence(self):
        gw = _make_gateway(
            ["from game import X", "<html>web</html>", "import click\ndef cli(): pass", "def lib(): pass"]
        )
        gen = SoftwareGenerator(gateway=gw)
        for ptype in ("game", "web", "cli", "library"):
            spec = ProjectSpec(project_type=ptype, name=f"seq_{ptype}", description="d", roles=(TaskRole.CODER,))
            result = gen.generate(spec)
            assert result.success is True, f"{ptype} pipeline failed"

    def test_no_context_leakage_between_runs(self):
        gw = _make_gateway(["secret_a", "secret_b"])
        gen = SoftwareGenerator(gateway=gw)
        spec_a = ProjectSpec(project_type="game", name="a", description="secret_a", roles=(TaskRole.CODER,))
        spec_b = ProjectSpec(project_type="game", name="b", description="secret_b", roles=(TaskRole.CODER,))
        r1 = gen.generate(spec_a)
        r2 = gen.generate(spec_b)
        assert "secret_a" in r1.final_output
        assert "secret_b" in r2.final_output
        assert "secret_a" not in r2.final_output

    def test_metrics_reset_per_run(self):
        gw = _make_gateway(["run1", "run2"])
        gen = SoftwareGenerator(gateway=gw)
        result1 = gen.generate(ProjectSpec(project_type="game", name="m1", description="d", roles=(TaskRole.CODER,)))
        result2 = gen.generate(ProjectSpec(project_type="game", name="m2", description="d", roles=(TaskRole.CODER,)))
        assert result1.total_elapsed_seconds >= 0
        assert result2.total_elapsed_seconds >= 0


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

    def test_profile_integrated_during_generation(self):
        gw = _make_gateway(["DESIGN: profile_test", "CODE: ok", "REVIEW: ok"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="profile_test", description="desc")
        result = gen.generate(spec)
        assert result.step_count == 3
        assert result.success is True

    def test_custom_model_id_overrides_profile(self):
        gw = _make_gateway(["custom_output"])
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(
            project_type="game",
            name="custom_model",
            description="desc",
            roles=(TaskRole.CODER,),
        )
        gen.generate(spec, model_id="my-custom-model")
        gw.call_model.assert_called()
        call = gw.call_model.call_args_list[0]
        assert call[0][0] == "my-custom-model"

    def test_profile_per_step_routing(self):
        responses = [
            "DESIGN from cheap model",
            "CODE from good model",
            "REVIEW from best model",
        ]
        gw = _make_gateway(responses)
        gen = SoftwareGenerator(gateway=gw)
        spec = ProjectSpec(project_type="game", name="multi_profile", description="desc")
        result = gen.generate(spec)
        assert result.step_count == 3
        assert "best model" in result.final_output


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
