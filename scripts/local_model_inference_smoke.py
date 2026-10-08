#!/usr/bin/env python3
"""Run a bounded warning-free inference against one explicit GGUF artifact."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast


class _InferenceModel(Protocol):
    def __call__(
        self,
        prompt: str,
        *,
        max_tokens: int,
        echo: bool,
    ) -> object: ...


_ModelFactory = Callable[..., _InferenceModel]


def run_inference(model_path: Path, model_factory: _ModelFactory) -> str:
    """Load one readable artifact with native context and return generated text."""
    if not model_path.is_file() or not os.access(model_path, os.R_OK):
        raise FileNotFoundError(f"GGUF artifact is not readable: {model_path}")

    model = model_factory(
        model_path=str(model_path),
        n_ctx=0,
        verbose=False,
    )
    output = model("def hello(): return", max_tokens=32, echo=True)
    if not isinstance(output, Mapping):
        raise RuntimeError("local inference returned a non-object response")
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise RuntimeError("local inference response has no choices")
    text = choices[0].get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("local inference response has no generated text")
    return text


def main(argv: list[str] | None = None) -> int:
    """Load the locked llama.cpp runtime and execute the bounded smoke test."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    args = parser.parse_args(argv)

    import llama_cpp

    print(f"Model: {args.model_path}")
    print(f"llama_cpp version: {llama_cpp.__version__}")
    print("Loading model with native context...")
    text = run_inference(args.model_path, cast("_ModelFactory", llama_cpp.Llama))
    print("Running inference...")
    print(f"Output: {text!r}")
    print("SUCCESS: Local model inference works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
