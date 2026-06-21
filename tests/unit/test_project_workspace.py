"""Unit tests for per-project filesystem isolation."""

from __future__ import annotations

import os
from pathlib import Path

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

    def test_relative_base_dir_does_not_leak_into_cwd(self, tmp_path, monkeypatch):
        """A RELATIVE base_dir must not materialize dirs under the process CWD.

        REGRESSION GUARD (leak incident): a now-removed workspace test exercised
        ``ProjectWorkspace``/``materialize_project_workspace`` with a *relative*
        ``base_dir`` (e.g. ``"nested"`` / ``"proj-ok"``) while pytest's CWD was the
        repo root. ``Path(base_dir) / project_id`` then resolved against the CWD and
        ``ensure_dirs()`` planted ``nested/proj-ok/repo`` and ``proj-ok/repo`` into
        the repo root, leaking out of tmp. Every test MUST anchor ``base_dir`` under
        ``tmp_path``; this test asserts that running with a relative ``base_dir``
        from a tmp CWD keeps all created dirs inside that tmp CWD (never the real
        repo root) so the leak can never silently recur.
        """
        # Run from an isolated tmp CWD so even a relative base_dir stays contained.
        monkeypatch.chdir(tmp_path)
        repo_root_before = set(os.listdir(tmp_path))

        ws = ProjectWorkspace(project_id="proj-ok", base_dir="nested")
        ws.ensure_dirs()

        # The workspace tree must live under the tmp CWD, never escape it.
        resolved_root = Path(ws.root).resolve()
        assert resolved_root.is_relative_to(tmp_path.resolve()), (
            f"workspace root {resolved_root} escaped the tmp CWD {tmp_path}"
        )
        # Exactly the expected relative subtree appeared (nested/proj-ok/repo, ...).
        assert (tmp_path / "nested" / "proj-ok" / "repo").is_dir()
        # Nothing leaked as a sibling of base_dir — only "nested" is new at the top.
        assert set(os.listdir(tmp_path)) - repo_root_before == {"nested"}
