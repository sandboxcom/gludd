from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.infra.slurm import SlurmAdapter, SlurmNotInstalledError


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    adapter = SlurmAdapter(
        api_url=os.environ.get("SLURM_API_URL") or None,
        auth_token=os.environ.get("SLURM_AUTH_TOKEN") or None,
    )

    @app.get("/admin/slurm/status")
    async def admin_slurm_status() -> dict[str, Any]:
        return {"available": adapter.available()}

    @app.post("/admin/slurm/submit")
    async def admin_slurm_submit(req: dict[str, Any]) -> dict[str, Any]:
        command = req.get("command", "")
        if not command:
            raise HTTPException(status_code=422, detail="command is required")
        try:
            job_id = adapter.submit(
                command=command,
                job_name=req.get("job_name"),
                partition=req.get("partition"),
                cpus_per_task=req.get("cpus_per_task"),
                gpus=req.get("gpus"),
                memory=req.get("memory"),
                time_limit=req.get("time_limit"),
                output=req.get("output"),
                extra_args=req.get("extra_args"),
            )
            return {"job_id": job_id}
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/admin/slurm/jobs/{job_id}")
    async def admin_slurm_job_status(job_id: str) -> dict[str, Any]:
        try:
            info = adapter.status(job_id)
            return {
                "job_id": info.job_id,
                "state": info.state.value,
                "exit_code": info.exit_code,
            }
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None

    @app.delete("/admin/slurm/jobs/{job_id}")
    async def admin_slurm_job_cancel(job_id: str) -> dict[str, Any]:
        try:
            adapter.cancel(job_id)
            return {"cancelled": job_id}
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/admin/slurm/jobs")
    async def admin_slurm_jobs_list() -> dict[str, Any]:
        try:
            jobs = adapter.list_jobs()
            return {
                "jobs": [
                    {"job_id": j.job_id, "state": j.state.value}
                    for j in jobs
                ],
            }
        except Exception:
            return {"jobs": [], "message": "Could not list jobs"}
