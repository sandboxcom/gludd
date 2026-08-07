"""Generic ModelPipeline for multi-step LLM workflows.

Each step is a (TaskRole, prompt_template) pair. Context from prior steps
is passed through to subsequent steps via a ``{context}`` placeholder in
the prompt template.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from general_ludd.schemas.benchmark import TaskRole

if TYPE_CHECKING:
    from general_ludd.models.gateway import ModelGateway


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return 0
        return max(0, int(value))
    return 0


@dataclass(frozen=True)
class PipelineStep:
    """One stage in a multi-step LLM pipeline."""

    role: TaskRole
    prompt_template: str
    system_prompt: str = ""


@dataclass
class StepResult:
    """Metrics and output for a single pipeline step."""

    role: TaskRole
    output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_seconds: float = 0.0
    success: bool = False
    error: str = ""


@dataclass
class PipelineResult:
    """Aggregated result from running a full pipeline."""

    final_output: str = ""
    step_results: tuple[StepResult, ...] = ()
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_elapsed_seconds: float = 0.0
    success: bool = False

    @property
    def step_count(self) -> int:
        return len(self.step_results)

    def __post_init__(self) -> None:
        if self.step_results:
            self.total_cost_usd = sum(s.cost_usd for s in self.step_results)
            self.total_input_tokens = sum(s.input_tokens for s in self.step_results)
            self.total_output_tokens = sum(s.output_tokens for s in self.step_results)
            self.total_elapsed_seconds = sum(s.elapsed_seconds for s in self.step_results)
            self.success = all(s.success for s in self.step_results)


class ModelPipeline:
    """Orchestrate a sequence of LLM calls with context passing between steps.

    Each step is a ``PipelineStep`` carrying a :class:`TaskRole` and a prompt
    template.  The template may contain ``{context}``, which is replaced with
    the accumulated output of all prior steps (or ``initial_context`` on the
    first call).  A ``system_prompt`` on a step prepends a system message.

    Example::

        pipeline = ModelPipeline(
            gateway=gw,
            model_id="deepseek-v3",
            steps=[
                PipelineStep(TaskRole.PLANNER, "Plan: {context}"),
                PipelineStep(TaskRole.CODER, "Code from: {context}", "You are a coder"),
            ],
        )
        result = pipeline.run(initial_context="build a calculator")
        print(result.final_output)
    """

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        model_id: str,
        steps: list[PipelineStep],
    ) -> None:
        if gateway is None:
            raise ValueError("ModelGateway is required")
        if not steps:
            raise ValueError("At least one step is required")
        self._gateway: ModelGateway = gateway
        self._model_id = model_id
        self._steps: list[PipelineStep] = list(steps)

    def run(self, initial_context: str) -> PipelineResult:
        """Execute every step in order, passing context forward.

        Args:
            initial_context: The starting input fed as ``{context}`` into the
                first step's prompt template.

        Returns:
            A ``PipelineResult`` aggregating every step's metrics and the final
            output.
        """
        step_results: list[StepResult] = []
        context = initial_context

        for step in self._steps:
            result = self._run_single_step(step, context)
            step_results.append(result)
            if result.success:
                context = context + ("\n\n" + result.output if context else result.output)
            else:
                break

        final_output = step_results[-1].output if step_results and step_results[-1].success else ""

        remaining = self._steps[len(step_results) :]
        for step in remaining:
            step_results.append(
                StepResult(
                    role=step.role,
                    output="",
                    success=False,
                    error="Pipeline stopped due to prior step failure",
                )
            )

        return PipelineResult(
            final_output=final_output,
            step_results=tuple(step_results),
        )

    def _run_single_step(self, step: PipelineStep, context: str) -> StepResult:
        messages: list[dict[str, str]] = []
        if step.system_prompt:
            messages.append({"role": "system", "content": step.system_prompt})

        user_content = step.prompt_template.replace("{context}", context)
        messages.append({"role": "user", "content": user_content})

        start = time.monotonic()
        try:
            response = self._gateway.call_model(
                self._model_id,
                messages,
                estimated_cost=0.0,
                budget_remaining=float("inf"),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return StepResult(
                role=step.role,
                output="",
                elapsed_seconds=round(elapsed, 4),
                success=False,
                error=str(exc),
            )

        elapsed = time.monotonic() - start
        usage: dict[str, object] = response.usage_metadata or {}
        input_tokens = _safe_int(usage.get("input_tokens", 0))
        output_tokens = _safe_int(usage.get("output_tokens", 0))

        return StepResult(
            role=step.role,
            output=response.content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=response.cost_estimate,
            elapsed_seconds=round(elapsed, 4),
            success=True,
        )
