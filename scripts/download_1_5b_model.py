#!/usr/bin/env python3
"""Download Qwen2.5-1.5B-Instruct-Q4_K_M.gguf (~1 GB, ~4GB RAM at 4-bit)."""

import os
import sys
import time

from general_ludd.small_models.download import ModelDownloader

CACHE_DIR = "/tmp/gludd-qwen-1.5b-model"
MODEL_ID = "bartowski/Qwen2.5-1.5B-Instruct-GGUF"
MODEL_FILENAME = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    d = ModelDownloader(cache_dir=CACHE_DIR)
    t0 = time.time()
    result = d.download_gguf(model_id=MODEL_ID, filename=MODEL_FILENAME)
    elapsed = time.time() - t0

    size_mb = result.size_bytes / 1e6
    size_gb = result.size_bytes / 1e9
    print(f"\nDownloaded: {result.local_path}")
    print(f"Size: {size_mb:.1f} MB ({size_gb:.2f} GB)")
    print(f"Time: {elapsed:.0f}s ({size_mb / elapsed:.1f} MB/s)")
    print(f"Feasible in 4GB RAM: YES (Q4_K_M ~4-6 bits/model, ~1.0GB VRAM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
