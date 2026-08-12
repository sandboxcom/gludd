"""Benchmark recording helpers for the event loop."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def record_job_benchmark(
    recorder: Any,
    model_profile: str | None,
    prompt_profile: str | None,
    work_type: str,
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    test_exit_code: int | None = None,
    test_summary: str | None = None,
    skill_id: str | None = None,
) -> None:
    if recorder is None or recorder._repo is None:
        return
    # S19: code_quality_score was hardcoded 0.5. Derive from test results
    # when available; falls back to 0.5 only when no test data is provided.
    if test_exit_code is not None:
        code_quality_score: float = 1.0 if test_exit_code == 0 else 0.3
    else:
        code_quality_score = 0.5
    with contextlib.suppress(Exception):
        data = {
                "model_profile_id": model_profile or "unknown",
                "prompt_profile_id": prompt_profile,
                "task_type": work_type,
                "success": success,
                "completion_score": 1.0 if success else 0.0,
                "code_quality_score": code_quality_score,
                "instruction_adherence_score": 1.0 if success else 0.5,
                "token_efficiency_score": min(1.0, 1000.0 / max(float(input_tokens), 1.0)),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "time_seconds": 0.0,
                "error_message": "" if success else "Job failed",
                "raw_output": "",
            }
        # Preserve the exact legacy payload when the caller has no resolved
        # skill; the nullable ORM column supplies NULL in that case.
        if skill_id is not None:
            data["skill_id"] = skill_id
        await recorder._repo.record_result(data=data)
        if test_summary:
            logger.info(
                "Benchmark recorded: model=%s task=%s success=%s quality_score=%.2f (exit_code=%s)",
                model_profile,
                work_type,
                success,
                code_quality_score,
                test_exit_code,
            )
        else:
            logger.info(
                "Benchmark recorded: model=%s task=%s success=%s quality_score=%.2f (no test data)",
                model_profile,
                work_type,
                success,
                code_quality_score,
            )
