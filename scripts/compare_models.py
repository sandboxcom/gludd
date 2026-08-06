#!/usr/bin/env python3
"""Download + benchmark multiple GGUF models. Score: syntax validity, code quality, speed."""

from __future__ import annotations

import ast
import os
import sys
import time
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from general_ludd.small_models.download import ModelDownloader

CACHE_DIR = os.environ.get("GLUDD_MODEL_COMPARE_DIR", "/tmp/gludd-model-compare")
os.makedirs(CACHE_DIR, exist_ok=True)

STANDARD_PROMPT = """Write a Python function that validates a password.
Requirements:
- at least 8 characters
- at least one uppercase letter
- at least one digit
- return True if valid, False otherwise
- include docstring and type hints

def validate_password(password: str) -> bool:
"""

MODELS: list[dict[str, str]] = [
    {
        "name": "Qwen2.5-0.5B",
        "repo": "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        "filename": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        "size": "0.5B",
    },
    {
        "name": "Qwen2.5-1.5B",
        "repo": "bartowski/Qwen2.5-1.5B-Instruct-GGUF",
        "filename": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "size": "1.5B",
    },
    {
        "name": "DeepSeek-Coder-1.3B",
        "repo": "bartowski/DeepSeek-Coder-1.3B-Base-GGUF",
        "filename": "DeepSeek-Coder-1.3B-Base-Q4_K_M.gguf",
        "size": "1.3B",
    },
    {
        "name": "Llama-3.2-1B",
        "repo": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size": "1.0B",
    },
    {
        "name": "Phi-3-mini",
        "repo": "bartowski/Phi-3-mini-4k-instruct-GGUF",
        "filename": "Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        "size": "3.8B",
    },
]

MAX_TOKENS = 128
N_RUNS = 3
TEMPERATURE = 0.2


@dataclass
class ModelResult:
    name: str
    size: str
    load_time_s: float = 0.0
    avg_ttft_ms: float = 0.0
    avg_tok_s: float = 0.0
    total_time_s: float = 0.0
    syntax_valid: bool = False
    syntax_errors: list[str] = field(default_factory=list)
    code_quality: int = 0
    quality_notes: list[str] = field(default_factory=list)
    output_text: str = ""
    download_size_mb: float = 0.0
    error: str = ""


