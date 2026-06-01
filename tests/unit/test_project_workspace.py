"""Unit tests for per-project filesystem isolation."""

from __future__ import annotations

from general_ludd.projects.workspace import ProjectWorkspace


class TestProjectWorkspace:
    def test_workspace_creates_project_dirs(self, tmp_path):
        ws = ProjectWorkspace(
            project_id="proj-alpha",
            base_dir=str(tmp_path),
        )
        ws.ensure_dirs()
        assert ws.root.exists()
        assert ws.artifacts_dir.exists()
        assert ws.logs_dir.exists()
        assert ws.config_dir.exists()
        assert ws.repo_dir.exists()
        assert ws.private_data_dir.exists()

    def test_two_projects_isolated_dirs(self, tmp_path):
        alpha = ProjectWorkspace(project_id="alpha", base_dir=str(tmp_path))
        beta = ProjectWorkspace(project_id="beta", base_dir=str(tmp_path))
        alpha.ensure_dirs()
        beta.ensure_dirs()

        assert alpha.root != beta.root
        assert str(alpha.root).endswith("alpha")
        assert str(beta.root).endswith("beta")
        assert not alpha.artifacts_dir.exists() or alpha.artifacts_dir != beta.artifacts_dir

    def test_job_artifact_dir_per_project(self, tmp_path):
        ws = ProjectWorkspace(project_id="proj-alpha", base_dir=str(tmp_path))
        job_dir = ws.job_artifact_dir("EXEC-001")
        assert "proj-alpha" in str(job_dir)
        assert "EXEC-001" in str(job_dir)

    def test_workspace_custom_path(self, tmp_path):
        custom = tmp_path / "custom-location"
        ws = ProjectWorkspace(
            project_id="proj-alpha",
            workspace_path=str(custom),
        )
        assert ws.root == custom

    def test_to_dict(self, tmp_path):
        ws = ProjectWorkspace(project_id="proj-alpha", base_dir=str(tmp_path))
        d = ws.to_dict()
        assert d["project_id"] == "proj-alpha"
        assert "artifacts_dir" in d
        assert "logs_dir" in d
        assert "config_dir" in d
