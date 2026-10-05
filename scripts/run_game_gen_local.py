#!/usr/bin/env python3
"""Full pipeline: download tiny model, serve locally, generate a Snake game, verify.

Run with: make run-game-gen-local
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import traceback
from pathlib import Path

import httpx

SNAKE_PROMPT = """Write a complete, self-contained Python Snake game as a single class.

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


def main() -> int:
    # ── Step 0: Check deps ──
    try:
        llama_cpp_available = importlib.util.find_spec("llama_cpp") is not None
    except (ImportError, ValueError):
        llama_cpp_available = False
    if not llama_cpp_available:
        print("llama-cpp-python not installed. Run: make sync-llama-cpp", flush=True)
        return 1

    try:
        hub_available = importlib.util.find_spec("huggingface_hub") is not None
    except (ImportError, ValueError):
        hub_available = False
    if not hub_available:
        print("huggingface_hub not installed", flush=True)
        return 1

    # ── Step 1: Download model from bartowski (Q5_K_M — different quant than Q4_K_M) ──
    print("=== STEP 1: Download model ===", flush=True)
    from general_ludd.small_models.download import ModelDownloader

    MODEL_REPO = "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
    MODEL_FILE = "Qwen2.5-0.5B-Instruct-Q5_K_M.gguf"

    downloader = ModelDownloader()
    try:
        model = downloader.download_gguf(MODEL_REPO, MODEL_FILE)
    except Exception as e:
        print(f"Download failed: {e}", flush=True)
        traceback.print_exc()
        return 1
    print(f"  Downloaded: {model.local_path} ({model.size_bytes / 1e6:.1f} MB)", flush=True)

    # ── Step 2: Start llama.cpp server ──
    print("=== STEP 2: Start local inference server ===", flush=True)
    from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig

    port = 9999
    config = LocalServerConfig(
        engine="llamacpp",
        model_path=model.local_path,
        host="localhost",
        port=port,
        gpu_layers=0,
        context_size=2048,
        startup_timeout=120.0,
    )

    async def run_pipeline() -> int:
        mgr = LocalInferenceManager()
        server = mgr.create_server(config)
        tmp_owner = None
        print(f"  Server ID: {server.server_id}", flush=True)

        try:
            cmd = mgr._build_command(server.config)
            print(f"  Command: {' '.join(cmd)}", flush=True)

            server = await mgr.start_server(server.server_id)
            print(f"  Server running at {server.endpoint_url} (PID={server.pid})", flush=True)

            # ── Step 3: Verify server endpoints ──
            print("=== STEP 3: Verify server endpoints ===", flush=True)

            completions_url = f"http://{server.config.host}:{server.config.port}/v1/completions"
            health_url = f"http://{server.config.host}:{server.config.port}/health"

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(health_url)
                print(f"  /health: {resp.status_code}", flush=True)

                resp = await client.post(
                    completions_url,
                    json={"prompt": "Hello", "max_tokens": 1},
                )
                print(f"  /v1/completions: {resp.status_code} (not streaming)", flush=True)
                if resp.status_code == 200:
                    body = resp.json()
                    if "choices" in body:
                        print(f"  /v1/completions response OK — {len(body['choices'])} choice(s)", flush=True)
                    else:
                        print(f"  /v1/completions response: {body}", flush=True)
                else:
                    print(f"  /v1/completions returned {resp.status_code}: {resp.text[:300]}", flush=True)

            # ── Step 4: Build gateway and generate game ──
            print("=== STEP 4: Generate Snake game ===", flush=True)

            from general_ludd.cloud.game_e2e import GameGenerator, GameSpec
            from general_ludd.models.gateway import ModelGateway, ModelProfile
            from general_ludd.models.provider_registry import ProviderRegistry
            from general_ludd.secrets.env import EnvSecretsManager

            profile_id = "local-snake-test"
            profile = ModelProfile(
                model_profile_id=profile_id,
                provider="openai",
                provider_package="langchain_openai",
                provider_class_hint="ChatOpenAI",
                model_name="local-model",
                api_base_alias="LOCAL_MODEL_BASE",
                credential_alias="LOCAL_MODEL_KEY",
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
            secrets.set("LOCAL_MODEL_BASE", server.endpoint_url)
            secrets.set("LOCAL_MODEL_KEY", "not-needed")

            gateway = ModelGateway(
                profiles=[profile],
                provider_registry=registry,
                secrets_manager=secrets,
            )

            t0 = time.time()

            gen = GameGenerator(gateway)
            spec = GameSpec(
                name="snake",
                genre="arcade",
                description="Snake game",
                prompt_template=SNAKE_PROMPT,
                expected_frames=30,
                similarity_threshold=0.0,
            )

            code = gen.generate_game(spec, model_id=profile_id)
            elapsed = time.time() - t0
            print(f"  Generation took {elapsed:.1f}s", flush=True)
            print(f"  Code length: {len(code)} chars", flush=True)

            # ── Step 5: Verify code ──
            print("=== STEP 5: Verify generated code ===", flush=True)

            import ast

            try:
                tree = ast.parse(code)
                print("  AST parse: OK", flush=True)
            except SyntaxError as e:
                print(f"  AST parse: FAILED — {e}", flush=True)
                print("  Generated code:\n" + code[:2000])
                return 1

            classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            class_names = [c.name for c in classes]
            print(f"  Classes: {class_names}", flush=True)

            methods: dict[str, bool] = {}
            for cls in classes:
                for node in ast.walk(cls):
                    if isinstance(node, ast.FunctionDef):
                        methods[node.name] = True
            print(f"  Methods: {list(methods.keys())}", flush=True)

            required = {"__init__", "start", "tick", "score", "is_game_over", "restart"}
            missing = required - set(methods.keys())
            if missing:
                print(f"  WARNING: Missing methods: {missing}", flush=True)

            # Write to temp and import
            import tempfile

            tmp_owner = tempfile.TemporaryDirectory(prefix="gludd-game-")
            tmp_dir = tmp_owner.name
            game_path = Path(tmp_dir) / "game_snake.py"
            game_path.write_text(code)

            spec_obj = importlib.util.spec_from_file_location("game_snake", str(game_path))
            if spec_obj is None or spec_obj.loader is None:
                print("  Import: FAILED", flush=True)
                return 1

            try:
                mod = importlib.util.module_from_spec(spec_obj)
                spec_obj.loader.exec_module(mod)
                print("  Import: OK", flush=True)
            except Exception as e:
                print(f"  Import: FAILED — {e}", flush=True)
                traceback.print_exc()
                return 1

            # Instantiate
            game_cls = None
            for name in class_names:
                if hasattr(mod, name):
                    game_cls = getattr(mod, name)
                    break
            if game_cls is None:
                print("  No game class in module", flush=True)
                return 1

            print("=== STEP 6: Runtime verification ===", flush=True)
            try:
                game = game_cls()
                print(f"  Instantiated {game_cls.__name__}", flush=True)

                if hasattr(game, "start"):
                    game.start()
                    print("  start(): OK", flush=True)

                if hasattr(game, "score"):
                    print(f"  score(): {game.score()}", flush=True)

                if hasattr(game, "is_game_over"):
                    print(f"  is_game_over(): {game.is_game_over()}", flush=True)

                if hasattr(game, "tick"):
                    for _ in range(5):
                        if hasattr(game, "is_game_over") and not game.is_game_over():
                            game.tick("right")
                    print("  tick() x5: OK", flush=True)

                if hasattr(game, "restart"):
                    game.restart()
                    print("  restart(): OK", flush=True)
                    if hasattr(game, "score"):
                        print(f"  score after restart: {game.score()}", flush=True)

            except Exception as e:
                print(f"  Runtime FAILED: {type(e).__name__}: {e}", flush=True)
                traceback.print_exc()

            print("=== SUMMARY ===", flush=True)
            print(f"  Generated: {len(code)} chars", flush=True)
            print("  AST parse: OK", flush=True)
            print("  Import: OK", flush=True)
            print(f"  Classes: {class_names}", flush=True)
            print(f"  Methods: {list(methods.keys())}", flush=True)
            if missing:
                print(f"  Missing methods: {missing}", flush=True)
            print(f"  Total time: {elapsed:.1f}s", flush=True)
            print("\n--- Generated code (first 800 chars) ---")
            print(code[:800])
            print("--- end ---")

            return 0

        except RuntimeError as e:
            print(f"\n  Server start failed: {e}", flush=True)
            return 1

        finally:
            if tmp_owner is not None:
                tmp_owner.cleanup()
            print("\n=== CLEANUP: Stopping server ===", flush=True)
            await mgr.stop_all()

    return asyncio.run(run_pipeline())


if __name__ == "__main__":
    sys.exit(main())