def check_syntax(code: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError: {e}")
        return False, errors
    return True, errors


def score_code_quality(code: str) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []

    if "def validate_password" not in code:
        notes.append("Missing function name")
        return 0, notes

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0, notes

    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    if not funcs:
        notes.append("No function found")
        return 0, notes

    func = funcs[0]

    # 1. Has docstring
    if (
        func.body
        and isinstance(func.body[0], ast.Expr)
        and isinstance(func.body[0].value, ast.Constant)
        and isinstance(func.body[0].value.value, str)
    ):
        score += 1
    else:
        notes.append("Missing docstring")

    # 2. Has type annotations for params + return
    if func.returns:
        score += 1
    else:
        notes.append("Missing return annotation")
    if func.args.args and func.args.args[0].annotation:
        score += 1
    else:
        notes.append("Missing param annotation")

    # 3. Checks length >= 8
    code_lower = code.lower()
    if "len(password)" in code or "len(" in code:
        score += 1
    else:
        notes.append("Missing length check")

    # 4. Checks uppercase
    if "upper" in code_lower or "isupper" in code_lower or "upper" in code:
        score += 1
    else:
        notes.append("Missing uppercase check")

    # 5. Checks digit
    if ".digit" in code_lower or "isdigit" in code_lower:
        score += 1
    else:
        notes.append("Missing digit check")

    # 6. Uses any() or loops for checks
    if "any(" in code or " for " in code:
        score += 1
    else:
        notes.append("No iteration/any()")

    # 7. Returns bool
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    return_ok = any(isinstance(r.value, ast.Constant) and isinstance(r.value.value, bool) for r in returns if r.value)
    if return_ok:
        score += 1
    else:
        notes.append("Return bool unclear")

    # 8. Reasonable length (not too short)
    if len(code) > 150:
        score += 1
    else:
        notes.append("Too short")

    return min(score, 8), notes


def run_model(llm: Any, model_info: dict[str, str]) -> ModelResult:
    result = ModelResult(
        name=model_info["name"],
        size=model_info["size"],
    )

    try:
        t0 = time.time()
        last_text_parts: list[str] = []
        for i in range(N_RUNS):
            batch_t0 = time.time()
            first_token = True
            first_ts = 0.0
            token_count = 0
            run_text_parts: list[str] = []

            stream = llm(STANDARD_PROMPT, max_tokens=MAX_TOKENS, echo=False, stream=True, temperature=TEMPERATURE)
            for chunk in stream:
                choices = chunk.get("choices", [])
                if first_token and choices:
                    first_ts = time.time()
                    first_token = False
                if choices and "text" in choices[0]:
                    token_count += 1
                    run_text_parts.append(choices[0]["text"])

            last_text_parts = run_text_parts
            batch_s = time.time() - batch_t0
            ttf = first_ts - batch_t0 if first_ts else batch_s
            tok_s = token_count / batch_s if batch_s > 0 else 0.0

            if i == 0:
                result.avg_ttft_ms = ttf * 1000
                result.avg_tok_s = tok_s
            else:
                result.avg_ttft_ms += ttf * 1000
                result.avg_tok_s += tok_s

        result.total_time_s = time.time() - t0
        result.avg_ttft_ms /= N_RUNS
        result.avg_tok_s /= N_RUNS

        result.output_text = "".join(last_text_parts)

        valid, errors = check_syntax(result.output_text)
        result.syntax_valid = valid
        result.syntax_errors = errors

        score, notes = score_code_quality(result.output_text)
        result.code_quality = score
        result.quality_notes = notes

    except Exception as e:
        result.error = str(e)

    return result


def format_table(results: list[ModelResult]) -> str:
    lines: list[str] = []
    sep = "-" * 88
    lines.append(sep)
    lines.append(f"{'Model':<24} {'Size':>5} {'TTFT':>7} {'tok/s':>8} {'Syn':>4} {'Qual':>5} {'Load':>6} {'Note'}")
    lines.append(sep)

    for r in results:
        if r.error:
            lines.append(f"{r.name:<24} {'—':>5} {'—':>7} {'—':>8} {'ERR':>4} {'0/8':>5} {'—':>6} {r.error[:30]}")
            continue

        syn = "PASS" if r.syntax_valid else "FAIL"
        qual = f"{r.code_quality}/8"
        note = r.quality_notes[0][:40] if r.quality_notes else ("OK" if r.syntax_valid else "syntax err")
        lines.append(
            f"{r.name:<24} {r.size:>5} {r.avg_ttft_ms:>6.0f}ms {r.avg_tok_s:>7.1f} {syn:>4} {qual:>5} {r.load_time_s:>5.1f}s {note}"
        )

    lines.append(sep)
    lines.append(
        "TTFT=Time To First Token (lower=better), tok/s=higher=better, Syn=syntax validity, Qual=code quality (0-8)"
    )
    lines.append("Prompt: 'Write a Python function that validates a password.'")
    return "\n".join(lines)


def main() -> int:
    import llama_cpp

    downloader = ModelDownloader(cache_dir=CACHE_DIR)
    results: list[ModelResult] = []

    for model_info in MODELS:
        name = model_info["name"]
        repo = model_info["repo"]
        filename = model_info["filename"]

        print(f"\n{'=' * 60}")
        print(f"  Downloading {name} ({model_info['size']})")
        print(f"{'=' * 60}")

        try:
            d = downloader.download_gguf(model_id=repo, filename=filename)
            size_mb = d.size_bytes / 1e6
            print(f"  Downloaded: {d.local_path} ({size_mb:.0f} MB)")

            print(f"  Loading {name}...")
            t_load = time.time()
            llm = llama_cpp.Llama(model_path=d.local_path, n_ctx=1024, verbose=False)
            load_time = time.time() - t_load
            print(f"  Loaded in {load_time:.1f}s")

            result = run_model(llm, model_info)
            result.load_time_s = load_time
            result.download_size_mb = size_mb
            results.append(result)

            print(
                f"  TTFT: {result.avg_ttft_ms:.0f}ms  tok/s: {result.avg_tok_s:.1f}  "
                f"Syn: {'PASS' if result.syntax_valid else 'FAIL'}  Qual: {result.code_quality}/8"
            )

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(
                ModelResult(
                    name=name,
                    size=model_info["size"],
                    error=str(e),
                )
            )

    print(f"\n{format_table(results)}")
    return 0 if all(not r.error for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
