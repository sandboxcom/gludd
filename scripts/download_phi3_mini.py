#!/usr/bin/env python3
"""Download Phi-3.1-mini-4k-instruct GGUF (~2.2 GB), run game gen + benchmark."""

from __future__ import annotations

import glob
import os
import sys
import time

CACHE_DIR = "/tmp/gludd-phi3-mini-model"
MODEL_ID = "bartowski/Phi-3.1-mini-4k-instruct-GGUF"
MODEL_FILENAME = "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf"

SNAKE_PROMPT = """Write a complete, self-contained Python Snake game as a single class.

Output ONLY the Python code — no prose, no markdown, no explanation.

The game must be a class with the following lifecycle methods:
- __init__(self): set up initial state
- start(self): begin/reset the game
- tick(self, direction): advance the snake by one step in the given direction ('up','down','left','right')
- score(self) -> int: return current score
- is_game_over(self) -> bool: return whether the game is over
- restart(self): reset everything

class Snake:
"""


def _find_gguf(directory: str) -> str | None:
    ggufs = glob.glob(os.path.join(directory, "*.gguf"))
    return ggufs[0] if ggufs else None


def run_quality_check(model_path: str) -> dict:
    import ast
    import importlib.util
    import tempfile
    from pathlib import Path

    import llama_cpp

    model_size_mb = os.path.getsize(model_path) / 1e6
    t0 = time.time()
    llm = llama_cpp.Llama(model_path=model_path, n_ctx=2048, verbose=False)
    load_s = time.time() - t0

    # ── Game gen ──
    t1 = time.time()
    output = llm(SNAKE_PROMPT, max_tokens=1024, echo=False, temperature=0.2)
    game_s = time.time() - t1
    text = output["choices"][0]["text"]
    tok_count = output["usage"]["completion_tokens"]

    # ── Parse output ──
    parse_ok = False
    has_snake = False
    required = {"__init__", "start", "tick", "score", "is_game_over", "restart"}
    methods_found: set[str] = set()

    try:
        tree = ast.parse(text)
        parse_ok = True
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "Snake" in node.name:
                has_snake = True
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef):
                        methods_found.add(child.name)
    except SyntaxError:
        pass

    missing = required - methods_found

    # ── Import + runtime ──
    import_ok = False
    runtime_ok = False
    tmp = tempfile.mkdtemp(prefix="gludd-phi3-verify-")
    try:
        game_path = Path(tmp) / "game.py"
        game_path.write_text(text)
        spec = importlib.util.spec_from_file_location("game", str(game_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import_ok = True
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and (hasattr(obj, "tick") or hasattr(obj, "start")):
                    try:
                        game = obj()
                        if hasattr(game, "start"):
                            game.start()
                        s = game.score() if hasattr(game, "score") else None
                        g = game.is_game_over() if hasattr(game, "is_game_over") else None
                        for _ in range(5):
                            if hasattr(game, "is_game_over") and hasattr(game, "tick"):
                                if not game.is_game_over():
                                    game.tick("right")
                        if hasattr(game, "restart"):
                            game.restart()
                        runtime_ok = True
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    # ── Code snippet (abridged) ──
    snippet = text[:400]

    return {
        "model_size_mb": model_size_mb,
        "load_s": load_s,
        "game_s": game_s,
        "tok_count": tok_count,
        "tok_per_s": tok_count / game_s if game_s > 0 else 0,
        "parse_ok": parse_ok,
        "has_snake": has_snake,
        "methods_found": methods_found,
        "missing": missing,
        "import_ok": import_ok,
        "runtime_ok": runtime_ok,
        "snippet": snippet,
    }


def run_benchmark(model_path: str, n: int = 5) -> dict:
    import llama_cpp

    t0 = time.time()
    llm = llama_cpp.Llama(model_path=model_path, n_ctx=512, verbose=False)
    load_s = time.time() - t0

    ttf_times: list[float] = []
    tok_s_rates: list[float] = []

    for _ in range(n):
        batch_t0 = time.time()
        first_token = True
        first_ts = 0.0
        token_count = 0

        stream = llm("def fibonacci(n):", max_tokens=32, echo=False, stream=True)
        for chunk in stream:
            choices = chunk.get("choices", [])
            if first_token and choices:
                first_ts = time.time()
                first_token = False
            if choices and "text" in choices[0]:
                token_count += 1

        batch_s = time.time() - batch_t0
        ttf = first_ts - batch_t0 if first_ts else batch_s
        tok_s = token_count / batch_s if batch_s > 0 else 0.0
        ttf_times.append(ttf)
        tok_s_rates.append(tok_s)

    return {
        "load_s": load_s,
        "avg_ttft_ms": (sum(ttf_times) / len(ttf_times)) * 1000,
        "avg_tok_s": sum(tok_s_rates) / len(tok_s_rates),
        "n": n,
    }


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    # ── Step 1: Download ──
    existing = _find_gguf(CACHE_DIR)
    if existing:
        print(f"=== Using cached model: {existing} ===")
    else:
        from general_ludd.small_models.download import ModelDownloader

        print(f"=== Downloading Phi-3.1-mini-4k-instruct GGUF ===")
        t0 = time.time()
        d = ModelDownloader(cache_dir=CACHE_DIR)
        result = d.download_gguf(model_id=MODEL_ID, filename=MODEL_FILENAME)
        elapsed = time.time() - t0
        print(f"Downloaded: {result.local_path} ({result.size_bytes / 1e6:.0f} MB) in {elapsed:.0f}s")
        print()

    model_path = _find_gguf(CACHE_DIR)
    if not model_path:
        print(f"ERROR: No GGUF found in {CACHE_DIR}", file=sys.stderr)
        return 1

    model_size_mb = os.path.getsize(model_path) / 1e6
    print(f"Model: {os.path.basename(model_path)} ({model_size_mb:.0f} MB)")
    print()

    # ── Step 2: Game gen quality ──
    print("=" * 60)
    print("GAME GEN QUALITY CHECK (Phi-3.1-mini-4k)")
    print("=" * 60)
    q = run_quality_check(model_path)
    print(f"  Load time:        {q['load_s']:.1f}s")
    print(f"  Generation time:  {q['game_s']:.1f}s")
    print(f"  Tokens:           {q['tok_count']} ({q['tok_per_s']:.1f} tok/s)")
    print(f"  AST parse:        {'OK' if q['parse_ok'] else 'FAIL'}")
    print(f"  Snake class:      {'YES' if q['has_snake'] else 'NO'}")
    print(f"  Methods:          {len(q['methods_found'])}/6 ({', '.join(sorted(q['methods_found']))})")
    if q["missing"]:
        print(f"  Missing methods:  {', '.join(sorted(q['missing']))}")
    print(f"  Import:           {'OK' if q['import_ok'] else 'FAIL'}")
    print(f"  Runtime:          {'OK' if q['runtime_ok'] else 'FAIL'}")
    print()
    print("--- Generated code (first 400 chars) ---")
    print(q["snippet"])
    print("--- end ---")
    print()

    # ── Step 3: Speed benchmark ──
    print("=" * 60)
    print("SPEED BENCHMARK (Phi-3.1-mini-4k)")
    print("=" * 60)
    b = run_benchmark(model_path)
    print(f"  Model load:       {b['load_s']:.1f}s")
    print(f"  Inferences:       {b['n']}")
    print(f"  Avg TTFT:         {b['avg_ttft_ms']:.0f}ms")
    print(f"  Avg tok/s:        {b['avg_tok_s']:.1f}")
    print()

    # ── Verdict ──
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)
    print(f"Download:  {'CACHED' if existing else 'FRESH'} ({model_size_mb:.0f} MB)")
    print(f"Quality:   {'FUNCTIONAL' if q['parse_ok'] and q['runtime_ok'] else 'INCOMPLETE'}")
    print(f"           AST={q['parse_ok']}, import={q['import_ok']}, runtime={q['runtime_ok']}")
    print(f"           Methods found: {len(q['methods_found'])}/6")
    print(f"Speed:     {q['tok_per_s']:.1f} tok/s (game gen), {b['avg_tok_s']:.1f} tok/s (benchmark)")
    print(f"           Load: {q['load_s']:.1f}s, TTFT: {b['avg_ttft_ms']:.0f}ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())
