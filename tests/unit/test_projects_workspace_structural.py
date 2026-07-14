"""Structural tests for projects/workspace.py — filesystem workspace isolation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.projects.workspace import ProjectWorkspace, confine_workspace_path


class TestConfineWorkspacePath:
    def test_simple_name_under_base(self):
        with tempfile.TemporaryDirectory() as base:
            result = confine_workspace_path(base, "myproject")
            expected = os.path.realpath(os.path.join(base, "myproject"))
            assert result == expected

    def test_deep_name_under_base(self):
        with tempfile.TemporaryDirectory() as base:
            result = confine_workspace_path(base, "team/myproject")
            expected = os.path.realpath(os.path.join(base, "team/myproject"))
            assert result == expected

    def test_refuses_absolute_path(self):
        with (
            tempfile.TemporaryDirectory() as base,
            pytest.raises(ValueError, match="refusing absolute workspace_path"),
        ):
            confine_workspace_path(base, "/etc/passwd")

    def test_refuses_dotdot_traversal(self):
        with (
            tempfile.TemporaryDirectory() as base,
            pytest.raises(ValueError, match=r"refusing workspace_path containing '\.\."),
        ):
            confine_workspace_path(base, "../escape")

    def test_refuses_deep_dotdot_traversal(self):
        with (
            tempfile.TemporaryDirectory() as base,
            pytest.raises(ValueError, match=r"refusing workspace_path containing '\.\."),
        ):
            confine_workspace_path(base, "foo/../../bar")

    def test_name_with_dots_is_ok(self):
        with tempfile.TemporaryDirectory() as base:
            result = confine_workspace_path(base, "my.project")
            assert os.path.basename(result) == "my.project"

    def test_empty_name_under_base(self):
        with tempfile.TemporaryDirectory() as base:
            result = confine_workspace_path(base, "")
            assert result == os.path.realpath(base)


class TestProjectWorkspace:
    def test_construction_with_explicit_path(self):
        ws = ProjectWorkspace("proj-1", "/tmp/ws", workspace_path="/tmp/ws/myteam")
        assert ws.project_id == "proj-1"
        assert ws.root == Path("/tmp/ws/myteam")

    def test_construction_default_path(self):
        ws = ProjectWorkspace("proj-abc", "/tmp/ws")
        assert ws.root == Path("/tmp/ws") / "proj-abc"

    def test_artifacts_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.artifacts_dir == Path("/tmp") / "p1" / "artifacts"

    def test_logs_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.logs_dir == Path("/tmp") / "p1" / "logs"

    def test_config_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.config_dir == Path("/tmp") / "p1" / "config"

    def test_repo_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.repo_dir == Path("/tmp") / "p1" / "repo"

    def test_private_data_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.private_data_dir == Path("/tmp") / "p1" / "runner"

    def test_playbooks_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.playbooks_dir == Path("/tmp") / "p1" / "playbooks"

    def test_templates_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.templates_dir == Path("/tmp") / "p1" / "templates"

    def test_roles_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.roles_dir == Path("/tmp") / "p1" / "roles"

    def test_ensure_dirs_creates_all(self):
        with tempfile.TemporaryDirectory() as base:
            ws = ProjectWorkspace("proj-x", base)
            ws.ensure_dirs()
            for d in (ws.root, ws.artifacts_dir, ws.logs_dir, ws.config_dir,
                      ws.repo_dir, ws.private_data_dir, ws.playbooks_dir,
                      ws.templates_dir, ws.roles_dir):
                assert d.is_dir(), f"expected {d} to exist"

    def test_ensure_dirs_idempotent(self):
        with tempfile.TemporaryDirectory() as base:
            ws = ProjectWorkspace("proj-x", base)
            ws.ensure_dirs()
            ws.ensure_dirs()
            assert ws.root.is_dir()

    def test_job_artifact_dir(self):
        ws = ProjectWorkspace("p1", "/tmp")
        assert ws.job_artifact_dir("job-42") == Path("/tmp") / "p1" / "artifacts" / "job-42"

    def test_to_dict(self):
        ws = ProjectWorkspace("proj-1", "/tmp/ws")
        d = ws.to_dict()
        assert d["project_id"] == "proj-1"
        assert "artifacts_dir" in d
        assert "logs_dir" in d
        assert "config_dir" in d
        assert "repo_dir" in d
        assert "private_data_dir" in d
        assert "root" in d
