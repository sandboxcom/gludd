"""Verify production dependency declarations required by runtime code."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = ROOT / "pyproject.toml"


@pytest.fixture
def project_deps() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return list(data.get("project", {}).get("dependencies", []))


def test_greenlet_is_production_dependency(project_deps: list[str]) -> None:
    """SQLAlchemy asyncio is imported from production db code, so greenlet must be a main dependency."""
    names = {dep.split("[")[0].split(">=")[0].split("==")[0].split("<")[0].strip() for dep in project_deps}
    assert "greenlet" in names, "greenlet must be declared in [project] dependencies for SQLAlchemy asyncio"
