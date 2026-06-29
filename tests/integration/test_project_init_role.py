"""Integration tests for the ``general_ludd.agent.project_init`` role.

Runs the role for real against a tmp project dir via
``AnsibleRunnerAdapter.run_playbook`` and asserts the scaffold tree matches
the contract: galaxy.yml with namespace+name, the roles/ + plugins/
skeleton dirs, the terraform plugins dir, config.yml collection section,
refusal without --force, and force-overwrite behaviour.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from general_ludd.ansible.runner import AnsibleRunnerAdapter

_PLAYBOOK = "project_init.yml"


def _run_role(
    project_dir: Path,
    namespace: str = "acme",
    collection: str = "project",
    force: bool = False,
) -> dict:
    adapter = AnsibleRunnerAdapter(project_root=str(project_dir))
    pb = Path(__file__).resolve().parent.parent.parent / "playbooks" / _PLAYBOOK
    if _PLAYBOOK not in adapter.list_playbooks():
        adapter.register_playbook(_PLAYBOOK, str(pb))
    return adapter.run_playbook(
        _PLAYBOOK,
        extravars={
            "collection_namespace": namespace,
            "collection_name": collection,
            "project_dir": str(project_dir),
            "force": force,
        },
    )


def _collection_root(project_dir: Path, namespace: str, collection: str) -> Path:
    return (
        project_dir
        / ".gludd"
        / "collections"
        / "ansible_collections"
        / namespace
        / collection
    )


def _has_ansible() -> bool:
    return shutil.which("ansible-playbook") is not None


pytestmark = pytest.mark.skipif(
    not _has_ansible(), reason="ansible-playbook not installed"
)


def test_role_creates_gludd_collections_dir(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme")
    assert result.get("rc", 1) == 0, result
    assert (tmp_path / ".gludd" / "collections").is_dir()


def test_role_creates_galaxy_yml_with_namespace_and_name(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme", collection="platform")
    assert result.get("rc", 1) == 0, result
    galaxy = _collection_root(tmp_path, "acme", "platform") / "galaxy.yml"
    assert galaxy.is_file()
    data = yaml.safe_load(galaxy.read_text())
    assert data["namespace"] == "acme"
    assert data["name"] == "platform"
    assert data["version"] == "1.0.0"


def test_role_creates_roles_and_plugins_dirs(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme")
    assert result.get("rc", 1) == 0, result
    base = _collection_root(tmp_path, "acme", "project")
    assert (base / "roles" / ".gitkeep").is_file()
    assert (base / "plugins" / "modules" / ".gitkeep").is_file()
    assert (base / "plugins" / "module_utils" / ".gitkeep").is_file()


def test_role_creates_config_yml_with_collection_section(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme", collection="platform")
    assert result.get("rc", 1) == 0, result
    cfg = tmp_path / ".gludd" / "config.yml"
    assert cfg.is_file()
    data = yaml.safe_load(cfg.read_text())
    assert "collection" in data
    assert data["collection"]["namespace"] == "acme"
    assert data["collection"]["name"] == "platform"


def test_role_refuses_existing_unless_force(tmp_path: Path) -> None:
    first = _run_role(tmp_path, namespace="acme")
    assert first.get("rc", 1) == 0, first
    second = _run_role(tmp_path, namespace="acme")
    assert second.get("rc", 0) != 0, "role should have refused to overwrite"


def test_role_force_overwrites_galaxy_yml(tmp_path: Path) -> None:
    first = _run_role(tmp_path, namespace="acme", collection="project")
    assert first.get("rc", 1) == 0, first
    galaxy = _collection_root(tmp_path, "acme", "project") / "galaxy.yml"
    galaxy.write_text("namespace: old\nname: project\n")
    forced = _run_role(tmp_path, namespace="acme", collection="project", force=True)
    assert forced.get("rc", 1) == 0, forced
    data = yaml.safe_load(galaxy.read_text())
    assert data["namespace"] == "acme"
    assert data["name"] == "project"


def test_role_default_collection_name_is_project(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme")
    assert result.get("rc", 1) == 0, result
    base = _collection_root(tmp_path, "acme", "project")
    assert base.is_dir()
    data = yaml.safe_load((base / "galaxy.yml").read_text())
    assert data["name"] == "project"


def test_role_creates_terraform_plugins_dir(tmp_path: Path) -> None:
    result = _run_role(tmp_path, namespace="acme")
    assert result.get("rc", 1) == 0, result
    base = _collection_root(tmp_path, "acme", "project")
    assert (base / "plugins" / "terraform" / ".gitkeep").is_file()
