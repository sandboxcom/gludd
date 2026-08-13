"""Behavioral contract for the commit-time disk headroom guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _ROOT / "Makefile"


def _guard_block() -> str:
    content = _MAKEFILE.read_text(encoding="utf-8")
    return content.split("_disk-usage-guard:", 1)[1].split(
        "check-worktree-staleness:", 1
    )[0]


def test_disk_guard_uses_absolute_headroom_without_bypass() -> None:
    content = _MAKEFILE.read_text(encoding="utf-8")
    block = _guard_block()

    assert "DISK_MIN_FREE_GIB ?= 8" in content
    assert 'df -Pk "$(CURDIR)"' in block
    assert "AVAILABLE_KIB" in block
    assert "MIN_FREE_KIB" in block
    assert "FORCE" not in block
    assert "exit 1" in block


def test_disk_guard_accepts_sufficient_headroom() -> None:
    result = subprocess.run(
        ["make", "_disk-usage-guard", "DISK_MIN_FREE_GIB=1"],
        cwd=_ROOT,
        env={**os.environ, "GLUDD_DISK_THRESHOLD": "95"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "available_gib=" in result.stdout


def test_disk_guard_fails_closed_below_required_headroom() -> None:
    result = subprocess.run(
        ["make", "_disk-usage-guard", "DISK_MIN_FREE_GIB=999999"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert "BLOCKED" in result.stdout
