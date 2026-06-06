"""Auto-benchmark recorder — automatically records benchmark results from execution traces."""

from __future__ import annotations

import logging
from typing import Any

from general_ludd.observability.tracer import ExecutionTrace

logger = logging.getLogger(__name__)


def compute_scores_from_trace(trace: ExecutionTrace, success: bool) -> dict[str, float]:
    """Compute benchmark scores from an execution trace."""
    completion_score = 1.0 if success else 0.0
    code_quality_score = 0.5
    instruction_score = 1.0 if success else 0.5

    total_input = trace.total_input_tokens
    token_efficiency = min(1.0, 1000.0 / max(float(total_input), 1.0))

    return {
        "completion": completion_score,
        "code_quality": code_quality_score,
        "instruction": instruction_score,
        "token_efficiency": token_efficiency,
    }


class AutoBenchmarkRecorder:
    """Records benchmark results automatically from execution traces."""

    def __init__(self, benchmark_repo: Any | None = None) -> None:
        self._repo = benchmark_repo

    async def record_from_trace(
        self,
        trace: ExecutionTrace,
        success: bool = True,
        test_results: dict[str, int] | None = None,
    ) -> None:
        """Record a benchmark result from a completed execution trace."""
        if self._repo is None:
            return
        if not trace.spans:
            logger.debug("No spans in trace %s, skipping benchmark record", trace.trace_id)
            return

        scores = compute_scores_from_trace(trace, success)
        if test_results:
            total = test_results.get("total", 0)
            passed = test_results.get("passed", 0)
            if total > 0:
                scores["code_quality"] = passed / total

        last_span = trace.spans[-1]
        error_msg = last_span.error_message or ""

        try:
            await self._repo.record_result(
                model_profile_id=last_span.model_profile_id or "unknown",
                prompt_profile_id=last_span.prompt_profile_id,
                task_type=trace.work_type,
                scores=scores,
                success=success,
                input_tokens=trace.total_input_tokens,
                output_tokens=trace.total_tokens,
                cost_usd=trace.total_cost_usd,
                time_seconds=(last_span.duration_ms / 1000) if last_span.duration_ms > 0 else 0.0,
                error_message=error_msg,
            )
            logger.info(
                "Benchmark recorded: trace=%s model=%s success=%s score=%.2f",
                trace.trace_id,
                last_span.model_profile_id,
                success,
                scores["completion"],
            )
        except Exception as exc:
            logger.warning("Failed to record benchmark for trace %s: %s", trace.trace_id, exc)
