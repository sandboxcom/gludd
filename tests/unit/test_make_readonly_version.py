"""Source version metadata must not bootstrap application runtime for Git helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MAKE = shutil.which("make") or "make"


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "VIRTUAL_ENV", "PYTHONPATH"):
        environment.pop(variable, None)
    environment["UV_OFFLINE"] = "1"
    return environment


def _make(
    tmp_path: Path, *arguments: str, runtime_path: Path | None = None,
    ambient_project: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = _environment()
    if runtime_path is not None:
        environment["PYTHONPATH"] = str(runtime_path)
    if ambient_project is not None:
        environment["UV_PROJECT"] = str(ambient_project)
    return subprocess.run(
        [MAKE, "--no-print-directory", *arguments, f"PYTHON={sys.executable}"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False, timeout=30,
    )


def _git(tmp_path: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=tmp_path, env=_environment(),
        capture_output=True, text=True, check=True, timeout=30,
    )


def test_actual_git_helper_is_silent_without_environment_and_keeps_runtime_checks(
    tmp_path: Path,
) -> None:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copytree(ROOT / "make", tmp_path / "make")
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    runtime = tmp_path / "src/general_ludd"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("raise RuntimeError('fixture runtime import forbidden')\n")
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Make fixture")
    _git(tmp_path, "config", "user.email", "make-fixture@example.invalid")
    _git(tmp_path, "add", "Makefile", "pyproject.toml")
    _git(tmp_path, "commit", "--quiet", "-m", "fixture")

    metadata_before = (tmp_path / "pyproject.toml").read_bytes()
    diagnostic = _make(tmp_path, "git-show-commit", "C=HEAD", runtime_path=runtime.parent)
    assert diagnostic.returncode == 0, diagnostic.stdout + diagnostic.stderr
    assert diagnostic.stderr == ""
    assert "--- HEAD summary ---" in diagnostic.stdout
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "uv.lock").exists()
    assert (tmp_path / "pyproject.toml").read_bytes() == metadata_before

    # Explicit runtime introspection still invokes the application and exposes its error.
    runtime_check = _make(tmp_path, "version", runtime_path=runtime.parent)
    assert runtime_check.returncode != 0
    assert "fixture runtime import forbidden" in runtime_check.stderr


def _packaging_fixture(tmp_path: Path, project_text: str | None) -> None:
    foundation = (ROOT / "make/50-development-and-guardrails.mk").read_text().splitlines()
    packaging = (ROOT / "make/60-quality-packaging-and-sandbox.mk").read_text().splitlines()
    definitions = [
        line for line in foundation if line.startswith(("VERSION =", "TARBALL_NAME ="))
    ] + [
        line for line in packaging if line.startswith(("DMG_NAME :=", "WINDOWS_INSTALLER :="))
    ]
    (tmp_path / "names.mk").write_text(
        "UV := uv\nGLUDD_UV_CACHE_DIR := /tmp/gludd-uv-cache-public-v2\nPLATFORM := fixture-platform\n"
        + "\n".join(definitions)
        + '\nnames:\n\t@printf "%s\\n" "$(VERSION)" "$(TARBALL_NAME)" "$(DMG_NAME)" "$(WINDOWS_INSTALLER)"\n'
    )
    if project_text is not None:
        (tmp_path / "pyproject.toml").write_text(project_text)


def test_actual_packaging_defaults_use_static_project_version_not_other_tables(
    tmp_path: Path,
) -> None:
    _packaging_fixture(
        tmp_path,
        '[tool.unrelated]\nversion = "99.0"\n[project]\nname = "fixture"\nversion = "3.2.1"\n',
    )
    result = _make(tmp_path, "-f", "names.mk", "names")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "3.2.1", "general-ludd-agent-3.2.1-fixture-platform",
        "gludd-3.2.1-macos-arm64.dmg", "gludd-3.2.1-setup-x86_64.exe",
    ]
    assert not (tmp_path / ".venv").exists()


def test_source_version_is_bound_to_checkout_not_ambient_project(tmp_path: Path) -> None:
    _packaging_fixture(tmp_path, '[project]\nname = "fixture"\nversion = "3.2.1"\n')
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "99.0"\n')
    result = _make(tmp_path, "-f", "names.mk", "names", ambient_project=other)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines()[0] == "3.2.1"


def test_make_command_line_version_override_precedence_is_unchanged(tmp_path: Path) -> None:
    _packaging_fixture(tmp_path, None)
    result = _make(tmp_path, "-f", "names.mk", "names", "VERSION=7.8.9")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "7.8.9", "general-ludd-agent-7.8.9-fixture-platform",
        "gludd-7.8.9-macos-arm64.dmg", "gludd-7.8.9-setup-x86_64.exe",
    ]


@pytest.mark.parametrize("project_text", [
    None, '[project]\nname = "fixture"\n', '[project]\nversion = ""\n',
    '[project]\nversion = "3.2.1"\nversion = "3.2.2"\n',
])
def test_unreadable_or_empty_source_version_fails_without_runtime_bootstrap(
    tmp_path: Path, project_text: str | None,
) -> None:
    _packaging_fixture(tmp_path, project_text)
    result = _make(tmp_path, "-f", "names.mk", "names")
    assert result.returncode != 0
    assert "Cannot read [project].version from pyproject.toml" in result.stderr
    assert not (tmp_path / ".venv").exists()
