"""Regression tests for the release version bumper."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from scripts.bump_version import bump_versions


def _project(tmp_path: Path) -> tuple[Path, Path]:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\n'
        'name = "general-ludd-agent"\n'
        'version = "0.1.0-beta.2"\n'
        'dependencies = ["fastapi>=0.115.0", "jsonschema>=4.21"]\n'
        '\n'
        '[tool.example]\n'
        'schema-version = "2.7.1"\n',
    )
    init = tmp_path / "src" / "general_ludd" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text(
        'DEPENDENCY_FLOOR = "9.8.7"\n'
        '__version__ = "0.1.0-beta.2"\n',
    )
    return pyproject, init


def test_bump_changes_only_owned_version_fields(tmp_path: Path) -> None:
    pyproject, init = _project(tmp_path)

    bump_versions(tmp_path, "0.1.0-beta.3")

    assert 'version = "0.1.0-beta.3"' in pyproject.read_text()
    assert '"fastapi>=0.115.0"' in pyproject.read_text()
    assert '"jsonschema>=4.21"' in pyproject.read_text()
    assert 'schema-version = "2.7.1"' in pyproject.read_text()
    assert '__version__ = "0.1.0-beta.3"' in init.read_text()
    assert 'DEPENDENCY_FLOOR = "9.8.7"' in init.read_text()


def test_bump_rejects_invalid_pep440_version_without_writes(
    tmp_path: Path,
) -> None:
    pyproject, init = _project(tmp_path)
    before = (pyproject.read_text(), init.read_text())

    with pytest.raises(ValueError, match="invalid PEP 440"):
        bump_versions(tmp_path, "not a release/version")

    assert (pyproject.read_text(), init.read_text()) == before


def test_bump_fails_closed_when_owned_field_is_missing(tmp_path: Path) -> None:
    pyproject, init = _project(tmp_path)
    init.write_text('DEPENDENCY_FLOOR = "9.8.7"\n')
    before = (pyproject.read_text(), init.read_text())

    with pytest.raises(ValueError, match="__version__"):
        bump_versions(tmp_path, "0.1.0-beta.3")

    assert (pyproject.read_text(), init.read_text()) == before


def test_repository_dependencies_are_not_gludd_release_versions() -> None:
    """A release bump must never rewrite third-party dependency constraints."""
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    groups = {
        "dependencies": project["project"]["dependencies"],
        **project["project"]["optional-dependencies"],
        **{
            f"dependency-group:{name}": requirements
            for name, requirements in project["dependency-groups"].items()
        },
    }

    contaminated = [
        f"{group}: {requirement}"
        for group, requirements in groups.items()
        for requirement in requirements
        if ">=0.1.0-beta." in requirement
    ]

    assert not contaminated, (
        "third-party dependency floors were replaced by a Gludd release "
        f"version: {contaminated}"
    )


def test_repository_declares_starlette_testclient_backend() -> None:
    """Starlette 1.3 deprecates its legacy httpx TestClient backend."""
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    dev_requirements = {
        *project["project"]["optional-dependencies"]["dev"],
        *project["dependency-groups"]["dev"],
    }

    assert any(requirement.startswith("httpx2>=") for requirement in dev_requirements)
