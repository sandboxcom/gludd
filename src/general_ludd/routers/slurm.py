from __future__ import annotations

import asyncio
import os
from typing import cast

from fastapi import FastAPI, HTTPException

from general_ludd.infra.slurm import SlurmAdapter, SlurmNotInstalledError


def _resolve_slurm_creds(app: FastAPI) -> tuple[str | None, str | None]:
    secrets_resolver = getattr(app.state, "_secrets_resolver", None)
    if secrets_resolver is not None:
        api_url = (
            secrets_resolver.resolve("slurm_api_url")
            or os.environ.get("SLURM_API_URL")
        )
        auth_token = (
            secrets_resolver.resolve("slurm_auth_token")
            or os.environ.get("SLURM_AUTH_TOKEN")
        )
    else:
        api_url = os.environ.get("SLURM_API_URL") or None
        auth_token = os.environ.get("SLURM_AUTH_TOKEN") or None
    return api_url or None, auth_token or None


def _make_adapter(app: FastAPI) -> SlurmAdapter:
    api_url, auth_token = _resolve_slurm_creds(app)
    return SlurmAdapter(api_url=api_url, auth_token=auth_token)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    @app.get("/admin/slurm/status")
    async def admin_slurm_status() -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            available = await asyncio.to_thread(adapter.available)
            return {"available": available}
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None

    @app.post("/admin/slurm/submit")
    async def admin_slurm_submit(req: dict[str, object]) -> dict[str, object]:
        adapter = _make_adapter(app)
        command = cast(str, req.get("command", ""))
        if not command:
            raise HTTPException(status_code=422, detail="command is required")
        try:
            job_id = await asyncio.to_thread(
                adapter.submit,
                command=command,
                job_name=cast(str | None, req.get("job_name")),
                partition=cast(str | None, req.get("partition")),
                cpus_per_task=cast(int | None, req.get("cpus_per_task")),
                gpus=cast(str | None, req.get("gpus")),
                memory=cast(str | None, req.get("memory")),
                time_limit=cast(str | None, req.get("time_limit")),
                output=cast(str | None, req.get("output")),
                extra_args=cast(list[str] | None, req.get("extra_args")),
                account=cast(str | None, req.get("account")),
                qos=cast(str | None, req.get("qos")),
            )
            return {"job_id": job_id}
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/admin/slurm/jobs/{job_id}")
    async def admin_slurm_job_status(job_id: str) -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            info = await asyncio.to_thread(adapter.status, job_id)
            return {
                "job_id": info.job_id,
                "state": info.state.value,
                "exit_code": info.exit_code,
            }
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None

    @app.delete("/admin/slurm/jobs/{job_id}")
    async def admin_slurm_job_cancel(job_id: str) -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            await asyncio.to_thread(adapter.cancel, job_id)
            return {"cancelled": job_id}
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/admin/slurm/jobs")
    async def admin_slurm_jobs_list() -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            jobs = await asyncio.to_thread(adapter.list_jobs)
            return {
                "jobs": [
                    {"job_id": j.job_id, "state": j.state.value}
                    for j in jobs
                ],
            }
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not list jobs: {exc}") from exc

    @app.get("/admin/slurm/jobs/{job_id}/cost")
    async def admin_slurm_job_cost(job_id: str) -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            info = await asyncio.to_thread(adapter.status, job_id)
            return {
                "job_id": info.job_id,
                "cost_breakdown": {
                    "estimated_cost_usd": info.cost_incurred,
                    "state": info.state.value,
                },
            }
        except SlurmNotInstalledError:
            raise HTTPException(status_code=503, detail="Slurm is not installed") from None
