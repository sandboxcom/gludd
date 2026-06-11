from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.infra.compute import (
    ComputeConfig, ComputeInstance, ComputeProvider, GPUType, InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager

logger = logging.getLogger(__name__)


def _get_or_create_extended_subsystems(app: FastAPI) -> dict[str, Any]:
    from general_ludd.daemon import (
        _get_or_create_extended_subsystems as _daemon_ext,
    )
    return _daemon_ext(app)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    _deployments: dict[str, ComputeInstance] = {}

    def _get_deployment_manager() -> DeploymentManager:
        cached = getattr(app.state, "_deployment_manager", None)
        if cached is not None:
            return cached
        secrets_resolver = getattr(app.state, "_secrets_resolver", None)
        pdd = os.path.join(
            os.path.expanduser("~/.local/share/general-ludd"),
            "deployments",
        )
        os.makedirs(pdd, exist_ok=True)
        mgr = DeploymentManager(
            secrets_resolver=secrets_resolver,
        )
        mgr.private_data_dir = pdd
        app.state._deployment_manager = mgr
        return mgr

    @app.get("/admin/compute/utilization")
    async def admin_compute_utilization() -> dict[str, Any]:
        from typing import cast
        ext = _get_or_create_extended_subsystems(app)
        return cast(dict[str, Any], ext["utilization"].get_utilization_report())

    @app.get("/admin/compute/endpoints")
    async def admin_compute_endpoints() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        endpoints = ext["utilization"].list_endpoints()
        return {
            "endpoints": [
                {
                    "endpoint_id": e.endpoint_id,
                    "url": e.url,
                    "model": e.model,
                    "utilization_pct": e.utilization * 100,
                    "current_load": e.current_load,
                    "max_concurrent": e.max_concurrent,
                    "available_slots": e.available_slots,
                    "active": e.active,
                }
                for e in endpoints
            ]
        }

    @app.post("/admin/compute/endpoints")
    async def admin_register_compute_endpoint(req: dict[str, Any]) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        endpoint_id = req.get("endpoint_id", "")
        url = req.get("url", "")
        if not endpoint_id or not url:
            raise HTTPException(status_code=422, detail="endpoint_id and url required")
        ep = ext["utilization"].register_endpoint(
            endpoint_id=endpoint_id,
            url=url,
            model=req.get("model", ""),
            gpu_type=req.get("gpu_type", ""),
            gpu_count=req.get("gpu_count", 1),
            max_concurrent=req.get("max_concurrent", 4),
        )
        return {"endpoint_id": ep.endpoint_id, "url": ep.url, "model": ep.model}

    @app.delete("/admin/compute/endpoints/{endpoint_id}")
    async def admin_unregister_compute_endpoint(endpoint_id: str) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        ext["utilization"].unregister_endpoint(endpoint_id)
        return {"removed": endpoint_id}

    @app.post("/admin/compute/deploy")
    async def admin_compute_deploy(req: dict[str, Any]) -> dict[str, Any]:
        provider_str = req.get("provider", "")
        gpu_str = req.get("gpu_type", "")
        model_name = req.get("model_name", "")
        if not provider_str or not gpu_str or not model_name:
            raise HTTPException(
                status_code=422,
                detail="provider, gpu_type, and model_name are required",
            )
        try:
            provider = ComputeProvider(provider_str)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown provider: {provider_str}") from None
        try:
            gpu_type = GPUType(gpu_str)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown GPU type: {gpu_str}") from None
        try:
            engine = InferenceEngine(req.get("engine", "vllm"))
        except ValueError:
            engine = InferenceEngine.VLLM

        config = ComputeConfig(
            provider=provider,
            gpu_type=gpu_type,
            model_name=model_name,
            engine=engine,
            region=req.get("region"),
            spot=req.get("spot", True),
            max_cost_usd=req.get("max_cost_usd", 10.0),
            timeout_minutes=req.get("timeout_minutes", 60.0),
            disk_size_gb=req.get("disk_size_gb", 100),
            gpu_count=req.get("gpu_count", 1),
            deploy_type=req.get("deploy_type", "vm"),
            container_image=req.get("container_image"),
            provider_auth_aliases=req.get("provider_auth_aliases"),
        )

        mgr = _get_deployment_manager()
        try:
            instance = await mgr.deploy(config)
        except Exception as exc:
            logger.exception("Deploy failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        _deployments[instance.instance_id] = instance
        return {
            "instance_id": instance.instance_id,
            "provider": instance.provider.value,
            "status": instance.status,
            "ip_address": instance.ip_address,
            "port": instance.port,
            "gpu_type": instance.gpu_type.value,
            "endpoint_url": instance.endpoint_url,
        }

    @app.delete("/admin/compute/destroy/{instance_id}")
    async def admin_compute_destroy(instance_id: str) -> dict[str, Any]:
        mgr = _get_deployment_manager()
        try:
            await mgr.destroy(instance_id)
        except Exception as exc:
            logger.exception("Destroy failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        _deployments.pop(instance_id, None)
        return {"destroyed": instance_id}
