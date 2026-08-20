#!/usr/bin/env python3
"""Quick quality check: load Qwen2.5-0.5B GGUF, run game gen + code completion."""

from __future__ import annotations

import glob
import os
import sys
import time
from importlib import import_module

MODEL_DIR = "/tmp/gludd-qwen-e2e-model"

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

FIBONACCI_PROMPT = "def fibonacci(n):"


def main() -> int:
    llama_cpp = import_module("llama_cpp")

    ggufs = glob.glob(os.path.join(MODEL_DIR, "*.gguf"))
    if not ggufs:
        print(f"ENOENT: No GGUF found in {MODEL_DIR}", file=sys.stderr)
        return 1
    model_path = ggufs[0]
    model_size_mb = os.path.getsize(model_path) / 1e6

    print(f"Model: {os.path.basename(model_path)} ({model_size_mb:.0f} MB)")
    print(f"llama_cpp: {llama_cpp.__version__}")
    print()

    # ── Load ──
    t0 = time.time()
    llm = llama_cpp.Llama(model_path=model_path, n_ctx=2048, verbose=False)
    load_time = time.time() - t0
    print(f"Model loaded in {load_time:.1f}s")
    print()

    # ── Test 1: Snake game gen ──
    print("=" * 60)
    print("TEST 1: Snake game generation")
    print("=" * 60)
    t0 = time.time()
    output = llm(SNAKE_PROMPT, max_tokens=1024, echo=False, temperature=0.2)
    game_time = time.time() - t0
    text = output["choices"][0]["text"]
    token_count = output["usage"]["completion_tokens"]

    print(f"Tokens: {token_count} in {game_time:.1f}s ({token_count / game_time:.1f} tok/s)")
    print(f"Output length: {len(text)} chars")
    print()

    # Parse the output
    import ast

    parse_ok = False
    has_snake_class = False
    required_methods = {"__init__", "start", "tick", "score", "is_game_over", "restart"}
    methods_found: set[str] = set()

    try:
        tree = ast.parse(text)
        parse_ok = True
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Snake":
                has_snake_class = True
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef):
                        methods_found.add(child.name)
    except SyntaxError as e:
        print(f"PARSE ERROR: {e}")

    print(f"AST parse: {'OK' if parse_ok else 'FAIL'}")
    print(f"Snake class: {'YES' if has_snake_class else 'NO'}")
    print(f"Methods found: {sorted(methods_found)}")
    missing = required_methods - methods_found
    if missing:
        print(f"Methods missing: {sorted(missing)}")
    else:
        print("All 6 required methods present!")

    # Import test
    import importlib.util
    import tempfile
    from pathlib import Path

    import_ok = False
    runtime_ok = False
    with tempfile.TemporaryDirectory(prefix="gludd-verify-") as tmp:
        game_path = Path(tmp) / "game.py"
        game_path.write_text(text)
        try:
            spec = importlib.util.spec_from_file_location("game", str(game_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                import_ok = True
        except Exception as e:
            print(f"Import FAILED: {type(e).__name__}: {e}")

        print(f"Import: {'OK' if import_ok else 'FAIL'}")

        # Instantiate and run
        if import_ok and hasattr(mod, "Snake"):
            try:
                game = mod.Snake()
                game.start()
                s = game.score()
                go = game.is_game_over()
                for _ in range(5):
                    if not game.is_game_over():
                        game.tick("right")
                game.restart()
                runtime_ok = True
                print(f"Runtime: OK (score={s}, game_over={go})")
            except Exception as e:
                print(f"Runtime FAILED: {type(e).__name__}: {e}")

    print()

    # ── Code snippet ──
    print("--- Generated code (first 600 chars) ---")
    print(text[:600])
    print("--- end ---")
    print()

    # ── Test 2: Fibonacci completion ──
    print("=" * 60)
    print("TEST 2: Code completion — fibonacci")
    print("=" * 60)
    t0 = time.time()
    output = llm(FIBONACCI_PROMPT, max_tokens=128, echo=True, temperature=0.1)
    fib_time = time.time() - t0
    text = output["choices"][0]["text"]
    fib_tokens = output["usage"]["completion_tokens"]

    print(f"Tokens: {fib_tokens} in {fib_time:.1f}s ({fib_tokens / fib_time:.1f} tok/s)")
    print()
    print(text.strip())
    print()

    # ── Summary ──
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Model: {os.path.basename(model_path)} ({model_size_mb:.0f} MB)")
    print(f"Load time: {load_time:.1f}s")
    print()
    print(f"Game gen: {token_count} tokens, {token_count / game_time:.1f} tok/s")
    print(f"  AST: {'OK' if parse_ok else 'FAIL'}")
    print(f"  Snake class: {'YES' if has_snake_class else 'NO'}")
    print(f"  Methods: {len(methods_found)}/6 ({', '.join(sorted(methods_found))})")
    print(f"  Import: {'OK' if import_ok else 'FAIL'}")
    print(f"  Runtime: {'OK' if runtime_ok else 'FAIL'}")
    print()
    print(f"Fibonacci: {fib_tokens} tokens, {fib_tokens / fib_time:.1f} tok/s")
    print()

    if parse_ok and has_snake_class and import_ok and runtime_ok:
        print("VERDICT: Inference works. Game gen quality is functional for a 0.5B model.")
    elif parse_ok:
        print("VERDICT: Inference works. Game gen produced valid Python but is incomplete.")
    else:
        print("VERDICT: Inference works, but game gen quality is poor (syntax errors).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
