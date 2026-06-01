"""FastAPI worker application for General Ludd Agent."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.ansible.runner import AnsibleRunnerAdapter
from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)

PLAYBOOK_REGISTRY: set[str] = {"noop.yml"}

_runner: AnsibleRunnerAdapter | None = None


def get_runner() -> AnsibleRunnerAdapter:
    global _runner
    if _runner is None:
        _runner = AnsibleRunnerAdapter()
    return _runner


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
        if job.playbook not in PLAYBOOK_REGISTRY:
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
                **job.budget_context,
            },
            shared_vars=None,
        )
        runner_result = runner.run_playbook(
            playbook_name=job.playbook,
            private_data_dir=dirs["root"],
        )
        exit_code = runner_result.get("rc", 1)
        events = runner_result.get("events", [])
        task_return = TaskReturn(
            return_id=f"RET-{job.job_id}",
            todo_id=job.todo_id,
            job_id=job.job_id,
            playbook=job.playbook,
            queue=job.queue,
            work_type=job.work_type,
            resource_profile=job.resource_profile,
            exit_code=exit_code,
            result_summary=f"Playbook {job.playbook} finished with rc={exit_code}",
            artifacts=[dirs["artifacts"]],
            logs_ref=dirs["root"],
        )
        response = task_return.model_dump(mode="json")
        response["events"] = events
        response["job_id"] = job.job_id
        response["todo_id"] = job.todo_id
        return response

    @application.post("/jobs/return-review")
    async def return_review(job: JobSpec) -> dict[str, Any]:
        if job.playbook != "return_review.yml" and job.playbook not in PLAYBOOK_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown playbook: {job.playbook}",
            )
        logger.info("Return review job_id=%s todo_id=%s", job.job_id, job.todo_id)
        return {"status": "review_dispatched", "job_id": job.job_id, "todo_id": job.todo_id}

    @application.post("/jobs/validate")
    async def validate_job(job: JobSpec) -> dict[str, Any]:
        logger.info("Validation job_id=%s todo_id=%s", job.job_id, job.todo_id)
        return {"status": "validation_dispatched", "job_id": job.job_id, "todo_id": job.todo_id}

    @application.post("/jobs/policy-validate")
    async def policy_validate(job: JobSpec) -> dict[str, Any]:
        logger.info("Policy validation job_id=%s todo_id=%s", job.job_id, job.todo_id)
        return {"status": "policy_validation_dispatched", "job_id": job.job_id, "todo_id": job.todo_id}

    @application.post("/jobs/reload-request")
    async def reload_request(job: JobSpec) -> dict[str, Any]:
        logger.info("Reload request job_id=%s todo_id=%s", job.job_id, job.todo_id)
        return {"status": "reload_dispatched", "job_id": job.job_id, "todo_id": job.todo_id}

    return application
