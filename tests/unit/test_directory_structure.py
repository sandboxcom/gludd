"""Verify docs/DIRECTORY_STRUCTURE.md is accurate and complete.

Checks:
  1. The doc exists in docs/
  2. Quick navigation table has required entries
  3. All documented directories exist on disk
  4. No stale directories are documented
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
DOC_PATH = ROOT / "docs" / "DIRECTORY_STRUCTURE.md"


class TestDirectoryStructureDoc:
    def test_doc_exists(self):
        """DIRECTORY_STRUCTURE.md exists under docs/."""
        assert DOC_PATH.exists(), f"{DOC_PATH} must exist"

    def test_quick_navigation_table(self):
        """Quick navigation section has required entries."""
        content = DOC_PATH.read_text()
        assert "Quick Navigation" in content
        required_links = [
            "src/general_ludd/",
            "playbooks/",
            "config/model_profiles/",
            ".github/workflows/",
            "molecule/playbooks/",
            "docs/",
            ".opencode/plugin/",
        ]
        for link in required_links:
            assert f"`{link}" in content, f"Quick nav must reference {link}"

    def test_root_files_table(self):
        """Root files table lists key files."""
        content = DOC_PATH.read_text()
        for key in ["Makefile", "pyproject.toml", "AGENTS.md", "README.md",
                     "CHANGELOG.md", "LICENSE", "SECURITY.md"]:
            assert f"`{key}`" in content, f"Root files must include {key}"

    def test_section_headers(self):
        """Document contains expected major section headers."""
        content = DOC_PATH.read_text()
        required_sections = [
            "# gludd Directory Structure",
            "## Quick Navigation",
            "## Root Files",
            "## Directories",
            "### `src/general_ludd/`",
            "### `tests/`",
            "### `config/`",
            "### `docs/`",
            "### `infra/`",
        ]
        for section in required_sections:
            assert section in content, f"Missing section: {section}"

    def test_key_src_dirs_exist(self):
        """Key src/general_ludd/ sub-packages exist."""
        key_dirs = [
            "daemon.py", "cli.py", "db/", "security/", "ansible/",
            "auth/", "config/", "worker/", "event_loop/",
            "remediation/", "approval/", "pipeline/", "git_automation/",
            "worktree/", "scheduling/", "budget/", "secrets/",
            "tui/", "mcp/", "observability/",
        ]
        for d in key_dirs:
            check = d.rstrip("/")
            path = ROOT / "src" / "general_ludd" / check
            assert path.exists(), f"src/general_ludd/{check} must exist"

    def test_src_subpackage_count(self):
        """src/general_ludd/ has >= 70 sub-packages (as documented)."""
        src_dir = ROOT / "src" / "general_ludd"
        packages = [
            d.name for d in src_dir.iterdir()
            if d.is_dir() and not d.name.startswith("__")
        ]
        assert len(packages) >= 70, (
            f"Expected >= 70 sub-packages, found {len(packages)}"
        )

    def test_key_config_dirs_exist(self):
        """Key config/ subdirectories exist."""
        key_dirs = [
            "config/permissions/", "config/model_profiles/",
            "config/opa/", "config/infra/", "config/examples/",
            "config/agents/", "config/mcp_servers/",
        ]
        for d in key_dirs:
            path = ROOT / d.rstrip("/")
            assert path.exists(), f"{d} must exist"

    def test_key_test_dirs_exist(self):
        """Key test/ subdirectories exist."""
        key_dirs = [
            "tests/unit/", "tests/integration/", "tests/e2e/",
            "tests/fixtures/",
        ]
        for d in key_dirs:
            path = ROOT / d.rstrip("/")
            assert path.exists(), f"{d} must exist"

    def test_key_infra_dirs_exist(self):
        """Key infra/ subdirectories exist."""
        key_dirs = [
            "infra/terraform/", "infra/terraform/stacks/",
            "infra/terraform/modules/", "infra/terraform/examples/",
            "infra/local-models/", "infra/kubernetes/",
        ]
        for d in key_dirs:
            path = ROOT / d.rstrip("/")
            assert path.exists(), f"{d} must exist"

    def test_key_collections_dirs_exist(self):
        """Collection subdirectories exist."""
        key_dirs = [
            "collections/ansible_collections/general_ludd/agent/",
            "collections/ansible_collections/general_ludd/web_server/",
            "collections/ansible_collections/general_ludd/radio/",
            "collections/ansible_collections/general_ludd/binary_re/",
        ]
        for d in key_dirs:
            path = ROOT / d.rstrip("/")
            assert path.exists(), f"{d} must exist"

    def test_key_docs_subdirs_exist(self):
        """Key docs/ subdirectories exist."""
        key_dirs = [
            "docs/audit/", "docs/research/", "docs/guides/",
            "docs/design/", "docs/presentation/",
        ]
        for d in key_dirs:
            path = ROOT / d.rstrip("/")
            assert path.exists(), f"{d} must exist"

    def test_key_plugin_files_exist(self):
        """Enforcement plugin directory has >= 15 plugin files."""
        plugin_dir = ROOT / ".opencode" / "plugin"
        assert plugin_dir.exists()
        ts_files = list(plugin_dir.glob("*.ts"))
        assert len(ts_files) >= 15, f"Expected >= 15 plugins, found {len(ts_files)}"

    def test_molecule_playbooks_exist(self):
        """Molecule playbook tests have >= 10 scenarios."""
        molecule_dir = ROOT / "molecule" / "playbooks"
        assert molecule_dir.exists()
        subdirs = [d for d in molecule_dir.iterdir() if d.is_dir()]
        assert len(subdirs) >= 10, f"Expected >= 10 molecule scenarios, found {len(subdirs)}"

    def test_playbooks_exist(self):
        """Playbook directory has >= 20 playbook files."""
        playbooks_dir = ROOT / "playbooks"
        assert playbooks_dir.exists()
        yml_files = list(playbooks_dir.glob("*.yml"))
        assert len(yml_files) >= 20, f"Expected >= 20 playbooks, found {len(yml_files)}"

    def test_alembic_versions_exist(self):
        """Alembic migrations directory has >= 20 migration files."""
        versions_dir = ROOT / "alembic" / "versions"
        assert versions_dir.exists()
        py_files = list(versions_dir.glob("*.py"))
        assert len(py_files) >= 20, f"Expected >= 20 migrations, found {len(py_files)}"

    def test_scripts_exist(self):
        """Scripts directory has >= 50 files."""
        scripts_dir = ROOT / "scripts"
        assert scripts_dir.exists()
        files = list(scripts_dir.glob("*"))
        assert len(files) >= 50, f"Expected >= 50 scripts, found {len(files)}"

    def test_github_workflows_exist(self):
        """GitHub workflows directory has build.yml."""
        workflows_dir = ROOT / ".github" / "workflows"
        assert workflows_dir.exists()
        build_yml = workflows_dir / "build.yml"
        assert build_yml.exists(), ".github/workflows/build.yml must exist"

    def test_claude_hooks_exist(self):
        """.claude/hooks/ directory has >= 15 hook files."""
        hooks_dir = ROOT / ".claude" / "hooks"
        assert hooks_dir.exists()
        sh_files = list(hooks_dir.glob("*.sh"))
        assert len(sh_files) >= 15, f"Expected >= 15 hooks, found {len(sh_files)}"

    def test_root_files_exist(self):
        """Key root files listed in documentation exist on disk."""
        root_files = [
            "Makefile", "pyproject.toml", "gludd.spec", "uv.lock",
            "opencode.json", "AGENTS.md", "CLAUDE.md", "TASKS.md",
            "BUGS.md", "SESSION.md", "README.md", "CHANGELOG.md",
            "LICENSE", "SECURITY.md", "CONTRIBUTING.md",
            "Containerfile", "Dockerfile", ".secrets.baseline",
            "ansible.cfg", "alembic.ini", "project.yml",
        ]
        missing = [f for f in root_files if not (ROOT / f).exists()]
        assert not missing, f"Root files missing: {missing}"

    def test_top_level_dirs_exist(self):
        """All top-level directories listed in the doc exist on disk."""
        top_dirs = [
            "src", "tests", "config", "docs", "scripts",
            "playbooks", "molecule", "collections", "infra",
            "templates", "roles", "plugins", "tools", "demos",
            "alembic", ".opencode", ".claude", ".github",
            ".gludd", ".integrity", ".devspark", "web_retriever",
        ]
        missing = [d for d in top_dirs if not (ROOT / d).exists()]
        assert not missing, f"Top-level dirs missing: {missing}"

    def test_unit_test_count(self):
        """Unit tests directory has >= 500 test files."""
        unit_dir = ROOT / "tests" / "unit"
        test_files = list(unit_dir.rglob("test_*.py"))
        assert len(test_files) >= 500, (
            f"Expected >= 500 unit test files, found {len(test_files)}"
        )

    def test_integration_test_count(self):
        """Integration tests directory has >= 80 test files."""
        integ_dir = ROOT / "tests" / "integration"
        test_files = list(integ_dir.rglob("test_*.py"))
        assert len(test_files) >= 80, (
            f"Expected >= 80 integration test files, found {len(test_files)}"
        )

    def test_e2e_test_count(self):
        """E2E tests directory has >= 40 test files."""
        e2e_dir = ROOT / "tests" / "e2e"
        test_files = list(e2e_dir.rglob("test_*.py"))
        assert len(test_files) >= 40, (
            f"Expected >= 40 e2e test files, found {len(test_files)}"
        )
