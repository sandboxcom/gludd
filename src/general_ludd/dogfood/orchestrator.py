"""Dogfood orchestrator — drives DogfoodRunner + DogfoodValidator.

This is the production entry point for the no-AI self-test loop. ``make
dogfood`` (scripts/dogfood.py) delegates the smoke-task + validation step here
so the runner/validator classes live on a real call path rather than only in
tests. Returns a serializable report.
"""

from __future__ import annotations

from typing import Any

from general_ludd.dogfood.runner import DogfoodConfig, DogfoodRunner
from general_ludd.dogfood.validator import DogfoodValidator


def run_smoke_and_validate(
    repo_root: str,
    task_name: str = "noop",
    target_repo: str | None = None,
    runtime_profile: str = "ansible",
    model_profile: str = "dogfood-echo",
) -> dict[str, Any]:
    """Run a dogfood smoke task and adjudicate it; return a report dict."""
    runner = DogfoodRunner(
        DogfoodConfig(
            repo_root=repo_root,
            target_repo=target_repo or repo_root,
            runtime_profile=runtime_profile,
            model_profile=model_profile,
        )
    )
    smoke = runner.run_smoke_task(task_name)
    validation = DogfoodValidator().validate_dogfood_run(smoke)
    return {
        "smoke": {
            "task_name": smoke.task_name,
            "success": smoke.success,
            "duration_seconds": smoke.duration_seconds,
        },
        "validation": {
            "valid": validation.valid,
            "uses_configured_runtime": validation.uses_configured_runtime,
            "uses_configured_models": validation.uses_configured_models,
        },
    }
