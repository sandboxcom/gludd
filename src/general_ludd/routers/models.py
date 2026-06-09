from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from general_ludd.daemon import (
    AddModelRequest,
    ModelSearchRequest,
    _get_or_create_extended_subsystems,
    _get_or_create_subsystems,
)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/models")
    async def admin_add_model(req: AddModelRequest) -> dict[str, Any]:
        from general_ludd.models.gateway import ModelGateway
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.models.router import ModelRouter

        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_model_gateway") or app.state._model_gateway is None:
            from general_ludd.models.response_cache import ModelResponseCache
            from general_ludd.models.timeout_detector import ModelHealthTracker

            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            app.state._model_gateway = ModelGateway(
                provider_registry=ProviderRegistry(),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
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
        from general_ludd.models.auto_configurator import AutoConfigurator, ModelPrioritizer
        from general_ludd.models.openrouter_discovery import OpenRouterScraper
        from general_ludd.models.provider_presets import detect_credential_alias, list_configured_providers

        configured = list_configured_providers()
        if provider not in configured and provider != "openrouter":
            msg = f"Provider '{provider}' not configured (missing credentials)"
            return {"success": False, "error": msg, "configured": configured}

        scraper = OpenRouterScraper()
        if detect_credential_alias(provider):
            import os

            from general_ludd.models.provider_presets import get_provider_preset

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
            "models": [
                {
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
                for p in ranked
            ],
        }

    @app.get("/admin/models/discovered")
    async def admin_models_discovered() -> dict[str, Any]:
        profiles = getattr(app.state, "_discovered_profiles", None)
        if profiles is None:
            return {"profiles": []}
        return {
            "profiles": [
                {
                    "model_profile_id": p["model_profile_id"],
                    "model_name": p["model_name"],
                    "display_name": p.get("display_name", p["model_name"]),
                    "cost_per_input_token": p["cost_per_input_token"],
                    "cost_per_output_token": p["cost_per_output_token"],
                    "context_window": p["context_window"],
                    "is_free": p.get("is_free", False),
                    "role_names": p["role_names"],
                    "quality_class": p["quality_class"],
                    "enabled": p.get("enabled", True),
                }
                for p in profiles
            ]
        }

    @app.get("/admin/observability/comparison")
    async def admin_observability_comparison(
        task_type: str | None = None,
        sort_by: str = "composite",
    ) -> dict[str, Any]:
        from general_ludd.observability.comparison import ModelComparison

        session = getattr(app.state, "_session", None)
        if session is None:
            return {"rankings": [], "summary": "No DB session available"}
        from general_ludd.db.repository import BenchmarkRepository

        repo = BenchmarkRepository(session)
        comparison = ModelComparison(benchmark_repo=repo)
        return await comparison.compare_models(task_type=task_type, sort_by=sort_by)

    @app.post("/admin/code/blocks")
    async def admin_code_blocks(request: Request) -> dict[str, Any]:
        import json

        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

        body = await request.json() if hasattr(request, "json") else {}
        if isinstance(body, str):
            body = json.loads(body)
        source = body.get("source", "")
        language = body.get("language", "python")
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        return {"blocks": blocks, "count": len(blocks)}

    @app.get("/admin/code/graph")
    async def admin_code_graph(source: str = "", language: str = "python") -> dict[str, Any]:
        from general_ludd.code_intelligence.callgraph import CallGraph
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor

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
        from general_ludd.code_intelligence.extractor import ASTBlockExtractor
        from general_ludd.code_intelligence.search import CodeSearch

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
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig

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
