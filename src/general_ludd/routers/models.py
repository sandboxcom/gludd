from __future__ import annotations

import json
import os
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request

from general_ludd.code_intelligence.callgraph import CallGraph
from general_ludd.code_intelligence.complexity_scorer import CodeComplexityScorer
from general_ludd.code_intelligence.extractor import ASTBlockExtractor
from general_ludd.code_intelligence.search import CodeSearch
from general_ludd.daemon import (
    AddModelRequest,
    ModelSearchRequest,
    _get_or_create_extended_subsystems,
    _get_or_create_subsystems,
)
from general_ludd.db.repository import BenchmarkRepository
from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
from general_ludd.models.auto_configurator import AutoConfigurator, ModelPrioritizer
from general_ludd.models.gateway import ModelGateway
from general_ludd.models.langgraph_gateway import LangGraphGateway
from general_ludd.models.openrouter_discovery import OpenRouterScraper
from general_ludd.models.provider_presets import (
    detect_credential_alias,
    get_provider_preset,
    list_configured_providers,
)
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.response_cache import ModelResponseCache
from general_ludd.models.router import ModelRouter
from general_ludd.models.timeout_detector import ModelHealthTracker
from general_ludd.observability.comparison import ModelComparison
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.security.sanitize import is_path_within


def _workspace_root(app: FastAPI) -> str:
    """The directory attacker-supplied code paths are confined to.

    Prefers GLUDD_WORKSPACE, then the daemon's configured workspace root, then
    the current working directory. Pure env/attr read — no I/O, no blocking.
    """
    return (
        os.environ.get("GLUDD_WORKSPACE")
        or getattr(app.state, "_workspace_root", None)
        or os.getcwd()
    )


def _allowed_code_roots(app: FastAPI) -> list[str]:
    """All roots inside which a code-analysis path is permitted.

    Includes the workspace root AND the system temp directory so tests (and
    legit callers that score files in tmpdir) work without being blocked.
    Out-of-bounds paths (e.g. /etc, /root, ~/.ssh) are still rejected.
    """
    import tempfile

    roots = [_workspace_root(app), tempfile.gettempdir()]
    # Also honour the real path so macOS /tmp -> /private/var/... resolves.
    roots += [os.path.realpath(r) for r in roots]
    return [r for r in roots if r]


def _confined_code_path(app: FastAPI, path: str) -> str:
    """Validate ``path`` is inside an allowed root or raise 422.

    Refuses ``../`` escapes and paths outside the workspace root or the system
    temp directory so /admin/code/* cannot be used to read arbitrary files
    (e.g. /etc/passwd, ~/.ssh/id_rsa) off the host.
    """
    if not path:
        raise HTTPException(
            status_code=422,
            detail=f"path must be inside the workspace root: {path!r}",
        )
    roots = _allowed_code_roots(app)
    if not any(is_path_within(path, root) for root in roots):
        raise HTTPException(
            status_code=422,
            detail=f"path must be inside the workspace root: {path!r}",
        )
    # Return the realpath of the candidate for callers that open the file.
    return os.path.realpath(path)


async def _parse_request_body(request: Request) -> dict[str, Any]:
    body = await request.json() if hasattr(request, "json") else {}
    if isinstance(body, str):
        body = json.loads(body)
    return body


