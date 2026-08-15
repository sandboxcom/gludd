"""Integration tests: SoftwareGenerator → MultiModelGamePipeline → ModelPipeline.

Verifies the full chain end-to-end without real API calls by mocking
ModelGateway.call_model() to return canned ModelResponse objects.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.model_pipeline import (
    ModelPipeline,
    PipelineResult,
    PipelineStep,
    StepResult,
)
from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline
from general_ludd.cloud.project_types import (
    ProjectType,
    _validate_rule,
    get_project_type,
    validate_project_against_rules,
)
from general_ludd.cloud.software_generator import ProjectSpec, SoftwareGenerator
from general_ludd.models.gateway import ModelResponse
from general_ludd.schemas.benchmark import TaskRole

_VALID_GAME_CODE = (
    '"""Synthetic game for testing."""\n'
    'print("hello world")\n'
    "import sys\n"
    'def main(): print("ok")\n'
    'if __name__ == "__main__": main()\n'
)


def _make_response(content: str, *, cost: float = 0.001) -> ModelResponse:
    return ModelResponse(
        content=content,
        usage_metadata={"input_tokens": 50, "output_tokens": 100},
        cost_estimate=cost,
        model_name="test-model",
    )


def _make_plan_response() -> ModelResponse:
    return _make_response(
        "name:TestGame\n"
        "genre:arcade\n"
        "architecture:simple main loop\n"
        "components:renderer,input_handler,score\n"
        "tech:pygame,python\n"
        "acceptance:runs 30 frames,handles input,displays score\n"
    )


def _make_code_response() -> ModelResponse:
    return _make_response(
        '"""Test Game main module."""\n'
        "import pygame\n"
        "pygame.init()\n"
        "screen = pygame.display.set_mode((800, 600))\n"
        "clock = pygame.time.Clock()\n"
        "running = True\n"
        "frame = 0\n"
        "while running:\n"
        "    for event in pygame.event.get():\n"
        "        if event.type == pygame.QUIT:\n"
        "            running = False\n"
        "    pygame.display.flip()\n"
        "    clock.tick(60)\n"
        "    frame += 1\n"
        "    if frame >= 30: running = False\n"
        "pygame.quit()\n",
        cost=0.002,
    )


def _make_review_pass_response() -> ModelResponse:
    return _make_response("issues:\nfixes:\nscore:0.95\npassed:true\n")


def _make_cli_code_response() -> ModelResponse:
    return _make_response(
        '"""CLI tool for testing."""\n'
        "import argparse\n"
        "import sys\n"
        "\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--version", action="version", version="1.0")\n'
        "    args = parser.parse_args()\n"
        "\n"
        'if __name__ == "__main__": main()\n',
    )


# ── Full chain: SoftwareGenerator.generate_multi (game) ─────────────────────


class TestSoftwareGeneratorGamePipeline:
    def test_generate_game_passes_review_first_round(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_plan_response(),
            _make_code_response(),
            _make_review_pass_response(),
        ]
        sg = SoftwareGenerator(gateway)

        spec = ProjectSpec(
            name="test-game",
            project_type="game",
            description="write a test game",
            prompt_template="",
        )

        code = sg.generate_multi(
            spec,
            model_profiles={
                TaskRole.PLANNER: "planner-profile",
                TaskRole.CODER: "coder-profile",
                TaskRole.REVIEWER: "reviewer-profile",
            },
        )

        assert "import pygame" in code
        assert "pygame.init" in code
        assert gateway.call_model.call_count == 3

    def test_generate_game_review_loop_then_pass(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_plan_response(),
            _make_code_response(),
            _make_review_pass_response(),
            _make_review_pass_response(),
        ]
        sg = SoftwareGenerator(gateway)

        spec = ProjectSpec(
            name="review-loop-game",
            project_type="game",
            description="build a game with review loop",
            prompt_template="",
        )

        code = sg.generate_multi(spec, model_profiles={})
        assert "import pygame" in code

    def test_generate_game_review_never_passes(self):
        gateway = MagicMock()
        failing_review = _make_response("issues:broken\nfixes:redo\nscore:0.2\npassed:false\n")
        gateway.call_model.side_effect = [
            _make_plan_response(),
            _make_code_response(),
            failing_review,
            _make_code_response(),
            failing_review,
            _make_code_response(),
            failing_review,
            _make_code_response(),
            failing_review,
        ]
        sg = SoftwareGenerator(gateway)

        with pytest.raises(RuntimeError, match="failed after 3 review rounds"):
            sg.generate_multi(
                ProjectSpec("doomed", "game", "a broken game", ""),
                model_profiles={},
            )


# ── SoftwareGenerator.generate (single-call cli_tool) ──────────────────────


class TestSoftwareGeneratorCliTool:
    def test_generate_cli_tool_single_call(self):
        gateway = MagicMock()
        gateway.call_model.return_value = _make_cli_code_response()
        sg = SoftwareGenerator(gateway)

        spec = ProjectSpec(
            name="hello-cli",
            project_type="cli_tool",
            description="a hello world cli tool",
            prompt_template="Write a CLI tool with argparse",
        )

        code = sg.generate(spec, model_id="test-model")
        assert "argparse" in code
        assert "def main" in code
        assert gateway.call_model.call_count == 1

    def test_generate_raises_when_gateway_is_none(self):
        sg = SoftwareGenerator(None)
        with pytest.raises(ValueError, match="not configured"):
            sg.generate(ProjectSpec("bad", "cli_tool", "", ""))


# ── Code validation against project type rules ────────────────────────────


class TestCodeValidationAgainstProjectType:
    def test_game_code_passes_validation(self):
        type_def = get_project_type("game")
        assert validate_project_against_rules(_VALID_GAME_CODE, type_def) is True

    def test_syntax_error_code_fails_validation(self):
        type_def = get_project_type("game")
        assert validate_project_against_rules("def broken(", type_def) is False

    def test_cli_code_with_argparse_passes(self):
        code = "import argparse\ndef main(): pass"
        type_def = get_project_type("cli_tool")
        result = validate_project_against_rules(code, type_def)
        assert result is True

    def test_empty_code_validates_ok(self):
        type_def = get_project_type("game")
        assert validate_project_against_rules("", type_def) is True

    def test_unknown_rule_passes_silently(self):
        type_def = ProjectType(
            type_id="test",
            display_name="Test",
            default_entry_point="t.py",
        )
        assert _validate_rule("x=1", "nonexistent_rule", type_def) is True


# ── Error propagation through pipeline ─────────────────────────────────────


class TestErrorPropagation:
    def test_plan_error_bubbles_up(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = RuntimeError("api connection lost")
        pipeline = MultiModelGamePipeline(gateway)

        with pytest.raises(RuntimeError, match="api connection lost"):
            pipeline.generate("build a game")

    def test_code_error_bubbles_up(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_plan_response(),
            RuntimeError("coder model crashed"),
        ]
        pipeline = MultiModelGamePipeline(gateway)

        with pytest.raises(RuntimeError, match="coder model crashed"):
            pipeline.generate("build a game")


# ── ModelPipeline generic step pipeline ─────────────────────────────────────


class TestModelPipelineIntegration:
    def test_two_step_pipeline(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_response("PLAN: architecture done", cost=0.001),
            _make_response("CODE: implementation done", cost=0.002),
        ]

        pipeline = ModelPipeline(
            gateway=gateway,
            model_id="test-model",
            steps=[
                PipelineStep(TaskRole.PLANNER, "Plan this: {context}"),
                PipelineStep(TaskRole.CODER, "Code from plan: {context}", "You are a coder"),
            ],
        )

        result = pipeline.run(initial_context="build a calculator")
        assert result.success is True
        assert result.step_count == 2
        assert result.total_cost_usd == 0.003
        assert result.total_input_tokens == 100
        assert result.total_output_tokens == 200
        assert result.final_output == "CODE: implementation done"

    def test_step_failure_stops_pipeline(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_response("PLAN: done"),
            RuntimeError("connection refused"),
        ]

        pipeline = ModelPipeline(
            gateway=gateway,
            model_id="test-model",
            steps=[
                PipelineStep(TaskRole.PLANNER, "Plan: {context}"),
                PipelineStep(TaskRole.CODER, "Code: {context}"),
            ],
        )

        result = pipeline.run(initial_context="input")
        assert result.success is False
        assert result.step_count == 2
        assert result.step_results[0].success is True
        assert result.step_results[1].success is False
        assert "connection refused" in result.step_results[1].error

    def test_pipeline_requires_steps(self):
        with pytest.raises(ValueError, match="At least one step"):
            ModelPipeline(gateway=MagicMock(), model_id="test", steps=[])

    def test_pipeline_requires_gateway(self):
        with pytest.raises(ValueError, match="ModelGateway is required"):
            ModelPipeline(gateway=None, model_id="test", steps=[PipelineStep(TaskRole.CODER, "{context}")])  # type: ignore[arg-type]


# ── Metrics collection across pipeline ──────────────────────────────────────


class TestMetricsCollection:
    def test_step_result_tracks_tokens_and_cost(self):
        result = StepResult(
            role=TaskRole.CODER,
            output="code",
            input_tokens=320,
            output_tokens=180,
            cost_usd=0.0045,
            elapsed_seconds=1.234,
            success=True,
        )
        assert result.input_tokens == 320
        assert result.output_tokens == 180
        assert result.cost_usd == 0.0045
        assert result.elapsed_seconds == 1.234

    def test_pipeline_result_aggregates_metrics(self):
        sr1 = StepResult(TaskRole.PLANNER, "plan", success=True, input_tokens=50, output_tokens=75, cost_usd=0.001)
        sr2 = StepResult(TaskRole.CODER, "code", success=True, input_tokens=100, output_tokens=200, cost_usd=0.003)
        sr3 = StepResult(TaskRole.REVIEWER, "review", success=True, input_tokens=30, output_tokens=40, cost_usd=0.0005)

        result = PipelineResult(
            final_output="review",
            step_results=(sr1, sr2, sr3),
        )
        assert result.success is True
        assert result.total_cost_usd == pytest.approx(0.0045)
        assert result.total_input_tokens == 180
        assert result.total_output_tokens == 315
        assert result.step_count == 3

    def test_partial_failure_aggregates_correctly(self):
        sr1 = StepResult(TaskRole.PLANNER, "plan", success=True, cost_usd=0.001)
        sr2 = StepResult(TaskRole.CODER, "", success=False, error="timeout")

        result = PipelineResult(
            final_output="",
            step_results=(sr1, sr2),
        )
        assert result.success is False
        assert result.total_cost_usd == 0.001

    def test_game_pipeline_tracks_response_metadata(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = [
            _make_response(
                "name:MetaGame\ngenre:test\narchitecture:simple\ncomponents:a\n\nacceptance:runs\n",
                cost=0.001,
            ),
            _make_response(_VALID_GAME_CODE, cost=0.002),
            _make_response("issues:\nfixes:\nscore:0.99\npassed:true\n", cost=0.0005),
        ]

        pipeline = MultiModelGamePipeline(gateway)

        spec = pipeline.plan("build a test game")
        assert spec.name == "MetaGame"
        assert spec.genre == "test"
        assert spec.component_list == ("a",)
        assert spec.acceptance_criteria == ("runs",)

        code = pipeline.code(spec)
        assert 'print("hello world")' in code

        review = pipeline.review(code, spec)
        assert review.passed is True
        assert review.quality_score == 0.99

        assert gateway.call_model.call_count == 3
