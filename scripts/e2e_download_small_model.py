#!/usr/bin/env python3
"""Download a pinned, pre-quantized GGUF artifact for local-model E2E tests."""

import errno
import os
import shutil
import sys
import tempfile
from pathlib import Path

from general_ludd.small_models.download import ModelDownloader

CACHE_DIR = "/tmp/gludd-qwen-e2e-model"
MODEL_ID = "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
MODEL_FILENAME = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
MODEL_REVISION = "41ba88dbac95fed2528c92514c131d73eb5a174b"


def _materialize_artifact(source: Path, destination: Path) -> None:
    """Atomically hard-link a cached model, copying only across filesystems."""
    if destination.exists() and os.path.samefile(source, destination):
        return

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.unlink()
        try:
            os.link(source, temporary)
        except OSError as error:
            if error.errno not in {errno.EXDEV, errno.EPERM, errno.EACCES, errno.EMLINK}:
                raise
            shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    os.makedirs(CACHE_DIR, exist_ok=True)

    d = ModelDownloader(cache_dir=CACHE_DIR)
    result = d.download_gguf(
        model_id=MODEL_ID,
        filename=MODEL_FILENAME,
        revision=MODEL_REVISION,
    )
    print(f"Downloaded: {result.local_path} ({result.size_bytes / 1e6:.1f} MB)")

    artifact = Path(CACHE_DIR, MODEL_FILENAME)
    _materialize_artifact(Path(result.local_path), artifact)
    print(f"Ready: {artifact} ({artifact.stat().st_size / 1e6:.1f} MB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
