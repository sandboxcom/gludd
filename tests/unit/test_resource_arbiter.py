"""Regression tests for project-scoped process/resource leases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.resource_arbiter import project_namespace, project_root, resource_path


def test_project_namespace_is_stable_and_path_safe(tmp_path: Path) -> None:
    first = project_namespace(tmp_path)
    second = project_namespace(tmp_path)

    assert first == second
    assert first
    assert all(char.isalnum() or char in "_.-" for char in first)


def test_different_project_roots_do_not_share_namespace(tmp_path: Path) -> None:
    left = project_namespace(tmp_path / "left")
    right = project_namespace(tmp_path / "right")

    assert left != right


def test_explicit_namespace_override_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLUDD_PROJECT_NAMESPACE", "customer-a")
    assert project_namespace(Path("/ignored")) == "customer-a"

    monkeypatch.setenv("GLUDD_PROJECT_NAMESPACE", "bad/name")
    with pytest.raises(ValueError, match="project namespace"):
        project_namespace(Path("/ignored"))


def test_resource_path_is_project_scoped(tmp_path: Path) -> None:
    path = resource_path("gate", tmp_path)

    assert path.parent.name == project_namespace(tmp_path)
    assert path.name == "gate.lock"
    assert "gludd-resources" in path.parts


def test_resource_name_cannot_escape_namespace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="resource name"):
        resource_path("../outside", tmp_path)


def test_project_root_prefers_explicit_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(tmp_path))
    assert project_root() == tmp_path.resolve()


def test_project_namespace_ignores_unrelated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    monkeypatch.delenv("GLUDD_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("TMPDIR", "/tmp/other-project")
    assert project_namespace(tmp_path) == project_namespace(tmp_path)
    assert os.environ["TMPDIR"] == "/tmp/other-project"
