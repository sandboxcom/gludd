from __future__ import annotations

import logging
import os
from typing import Any, cast

from fastapi import FastAPI, HTTPException

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager

logger = logging.getLogger(__name__)


def _get_or_create_extended_subsystems(app: FastAPI) -> dict[str, Any]:
    from general_ludd.daemon import (
        _get_or_create_extended_subsystems as _daemon_ext,
    )
    return _daemon_ext(app)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:
    if not hasattr(app.state, "_compute_deployments"):
        app.state._compute_deployments = {}

    def _get_deployment_manager() -> DeploymentManager:
        # W2.8: reuse a cached manager when present. Use an identity check, not
        # isinstance against DeploymentManager — tests patch that symbol with a
        # MagicMock, and isinstance(x, <MagicMock>) raises TypeError.
        cached = getattr(app.state, "_deployment_manager", None)
        if cached is not None:
            return cast("DeploymentManager", cached)
        secrets_resolver = getattr(app.state, "_secrets_resolver", None)
        pdd = os.path.join(
            os.path.expanduser("~/.local/share/general-ludd"),
            "deployments",
        )
        os.makedirs(pdd, exist_ok=True)
        mgr = DeploymentManager(
            secrets_resolver=secrets_resolver,
            working_dir=pdd,
        )
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
            endpoint_id=endpoint_id, url=url,
            model=req.get("model", ""), gpu_type=req.get("gpu_type", ""),
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
            provider=provider, gpu_type=gpu_type, model_name=model_name,
            engine=engine, region=req.get("region"), spot=req.get("spot", True),
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

        app.state._compute_deployments[instance.instance_id] = instance
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
        # W2.3 (C5): destroy refuses an instance_id with no deployment record.
        # Surface that as a 404 (unknown), terraform errors as 500.
        if mgr.get_deployment(instance_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown instance_id {instance_id}: no deployment record",
            )
        try:
            await mgr.destroy(instance_id)
        except Exception as exc:
            logger.exception("Destroy failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        app.state._compute_deployments.pop(instance_id, None)
        return {"destroyed": instance_id}

    def _resource_dict(r: Any) -> dict[str, Any]:
        return {
            "source": r.source.value,
            "id": r.id,
            "region": r.region,
            "gpu_type": r.gpu_type.value if r.gpu_type else None,
            "gpu_count": r.gpu_count,
            "vcpu": r.vcpu,
            "mem_gb": r.mem_gb,
            "cost_per_hour": r.cost_per_hour,
            "available": r.available,
            "endpoint_url": r.endpoint_url,
            "labels": r.labels,
        }

    def _result_dict(res: Any) -> dict[str, Any]:
        return {
            "source": res.source.value,
            "status": res.status.value,
            "from_cache": res.from_cache,
            "count": len(res.resources),
            "error": res.error,
            "resources": [_resource_dict(r) for r in res.resources],
        }

    @app.post("/admin/compute/discover")
    async def admin_compute_discover(req: dict[str, Any] | None = None) -> dict[str, Any]:
        # Never a 500 on offline: providers return structured OFFLINE/
        # PROVIDER_UNAVAILABLE results, surfaced as-is.
        from general_ludd.infra.discovery.base import DiscoverySource

        ext = _get_or_create_extended_subsystems(app)
        svc = ext.get("discovery")
        if svc is None:
            raise HTTPException(status_code=503, detail="discovery service unavailable")
        source_str = (req or {}).get("source") if req else None
        if source_str:
            try:
                source = DiscoverySource(source_str)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"Unknown source: {source_str}"
                ) from None
            res = await svc.discover(source)
            return {"results": [_result_dict(res)]}
        results = await svc.discover_all()
        return {"results": [_result_dict(r) for r in results.values()]}

    @app.get("/admin/compute/discover")
    async def admin_compute_discover_cached() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        svc = ext.get("discovery")
        if svc is None:
            raise HTTPException(status_code=503, detail="discovery service unavailable")
        out = []
        for source in svc.sources:
            cached = svc.cached(source)
            if cached is not None:
                out.append(_result_dict(cached))
        return {"results": out}

    @app.post("/admin/compute/select")
    async def admin_compute_select(req: dict[str, Any]) -> dict[str, Any]:
        import time

        from general_ludd.infra.compute import GPUType
        from general_ludd.infra.discovery.base import WorkSpec
        from general_ludd.infra.discovery.selector import select_resource

        ext = _get_or_create_extended_subsystems(app)
        svc = ext.get("discovery")
        if svc is None:
            raise HTTPException(status_code=503, detail="discovery service unavailable")

        ws_raw = req.get("work_spec") or {}
        gpu_type_str = ws_raw.get("gpu_type")
        gpu_type = None
        if gpu_type_str:
            try:
                gpu_type = GPUType(gpu_type_str)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail=f"Unknown gpu_type: {gpu_type_str}"
                ) from None
        work_spec = WorkSpec(
            model=ws_raw.get("model", ""),
            needs_gpu=bool(ws_raw.get("needs_gpu", False)),
            gpu_type=gpu_type,
            gpu_count=int(ws_raw.get("gpu_count", 0)),
            vcpu=int(ws_raw.get("vcpu", 0)),
            mem_gb=float(ws_raw.get("mem_gb", 0.0)),
            max_cost_usd=ws_raw.get("max_cost_usd"),
            project_id=ws_raw.get("project_id"),
        )
        runtime_hours = float(req.get("runtime_hours", 1.0))

        # Headroom = min(SpendLimiter.remaining, RunBudgetGuard remaining).
        headroom = float("inf")
        spend_limiter = getattr(app.state, "_spend_limiter", None)
        if spend_limiter is not None:
            headroom = min(headroom, spend_limiter.remaining(time.monotonic()))
        guard = getattr(app.state, "_run_budget_guard", None)
        if guard is not None:
            check = guard.check_all_limits(0.0)
            if check.get("allowed"):
                rb = check.get("remaining_budget")
                if isinstance(rb, int | float):
                    headroom = min(headroom, float(rb))
            else:
                headroom = 0.0

        candidates = svc.all_cached_resources()
        pick = select_resource(
            work_spec,
            candidates,
            headroom,
            spend_limiter=spend_limiter,
            runtime_hours=runtime_hours,
        )
        if pick is None:
            return {"selected": None, "reason": "no budget-fitting resource"}
        reg = svc.auto_register(pick, work_spec)
        return {"selected": _resource_dict(pick), "registration": reg}

    @app.get("/api/deployments")
    async def list_deployments() -> dict[str, Any]:
        # W2.3 (M2): expose the persisted deployment registry.
        mgr = _get_deployment_manager()
        records = mgr.list_deployments()
        return {
            "deployments": [
                {
                    "instance_id": r.instance_id,
                    "provider": r.provider,
                    "model_name": r.model_name,
                    "state": r.state,
                    "ip_address": r.ip_address,
                    "endpoint_url": r.endpoint_url,
                    "working_dir": r.working_dir,
                    "created_at": r.created_at.isoformat(),
                }
                for r in records
            ],
            "count": len(records),
        }
