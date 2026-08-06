#!/usr/bin/env python3
"""Download DeepSeek-Coder-1.3B-Instruct GGUF (~0.8 GB, Q4_K_M quant)."""

import os
import shutil
import sys
import time

from general_ludd.small_models.download import ModelDownloader

CACHE_DIR = "/tmp/gludd-deepseek-1.3b-model"
MODEL_ID = "TheBloke/deepseek-coder-1.3b-instruct-GGUF"
MODEL_FILENAME = "deepseek-coder-1.3b-instruct.Q4_K_M.gguf"


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    d = ModelDownloader(cache_dir=CACHE_DIR)
    t0 = time.time()
    result = d.download_gguf(model_id=MODEL_ID, filename=MODEL_FILENAME)
    elapsed = time.time() - t0

    dest = os.path.join(CACHE_DIR, MODEL_FILENAME)
    if result.local_path != dest:
        shutil.copy2(result.local_path, dest)

    size_mb = result.size_bytes / 1e6
    print(f"\nDownloaded: {result.local_path}")
    print(f"Copied to: {dest}")
    print(f"Size: {size_mb:.1f} MB")
    print(f"Time: {elapsed:.0f}s ({size_mb / elapsed:.1f} MB/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
