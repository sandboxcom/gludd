#!/usr/bin/env python3
"""Benchmark local GGUF model: load, N inferences, tokens/sec, time-to-first-token."""

from __future__ import annotations

import glob
import os
import sys
import time

MODEL_DIR = os.environ.get("GLUDD_BENCH_MODEL_DIR", "/tmp/gludd-qwen-e2e-model")
N = int(os.environ.get("GLUDD_BENCH_N", "10"))
MAX_TOKENS = int(os.environ.get("GLUDD_BENCH_MAX_TOKENS", "32"))
PROMPT = os.environ.get("GLUDD_BENCH_PROMPT", "def fibonacci(n):")


def main() -> int:
    import llama_cpp

    ggufs = glob.glob(os.path.join(MODEL_DIR, "*.gguf"))
    if not ggufs:
        print(f"No GGUF found in {MODEL_DIR}", file=sys.stderr)
        return 1
    model_path = ggufs[0]

    print(f"Model: {os.path.basename(model_path)} ({os.path.getsize(model_path) / 1e6:.0f} MB)")
    print(f"Inferences: {N}, max_tokens: {MAX_TOKENS}, prompt: {PROMPT!r}")
    print()

    t0 = time.time()
    llm = llama_cpp.Llama(model_path=model_path, n_ctx=512, verbose=False)
    load_s = time.time() - t0
    print(f"Loaded in {load_s:.1f}s\n")

    ttf_times: list[float] = []
    tok_s_rates: list[float] = []
    total_tokens: list[int] = []

    for i in range(N):
        batch_t0 = time.time()
        first_token = True
        first_ts = 0.0
        token_count = 0

        stream = llm(PROMPT, max_tokens=MAX_TOKENS, echo=False, stream=True)
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
        total_tokens.append(token_count)

        label = f"  [{i + 1:2d}/{N}]"
        print(f"{label} TTFT: {ttf * 1000:6.0f}ms  Tok/s: {tok_s:7.1f}  Tokens: {token_count}  Total: {batch_s:5.1f}s")

    avg_ttft = sum(ttf_times) / len(ttf_times)
    avg_tok_s = sum(tok_s_rates) / len(tok_s_rates)
    total_tok = sum(total_tokens)

    print(f"\n{'=' * 60}")
    print(f"SUMMARY  (N={N}, max_tokens={MAX_TOKENS})")
    print(f"{'=' * 60}")
    print(f"  Model load time:       {load_s:.1f}s")
    print(f"  Avg TTFT:              {avg_ttft * 1000:.0f}ms")
    print(f"  Avg tokens/sec:        {avg_tok_s:.1f}")
    print(f"  Total tokens streamed: {total_tok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
