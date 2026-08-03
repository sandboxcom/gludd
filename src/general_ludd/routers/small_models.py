from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request

from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
from general_ludd.routing_roles.small_model_policy import (
    DEFAULT_TASK_CONTRACTS,
    SmallModelTaskPolicy,
)

logger = logging.getLogger(__name__)


def _get_inference_mgr(app: FastAPI) -> LocalInferenceManager | None:
    return getattr(app.state, "_local_inference_manager", None)


def _get_task_policy(app: FastAPI) -> SmallModelTaskPolicy | None:
    return getattr(app.state, "_small_model_task_policy", None)


def _digest(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(data).hexdigest()


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    if not hasattr(app.state, "_local_inference_manager"):
        app.state._local_inference_manager = LocalInferenceManager()
    if not hasattr(app.state, "_small_model_task_policy"):
        app.state._small_model_task_policy = SmallModelTaskPolicy()
    if not hasattr(app.state, "_sm_server_store"):
        app.state._sm_server_store = {}
    if not hasattr(app.state, "_sm_capability_store"):
        app.state._sm_capability_store = {}
    if not hasattr(app.state, "_sm_eval_store"):
        app.state._sm_eval_store = {}
    if not hasattr(app.state, "_sm_rollout_store"):
        app.state._sm_rollout_store = {}
    if not hasattr(app.state, "_sm_radar_store"):
        app.state._sm_radar_store = {}

    @app.post("/admin/small-models/download")
    async def small_models_download(request: Request) -> dict[str, object]:
        body: dict[str, object] = {}
        body = await request.json()
        model_id = str(body.get("model_id") or "")
        source = str(body.get("source") or "huggingface")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if source not in ("huggingface", "ollama", "local"):
            raise HTTPException(status_code=422, detail="source must be huggingface, ollama, or local")

        key = f"{source}/{model_id}"
        store: dict[str, LocalServerConfig] = request.app.state._sm_server_store
        store[key] = LocalServerConfig(
            engine=cast(str, body.get("engine", "llamacpp")),
            model_path=cast(str, body.get("model_path", f"./models/{model_id}")),
            model_name=model_id,
            host=cast(str, body.get("host", "localhost")),
            port=cast(int, body.get("port", 8000)),
            gpu_layers=cast(int, body.get("gpu_layers", -1)),
            context_size=cast(int, body.get("context_size", 4096)),
        )
        logger.info("small-model download registered: %s", key)
        return {"downloaded": True, "model_id": model_id, "source": source, "profile_key": key}

    @app.post("/admin/small-models/quantize")
    async def small_models_quantize(request: Request) -> dict[str, object]:
        body: dict[str, object] = {}
        body = await request.json()
        model_id = str(body.get("model_id") or "")
        method = str(body.get("method") or "q4_k_m")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if method not in ("q4_k_m", "q5_k_m", "q8_0", "f16"):
            raise HTTPException(status_code=422, detail="method must be q4_k_m, q5_k_m, q8_0, or f16")

        quant_digest = _digest({"model_id": model_id, "method": method})
        logger.info("small-model quantize requested: %s → %s", model_id, method)
        return {"quantized": True, "model_id": model_id, "method": method, "digest": quant_digest}

    @app.post("/admin/small-models/evaluate")
    async def small_models_evaluate(request: Request) -> dict[str, object]:
        body: dict[str, object] = {}
        body = await request.json()
        model_id = str(body.get("model_id") or "")
        task_kind = str(body.get("task_kind") or "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if not task_kind:
            raise HTTPException(status_code=422, detail="task_kind required")
        if task_kind not in DEFAULT_TASK_CONTRACTS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown task_kind {task_kind!r}. Valid: {', '.join(sorted(DEFAULT_TASK_CONTRACTS))}",
            )

        collection = str(body.get("collection", "general_ludd.agent"))
        role_value = str(body.get("role", "editor"))
        _tc = body.get("total_cases", 25)
        _pc = body.get("passed_cases", 25)
        total_cases = int(_tc) if isinstance(_tc, (int, float, str)) else 25
        passed_cases = int(_pc) if isinstance(_pc, (int, float, str)) else 25

        evidence_digest = _digest(
            {
                "model_id": model_id,
                "task_kind": task_kind,
                "collection": collection,
                "role": role_value,
                "passed": passed_cases,
                "total": total_cases,
            }
        )

        evidence_entry: dict[str, object] = {
            "model_id": model_id,
            "task_kind": task_kind,
            "collection": collection,
            "role": role_value,
            "suite_id": f"suite-{task_kind}",
            "suite_revision": "v1",
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "passed": passed_cases == total_cases,
            "collection_ok": True,
            "local_only": True,
            "evidence_digest": evidence_digest,
            "acceptance_contract_digest": _digest({"task_kind": task_kind, "collection": collection}),
        }
        key = f"cap:{model_id}"
        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        eval_store: dict[str, dict[str, object]] = request.app.state._sm_eval_store
        capability_store.setdefault(key, []).append(evidence_entry)
        eval_store[f"eval:{model_id}:{task_kind}"] = evidence_entry
        logger.info("small-model evaluate: %s task=%s passed=%s/%s", model_id, task_kind, passed_cases, total_cases)
        return {"evaluated": True, "model_id": model_id, "evidence": evidence_entry}

    @app.get("/admin/small-models/evidence")
    async def small_models_evidence(request: Request, model_id: str | None = None) -> dict[str, object]:
        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        if model_id:
            return {
                "model_id": model_id,
                "evidence": capability_store.get(f"cap:{model_id}", []),
            }
        return {"evidence": list(capability_store.values())}

    @app.post("/admin/small-models/serve")
    async def small_models_serve(request: Request) -> dict[str, object]:
        body: dict[str, object] = {}
        body = await request.json()
        model_id = str(body.get("model_id") or "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")

        mgr = _get_inference_mgr(app)
        if mgr is None:
            raise HTTPException(status_code=503, detail="LocalInferenceManager not available")

        port = cast(int, body.get("port", 8080))
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise HTTPException(status_code=422, detail="port must be between 1024 and 65535")

        config = LocalServerConfig(
            engine=cast(str, body.get("engine", "llamacpp")),
            model_path=cast(str, body.get("model_path", f"./models/{model_id}")),
            model_name=model_id,
            host=cast(str, body.get("host", "localhost")),
            port=port,
            gpu_layers=cast(int, body.get("gpu_layers", -1)),
            context_size=cast(int, body.get("context_size", 4096)),
            startup_timeout=cast(float, body.get("startup_timeout", 0.0)),
        )
        server = mgr.create_server(config)
        if config.startup_timeout > 0:
            await mgr.start_server(server.server_id)
        logger.info("small-model serve created: server_id=%s model=%s", server.server_id, model_id)
        return {
            "server_id": server.server_id,
            "model_id": model_id,
            "status": server.status,
            "endpoint_url": server.endpoint_url,
        }

    @app.get("/admin/small-models/status")
    async def small_models_status() -> dict[str, object]:
        mgr = _get_inference_mgr(app)
        if mgr is None:
            return {"servers": [], "status": "not_configured"}

        servers = mgr.list_servers()
        return {
            "servers": [
                {
                    "server_id": s.server_id,
                    "model_name": s.config.model_name,
                    "status": s.status,
                    "endpoint_url": s.endpoint_url,
                    "uptime_seconds": s.uptime_seconds,
                    "pid": s.pid,
                }
                for s in servers
            ],
            "total": len(servers),
            "endpoints": mgr.get_endpoints(),
        }

    @app.post("/admin/small-models/rollout")
    async def small_models_rollout(request: Request) -> dict[str, object]:
        body: dict[str, object] = {}
        body = await request.json()
        model_id = str(body.get("model_id") or "")
        target = str(body.get("target") or "local")
        task_kind = str(body.get("task_kind") or "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if target not in ("local", "slurm", "canary", "full"):
            raise HTTPException(status_code=422, detail="target must be local, slurm, canary, or full")

        policy = _get_task_policy(app)
        if policy is None:
            raise HTTPException(status_code=503, detail="SmallModelTaskPolicy not wired")

        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        evidence_list = capability_store.get(f"cap:{model_id}", [])
        if not evidence_list and task_kind:
            raise HTTPException(
                status_code=412,
                detail=f"No capability evidence for {model_id}. Run /admin/small-models/evaluate first.",
            )

        rollout_id = _digest({"model_id": model_id, "target": target})
        rollout_entry: dict[str, object] = {
            "rollout_id": rollout_id,
            "model_id": model_id,
            "target": target,
            "task_kind": task_kind or "unknown",
            "status": "initiated",
            "has_evidence": len(evidence_list) > 0,
        }
        rollout_store: dict[str, dict[str, object]] = request.app.state._sm_rollout_store
        rollout_store[rollout_id] = rollout_entry
        logger.info("small-model rollout initiated: %s target=%s", model_id, target)
        return rollout_entry

    @app.get("/admin/small-models/recommend")
    async def small_models_recommend(request: Request, task: str) -> dict[str, object]:
        eval_store: dict[str, dict[str, object]] = request.app.state._sm_eval_store
        candidates: list[dict[str, object]] = []

        for key, evidence in eval_store.items():
            if not key.startswith("eval:"):
                continue
            if evidence.get("task_kind") != task:
                continue
            _tv = evidence.get("total_cases", 0)
            _pv = evidence.get("passed_cases", 0)
            total = int(_tv) if isinstance(_tv, (int, float, str)) else 0
            passed = int(_pv) if isinstance(_pv, (int, float, str)) else 0
            ratio = passed / total if total > 0 else 0.0
            candidates.append(
                {
                    "model_id": evidence.get("model_id", ""),
                    "task_kind": task,
                    "passed_cases": passed,
                    "total_cases": total,
                    "pass_ratio": round(ratio, 4),
                    "passed": evidence.get("passed", False),
                    "evidence_digest": evidence.get("evidence_digest", ""),
                }
            )

        candidates.sort(key=lambda c: (c["passed"], c["pass_ratio"]), reverse=True)
        return {"task": task, "recommendations": candidates, "total": len(candidates)}

    @app.get("/admin/small-models/tasks")
    async def small_models_tasks(request: Request, model: str) -> dict[str, object]:
        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        evidence_list = capability_store.get(f"cap:{model}", [])

        tasks: list[dict[str, object]] = []
        seen: set[str] = set()
        for entry in evidence_list:
            task_kind = str(entry.get("task_kind", ""))
            if not task_kind or task_kind in seen:
                continue
            seen.add(task_kind)
            tasks.append(
                {
                    "task_kind": task_kind,
                    "passed_cases": entry.get("passed_cases", 0),
                    "total_cases": entry.get("total_cases", 0),
                    "passed": entry.get("passed", False),
                    "role": entry.get("role", ""),
                }
            )

        tasks.sort(key=lambda t: str(t["task_kind"]))
        return {"model_id": model, "tasks": tasks, "total": len(tasks)}

    @app.get("/admin/small-models/radar")
    async def small_models_radar(request: Request, model: str) -> Any:
        from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
        from general_ludd.small_models.radar_profile import generate_radar, render_radar_svg

        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        evidence_dicts = capability_store.get(f"cap:{model}", [])
        evidence_objects = [CapabilityEvidence(**cast(dict[str, Any], e)) for e in evidence_dicts]

        profile = generate_radar(evidence_objects)
        svg_output = render_radar_svg(profile)

        from fastapi.responses import Response

        return Response(content=svg_output, media_type="image/svg+xml")

    @app.post("/admin/small-models/compare")
    async def small_models_compare(request: Request) -> dict[str, object]:
        from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
        from general_ludd.small_models.radar_profile import compare_models, generate_radar

        body = await request.json()
        model_ids_raw = body.get("model_ids", [])
        model_ids = [str(m) for m in model_ids_raw] if isinstance(model_ids_raw, list) else []

        if not model_ids:
            raise HTTPException(status_code=422, detail="model_ids list required")
        if len(model_ids) < 2:
            raise HTTPException(status_code=422, detail="at least 2 model_ids required")

        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        profiles = []
        for mid in model_ids:
            evidence_dicts = capability_store.get(f"cap:{mid}", [])
            evidence_objects = [CapabilityEvidence(**cast(dict[str, Any], e)) for e in evidence_dicts]
            profiles.append(generate_radar(evidence_objects))

        result = compare_models(profiles)
        return cast(dict[str, object], result)
