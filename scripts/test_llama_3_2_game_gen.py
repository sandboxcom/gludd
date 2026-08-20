#!/usr/bin/env python3
"""Download Llama-3.2-1B GGUF, serve locally, run game gen prompts, compare vs Qwen/DeepSeek.

Run: make test-llama-game-gen
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

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

DOOM_HALLWAY_PROMPT = """Write a complete Python game using pygame that renders a first-person shooter scene.

Requirements:
- Grey stone-textured walls forming a long hallway
- Square pillars along the hallway sides
- Red/brown floor
- Ceiling with periodic light sources
- A green glowing pickup item at the far end of the hallway
- Player can look around with mouse and move forward/backward with W/S
- Rendering should use raycasting or simple 3D projection
- Window size 800x600
- Run for at least 30 frames then exit
- The game must be self-contained in one file and runnable with: python game.py

Output ONLY the Python code — no prose, no markdown, no explanation.
"""


def score_response(code: str) -> dict[str, Any]:
    """Score generated code quality."""
    result = {
        "len": len(code),
        "parse_ok": False,
        "class_count": 0,
        "method_count": 0,
        "import_ok": False,
        "has_game_loop": False,
        "has_pygame_import": False,
        "method_names": [],
        "runtime_ok": False,
        "score_after_start": None,
    }
    try:
        tree = ast.parse(code)
        result["parse_ok"] = True
    except SyntaxError:
        return result

    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    result["class_count"] = len(classes)

    methods_seen: list[str] = []
    for cls in classes:
        for node in ast.walk(cls):
            if isinstance(node, ast.FunctionDef):
                methods_seen.append(node.name)
    result["method_count"] = len(methods_seen)
    result["method_names"] = methods_seen

    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.For)):
            result["has_game_loop"] = True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pygame":
                    result["has_pygame_import"] = True
        elif isinstance(node, ast.ImportFrom) and node.module and "pygame" in node.module:
            result["has_pygame_import"] = True

    with tempfile.TemporaryDirectory(prefix="gludd-game-") as tmp_dir:
        game_path = Path(tmp_dir) / "game_test.py"
        game_path.write_text(code)
        spec_obj = importlib.util.spec_from_file_location("game_test", str(game_path))
        if spec_obj and spec_obj.loader:
            try:
                mod = importlib.util.module_from_spec(spec_obj)
                spec_obj.loader.exec_module(mod)
                result["import_ok"] = True
                class_names = [n.name for n in classes]
                for name in class_names:
                    if hasattr(mod, name):
                        game = getattr(mod, name)()
                        if hasattr(game, "start"):
                            game.start()
                        if hasattr(game, "score"):
                            result["score_after_start"] = game.score()
                        if hasattr(game, "tick"):
                            for _ in range(5):
                                if hasattr(game, "is_game_over") and not game.is_game_over():
                                    game.tick("right")
                        if hasattr(game, "restart"):
                            game.restart()
                        result["runtime_ok"] = True
                        break
            except Exception:
                pass
    return result


def _build_command(model_path: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "llama_cpp.server",
        "--model",
        model_path,
        "--host",
        "localhost",
        "--port",
        str(port),
        "--n_gpu_layers",
        "0",
        "--n_ctx",
        "2048",
    ]


async def _wait_ready(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    resp2 = await client.post(
                        f"{url}/v1/completions",
                        json={"prompt": "Hello", "max_tokens": 1},
                    )
                    if resp2.status_code == 200:
                        return
            except Exception:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Server did not become ready within {timeout}s")


async def _generate(via_url: str, prompt: str, max_tokens: int = 1024) -> tuple[str, float]:
    t0 = time.time()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{via_url}/v1/completions",
            json={
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "stop": ["```", "\n\n\n"],
            },
        )
        elapsed = time.time() - t0
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}: {resp.text[:500]}", elapsed
        body = resp.json()
        text = body.get("choices", [{}])[0].get("text", "")
        return text, elapsed


async def run_model(
    model_name: str,
    model_repo: str,
    model_file: str,
    prompts: dict[str, str],
    port: int,
) -> dict[str, Any]:
    from general_ludd.small_models.download import ModelDownloader

    print(f"\n{'=' * 60}")
    print(f"  MODEL: {model_name}")
    print(f"{'=' * 60}\n")

    print(f"  Downloading {model_repo}/{model_file} ...", flush=True)
    dl = ModelDownloader()
    t_dl = time.time()
    model = dl.download_gguf(model_repo, model_file)
    print(
        f"  Downloaded: {model.local_path} ({model.size_bytes / 1e6:.1f} MB) in {time.time() - t_dl:.1f}s", flush=True
    )

    print(f"  Starting llama.cpp server on port {port} ...", flush=True)
    import subprocess

    cmd = _build_command(model.local_path, port)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://localhost:{port}"
        await _wait_ready(url)
        print(f"  Server ready at {url}", flush=True)

        results: dict[str, dict[str, Any]] = {}
        for pname, prompt in prompts.items():
            print(f"\n  --- Prompt: {pname} ---", flush=True)
            code, elapsed = await _generate(url, prompt)
            score = score_response(code)
            score["elapsed_s"] = round(elapsed, 1)
            score["tokens_per_sec"] = round(len(code.split()) / max(elapsed, 0.1), 1)
            results[pname] = score
            print(f"  Generated: {score['len']} chars in {elapsed:.1f}s ({score['tokens_per_sec']} tok/s)", flush=True)
            print(
                f"  AST OK: {score['parse_ok']} | Import OK: {score['import_ok']} | Runtime: {score['runtime_ok']}",
                flush=True,
            )
            print(f"  Classes: {score['class_count']} | Methods: {score['method_count']}", flush=True)
            if score["runtime_ok"] and score["score_after_start"] is not None:
                print(f"  Score: {score['score_after_start']}", flush=True)
            if not score["parse_ok"]:
                print(f"  CODE:\n{code[:400]}\n...", flush=True)

        return {"model": model_name, "repo": model_repo, "file": model_file, "results": results}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def print_comparison(entries: list[dict[str, Any]]) -> None:
    print(f"\n{'=' * 60}")
    print("  CROSS-MODEL COMPARISON")
    print(f"{'=' * 60}")
    header = (
        f"{'Model':<25} {'Prompt':<18} {'Chars':>6} {'Time':>6} "
        f"{'Tok/s':>7} {'AST':>5} {'Import':>7} {'Runtime':>8} {'Score':>6}"
    )
    print(header)
    print("-" * len(header))
    for entry in entries:
        for pname, r in entry["results"].items():
            s = r.get("score_after_start")
            print(
                f"{entry['model']:<25} {pname:<18} {r['len']:>6} {r['elapsed_s']:>5.1f}s {r['tokens_per_sec']:>6.1f} "
                f"{'OK' if r['parse_ok'] else 'FAIL':>5} {'OK' if r['import_ok'] else 'FAIL':>7} "
                f"{'OK' if r['runtime_ok'] else 'FAIL':>8} {str(s) if s is not None else 'N/A':>6}"
            )


async def main_async() -> int:
    deps_ok = True
    try:
        llama_cpp_available = importlib.util.find_spec("llama_cpp") is not None
    except (ImportError, ValueError):
        llama_cpp_available = False
    if not llama_cpp_available:
        print("llama-cpp-python not installed. Run: make sync-llama-cpp")
        deps_ok = False
    try:
        hub_available = importlib.util.find_spec("huggingface_hub") is not None
    except (ImportError, ValueError):
        hub_available = False
    if not hub_available:
        print("huggingface_hub not installed")
        deps_ok = False
    if not deps_ok:
        return 1

    prompts = {
        "snake": SNAKE_PROMPT,
        "doom_hallway": DOOM_HALLWAY_PROMPT,
    }

    llama_result = await run_model(
        model_name="Llama-3.2-1B-Instruct",
        model_repo="bartowski/Llama-3.2-1B-Instruct-GGUF",
        model_file="Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        prompts=prompts,
        port=9991,
    )

    qwen_result = await run_model(
        model_name="Qwen2.5-0.5B-Instruct",
        model_repo="bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        model_file="Qwen2.5-0.5B-Instruct-Q5_K_M.gguf",
        prompts=prompts,
        port=9992,
    )

    print_comparison([llama_result, qwen_result])

    llama_pass = sum(1 for r in llama_result["results"].values() if r["runtime_ok"])
    qwen_pass = sum(1 for r in qwen_result["results"].values() if r["runtime_ok"])

    print(f"\n  Llama-3.2-1B  pass rate: {llama_pass}/{len(prompts)}")
    print(f"  Qwen2.5-0.5B pass rate: {qwen_pass}/{len(prompts)}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
