"""Behavioral contract for the commit-time disk headroom guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _ROOT / "Makefile"
_KIB_PER_GIB = 1024 * 1024


def _guard_block() -> str:
    content = _MAKEFILE.read_text(encoding="utf-8")
    return content.split("_disk-usage-guard:", 1)[1].split(
        "check-worktree-staleness:", 1
    )[0]


def _run_guard_with_available_kib(
    tmp_path: Path, available_kib: int
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_df = fake_bin / "df"
    fake_df.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        "'Filesystem 1024-blocks Used Available Capacity Mounted on' "
        f"'fakefs 4194304 1048576 {available_kib} 25% /'\n",
        encoding="utf-8",
    )
    fake_df.chmod(0o755)

    return subprocess.run(
        ["make", "_disk-usage-guard", "DISK_MIN_FREE_GIB=1"],
        cwd=_ROOT,
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


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
    assert "available_bytes=" in result.stdout
    assert "required_bytes=" in result.stdout


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


@pytest.mark.parametrize(
    ("available_kib", "should_block"),
    [
        pytest.param(_KIB_PER_GIB - 1, True, id="below"),
        pytest.param(_KIB_PER_GIB, False, id="equal"),
        pytest.param(_KIB_PER_GIB + 1, False, id="above"),
    ],
)
def test_disk_guard_enforces_exact_headroom_boundary(
    tmp_path: Path, available_kib: int, should_block: bool
) -> None:
    result = _run_guard_with_available_kib(tmp_path, available_kib)

    assert (result.returncode != 0) is should_block, result.stdout + result.stderr
    if should_block:
        assert "BLOCKED" in result.stdout
    else:
        assert "_disk-usage-guard: PASS" in result.stdout
