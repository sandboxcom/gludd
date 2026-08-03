"""Diagnose which tools are missing/working for E2E model pipeline tests."""

import os
import shutil
import subprocess
import sys


def main() -> int:
    bundled = os.path.abspath(os.path.join("external", "llamacpp", "build", "bin", "llama-quantize"))
    print(f"bundled path: {bundled}")
    print(f"  exists: {os.path.isfile(bundled)}")
    print(f"  executable: {os.access(bundled, os.X_OK)}")

    which = shutil.which("llama-quantize")
    print(f"PATH llama-quantize: {which}")

    try:
        r = subprocess.run([bundled, "--help"], capture_output=True, text=True, timeout=10)
        print(f"--help exit={r.returncode}")
        print(f"  stdout (first 300): {r.stdout[:300]}")
        print(f"  stderr (first 300): {r.stderr[:300]}")
    except Exception as e:
        print(f"--help ERROR: {e}")

    # Also check the quantizer's own path resolution
    from general_ludd.quantization.quantize import ModelQuantizer

    q = ModelQuantizer()
    print(f"\nModelQuantizer.llama_cpp_quantize_path: {q.llama_cpp_quantize_path}")
    print(f"ModelQuantizer._can_quantize_locally: {q._can_quantize_locally()}")

    try:
        import huggingface_hub  # noqa: F401

        print("huggingface_hub: OK")
    except ImportError:
        print("huggingface_hub: MISSING")

    try:
        import llama_cpp  # noqa: F401

        print("llama_cpp: OK")
    except ImportError:
        print("llama_cpp: MISSING")

    return 0


if __name__ == "__main__":
    sys.exit(main())
