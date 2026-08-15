"""Tests for MultiModelGamePipeline — PLANNER → CODER → REVIEWER pipeline."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.multi_model_game_pipeline import (
    DesignSpec,
    MultiModelGamePipeline,
    ReviewResult,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse


class TestDesignSpec:
    def test_default_construction(self):
        spec = DesignSpec(
            name="test-game",
            genre="platformer",
            description="A test game",
        )
        assert spec.name == "test-game"
        assert spec.genre == "platformer"
        assert spec.description == "A test game"
        assert spec.architecture_plan == ""
        assert spec.component_list == ()
        assert spec.tech_stack == ()
        assert spec.acceptance_criteria == ()

    def test_full_construction(self):
        spec = DesignSpec(
            name="full-game",
            genre="rpg",
            description="An RPG game",
            architecture_plan="Use ECS pattern",
            component_list=("player", "enemy", "world"),
            tech_stack=("pygame", "numpy"),
            acceptance_criteria=("runs 60fps", "has save/load"),
        )
        assert spec.architecture_plan == "Use ECS pattern"
        assert spec.component_list == ("player", "enemy", "world")
        assert spec.tech_stack == ("pygame", "numpy")
        assert len(spec.acceptance_criteria) == 2

    def test_equality(self):
        a = DesignSpec(name="g", genre="p", description="d")
        b = DesignSpec(name="g", genre="p", description="d")
        assert a == b

    def test_inequality(self):
        a = DesignSpec(name="g1", genre="p", description="d")
        b = DesignSpec(name="g2", genre="p", description="d")
        assert a != b

    def test_to_prompt(self):
        spec = DesignSpec(
            name="prompt-game",
            genre="shooter",
            description="A shooter game",
            architecture_plan="raycasting engine",
            component_list=("renderer", "player", "enemy"),
            tech_stack=("pygame",),
            acceptance_criteria=("syntax_valid", "import_ok"),
        )
        prompt = spec.to_prompt()
        assert "prompt-game" in prompt
        assert "shooter" in prompt
        assert "raycasting engine" in prompt
        assert "renderer" in prompt
        assert "pygame" in prompt
        assert "syntax_valid" in prompt


class TestReviewResult:
    def test_default_construction(self):
        result = ReviewResult(code="print('hello')")
        assert result.code == "print('hello')"
        assert result.issues_found == ()
        assert result.fixes_applied == ()
        assert result.quality_score == 0.0
        assert not result.passed

    def test_passed_result(self):
        result = ReviewResult(
            code="import pygame\npygame.init()",
            issues_found=(),
            fixes_applied=("lint_formatting",),
            quality_score=0.95,
            passed=True,
        )
        assert result.passed
        assert result.quality_score == 0.95
        assert "lint_formatting" in result.fixes_applied

    def test_failed_result(self):
        result = ReviewResult(
            code="broken code",
            issues_found=("missing_import", "syntax_error"),
            fixes_applied=(),
            quality_score=0.2,
            passed=False,
        )
        assert not result.passed
        assert len(result.issues_found) == 2

    def test_to_feedback_prompt(self):
        result = ReviewResult(
            code="print('x')",
            issues_found=("missing_pygame_init", "no_game_loop"),
            fixes_applied=(),
            quality_score=0.3,
            passed=False,
        )
        prompt = result.to_feedback_prompt()
        assert "missing_pygame_init" in prompt
        assert "no_game_loop" in prompt


class TestMultiModelGamePipeline:
    @staticmethod
    def _mock_gateway(content: str) -> ModelGateway:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = ModelResponse(content=content)
        return gateway

    def test_init_defaults(self):
        gateway = self._mock_gateway("ok")
        pipeline = MultiModelGamePipeline(gateway)
        assert pipeline._gateway is gateway
        assert pipeline._task_policy is None

    def test_init_with_policy(self):
        gateway = self._mock_gateway("ok")
        policy = object()
        pipeline = MultiModelGamePipeline(gateway, task_policy=policy)
        assert pipeline._task_policy is policy

    def test_plan_calls_planner(self):
        plan_response = textwrap.dedent("""\
            name:arcade-shooter
            genre:shooter
            architecture:raycasting engine with sprite enemies
            components:renderer,player,enemy,projectile,hud
            tech:pygame
            acceptance:syntax_valid,import_ok,run_without_crash
        """)
        gateway = self._mock_gateway(plan_response)
        pipeline = MultiModelGamePipeline(gateway)
        spec = pipeline.plan("Make an arcade shooter", model_id="planner-model")

        assert spec.name == "arcade-shooter"
        assert spec.genre == "shooter"
        assert "renderer" in spec.component_list
        assert "pygame" in spec.tech_stack
        gateway.call_model.assert_called_once()
        posargs, kwargs = gateway.call_model.call_args
        assert posargs[0] == "planner-model"
        assert "PLANNER" in kwargs["messages"][0]["content"].upper()

    def test_code_calls_coder(self):
        gateway = self._mock_gateway("import pygame\npygame.init()\nprint('game')")
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(
            name="test",
            genre="action",
            description="An action game",
            architecture_plan="simple loop",
            component_list=("player",),
            tech_stack=("pygame",),
            acceptance_criteria=("syntax_valid",),
        )
        code = pipeline.code(spec, model_id="coder-model")
        assert "import pygame" in code
        gateway.call_model.assert_called_once()
        posargs, kwargs = gateway.call_model.call_args
        assert posargs[0] == "coder-model"
        assert "CODER" in kwargs["messages"][0]["content"].upper()

    def test_review_calls_reviewer(self):
        review_response = textwrap.dedent("""\
            issues:
            fixes:lint_formatting
            score:0.92
            passed:true
        """)
        gateway = self._mock_gateway(review_response)
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(
            name="test",
            genre="action",
            description="test",
            architecture_plan="simple",
            component_list=("player",),
            tech_stack=("pygame",),
            acceptance_criteria=("syntax_valid",),
        )
        code = "import pygame\npygame.init()"
        result = pipeline.review(code, spec, model_id="reviewer-model")
        assert result.quality_score == 0.92
        assert result.passed
        assert "lint_formatting" in result.fixes_applied
        gateway.call_model.assert_called_once()
        posargs, kwargs = gateway.call_model.call_args
        assert posargs[0] == "reviewer-model"
        assert "REVIEWER" in kwargs["messages"][0]["content"].upper()

    def test_generate_full_pipeline(self):
        plan_resp = textwrap.dedent("""\
            name:simple-game
            genre:arcade
            architecture:basic loop
            components:player
            tech:pygame
            acceptance:syntax_valid,import_ok
        """)
        code_resp = "import pygame\npygame.init()\nscreen = pygame.display.set_mode((800,600))\n"
        review_resp = textwrap.dedent("""\
            issues:
            fixes:
            score:0.95
            passed:true
        """)

        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=plan_resp),
            ModelResponse(content=code_resp),
            ModelResponse(content=review_resp),
        ]
        pipeline = MultiModelGamePipeline(gateway)
        final_code = pipeline.generate("Make a simple arcade game")

        assert "import pygame" in final_code
        assert gateway.call_model.call_count == 3

    def test_generate_with_review_feedback_loop(self):
        plan_resp = textwrap.dedent("""\
            name:buggy-game
            genre:arcade
            architecture:simple
            components:player
            tech:pygame
            acceptance:syntax_valid,import_ok,run_without_crash
        """)
        code_resp = "broken code"
        review_fail = textwrap.dedent("""\
            issues:missing_import,no_game_loop
            fixes:
            score:0.2
            passed:false
        """)
        review_pass = textwrap.dedent("""\
            issues:
            fixes:added_imports,added_loop
            score:0.85
            passed:true
        """)

        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=plan_resp),
            ModelResponse(content=code_resp),
            ModelResponse(content=review_fail),
            ModelResponse(content="import pygame\npygame.init()\nwhile True: pass"),
            ModelResponse(content=review_pass),
        ]
        pipeline = MultiModelGamePipeline(gateway)
        final_code = pipeline.generate(
            "Make a buggy game",
            max_review_rounds=2,
        )
        assert "import pygame" in final_code
        assert gateway.call_model.call_count == 5

    def test_generate_raises_on_max_review_rounds(self):
        plan_resp = textwrap.dedent("""\
            name:unfixable
            genre:arcade
            architecture:basic
            components:player
            tech:pygame
            acceptance:syntax_valid
        """)
        code_resp = "always broken"
        review_fail = textwrap.dedent("""\
            issues:unfixable_error
            fixes:
            score:0.1
            passed:false
        """)

        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=plan_resp),
            ModelResponse(content=code_resp),
            ModelResponse(content=review_fail),
            ModelResponse(content="still broken"),
            ModelResponse(content=review_fail),
        ]
        pipeline = MultiModelGamePipeline(gateway)
        with pytest.raises(RuntimeError, match="review rounds"):
            pipeline.generate("Make an unfixable game", max_review_rounds=1)

    def test_parse_design_spec_handles_minimal_response(self):
        gateway = self._mock_gateway("name:minimal\nother stuff")
        pipeline = MultiModelGamePipeline(gateway)
        spec = pipeline.plan("minimal")
        assert spec.name == "minimal"
        assert spec.genre == ""
        assert spec.component_list == ()

    def test_parse_review_result_handles_malformed(self):
        gateway = self._mock_gateway("garbage response with no structure")
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(
            name="x",
            genre="x",
            description="x",
            architecture_plan="x",
            component_list=("x",),
            tech_stack=("x",),
            acceptance_criteria=("x",),
        )
        result = pipeline.review("code", spec, model_id="m")
        assert not result.passed
        assert result.quality_score == 0.0
