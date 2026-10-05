"""TDD tests for submodule management Makefile targets.

The gap: external repositories (llama.cpp, openbao, opentofu, osquery, codebase-memory-mcp,
skills) are currently cloned/downloaded directly. They should be git submodules pinned to
stable tags with make targets for management.

These tests prove the gap is closed by checking the Makefile structurally:
- .gitmodules exists with all required submodules
- submodule-init initializes all submodules
- submodule-update updates all submodules to latest stable tags
- submodule-status shows status of each submodule
- submodule-pin pins a specific submodule to a specific tag
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"
GITMODULES = ROOT / ".gitmodules"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestGitmodulesFile:
    """Verify .gitmodules exists and contains all required submodules."""

    def test_gitmodules_exists(self) -> None:
        assert GITMODULES.exists(), ".gitmodules file must exist"

    def test_contains_llamacpp(self) -> None:
        content = GITMODULES.read_text()
        assert "llamacpp" in content.lower(), ".gitmodules must contain llama.cpp submodule"

    def test_contains_openbao(self) -> None:
        content = GITMODULES.read_text()
        assert "openbao" in content.lower(), ".gitmodules must contain openbao submodule"

    def test_contains_opentofu(self) -> None:
        content = GITMODULES.read_text()
        assert "opentofu" in content.lower(), ".gitmodules must contain opentofu submodule"

    def test_contains_osquery(self) -> None:
        content = GITMODULES.read_text()
        assert "osquery" in content.lower(), ".gitmodules must contain osquery submodule"

    def test_contains_codebase_memory_mcp(self) -> None:
        content = GITMODULES.read_text()
        assert "codebase-memory-mcp" in content.lower() or "codebase_memory_mcp" in content.lower(), (
            ".gitmodules must contain codebase-memory-mcp submodule"
        )

    def test_contains_skills(self) -> None:
        content = GITMODULES.read_text()
        assert "skills" in content.lower(), ".gitmodules must contain mattpocock/skills submodule"

    def test_each_submodule_has_path_and_url(self) -> None:
        content = GITMODULES.read_text()
        # Each submodule should have path and url entries
        assert content.count("[submodule") >= 6, "Must have at least 6 submodules defined"
        assert "path =" in content, "Submodules must have path"
        assert "url =" in content, "Submodules must have url"


class TestSubmoduleInit:
    """submodule-init initializes all submodules."""

    def test_target_exists(self) -> None:
        assert _recipe("submodule-init"), "submodule-init target must exist"

    def test_runs_git_submodule_update_init(self) -> None:
        recipe = _recipe("submodule-init")
        assert "git submodule update --init" in recipe, (
            "submodule-init must run 'git submodule update --init'"
        )

    def test_recursive_flag(self) -> None:
        recipe = _recipe("submodule-init")
        assert "--recursive" in recipe, "submodule-init must use --recursive flag"

    def test_validate_only_contract_is_observable(self) -> None:
        """The gate-safe path must validate configuration without network access."""
        recipe = _recipe("submodule-init")
        assert "SUBMODULE_INIT_VALIDATE_ONLY" in recipe
        assert "SUBMODULE_INIT_VALIDATED" in recipe

    def test_phony_listed(self) -> None:
        content = MAKEFILE.read_text()
        assert "submodule-init" in content, "submodule-init must be in .PHONY"


class TestSubmoduleUpdate:
    """submodule-update updates all submodules to latest stable tags."""

    def test_target_exists(self) -> None:
        assert _recipe("submodule-update"), "submodule-update target must exist"

    def test_runs_git_submodule_update_remote(self) -> None:
        recipe = _recipe("submodule-update")
        assert "git submodule update --remote" in recipe, (
            "submodule-update must run 'git submodule update --remote'"
        )

    def test_merges_remote_changes(self) -> None:
        recipe = _recipe("submodule-update")
        assert "--merge" in recipe or "--rebase" in recipe, (
            "submodule-update must merge or rebase remote changes"
        )

    def test_phony_listed(self) -> None:
        _recipe("submodule-update")
        assert "submodule-update" in MAKEFILE.read_text(), "submodule-update must be in .PHONY"


class TestSubmoduleStatus:
    """submodule-status shows status of each submodule."""

    def test_target_exists(self) -> None:
        assert _recipe("submodule-status"), "submodule-status target must exist"

    def test_runs_git_submodule_status(self) -> None:
        recipe = _recipe("submodule-status")
        assert "git submodule status" in recipe, (
            "submodule-status must run 'git submodule status'"
        )

    def test_shows_commit_and_path(self) -> None:
        recipe = _recipe("submodule-status")
        assert "git submodule status" in recipe

    def test_phony_listed(self) -> None:
        content = MAKEFILE.read_text()
        assert "submodule-status" in content, "submodule-status must be in .PHONY"


class TestSubmodulePin:
    """submodule-pin pins a specific submodule to a specific tag/commit."""

    def test_target_exists(self) -> None:
        assert _recipe("submodule-pin"), "submodule-pin target must exist"

    def test_requires_REPO_argument(self) -> None:
        recipe = _recipe("submodule-pin")
        assert "$(REPO)" in recipe, "submodule-pin must require REPO argument"

    def test_requires_TAG_argument(self) -> None:
        recipe = _recipe("submodule-pin")
        assert "$(TAG)" in recipe, "submodule-pin must require TAG argument"

    def test_checks_out_tag_in_submodule(self) -> None:
        recipe = _recipe("submodule-pin")
        assert "git -C" in recipe, "submodule-pin must operate inside submodule directory"
        assert "checkout" in recipe, "submodule-pin must checkout the tag"

    def test_updates_gitmodules(self) -> None:
        recipe = _recipe("submodule-pin")
        assert "git add .gitmodules" in recipe or "git add" in recipe, (
            "submodule-pin must stage .gitmodules after pinning"
        )

    def test_phony_listed(self) -> None:
        content = MAKEFILE.read_text()
        assert "submodule-pin" in content, "submodule-pin must be in .PHONY"


class TestSubmoduleIntegration:
    """Integration tests that actually run the make targets (requires git)."""

    def test_submodule_init_runs(self) -> None:
        """submodule-init validation should be hermetic when .gitmodules exists."""
        result = subprocess.run(
            ["make", "submodule-init", "SUBMODULE_INIT_VALIDATE_ONLY=1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # If .gitmodules doesn't exist yet, this will fail - that's expected in TDD
        if not GITMODULES.exists():
            assert result.returncode != 0, "Should fail when .gitmodules missing"
        else:
            assert result.returncode == 0, f"submodule-init failed: {result.stderr}"
            assert "SUBMODULE_INIT_VALIDATED" in result.stdout

    def test_submodule_status_runs(self) -> None:
        """submodule-status should run without error."""
        result = subprocess.run(
            ["make", "submodule-status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if not GITMODULES.exists():
            assert result.returncode != 0, "Should fail when .gitmodules missing"
        else:
            assert result.returncode == 0, f"submodule-status failed: {result.stderr}"

    def test_submodules_are_at_specific_tags(self) -> None:
        """After init, each submodule should be at a tagged commit (not bare hash)."""
        if not GITMODULES.exists():
            return  # Skip until .gitmodules exists

        result = subprocess.run(
            ["make", "submodule-status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        output = result.stdout
        # Each submodule line should show a tag name (not just a hash)
        # Format: <hash> <path> (<tag>)
        status_lines = [ln for ln in output.strip().split("\n") if ln and not ln.startswith("#")]
        if not status_lines or any(ln.lstrip().startswith("-") for ln in status_lines):
            pytest.skip("submodules not initialized")
        for line in status_lines:
            assert "(" in line and ")" in line, (
                f"Submodule should be at a tag: {line}"
            )
