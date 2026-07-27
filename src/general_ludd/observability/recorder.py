"""Auto-benchmark recorder — automatically records benchmark results from execution traces.

KEEP LIST (V3.8): 15 LOC of domain-specific logic for recording benchmark results
from execution traces. Not replaceable — the project already adopted prometheus-client
for general metrics; this module handles the application-specific benchmark recording
pipeline (task completion → benchmark write).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from general_ludd.observability.tracer import ExecutionTrace

if TYPE_CHECKING:
    from general_ludd.observability.trace_store import RecentTracesBuffer

logger = logging.getLogger(__name__)


def compute_scores_from_trace(trace: ExecutionTrace, success: bool) -> dict[str, float]:
    """Compute benchmark scores from an execution trace.

    Args:
        trace: The execution trace object with token counts and metrics.
        success: Whether the overall task succeeded.

    Returns:
        Dict with completion, code_quality, instruction, and token_efficiency scores.
    """
    completion_score = 1.0 if success else 0.0
    # S19: code_quality_score was hardcoded 0.5 regardless of real test results.
    # When test_results are available on the trace, derive a real quality score;
    # otherwise, fall back to a neutral 0.5.
    test_results: dict[str, object] | None = getattr(trace, "test_results", None)
    if test_results and isinstance(test_results, dict):
        total = test_results.get("total", 0)
        passed = test_results.get("passed", 0)
        if isinstance(total, (int, float)) and isinstance(passed, (int, float)) and total > 0:
            code_quality_score = passed / total
        else:
            code_quality_score = 0.5
    else:
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

    def __init__(
        self,
        benchmark_repo: Any | None = None,
        trace_buffer: RecentTracesBuffer | None = None,
    ) -> None:
        self._repo = benchmark_repo
        self._trace_buffer = trace_buffer

    async def record_from_trace(
        self,
        trace: ExecutionTrace,
        success: bool = True,
        test_results: dict[str, int] | None = None,
    ) -> None:
        """Record a benchmark result from a completed execution trace.

        Also retains the trace in the bounded recent-traces buffer (when one is
        configured) so it is queryable as an Ansible dynamic fact. The buffer
        append is unconditional on a populated trace — even when no benchmark
        repo is wired — so /api/traces reflects genuinely-captured telemetry.
        """
        if trace.spans and self._trace_buffer is not None:
            self._trace_buffer.record(trace)
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

        # Flatten the score dict into the real BenchmarkResultModel columns and
        # pass a single positional ``data`` dict — BenchmarkRepository.record_result
        # takes one dict and constructs BenchmarkResultModel(**data). The previous
        # kwargs/``scores`` form did not match any column and raised TypeError, so
        # every benchmark write was silently dropped (empty table -> no routing data).
        data: dict[str, Any] = {
            "model_profile_id": last_span.model_profile_id or "unknown",
            "prompt_profile_id": last_span.prompt_profile_id,
            "task_type": trace.work_type,
            "completion_score": scores["completion"],
            "code_quality_score": scores["code_quality"],
            "instruction_adherence_score": scores["instruction"],
            "token_efficiency_score": scores["token_efficiency"],
            "success": success,
            "input_tokens": trace.total_input_tokens,
            "output_tokens": trace.total_tokens,
            "cost_usd": trace.total_cost_usd,
            "time_seconds": (last_span.duration_ms / 1000) if last_span.duration_ms > 0 else 0.0,
            "error_message": error_msg,
        }

        try:
            await self._repo.record_result(data)
            logger.info(
                "Benchmark recorded: trace=%s model=%s success=%s score=%.2f",
                trace.trace_id,
                last_span.model_profile_id,
                success,
                scores["completion"],
            )
        except Exception as exc:
            logger.warning("Failed to record benchmark for trace %s: %s", trace.trace_id, exc)
