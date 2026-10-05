"""Integration tests for MultiModelGamePipeline + GameGenerator.generate_game_multi().

Tests the full PLANNER → CODER → REVIEWER flow, single-model fallback,
error handling in each phase, review-fix iteration, and quality scoring.
"""

from __future__ import annotations

import textwrap
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.game_e2e import GameGenerator, GameSpec
from general_ludd.cloud.multi_model_game_pipeline import (
    DesignSpec,
    MultiModelGamePipeline,
)
from general_ludd.models.gateway import ModelGateway, ModelResponse
from general_ludd.routing_roles.small_model_policy import DispatchAction
from general_ludd.schemas.benchmark import TaskRole


class TestGenerateGameMultiIntegration:
    @staticmethod
    def _plan_response(name: str = "arcade-shooter") -> str:
        return textwrap.dedent(f"""\
            name:{name}
            genre:shooter
            architecture:raycasting engine
            components:renderer,player,enemy
            tech:pygame
            acceptance:syntax_valid,import_ok,run_without_crash
        """)

    @staticmethod
    def _code_response() -> str:
        return (
            "import pygame\n"
            "pygame.init()\n"
            "screen = pygame.display.set_mode((800,600))\n"
            "while True:\n"
            "    pygame.event.get()\n"
        )

    @staticmethod
    def _review_pass(score: float = 0.95) -> str:
        return textwrap.dedent(f"""\
            issues:
            fixes:
            score:{score}
            passed:true
        """)

    @staticmethod
    def _review_fail(score: float = 0.2) -> str:
        return textwrap.dedent(f"""\
            issues:missing_import,no_game_loop
            fixes:
            score:{score}
            passed:false
        """)

    # ── full planner→coder→reviewer flow ────────────────────────────────

    def test_full_pipeline_flow(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=self._code_response()),
            ModelResponse(content=self._review_pass()),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="full-flow",
            genre="shooter",
            description="A complete multi-model game",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a shooter game.",
        )
        model_profiles = {
            TaskRole.PLANNER: "planner-model",
            TaskRole.CODER: "coder-model",
            TaskRole.REVIEWER: "reviewer-model",
        }
        result = generator.generate_game_multi(spec, model_profiles)

        assert "import pygame" in result
        assert gateway.call_model.call_count == 3

        planner_call = gateway.call_model.call_args_list[0]
        assert planner_call[0][0] == "planner-model"
        coder_call = gateway.call_model.call_args_list[1]
        assert coder_call[0][0] == "coder-model"
        reviewer_call = gateway.call_model.call_args_list[2]
        assert reviewer_call[0][0] == "reviewer-model"

    # ── single-model fallback ────────────────────────────────────────────

    def test_single_model_fallback_all_default(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=self._code_response()),
            ModelResponse(content=self._review_pass()),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="fallback",
            genre="shooter",
            description="All models default",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        result = generator.generate_game_multi(spec, {})

        assert "import pygame" in result
        assert gateway.call_model.call_count == 3
        for call_args in gateway.call_model.call_args_list:
            assert call_args[0][0] == "default"

    def test_single_model_fallback_partial_profiles(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=self._code_response()),
            ModelResponse(content=self._review_pass()),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="partial",
            genre="shooter",
            description="Only planner is explicit",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        result = generator.generate_game_multi(
            spec,
            {TaskRole.PLANNER: "custom-planner"},
        )

        assert "import pygame" in result
        planner_call = gateway.call_model.call_args_list[0]
        assert planner_call[0][0] == "custom-planner"
        coder_call = gateway.call_model.call_args_list[1]
        assert coder_call[0][0] == "default"
        reviewer_call = gateway.call_model.call_args_list[2]
        assert reviewer_call[0][0] == "default"

    # ── error in planner phase ──────────────────────────────────────────

    def test_error_in_planner_phase(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = RuntimeError("Planner model unavailable")
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="planner-err",
            genre="shooter",
            description="Planner fails",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        with pytest.raises(RuntimeError, match="Planner model unavailable"):
            generator.generate_game_multi(spec, {TaskRole.PLANNER: "bad-model"})

    def test_error_in_planner_gateway_none(self) -> None:
        generator = GameGenerator(gateway=None)
        spec = GameSpec(
            name="no-gw",
            genre="shooter",
            description="No gateway configured",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        with pytest.raises(ValueError, match="ModelGateway is not configured"):
            generator.generate_game_multi(spec, {})

    # ── error in coder phase ────────────────────────────────────────────

    def test_error_in_coder_phase(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            RuntimeError("Coder model timeout"),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="coder-err",
            genre="shooter",
            description="Coder fails",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        with pytest.raises(RuntimeError, match="Coder model timeout"):
            generator.generate_game_multi(spec, {TaskRole.CODER: "bad-coder"})

    # ── error in reviewer phase ─────────────────────────────────────────

    def test_error_in_reviewer_phase(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=self._code_response()),
            RuntimeError("Reviewer model unreachable"),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="reviewer-err",
            genre="shooter",
            description="Reviewer fails",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        with pytest.raises(RuntimeError, match="Reviewer model unreachable"):
            generator.generate_game_multi(spec, {TaskRole.REVIEWER: "bad-reviewer"})

    # ── review-fix iteration loop (up to 3 rounds) ──────────────────────

    def test_review_fix_iteration_loop_three_rounds(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),  # plan
            ModelResponse(content="broken code"),  # code v1
            ModelResponse(content=self._review_fail(0.1)),  # review v1 → fail
            ModelResponse(content="still broken"),  # code v2 (fix)
            ModelResponse(content=self._review_fail(0.25)),  # review v2 → fail
            ModelResponse(content="getting better"),  # code v3 (fix)
            ModelResponse(content=self._review_fail(0.4)),  # review v3 → fail
            ModelResponse(content="working code"),  # code v4 (fix)
            ModelResponse(content=self._review_pass(0.9)),  # review v4 → pass
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="iterative",
            genre="shooter",
            description="Iterative refinement",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        result = generator.generate_game_multi(spec, {})

        assert "working code" in result
        assert gateway.call_model.call_count == 9  # plan + 4x(code+review)

    def test_review_fix_exhausts_max_rounds(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),  # plan
            ModelResponse(content="unfixable code"),  # code v1
            ModelResponse(content=self._review_fail(0.0)),  # review v1 → fail
            ModelResponse(content="still unfixable"),  # code v2 (fix)
            ModelResponse(content=self._review_fail(0.0)),  # review v2 → fail
            ModelResponse(content="nope"),  # code v3 (fix)
            ModelResponse(content=self._review_fail(0.0)),  # review v3 → fail
            ModelResponse(content="last try"),  # code v4 (fix)
            ModelResponse(content=self._review_fail(0.0)),  # final review → fail
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="unfixable",
            genre="shooter",
            description="Cannot be fixed",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        with pytest.raises(RuntimeError, match="review rounds"):
            generator.generate_game_multi(spec, {})
        assert gateway.call_model.call_count == 9  # plan + 4x(code+review)

    # ── scoring with different quality levels ───────────────────────────

    def test_scoring_high_quality_pass(self) -> None:
        pipeline = MultiModelGamePipeline(cast(ModelGateway, MagicMock(spec=ModelGateway)))
        code = self._code_response()
        spec = DesignSpec(
            name="highq",
            genre="shooter",
            description="High quality game",
            architecture_plan="raycasting",
            component_list=("renderer", "player"),
            tech_stack=("pygame",),
            acceptance_criteria=("syntax_valid",),
        )
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = ModelResponse(content=self._review_pass(score=0.99))
        pipeline._gateway = gateway

        result = pipeline.review(code, spec, model_id="r")
        assert result.passed
        assert result.quality_score == 0.99
        assert result.issues_found == ()

    def test_scoring_borderline_fail(self) -> None:
        pipeline = MultiModelGamePipeline(cast(ModelGateway, MagicMock(spec=ModelGateway)))
        code = "barely working code"
        spec = DesignSpec(
            name="borderline",
            genre="shooter",
            description="Borderline quality",
            architecture_plan="simple",
            component_list=("player",),
            tech_stack=("pygame",),
            acceptance_criteria=("syntax_valid",),
        )
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = ModelResponse(content=self._review_fail(score=0.49))
        pipeline._gateway = gateway

        result = pipeline.review(code, spec, model_id="r")
        assert not result.passed
        assert result.quality_score == 0.49
        assert len(result.issues_found) == 2

    def test_scoring_zero_quality(self) -> None:
        pipeline = MultiModelGamePipeline(cast(ModelGateway, MagicMock(spec=ModelGateway)))
        code = "not even python"
        spec = DesignSpec(
            name="zeroq",
            genre="shooter",
            description="Zero quality output",
            architecture_plan="none",
            component_list=(),
            tech_stack=(),
            acceptance_criteria=(),
        )
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = ModelResponse(
            content=textwrap.dedent("""\
                issues:syntax_error,no_imports,no_init,no_loop,empty_file
                fixes:rewrite_from_scratch
                score:0.0
                passed:false
            """)
        )
        pipeline._gateway = gateway

        result = pipeline.review(code, spec, model_id="r")
        assert not result.passed
        assert result.quality_score == 0.0
        assert len(result.issues_found) >= 2

    def test_scoring_malformed_score_clamped(self) -> None:
        pipeline = MultiModelGamePipeline(cast(ModelGateway, MagicMock(spec=ModelGateway)))
        spec = DesignSpec(
            name="clamp",
            genre="shooter",
            description="Clamp test",
            architecture_plan="",
            component_list=(),
            tech_stack=(),
            acceptance_criteria=(),
        )
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.return_value = ModelResponse(content="issues:\nfixes:\nscore:not-a-number\npassed:true")
        pipeline._gateway = gateway

        result = pipeline.review("code", spec, model_id="r")
        assert result.quality_score == 0.0

    # ── integration: generate_game_multi with authorization ─────────────

    def test_generate_game_multi_with_authorize_passes(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=self._code_response()),
            ModelResponse(content=self._review_pass()),
        ]
        policy = MagicMock()
        decision = MagicMock()
        decision.action = DispatchAction.LOCAL
        policy.authorize.return_value = decision

        generator = GameGenerator(
            gateway=cast(ModelGateway, gateway),
            task_policy=policy,
        )
        spec = GameSpec(
            name="authorized",
            genre="shooter",
            description="Authorized dispatch",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        identity = SimpleNamespace(model_id="local-model", provider="openai")
        result = generator.generate_game_multi(
            spec,
            {},
            model_identity=identity,
            evidence=(),
        )
        assert "import pygame" in result
        policy.authorize.assert_called_once()
        assert gateway.call_model.call_count == 3

    def test_generate_game_multi_authorize_denies(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        policy = MagicMock()
        decision = MagicMock()
        decision.action = DispatchAction.ESCALATE
        decision.reason = "Not enough capability evidence"
        policy.authorize.return_value = decision

        generator = GameGenerator(
            gateway=cast(ModelGateway, gateway),
            task_policy=policy,
        )
        spec = GameSpec(
            name="denied",
            genre="shooter",
            description="Denied dispatch",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        identity = SimpleNamespace(model_id="tiny-model", provider="local")
        with pytest.raises(PermissionError, match="Not enough capability evidence"):
            generator.generate_game_multi(
                spec,
                {},
                model_identity=identity,
                evidence=(),
            )
        gateway.call_model.assert_not_called()

    # ── integration: generated code is normalized ───────────────────────

    def test_generated_code_normalized(self) -> None:
        gateway = MagicMock(spec=ModelGateway)
        code = "  import pygame  \n\npygame.init()\n"
        gateway.call_model.side_effect = [
            ModelResponse(content=self._plan_response()),
            ModelResponse(content=code),
            ModelResponse(content=self._review_pass()),
        ]
        generator = GameGenerator(gateway=cast(ModelGateway, gateway))
        spec = GameSpec(
            name="normalize",
            genre="shooter",
            description="Normalization test",
            expected_frames=30,
            similarity_threshold=0.35,
            prompt_template="Write a game.",
        )
        result = generator.generate_game_multi(spec, {})
        assert "import pygame" in result
