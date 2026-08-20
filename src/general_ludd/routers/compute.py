"""Authenticated compute deployment and utilization routes."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import cast

from fastapi import Depends, FastAPI, HTTPException

from general_ludd.infra.azure_accelerator import (
    build_default_azure_preflight,
    resolve_accelerator,
)
from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deploy_precheck import precheck
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.infra.discovery import LocalProbe, discover_all
from general_ludd.infra.model_deploy_check import Finding
from general_ludd.infra.providers import ProviderRegistry
from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.models.deployment_health import DeploymentHealthChecker
from general_ludd.security.capability_guard import RequireCapability

logger = logging.getLogger(__name__)


def _finding_to_dict(f: Finding) -> dict[str, object]:
    """Serialize a MisconfigDetector Finding for a JSON response body."""
    return {
        "rule_id": f.rule_id,
        "severity": f.severity,
        "engine": f.engine,
        "message": f.message,
        "remediation": f.remediation,
        "evidence": f.evidence,
    }


def _get_health_checker(app: FastAPI) -> DeploymentHealthChecker | None:
    """Return the wired DeploymentHealthChecker, or None if unavailable.

    The self-healing router (set up by the daemon) owns the checker; mirror the
    accessor in routers/deployments.py without importing its symbols. Fully
    defensive — any missing attribute degrades to None so the deploy path never
    fails because health tracking is absent.
    """
    router = getattr(app.state, "_deployment_health_router", None)
    if router is None:
        return None
    return getattr(router, "health_checker", None)


def _get_or_create_extended_subsystems(app: FastAPI) -> dict[str, object]:
    from general_ludd.daemon import (
        _get_or_create_extended_subsystems as _daemon_ext,
    )

    return _daemon_ext(app)


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register compute lifecycle routes on ``app``."""
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
            session_factory=getattr(app.state, "_session_factory", None),
            worker_id=f"{os.environ.get('GLUDD_WORKER_ID', 'router')}-{os.getpid()}",
        )
        app.state._deployment_manager = mgr
        return mgr

    @app.get("/admin/compute/utilization")
    async def admin_compute_utilization() -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        util = cast(UtilizationTracker, ext["utilization"])
        return cast(dict[str, object], util.get_utilization_report())

    @app.get("/admin/compute/endpoints")
    async def admin_compute_endpoints() -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        util = cast(UtilizationTracker, ext["utilization"])
        endpoints = util.list_endpoints()
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

    @app.post("/admin/compute/azure/preflight")
    async def admin_compute_azure_preflight(
        req: dict[str, object],
    ) -> dict[str, object]:
        gpu_raw = cast(str, req.get("gpu_type", ""))
        region = cast(str, req.get("region", "eastus")) or "eastus"
        try:
            gpu_type = GPUType(gpu_raw)
            gpu_count = int(cast(int, req.get("gpu_count", 1)))
            # Resolve locally before constructing an SDK client. Invalid shapes
            # must never cause an authentication or network call.
            resolve_accelerator(gpu_type, gpu_count)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "invalid Azure accelerator preflight request: %s",
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=422,
                detail="invalid Azure accelerator preflight request",
            ) from None
        try:
            preflight = build_default_azure_preflight(
                cast("str | None", req.get("subscription_id"))
            )
            result = await asyncio.to_thread(
                preflight.check,
                gpu_type=gpu_type,
                gpu_count=gpu_count,
                location=region,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Azure accelerator preflight unavailable: %s",
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Azure accelerator preflight unavailable",
            ) from exc
        except Exception as exc:
            logger.warning("Azure accelerator preflight failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="Azure accelerator preflight failed",
            ) from exc
        return result.as_dict()

    @app.get("/admin/compute/idle")
    async def admin_compute_idle() -> dict[str, object]:
        daemon_state = getattr(app.state, "daemon_state", None) or {}
        idle_endpoints = daemon_state.get("idle_endpoints", {})
        torn_down = daemon_state.get("torn_down_endpoints", [])
        return {
            "idle_endpoints": list(idle_endpoints.values()),
            "torn_down_endpoints": torn_down,
        }

    @app.post("/admin/compute/endpoints")
    async def admin_register_compute_endpoint(req: dict[str, object]) -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        endpoint_id = cast(str, req.get("endpoint_id", ""))
        url = cast(str, req.get("url", ""))
        if not endpoint_id or not url:
            raise HTTPException(status_code=422, detail="endpoint_id and url required")
        ep = cast(UtilizationTracker, ext["utilization"]).register_endpoint(
            endpoint_id=endpoint_id,
            url=url,
            model=cast(str, req.get("model", "")),
            gpu_type=cast(str, req.get("gpu_type", "")),
            gpu_count=cast(int, req.get("gpu_count", 1)),
            max_concurrent=cast(int, req.get("max_concurrent", 4)),
        )
        return {"endpoint_id": ep.endpoint_id, "url": ep.url, "model": ep.model}

    @app.delete("/admin/compute/endpoints/{endpoint_id}")
    async def admin_unregister_compute_endpoint(endpoint_id: str) -> dict[str, object]:
        ext = _get_or_create_extended_subsystems(app)
        cast(UtilizationTracker, ext["utilization"]).unregister_endpoint(endpoint_id)
        return {"removed": endpoint_id}

    @app.post("/admin/compute/deploy")
    async def admin_compute_deploy(req: dict[str, object]) -> dict[str, object]:
        provider_str = cast(str, req.get("provider", ""))
        gpu_str = cast(str, req.get("gpu_type", ""))
        model_name = cast(str, req.get("model_name", ""))
        if not gpu_str or not model_name:
            raise HTTPException(
                status_code=422,
                detail="gpu_type and model_name are required",
            )
        try:
            gpu_type = GPUType(gpu_str)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown GPU type: {gpu_str}") from None

        if not provider_str:
            resources = discover_all([LocalProbe()])
            if resources:
                resource = resources[0]
                if resource.gpu_count > 0:
                    try:
                        provider_info = ProviderRegistry().get_cheapest_for_gpu(gpu_type)
                        provider = provider_info.provider
                    except KeyError:
                        raise HTTPException(
                            status_code=422,
                            detail=f"No provider supports GPU type: {gpu_str}",
                        ) from None
                else:
                    provider = ComputeProvider.AWS
            else:
                provider = ComputeProvider.AWS
        else:
            try:
                provider = ComputeProvider(provider_str)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Unknown provider: {provider_str}") from None
        try:
            engine = InferenceEngine(cast(str, req.get("engine", "vllm")))
        except ValueError:
            engine = InferenceEngine.VLLM

        config = ComputeConfig(
            provider=provider,
            gpu_type=gpu_type,
            model_name=model_name,
            engine=engine,
            region=cast("str | None", req.get("region")),
            spot=cast(bool, req.get("spot", True)),
            max_cost_usd=cast(float, req.get("max_cost_usd", 10.0)),
            timeout_minutes=cast(float, req.get("timeout_minutes", 60.0)),
            disk_size_gb=cast(int, req.get("disk_size_gb", 100)),
            gpu_count=cast(int, req.get("gpu_count", 1)),
            deploy_type=cast(str, req.get("deploy_type", "vm")),
            container_image=cast("str | None", req.get("container_image")),
            provider_auth_aliases=cast("dict[str, str] | None", req.get("provider_auth_aliases")),
            allowed_cidr=cast(str, req.get("allowed_cidr", "127.0.0.1/32")),
            ssh_public_key_path=cast(
                str,
                req.get("ssh_public_key_path", "~/.ssh/id_ed25519.pub"),
            ),
            hourly_rate_usd=cast("float | None", req.get("hourly_rate_usd")),
        )

        azure_preflight: dict[str, object] | None = None
        if provider == ComputeProvider.AZURE and config.deploy_type == "vm":
            # Mandatory read-only spend gate: resolve the exact fixed GPU shape,
            # then verify subscription SKU eligibility plus both quota tiers.
            # Terraform is not invoked unless this result is ready.
            try:
                resolve_accelerator(gpu_type, config.gpu_count)
                preflight_client = build_default_azure_preflight(
                    cast("str | None", req.get("subscription_id"))
                )
                preflight_result = await asyncio.to_thread(
                    preflight_client.check,
                    gpu_type=gpu_type,
                    gpu_count=config.gpu_count,
                    location=config.region or "eastus",
                )
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Azure accelerator preflight unavailable: %s",
                    exc,
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Azure accelerator preflight unavailable",
                ) from exc
            except Exception as exc:
                logger.warning("Azure accelerator preflight failed: %s", exc)
                raise HTTPException(
                    status_code=502,
                    detail="Azure accelerator preflight failed",
                ) from exc
            azure_preflight = preflight_result.as_dict()
            if not preflight_result.ready:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Azure accelerator preflight blocked deployment",
                        "preflight": azure_preflight,
                    },
                )

        # PRE-DEPLOY: static misconfig precheck. A critical finding refuses the
        # spend (422) unless the caller explicitly forces past it. Non-critical
        # findings ride along on the success response but do not block.
        findings, remediations = precheck(config, req)
        critical = [f for f in findings if f.severity == "critical"]
        if critical and not req.get("force"):
            crit_idx = {id(f) for f in critical}
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "deploy refused: critical misconfiguration detected",
                    "misconfig": [_finding_to_dict(f) for f in critical],
                    "remediations": [remediations[i] for i, f in enumerate(findings) if id(f) in crit_idx],
                    "hint": "resolve the misconfig or resubmit with force=true",
                },
            )

        mgr = _get_deployment_manager()

        # EPHEMERAL ACCOUNT HOOK: if the request (or daemon-wide config) asks
        # for an ephemeral account, provision a fresh one NOW and stamp its
        # credentials onto the deploy env. The account is tracked for
        # automatic cleanup after the workload that used it completes.
        ephemeral_creds: dict[str, object] | None = None
        ephemeral_mgr = getattr(app.state, "_ephemeral_account_manager", None)
        ephemeral_policy = getattr(app.state, "_ephemeral_policy", None)
        if (
            cast(bool, req.get("ephemeral"))
            and ephemeral_mgr is not None
            and ephemeral_policy is not None
            and ephemeral_policy.auto_delete_after_use
            and str(provider) in ("aws", "gcp", "azure")
        ):
            try:
                from general_ludd.account.ephemeral import maybe_create_ephemeral_for_deploy

                _meta: dict[str, object] = {}
                _new_mgr, creds = maybe_create_ephemeral_for_deploy(
                    provider=str(provider),
                    policy=ephemeral_policy,
                    metadata=_meta,
                    manager=ephemeral_mgr,
                )
                if creds is not None:
                    ephemeral_creds = {
                        "account_id": creds.account_id,
                        "provider": creds.provider,
                        "access_key_id": creds.access_key_id,
                        "secret_access_key": creds.secret_access_key,
                        "budget_limit": creds.budget_limit,
                    }
                    # Attach the ephemeral stamp so the EventLoop reconcile
                    # phase can find it after the task completes.
                    app.state._compute_deployments_ephemeral = getattr(app.state, "_compute_deployments_ephemeral", {})
            except Exception as exc:
                logger.warning("ephemeral account creation failed: %s", exc, exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail={"error": "ephemeral account creation failed"},
                ) from exc

        try:
            instance = await mgr.deploy(config)
        except Exception as exc:
            logger.warning("compute deploy failed: %s", exc, exc_info=True)
            # POST-FAILURE: record the failure against the health checker (if
            # wired) and surface the precheck findings alongside the 500 so the
            # operator can diagnose. Never raise a NEW error from this path.
            try:
                checker = _get_health_checker(app)
                if checker is not None:
                    checker.record_failure(model_name, exc, kind="deploy_failure")
            except Exception:  # pragma: no cover - defensive
                logger.debug("failed to record deploy failure", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "compute deploy failed",
                    "misconfig_findings": [_finding_to_dict(f) for f in findings],
                },
            ) from exc

        app.state._compute_deployments[instance.instance_id] = instance

        # Make a newly provisioned server immediately eligible for the existing
        # utilization-aware scheduler. If registration fails, destroy the paid
        # deployment rather than returning a resource gludd cannot use.
        if instance.endpoint_url:
            try:
                ext = _get_or_create_extended_subsystems(app)
                cast(UtilizationTracker, ext["utilization"]).register_endpoint(
                    endpoint_id=instance.instance_id,
                    url=instance.endpoint_url,
                    model=model_name,
                    gpu_type=gpu_type.value,
                    gpu_count=config.gpu_count,
                    max_concurrent=cast(int, req.get("max_concurrent", 4)),
                )
            except Exception as exc:
                app.state._compute_deployments.pop(instance.instance_id, None)
                try:
                    await mgr.destroy(instance.instance_id)
                except Exception:
                    logger.exception(
                        "failed to roll back unregistered deployment %s",
                        instance.instance_id,
                    )
                raise HTTPException(
                    status_code=500,
                    detail="compute endpoint registration failed; deployment rolled back",
                ) from exc

        # EPHEMERAL STAMP: if we provisioned an ephemeral account for this
        # deploy, record the linkage so the EventLoop reconcile phase can find
        # it when the workload completes.
        if ephemeral_creds is not None:
            app.state._compute_deployments_ephemeral[instance.instance_id] = ephemeral_creds

        # Bill-2: wire Slurm cost cap monitor for compute deployments with max_cost_usd
        max_cost = cast(float, req.get("max_cost_usd", 10.0))
        if max_cost and max_cost > 0 and req.get("slurm_job_id"):
            from general_ludd.infra.slurm import SlurmAdapter, SlurmJobConfig, SlurmJobMonitor

            try:
                adapter = SlurmAdapter()
                slurm_config = SlurmJobConfig(
                    max_cost_usd=float(max_cost),
                    hourly_rate_usd=cast("float | None", req.get("hourly_rate_usd")),
                    idle_timeout_minutes=cast("float | None", req.get("idle_timeout_minutes")),
                )
                monitor = SlurmJobMonitor(
                    adapter=adapter,
                    job_id=instance.instance_id,
                    config=slurm_config,
                )
                if not hasattr(app.state, "_slurm_monitors"):
                    app.state._slurm_monitors = {}
                app.state._slurm_monitors[instance.instance_id] = monitor
                monitor.start()
                logger.info(
                    "started Slurm cost-cap monitor for %s (max_cost_usd=%.2f)",
                    instance.instance_id,
                    max_cost,
                )
            except Exception as exc:
                logger.warning(
                    "failed to start Slurm cost-cap monitor for %s: %s",
                    instance.instance_id,
                    exc,
                )

        return {
            "instance_id": instance.instance_id,
            "provider": instance.provider.value,
            "status": instance.status,
            "ip_address": instance.ip_address,
            "port": instance.port,
            "gpu_type": instance.gpu_type.value,
            "endpoint_url": instance.endpoint_url,
            "findings": [_finding_to_dict(f) for f in findings],
            "azure_preflight": azure_preflight,
        }

    @app.delete(
        "/admin/compute/destroy/{instance_id}",
        dependencies=[Depends(RequireCapability(resource="admin:compute", action="destroy"))],
    )
    async def admin_compute_destroy(instance_id: str) -> dict[str, object]:
        mgr = _get_deployment_manager()
        # W2.3 (C5): destroy refuses an instance_id with no deployment record.
        # Surface that as a 404 (unknown), terraform errors as 500.
        if await mgr.get_deployment_shared(instance_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown instance_id {instance_id}: no deployment record",
            )
        try:
            await mgr.destroy(instance_id)
        except Exception as exc:
            logger.warning("compute destroy failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="compute destroy failed") from exc
        app.state._compute_deployments.pop(instance_id, None)
        return {"destroyed": instance_id}

    @app.get("/admin/compute/gpu-metrics")
    async def admin_compute_gpu_metrics() -> dict[str, object]:
        daemon_state = getattr(app.state, "daemon_state", None) or {}
        metrics_list: list[dict[str, float]] = []
        raw = daemon_state.get("_last_gpu_metrics")
        if raw is not None:
            for m in raw:
                if hasattr(m, "as_dict"):
                    metrics_list.append(m.as_dict())
                elif isinstance(m, dict):
                    metrics_list.append(m)
                else:
                    metrics_list.append(
                        {
                            "gpu_sm_util_pct": getattr(m, "gpu_sm_util_pct", 0.0),
                            "gpu_mem_util_pct": getattr(m, "gpu_mem_util_pct", 0.0),
                            "gpu_temp_c": getattr(m, "gpu_temp_c", 0.0),
                            "power_draw_w": getattr(m, "power_draw_w", 0.0),
                            "memory_used_mb": getattr(m, "memory_used_mb", 0.0),
                            "memory_total_mb": getattr(m, "memory_total_mb", 0.0),
                        }
                    )
        ext = _get_or_create_extended_subsystems(app)
        util = cast(UtilizationTracker, ext["utilization"])
        endpoints = util.list_endpoints()
        metrics: dict[str, object] = {}
        result: dict[str, object] = {
            "metrics": metrics,
            "collected_at": daemon_state.get("_last_gpu_metrics_at"),
        }
        for idx, m in enumerate(metrics_list):
            if idx < len(endpoints):
                metrics[endpoints[idx].endpoint_id] = m
            else:
                metrics[f"device_{idx}"] = m
        return result

    @app.get("/admin/compute/gpu-metrics/{endpoint_id}")
    async def admin_compute_gpu_metric_by_endpoint(endpoint_id: str) -> dict[str, object]:
        daemon_state = getattr(app.state, "daemon_state", None) or {}
        raw = daemon_state.get("_last_gpu_metrics")
        ext = _get_or_create_extended_subsystems(app)
        util = cast(UtilizationTracker, ext["utilization"])
        endpoints = util.list_endpoints()
        endpoint_idx: int | None = None
        for idx, ep in enumerate(endpoints):
            if ep.endpoint_id == endpoint_id:
                endpoint_idx = idx
                break
        if endpoint_idx is None or raw is None or endpoint_idx >= len(raw):
            raise HTTPException(
                status_code=404,
                detail=f"No GPU metrics found for endpoint {endpoint_id}",
            )
        m = raw[endpoint_idx]
        if hasattr(m, "as_dict"):
            return {"endpoint_id": endpoint_id, "metrics": m.as_dict()}
        if isinstance(m, dict):
            return {"endpoint_id": endpoint_id, "metrics": dict(m)}
        return {
            "endpoint_id": endpoint_id,
            "metrics": {
                "gpu_sm_util_pct": getattr(m, "gpu_sm_util_pct", 0.0),
                "gpu_mem_util_pct": getattr(m, "gpu_mem_util_pct", 0.0),
                "gpu_temp_c": getattr(m, "gpu_temp_c", 0.0),
                "power_draw_w": getattr(m, "power_draw_w", 0.0),
                "memory_used_mb": getattr(m, "memory_used_mb", 0.0),
                "memory_total_mb": getattr(m, "memory_total_mb", 0.0),
            },
        }

    @app.get("/api/deployments")
    async def list_deployments() -> dict[str, object]:
        # W2.3 (M2): expose the persisted deployment registry.
        mgr = _get_deployment_manager()
        records = await mgr.list_deployments_shared()
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
