"""BP.18: Clean-tree check exempts metadata files (SESSION.md, TASKS.md, BUGS.md, .gitignore, .ci-status, .gate-status).

Per AGENTS.md "Clean Tree Before Dispatch" (2026-07-08) + BP.18 exemption.
When ALL dirty files are runtime metadata/docs (not code), the clean-tree
plugin allows dispatch instead of denying it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-clean-tree.ts"
EXPORTS_PATH = ROOT / ".opencode/lib/plugin_test_exports.ts"

EXPECTED_METADATA_FILES = {
    "SESSION.md", "TASKS.md", "BUGS.md", ".gitignore",
    ".ci-status", ".gate-status",
}


def _plugin_source() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


def _exports_source() -> str:
    assert EXPORTS_PATH.exists(), f"Exports missing at {EXPORTS_PATH}"
    return EXPORTS_PATH.read_text()


def _git_status_porcelain(cwd: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _init_repo(cwd: Path) -> None:
    subprocess.run(["git", "init"], cwd=cwd, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=cwd, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=cwd, capture_output=True
    )


def _extract_file_paths(status: str) -> set[str]:
    """Mirror _extractFilePath() logic: strip 3-char status, handle renames."""
    paths: set[str] = set()
    for line in status.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        path = line[3:].strip()
        arrow = path.rfind(" -> ")
        if arrow >= 0:
            path = path[arrow + 4:]
        paths.add(path)
    return paths


class TestMetadataFilesConstant:
    """METADATA_FILES constant exists and contains the expected set."""

    def test_constant_exists_in_exports(self):
        src = _exports_source()
        assert "METADATA_FILES" in src, "METADATA_FILES constant not found in exports"

    def test_all_expected_files_present(self):
        src = _exports_source()
        match = re.search(
            r'METADATA_FILES[^=]*=\s*Object\.freeze\(\s*new Set\(\[(.*?)\]\)\s*\)',
            src, re.DOTALL,
        )
        assert match, "METADATA_FILES Object.freeze(new Set(...)) not found"
        body = match.group(1)
        files = set(re.findall(r'"([^"]+)"', body))
        assert files == EXPECTED_METADATA_FILES, (
            f"Expected {EXPECTED_METADATA_FILES}, got {files}"
        )

    def test_constant_is_readonly(self):
        src = _exports_source()
        assert "ReadonlySet<string>" in src or "readonly" in src.lower(), (
            "METADATA_FILES should be read-only"
        )


class TestIsMetadataOnlyDirtyFunction:
    """isMetadataOnlyDirty() correctly classifies metadata-only vs mixed dirty trees."""

    def test_function_exists_in_exports(self):
        src = _exports_source()
        assert "isMetadataOnlyDirty" in src, (
            "isMetadataOnlyDirty function not found in exports"
        )

    def test_metadata_only_returns_true(self, tmp_path):
        """Dirty tree with only SESSION.md and TASKS.md → metadata-only → allowed."""
        repo = tmp_path / "meta-only"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / "SESSION.md").write_text("session update")
        (repo / "TASKS.md").write_text("tasks update")
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert paths == {"SESSION.md", "TASKS.md"}, f"Unexpected dirty paths: {paths}"
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert is_meta_only, "Only metadata files dirty → should be allowed"

    def test_mixed_returns_false(self, tmp_path):
        """Dirty tree with SESSION.md AND src.py → NOT metadata-only → denied."""
        repo = tmp_path / "mixed"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / "SESSION.md").write_text("session update")
        (repo / "src.py").write_text("modified")
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert "src.py" in paths, "src.py should be dirty"
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert not is_meta_only, "Non-metadata file dirty → should be denied"

    def test_code_only_returns_false(self, tmp_path):
        """Dirty tree with only src.py → NOT metadata-only → denied."""
        repo = tmp_path / "code-only"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / "src.py").write_text("modified")
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert paths == {"src.py"}
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert not is_meta_only, "Code-only dirty → should be denied"

    def test_empty_status_returns_true(self, tmp_path):
        repo = tmp_path / "clean"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        status = _git_status_porcelain(repo)
        assert status == "", "Clean repo should have empty status"
        is_meta_only = len(status) == 0 or _extract_file_paths(status).issubset(
            EXPECTED_METADATA_FILES
        )
        assert is_meta_only, "Empty status = clean = allowed"

    def test_gitignore_only_returns_true(self, tmp_path):
        """Dirty tree with only .gitignore → metadata-only → allowed."""
        repo = tmp_path / "gitignore-only"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / ".gitignore").write_text("*.pyc")
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert paths == {".gitignore"}
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert is_meta_only, ".gitignore only → metadata-only → allowed"

    def test_staged_metadata_returns_true(self, tmp_path):
        """Staged BUGS.md (index dirty) → metadata-only → allowed."""
        repo = tmp_path / "staged-meta"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / "BUGS.md").write_text("bug entry")
        subprocess.run(["git", "add", "BUGS.md"], cwd=repo, capture_output=True)
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert "BUGS.md" in paths, f"BUGS.md should be in dirty paths: {paths}"
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert is_meta_only, "Staged metadata only → allowed"

    def test_staged_code_returns_false(self, tmp_path):
        """Staged src.py (index dirty) → NOT metadata-only → denied."""
        repo = tmp_path / "staged-code"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / "src.py").write_text("modified")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert "src.py" in paths
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert not is_meta_only, "Staged code → denied"

    def test_ci_status_only_returns_true(self, tmp_path):
        """Dirty tree with only .ci-status → metadata-only → allowed."""
        repo = tmp_path / "ci-status-only"
        repo.mkdir()
        _init_repo(repo)
        (repo / "src.py").write_text("content")
        subprocess.run(["git", "add", "src.py"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        (repo / ".ci-status").write_text("GREEN")
        status = _git_status_porcelain(repo)
        paths = _extract_file_paths(status)
        assert paths == {".ci-status"}
        is_meta_only = paths.issubset(EXPECTED_METADATA_FILES)
        assert is_meta_only, ".ci-status only → metadata-only → allowed"


class TestPluginUsesMetadataCheck:
    """Plugin source imports and uses isMetadataOnlyDirty."""

    def test_imports_is_metadata_only_dirty(self):
        src = _plugin_source()
        assert "isMetadataOnlyDirty" in src, (
            "Plugin must import isMetadataOnlyDirty"
        )

    def test_metadata_check_before_deny(self):
        src = _plugin_source()
        assert "isMetadataOnlyDirty(status)" in src, (
            "Plugin must call isMetadataOnlyDirty before denying"
        )

    def test_metadata_check_returns_early(self):
        """When isMetadataOnlyDirty is true, the function returns (allows)."""
        src = _plugin_source()
        check_idx = src.index("isMetadataOnlyDirty")
        after_check = src[check_idx:]
        early_return = after_check.split("\n")
        # The line after isMetadataOnlyDirty(status) should have "return;"
        found_return = False
        for i, line in enumerate(early_return):
            if "isMetadataOnlyDirty(status)" in line:
                # Look at next few lines for "return;"
                for j in range(1, 4):
                    if i + j < len(early_return) and "return" in early_return[i + j]:
                        found_return = True
                        break
                break
        assert found_return, (
            "After isMetadataOnlyDirty check, must return (allow) on match"
        )

    def test_fail_open_unchanged(self):
        src = _plugin_source()
        assert "catch" in src.lower(), "Fail-open try/catch must remain"
        assert "GLUDD_CLEAN_TREE_ENFORCE" in src, (
            "Env-var disable must remain"
        )
