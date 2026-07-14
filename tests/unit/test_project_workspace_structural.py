"""Structural tests for projects/workspace.py — ProjectWorkspace + confine_workspace_path."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.projects.workspace import ProjectWorkspace, confine_workspace_path


class TestConfineWorkspacePath:
    def test_simple_name_under_base_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = confine_workspace_path(tmp, "myproject")
            expected = Path(tmp).resolve() / "myproject"
            assert Path(result).resolve() == expected

    def test_empty_workspace_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = confine_workspace_path(tmp, "")
            assert Path(result).resolve() == Path(tmp).resolve()

    def test_absolute_workspace_path_raises(self):
        with tempfile.TemporaryDirectory() as tmp, pytest.raises(ValueError, match="refusing absolute"):
            confine_workspace_path(tmp, "/etc/passwd")

    def test_dotdot_traversal_raises(self):
        with tempfile.TemporaryDirectory() as tmp, pytest.raises(ValueError, match=r"'..' traversal"):
            confine_workspace_path(tmp, "../../root/.ssh")

    def test_subdirectory_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = confine_workspace_path(tmp, "sub/dir")
            expected = Path(tmp).resolve() / "sub" / "dir"
            assert Path(result).resolve() == expected


class TestProjectWorkspace:
    def test_init_with_explicit_path(self):
        ws = ProjectWorkspace(project_id="p1", base_dir="/tmp/ws", workspace_path="/tmp/ws/p1")
        assert ws.project_id == "p1"
        assert ws.root == Path("/tmp/ws/p1")

    def test_init_without_workspace_path_uses_base_dir_plus_id(self):
        ws = ProjectWorkspace(project_id="proj-x", base_dir="/tmp/base")
        assert ws.root == Path("/tmp/base/proj-x")

    def test_artifacts_dir(self):
        ws = ProjectWorkspace(project_id="p2", base_dir="/tmp/ws", workspace_path="/tmp/ws/p2")
        assert ws.artifacts_dir == Path("/tmp/ws/p2/artifacts")

    def test_logs_dir(self):
        ws = ProjectWorkspace(project_id="p3", base_dir="/tmp/ws", workspace_path="/tmp/ws/p3")
        assert ws.logs_dir == Path("/tmp/ws/p3/logs")

    def test_config_dir(self):
        ws = ProjectWorkspace(project_id="p4", base_dir="/tmp/ws", workspace_path="/tmp/ws/p4")
        assert ws.config_dir == Path("/tmp/ws/p4/config")

    def test_repo_dir(self):
        ws = ProjectWorkspace(project_id="p5", base_dir="/tmp/ws", workspace_path="/tmp/ws/p5")
        assert ws.repo_dir == Path("/tmp/ws/p5/repo")

    def test_private_data_dir(self):
        ws = ProjectWorkspace(project_id="p6", base_dir="/tmp/ws", workspace_path="/tmp/ws/p6")
        assert ws.private_data_dir == Path("/tmp/ws/p6/runner")

    def test_playbooks_dir(self):
        ws = ProjectWorkspace(project_id="p7", base_dir="/tmp/ws", workspace_path="/tmp/ws/p7")
        assert ws.playbooks_dir == Path("/tmp/ws/p7/playbooks")

    def test_templates_dir(self):
        ws = ProjectWorkspace(project_id="p8", base_dir="/tmp/ws", workspace_path="/tmp/ws/p8")
        assert ws.templates_dir == Path("/tmp/ws/p8/templates")

    def test_roles_dir(self):
        ws = ProjectWorkspace(project_id="p9", base_dir="/tmp/ws", workspace_path="/tmp/ws/p9")
        assert ws.roles_dir == Path("/tmp/ws/p9/roles")

    def test_job_artifact_dir(self):
        ws = ProjectWorkspace(project_id="p10", base_dir="/tmp/ws", workspace_path="/tmp/ws/p10")
        job_dir = ws.job_artifact_dir("job-42")
        assert job_dir == Path("/tmp/ws/p10/artifacts/job-42")

    def test_ensure_dirs_creates_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = ProjectWorkspace(project_id="test-ensure", base_dir=tmp)
            ws.ensure_dirs()
            assert ws.root.exists()
            assert ws.artifacts_dir.exists()
            assert ws.logs_dir.exists()
            assert ws.config_dir.exists()
            assert ws.repo_dir.exists()
            assert ws.private_data_dir.exists()
            assert ws.playbooks_dir.exists()
            assert ws.templates_dir.exists()
            assert ws.roles_dir.exists()

    def test_to_dict(self):
        ws = ProjectWorkspace(project_id="p-dict", base_dir="/tmp/ws", workspace_path="/tmp/ws/p-dict")
        d = ws.to_dict()
        assert d["project_id"] == "p-dict"
        assert "root" in d
        assert "artifacts_dir" in d
        assert "logs_dir" in d
        assert "config_dir" in d
        assert "repo_dir" in d
        assert "private_data_dir" in d
