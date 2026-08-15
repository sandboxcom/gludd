"""E2E: Real model game generation — download, serve, generate snake, verify, cleanup.

Pipeline:
1. Check dependencies (llama-cpp-python, huggingface_hub)
2. Download a GGUF model to temp dir via ModelDownloader
3. Start llama.cpp server via LocalInferenceManager
4. Build ModelGateway → generate snake game via GameGenerator
5. Verify output is valid Python (AST parse, import, required methods)
6. Clean up server + temp files

Models tested are defined in tests/e2e/_local_model_configs.py (LOCAL_GGUF_MODELS).
Filter via E2E_LOCAL_MODEL env var (e.g. E2E_LOCAL_MODEL=SmolLM2-360M).
Skip if deps unavailable. Model not stored in repo; downloaded at runtime to temp dir.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest

from general_ludd.local_model._local_model_configs import LocalModelConfig

from ._local_model_configs import get_e2e_configs

_E2E_MODELS = get_e2e_configs()


def _find_cached_gguf(cache_dir: str) -> str | None:
    """Return path to first .gguf file in the E2E cache dir, or None."""
    cache = Path(cache_dir)
    if not cache.is_dir():
        return None
    for f in cache.iterdir():
        if f.suffix == ".gguf" and f.is_file():
            return str(f)
    return None


_SNAKE_PROMPT = """Write a complete, self-contained Python Snake game as a single class.

Output ONLY the Python code — no prose, no markdown, no explanation.

The game must be a class with the following lifecycle methods:
- __init__(self): set up initial state
- start(self): begin/reset the game
- tick(self, direction): advance the snake by one step in the given direction ('up','down','left','right')
- score(self) -> int: return current score
- is_game_over(self) -> bool: return whether the game is over
- restart(self): reset everything

Lifecycle requirements:
- state: the snake is a list of (x,y) tuples, head is first element
- start(): must reset score to 0 and place food randomly
- restart(): must reset everything
- score starts at 0 after start() and restart()
- score increments by 1 when the snake eats food
- game_over is true when the snake hits a wall (0 <= x < 20, 0 <= y < 20) or itself
- game_over is idempotent: once true, stays true until restart()
- tick() while game_over does nothing

class Snake:
    # your implementation
