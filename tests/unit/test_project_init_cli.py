"""Tests for ``gludd project init`` — scaffolds a project-local ansible
collection under ``<project_dir>/.gludd/collections/``.

Covers: galaxy.yml generation with namespace/name, directory layout (roles,
plugins/modules, plugins/module_utils, plugins/terraform), config.yml update
with the collection section, --force overwrite behavior, --namespace required,
default collection name, and the precedence summary printout.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

from general_ludd.cli import build_parser
from general_ludd.cli_project_init import _cmd_project_init


def _ns(**kwargs):
    return argparse.Namespace(**kwargs)


def _run_init(project_dir: Path, namespace: str, collection: str = "project",
              force: bool = False) -> str:
    args = _ns(
        project_dir=str(project_dir),
        namespace=namespace,
        collection=collection,
        force=force,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        _cmd_project_init(args)
    return buf.getvalue()


def test_init_creates_gludd_collections_dir(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme")
    expected_root = tmp_path / ".gludd" / "collections"
    assert expected_root.is_dir()


def test_init_creates_galaxy_yml_with_namespace_and_name(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme", collection="platform")
    galaxy_path = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "platform" / "galaxy.yml"
    )
    assert galaxy_path.is_file()
    data = yaml.safe_load(galaxy_path.read_text())
    assert data["namespace"] == "acme"
    assert data["name"] == "platform"
    assert data["version"] == "1.0.0"


def test_init_creates_roles_and_plugins_dirs(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme")
    base = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "project"
    )
    assert (base / "roles" / ".gitkeep").is_file()
    assert (base / "plugins" / "modules" / ".gitkeep").is_file()
    assert (base / "plugins" / "module_utils" / ".gitkeep").is_file()


def test_init_creates_config_yml_with_collection_section(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme", collection="platform")
    cfg = tmp_path / ".gludd" / "config.yml"
    assert cfg.is_file()
    data = yaml.safe_load(cfg.read_text())
    assert "collection" in data
    assert data["collection"]["namespace"] == "acme"
    assert data["collection"]["name"] == "platform"


def test_init_refuses_existing_unless_force(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme")
    galaxy = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "project" / "galaxy.yml"
    )
    assert galaxy.is_file()
    with pytest.raises(SystemExit) as excinfo:
        _run_init(tmp_path, namespace="acme")
    assert excinfo.value.code == 1


def test_init_force_overwrites_galaxy_yml(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme", collection="project")
    galaxy = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "project" / "galaxy.yml"
    )
    galaxy.write_text("namespace: old\nname: project\n")
    _run_init(tmp_path, namespace="acme", collection="project", force=True)
    data = yaml.safe_load(galaxy.read_text())
    assert data["namespace"] == "acme"
    assert data["name"] == "project"


def test_init_default_collection_name_is_project(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme")
    base = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "project"
    )
    assert base.is_dir()
    data = yaml.safe_load((base / "galaxy.yml").read_text())
    assert data["name"] == "project"


def test_init_namespace_required() -> None:
    parser, _ = build_parser()
    err = io.StringIO()
    with redirect_stderr(err), pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["project", "init"])
    assert excinfo.value.code == 2


def test_init_prints_precedence_summary(tmp_path: Path) -> None:
    out = _run_init(tmp_path, namespace="acme")
    assert "project" in out.lower() or "precedence" in out.lower()
    assert "user" in out.lower()
    assert "bundled" in out.lower()


def test_init_creates_terraform_plugins_dir(tmp_path: Path) -> None:
    _run_init(tmp_path, namespace="acme")
    base = (
        tmp_path / ".gludd" / "collections" / "ansible_collections"
        / "acme" / "project"
    )
    assert (base / "plugins" / "terraform" / ".gitkeep").is_file()
