"""Make must never force an approval prompt through the user's uv cache."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SAFE_CACHE = "/tmp/gludd-uv-cache-public-v2"


def _recipe(target: str) -> str:
    """Return the indented recipe body for a single Makefile target."""
    lines = compose_makefile(ROOT / "Makefile").splitlines()
    body: list[str] = []
    in_target = False
    for line in lines:
        if re.match(rf"^{re.escape(target)}:", line):
            in_target = True
            continue
        if in_target:
            if line and not line[0].isspace():
                break
            body.append(line)
    return "\n".join(body)


def test_make_overrides_ambient_uv_cache_with_writable_shared_cache() -> None:
    makefile = compose_makefile(ROOT / "Makefile")

    assert f"GLUDD_UV_CACHE_DIR ?= {SAFE_CACHE}" in makefile
    assert "override UV_CACHE_DIR := $(GLUDD_UV_CACHE_DIR)" in makefile
    assert "export UV_CACHE_DIR" in makefile
    assert "@printf '%s\\n' \"$$UV_CACHE_DIR\"" in makefile
    assert (
        'VERSION = $(shell UV_CACHE_DIR="$(GLUDD_UV_CACHE_DIR)" '
        "$(UV) run --no-sync python"
    ) in makefile


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


def test_uv_cache_prune_has_timeout_or_force_skip_guard() -> None:
    """uv-cache-prune must not invoke a bare $(UV) cache prune that can hang."""
    body = _recipe("uv-cache-prune")

    assert "UV_CACHE_PRUNE_TIMEOUT" in body, (
        "uv-cache-prune must declare a bounded timeout so the prune cannot block forever"
    )
    assert "UV_CACHE_PRUNE_FORCE" in body, "uv-cache-prune must support a UV_CACHE_PRUNE_FORCE=1 override"
    assert any(
        marker in body for marker in ("UV_CACHE_PRUNE_SKIP", "UV_CACHE_PRUNE_HEARTBEAT", "UV_CACHE_PRUNE_TIMEOUT pid")
    ), "uv-cache-prune must detect active uv processes or emit timeout/heartbeat guards"

    bare_prune_pattern = re.compile(r"^\s*@?\$\(UV\) cache prune\s*(--ci)?\s*$")
    for line in body.splitlines():
        assert not bare_prune_pattern.match(line), (
            f"uv-cache-prune must not invoke a bare $(UV) cache prune that can block forever: {line!r}"
        )
