#!/usr/bin/env python3
"""Download and quantize a small GGUF model for E2E testing."""

import os
import sys

from general_ludd.small_models.download import ModelDownloader
from general_ludd.quantization.quantize import ModelQuantizer, QuantMethod

CACHE_DIR = "/tmp/gludd-qwen-e2e-model"
MODEL_ID = "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
MODEL_FILENAME = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
QUANT_NAME = "qwen2.5-0.5b-q4_0.gguf"


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    d = ModelDownloader(cache_dir=CACHE_DIR)
    result = d.download_gguf(model_id=MODEL_ID, filename=MODEL_FILENAME)
    print(f"Downloaded: {result.local_path} ({result.size_bytes / 1e6:.1f} MB)")

    qpath = os.path.join(CACHE_DIR, QUANT_NAME)
    quantizer = ModelQuantizer()
    ok = quantizer.quantize(
        input_gguf=result.local_path,
        output_gguf=qpath,
        method=QuantMethod.Q4_0,
    )
    if ok:
        sz = os.path.getsize(qpath)
        print(f"Quantized: {qpath} ({sz / 1e6:.1f} MB)")
    else:
        print("Quantization FAILED")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