"""


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


def _deps_reason() -> str | None:
    missing: list[str] = []
    if not _has_llama_cpp():
        missing.append("llama-cpp-python")
    if not _has_huggingface_hub():
        missing.append("huggingface_hub")
    return f"Missing deps: {', '.join(missing)}" if missing else None


_REASON = _deps_reason()
if _REASON is not None:
    pytestmark = pytest.mark.skip(reason=_REASON)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


_TMPDIR: str | None = None


def teardown_module() -> None:
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.parametrize(
    "model_config",
    [pytest.param(m, id=m.name) for m in _E2E_MODELS],
)
class TestGameGenRealModel:
    """Full pipeline: download real model → start server → generate snake → verify → cleanup."""

    @pytest.mark.asyncio
    async def test_download_serve_generate_verify_cleanup(self, model_config: LocalModelConfig) -> None:
        """End-to-end with real model download, local server, game generation, verification."""
        from general_ludd.cloud.game_e2e import GameGenerator, GameSpec
        from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry
        from general_ludd.secrets.env import EnvSecretsManager
        from general_ludd.small_models.download import DownloadedModel, DownloadSource, ModelDownloader

        global _TMPDIR

        cache_dir = f"/tmp/gludd-{model_config.name}-e2e-model"
        tmpdir = tempfile.mkdtemp(prefix="gludd-game-gen-e2e-")
        _TMPDIR = tmpdir
        mgr = None

        try:
            # ── Step 1: Download model (skip if cache hit) ──
            cached_path = _find_cached_gguf(cache_dir)
            if cached_path is not None and os.path.isfile(cached_path):
                model = DownloadedModel(
                    model_id=model_config.repo,
                    local_path=cached_path,
                    source=DownloadSource.CACHE,
                    filename=os.path.basename(cached_path),
                    size_bytes=os.path.getsize(cached_path),
                )
            else:
                downloader = ModelDownloader(cache_dir=tmpdir)
                try:
                    model = downloader.download_gguf(model_config.repo, model_config.filename)
                except Exception as exc:
                    pytest.skip(f"Model download failed: {exc}")

            assert model.local_path, "download must produce a local path"
            assert os.path.isfile(model.local_path), f"model file not found: {model.local_path}"
            assert model.size_bytes > 0, "downloaded model must have non-zero size"

            # ── Step 2: Start llama.cpp server ──
            port = _find_free_port()
            base_url = f"http://localhost:{port}"

            mgr = LocalInferenceManager()
            config = LocalServerConfig(
                engine="llamacpp",
                model_path=model.local_path,
                host="localhost",
                port=port,
                gpu_layers=0,
                context_size=model_config.context_size,
                startup_timeout=120.0,
            )
            server = mgr.create_server(config)

            try:
                await mgr.start_server(server.server_id)
            except RuntimeError as exc:
                pytest.skip(f"Server failed to start: {exc}")

            assert server.is_running, f"server status: {server.status}"

            # Wait for server readiness
            import httpx

            async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
                for _attempt in range(30):
                    try:
                        resp = await client.get("/v1/models")
                        if resp.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    await mgr.stop_all()
                    pytest.fail("Server /v1/models did not become 200 within 30s")

                # Warm-up inference
                warmup_resp = await client.post(
                    "/v1/completions",
                    json={"prompt": "Hello", "max_tokens": 1},
                )
                assert warmup_resp.status_code == 200, f"warmup failed: {warmup_resp.text}"

            # ── Step 3: Build ModelGateway ──
            profile_id = "local-game-gen-test"
            profile = ModelProfile(
                model_profile_id=profile_id,
                provider="openai",
                provider_package="langchain_openai",
                provider_class_hint="ChatOpenAI",
                model_name="local-model",
                api_base_alias="LOCAL_GAME_GEN_BASE",
                credential_alias="LOCAL_GAME_GEN_KEY",
                context_window=2048,
                max_input_tokens=1500,
                max_output_tokens=1024,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
                api_metered=False,
                run_budget_usd=0.0,
                enabled=True,
                resource_profile="ai_light",
                roles=["coder"],
                latency_class="medium",
                quality_class="variable",
            )
            registry = ProviderRegistry()
            registry.register_provider("openai", "langchain_openai", "ChatOpenAI")
            secrets = EnvSecretsManager()
            secrets.set("LOCAL_GAME_GEN_BASE", f"http://localhost:{port}/v1")
            secrets.set("LOCAL_GAME_GEN_KEY", "not-needed")

            gateway = ModelGateway(
                profiles=[profile],
                provider_registry=registry,
                secrets_manager=secrets,
            )

            # ── Step 4: Generate snake game ──
            t0 = time.time()
            gen = GameGenerator(gateway)
            spec = GameSpec(
                name="snake",
                genre="arcade",
                description="Snake game",
                prompt_template=_SNAKE_PROMPT,
                expected_frames=30,
                similarity_threshold=0.0,
            )

            code = gen.generate_game(spec, model_id=profile_id)
            elapsed = time.time() - t0

            assert isinstance(code, str), "generated code must be a string"
            assert len(code) > 50, f"generated code too short ({len(code)} chars)"
            assert elapsed < 300, f"generation took too long: {elapsed:.1f}s"

            # ── Step 5: Verify code ──
            import ast
            import importlib.util

            # AST parse
            try:
                tree = ast.parse(code)
            except SyntaxError as e:
                pytest.fail(f"Generated code has syntax error: {e}\nCode:\n{code[:1000]}")

            # Find class names
            classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            assert len(classes) >= 1, "generated code must contain at least one class"

            # Find methods
            methods: dict[str, bool] = {}
            for cls in classes:
                for node in ast.walk(cls):
                    if isinstance(node, ast.FunctionDef):
                        methods[node.name] = True

            # Check for required snake methods (name-agnostic check)
            required = {"__init__", "tick", "score"}
            found_methods = set(methods.keys())
            missing = required - found_methods
            # Allow alternative names for start/restart/is_game_over
            alt_start = {"start", "play", "begin", "new_game", "start_game", "reset"}
            alt_over = {"is_game_over", "game_over", "over", "crashed", "finished", "ended", "dead", "done"}
            alt_restart = {"restart", "reset", "new_game", "start_over", "play_again"}

            has_start = bool(found_methods & alt_start)
            has_over = bool(found_methods & alt_over)
            has_restart = bool(found_methods & alt_restart)

            if not has_start:
                missing.add("start")
            if not has_over:
                missing.add("is_game_over")
            if not has_restart:
                missing.add("restart")

            assert not missing, f"Missing methods: {sorted(missing)}. Found: {sorted(found_methods)}"

            # Import generated module
            game_path = Path(tmpdir) / "game_snake.py"
            game_path.write_text(code)

            spec_obj = importlib.util.spec_from_file_location("game_snake", str(game_path))
            assert spec_obj is not None and spec_obj.loader is not None, "failed to create module spec"

            mod = importlib.util.module_from_spec(spec_obj)
            spec_obj.loader.exec_module(mod)

            # Find and instantiate game class
            game_cls = None
            for name in [c.name for c in classes]:
                if hasattr(mod, name):
                    game_cls = getattr(mod, name)
                    break
            assert game_cls is not None, "no game class found in module"

            game = game_cls()
            assert game is not None, "failed to instantiate game"

            # Runtime checks
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

            # ── Step 6: Stop server ──
            await mgr.stop_all()
            assert server.status == "stopped"

        finally:
            if mgr is not None:
                with contextlib.suppress(Exception):
                    await mgr.stop_all()
            _TMPDIR = None
            shutil.rmtree(tmpdir, ignore_errors=True)
