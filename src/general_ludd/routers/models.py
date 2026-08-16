"""Administrative model, inference, and code-intelligence HTTP routes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from general_ludd.cloud.model_sources import (
    DownloadedFile as CloudDownloadedFile,
)
from general_ludd.cloud.model_sources import (
    DownloadError as ModelSourceDownloadError,
)
from general_ludd.cloud.model_sources import (
    ModelSource,
    download_with_fallback,
)
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
from general_ludd.hardware.survey import HardwareInventory
from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
from general_ludd.local_model._local_model_configs import _LOCAL_MODELS
from general_ludd.models.auto_configurator import AutoConfigurator, ModelPrioritizer
from general_ludd.models.gateway import ModelGateway, ModelResponse
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
from general_ludd.quantization.quantize import ModelQuantizer, QuantMethod
from general_ludd.routing_roles.small_model_policy import (
    DEFAULT_TASK_CONTRACTS,
    SmallModelTaskPolicy,
)
from general_ludd.scoring.router import AdaptiveRouter
from general_ludd.security.sanitize import is_path_within
from general_ludd.small_models import ModelDownloader, ModelHashDB
from general_ludd.small_models.download import DownloadedModel, DownloadSource
from general_ludd.small_models.lm_eval_runner import _DEFAULT_TASKS, LMEvalRunner, to_capability_evidence

logger = logging.getLogger(__name__)


class _CheckAllLimitsGuard(Protocol):
    """Structural type for budget guards exposing ``check_all_limits``."""

    def check_all_limits(self, estimated_cost: float = 0.0) -> dict[str, bool | str | float]: ...


# DoS cap: /admin/models/call accepts a caller-supplied max_tokens int that is
# threaded into the budget gate. An absurd value (e.g. 10**18) is a resource /
# cost-estimation DoS; reject anything above this ceiling with HTTP 413.
_MAX_MODELS_CALL_MAX_TOKENS = 1_000_000


def _workspace_root(app: FastAPI) -> str:
    """The directory attacker-supplied code paths are confined to.

    Prefers GLUDD_WORKSPACE_ROOT, then the daemon's configured workspace root, then
    the current working directory. Pure env/attr read — no I/O, no blocking.
    """
    return os.environ.get("GLUDD_WORKSPACE_ROOT") or getattr(app.state, "_workspace_root", None) or os.getcwd()


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


async def _parse_request_body(request: Request) -> dict[str, object]:
    body = await request.json() if hasattr(request, "json") else {}
    if isinstance(body, str):
        body = json.loads(body)
    return body


_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


class _ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"invalid role {v!r}; must be one of {sorted(_VALID_ROLES)}")
        return v


class ChatStreamRequest(BaseModel):
    """Validated request body for the server-sent-event chat endpoint."""

    messages: list[_ChatMessage] = Field(min_length=1, max_length=256)
    model_profile_id: str = "default"
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


def _serialize_discovered_profile(p: dict[str, object], *, include_enabled: bool = False) -> dict[str, object]:
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


_MODEL_VRAM_GB: dict[str, float] = {
    "llama3-8b": 6.0,
    "llama3-70b": 40.0,
    "mistral-7b": 6.0,
    "mixtral-8x7b": 48.0,
    "phi3-mini": 4.0,
    "phi3-medium": 12.0,
    "gemma2-9b": 8.0,
    "gemma2-27b": 24.0,
    "codestral-22b": 18.0,
    "deepseek-coder-7b": 6.0,
    "deepseek-coder-33b": 24.0,
    "qwen2-7b": 6.0,
    "qwen2-72b": 40.0,
    "starcoder2-15b": 12.0,
}


def _get_inference_mgr(app: FastAPI) -> LocalInferenceManager | None:
    return getattr(app.state, "_local_inference_manager", None)


def _get_task_policy(app: FastAPI) -> SmallModelTaskPolicy | None:
    return getattr(app.state, "_small_model_task_policy", None)


def _get_model_quantizer(app: FastAPI) -> ModelQuantizer | None:
    return getattr(app.state, "_sm_model_quantizer", None)


def _models_dir() -> str:
    from general_ludd.small_models.download import DEFAULT_CACHE_DIR

    return os.environ.get("GLUDD_MODELS_DIR", DEFAULT_CACHE_DIR)


def _digest(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(data).hexdigest()


def _compute_size_disk(path: str) -> int:
    p = Path(path)
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def _can_run_local(inventory: HardwareInventory | None, model_name: str) -> bool:
    if inventory is None:
        return False
    required_vram = _MODEL_VRAM_GB.get(model_name.lower(), 4.0)
    gpu_vram = max((g.vram_gb for g in inventory.gpus), default=0.0)
    extra_ram = max(0.0, inventory.total_ram_gb - 2.0)
    return gpu_vram >= required_vram or extra_ram >= required_vram


def _track_router_owned_gateway(
    app: FastAPI,
    gateway: ModelGateway,
) -> ModelGateway:
    """Record a fallback gateway for deterministic application shutdown."""
    owned: list[ModelGateway] = getattr(
        app.state,
        "_models_router_owned_gateways",
        [],
    )
    owned.append(gateway)
    app.state._models_router_owned_gateways = owned
    app.state._model_gateway = gateway
    return gateway


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    """Register model routes and their application-owned resource lifecycle."""
    if not getattr(app.state, "_models_router_shutdown_registered", False):
        app.state._models_router_shutdown_registered = True
        app.state._models_router_owned_gateways = []

        async def _close_router_owned_gateways() -> None:
            owned: list[ModelGateway] = app.state._models_router_owned_gateways
            app.state._models_router_owned_gateways = []
            for gateway in reversed(owned):
                gateway.close()

        app.router.add_event_handler("shutdown", _close_router_owned_gateways)

    if not hasattr(app.state, "_local_inference_manager"):
        app.state._local_inference_manager = LocalInferenceManager(
            ansible_adapter=getattr(app.state, "_runner", None),
        )
    if not hasattr(app.state, "_small_model_task_policy"):
        app.state._small_model_task_policy = SmallModelTaskPolicy()
    if not hasattr(app.state, "_model_downloader"):
        app.state._model_downloader = ModelDownloader(hash_db=ModelHashDB.from_known_models())
    if not hasattr(app.state, "_sm_server_store"):
        app.state._sm_server_store = cast(dict[str, LocalServerConfig], {})
    if not hasattr(app.state, "_sm_capability_store"):
        app.state._sm_capability_store = cast(dict[str, list[dict[str, object]]], {})
    if not hasattr(app.state, "_sm_eval_store"):
        app.state._sm_eval_store = cast(dict[str, dict[str, object]], {})
    if not hasattr(app.state, "_sm_rollout_store"):
        app.state._sm_rollout_store = cast(dict[str, dict[str, object]], {})
    if not hasattr(app.state, "_sm_radar_store"):
        app.state._sm_radar_store = cast(dict[str, object], {})
    if not hasattr(app.state, "_sm_quantize_store"):
        app.state._sm_quantize_store = cast(dict[str, dict[str, object]], {})
    if not hasattr(app.state, "_sm_model_quantizer"):
        app.state._sm_model_quantizer = ModelQuantizer()

    @app.post("/admin/models")
    async def admin_add_model(req: AddModelRequest) -> dict[str, object]:
        subsys = _get_or_create_subsystems(app)
        if not hasattr(app.state, "_model_gateway") or app.state._model_gateway is None:
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            # H12 (W3.10): pass metrics_collector from app.state so API-driven
            # model calls are visible to the cost/metrics subsystem.
            metrics_collector = getattr(app.state, "_metrics_collector", None)
            app.state._model_gateway = ModelGateway(
                # CI-1: use the shared factory for consistency with daemon/worker.
                # No profiles are in scope at this fallback path (profiles are
                # added afterwards via gateway.add_profile), so the registry is
                # empty — equivalent to ProviderRegistry() but built via the factory.
                provider_registry=ProviderRegistry.from_profiles([]),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
                metrics_collector=metrics_collector,
            )
            _track_router_owned_gateway(app, app.state._model_gateway)
        gateway: ModelGateway = app.state._model_gateway
        # A zero-cost profile with api_metered UNSPECIFIED is un-metered by
        # definition (validator rejects enabled + metered + zero cost, and
        # the pinned registration contract accepts such profiles as 200).
        # An explicit api_metered=True keeps the fail-closed 422 contract.
        if req.api_metered is None:
            req.api_metered = not (req.cost_per_input_token == 0.0 and req.cost_per_output_token == 0.0)
        try:
            profile = gateway.add_profile(
                model_id=req.model_id,
                provider=req.provider,
                model=req.model,
                api_key_env=req.api_key_env,
                api_base_alias=req.api_base_alias,
                enabled=req.enabled,
                api_metered=req.api_metered,
                cost_per_input_token=req.cost_per_input_token,
                cost_per_output_token=req.cost_per_output_token,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"model_id": req.model_id, "profile": profile.model_dump()}

    @app.delete("/admin/models/{model_id}")
    async def admin_remove_model(model_id: str) -> dict[str, object]:
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            app.state._model_gateway.remove_profile(model_id)
        return {"removed": model_id}

    @app.post("/admin/models/discover")
    async def admin_models_discover(
        provider: str = "openrouter",
    ) -> dict[str, object]:
        configured = list_configured_providers()
        if provider not in configured and provider != "openrouter":
            msg = f"Provider '{provider}' not configured (missing credentials)"
            return {"success": False, "error": msg, "configured": configured}

        scraper = OpenRouterScraper()
        if detect_credential_alias(provider):
            import os

            preset = get_provider_preset(provider)
            env_var = cast(str, preset["credential_env_var"]) if preset else "OPENROUTER_API_KEY"
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

    @app.post("/admin/models/discover-searx")
    async def admin_models_discover_searx(request: Request) -> dict[str, object]:
        discoverer = getattr(app.state, "_searx_model_discoverer", None)
        if discoverer is None:
            raise HTTPException(status_code=503, detail="SearX model discoverer not wired")
        body = await _parse_request_body(request)
        query = cast(str, body.get("query", "LLM"))
        added = discoverer.discover_now(query)
        return {
            "success": True,
            "discovered_count": added,
            "index_size": discoverer.index_size,
        }

    @app.get("/admin/models/discovered")
    async def admin_models_discovered() -> dict[str, object]:
        profiles = getattr(app.state, "_discovered_profiles", None)
        if profiles is None:
            return {"profiles": []}
        return {"profiles": [_serialize_discovered_profile(p, include_enabled=True) for p in profiles]}

    @app.get("/admin/observability/comparison")
    async def admin_observability_comparison(
        task_type: str | None = None,
        sort_by: str = "composite",
    ) -> dict[str, object]:
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
    async def admin_code_blocks(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        source = cast(str, body.get("source", ""))
        language = cast(str, body.get("language", "python"))
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        return {"blocks": blocks, "count": len(blocks)}

    @app.get("/admin/code/graph")
    async def admin_code_graph(source: str = "", language: str = "python") -> dict[str, object]:
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
    ) -> dict[str, object]:
        extractor = ASTBlockExtractor()
        blocks = extractor.extract_blocks(source, language=language)
        searcher = CodeSearch(blocks)
        results = searcher.search(query=query, type_filter=type_filter)
        return {"results": results, "count": len(results)}

    @app.get("/admin/models")
    async def admin_list_models() -> dict[str, object]:
        inventory: HardwareInventory | None = getattr(app.state, "_hardware_inventory", None)
        result: dict[str, object] = {}
        if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
            profiles = app.state._model_gateway.list_profiles()
            result["profiles"] = [
                {
                    **p.model_dump(),
                    "can_run_local": _can_run_local(inventory, p.model_dump().get("model", "")),
                }
                for p in profiles
            ]
            return result
        return {"profiles": []}

    @app.get("/admin/models/health")
    async def admin_models_health() -> dict[str, object]:
        if hasattr(app.state, "_health_tracker") and app.state._health_tracker is not None:
            tracker = app.state._health_tracker
            if hasattr(app.state, "_model_gateway") and app.state._model_gateway is not None:
                profiles = app.state._model_gateway.list_profiles()
                return {"health": [tracker.get_health(p.model_profile_id) for p in profiles]}
            return {"health": []}
        return {"health": []}

    @app.post("/admin/models/search")
    async def admin_models_search(req: ModelSearchRequest) -> dict[str, object]:
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
    async def admin_models_downloaded() -> dict[str, object]:
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

    @app.post("/admin/models/local/serve")
    async def admin_models_local_serve(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        model_id = str(body.get("model_id") or "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")

        mgr = _get_inference_mgr(app)
        if mgr is None:
            if not hasattr(app.state, "_local_inference") or app.state._local_inference is None:
                subsys = _get_or_create_subsystems(app)
                app.state._local_inference = LocalInferenceManager(event_bus=subsys["bus"])
            mgr = app.state._local_inference

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
        logger.info("local model serve created: server_id=%s model=%s", server.server_id, model_id)
        return {
            "server_id": server.server_id,
            "model_id": model_id,
            "engine": config.engine,
            "model": config.model_path or config.model_name,
            "endpoint_url": server.endpoint_url,
            "status": server.status,
        }

    @app.post("/admin/code/complexity")
    async def admin_code_complexity(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        path = cast(str, body.get("path", ""))
        safe_path = _confined_code_path(app, path)
        scorer = CodeComplexityScorer()
        score = scorer.score_file(safe_path)
        task_type = scorer.suggest_task_type(score)
        return {
            "score": score.model_dump(),
            "suggested_task_type": task_type.value,
        }

    @app.post("/admin/code/suggest-model")
    async def admin_code_suggest_model(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        path = cast(str, body.get("path", ""))
        safe_path = _confined_code_path(app, path)
        scorer = CodeComplexityScorer()
        score = scorer.score_file(safe_path)
        task_type = scorer.suggest_task_type(score)

        recommendation: dict[str, object] = {
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
        except Exception as exc:
            # S26: distinguish a router crash from genuine cold-start fallback.
            # A broken router looks like "insufficient_historical_data" —
            # now it surfaces as "router_error" so operators can tell the
            # difference.
            logger.warning(
                "AdaptiveRouter.route failed for task_type=%s path=%s: %s",
                task_type.value,
                path,
                exc,
            )
            recommendation = {
                "selected_prompt_profile_id": None,
                "selected_model_profile_id": "default",
                "composite_score": 0.0,
                "estimated_cost_usd": 0.0,
                "sample_count": 0,
                "fallback": True,
                "reason": f"router_error: {exc!r}",
            }

        return {
            "path": path,
            "complexity": score.model_dump(),
            "suggested_task_type": task_type.value,
            "model_recommendation": recommendation,
        }

    @app.post(
        "/admin/models/call",
        summary="Call a model with a prompt via the gateway",
        description=(
            "Synchronous single-turn generation with optional system prompt, "
            "explicit/auto-routed model profile, and best-effort "
            "structured-output hints. Returns text + chosen profile + token "
            "usage. Budget-gated, PSK-authenticated."
        ),
    )
    async def admin_models_call(request: Request) -> dict[str, object]:
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

        prompt: str = cast(str, body.get("prompt", ""))
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
            isinstance(response_format, str) and response_format.strip().lower() == "json"
        ) or response_schema is not None
        if _wants_json:
            _nudge = "Respond ONLY with a single valid JSON value and no surrounding prose or Markdown code fences."
            if response_schema is not None:
                try:
                    _schema_json = json.dumps(response_schema, sort_keys=True)
                except (TypeError, ValueError):
                    _schema_json = ""
                if _schema_json:
                    _nudge += f" The JSON must match this schema: {_schema_json}."
            system_text = f"{system_text}\n\n{_nudge}".strip() if system_text else _nudge

        model_profile_id: str | None = cast(str | None, body.get("model_profile"))
        route_task_type: str | None = cast(str | None, body.get("route_task_type"))
        # The caller-supplied output cap. The gateway does not yet forward this
        # to the provider (per-call token limits come from profile config), but
        # it IS threaded into the budget gate as requested_max_output_tokens so
        # a call that caps its output is estimated at its real (smaller) cost
        # instead of the worst-case profile.max_output_tokens — without this a
        # low-run_budget_usd deployment rejected every metered call (D-21
        # over-conservatism). estimate_cost min()s it against the profile cap, so
        # a caller cannot use it to under-report below the profile's capacity.
        try:
            requested_max_output_tokens: int | None = int(cast(int | float | str, body.get("max_tokens", 2048)))
        except (TypeError, ValueError):
            requested_max_output_tokens = None
        if requested_max_output_tokens is not None and requested_max_output_tokens <= 0:
            requested_max_output_tokens = None
        if requested_max_output_tokens is not None and requested_max_output_tokens > _MAX_MODELS_CALL_MAX_TOKENS:
            raise HTTPException(
                status_code=413,
                detail="max_tokens exceeds maximum allowed count",
            )

        # B5: budget gate — fail-closed when guard exhausted or degraded startup.
        _BUDGET_UNSET = object()  # sentinel: attr absent (degraded startup)
        _budget_guard = getattr(app.state, "_budget_guard", _BUDGET_UNSET)
        _fail_closed_degraded = os.environ.get("GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
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
                _verdict = cast(_CheckAllLimitsGuard, _budget_guard).check_all_limits(estimated_cost=0.0)
            except Exception as _exc:
                logger.warning("budget check raised: %s", _exc, exc_info=True)
                raise HTTPException(status_code=503, detail="budget check failed") from _exc
            if not isinstance(_verdict, dict) or not _verdict.get("allowed", False):
                _reason = _verdict.get("reason", "budget exhausted") if isinstance(_verdict, dict) else "non-dict"
                raise HTTPException(status_code=429, detail=f"budget exhausted: {_reason}")

        # Resolve the gateway — use app.state if available, else build a minimal one
        gateway: ModelGateway | None = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            subsys = _get_or_create_subsystems(app)
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            # H12 (W3.10): pass metrics_collector from app.state so API-driven
            # model calls through this fallback gateway are visible to the
            # cost/metrics subsystem (the daemon-built gateway gets the same
            # collector). Defensive getattr — falls back to the gateway default
            # when app.state has no collector (degraded startup), never crashes.
            metrics_collector = getattr(app.state, "_metrics_collector", None)
            gateway = ModelGateway(
                # CI-1: use the shared factory for consistency with daemon/worker.
                # No profiles are in scope at this fallback path, so the registry
                # is empty — equivalent to ProviderRegistry() but via the factory.
                provider_registry=ProviderRegistry.from_profiles([]),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
                metrics_collector=metrics_collector,
            )
            _track_router_owned_gateway(app, gateway)

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
            import functools

            response = await asyncio.to_thread(
                functools.partial(
                    gateway.call_model,
                    used_profile_id,
                    messages,
                    requested_max_output_tokens=requested_max_output_tokens,
                )
            )
            return {
                "text": response.content,
                "model_profile_id": used_profile_id,
                "usage": dict(response.usage_metadata) if response.usage_metadata else {},
            }
        except Exception as exc:
            logger.warning("model call failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail="model call failed") from exc

    @app.post(
        "/admin/models/workflow",
        summary="Run the multi-step LangGraph workflow (classify→select→generate→review)",
        description=(
            "Quality-gated multi-step model workflow with adaptive routing and "
            "retries. Returns final content, model, quality score, retry count, "
            "warnings. Budget-gated, PSK-authenticated."
        ),
    )
    async def admin_models_workflow(request: Request) -> dict[str, object]:
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
            raise HTTPException(status_code=422, detail="messages must be a non-empty list")

        profile_id: str | None = cast(str | None, body.get("profile_id"))
        work_type: str | None = cast(str | None, body.get("work_type"))
        try:
            max_retries = int(cast(int | float | str, body.get("max_retries", 2)))
            quality_threshold = float(cast(float | int | str, body.get("quality_threshold", 0.6)))
        except (TypeError, ValueError) as exc:
            logger.warning("invalid numeric parameter in workflow request: %s", exc, exc_info=True)
            raise HTTPException(status_code=422, detail="invalid numeric parameter") from exc
        enable_graph = bool(body.get("enable_graph", True))

        # B5: budget gate — mirror /admin/models/call exactly (fail-closed when
        # the guard is exhausted or startup degraded).
        _BUDGET_UNSET = object()  # sentinel: attr absent (degraded startup)
        _budget_guard = getattr(app.state, "_budget_guard", _BUDGET_UNSET)
        _fail_closed_degraded = os.environ.get("GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
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
                _verdict = cast(_CheckAllLimitsGuard, _budget_guard).check_all_limits(estimated_cost=0.0)
            except Exception as _exc:
                logger.warning("budget check raised: %s", _exc, exc_info=True)
                raise HTTPException(status_code=503, detail="budget check failed") from _exc
            if not isinstance(_verdict, dict) or not _verdict.get("allowed", False):
                _reason = _verdict.get("reason", "budget exhausted") if isinstance(_verdict, dict) else "non-dict"
                raise HTTPException(status_code=429, detail=f"budget exhausted: {_reason}")

        # Resolve the gateway — use app.state if available, else build a minimal
        # one (same construction path as /admin/models/call).
        gateway: ModelGateway | None = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            subsys = _get_or_create_subsystems(app)
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            # H12 (W3.10): pass metrics_collector from app.state so API-driven
            # model calls through this fallback gateway are visible to the
            # cost/metrics subsystem (the daemon-built gateway gets the same
            # collector). Defensive getattr — falls back to the gateway default
            # when app.state has no collector (degraded startup), never crashes.
            metrics_collector = getattr(app.state, "_metrics_collector", None)
            gateway = ModelGateway(
                # CI-1: use the shared factory for consistency with daemon/worker.
                # No profiles are in scope at this fallback path, so the registry
                # is empty — equivalent to ProviderRegistry() but via the factory.
                provider_registry=ProviderRegistry.from_profiles([]),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
                metrics_collector=metrics_collector,
            )
            _track_router_owned_gateway(app, gateway)

        # call_model is synchronous on ModelGateway; LangGraphGateway invokes
        # call_model_fn as `await fn(profile_id=..., messages=...)`, so wrap the
        # blocking call in a thread (mirrors /admin/models/call).
        import asyncio

        _gateway = gateway

        async def _call_model_fn(profile_id: str, messages: list[dict[str, str]], **kwargs: object) -> ModelResponse:
            # Forward extra kwargs (e.g. work_type, used for token-cost capture at
            # the gateway billing chokepoint) through to call_model so the
            # LangGraphGateway generation path is metered like every other path.
            return await asyncio.to_thread(
                cast(Callable[..., ModelResponse], _gateway.call_model),
                profile_id,
                messages,
                **kwargs,
            )

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
            logger.warning("workflow execution failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="workflow execution failed") from exc
        return result

    @app.post(
        "/admin/models/chat-stream",
        summary="Chat-streaming endpoint (SSE)",
        description=(
            "Server-Sent Events streaming endpoint. Accepts messages + optional "
            "model_profile_id, streams tokens as `data:` lines, and terminates "
            "with a `done: true` event carrying usage metadata. "
            "Budget-gated, PSK-authenticated."
        ),
    )
    async def admin_models_chat_stream(req: ChatStreamRequest) -> StreamingResponse:
        messages: list[dict[str, str]] = [{"role": m.role, "content": m.content} for m in req.messages]
        model_profile_id = req.model_profile_id

        # Budget pre-check — fail-closed guard exhausted or degraded startup
        _BUDGET_UNSET = object()
        _budget_guard = getattr(app.state, "_budget_guard", _BUDGET_UNSET)
        _fail_closed_degraded = os.environ.get("GLUDD_BUDGET_FAIL_CLOSED_DEGRADED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if _budget_guard is _BUDGET_UNSET and _fail_closed_degraded:
            raise HTTPException(
                status_code=503,
                detail="budget guard unavailable (degraded startup); GLUDD_BUDGET_FAIL_CLOSED_DEGRADED=1",
            )
        if (
            _budget_guard is not _BUDGET_UNSET
            and _budget_guard is not None
            and hasattr(_budget_guard, "check_all_limits")
        ):
            try:
                _verdict = cast(_CheckAllLimitsGuard, _budget_guard).check_all_limits(estimated_cost=0.0)
            except Exception as _exc:
                logger.warning("budget check raised: %s", _exc, exc_info=True)
                raise HTTPException(status_code=503, detail="budget check failed") from _exc
            if not isinstance(_verdict, dict) or not _verdict.get("allowed", False):
                _reason = _verdict.get("reason", "budget exhausted") if isinstance(_verdict, dict) else "non-dict"
                raise HTTPException(status_code=429, detail=f"budget exhausted: {_reason}")

        gateway: ModelGateway | None = getattr(app.state, "_model_gateway", None)
        if gateway is None:
            subsys = _get_or_create_subsystems(app)
            if not hasattr(app.state, "_health_tracker"):
                app.state._health_tracker = ModelHealthTracker()
            metrics_collector = getattr(app.state, "_metrics_collector", None)
            gateway = ModelGateway(
                provider_registry=ProviderRegistry.from_profiles([]),
                router=ModelRouter(),
                event_bus=subsys["bus"],
                hook_system=subsys["hooks"],
                worker_broadcaster=subsys["broadcaster"],
                response_cache=ModelResponseCache(),
                health_tracker=app.state._health_tracker,
                metrics_collector=metrics_collector,
            )
            _track_router_owned_gateway(app, gateway)

        available_profiles = gateway.list_profiles()
        if not available_profiles:
            raise HTTPException(
                status_code=503,
                detail="No model profiles configured. Add a profile via POST /admin/models first.",
            )

        import asyncio

        try:
            if req.max_tokens is not None:
                stream_iterator = await asyncio.to_thread(
                    gateway.call_model_stream,
                    model_profile_id,
                    messages,
                    requested_max_output_tokens=req.max_tokens,
                )
            else:
                stream_iterator = await asyncio.to_thread(
                    gateway.call_model_stream,
                    model_profile_id,
                    messages,
                )
        except Exception as exc:
            logger.warning("chat-stream init failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail="model stream failed") from exc

        async def _sse_events() -> AsyncIterator[str]:
            try:
                latest_usage: dict[str, object] = {}
                for chunk in stream_iterator:
                    content = getattr(chunk, "content", "") or ""
                    usage_obj = getattr(chunk, "usage_metadata", None)
                    if isinstance(usage_obj, dict) and usage_obj:
                        latest_usage = usage_obj
                    event = json.dumps(
                        {"content": content, "done": False},
                        ensure_ascii=False,
                    )
                    yield f"data: {event}\n\n"
                final_event = json.dumps(
                    {"content": "", "done": True, "usage": latest_usage},
                    ensure_ascii=False,
                )
                yield f"data: {final_event}\n\n"
            except Exception:
                err_event = json.dumps(
                    {"content": "", "done": True, "error": True},
                    ensure_ascii=False,
                )
                yield f"data: {err_event}\n\n"

        return StreamingResponse(
            _sse_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── local model management ──────────────────────────────────────────

    @app.post("/admin/models/local/download")
    async def admin_models_local_download(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        model_id = str(body.get("model_id") or "")
        source = request.query_params.get("source") or str(body.get("source") or "huggingface")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")

        valid_model_sources = {s.value for s in ModelSource}
        valid_legacy_sources = {"huggingface", "ollama", "local", "multi"}
        all_valid = valid_model_sources | valid_legacy_sources
        if source not in all_valid:
            raise HTTPException(
                status_code=422,
                detail=f"source must be one of {sorted(all_valid)}",
            )

        filename = str(body.get("filename", "")) or None
        revision = str(body.get("revision", "")) or None
        downloader: ModelDownloader = request.app.state._model_downloader

        config = next((c for c in _LOCAL_MODELS if c.name == model_id), None)
        registry_bound_sources = valid_model_sources - {"huggingface", "ollama"}
        if source in registry_bound_sources and config is None:
            raise HTTPException(
                status_code=404,
                detail=f"Model {model_id!r} not found in local model registry",
            )

        # URL, filesystem, and mirror sources are resolved exclusively from the
        # trusted registry above.  Hugging Face also supports registered aliases,
        # while direct Hugging Face repository IDs and Ollama tags use the fixed
        # provider transports below and do not expose registry-controlled paths.
        if config is not None and source in registry_bound_sources | {"huggingface"}:
            source_order_raw = body.get("order")
            source_order: list[ModelSource] | None = None
            if isinstance(source_order_raw, list):
                source_map = {s.value: s for s in ModelSource}
                source_order = []
                for s in source_order_raw:
                    mapped = source_map.get(str(s))
                    if mapped is not None:
                        source_order.append(mapped)

            if source_order is None:
                source_order = [ModelSource(source)]

            try:
                result: CloudDownloadedFile = download_with_fallback(
                    config=config,
                    order=source_order,
                    cache_dir=downloader.cache_dir,
                    timeout=downloader.timeout,
                )
            except ModelSourceDownloadError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

            store = cast(dict[str, LocalServerConfig], request.app.state._sm_server_store)
            key = f"{result.source.value}/{model_id}"
            store[key] = LocalServerConfig(
                engine=cast(str, body.get("engine", "llamacpp")),
                model_path=result.local_path,
                model_name=model_id,
                host=cast(str, body.get("host", "localhost")),
                port=cast(int, body.get("port", 8000)),
                gpu_layers=cast(int, body.get("gpu_layers", -1)),
                context_size=cast(int, body.get("context_size", 4096)),
            )
            logger.info(
                "model downloaded via %s: %s -> %s (%.1f MB)",
                result.source.value,
                key,
                result.local_path,
                result.size_bytes / 1e6,
            )
            return {
                "downloaded": True,
                "model_id": model_id,
                "source": result.source.value,
                "profile_key": key,
                "local_path": result.local_path,
                "size_bytes": result.size_bytes,
            }

        if source == "local":
            model_path = str(body.get("model_path", f"./models/{model_id}"))
            size_bytes = _compute_size_disk(model_path)
            downloaded = DownloadedModel(
                model_id=model_id,
                local_path=model_path,
                source=DownloadSource.CACHE,
                size_bytes=size_bytes,
            )
        elif source == "multi":
            order_raw = body.get("order")
            order: list[str] | None = None
            if isinstance(order_raw, list):
                order = [str(o) for o in order_raw]
            downloaded = downloader.download(
                model_id=model_id,
                filename=filename,
                revision=revision,
                order=order,
            )
        elif source == "ollama":
            downloaded = downloader.pull_ollama(model_id=model_id, revision=revision)
        else:
            downloaded = downloader.download(
                model_id=model_id,
                filename=filename,
                revision=revision,
            )

        key = f"{source}/{model_id}"
        store = cast(dict[str, LocalServerConfig], request.app.state._sm_server_store)
        store[key] = LocalServerConfig(
            engine=cast(str, body.get("engine", "llamacpp")),
            model_path=downloaded.local_path,
            model_name=model_id,
            host=cast(str, body.get("host", "localhost")),
            port=cast(int, body.get("port", 8000)),
            gpu_layers=cast(int, body.get("gpu_layers", -1)),
            context_size=cast(int, body.get("context_size", 4096)),
        )
        logger.info(
            "model downloaded: %s -> %s (%.1f MB)",
            key,
            downloaded.local_path,
            downloaded.size_bytes / 1e6,
        )
        return {
            "downloaded": True,
            "model_id": model_id,
            "source": source,
            "profile_key": key,
            "local_path": downloaded.local_path,
            "size_bytes": downloaded.size_bytes,
        }

    @app.post("/admin/models/local/quantize")
    async def admin_models_local_quantize(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        model_id = str(body.get("model_id") or "")
        method_name = str(body.get("method") or "q4_k_m")
        input_path_override: str | None = cast(str | None, body.get("input_path"))
        output_path_override: str | None = cast(str | None, body.get("output_path"))

        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")
        if method_name not in ("q4_k_m", "q5_k_m", "q8_0", "f16", "q4_0"):
            raise HTTPException(status_code=422, detail="method must be q4_0, q4_k_m, q5_k_m, q8_0, or f16")

        quantizer = _get_model_quantizer(app)
        if quantizer is None:
            raise HTTPException(status_code=503, detail="ModelQuantizer not available")

        sanitized_id = model_id.replace("/", "_")
        models_root = _models_dir()

        if input_path_override:
            input_gguf = input_path_override
        else:
            download_store_raw: dict[str, object] = request.app.state._sm_server_store
            download_store = cast(dict[str, LocalServerConfig], download_store_raw)
            candidate_gguf = os.path.join(models_root, sanitized_id, f"{sanitized_id}-f16.gguf")
            existent_ggufs: list[str] = []
            model_dir = os.path.join(models_root, sanitized_id)
            if os.path.isdir(model_dir):
                for fname in os.listdir(model_dir):
                    if fname.endswith(".gguf"):
                        existent_ggufs.append(os.path.join(model_dir, fname))

            conf = download_store.get(f"huggingface/{model_id}") or download_store.get(f"gguf/{model_id}")
            conf_path = str(conf.model_path) if conf else ""

            if existent_ggufs:
                existent_ggufs.sort(key=lambda p: ("-f16" in os.path.basename(p), p), reverse=True)
                input_gguf = existent_ggufs[0]
            elif conf_path and os.path.isfile(conf_path) and conf_path.endswith(".gguf"):
                input_gguf = conf_path
            elif os.path.isfile(candidate_gguf):
                input_gguf = candidate_gguf
            else:
                logger.warning("No GGUF input found for %s in %s", model_id, models_root)
                raise HTTPException(
                    status_code=422,
                    detail=f"No GGUF file found for {model_id}. Download the model first or specify input_path.",
                )

        if output_path_override:
            output_gguf = output_path_override
        else:
            quant_out_dir = os.path.join(models_root, sanitized_id, "quantized")
            os.makedirs(quant_out_dir, exist_ok=True)
            output_gguf = os.path.join(quant_out_dir, f"{sanitized_id}-{method_name}.gguf")

        method = QuantMethod.from_string(method_name)
        success = quantizer.quantize(input_gguf, output_gguf, method)

        quantize_store: dict[str, dict[str, object]] = request.app.state._sm_quantize_store
        quant_digest = _digest(
            {"model_id": model_id, "method": method_name, "input": input_gguf, "output": output_gguf}
        )
        quant_entry: dict[str, object] = {
            "model_id": model_id,
            "method": method.value,
            "method_name": method_name,
            "input_path": input_gguf,
            "output_path": output_gguf,
            "output_size_bytes": os.path.getsize(output_gguf) if success and os.path.isfile(output_gguf) else 0,
            "success": success,
            "digest": quant_digest,
        }
        quantize_store[f"quant:{sanitized_id}:{method_name}"] = quant_entry

        logger.info(
            "model quantize: %s method=%s success=%s output=%s",
            model_id,
            method_name,
            success,
            output_gguf if success else "n/a",
        )
        return {
            "quantized": success,
            "model_id": model_id,
            "method": method_name,
            "method_value": method.value,
            "output_path": output_gguf,
            "digest": quant_digest,
        }

    @app.post("/admin/models/local/evaluate")
    async def admin_models_local_evaluate(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        model_id = str(body.get("model_id") or "")
        if not model_id:
            raise HTTPException(status_code=422, detail="model_id required")

        run_benchmark_flag = bool(body.get("benchmark", False))

        if run_benchmark_flag:
            tasks_raw = body.get("tasks", [])
            tasks = [str(t) for t in tasks_raw] if isinstance(tasks_raw, list) and tasks_raw else list(_DEFAULT_TASKS)

            limit_val = body.get("limit")
            limit = int(limit_val) if isinstance(limit_val, (int, float, str)) else None
            device_val = body.get("device")
            device = str(device_val) if device_val else None
            batch_size = str(body.get("batch_size", "auto"))

            runner = LMEvalRunner(
                model_id=model_id,
                batch_size=batch_size,
                device=device,
                limit=limit,
            )

            scores = runner.run_benchmark(tasks)
            evidence_objects = to_capability_evidence(scores, model_id)

            key = f"cap:{model_id}"
            capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
            eval_store: dict[str, dict[str, object]] = request.app.state._sm_eval_store

            evidence_dicts: list[dict[str, object]] = []
            for ev in evidence_objects:
                entry: dict[str, object] = {
                    "model_id": model_id,
                    "task_kind": ev.task_kind,
                    "collection": ev.collection,
                    "role": str(ev.role),
                    "suite_id": ev.suite_id,
                    "suite_revision": ev.suite_revision,
                    "total_cases": ev.total_cases,
                    "passed_cases": ev.passed_cases,
                    "passed": ev.passed_cases == ev.total_cases,
                    "collection_ok": ev.collection_ok,
                    "local_only": ev.local_only,
                    "evidence_digest": ev.evidence_digest,
                    "acceptance_contract_digest": ev.acceptance_contract_digest,
                }
                capability_store.setdefault(key, []).append(entry)
                eval_store[f"eval:{model_id}:{ev.task_kind}"] = entry
                evidence_dicts.append(entry)
                logger.info(
                    "model benchmark: %s task=%s passed=%s/%s",
                    model_id,
                    ev.task_kind,
                    ev.passed_cases,
                    ev.total_cases,
                )

            return {
                "evaluated": True,
                "model_id": model_id,
                "benchmark": True,
                "tasks_run": tasks,
                "scores": scores,
                "evidence": evidence_dicts,
            }

        task_kind = str(body.get("task_kind") or "")
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
        cap_store = request.app.state._sm_capability_store
        ev_store = request.app.state._sm_eval_store
        cap_store.setdefault(key, []).append(evidence_entry)
        ev_store[f"eval:{model_id}:{task_kind}"] = evidence_entry
        logger.info("model evaluate: %s task=%s passed=%s/%s", model_id, task_kind, passed_cases, total_cases)
        return {"evaluated": True, "model_id": model_id, "evidence": evidence_entry}

    @app.get("/admin/models/local/evidence")
    async def admin_models_local_evidence(request: Request, model_id: str | None = None) -> dict[str, object]:
        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        if model_id:
            return {
                "model_id": model_id,
                "evidence": capability_store.get(f"cap:{model_id}", []),
            }
        return {"evidence": list(capability_store.values())}

    @app.get("/admin/models/local/status")
    async def admin_models_local_status() -> dict[str, object]:
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

    @app.post("/admin/models/local/consume")
    async def admin_models_local_consume(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        server_id = str(body.get("server_id") or "")
        prompt = str(body.get("prompt") or "")
        if not server_id:
            raise HTTPException(status_code=422, detail="server_id required")
        if not prompt:
            raise HTTPException(status_code=422, detail="prompt required")

        mgr = _get_inference_mgr(app)
        if mgr is None:
            raise HTTPException(status_code=503, detail="LocalInferenceManager not available")
        servers = mgr.list_servers()
        target = None
        for s in servers:
            if s.server_id == server_id:
                target = s
                break
        if target is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
        if target.status != "running":
            raise HTTPException(status_code=503, detail=f"Server {server_id} not running (status={target.status})")

        max_tokens = cast(int, body.get("max_tokens", 256))
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            try:
                resp = await client.post(
                    f"{target.endpoint_url}/completions",
                    json={
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                raw = resp.json()
                text = ""
                choices = raw.get("choices")
                if choices:
                    text = choices[0].get("text", "")
                usage = raw.get("usage", {})
                return {
                    "server_id": server_id,
                    "text": text,
                    "usage": usage,
                }
            except httpx.HTTPError as exc:
                logger.warning("local consume failed: %s", exc, exc_info=True)
                raise HTTPException(status_code=502, detail="local model call failed") from exc

    @app.post("/admin/models/local/shutdown")
    async def admin_models_local_shutdown(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
        server_id = str(body.get("server_id") or "")
        if not server_id:
            raise HTTPException(status_code=422, detail="server_id required")

        mgr = _get_inference_mgr(app)
        if mgr is None:
            raise HTTPException(status_code=503, detail="LocalInferenceManager not available")

        if mgr.get_server(server_id) is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
        try:
            await mgr.stop_server(server_id)
            logger.info("local server shut down: server_id=%s", server_id)
            return {"shutdown": True, "server_id": server_id}
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found") from None

    @app.post("/admin/models/rollout")
    async def admin_models_rollout(request: Request) -> dict[str, object]:
        body = await _parse_request_body(request)
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
                detail=f"No capability evidence for {model_id}. Run /admin/models/local/evaluate first.",
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
        logger.info("model rollout initiated: %s target=%s", model_id, target)
        return rollout_entry

    @app.get("/admin/models/recommend")
    async def admin_models_recommend(request: Request, task: str) -> dict[str, object]:
        from general_ludd.small_models.cost import (
            estimate_download_cost,
            estimate_inference_cost,
        )

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
            model_id = str(evidence.get("model_id", ""))
            cost_inference = estimate_inference_cost(model_id)
            cost_download = estimate_download_cost(model_id)
            candidates.append(
                {
                    "model_id": model_id,
                    "task_kind": task,
                    "passed_cases": passed,
                    "total_cases": total,
                    "pass_ratio": round(ratio, 4),
                    "passed": evidence.get("passed", False),
                    "evidence_digest": evidence.get("evidence_digest", ""),
                    "cost": {
                        "inference_usd_per_hour": cost_inference.get("estimated_usd_per_hour", 0.0),
                        "inference_tier": cost_inference.get("tier", "small_local"),
                        "download_size_gb": cost_download.get("size_gb", 0.0),
                        "download_data_transfer_usd": cost_download.get("data_transfer_usd", 0.0),
                        "storage_usd_per_month": cost_download.get("estimated_storage_usd_per_month", 0.0),
                    },
                }
            )

        candidates.sort(key=lambda c: (c["passed"], c["pass_ratio"]), reverse=True)
        selected_model_profile_id = str(candidates[0]["model_id"]) if candidates else None
        return {
            "task": task,
            "recommendations": candidates,
            "total": len(candidates),
            "selected_model_profile_id": selected_model_profile_id,
        }

    @app.get("/admin/models/cost")
    async def admin_models_cost(request: Request, model: str) -> dict[str, object]:
        from general_ludd.small_models.cost import (
            estimate_download_cost,
            estimate_inference_cost,
            estimate_quantize_cost,
            is_off_peak,
            next_off_peak_window,
            should_defer_download,
        )

        inference = estimate_inference_cost(model)
        download = estimate_download_cost(model)
        size_gb_val = download.get("size_gb", 0.0)
        size_gb = float(size_gb_val) if isinstance(size_gb_val, (int, float)) else 0.0
        quantize = estimate_quantize_cost(model, size_gb=size_gb)
        defer = should_defer_download(size_gb)
        off_peak_window = next_off_peak_window()

        return {
            "model_id": model,
            "inference": inference,
            "download": download,
            "quantize": quantize,
            "off_peak": {
                "is_off_peak_now": is_off_peak(),
                "next_window": off_peak_window,
            },
            "scheduling": defer,
        }

    @app.get("/admin/models/tasks")
    async def admin_models_tasks(request: Request, model: str) -> dict[str, object]:
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

    @app.get("/admin/models/radar")
    async def admin_models_radar(request: Request, model: str) -> Response:
        from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
        from general_ludd.small_models.radar_profile import generate_radar, render_radar_svg

        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        evidence_dicts = capability_store.get(f"cap:{model}", [])
        evidence_objects = [CapabilityEvidence(**cast(dict[str, Any], e)) for e in evidence_dicts]

        profile = generate_radar(evidence_objects)
        svg_output = render_radar_svg(profile)

        return Response(content=svg_output, media_type="image/svg+xml")

    @app.get("/admin/models/report")
    async def admin_models_report(request: Request, model: str, compare: str | None = None) -> dict[str, object]:
        from general_ludd.small_models.benchmark_report import generate_report, render_report

        model_ids = [model]
        if compare:
            model_ids.append(compare)

        capability_store: dict[str, list[dict[str, object]]] = request.app.state._sm_capability_store
        report = generate_report(model_ids, capability_store, include_svg=True)
        return render_report(report)

    @app.post("/admin/models/compare")
    async def admin_models_compare(request: Request) -> dict[str, object]:
        from general_ludd.routing_roles.small_model_policy import CapabilityEvidence
        from general_ludd.small_models.radar_profile import compare_models, generate_radar

        body = await _parse_request_body(request)
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
