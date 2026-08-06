#!/usr/bin/env python3
"""Benchmark code generation quality: DeepSeek-Coder-1.3B vs Qwen2.5-1.5B.

Prompts: fibonacci, sorting (quicksort), snake game (class skeleton).
Measures: tokens/sec, TTFT, and syntactic quality (exec/parse pass rate).
"""

from __future__ import annotations

import ast
import glob
import os
import sys
import time

DEEPSEEK_DIR = "/tmp/gludd-deepseek-1.3b-model"
QWEN_DIR = "/tmp/gludd-qwen-1.5b-model"
MAX_TOKENS = 256

PROMPTS = {
    "fibonacci": 'def fibonacci(n):\n    """Return the nth Fibonacci number."""\n',
    "quicksort": 'def quicksort(arr):\n    """Sort array in-place using quicksort."""\n',
    "snake_game": 'import pygame\n\nclass SnakeGame:\n    """A simple snake game."""\n    def __init__(self, width=640, height=480):\n',
}

ParseResult = tuple[str, str, float, float, int, bool, str]  # model, prompt, ttft_ms, tok_s, tokens, parse_ok, err


def load_model(model_dir: str):
    import llama_cpp  # type: ignore[import-untyped]

    ggufs = glob.glob(os.path.join(model_dir, "*.gguf"))
    if not ggufs:
        raise FileNotFoundError(f"No GGUF in {model_dir}")
    path = ggufs[0]
    name = os.path.basename(path)
    size_mb = os.path.getsize(path) / 1e6
    t0 = time.time()
    llm = llama_cpp.Llama(model_path=path, n_ctx=1024, verbose=False)
    load_s = time.time() - t0
    return llm, name, size_mb, load_s


def generate(llm, prompt: str) -> tuple[float, float, int, str]:
    t0 = time.time()
    first_token = True
    first_ts = t0
    token_count = 0
    text_parts: list[str] = []

    stream = llm(prompt, max_tokens=MAX_TOKENS, echo=False, stream=True)
    for chunk in stream:
        choices = chunk.get("choices", [])
        if first_token and choices:
            first_ts = time.time()
            first_token = False
        if choices:
            t = choices[0].get("text", "")
            if t:
                text_parts.append(t)
                token_count += 1

    elapsed = time.time() - t0
    ttft = (first_ts - t0) * 1000
    tok_s = token_count / elapsed if elapsed > 0 else 0
    return ttft, tok_s, token_count, "".join(text_parts)


def check_parse(code: str, prompt_name: str) -> tuple[bool, str]:
    full = f"def {prompt_name}():\n    pass\n\n" + code
    try:
        ast.parse(full)
        return True, ""
    except SyntaxError as e:
        return False, str(e)


def bench_model(model_dir: str, label: str) -> list[ParseResult]:
    llm, name, size_mb, load_s = load_model(model_dir)
    print(f"\n{'=' * 70}")
    print(f"MODEL: {label} ({name}, {size_mb:.0f} MB) — loaded in {load_s:.1f}s")
    print(f"{'=' * 70}")

    results: list[ParseResult] = []
    for prompt_name, prompt_text in PROMPTS.items():
        ttft, tok_s, tokens, text = generate(llm, prompt_text)
        parse_ok, parse_err = check_parse(text, prompt_name)
        results.append((label, prompt_name, ttft, tok_s, tokens, parse_ok, parse_err))

        ok_str = "OK" if parse_ok else f"FAIL: {parse_err[:60]}"
        print(f"  [{prompt_name:12s}] TTFT: {ttft:6.0f}ms  Tok/s: {tok_s:5.1f}  Tokens: {tokens:3d}  Parse: {ok_str}")
        if text:
            lines = text.strip().split("\n")[:3]
            preview = " | ".join(l[:60] for l in lines)
            print(f"    {preview}")

    return results


