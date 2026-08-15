"""Tests for ModelPipeline — generic multi-step LLM workflow.

Covers: PipelineStep, StepResult, PipelineResult, ModelPipeline.run(),
all TaskRole values, context passing, error handling, frozen dataclass,
_safe_int, multiple {context} substitutions, edge cases.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.model_pipeline import (
    ModelPipeline,
    PipelineResult,
    PipelineStep,
    StepResult,
    _safe_int,
)
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
        gw.call_model.return_value = _make_response("default")
    return gw


def _make_failing_gateway(error_msg: str = "gateway error") -> ModelGateway:
    gw = MagicMock(spec=ModelGateway)
    gw.call_model.side_effect = RuntimeError(error_msg)
    return gw


class TestPipelineStep:
    def test_construction(self):
        step = PipelineStep(role=TaskRole.CODER, prompt_template="Write code: {context}")
        assert step.role == TaskRole.CODER
        assert step.prompt_template == "Write code: {context}"
        assert step.system_prompt == ""

    def test_with_system_prompt(self):
        step = PipelineStep(role=TaskRole.PLANNER, prompt_template="Plan", system_prompt="You are a planner")
        assert step.system_prompt == "You are a planner"

    def test_default_role(self):
        step = PipelineStep(role=TaskRole.CODER, prompt_template="Code it")
        assert step.role == TaskRole.CODER

    def test_equality(self):
        a = PipelineStep(role=TaskRole.CODER, prompt_template="a")
        b = PipelineStep(role=TaskRole.CODER, prompt_template="a")
        assert a == b

    def test_inequality_different_role(self):
        a = PipelineStep(role=TaskRole.CODER, prompt_template="a")
        b = PipelineStep(role=TaskRole.PLANNER, prompt_template="a")
        assert a != b

    def test_inequality_different_template(self):
        a = PipelineStep(role=TaskRole.CODER, prompt_template="a")
        b = PipelineStep(role=TaskRole.CODER, prompt_template="b")
        assert a != b


class TestStepResult:
    def test_default_construction(self):
        result = StepResult(role=TaskRole.CODER)
        assert result.role == TaskRole.CODER
        assert result.output == ""
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cost_usd == 0.0
        assert result.elapsed_seconds == 0.0
        assert result.success is False
        assert result.error == ""

    def test_success_result(self):
        result = StepResult(
            role=TaskRole.PLANNER,
            output="A plan",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.004,
            elapsed_seconds=1.2,
            success=True,
        )
        assert result.output == "A plan"
        assert result.input_tokens == 200
        assert result.output_tokens == 100
        assert result.cost_usd == 0.004
        assert result.elapsed_seconds == 1.2
        assert result.success is True

    def test_error_result(self):
        result = StepResult(
            role=TaskRole.CODER,
            output="",
            input_tokens=50,
            output_tokens=0,
            cost_usd=0.001,
            elapsed_seconds=0.5,
            success=False,
            error="Budget exceeded",
        )
        assert result.error == "Budget exceeded"
        assert result.success is False


class TestPipelineResult:
    def test_empty_result(self):
        result = PipelineResult()
        assert result.final_output == ""
        assert result.step_results == ()
        assert result.total_cost_usd == 0.0
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_elapsed_seconds == 0.0
        assert result.success is False

    def test_aggregate_metrics(self):
        s1 = StepResult(
            role=TaskRole.PLANNER,
            output="p",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            elapsed_seconds=0.5,
            success=True,
        )
        s2 = StepResult(
            role=TaskRole.CODER,
            output="c",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.003,
            elapsed_seconds=1.0,
            success=True,
        )
        result = PipelineResult(final_output="c", step_results=(s1, s2))
        assert result.total_cost_usd == 0.004
        assert result.total_input_tokens == 300
        assert result.total_output_tokens == 150
        assert result.total_elapsed_seconds == 1.5
        assert result.success is True
        assert result.step_count == 2

    def test_failure_propagates(self):
        s1 = StepResult(
            role=TaskRole.PLANNER,
            output="p",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
            elapsed_seconds=0.5,
            success=True,
        )
        s2 = StepResult(
            role=TaskRole.CODER,
            output="",
            input_tokens=200,
            output_tokens=0,
            cost_usd=0.003,
            elapsed_seconds=1.0,
            success=False,
            error="fail",
        )
        result = PipelineResult(final_output="", step_results=(s1, s2))
        assert result.success is False

    def test_step_count_empty(self):
        result = PipelineResult()
        assert result.step_count == 0


class TestModelPipeline:
    def test_construction(self):
        gw = _make_gateway()
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="deepseek-v3",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="Plan: {context}"),
                PipelineStep(role=TaskRole.CODER, prompt_template="Code: {context}"),
            ],
        )
        assert len(pipeline._steps) == 2
        assert pipeline._gateway is gw

    def test_no_gateway_raises(self):
        with pytest.raises(ValueError, match="ModelGateway is required"):
            ModelPipeline(gateway=None, model_id="test", steps=[])

    def test_empty_steps_raises(self):
        gw = _make_gateway()
        with pytest.raises(ValueError, match="At least one step"):
            ModelPipeline(gateway=gw, model_id="test", steps=[])

    def test_single_step_pipeline(self):
        gw = _make_gateway(["Hello, world"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="Say hello")],
        )
        result = pipeline.run(initial_context="")

        assert result.final_output == "Hello, world"
        assert result.step_count == 1
        assert result.success is True
        step = result.step_results[0]
        assert step.role == TaskRole.CODER
        assert step.output == "Hello, world"
        assert step.success is True

    def test_context_passes_between_steps(self):
        gw = _make_gateway(["Plan: do X", "Code implementing: Plan: do X"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="Create a plan"),
                PipelineStep(role=TaskRole.CODER, prompt_template="Implement this: {context}"),
            ],
        )
        result = pipeline.run(initial_context="")

        assert result.final_output == "Code implementing: Plan: do X"
        assert result.step_count == 2
        second_call = gw.call_model.call_args_list[1]
        messages = second_call[0][1]
        assert any("Plan: do X" in m["content"] for m in messages)

    def test_initial_context(self):
        gw = _make_gateway(["Response with spec: game spec v1"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="Write code for: {context}")],
        )
        result = pipeline.run(initial_context="game spec v1")

        assert result.final_output == "Response with spec: game spec v1"
        first_call = gw.call_model.call_args_list[0]
        messages = first_call[0][1]
        assert any("game spec v1" in m["content"] for m in messages)

    def test_system_prompt(self):
        gw = _make_gateway(["Planned"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[
                PipelineStep(
                    role=TaskRole.PLANNER,
                    prompt_template="Plan something",
                    system_prompt="You are a game planner",
                )
            ],
        )
        pipeline.run(initial_context="")

        call = gw.call_model.call_args_list[0]
        messages = call[0][1]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a game planner"

    def test_no_system_prompt_when_empty(self):
        gw = _make_gateway(["Output"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="Do it")],
        )
        pipeline.run(initial_context="")

        call = gw.call_model.call_args_list[0]
        messages = call[0][1]
        assert messages[0]["role"] != "system"

    def test_metrics_aggregated(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.side_effect = [
            ModelResponse(
                content="step1",
                usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                cost_estimate=0.002,
                model_name="m1",
            ),
            ModelResponse(
                content="step2",
                usage_metadata={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
                cost_estimate=0.004,
                model_name="m2",
            ),
        ]
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="step1"),
                PipelineStep(role=TaskRole.CODER, prompt_template="step2"),
            ],
        )
        result = pipeline.run(initial_context="")

        assert result.total_input_tokens == 300
        assert result.total_output_tokens == 130
        assert result.total_cost_usd == 0.006
        assert result.step_count == 2

    def test_step_failure_stops_pipeline(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.side_effect = RuntimeError("Model unavailable")

        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="step1"),
                PipelineStep(role=TaskRole.CODER, prompt_template="step2"),
            ],
        )
        result = pipeline.run(initial_context="")

        assert result.success is False
        assert result.step_count == 2
        assert result.step_results[0].success is False
        assert "Model unavailable" in result.step_results[0].error
        assert result.step_results[1].success is False
        assert result.final_output == ""

    def test_elapsed_time_tracked(self):
        gw = _make_gateway(["quick"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="quick")],
        )
        result = pipeline.run(initial_context="")

        assert result.step_results[0].elapsed_seconds >= 0
        assert result.total_elapsed_seconds >= 0

    def test_full_planner_coder_reviewer_pipeline(self):
        responses = [
            "DESIGN: 2D platformer with parallax scrolling",
            "CODE: import pygame\n...game code...",
            "REVIEWED CODE: import pygame\n...fixed game code...",
        ]
        gw = _make_gateway(responses)
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[
                PipelineStep(
                    role=TaskRole.PLANNER,
                    prompt_template="Design a game: {context}",
                    system_prompt="You are a game designer",
                ),
                PipelineStep(
                    role=TaskRole.CODER,
                    prompt_template="Write code for: {context}",
                    system_prompt="You are a game coder",
                ),
                PipelineStep(
                    role=TaskRole.REVIEWER,
                    prompt_template="Review: {context}",
                    system_prompt="You are a code reviewer",
                ),
            ],
        )
        result = pipeline.run(initial_context="platformer game")

        assert result.success is True
        assert result.step_count == 3
        assert result.final_output == "REVIEWED CODE: import pygame\n...fixed game code..."
        assert result.total_elapsed_seconds >= 0
        assert all(s.success for s in result.step_results)

    def test_context_accumulates_across_steps(self):
        responses = [
            "STEP1_OUTPUT",
            "STEP2 using: STEP1_OUTPUT",
        ]
        gw = _make_gateway(responses)
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="plan"),
                PipelineStep(role=TaskRole.CODER, prompt_template="code from: {context}"),
            ],
        )
        result = pipeline.run(initial_context="")

        second_call = gw.call_model.call_args_list[1]
        messages = second_call[0][1]
        assert any("STEP1_OUTPUT" in m["content"] for m in messages)
        assert result.final_output == "STEP2 using: STEP1_OUTPUT"

    def test_is_frozen(self):
        step = PipelineStep(role=TaskRole.CODER, prompt_template="x")
        with pytest.raises(AttributeError):
            step.prompt_template = "y"

    def test_role_is_taskrole_enum(self):
        step = PipelineStep(role=TaskRole.CODER, prompt_template="x")
        assert isinstance(step.role, TaskRole)

    def test_all_roles_accepted(self):
        for role in TaskRole:
            step = PipelineStep(role=role, prompt_template="ok")
            assert step.role == role

    def test_context_substitution(self):
        gw = _make_gateway(["done"])
        step = PipelineStep(role=TaskRole.CODER, prompt_template="Task: {context}")
        pipeline = ModelPipeline(gateway=gw, model_id="test-model", steps=[step])
        pipeline.run(initial_context="build app")
        messages = gw.call_model.call_args[0][1]
        assert messages[-1]["content"] == "Task: build app"

    def test_mixed_results(self):
        ok = StepResult(role=TaskRole.PLANNER, output="plan", success=True, cost_usd=0.01)
        bad = StepResult(role=TaskRole.CODER, output="", success=False, error="crash")
        pr = PipelineResult(step_results=(ok, bad))
        assert pr.step_count == 2
        assert pr.success is False
        assert pr.total_cost_usd == 0.01

    def test_aggregate_tokens(self):
        a = StepResult(role=TaskRole.CODER, input_tokens=5, output_tokens=10, success=True)
        b = StepResult(role=TaskRole.CODER, input_tokens=3, output_tokens=7, success=True)
        pr = PipelineResult(step_results=(a, b))
        assert pr.total_input_tokens == 8
        assert pr.total_output_tokens == 17

    def test_aggregate_elapsed(self):
        a = StepResult(role=TaskRole.CODER, elapsed_seconds=1.0, success=True)
        b = StepResult(role=TaskRole.CODER, elapsed_seconds=2.5, success=True)
        pr = PipelineResult(step_results=(a, b))
        assert pr.total_elapsed_seconds == 3.5

    def test_error_stops_subsequent_steps(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.side_effect = [
            _make_response("first ok"),
            RuntimeError("mid crash"),
        ]

        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test-model",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="plan: {context}"),
                PipelineStep(role=TaskRole.CODER, prompt_template="code: {context}"),
                PipelineStep(role=TaskRole.REVIEWER, prompt_template="review: {context}"),
            ],
        )
        result = pipeline.run(initial_context="task")

        assert result.step_count == 3
        assert result.success is False
        assert result.step_results[0].success is True
        assert result.step_results[1].success is False
        assert result.step_results[2].success is False
        assert result.step_results[2].error == "Pipeline stopped due to prior step failure"

    def test_all_six_roles(self):
        steps = [PipelineStep(role=role, prompt_template="{context}") for role in TaskRole]
        gw = _make_gateway(["done"] * 6)
        pipeline = ModelPipeline(gateway=gw, model_id="test-model", steps=steps)
        result = pipeline.run(initial_context="task")

        assert result.step_count == 6
        assert result.success is True
        assert len({sr.role for sr in result.step_results}) == 6


# ---------------------------------------------------------------------------
# _safe_int — deep coverage
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_positive_int(self):
        assert _safe_int(5) == 5

    def test_zero(self):
        assert _safe_int(0) == 0

    def test_negative_int_clamped_to_zero(self):
        assert _safe_int(-1) == 0

    def test_positive_float_truncated(self):
        assert _safe_int(3.9) == 3

    def test_negative_float_clamped(self):
        assert _safe_int(-2.7) == 0

    def test_zero_float(self):
        assert _safe_int(0.0) == 0

    def test_bool_true_returns_zero(self):
        assert _safe_int(True) == 0

    def test_bool_false_returns_zero(self):
        assert _safe_int(False) == 0

    def test_infinity_returns_zero(self):
        assert _safe_int(float("inf")) == 0

    def test_negative_infinity_returns_zero(self):
        assert _safe_int(float("-inf")) == 0

    def test_nan_returns_zero(self):
        assert _safe_int(float("nan")) == 0

    def test_none_returns_zero(self):
        assert _safe_int(None) == 0

    def test_string_returns_zero(self):
        assert _safe_int("42") == 0

    def test_list_returns_zero(self):
        assert _safe_int([1, 2, 3]) == 0

    def test_dict_returns_zero(self):
        assert _safe_int({"a": 1}) == 0

    def test_empty_string_returns_zero(self):
        assert _safe_int("") == 0

    def test_very_large_int(self):
        assert _safe_int(10**9) == 10**9

    def test_very_large_negative_clamped(self):
        assert _safe_int(-(10**9)) == 0


# ---------------------------------------------------------------------------
# PipelineResult — deep edge cases
# ---------------------------------------------------------------------------


class TestPipelineResultDeep:
    def test_post_init_no_step_results_no_modify(self):
        result = PipelineResult(
            final_output="unused",
            total_cost_usd=99.99,
            total_input_tokens=999,
            total_output_tokens=888,
            total_elapsed_seconds=77.7,
            success=True,
        )
        assert result.total_cost_usd == 99.99
        assert result.total_input_tokens == 999
        assert result.total_output_tokens == 888
        assert result.total_elapsed_seconds == 77.7
        assert result.success is True

    def test_single_step_with_zero_values(self):
        sr = StepResult(
            role=TaskRole.CODER,
            output="x",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            elapsed_seconds=0.0,
            success=True,
        )
        result = PipelineResult(step_results=(sr,))
        assert result.total_input_tokens == 0
        assert result.total_output_tokens == 0
        assert result.total_cost_usd == 0.0
        assert result.total_elapsed_seconds == 0.0
        assert result.success is True

    def test_single_step_failure_aggregates_totals(self):
        sr = StepResult(
            role=TaskRole.CODER,
            output="",
            input_tokens=50,
            output_tokens=0,
            cost_usd=0.001,
            elapsed_seconds=0.3,
            success=False,
            error="boom",
        )
        result = PipelineResult(step_results=(sr,))
        assert result.total_input_tokens == 50
        assert result.total_output_tokens == 0
        assert result.total_cost_usd == 0.001
        assert result.success is False

    def test_many_steps_aggregated(self):
        steps = tuple(
            StepResult(
                role=TaskRole.CODER,
                output="x",
                input_tokens=i,
                output_tokens=i * 2,
                cost_usd=i * 0.001,
                elapsed_seconds=i * 0.1,
                success=True,
            )
            for i in range(1, 11)
        )
        result = PipelineResult(step_results=steps)
        assert result.step_count == 10
        assert result.total_input_tokens == 55
        assert result.total_output_tokens == 110
        assert result.total_cost_usd == pytest.approx(0.055)
        assert result.total_elapsed_seconds == pytest.approx(5.5)
        assert result.success is True

    def test_mixed_success_failure_midway(self):
        steps = (
            StepResult(
                role=TaskRole.PLANNER,
                output="p",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.01,
                elapsed_seconds=0.1,
                success=True,
            ),
            StepResult(
                role=TaskRole.CODER,
                output="",
                input_tokens=2,
                output_tokens=0,
                cost_usd=0.02,
                elapsed_seconds=0.2,
                success=False,
                error="fail",
            ),
            StepResult(
                role=TaskRole.REVIEWER,
                output="",
                input_tokens=3,
                output_tokens=0,
                cost_usd=0.03,
                elapsed_seconds=0.3,
                success=False,
                error="skip",
            ),
        )
        result = PipelineResult(step_results=steps)
        assert result.step_count == 3
        assert result.success is False
        assert result.total_cost_usd == 0.06
        assert result.total_input_tokens == 6

    def test_step_count_property_matches(self):
        steps = tuple(StepResult(role=TaskRole.CODER, success=True) for _ in range(5))
        result = PipelineResult(step_results=steps)
        assert result.step_count == 5


# ---------------------------------------------------------------------------
# ModelPipeline — deep edge cases
# ---------------------------------------------------------------------------


class TestModelPipelineDeep:
    def test_multiple_context_placeholders_replaced(self):
        gw = _make_gateway(["done"])
        step = PipelineStep(role=TaskRole.CODER, prompt_template="A: {context} B: {context} C: {context}")
        pipeline = ModelPipeline(gateway=gw, model_id="test", steps=[step])
        pipeline.run(initial_context="X")
        messages = gw.call_model.call_args[0][1]
        assert messages[-1]["content"] == "A: X B: X C: X"

    def test_context_accumulates_with_newlines(self):
        responses = ["first", "second"]
        gw = _make_gateway(responses)
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="{context}"),
                PipelineStep(role=TaskRole.CODER, prompt_template="{context}"),
            ],
        )
        result = pipeline.run(initial_context="init")
        first_call = gw.call_model.call_args_list[0]
        assert first_call[0][1][-1]["content"] == "init"
        second_call = gw.call_model.call_args_list[1]
        assert "\n\n" in second_call[0][1][-1]["content"]
        assert "init" in second_call[0][1][-1]["content"]
        assert "first" in second_call[0][1][-1]["content"]
        assert result.final_output == "second"

    def test_empty_initial_context_still_propagates_output(self):
        gw = _make_gateway(["only output"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="{context}")],
        )
        result = pipeline.run(initial_context="")
        assert result.final_output == "only output"

    def test_first_step_output_empty_context_still_accumulates(self):
        responses = ["", "second from empty"]
        gw = _make_gateway(responses)
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="{context}"),
                PipelineStep(role=TaskRole.CODER, prompt_template="{context}"),
            ],
        )
        result = pipeline.run(initial_context="start")
        assert result.success is True
        assert result.step_count == 2

    def test_usage_metadata_missing_keys_defaults_zero(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.return_value = ModelResponse(
            content="output",
            usage_metadata={},
            cost_estimate=0.01,
            model_name="test",
        )
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        result = pipeline.run(initial_context="")
        assert result.step_results[0].input_tokens == 0
        assert result.step_results[0].output_tokens == 0

    def test_usage_metadata_string_token_values_coerced(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.return_value = ModelResponse(
            content="out",
            usage_metadata={"input_tokens": "100", "output_tokens": "50"},
            cost_estimate=0.0,
            model_name="test",
        )
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        result = pipeline.run(initial_context="")
        assert result.step_results[0].input_tokens == 0
        assert result.step_results[0].output_tokens == 0

    def test_negative_cost_estimate_preserved(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.return_value = ModelResponse(
            content="out",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
            cost_estimate=-0.01,
            model_name="test",
        )
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        result = pipeline.run(initial_context="")
        assert result.step_results[0].cost_usd == -0.01

    def test_zero_cost_estimate(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.return_value = ModelResponse(
            content="free",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
            cost_estimate=0.0,
            model_name="free-model",
        )
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        result = pipeline.run(initial_context="")
        assert result.step_results[0].cost_usd == 0.0

    def test_last_step_fails_no_final_output(self):
        gw = MagicMock(spec=ModelGateway)
        gw.call_model.side_effect = [
            _make_response("first ok"),
            RuntimeError("last step crash"),
        ]
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="plan"),
                PipelineStep(role=TaskRole.CODER, prompt_template="code"),
            ],
        )
        result = pipeline.run(initial_context="task")
        assert result.final_output == ""
        assert result.success is False

    def test_elapsed_time_rounded_to_4_places(self):
        gw = _make_gateway(["x"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        result = pipeline.run(initial_context="")
        elapsed = result.step_results[0].elapsed_seconds
        assert elapsed == round(elapsed, 4)

    def test_system_prompt_appended_before_user_message(self):
        gw = _make_gateway(["ok"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[
                PipelineStep(
                    role=TaskRole.PLANNER,
                    prompt_template="do {context}",
                    system_prompt="sys msg",
                )
            ],
        )
        pipeline.run(initial_context="in")
        messages = gw.call_model.call_args[0][1]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_gateway_call_model_receives_model_id(self):
        gw = _make_gateway(["resp"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="special-model-id",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        pipeline.run(initial_context="")
        call_args = gw.call_model.call_args
        assert call_args[0][0] == "special-model-id"

    def test_gateway_receives_estimated_cost_zero(self):
        gw = _make_gateway(["resp"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        pipeline.run(initial_context="")
        call_args = gw.call_model.call_args
        assert call_args.kwargs["estimated_cost"] == 0.0

    def test_gateway_receives_infinite_budget(self):
        gw = _make_gateway(["resp"])
        pipeline = ModelPipeline(
            gateway=gw,
            model_id="test",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="x")],
        )
        pipeline.run(initial_context="")
        call_args = gw.call_model.call_args
        assert call_args.kwargs["budget_remaining"] == float("inf")

    def test_step_result_fields_are_independent(self):
        sr = StepResult(
            role=TaskRole.CODER,
            output="data",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.02,
            elapsed_seconds=1.5,
            success=True,
            error="",
        )
        assert sr.role == TaskRole.CODER
        assert sr.output == "data"
        assert sr.input_tokens == 100
        assert sr.output_tokens == 50
        assert sr.cost_usd == 0.02
        assert sr.elapsed_seconds == 1.5
        assert sr.success is True
        assert sr.error == ""

    def test_pipeline_step_hash_consistent(self):
        a = PipelineStep(role=TaskRole.CODER, prompt_template="x", system_prompt="s")
        b = PipelineStep(role=TaskRole.CODER, prompt_template="x", system_prompt="s")
        assert hash(a) == hash(b)

    def test_pipeline_step_hash_differs_by_system_prompt(self):
        a = PipelineStep(role=TaskRole.CODER, prompt_template="x", system_prompt="a")
        b = PipelineStep(role=TaskRole.CODER, prompt_template="x", system_prompt="b")
        assert hash(a) != hash(b)
