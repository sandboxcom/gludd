"""Make must never force an approval prompt through the user's uv cache."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAFE_CACHE = "/tmp/gludd-uv-cache-public-v2"


def test_make_overrides_ambient_uv_cache_with_writable_shared_cache() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert f"GLUDD_UV_CACHE_DIR ?= {SAFE_CACHE}" in makefile
    assert "override UV_CACHE_DIR := $(GLUDD_UV_CACHE_DIR)" in makefile
    assert "export UV_CACHE_DIR" in makefile
    assert "@printf '%s\\n' \"$$UV_CACHE_DIR\"" in makefile
    assert (
        'VERSION = $(shell UV_CACHE_DIR="$(GLUDD_UV_CACHE_DIR)" $(UV) run python'
        in makefile
    )


def test_uv_cache_path_ignores_prompt_prone_ambient_value() -> None:
    environment = {**os.environ, "UV_CACHE_DIR": "/Users/example/.cache/uv"}
    result = subprocess.run(
        ["make", "--no-print-directory", "uv-cache-path"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == SAFE_CACHE
    assert result.stderr == ""
