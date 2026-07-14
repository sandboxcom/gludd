from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.ansible.runner import AnsibleRunnerAdapter


class TestRunnerResolution:
    def test_runner_discovers_playbooks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook_a = Path(tmpdir) / "a.yml"
            playbook_b = Path(tmpdir) / "b.yml"
            playbook_a.write_text("- hosts: localhost\n  tasks: []\n")
            playbook_b.write_text("- hosts: localhost\n  tasks: []\n")

            runner = AnsibleRunnerAdapter(playbooks_dir=tmpdir)
            playbooks = runner.list_playbooks()
            assert "a.yml" in playbooks
            assert "b.yml" in playbooks

    def test_unknown_playbook_returns_failed_result_not_raise(self):
        runner = AnsibleRunnerAdapter(playbooks_dir="/nonexistent")
        result = runner.run_playbook("nonexistent.yml")
        assert result["status"] == "failed"
        assert result["rc"] != 0
        assert "not registered" in result["error"]

    def test_noop_registered_by_default(self):
        runner = AnsibleRunnerAdapter()
        playbooks = runner.list_playbooks()
        assert "noop.yml" in playbooks

    def test_run_playbook_catches_core_runner_failure(self):
        runner = AnsibleRunnerAdapter()
        result = runner.run_playbook("noop.yml")
        assert "status" in result

    def test_resolve_playbook_raises_for_unknown(self):
        runner = AnsibleRunnerAdapter()
        with pytest.raises(ValueError, match="not registered"):
            runner.resolve_playbook("nonexistent.yml")


class TestRunnerConstruction:
    def test_runner_constructed_with_playbooks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pf = Path(tmpdir) / "x.yml"
            pf.write_text("- hosts: localhost\n  tasks: []\n")
            runner = AnsibleRunnerAdapter(playbooks_dir=tmpdir)
            assert "x.yml" in runner.list_playbooks()

    def test_runner_constructed_without_playbooks_dir(self):
        runner = AnsibleRunnerAdapter()
        assert "noop.yml" in runner.list_playbooks()


def _make_versioned_collection(base: Path, namespace: str, collection: str, version: str) -> Path:
    coll_root = base / "ansible_collections" / f"{namespace}@{version}" / collection
    (coll_root / "roles" / "test_role" / "tasks").mkdir(parents=True)
    (coll_root / "plugins" / "modules").mkdir(parents=True)
    (coll_root / "roles" / "test_role" / "tasks" / "main.yml").write_text(
        f"- name: version {version}\n"
    )
    return coll_root


class TestRunnerVersionActivation:
    def test_activate_collection_creates_symlink(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        proj = tmp_path / "proj"
        coll_root = proj / ".gludd" / "collections"
        coll_root.mkdir(parents=True)
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_path / "bundled" / "collections",
        )

        runner = AnsibleRunnerAdapter(project_root=proj)
        activation_root = runner.activate_collection("general_ludd", "agent", version="0.1.0")
        link = activation_root / "ansible_collections" / "general_ludd" / "agent"
        assert link.is_symlink()
        runner.clear_collection_versions()

    def test_activate_collection_without_version_uses_precedence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        proj = tmp_path / "proj"
        coll_root = proj / ".gludd" / "collections"
        coll_root.mkdir(parents=True)
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        _make_versioned_collection(coll_root, "general_ludd", "agent", "latest")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_path / "bundled" / "collections",
        )

        runner = AnsibleRunnerAdapter(project_root=proj)
        activation_root = runner.activate_collection("general_ludd", "agent")
        link = activation_root / "ansible_collections" / "general_ludd" / "agent"
        assert "@latest" in str(link.resolve())
        runner.clear_collection_versions()

    def test_clear_collection_versions_removes_activations(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        proj = tmp_path / "proj"
        coll_root = proj / ".gludd" / "collections"
        coll_root.mkdir(parents=True)
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_path / "bundled" / "collections",
        )

        runner = AnsibleRunnerAdapter(project_root=proj)
        activation_root = runner.activate_collection("general_ludd", "agent", version="0.1.0")
        assert len(runner._version_activation_roots) == 1
        runner.clear_collection_versions()
        assert runner._version_activation_roots == []
        assert runner._version_cleanup_dirs == []
        assert not activation_root.exists()

    def test_run_playbook_prepends_activation_root_to_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        proj = tmp_path / "proj"
        coll_root = proj / ".gludd" / "collections"
        coll_root.mkdir(parents=True)
        _make_versioned_collection(coll_root, "general_ludd", "agent", "0.1.0")
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_path / "bundled" / "collections",
        )

        with patch("general_ludd.ansible.runner.CoreAnsibleRunner") as mock_cls:
            mock_core = MagicMock()
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"status": "successful", "rc": 0}
            mock_core.run_playbook.return_value = mock_result
            mock_cls.return_value = mock_core

            runner = AnsibleRunnerAdapter(project_root=proj)
            activation_root = runner.activate_collection("general_ludd", "agent", version="0.1.0")
            runner.run_playbook("noop.yml")

            call_kwargs = mock_core.run_playbook.call_args.kwargs
            extra_env = call_kwargs.get("extra_env") or {}
            cp = extra_env["ANSIBLE_COLLECTIONS_PATH"]
            assert str(activation_root) in cp.split(os.pathsep)[0]
            runner.clear_collection_versions()

    def test_activate_missing_collection_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        proj = tmp_path / "proj"
        (proj / ".gludd" / "collections").mkdir(parents=True)
        monkeypatch.setattr(
            "general_ludd.ansible.paths._bundled_collections_root",
            lambda: tmp_path / "bundled" / "collections",
        )

        runner = AnsibleRunnerAdapter(project_root=proj)
        with pytest.raises(FileNotFoundError, match=r"nope\.absent"):
            runner.activate_collection("nope", "absent", version="0.1.0")