def main() -> int:
    all_results: list[ParseResult] = []

    for model_dir, label in [(DEEPSEEK_DIR, "DeepSeek-Coder-1.3B"), (QWEN_DIR, "Qwen2.5-1.5B")]:
        if not glob.glob(os.path.join(model_dir, "*.gguf")):
            print(f"SKIP: No GGUF in {model_dir} — run download first", file=sys.stderr)
            continue
        all_results.extend(bench_model(model_dir, label))

    if len({r[0] for r in all_results}) < 2:
        print("\nNeed both models downloaded to compare.", file=sys.stderr)
        return 1

    by_model: dict[str, list[ParseResult]] = {}
    for r in all_results:
        by_model.setdefault(r[0], []).append(r)

    print(f"\n{'=' * 70}")
    print("COMPARISON SUMMARY")
    print(f"{'=' * 70}")
    print(
        f"{'Prompt':12s} {'DeepSeek TTFT':>14s} {'Qwen TTFT':>14s} {'DS Tok/s':>9s} {'Qwen Tok/s':>9s} {'DS Parse':>9s} {'Qwen Parse':>9s}"
    )

    for prompt_name in PROMPTS:
        ds = [r for r in all_results if r[0].startswith("DeepSeek") and r[1] == prompt_name]
        qw = [r for r in all_results if r[0].startswith("Qwen") and r[1] == prompt_name]
        ds_row = ds[0] if ds else None
        qw_row = qw[0] if qw else None
        ds_ttft = f"{ds_row[2]:6.0f}ms" if ds_row else "N/A"
        qw_ttft = f"{qw_row[2]:6.0f}ms" if qw_row else "N/A"
        ds_tok = f"{ds_row[3]:.1f}" if ds_row else "N/A"
        qw_tok = f"{qw_row[3]:.1f}" if qw_row else "N/A"
        ds_ok = "OK" if (ds_row and ds_row[5]) else ("FAIL" if ds_row else "N/A")
        qw_ok = "OK" if (qw_row and qw_row[5]) else ("FAIL" if qw_row else "N/A")
        print(f"{prompt_name:12s} {ds_ttft:>14s} {qw_ttft:>14s} {ds_tok:>9s} {qw_tok:>9s} {ds_ok:>9s} {qw_ok:>9s}")

    ds_all = by_model.get("DeepSeek-Coder-1.3B", [])
    qw_all = by_model.get("Qwen2.5-1.5B", [])
    ds_avg_tok = sum(r[3] for r in ds_all) / len(ds_all) if ds_all else 0
    qw_avg_tok = sum(r[3] for r in qw_all) / len(qw_all) if qw_all else 0
    ds_parse = sum(1 for r in ds_all if r[5])
    qw_parse = sum(1 for r in qw_all if r[5])
    ds_avg_ttft = sum(r[2] for r in ds_all) / len(ds_all) if ds_all else 0
    qw_avg_ttft = sum(r[2] for r in qw_all) / len(qw_all) if qw_all else 0

    print(f"\n{'OVERALL':12s} {'':>14s} {'':>14s} {'':>9s} {'':>9s} {'':>9s} {'':>9s}")
    print(f"{'Avg Tok/s':12s} {'':>14s} {'':>14s} {ds_avg_tok:>9.1f} {qw_avg_tok:>9.1f}")
    print(f"{'Avg TTFT':12s} {'':>14s} {'':>14s} {'':>9s} {'':>9s} {'':>9s} {'':>9s}")
    if ds_all:
        print(f"  DeepSeek: {ds_avg_ttft:.0f}ms")
    if qw_all:
        print(f"  Qwen:     {qw_avg_ttft:.0f}ms")
    print(f"{'Parse OK':12s} {'':>14s} {'':>14s} {'':>9s} {'':>9s} {ds_parse}/3 {'':>4s} {qw_parse}/3")

    return 0


if __name__ == "__main__":
    sys.exit(main())
