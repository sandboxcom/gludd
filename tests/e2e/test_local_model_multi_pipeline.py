"""E2E: Multi-model pipeline with locally-downloaded GGUF models.

Pipeline: PLANNER (SmolLM2-360M) → CODER (Qwen2.5-Coder-0.5B) → REVIEWER (Phi-2)

Flow:
1. Check deps (llama-cpp-python, huggingface_hub)
2. Check RAM (skip if <2GB free)
3. Download 3 GGUF models to temp dir
4. Serve each on a different port via LocalInferenceManager
5. Build ModelGateway with 3 role-specific profiles
6. Run planner→coder→reviewer pipeline via SoftwareGenerator.generate_multi()
7. Verify generated code (AST parse, required methods, runtime)
8. Shut down all servers + cleanup temp files

Models total ~1GB (224 + 312 + 487 MB).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest

_MULTI_PIPELINE_MODELS = {
    "planner": {
        "name": "SmolLM2-360M",
        "repo": "bartowski/SmolLM2-360M-Instruct-GGUF",
        "filename": "SmolLM2-360M-Instruct-Q4_K_M.gguf",
        "context_size": 8192,
    },
    "coder": {
        "name": "Qwen2.5-Coder-0.5B",
        "repo": "bartowski/Qwen2.5-Coder-0.5B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf",
        "context_size": 32768,
    },
    "reviewer": {
        "name": "Phi-2",
        "repo": "bartowski/phi-2-GGUF",
        "filename": "phi-2-Q2_K.gguf",
        "context_size": 2048,
    },
}

_SNAKE_DESCRIPTION = (
    "Snake game: a snake moves on a 20x20 grid eating food to grow. "
    "The snake is a list of (x,y) tuples, head first. "
    "Score starts at 0, increments by 1 when snake eats food. "
    "Game over when snake hits a wall (x<0, x>=20, y<0, y>=20) or itself. "
    "Game over is idempotent. tick() while game_over does nothing. "
    "Required methods: __init__, start, tick(direction), score->int, is_game_over->bool, restart. "
    "Output Python code only, no prose."
)


# ── Dependency checks ────────────────────────────────────────────────────────


def _has_llama_cpp() -> bool:
    try:
        import llama_cpp  # noqa: F401

        return True
    except ImportError:
        return False


def _has_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401

        return True
    except ImportError:
        return False


def _free_ram_mb() -> int:
    import subprocess

    try:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        page_size = 16384  # macOS default
        free_pages = 0
        for line in result.stdout.splitlines():
            if "page size of" in line:
                page_size = int(line.strip().split()[-1])
            if "Pages free:" in line:
                free_pages = int(line.strip().split(":")[-1].strip().rstrip("."))
        if free_pages > 0:
            return (free_pages * page_size) // (1024 * 1024)
        # Fallback: use memory_pressure
        mem_pressure = subprocess.run(
            ["memory_pressure"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in mem_pressure.stdout.splitlines():
            if "Free:" in line:
                free_bytes = int(line.strip().split()[-1])
                return free_bytes // (1024 * 1024)
        return 0
    except Exception:
        return 0


def _deps_reason() -> str | None:
    missing: list[str] = []
    if not _has_llama_cpp():
        missing.append("llama-cpp-python")
    if not _has_huggingface_hub():
        missing.append("huggingface_hub")
    if missing:
        return f"Missing deps: {', '.join(missing)}"
    free_ram = _free_ram_mb()
    if 0 < free_ram < 2048:
        return f"Insufficient free RAM: {free_ram}MB (need >= 2048MB for 3 models)"
    return None


_REASON = _deps_reason()
if _REASON is not None:
    pytestmark = pytest.mark.skip(reason=_REASON)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _find_cached_gguf(cache_dir: str, filename: str) -> str | None:
    cache = Path(cache_dir)
    if not cache.is_dir():
        return None
    direct = cache / filename
    if direct.is_file():
        return str(direct)
    for f in cache.iterdir():
        if f.suffix == ".gguf" and f.is_file():
            return str(f)
    return None


async def _wait_for_server(base_url: str, timeout: float = 60.0) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        for _attempt in range(int(timeout)):
            try:
                resp = await client.get("/v1/models")
                if resp.status_code == 200:
                    # Warm-up
                    warmup = await client.post(
                        "/v1/completions",
                        json={"prompt": "Hello", "max_tokens": 1},
                    )
                    if warmup.status_code == 200:
                        return
            except httpx.TransportError:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Server /v1/models did not become 200 within {timeout}s at {base_url}")


def _verify_generated_code(code: str, tmpdir: str) -> dict[str, object]:
    tree = ast.parse(code)
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert len(classes) >= 1, "generated code must contain at least one class"

    methods: dict[str, bool] = {}
    for cls in classes:
        for node in ast.walk(cls):
            if isinstance(node, ast.FunctionDef):
                methods[node.name] = True

    found = set(methods.keys())
    alt_start = {"start", "play", "begin", "new_game", "start_game", "reset"}
    alt_over = {"is_game_over", "game_over", "over", "crashed", "finished", "ended", "dead", "done"}
    alt_restart = {"restart", "reset", "new_game", "start_over", "play_again"}

    has_start = bool(found & alt_start)
    has_over = bool(found & alt_over)
    has_restart = bool(found & alt_restart)
    has_tick = "tick" in found
    has_score = "score" in found

    assert "__init__" in found, "missing __init__"
    assert has_tick, "missing tick method"
    assert has_score, "missing score method"
    assert has_start, "missing start method"
    assert has_over, "missing is_game_over method"
    assert has_restart, "missing restart method"

    game_path = Path(tmpdir) / "game_snake.py"
    game_path.write_text(code)
    spec = importlib.util.spec_from_file_location("game_snake", str(game_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    game_cls = None
    for name in [c.name for c in classes]:
        if hasattr(mod, name):
            game_cls = getattr(mod, name)
            break
    assert game_cls is not None, "no game class found in module"

    game = game_cls()
    assert game is not None

    if hasattr(game, "start"):
        game.start()
    if hasattr(game, "score"):
        score_val = game.score()
        assert isinstance(score_val, int), f"score() must return int, got {type(score_val)}"
    if hasattr(game, "is_game_over"):
        over_val = game.is_game_over()
        assert isinstance(over_val, bool), f"is_game_over() must return bool, got {type(over_val)}"
    if hasattr(game, "tick"):
        for _ in range(5):
            if hasattr(game, "is_game_over") and not game.is_game_over():
                game.tick("right")
    if hasattr(game, "restart"):
        game.restart()
        if hasattr(game, "score"):
            assert game.score() == 0, "score must be 0 after restart"

    return {"class_name": classes[0].name, "passed": True}


# ── Teardown ─────────────────────────────────────────────────────────────────

_TMPDIR: str | None = None


def teardown_module() -> None:
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.slow
class TestLocalModelMultiPipeline:
    """Multi-model pipeline: download 3 models → serve → planner→coder→reviewer → verify."""

    @pytest.mark.asyncio
    async def test_multi_model_snake_generation(self) -> None:
        """End-to-end multi-model snake game generation with local GGUF models."""
        from general_ludd.cloud.multi_model_game_pipeline import MultiModelGamePipeline
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServer, LocalServerConfig
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager
        from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

        global _TMPDIR

        tmpdir = tempfile.mkdtemp(prefix="gludd-multi-pipeline-e2e-")
        _TMPDIR = tmpdir
        mgr = LocalInferenceManager()
        servers: list[LocalServer] = []
        ports: dict[str, int] = {}

        try:
            downloaded: dict[str, DownloadedModel] = {}

            for role, cfg in _MULTI_PIPELINE_MODELS.items():
                cache_dir = f"/tmp/gludd-{cfg['name']}-e2e-model"
                cached = _find_cached_gguf(cache_dir, cfg["filename"])
                if cached is not None and os.path.isfile(cached):
                    downloaded[role] = DownloadedModel(
                        model_id=cfg["repo"],
                        local_path=cached,
                        source=DownloadSource.CACHE,
                        filename=cfg["filename"],
                        size_bytes=os.path.getsize(cached),
                    )
                else:
                    downloader = ModelDownloader(cache_dir=tmpdir)
                    try:
                        downloaded[role] = downloader.download_gguf(cfg["repo"], cfg["filename"])
                    except Exception as exc:
                        pytest.skip(f"Model download failed for {cfg['name']}: {exc}")

                mdl = downloaded[role]
                assert mdl.local_path
                assert os.path.isfile(mdl.local_path), f"{cfg['name']} not found at {mdl.local_path}"
                assert mdl.size_bytes > 0

            for role, cfg in _MULTI_PIPELINE_MODELS.items():
                port = _find_free_port()
                ports[role] = port

                config = LocalServerConfig(
                    engine="llamacpp",
                    model_path=downloaded[role].local_path,
                    host="localhost",
                    port=port,
                    gpu_layers=0,
                    context_size=cfg["context_size"],
                    startup_timeout=120.0,
                )
                server = mgr.create_server(config)
                servers.append(server)

                try:
                    await mgr.start_server(server.server_id)
                except RuntimeError as exc:
                    await mgr.stop_all()
                    pytest.skip(f"Server failed to start for {cfg['name']}: {exc}")

                assert server.is_running, f"{cfg['name']} server not running"
                await _wait_for_server(f"http://localhost:{port}")

            registry = ProviderRegistry()
            registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
            secrets = EnvSecretsManager()

            profiles: list[ModelProfile] = []
            for role, _role_key in [
                ("planner", "planner"),
                ("coder", "coder"),
                ("reviewer", "reviewer"),
            ]:
                port = ports[role]
                profile_id = f"local-multi-{role}"
                secrets.set(f"LOCAL_MULTI_{role.upper()}_BASE", f"http://localhost:{port}/v1")
                secrets.set(f"LOCAL_MULTI_{role.upper()}_KEY", "not-needed")
                profiles.append(
                    ModelProfile(
                        model_profile_id=profile_id,
                        provider="openai",
                        provider_package="langchain_openai",
                        provider_class_hint="ChatOpenAI",
                        model_name="local-model",
                        api_base_alias=f"LOCAL_MULTI_{role.upper()}_BASE",
                        credential_alias=f"LOCAL_MULTI_{role.upper()}_KEY",
                        context_window=2048,
                        max_input_tokens=1500,
                        max_output_tokens=1024,
                        cost_per_input_token=0.0,
                        cost_per_output_token=0.0,
                        api_metered=False,
                        run_budget_usd=0.0,
                        enabled=True,
                        resource_profile="ai_light",
                        roles=[role],
                        latency_class="medium",
                        quality_class="variable",
                    )
                )

            gateway = ModelGateway(
                profiles=profiles,
                provider_registry=registry,
                secrets_manager=secrets,
            )

            pipeline = MultiModelGamePipeline(gateway)
            t0 = time.time()

            code = pipeline.generate(
                _SNAKE_DESCRIPTION,
                planner_model="local-multi-planner",
                coder_model="local-multi-coder",
                reviewer_model="local-multi-reviewer",
                max_review_rounds=1,
            )

            elapsed = time.time() - t0

            assert isinstance(code, str), "generated code must be a string"
            assert len(code) > 50, f"generated code too short ({len(code)} chars)"
            assert elapsed < 600, f"multi-model generation took too long: {elapsed:.1f}s"

            result = _verify_generated_code(code, tmpdir)
            assert result["passed"] is True, "code verification failed"

            await mgr.stop_all()
            for s in servers:
                assert s.status == "stopped", f"server {s.server_id} not stopped"

        finally:
            with contextlib.suppress(Exception):
                await mgr.stop_all()
            _TMPDIR = None
            shutil.rmtree(tmpdir, ignore_errors=True)