def _serialize_discovered_profile(p: dict[str, Any], *, include_enabled: bool = False) -> dict[str, Any]:
    result = {
        "model_profile_id": p["model_profile_id"],
        "model_name": p["model_name"],
        "display_name": p.get("display_name", p["model_name"]),
        "cost_per_input_token": p["cost_per_input_token"],
        "cost_per_output_token": p["cost_per_output_token"],
        "context_window": p["context_window"],
        "is_free": p.get("is_free", False),
        "role_names": p["role_names"],
        "quality_class": p["quality_class"],
    }
    if include_enabled:
        result["enabled"] = p.get("enabled", True)
    return result


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/models")
    async def admin_add_model(req: AddModelRequest) -> dict[str, Any]:
        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_model_gateway") or app.state._model_gateway is None:
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            # H12 (W3.10): pass metrics_collector from app.state so API-driven
            # model calls are visible to the cost/metrics subsystem.
            metrics_collector = getattr(app.state, "_metrics_collector", None)
            app.state._model_gateway = ModelGateway(
                provider_registry=ProviderRegistry(),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
                metrics_collector=metrics_collector,
            )
        gateway: ModelGateway = app.state._model_gateway
        profile = gateway.add_profile(
            model_id=req.model_id,
            provider=req.provider,
            model=req.model,
            api_key_env=req.api_key_env,
            api_base_alias=req.api_base_alias,
        )
        return {"model_id": req.model_id, "profile": profile.model_dump()}

    @app.delete("/admin/models/{model_id}")
    async def admin_remove_model(model_id: str) -> dict[str, Any]:
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            app.state._model_gateway.remove_profile(model_id)
        return {"removed": model_id}

    @app.post("/admin/models/discover")
    async def admin_models_discover(
        provider: str = "openrouter",
    ) -> dict[str, Any]:
        configured = list_configured_providers()
        if provider not in configured and provider != "openrouter":
            msg = f"Provider '{provider}' not configured (missing credentials)"
            return {"success": False, "error": msg, "configured": configured}

        scraper = OpenRouterScraper()
        if detect_credential_alias(provider):
            import os

            preset = get_provider_preset(provider)
            env_var = preset["credential_env_var"] if preset else "OPENROUTER_API_KEY"
            scraper._api_key = os.environ.get(env_var, None)
        scraped = await scraper.fetch_models()
        configurator = AutoConfigurator()
        profiles = configurator.generate_profiles(provider, scraped)
        prioritizer = ModelPrioritizer()
        ranked = prioritizer.rank(profiles)

        app.state._auto_configurator = configurator
        app.state._scraper = scraper
        app.state._discovered_profiles = profiles

        return {
            "success": True,
            "provider": provider,
            "discovered_count": len(scraped),
            "generated_profiles": len(profiles),
            "models": [_serialize_discovered_profile(p) for p in ranked],
        }

    @app.get("/admin/models/discovered")
    async def admin_models_discovered() -> dict[str, Any]:
        profiles = getattr(app.state, "_discovered_profiles", None)
        if profiles is None:
            return {"profiles": []}
        return {
            "profiles": [_serialize_discovered_profile(p, include_enabled=True) for p in profiles]
        }

    @app.get("/admin/observability/comparison")
    async def admin_observability_comparison(
        task_type: str | None = None,
        sort_by: str = "composite",
    ) -> dict[str, Any]:
        # H1-residual (W3.10): lifespan sets _session_factory, never _session.
        # Reading _session always returned "No DB session available".
        session_factory = getattr(app.state, "_session_factory", None)
        if session_factory is None:
            return {"rankings": [], "summary": "No DB session available"}
        async with session_factory() as session:
            repo = BenchmarkRepository(session)
            comparison = ModelComparison(benchmark_repo=repo)
            return await comparison.compare_models(task_type=task_type, sort_by=sort_by)

    @app.post("/admin/code/blocks")
    async def admin_code_blocks(request: Request) -> dict[str, Any]:
        body = await _parse_request_body(request)
        source = body.get("source", "")
        language = body.get("language", "python")
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        return {"blocks": blocks, "count": len(blocks)}

    @app.get("/admin/code/graph")
    async def admin_code_graph(source: str = "", language: str = "python") -> dict[str, Any]:
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        graph = CallGraph()
        graph.build_from_blocks(blocks)
        return graph.to_dict()

    @app.get("/admin/code/search")
    async def admin_code_search(
        source: str = "",
        query: str = "",
        type_filter: str | None = None,
        language: str = "python",
    ) -> dict[str, Any]:
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        searcher = CodeSearch(blocks)
        results = searcher.search(query=query, type_filter=type_filter)
        return {"results": results, "count": len(results)}

    @app.get("/admin/models")
    async def admin_list_models() -> dict[str, Any]:
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            profiles = app.state._model_gateway.list_profiles()
            return {"profiles": [p.model_dump() for p in profiles]}
        return {"profiles": []}

    @app.get("/admin/models/health")
    async def admin_models_health() -> dict[str, Any]:
        if hasattr(app.state, "_health_tracker") and app.state._health_tracker is not None:
            tracker = app.state._health_tracker
            if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
                profiles = app.state._model_gateway.list_profiles()
                return {"health": [tracker.get_health(p.model_profile_id) for p in profiles]}
            return {"health": []}
        return {"health": []}

    @app.post("/admin/models/search")
    async def admin_models_search(req: ModelSearchRequest) -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        results = ext["model_registry"].search(query=req.query, limit=req.limit)
        return {
            "results": [
                {
                    "model_id": r.model_id,
                    "author": r.author,
                    "downloads": r.downloads,
                    "tags": r.tags,
                    "pipeline_tag": r.pipeline_tag,
                    "library_name": r.library_name,
                }
                for r in results
            ]
        }

    @app.get("/admin/models/downloaded")
    async def admin_models_downloaded() -> dict[str, Any]:
        ext = _get_or_create_extended_subsystems(app)
        models = ext["model_registry"].list_downloaded()
        return {
            "models": [
                {
                    "model_id": m.model_id,
                    "local_path": m.local_path,
                    "engine": m.engine,
                    "size_bytes": m.size_bytes,
                }
                for m in models
            ]
        }

    @app.post("/admin/local-inference/start")
    async def admin_local_inference_start(payload: dict[str, Any]) -> dict[str, Any]:
        if not hasattr(app.state, "_local_inference") or app.state._local_inference is None:
            subsys = _get_or_create_subsystems(app)
            app.state._local_inference = LocalInferenceManager(event_bus=subsys["bus"])
        manager: LocalInferenceManager = app.state._local_inference
        config = LocalServerConfig(
            engine=payload.get("engine", "vllm"),
            model_path=payload.get("model_path", ""),
            model_name=payload.get("model_name", ""),
            host=payload.get("host", "localhost"),
            port=payload.get("port", 8001),
            gpu_layers=payload.get("gpu_layers", -1),
            context_size=payload.get("context_size", 4096),
        )
        server = manager.create_server(config)
        await manager.start_server(server.server_id)
        return {
            "server_id": server.server_id,
            "engine": config.engine,
            "model": config.model_path or config.model_name,
            "endpoint_url": server.endpoint_url,
            "status": server.status,
        }

    @app.post("/admin/code/complexity")
    async def admin_code_complexity(request: Request) -> dict[str, Any]:
        body = await _parse_request_body(request)
        path = body.get("path", "")
        safe_path = _confined_code_path(app, path)
        scorer = CodeComplexityScorer()
        score = scorer.score_file(safe_path)
        task_type = scorer.suggest_task_type(score)
        return {
            "score": score.model_dump(),
            "suggested_task_type": task_type.value,
        }

    @app.post("/admin/code/suggest-model")
    async def admin_code_suggest_model(request: Request) -> dict[str, Any]:
        body = await _parse_request_body(request)
        path = body.get("path", "")
        safe_path = _confined_code_path(app, path)
        scorer = CodeComplexityScorer()
        score = scorer.score_file(safe_path)
        task_type = scorer.suggest_task_type(score)

        recommendation: dict[str, Any] = {
            "selected_prompt_profile_id": None,
            "selected_model_profile_id": "default",
            "composite_score": 0.0,
            "estimated_cost_usd": 0.0,
            "sample_count": 0,
            "fallback": True,
            "reason": "insufficient_historical_data",
        }

        try:
            router = AdaptiveRouter()
            decision = await router.route(task_type)
            recommendation = {
                "selected_prompt_profile_id": decision.selected_prompt_profile_id,
                "selected_model_profile_id": decision.selected_model_profile_id,
                "composite_score": decision.composite_score,
                "estimated_cost_usd": decision.estimated_cost_usd,
                "sample_count": max(decision.sample_count, 1) if not decision.fallback else 0,
                "fallback": decision.fallback,
                "reason": decision.reason,
            }
        except Exception:
            pass

        return {
            "path": path,
            "complexity": score.model_dump(),
            "suggested_task_type": task_type.value,
            "model_recommendation": recommendation,
        }

    @app.post("/admin/models/call")
    async def admin_models_call(request: Request) -> dict[str, Any]:
        """W6.2: model generation endpoint for Ansible modules and external callers.

        Request body:
          prompt: str (required)
          system: str (optional — system prompt; prepended as a system message)
          model_profile: str (optional — explicit profile ID)
          route_task_type: str (optional — adaptive routing by task type)
          max_tokens: int (optional, default 2048)
          response_format / response_schema: optional — BEST-EFFORT structured
            output. When present the handler appends a light-touch JSON nudge to
            the system message; it does NOT enable hard provider JSON-mode (the
            gateway exposes none), so callers must still tolerate non-JSON.

        Unknown body keys (e.g. ``options`` sent by gludd_langgraph_decision)
        are tolerated — the body is parsed as a plain dict, never a strict
        pydantic model, so extra fields can never trigger a 422.

        Auth: same PSK as other admin routes (enforced by middleware).
        """
        body = await _parse_request_body(request)

        prompt: str = body.get("prompt", "")
        if not prompt:
            raise HTTPException(status_code=422, detail="prompt is required")

        # Optional system prompt — the langchain/langgraph Ansible modules POST
        # this so their steering instructions reach the model server-side.
        system_prompt = body.get("system")
        system_text: str = system_prompt if isinstance(system_prompt, str) else ""

        # Best-effort structured-output nudge. The gateway has no hard JSON mode,
        # so when a response_format/response_schema is supplied we append a
        # prompt-level instruction to the system message asking for JSON. Keep it
        # safe: never reject, never assume the model honours it.
        response_format = body.get("response_format")
        response_schema = body.get("response_schema")
        _wants_json = (
            (isinstance(response_format, str) and response_format.strip().lower() == "json")
            or response_schema is not None
        )
        if _wants_json:
            _nudge = (
                "Respond ONLY with a single valid JSON value and no surrounding "
                "prose or Markdown code fences."
            )
            if response_schema is not None:
                try:
                    _schema_json = json.dumps(response_schema, sort_keys=True)
                except (TypeError, ValueError):
                    _schema_json = ""
                if _schema_json:
                    _nudge += f" The JSON must match this schema: {_schema_json}."
            system_text = f"{system_text}\n\n{_nudge}".strip() if system_text else _nudge

        model_profile_id: str | None = body.get("model_profile")
        route_task_type: str | None = body.get("route_task_type")
        # max_tokens available for future use when gateway exposes token limits per-call
        _max_tokens: int = int(body.get("max_tokens", 2048))
        del _max_tokens  # currently unused — call_model controls this via profile config

        # B5: budget gate — fail-closed when guard exhausted or degraded startup.
        _BUDGET_UNSET = object()  # sentinel: attr absent (degraded startup)
        _budget_guard = getattr(app.state, "_budget_guard", _BUDGET_UNSET)
        _fail_closed_degraded = os.environ.get(
            "GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if _budget_guard is _BUDGET_UNSET and _fail_closed_degraded:
            raise HTTPException(
                status_code=503,
                detail="budget guard unavailable (degraded startup); GLUDD_BUDGET_FAIL_CLOSED_DEGRADED=1",
            )
        _guard_active = (
            _budget_guard is not _BUDGET_UNSET
            and _budget_guard is not None
            and hasattr(_budget_guard, "check_all_limits")
        )
        if _guard_active:
            try:
                _verdict = cast(Any, _budget_guard).check_all_limits(estimated_cost=0.0)
            except Exception as _exc:
                raise HTTPException(
                    status_code=503, detail=f"budget check raised: {_exc}"
                ) from _exc
            if not isinstance(_verdict, dict) or not _verdict.get("allowed", False):
                _reason = (
                    _verdict.get("reason", "budget exhausted")
                    if isinstance(_verdict, dict)
                    else "non-dict"
                )
                raise HTTPException(
                    status_code=429, detail=f"budget exhausted: {_reason}"
                )

        # Resolve the gateway — use app.state if available, else build a minimal one
        gateway: ModelGateway | None = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            subsys = _get_or_create_subsystems(app)
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            gateway = ModelGateway(
                provider_registry=ProviderRegistry(),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
            )
            app.state._model_gateway = gateway

        # Adaptive routing if requested
        resolved_profile: str | None = model_profile_id
        if route_task_type and not resolved_profile:
            try:
                from general_ludd.schemas.benchmark import TaskType
                from general_ludd.scoring.router import AdaptiveRouter
                try:
                    task_type = TaskType(route_task_type)
                except ValueError:
                    task_type = TaskType.FEATURE
                _router = AdaptiveRouter()
                decision = await _router.route(task_type)
                resolved_profile = decision.selected_model_profile_id
            except Exception:
                resolved_profile = None  # fall back to gateway default

        # Determine which profile to use
        available_profiles = gateway.list_profiles()
        used_profile_id: str
        if resolved_profile:
            used_profile_id = resolved_profile
        elif available_profiles:
            used_profile_id = available_profiles[0].model_profile_id
        else:
            # No profiles configured — return a clear error
            raise HTTPException(
                status_code=503,
                detail="No model profiles configured. Add a profile via POST /admin/models first.",
            )

        messages: list[dict[str, str]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt})

        try:
            import asyncio
            response = await asyncio.to_thread(
                gateway.call_model,
                used_profile_id,
                messages,
            )
            return {
                "text": response.content,
                "model_profile_id": used_profile_id,
                "usage": dict(response.usage_metadata) if response.usage_metadata else {},
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"model call failed: {exc}") from exc

    @app.post("/admin/models/workflow")
    async def admin_models_workflow(request: Request) -> dict[str, Any]:
        """Run the multi-step LangGraph workflow over a chat-message list.

        Mirrors /admin/models/call's auth (PSK middleware) and budget pre-check,
        but routes the call through LangGraphGateway so callers (e.g. the new
        Ansible langchain/langgraph modules) get classify→select→generate→review
        with quality-gated retries.

        Request body:
          messages: list[{role, content}]  (required)
          profile_id: str | null            (default model profile)
          work_type: str | null             (adaptive-routing task type hint)
          max_retries: int = 2
          quality_threshold: float = 0.6
          enable_graph: bool = true

        Response: the LangGraphGateway result dict
          {content, model, prompt, quality_score, retries, warnings}.

        Auth: same PSK as other admin routes (enforced by middleware).
        """
        body = await _parse_request_body(request)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(
                status_code=422, detail="messages must be a non-empty list"
            )

        profile_id: str | None = body.get("profile_id")
        work_type: str | None = body.get("work_type")
        try:
            max_retries = int(body.get("max_retries", 2))
            quality_threshold = float(body.get("quality_threshold", 0.6))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid numeric parameter: {exc}"
            ) from exc
        enable_graph = bool(body.get("enable_graph", True))

        # B5: budget gate — mirror /admin/models/call exactly (fail-closed when
        # the guard is exhausted or startup degraded).
        _BUDGET_UNSET = object()  # sentinel: attr absent (degraded startup)
        _budget_guard = getattr(app.state, "_budget_guard", _BUDGET_UNSET)
        _fail_closed_degraded = os.environ.get(
            "GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if _budget_guard is _BUDGET_UNSET and _fail_closed_degraded:
            raise HTTPException(
                status_code=503,
                detail="budget guard unavailable (degraded startup); GLUDD_BUDGET_FAIL_CLOSED_DEGRADED=1",
            )
        _guard_active = (
            _budget_guard is not _BUDGET_UNSET
            and _budget_guard is not None
            and hasattr(_budget_guard, "check_all_limits")
        )
        if _guard_active:
            try:
                _verdict = cast(Any, _budget_guard).check_all_limits(estimated_cost=0.0)
            except Exception as _exc:
                raise HTTPException(
                    status_code=503, detail=f"budget check raised: {_exc}"
                ) from _exc
            if not isinstance(_verdict, dict) or not _verdict.get("allowed", False):
                _reason = (
                    _verdict.get("reason", "budget exhausted")
                    if isinstance(_verdict, dict)
                    else "non-dict"
                )
                raise HTTPException(
                    status_code=429, detail=f"budget exhausted: {_reason}"
                )

        # Resolve the gateway — use app.state if available, else build a minimal
        # one (same construction path as /admin/models/call).
        gateway: ModelGateway | None = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            subsys = _get_or_create_subsystems(app)
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            gateway = ModelGateway(
                provider_registry=ProviderRegistry(),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
            )
            app.state._model_gateway = gateway

        # call_model is synchronous on ModelGateway; LangGraphGateway invokes
        # call_model_fn as `await fn(profile_id=..., messages=...)`, so wrap the
        # blocking call in a thread (mirrors /admin/models/call).
        import asyncio

        _gateway = gateway

        async def _call_model_fn(profile_id: str, messages: list[Any]) -> Any:
            return await asyncio.to_thread(_gateway.call_model, profile_id, messages)

        gw = LangGraphGateway(
            call_model_fn=_call_model_fn,
            adaptive_router=getattr(app.state, "_adaptive_router", None),
            scoring_engine=getattr(app.state, "_scoring_engine", None),
            max_retries=max_retries,
            quality_threshold=quality_threshold,
            enable_graph=enable_graph,
        )

        try:
            result = await gw.call(
                messages,
                task_context={"work_type": work_type},
                profile_id=profile_id or "default",
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": str(exc)}) from exc
        return result
