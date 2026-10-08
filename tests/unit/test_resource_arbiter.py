"""Regression tests for project-scoped process/resource leases."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.resource_arbiter import (
    _main,
    project_namespace,
    project_root,
    resource_path,
    resource_root,
)


def test_project_namespace_is_stable_and_path_safe(tmp_path: Path) -> None:
    first = project_namespace(tmp_path)
    second = project_namespace(tmp_path)

    assert first == second
    assert first
    assert all(char.isalnum() or char in "_.-" for char in first)


def test_different_project_roots_do_not_share_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(tmp_path / "ambient-supervisor"))
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


def test_independent_projects_get_independent_gate_leases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(tmp_path / "ambient-supervisor"))
    first = resource_path("gate", tmp_path / "first")
    second = resource_path("gate", tmp_path / "second")

    assert first != second
    assert first.parent != second.parent


def test_project_root_prefers_explicit_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(tmp_path))
    assert project_root() == tmp_path.resolve()


def test_project_root_explicit_argument_precedes_ambient_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ambient_root = tmp_path / "ambient-supervisor"
    explicit_root = tmp_path / "explicit-project"
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(ambient_root))

    assert project_root(explicit_root) == explicit_root.resolve()


def test_project_namespace_ignores_unrelated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    monkeypatch.delenv("GLUDD_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("TMPDIR", "/tmp/other-project")
    assert project_namespace(tmp_path) == project_namespace(tmp_path)
    assert os.environ["TMPDIR"] == "/tmp/other-project"


def test_project_root_normalizes_file_and_discovers_project_marker(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    source_file = nested / "module.py"
    source_file.write_text("", encoding="utf-8")

    assert project_root(source_file) == project.resolve()


def test_configured_resource_root_keeps_project_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resources = tmp_path / "resources"
    project = tmp_path / "project"
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resources))

    assert resource_root(project) == resources.resolve() / project_namespace(project)


def test_cli_commands_preserve_explicit_and_fallback_roots(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit_root = tmp_path / "explicit"
    ambient_root = tmp_path / "ambient"
    resources = tmp_path / "resources"
    monkeypatch.delenv("GLUDD_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", str(ambient_root))
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resources))

    assert _main(["namespace", str(explicit_root)]) == 0
    assert capsys.readouterr().out.strip() == project_namespace(explicit_root)

    assert _main(["root"]) == 0
    assert Path(capsys.readouterr().out.strip()) == resource_root()

    assert _main(["path", "gate"]) == 0
    assert Path(capsys.readouterr().out.strip()) == resource_path("gate")


def test_cli_rejects_invalid_or_incomplete_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _main([]) == 2
    assert "usage:" in capsys.readouterr().err

    assert _main(["path"]) == 2
    assert "usage:" in capsys.readouterr().err
