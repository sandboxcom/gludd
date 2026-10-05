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

    def test_stage_token_budgets_are_forwarded_to_provider(self):
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:tiny\ngenre:arcade"),
            ModelResponse(content="print('game')"),
            ModelResponse(content="issues:\nfixes:\nscore:1.0\npassed:true"),
        ]
        pipeline = MultiModelGamePipeline(
            gateway,
            planner_max_tokens=96,
            coder_max_tokens=768,
            reviewer_max_tokens=64,
        )

        assert pipeline.generate("Make a tiny game", max_review_rounds=0) == "print('game')"
        assert [call.kwargs["max_tokens"] for call in gateway.call_model.call_args_list] == [
            96,
            768,
            64,
        ]
        assert [
            call.kwargs["requested_max_output_tokens"] for call in gateway.call_model.call_args_list
        ] == [96, 768, 64]

    def test_stage_token_budgets_reject_non_positive_values(self):
        gateway = self._mock_gateway("ok")

        with pytest.raises(ValueError, match="planner_max_tokens must be a positive int"):
            MultiModelGamePipeline(gateway, planner_max_tokens=0)

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

    def test_plan_accepts_small_model_markdown_fields(self):
        gateway = self._mock_gateway(
            "**Name:** snake\n- **Genre:** arcade\n"
            "**Architecture:** headless state machine\n"
            "- **Components:** snake, food\n"
            "**Tech:** stdlib\n- **Acceptance:** importable, lifecycle"
        )
        spec = MultiModelGamePipeline(gateway).plan("Build headless snake")

        assert spec.name == "snake"
        assert spec.genre == "arcade"
        assert spec.component_list == ("snake", "food")
        assert spec.tech_stack == ("stdlib",)

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

    def test_code_normalizes_unclosed_python_fence(self):
        gateway = self._mock_gateway("Here is the module:\n```python\nclass Snake:\n    pass\n")
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(name="snake", genre="arcade", description="headless")

        assert pipeline.code(spec) == "class Snake:\n    pass"

    def test_headless_spec_is_not_overridden_by_pygame_prompt(self):
        gateway = self._mock_gateway("class Snake:\n    pass")
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(
            name="snake",
            genre="arcade",
            description="NO external deps; implement a headless state machine",
            tech_stack=("stdlib",),
        )

        pipeline.code(spec)
        system_prompt = gateway.call_model.call_args.kwargs["messages"][0]["content"]
        assert "pygame" not in system_prompt.lower()
        assert "explicitly named" in system_prompt.lower()
        assert "lifecycle" in system_prompt.lower()

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

    def test_review_accepts_small_model_markdown_fields(self):
        gateway = self._mock_gateway(
            "- **Issues:**\n- **Fixes:** none\n- **Score:** 0.91\n- **Passed:** TRUE"
        )
        pipeline = MultiModelGamePipeline(gateway)
        spec = DesignSpec(name="snake", genre="arcade", description="headless")

        result = pipeline.review("class Snake:\n    pass", spec)

        assert result.passed
        assert result.quality_score == 0.91

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

    def test_generate_accepts_objectively_valid_reviewer_false_negative(self):
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:snake\ngenre:arcade"),
            ModelResponse(content="class Snake:\n    pass"),
            ModelResponse(content="issues:style\nfixes:style\nscore:0.5\npassed:false"),
        ]
        validator = MagicMock(return_value=True)
        pipeline = MultiModelGamePipeline(gateway)

        code = pipeline.generate(
            "Make snake",
            max_review_rounds=1,
            candidate_validator=validator,
        )

        assert code == "class Snake:\n    pass"
        validator.assert_called_once_with(code)
        assert gateway.call_model.call_count == 3

    def test_generate_normalizes_each_model_candidate_before_review_and_validation(self):
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:snake\ngenre:arcade"),
            ModelResponse(content="class Snake:\n    pass"),
            ModelResponse(content="issues:\nfixes:\nscore:1.0\npassed:true"),
        ]
        normalized = "class Snake:\n    def start(self):\n        self.state = 'playing'"
        normalizer = MagicMock(return_value=normalized)
        validator = MagicMock(return_value=True)

        code = MultiModelGamePipeline(gateway).generate(
            "Make snake",
            max_review_rounds=1,
            candidate_normalizer=normalizer,
            candidate_validator=validator,
        )

        assert code == normalized
        normalizer.assert_called_once_with("class Snake:\n    pass")
        validator.assert_called_once_with(normalized)
        review_messages = gateway.call_model.call_args_list[2].kwargs["messages"]
        assert review_messages[-1]["content"] == normalized

    def test_generate_rejects_an_empty_normalized_candidate(self):
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:snake\ngenre:arcade"),
            ModelResponse(content="class Snake:\n    pass"),
        ]

        with pytest.raises(ValueError, match="candidate_normalizer must return non-empty Python"):
            MultiModelGamePipeline(gateway).generate(
                "Make snake",
                candidate_normalizer=lambda _code: "",
            )

    def test_generate_uses_deterministic_failure_reason_for_repair(self):
        review_fail = "issues:style\nfixes:style\nscore:0.5\npassed:false"
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:snake\ngenre:arcade"),
            ModelResponse(content="class Snake:\n    pass"),
            ModelResponse(content=review_fail),
            ModelResponse(content="class Snake:\n    def start(self): pass\n    def restart(self): pass"),
            ModelResponse(content=review_fail),
        ]
        validator = MagicMock(side_effect=["Missing methods: start, restart", True])
        pipeline = MultiModelGamePipeline(gateway)

        code = pipeline.generate(
            "Make snake",
            max_review_rounds=1,
            candidate_validator=validator,
        )

        assert "def restart" in code
        assert gateway.call_model.call_count == 4
        repair_messages = gateway.call_model.call_args_list[3].kwargs["messages"]
        assert repair_messages[-1]["content"] == "Missing methods: start, restart"
        repair_system = repair_messages[0]["content"].lower()
        assert "complete module" in repair_system
        assert "indented body" in repair_system
        assert "partial patch" in repair_system

    def test_deterministic_validator_cannot_be_bypassed_by_reviewer_pass(self):
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content="name:snake\ngenre:arcade"),
            ModelResponse(content="class Snake:\n    pass"),
            ModelResponse(content="issues:\nfixes:\nscore:1.0\npassed:true"),
            ModelResponse(content="class Snake:\n    def start(self): pass"),
        ]
        validator = MagicMock(side_effect=["Missing methods: start", True])
        pipeline = MultiModelGamePipeline(gateway)

        code = pipeline.generate(
            "Make snake",
            max_review_rounds=1,
            candidate_validator=validator,
        )

        assert "def start" in code
        assert gateway.call_model.call_count == 4

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
