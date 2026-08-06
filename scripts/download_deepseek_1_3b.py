#!/usr/bin/env python3
"""Download DeepSeek-Coder-1.3B-Instruct GGUF (~0.8 GB, Q4_K_M quant)."""

import os
import sys
import time

from general_ludd.small_models.download import ModelDownloader

CACHE_DIR = "/tmp/gludd-deepseek-1.3b-model"
MODEL_ID = "bartowski/DeepSeek-Coder-1.3B-Instruct-GGUF"
MODEL_FILENAME = "DeepSeek-Coder-1.3B-Instruct-Q4_K_M.gguf"


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    d = ModelDownloader(cache_dir=CACHE_DIR)
    t0 = time.time()
    result = d.download_gguf(model_id=MODEL_ID, filename=MODEL_FILENAME)
    elapsed = time.time() - t0

    size_mb = result.size_bytes / 1e6
    print(f"\nDownloaded: {result.local_path}")
    print(f"Size: {size_mb:.1f} MB")
    print(f"Time: {elapsed:.0f}s ({size_mb / elapsed:.1f} MB/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
