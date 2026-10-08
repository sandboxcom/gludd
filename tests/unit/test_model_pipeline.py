"""Unit tests for ModelPipeline multi-step LLM orchestration."""

from unittest.mock import MagicMock

import pytest

from general_ludd.cloud.model_pipeline import (
    ModelPipeline,
    PipelineResult,
    PipelineStep,
    StepResult,
    _safe_int,
)
from general_ludd.schemas.benchmark import TaskRole


class TestSafeInt:
    def test_int_passthrough(self):
        assert _safe_int(5) == 5

    def test_float_truncation(self):
        assert _safe_int(5.9) == 5

    def test_bool_is_zero(self):
        assert _safe_int(True) == 0

    def test_negative_to_zero(self):
        assert _safe_int(-3) == 0

    def test_non_finite_to_zero(self):
        assert _safe_int(float("inf")) == 0

    def test_string_to_zero(self):
        assert _safe_int("abc") == 0


class TestPipelineResult:
    def test_post_init_aggregation(self):
        s1 = StepResult(
            role=TaskRole.PLANNER, cost_usd=1.0, input_tokens=10, output_tokens=5, elapsed_seconds=2.0, success=True
        )
        s2 = StepResult(
            role=TaskRole.CODER, cost_usd=2.0, input_tokens=20, output_tokens=10, elapsed_seconds=3.0, success=True
        )
        result = PipelineResult(step_results=(s1, s2))
        assert result.total_cost_usd == 3.0
        assert result.total_input_tokens == 30
        assert result.total_output_tokens == 15
        assert result.total_elapsed_seconds == 5.0
        assert result.success is True
        assert result.step_count == 2

    def test_failure_propagation(self):
        s1 = StepResult(role=TaskRole.PLANNER, success=True)
        s2 = StepResult(role=TaskRole.CODER, success=False)
        result = PipelineResult(step_results=(s1, s2))
        assert result.success is False


class TestModelPipeline:
    def test_requires_gateway(self):
        with pytest.raises(ValueError, match="ModelGateway is required"):
            ModelPipeline(gateway=None, model_id="x", steps=[PipelineStep(role=TaskRole.PLANNER, prompt_template="hi")])

    def test_requires_steps(self):
        gateway = MagicMock()
        with pytest.raises(ValueError, match="At least one step is required"):
            ModelPipeline(gateway=gateway, model_id="x", steps=[])

    def test_run_success(self):
        gateway = MagicMock()
        response = MagicMock()
        response.content = "plan output"
        response.cost_estimate = 0.5
        response.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        gateway.call_model.return_value = response

        pipeline = ModelPipeline(
            gateway=gateway,
            model_id="x",
            steps=[PipelineStep(role=TaskRole.PLANNER, prompt_template="Plan: {context}")],
        )
        result = pipeline.run("build a thing")
        assert result.success is True
        assert result.final_output == "plan output"
        assert result.total_cost_usd == 0.5

    def test_run_failure_stops(self):
        gateway = MagicMock()
        gateway.call_model.side_effect = RuntimeError("model down")

        pipeline = ModelPipeline(
            gateway=gateway,
            model_id="x",
            steps=[
                PipelineStep(role=TaskRole.PLANNER, prompt_template="Plan: {context}"),
                PipelineStep(role=TaskRole.CODER, prompt_template="Code: {context}"),
            ],
        )
        result = pipeline.run("build a thing")
        assert result.success is False
        assert result.final_output == ""
        assert len(result.step_results) == 2
        assert result.step_results[0].success is False
        assert result.step_results[1].success is False

    def test_system_prompt_message(self):
        gateway = MagicMock()
        response = MagicMock()
        response.content = "ok"
        response.cost_estimate = 0.0
        response.usage_metadata = {}
        gateway.call_model.return_value = response

        pipeline = ModelPipeline(
            gateway=gateway,
            model_id="x",
            steps=[PipelineStep(role=TaskRole.CODER, prompt_template="Code", system_prompt="You are a coder")],
        )
        result = pipeline.run("ctx")
        assert result.success is True
        call_args = gateway.call_model.call_args
        messages = call_args[0][1]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
