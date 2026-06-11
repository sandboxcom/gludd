"""FastAPI worker application for General Ludd Agent."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)

_runner: AnsibleRunnerAdapter | None = None


def get_runner() -> AnsibleRunnerAdapter:
    global _runner
    if _runner is None:
        _runner = AnsibleRunnerAdapter()
    return _runner


def get_playbook_registry() -> set[str]:
    return set(get_runner().list_playbooks())


def _redact_secrets(message: str, refs: list[str]) -> str:
    for ref in refs:
        message = message.replace(ref, "***REDACTED***")
    return message


def create_app() -> FastAPI:
    application = FastAPI(
        title="General Ludd Worker",
        version="0.1.0",
    )

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "healthy"}

    @application.post("/jobs/execute")
    async def execute_job(job: JobSpec) -> dict[str, Any]:
        registry = get_playbook_registry()
        if job.playbook not in registry:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown playbook: {job.playbook}",
            )
        redacted_vars = _redact_secrets(
            f"Executing job vars for {job.job_id}",
            job.vars_namespace_refs,
        )
        logger.info(
            "Executing job_id=%s todo_id=%s project_id=%s playbook=%s %s",
            job.job_id,
            job.todo_id,
            getattr(job, "project_id", None),
            job.playbook,
            redacted_vars,
        )
        runner = get_runner()
        dirs = runner.prepare_job_dirs(job.job_id)
        runner.write_vars(
            job.job_id,
            job_vars={
                "job_id": job.job_id,
                "todo_id": job.todo_id,
                "queue": job.queue,
                "work_type": job.work_type,
                "project_id": getattr(job, "project_id", None),
                "model_profile": job.model_profile,
                "prompt_text": job.prompt_text,
                "skill_body": job.skill_body,
                **job.budget_context,
            },
            shared_vars=None,
        )
        runner_result = runner.run_playbook(
            playbook_name=job.playbook,
            private_data_dir=dirs["root"],
        )
        return {
            "return_id": f"RET-{job.job_id}",
            "todo_id": job.todo_id,
            "job_id": job.job_id,
            "exit_code": runner_result.get("rc", runner_result.get("exit_code", 0)),
            "result_summary": runner_result.get("output", runner_result.get("result_summary", "")),
            "artifacts": runner_result.get("artifacts", []),
        }

    @application.post("/jobs/return-review")
    async def return_review_job(job: JobSpec) -> dict[str, Any]:
        try:
            from general_ludd.review.reviewer import ReturnReviewer
            reviewer = ReturnReviewer()
            task_return = TaskReturn(
                return_id=job.job_id,
                todo_id=job.todo_id or "",
                job_id=job.job_id,
                playbook=job.playbook,
                artifacts=job.artifacts,
                exit_code=0,
                stdout=job.task_output or "",
            )
            decision = reviewer.review_return(task_return, [], [])
            return TaskDecision(
                todo_id=job.todo_id or "",
                decision=decision.decision,
                reason=decision.reason,
            ).model_dump()
        except Exception as exc:
            logger.warning("ReturnReviewer failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.post("/jobs/validate")
    async def validate_job(job: JobSpec) -> dict[str, Any]:
        raise HTTPException(status_code=501, detail="Validate must be handled by the daemon")

    @application.post("/jobs/policy-validate")
    async def policy_validate_job(job: JobSpec) -> dict[str, Any]:
        raise HTTPException(status_code=501, detail="Policy validation not yet implemented")

    @application.post("/jobs/reload-request")
    async def reload_request_job(job: JobSpec) -> dict[str, Any]:
        raise HTTPException(status_code=501, detail="Reload requests not yet implemented")

    return application
