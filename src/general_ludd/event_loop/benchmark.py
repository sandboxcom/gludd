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
) -> None:
    if recorder is None or recorder._repo is None:
        return
    with contextlib.suppress(Exception):
        await recorder._repo.record_result(data={
            "model_profile_id": model_profile or "unknown",
            "prompt_profile_id": prompt_profile,
            "task_type": work_type,
            "success": success,
            "completion_score": 1.0 if success else 0.0,
            "code_quality_score": 0.5,
            "instruction_adherence_score": 1.0 if success else 0.5,
            "token_efficiency_score": min(1.0, 1000.0 / max(float(input_tokens), 1.0)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "time_seconds": 0.0,
            "error_message": "" if success else "Job failed",
            "raw_output": "",
        })
        logger.info("Benchmark recorded: model=%s task=%s success=%s", model_profile, work_type, success)
