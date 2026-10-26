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
GIT_SHOW_PASSED_GUIDANCE = (
    "AGENT_NEXT target=git-show-commit outcome=passed native_status=0 "
    "gate_evidence=false release_ready=false next_owner=worktree-state; "
    "requested revision metadata and every display child finished; "
    "verify branch/clean ownership and required native acceptance before "
    "integration or release\n"
)


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


def _search_fixture(tmp_path: Path) -> None:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copytree(ROOT / "make", tmp_path / "make")


@pytest.mark.parametrize(
    ("pattern", "line"),
    [
        ('"state": "failure"', '"state": "failure"'),
        (r'^\$\(printf harmless\)$', '$(printf harmless)'),
        (r'^\$GLUDD_LITERAL$', '$GLUDD_LITERAL'),
        (r'^`printf harmless`$', '`printf harmless`'),
        ('--literal', '--literal'),
    ],
)
def test_actual_search_treats_quoted_patterns_as_data(
    tmp_path: Path, pattern: str, line: str,
) -> None:
    _search_fixture(tmp_path)
    (tmp_path / "input.txt").write_text(line + "\n", encoding="utf-8")
    result = _make(tmp_path, "search", f"PATTERN={pattern}", "SEARCH_PATH=input.txt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.strip() == "1:" + line
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "uv.lock").exists()


def test_actual_search_preserves_quoted_path_and_protected_scope(tmp_path: Path) -> None:
    _search_fixture(tmp_path)
    name = 'data" file.txt'
    (tmp_path / name).write_text("owned\n", encoding="utf-8")
    result = _make(tmp_path, "search", "PATTERN=owned", f"SEARCH_PATH={name}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "1:owned"
    rejected = _make(tmp_path, "search", "PATTERN=owned", "SEARCH_PATH=../outside")
    assert rejected.returncode != 0
    assert "Refusing path outside workspace" in rejected.stdout


@pytest.mark.parametrize("variable", ["PATTERN", "SEARCH_PATH"])
def test_actual_search_never_evaluates_exported_make_function_data(
    tmp_path: Path, variable: str,
) -> None:
    _search_fixture(tmp_path)
    (tmp_path / "input.txt").write_text("owned\n", encoding="utf-8")
    assignments = {"PATTERN": "owned", "SEARCH_PATH": "input.txt"}
    assignments[variable] = "$(shell printf harmless > make-expansion.txt)"
    result = _make(tmp_path, "search", *(f"{key}={value}" for key, value in assignments.items()))
    assert not (tmp_path / "make-expansion.txt").exists()
    assert result.returncode != 0


def test_actual_search_keeps_tool_errors_distinct_from_no_matches(tmp_path: Path) -> None:
    _search_fixture(tmp_path)
    (tmp_path / "input.txt").write_text("owned\n", encoding="utf-8")
    result = _make(tmp_path, "search", "PATTERN=[", "SEARCH_PATH=input.txt")
    assert result.returncode != 0
    assert "No matches" not in result.stdout
    assert "error" in result.stderr.lower()
    absent = _make(tmp_path, "search", "PATTERN=absent", "SEARCH_PATH=input.txt")
    assert absent.returncode != 0
    assert "No matches" in absent.stdout


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
    assert diagnostic.stderr == GIT_SHOW_PASSED_GUIDANCE
    assert "--- HEAD summary ---" in diagnostic.stdout
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "uv.lock").exists()
    assert (tmp_path / "pyproject.toml").read_bytes() == metadata_before

    # Explicit runtime introspection still invokes the application and exposes its error.
    runtime_check = _make(tmp_path, "version", runtime_path=runtime.parent)
    assert runtime_check.returncode != 0
    assert "fixture runtime import forbidden" in runtime_check.stderr


def test_actual_git_helper_is_silent_without_source_metadata_or_environment(
    tmp_path: Path,
) -> None:
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    shutil.copytree(ROOT / "make", tmp_path / "make")
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.name", "Make fixture")
    _git(tmp_path, "config", "user.email", "make-fixture@example.invalid")
    _git(tmp_path, "add", "Makefile")
    _git(tmp_path, "commit", "--quiet", "-m", "metadata-free fixture")

    diagnostic = _make(tmp_path, "git-show-commit", "C=HEAD")
    assert diagnostic.returncode == 0, diagnostic.stdout + diagnostic.stderr
    assert diagnostic.stderr == GIT_SHOW_PASSED_GUIDANCE
    assert "--- HEAD summary ---" in diagnostic.stdout
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "uv.lock").exists()
    assert not (tmp_path / "src").exists()


def test_unrelated_external_make_dry_run_needs_no_metadata_and_preserves_environment(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / ".venv/required-package"
    sentinel.parent.mkdir()
    sentinel.write_text("leased", encoding="utf-8")
    contents_before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    diagnostic = _make(tmp_path, "-n", f"-I{ROOT}", "-f", str(ROOT / "Makefile"), "clean")
    assert diagnostic.returncode == 0, diagnostic.stdout + diagnostic.stderr
    assert diagnostic.stderr == ""
    assert sentinel.read_text(encoding="utf-8") == "leased"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == contents_before
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / "uv.lock").exists()


def _packaging_fixture(tmp_path: Path, project_text: str | None) -> None:
    foundation = (ROOT / "make/50-development-and-guardrails.mk").read_text().splitlines()
    packaging = (ROOT / "make/60-quality-packaging-and-sandbox.mk").read_text().splitlines()
    definitions = [
        line for line in foundation if line.startswith(("VERSION =", "TARBALL_NAME ="))
    ] + [
        line for line in packaging if line.startswith(("DMG_NAME =", "WINDOWS_INSTALLER ="))
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


def test_packaging_name_command_line_overrides_remain_authoritative(tmp_path: Path) -> None:
    _packaging_fixture(tmp_path, None)
    result = _make(
        tmp_path, "-f", "names.mk", "names", "VERSION=7.8.9",
        "DMG_NAME=custom-installer.dmg", "WINDOWS_INSTALLER=custom-installer.exe",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "7.8.9", "general-ludd-agent-7.8.9-fixture-platform",
        "custom-installer.dmg", "custom-installer.exe",
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
