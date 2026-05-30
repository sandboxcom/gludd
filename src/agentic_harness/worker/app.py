"""FastAPI worker application for the agentic harness."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from agentic_harness.schemas.job import JobSpec
from agentic_harness.schemas.task_return import TaskReturn

logger = logging.getLogger(__name__)

PLAYBOOK_REGISTRY: set[str] = {"noop.yml"}


def create_app() -> FastAPI:
    application = FastAPI(
        title="Agentic Harness Worker",
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
        logger.info("Executing job %s playbook %s", job.job_id, job.playbook)
        task_return = TaskReturn(
            return_id=f"RET-{job.job_id}",
            todo_id=job.todo_id,
            job_id=job.job_id,
            playbook=job.playbook,
            queue=job.queue,
            work_type=job.work_type,
            resource_profile=job.resource_profile,
            exit_code=0,
            result_summary=f"No-op playbook completed for job {job.job_id}",
        )
        return task_return.model_dump(mode="json")

    @application.post("/jobs/return-review")
    async def return_review(job: JobSpec) -> dict[str, Any]:
        if job.playbook != "return_review.yml" and job.playbook not in PLAYBOOK_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown playbook: {job.playbook}",
            )
        logger.info("Return review for job %s", job.job_id)
        return {"status": "review_dispatched", "job_id": job.job_id}

    @application.post("/jobs/validate")
    async def validate_job(job: JobSpec) -> dict[str, Any]:
        logger.info("Validation for job %s", job.job_id)
        return {"status": "validation_dispatched", "job_id": job.job_id}

    @application.post("/jobs/policy-validate")
    async def policy_validate(job: JobSpec) -> dict[str, Any]:
        logger.info("Policy validation for job %s", job.job_id)
        return {"status": "policy_validation_dispatched", "job_id": job.job_id}

    @application.post("/jobs/reload-request")
    async def reload_request(job: JobSpec) -> dict[str, Any]:
        logger.info("Reload request for job %s", job.job_id)
        return {"status": "reload_dispatched", "job_id": job.job_id}

    return application
