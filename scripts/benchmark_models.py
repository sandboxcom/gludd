#!/usr/bin/env python3
"""Multi-model comparison benchmark across all downloaded GGUF models.

Reports: model name, size, time-to-first-token, tokens/sec, output quality score.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

PROMPTS: list[dict[str, object]] = [
    {"name": "code-completion", "prompt": "def fibonacci(n):", "max_tokens": 64},
    {
        "name": "code-explanation",
        "prompt": "Explain what this does:\n\ndef grep(pattern, files):\n    return [f for f in files if pattern in open(f).read()]",
        "max_tokens": 48,
    },
    {"name": "json-output", "prompt": 'Return a JSON object with keys "name", "age", "city":\n', "max_tokens": 48},
    {"name": "simple-qa", "prompt": "What is 2+2? Answer in one word.", "max_tokens": 16},
]

MODEL_SEARCH_DIRS: list[str] = [
    "/tmp/gludd-qwen-e2e-model",
    "/tmp/gludd-qwen-1.5b-model",
    os.path.expanduser("~/.cache/huggingface/hub"),
]


@dataclass
class ModelResult:
    model_name: str
    model_path: str
    size_mb: float
    load_time_s: float
    ttft_ms: float
    tokens_per_sec: float
    total_tokens: int
    total_time_s: float
    quality_score: float
    per_prompt: list[dict[str, Any]] = field(default_factory=list)


def find_gguf_models() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for search_dir in MODEL_SEARCH_DIRS:
        if not os.path.isdir(search_dir):
            continue
        for gguf_path in glob.glob(os.path.join(search_dir, "**", "*.gguf"), recursive=True):
            size_mb = os.path.getsize(gguf_path) / 1e6
            if size_mb < 5:  # skip tiny vocab-only files
                continue
            name = os.path.basename(gguf_path)
            if name not in seen:
                seen.add(name)
                found.append((name, gguf_path))

    return found


def load_model(model_path: str, n_ctx: int = 512) -> tuple[Any, float]:
    """Return (Llama instance, load_time_s)."""
    import llama_cpp

    t0 = time.time()
    llm = llama_cpp.Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)
    load_s = time.time() - t0
    return llm, load_s


def run_prompts(llm: Any, prompts: list[dict[str, object]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for p in prompts:
        name = str(p["name"])
        prompt_text = str(p["prompt"])
        max_tokens = int(p["max_tokens"])  # type: ignore[arg-type]

        t0 = time.time()
        first_token = True
        first_ts = t0
        output_text = ""
        token_count = 0

        try:
            stream = llm(prompt_text, max_tokens=max_tokens, echo=False, stream=True)
            for chunk in stream:
                choices = chunk.get("choices", [])
                if first_token and choices:
                    first_ts = time.time()
                    first_token = False
                if choices and "text" in choices[0]:
                    t = choices[0]["text"]
                    output_text += t
                    token_count += 1
        except Exception as exc:
            results.append(
                {
                    "prompt_name": name,
                    "ttft_ms": 0.0,
                    "tokens_per_sec": 0.0,
                    "tokens": 0,
                    "total_time_s": time.time() - t0,
                    "output": "",
                    "quality_score": 0.0,
                    "error": str(exc),
                }
            )
            continue

        total_s = time.time() - t0
        ttft_ms = (first_ts - t0) * 1000 if first_ts > t0 else total_s * 1000
        tok_s = token_count / total_s if total_s > 0 else 0.0
        qs = _compute_quality(name, output_text, token_count)

        results.append(
            {
                "prompt_name": name,
                "ttft_ms": ttft_ms,
                "tokens_per_sec": tok_s,
                "tokens": token_count,
                "total_time_s": total_s,
                "output": output_text.strip(),
                "quality_score": qs,
                "error": None,
            }
        )

    return results


def _compute_quality(prompt_name: str, output: str, token_count: int) -> float:
    """Heuristic quality score 0.0–1.0 based on output characteristics."""
    score = 0.0

    if token_count == 0:
        return 0.0

    # Base: produced output at all
    score += 0.3

    if prompt_name == "code-completion":
        if output.strip().startswith(("def ", "    return", "    if", "    n")):
            score += 0.2
        if "return" in output or "yield" in output:
            score += 0.15
        if len(output) > 20:
            score += 0.1
        for kw in ("fibonacci", "sequence", "prev", "curr", "n-1"):
            if kw.lower() in output.lower():
                score += 0.05
                break
        score += min(token_count / 64.0, 1.0) * 0.2

    elif prompt_name == "code-explanation":
        output_lower = output.lower()
        if any(w in output_lower for w in ("function", "takes", "returns", "iterates", "searches", "pattern")):
            score += 0.3
        if "grep" in output_lower and "file" in output_lower:
            score += 0.15
        score += min(token_count / 48.0, 1.0) * 0.2

    elif prompt_name == "json-output":
        if "{" in output and "}" in output:
            score += 0.3
        if '"name"' in output.lower() or "name" in output:
            score += 0.15
        if ":" in output:
            score += 0.1
        score += min(token_count / 48.0, 1.0) * 0.1

    elif prompt_name == "simple-qa":
        if "4" in output or "four" in output.lower():
            score += 0.6
        if len(output) < 30:
            score += 0.1

    return min(score, 1.0)


def benchmark_model(model_path: str, model_name: str, size_mb: float) -> ModelResult:
    try:
        llm, load_s = load_model(model_path)
    except Exception as exc:
        return ModelResult(
            model_name=model_name,
            model_path=model_path,
            size_mb=size_mb,
            load_time_s=0,
            ttft_ms=0,
            tokens_per_sec=0,
            total_tokens=0,
            total_time_s=0,
            quality_score=0,
            per_prompt=[{"error": f"load_failed: {exc}"}],
        )

    per_prompt = run_prompts(llm, PROMPTS)

    successful = [r for r in per_prompt if r["error"] is None]
    if not successful:
        return ModelResult(
            model_name=model_name,
            model_path=model_path,
            size_mb=size_mb,
            load_time_s=load_s,
            ttft_ms=0,
            tokens_per_sec=0,
            total_tokens=0,
            total_time_s=0,
            quality_score=0,
            per_prompt=per_prompt,
        )

    avg_ttft = sum(r["ttft_ms"] for r in successful) / len(successful)
    avg_tok_s = sum(r["tokens_per_sec"] for r in successful) / len(successful)
    total_tokens = sum(r["tokens"] for r in successful)
    total_time = sum(r["total_time_s"] for r in successful)
    avg_quality = sum(r["quality_score"] for r in successful) / len(successful)

    return ModelResult(
        model_name=model_name,
        model_path=model_path,
        size_mb=size_mb,
        load_time_s=load_s,
        ttft_ms=avg_ttft,
        tokens_per_sec=avg_tok_s,
        total_tokens=total_tokens,
        total_time_s=total_time,
        quality_score=avg_quality,
        per_prompt=per_prompt,
    )


def render_table(results: list[ModelResult], as_json: bool = False) -> str:
    if as_json:
        out: list[dict[str, Any]] = []
        for r in results:
            out.append(
                {
                    "model": r.model_name,
                    "size_mb": round(r.size_mb, 1),
                    "load_time_s": round(r.load_time_s, 1),
                    "ttft_ms": round(r.ttft_ms, 0),
                    "tokens_per_sec": round(r.tokens_per_sec, 1),
                    "total_tokens": r.total_tokens,
                    "total_time_s": round(r.total_time_s, 1),
                    "quality_score": round(r.quality_score, 2),
                    "per_prompt": r.per_prompt,
                }
            )
        return json.dumps(out, indent=2)

    lines: list[str] = []
    lines.append(f"{'Model':<48s} {'Size':>7s} {'Load':>6s} {'TTFT':>7s} {'Tok/s':>7s} {'Tokens':>7s} {'Quality':>8s}")
    lines.append("-" * 100)

    for r in sorted(results, key=lambda x: x.tokens_per_sec, reverse=True):
        name = r.model_name[:46]
        size_s = f"{r.size_mb:.0f}MB" if r.size_mb < 1000 else f"{r.size_mb / 1000:.1f}GB"
        load_s = f"{r.load_time_s:.1f}s"
        ttft = f"{r.ttft_ms:.0f}ms"
        tok_s = f"{r.tokens_per_sec:.1f}"
        tokens = str(r.total_tokens)
        quality = f"{r.quality_score:.2f}"
        lines.append(f"  {name:<46s} {size_s:>6s} {load_s:>5s} {ttft:>6s} {tok_s:>6s} {tokens:>6s} {quality:>7s}")

    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Multi-model GGUF comparison benchmark")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--model-dir", action="append", dest="model_dirs", help="Additional model directory to scan (repeatable)"
    )
    args = parser.parse_args()

    if args.model_dirs:
        MODEL_SEARCH_DIRS.extend(args.model_dirs)

    models = find_gguf_models()

    if not models:
        print("No GGUF models found. Download one first with:", file=sys.stderr)
        print("  make e2e-download-small-model", file=sys.stderr)
        print("  make download-1.5b-model", file=sys.stderr)
        return 1

    print(f"Found {len(models)} model(s)\n")

    results: list[ModelResult] = []
    for i, (name, path) in enumerate(models, 1):
        size_mb = os.path.getsize(path) / 1e6
        print(f"[{i}/{len(models)}] {name} ({size_mb:.0f} MB) ... ", end="", flush=True)
        result = benchmark_model(path, name, size_mb)
        results.append(result)
        print(f"TTFT={result.ttft_ms:.0f}ms  Tok/s={result.tokens_per_sec:.1f}  Q={result.quality_score:.2f}")

    print()
    print(render_table(results, as_json=args.json))
    print()
    print(f"Benchmarked {len(results)} model(s) across {len(PROMPTS)} prompt(s).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
