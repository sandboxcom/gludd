from __future__ import annotations

import os

import yaml

from general_ludd.dependency.manager import DependencyManager


class TestDependencyPipelineE2E:
    async def test_dependency_manager_update_with_uv(self):
        mgr = DependencyManager(project_root=".")
        result = await mgr.update_package("nonexistent-pkg-e2e-test")
        assert result.package_name == "nonexistent-pkg-e2e-test"

    async def test_dependency_manager_update_returns_result(self):
        mgr = DependencyManager(project_root=".")
        result = await mgr.update_package("another-fake-pkg")
        assert result.package_name == "another-fake-pkg"

    async def test_sync_environment(self):
        mgr = DependencyManager(project_root=".")
        result = await mgr.sync_environment()
        assert isinstance(result.success, bool)

    async def test_check_for_updates_returns_list(self):
        mgr = DependencyManager(project_root=".")
        outdated = await mgr.check_for_updates()
        assert isinstance(outdated, list)

    async def test_generate_requirements(self):
        mgr = DependencyManager(project_root=".")
        result = await mgr.generate_requirements()
        assert result is None

    def test_dependency_update_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        playbook_path = os.path.join(repo_root, "playbooks", "dependency_update.yml")
        assert os.path.exists(playbook_path)
        with open(playbook_path) as f:
            data = yaml.safe_load(f)
        assert data is not None
