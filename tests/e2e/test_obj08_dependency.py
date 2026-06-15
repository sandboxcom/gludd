from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from general_ludd.dependency.manager import DependencyManager

REPO_ROOT = Path(__file__).resolve().parents[2]

# A throwaway pyproject so update_package()'s `uv add` mutates a TEMP manifest,
# never the real repo one. The previous version passed project_root="." and
# permanently polluted the real pyproject.toml with `nonexistent-pkg-e2e-test`,
# breaking dependency resolution for every later `uv run` in the suite.
_MINIMAL_PYPROJECT = """\
[project]
name = "dep-e2e-tmp"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = []
"""


@pytest.fixture
def tmp_project(tmp_path: Path) -> str:
    (tmp_path / "pyproject.toml").write_text(_MINIMAL_PYPROJECT)
    return str(tmp_path)


class TestDependencyPipelineE2E:
    async def test_dependency_manager_update_with_uv(self, tmp_project: str):
        # Isolated project_root: `uv add` mutates the temp manifest, not the repo.
        mgr = DependencyManager(project_root=tmp_project)
        result = await mgr.update_package("nonexistent-pkg-e2e-test")
        assert result.package_name == "nonexistent-pkg-e2e-test"

    async def test_dependency_manager_update_returns_result(self, tmp_project: str):
        mgr = DependencyManager(project_root=tmp_project)
        result = await mgr.update_package("another-fake-pkg")
        assert result.package_name == "another-fake-pkg"

    async def test_sync_environment(self, tmp_project: str):
        mgr = DependencyManager(project_root=tmp_project)
        result = await mgr.sync_environment()
        assert isinstance(result.success, bool)

    async def test_check_for_updates_returns_list(self, tmp_project: str):
        mgr = DependencyManager(project_root=tmp_project)
        outdated = await mgr.check_for_updates()
        assert isinstance(outdated, list)

    async def test_generate_requirements(self, tmp_project: str):
        mgr = DependencyManager(project_root=tmp_project)
        result = await mgr.generate_requirements()
        assert result is None

    def test_dependency_update_playbook_exists(self):
        playbook_path = REPO_ROOT / "playbooks" / "dependency_update.yml"
        assert playbook_path.exists()
        with open(playbook_path) as f:
            data = yaml.safe_load(f)
        assert data is not None


class TestManifestNotPolluted:
    """Regression guard (#30): a test that mutates dependencies must use an
    isolated project_root, never '.'. If a fake/test package ever lands in the
    REAL pyproject.toml, dependency resolution breaks for the whole suite — so
    fail loudly here at collection time rather than cryptically later.
    """

    FORBIDDEN = ("nonexistent-pkg-e2e-test", "another-fake-pkg")

    def test_real_pyproject_has_no_test_pollution(self):
        text = (REPO_ROOT / "pyproject.toml").read_text()
        offenders = [pkg for pkg in self.FORBIDDEN if pkg in text]
        assert not offenders, (
            f"real pyproject.toml polluted by a test fixture: {offenders}. A "
            f"DependencyManager test used project_root='.' instead of a tmp project."
        )
