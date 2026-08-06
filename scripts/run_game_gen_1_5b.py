#!/usr/bin/env python3
"""Test Qwen2.5-1.5B-Q4_K_M GGUF for game gen quality: snake, tetris, pong."""

from __future__ import annotations

import ast
import os
import sys
import time

MODEL_PATH = os.environ.get(
    "GLUDD_GAME_GEN_MODEL",
    os.path.expanduser(
        "~/.cache/huggingface/hub/models--bartowski--Qwen2.5-1.5B-Instruct-GGUF/"
        "snapshots/9eadc66189c7641e1ddd226b8267a9119b2ce2d4/"
        "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    ),
)

PROMPTS: dict[str, str] = {
    "snake": (
        "Write a complete Python Snake game as a single class Snake with methods: "
        "__init__, start, tick(direction), score->int, is_game_over->bool, restart. "
        "Output ONLY Python code, no explanation."
    ),
    "tetris": (
        "Write a complete Python Tetris game as a single class Tetris with methods: "
        "__init__, start, tick(direction), score->int, is_game_over->bool, restart. "
        "Output ONLY Python code, no explanation."
    ),
    "pong": (
        "Write a complete Python Pong game as a single class Pong with methods: "
        "__init__, start, tick(direction), score->int, is_game_over->bool, restart. "
        "Output ONLY Python code, no explanation."
    ),
}


def check_syntax(code: str) -> tuple[bool, str, set[str]]:
    """Return (parse_ok, error_msg, class_names_found)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, str(e), set()
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    return True, "", classes


def main() -> int:
    import llama_cpp

    if not os.path.exists(MODEL_PATH):
        print(f"Model not found: {MODEL_PATH}", file=sys.stderr)
        return 1

    size_mb = os.path.getsize(MODEL_PATH) / 1e6
    print(f"Model: {os.path.basename(MODEL_PATH)} ({size_mb:.0f} MB)")

    t0 = time.time()
    llm = llama_cpp.Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
    print(f"Loaded in {time.time() - t0:.1f}s\n")

    results: list[dict] = []

    for game_name, prompt in PROMPTS.items():
        print(f"{'=' * 60}")
        print(f"GAME: {game_name}")
        print(f"{'=' * 60}")

        t0 = time.time()
        output = llm(prompt, max_tokens=768, echo=False, temperature=0.2)
        elapsed = time.time() - t0
        code = output["choices"][0]["text"]
        tokens = output["usage"]["completion_tokens"]
        tok_s = tokens / elapsed if elapsed > 0 else 0

        parse_ok, parse_err, classes = check_syntax(code)

        print(f"  Tokens: {tokens} in {elapsed:.1f}s ({tok_s:.1f} tok/s)")
        print(f"  AST parse: {'OK' if parse_ok else 'FAIL'}")
        if parse_err:
            print(f"  Parse error: {parse_err}")
        print(f"  Classes: {sorted(classes) if classes else 'NONE'}")
        print(f"  Code length: {len(code)} chars")

        results.append(
            {
                "game": game_name,
                "tokens": tokens,
                "elapsed": elapsed,
                "tok_s": tok_s,
                "parse_ok": parse_ok,
                "parse_err": parse_err,
                "classes": classes,
                "code_len": len(code),
            }
        )
        print()

    print(f"{'=' * 60}")
    print("QUALITY REPORT")
    print(f"{'=' * 60}")
    for r in results:
        status = "OK" if r["parse_ok"] else "FAIL"
        print(
            f"  {r['game']:8s}  AST:{status:5s}  "
            f"{r['tokens']:4d} tok  {r['tok_s']:6.1f} tok/s  "
            f"classes: {sorted(r['classes']) if r['classes'] else '-'}"
        )

    all_ok = all(r["parse_ok"] for r in results)
    print(f"\nVERDICT: {'ALL PARSE OK' if all_ok else 'SYNTAX ERRORS FOUND'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
