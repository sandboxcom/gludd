"""Slurm inspection and bounded model-deployment HTTP routes."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal, cast

from fastapi import Depends, FastAPI, HTTPException
from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator

from general_ludd.infra.slurm import (
    SlurmAdapter,
    SlurmConnectionError,
    SlurmNotInstalledError,
)
from general_ludd.infra.slurm_deployment import (
    LlamacppSlurmDeployment,
    VllmSlurmDeployment,
)
from general_ludd.routers._runtime import IdempotencyStore, StrictRuntimeRequest
from general_ludd.security.capability_guard import RequireCapability

logger = logging.getLogger(__name__)


class SlurmDeployRequest(StrictRuntimeRequest):
    """Bounded model-service deployment request."""

    engine: Literal["vllm", "llamacpp"]
    model_id: StrictStr = Field(min_length=1, max_length=1024)
    gpu_count: StrictInt = Field(default=1, ge=1, le=64)
    gpu_type: StrictStr = Field(default="a100", min_length=1, max_length=128)
    port: StrictInt = Field(default=8000, ge=1, le=65535)
    max_hours: StrictInt = Field(default=4, ge=1, le=240)
    mem_gb: StrictInt = Field(default=32, ge=1, le=4096)
    partition: StrictStr = Field(default="gpu", min_length=1, max_length=128)
    max_ctx: StrictInt = Field(default=32768, ge=512, le=2_000_000)
    artifact_dir: StrictStr = Field(min_length=1, max_length=4096)
    poll_timeout: StrictInt = Field(default=300, ge=1, le=3600)
    poll_interval: StrictFloat = Field(default=5.0, ge=0.1, le=30.0)
    module_loads: list[StrictStr] = Field(default_factory=list, max_length=64)
    extra_args: list[StrictStr] = Field(default_factory=list, max_length=128)
    idempotency_key: StrictStr | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("artifact_dir")
    @classmethod
    def _require_absolute_artifact_dir(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("artifact_dir must be absolute")
        return value

    @field_validator("module_loads", "extra_args")
    @classmethod
    def _bound_argv(cls, value: list[str]) -> list[str]:
        if any(not token or len(token) > 512 for token in value):
            raise ValueError("deployment argv values must be non-empty and bounded")
        return value


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


def _slurm_http_error(operation: str, exc: Exception) -> HTTPException:
    """Map adapter failures to stable HTTP responses without leaking internals."""
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, SlurmNotInstalledError):
        return HTTPException(status_code=503, detail="Slurm is not installed")
    if isinstance(exc, SlurmConnectionError):
        return HTTPException(
            status_code=503,
            detail="Slurm controller is unavailable",
        )
    logger.exception("Slurm %s request failed", operation)
    return HTTPException(
        status_code=500,
        detail=f"Slurm {operation} request failed",
    )


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register authenticated Slurm control-plane routes."""
    deploy_store = IdempotencyStore()

    @app.post(
        "/admin/slurm/deploy",
        dependencies=[Depends(RequireCapability(resource="admin:slurm", action="deploy"))],
    )
    async def admin_slurm_deploy(req: SlurmDeployRequest) -> dict[str, object]:
        async def _run() -> dict[str, object]:
            adapter = _make_adapter(app)
            deployment_class = (
                VllmSlurmDeployment if req.engine == "vllm" else LlamacppSlurmDeployment
            )
            deployment = deployment_class(adapter=adapter)

            def _deploy() -> dict[str, object]:
                job_id = deployment.submit(
                    model_id=req.model_id,
                    gpu_count=req.gpu_count,
                    gpu_type=req.gpu_type,
                    port=req.port,
                    max_hours=req.max_hours,
                    mem_gb=req.mem_gb,
                    partition=req.partition,
                    max_ctx=req.max_ctx,
                    artifact_dir=req.artifact_dir,
                    module_loads=req.module_loads,
                    extra_args=req.extra_args,
                )
                try:
                    servable_url = deployment.poll_until_servable(
                        job_id=job_id,
                        artifact_dir=req.artifact_dir,
                        timeout=float(req.poll_timeout),
                        poll_interval=req.poll_interval,
                    )
                except Exception:
                    try:
                        adapter.cancel(job_id)
                    except Exception:
                        logger.warning("failed to cancel rolled-back Slurm job %s", job_id, exc_info=True)
                    raise
                return {
                    "job_id": job_id,
                    "servable_url": servable_url,
                    "engine": req.engine,
                    "model_id": req.model_id,
                    "error": "",
                }

            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(_deploy),
                    timeout=float(req.poll_timeout) + 30.0,
                )
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="Slurm deployment timed out") from exc
            except Exception as exc:
                raise _slurm_http_error("deployment", exc) from exc

        return await deploy_store.run(
            key=req.idempotency_key,
            payload=req.model_dump(exclude={"idempotency_key"}, mode="json"),
            producer=_run,
        )

    @app.get("/admin/slurm/status")
    async def admin_slurm_status() -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            available = await asyncio.to_thread(adapter.available)
            return {"available": available}
        except Exception as exc:
            raise _slurm_http_error("status", exc) from exc

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
        except Exception as exc:
            raise _slurm_http_error("submit", exc) from exc

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
        except Exception as exc:
            raise _slurm_http_error("job status", exc) from exc

    @app.delete("/admin/slurm/jobs/{job_id}")
    async def admin_slurm_job_cancel(job_id: str) -> dict[str, object]:
        adapter = _make_adapter(app)
        try:
            await asyncio.to_thread(adapter.cancel, job_id)
            return {"cancelled": job_id}
        except Exception as exc:
            raise _slurm_http_error("cancel", exc) from exc

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
        except Exception as exc:
            raise _slurm_http_error("jobs", exc) from exc

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
        except Exception as exc:
            raise _slurm_http_error("job cost", exc) from exc
