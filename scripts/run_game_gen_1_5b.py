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

GAME_SPEC = "a class with __init__, start, tick(direction), score->int, is_game_over->bool, restart(all reset). Must be single-file runnable."


def build_prompt(game: str) -> str:
    return (
        f"<|im_start|>user\n"
        f"Write a complete, self-contained Python {game} game as {GAME_SPEC}\n"
        f"Output ONLY valid Python code — no markdown backticks, no explanations.\n"
        f"<|im_end|>\n"
        f"<|im_start|>assistant\n"
        f"```python\n"
    )


def check_syntax(code: str) -> tuple[bool, str, set[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, str(e), set()
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    return True, "", classes


def extract_code(raw: str) -> str:
    for prefix in ("```python\n", "```python", "```\n"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    raw = raw.strip()
    suffix = raw.rfind("```")
    if suffix != -1:
        raw = raw[:suffix].strip()
    return raw


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

    games = ["snake", "tetris", "pong"]
    results: list[dict] = []

    for game in games:
        prompt = build_prompt(game)
        print(f"{'=' * 60}")
        print(f"GAME: {game}")
        print(f"{'=' * 60}")

        t0 = time.time()
        output = llm(prompt, max_tokens=600, echo=False, temperature=0.1, stop=["<|im_end|>", "<|im_start|>"])
        elapsed = time.time() - t0
        raw = output["choices"][0]["text"]
        tokens = output["usage"]["completion_tokens"]
        tok_s = tokens / elapsed if elapsed > 0 else 0

        code = extract_code(raw)
        parse_ok, parse_err, classes = check_syntax(code)

        print(f"  Tokens: {tokens} in {elapsed:.1f}s ({tok_s:.1f} tok/s)")
        print(f"  Raw (first 150): {repr(raw[:150])}")
        if not parse_ok:
            print(f"  Code (first 200): {repr(code[:200])}")
        print(f"  AST: {'OK' if parse_ok else 'FAIL'} {parse_err}")
        print(f"  Classes: {sorted(classes) if classes else '-'}")

        results.append(
            {
                "game": game,
                "tokens": tokens,
                "elapsed": elapsed,
                "tok_s": tok_s,
                "parse_ok": parse_ok,
                "parse_err": parse_err,
                "classes": classes,
            }
        )
        print()

    print(f"{'=' * 60}")
    print("REPORT")
    print(f"{'=' * 60}")
    for r in results:
        status = "OK" if r["parse_ok"] else "FAIL"
        print(
            f"  {r['game']:8s} AST:{status:5s}  {r['tokens']:4d}tok  {r['tok_s']:6.1f} tok/s  classes:{sorted(r['classes']) if r['classes'] else '-'}"
        )

    all_ok = all(r["parse_ok"] for r in results)
    print(f"\n{'ALL PARSE OK' if all_ok else 'SYNTAX ERRORS — model fails game gen'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
