"""Preserve native directory-listing failures and the existing read-only scope."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, directory: str) -> subprocess.CompletedProcess[str]:
    """Execute the actual listing recipe in a confined fixture checkout."""
    content = compose_makefile(ROOT / "Makefile")
    recipe = content.split("\nlist-files:\n", 1)[1].split("\n\n", 1)[0]
    fixture = tmp_path / "fixture.mk"
    fixture.write_text(".PHONY: list-files\nlist-files:\n" + recipe + "\n", encoding="utf-8")
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "GNUMAKEFLAGS", "MAKELEVEL"):
        environment.pop(name, None)
    return subprocess.run(
        ["make", "--no-print-directory", "-f", str(fixture), "list-files", f"DIR={directory}"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=10,
    )


def test_listing_missing_input_preserves_native_failure(tmp_path: Path) -> None:
    """A sorter's success must not hide the native producer's missing-path error."""
    result = _run(tmp_path, "missing-directory")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "missing-directory" in result.stderr


def test_listing_keeps_sorted_paths_spaces_and_generated_directory_pruning(tmp_path: Path) -> None:
    """Retain successful output and every original generated-directory exclusion."""
    project = tmp_path / "project with space"
    project.mkdir()
    (project / "beta.py").write_text("", encoding="utf-8")
    (project / "alpha.py").write_text("", encoding="utf-8")
    (project / "nested").mkdir()
    (project / "nested/gamma.py").write_text("", encoding="utf-8")
    for name in (".git", ".venv", ".mypy_cache", ".pytest_cache", ".gate-logs"):
        (project / name).mkdir()
        (project / name / "retained").write_text("retained\n", encoding="utf-8")
    result = _run(tmp_path, project.name)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "project with space/alpha.py",
        "project with space/beta.py",
        "project with space/nested/gamma.py",
    ]
    assert result.stderr == ""
    assert all((project / name / "retained").read_text() == "retained\n" for name in (
        ".git", ".venv", ".mypy_cache", ".pytest_cache", ".gate-logs",
    ))


@pytest.mark.parametrize("directory", ["", "/tmp", "../outside"])
def test_listing_retains_existing_relative_path_boundary(tmp_path: Path, directory: str) -> None:
    """Keep absent, absolute and traversal inputs rejected before listing."""
    result = _run(tmp_path, directory)
    assert result.returncode != 0
    assert "Usage:" in result.stdout or "Refusing path outside workspace" in result.stdout


def test_listing_has_a_registered_readonly_behavior() -> None:
    """Expose one complete safe example to future models through the native catalog."""
    targets = json.loads((ROOT / "config/make_target_contract.json").read_text())["targets"]
    row = next(item for item in targets if item["name"] == "list-files")
    assert row["make_variables"] == ["DIR"]
    assert row["behavior"] == "make list-files DIR=.agents/policy"
