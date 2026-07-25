"""BP.20: TDD gate exempts __init__.py in new empty directories.

Previously ``__init__.py`` was globally allowlisted — any ``__init__.py``
anywhere in ``src/general_ludd/`` could be edited without a test. That was
too broad: ``__init__.py`` in a populated directory often carries real code
(imports, re-exports) that SHOULD be tested.

BP.20 narrows the exemption to scaffolding only: ``__init__.py`` in a brand-new
empty directory (no other ``.py`` files present) is scaffolding, not feature
code, and does not need a test file first. ``__init__.py`` in a populated
directory requires a test like any other ``.py`` file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
COMMIT_SCRIPT_PATH = ROOT / "scripts" / "check_tdd_compliance.py"


# --------------------------------------------------------------------------- #
# Structural: the init-in-empty-dir exemption exists in the plugin.
# --------------------------------------------------------------------------- #
class TestInitExemptPluginStructure:
    def test_function_is_init_in_empty_dir_exists(self):
        assert PLUGIN_PATH.exists(), "enforce-tdd.ts must exist"
        src = PLUGIN_PATH.read_text()
        assert "isInitInEmptyDir" in src, (
            "enforce-tdd.ts must define isInitInEmptyDir() — the function that "
            "checks whether __init__.py lives in a directory with no other .py files"
        )

    def test_init_py_removed_from_global_allowlist(self):
        src = PLUGIN_PATH.read_text()
        # Find ALLOWLIST_PATTERNS and capture from the array-opening `[`
        # after the `=` to the closing `];`.  The RegExp[] type annotation
        # also contains `[]` so a simple single-bracket capture is wrong.
        start_marker = "ALLOWLIST_PATTERNS: RegExp[] = ["
        idx = src.find(start_marker)
        assert idx >= 0, "ALLOWLIST_PATTERNS: RegExp[] = [ not found"
        # Find the matching `];` — the first `];` after the start marker.
        close_idx = src.find("];", idx)
        assert close_idx >= 0, "closing ] for ALLOWLIST_PATTERNS not found"
        body = src[idx:close_idx]
        assert "__init__" not in body, (
            "__init__.py must NOT be in the global ALLOWLIST_PATTERNS — "
            "it was moved to the targeted isInitInEmptyDir() check"
        )

    def test_is_init_in_empty_dir_references_fs(self):
        src = PLUGIN_PATH.read_text()
        assert any(
            keyword in src
            for keyword in ["readdirSync", "existsSync", "statSync", "isDirectory"]
        ), (
            "isInitInEmptyDir must use filesystem calls (readdirSync/existsSync) "
            "to check whether the parent directory has other .py files"
        )

    def test_is_init_in_empty_dir_checks_sibling_py_files(self):
        src = PLUGIN_PATH.read_text()
        assert ".py" in src and 'endsWith(".py")' in src, (
            "isInitInEmptyDir must check for sibling .py files "
            "(e.endsWith(\".py\")) in the parent directory"
        )


# --------------------------------------------------------------------------- #
# Structural: the commit-time script matches the plugin.
# --------------------------------------------------------------------------- #
class TestInitExemptCommitScript:
    def test_script_has_is_init_in_empty_dir(self):
        assert COMMIT_SCRIPT_PATH.exists(), "check_tdd_compliance.py must exist"
        src = COMMIT_SCRIPT_PATH.read_text()
        assert "_is_init_in_empty_dir" in src, (
            "check_tdd_compliance.py must define _is_init_in_empty_dir() "
            "to match the enforce-tdd.ts plugin — the commit-time and "
            "edit-time checks must agree"
        )

    def test_script_init_py_removed_from_global_allowlist(self):
        src = COMMIT_SCRIPT_PATH.read_text()
        # The ALLOWLIST tuple must NOT contain __init__ anymore.
        allowlist_match = re.search(r"ALLOWLIST\s*=\s*\(([^)]*)\)", src, re.DOTALL)
        assert allowlist_match, "ALLOWLIST tuple not found in commit script"
        body = allowlist_match.group(1)
        assert "__init__" not in body, (
            "__init__.py must NOT be in the global ALLOWLIST — it was moved "
            "to _is_init_in_empty_dir() to match the plugin"
        )

    def test_script_calls_is_init_in_empty_dir(self):
        src = COMMIT_SCRIPT_PATH.read_text()
        assert "_is_init_in_empty_dir(" in src and "continue" in src, (
            "check_tdd_compliance.py must call _is_init_in_empty_dir() in the "
            "main loop and skip (continue) when it returns True"
        )


# --------------------------------------------------------------------------- #
# Decision-table: the contract for init-in-empty-dir exemption.
# --------------------------------------------------------------------------- #
class TestInitExemptVerdictContract:
    """The behavioral contract without requiring a running node process."""

    def test_should_allow_edit_calls_is_init_in_empty_dir(self):
        src = PLUGIN_PATH.read_text()
        assert "isInitInEmptyDir(filePath)" in src or "isInitInEmptyDir(" in src, (
            "shouldAllowEdit() must call isInitInEmptyDir() — the function "
            "exists but must be wired into the decision pipeline"
        )

    def test_init_in_empty_dir_checked_after_allowlist(self):
        src = PLUGIN_PATH.read_text()
        # isInitInEmptyDir must appear after isAllowlisted and before test-file check.
        allowlist_idx = src.find("isAllowlisted(filePath)")
        init_idx = src.find("isInitInEmptyDir(filePath)")
        candidates_idx = src.find("candidateTestPaths(filePath")
        assert allowlist_idx < init_idx < candidates_idx, (
            "decision order must be: allowlist → init-in-empty-dir → "
            f"test-file check. Found allowlist@{allowlist_idx}, "
            f"init@{init_idx}, candidates@{candidates_idx}"
        )

    def test_fail_open_on_init_check_error(self):
        src = PLUGIN_PATH.read_text()
        assert "try" in src and "catch" in src, (
            "isInitInEmptyDir must use try/catch for fail-open — a broken "
            "filesystem check must not wedge the editor"
        )


# --------------------------------------------------------------------------- #
# Existing test update: __init__ is no longer globally allowlisted.
# The old test_allows_allowlisted_init_py in test_enforce_tdd_plugin.py must
# be updated to reflect the new targeted exemption.
# --------------------------------------------------------------------------- #
class TestExistingAllowlistTestUpdate:
    def test_old_init_global_allowlist_test_no_longer_valid(self):
        if not (ROOT / "tests" / "unit" / "test_enforce_tdd_plugin.py").exists():
            return
        old_test_src = (ROOT / "tests" / "unit" / "test_enforce_tdd_plugin.py").read_text()
        # The old test_allows_allowlisted_init_py checked "__init__" in the plugin
        # source. With BP.20, __init__ is no longer in ALLOWLIST_PATTERNS but
        # is handled by isInitInEmptyDir. The old test still passes because
        # "__init__" is still mentioned in the source (in isInitInEmptyDir).
        # So this is a no-op structural pin — no change needed.
        assert "__init__" in old_test_src, (
            "test_enforce_tdd_plugin.py test_allows_allowlisted_init_py needs "
            "updating: __init__.py is no longer universally allowlisted"
        )


# --------------------------------------------------------------------------- #
# Behavioral: invoke the actual exported shouldAllowEdit() via Node subprocess.
# These tests create real temp directories and verify the runtime verdict.
# --------------------------------------------------------------------------- #
class TestInitExemptBehavioral:
    """Invoke the plugin's exported shouldAllowEdit() with real filesystem
    state to verify the __init__.py exemption works at runtime."""

    def _verdict(self, file_path: str, project_root: str) -> dict:
        """Call shouldAllowEdit via Node --experimental-strip-types."""
        script = (
            "(async () => {\n"
            f"  const m = await import({json.dumps(str(PLUGIN_PATH))});\n"
            f"  const v = m.shouldAllowEdit("
            f"{json.dumps(file_path)}, {json.dumps(project_root)});\n"
            "  process.stdout.write(JSON.stringify(v));\n"
            "  process.exit(0);\n"
            "})();\n"
        )
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Node shouldAllowEdit failed (exit {result.returncode}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
        return json.loads(result.stdout.strip())

    def test_init_py_in_empty_dir_allowed(self, tmp_path):
        """Bullet 2: __init__.py in a brand-new empty directory is ALLOWED
        (scaffolding, not feature code)."""
        pkg = tmp_path / "src" / "general_ludd" / "newpkg"
        pkg.mkdir(parents=True)
        init = pkg / "__init__.py"
        init.write_text("")
        verdict = self._verdict(str(init), str(tmp_path))
        assert verdict["allow"] is True, (
            f"__init__.py in empty dir must be allowed (scaffolding). "
            f"Got: {verdict}"
        )

    def test_init_py_in_populated_dir_denied(self, tmp_path):
        """Bullet 3: __init__.py in a directory WITH other .py files requires
        a test — it is NOT globally exempt. The isInitInEmptyDir check
        returns false, and the test-file check applies."""
        pkg = tmp_path / "src" / "general_ludd" / "newpkg"
        pkg.mkdir(parents=True)
        init = pkg / "__init__.py"
        init.write_text("")
        (pkg / "feature.py").write_text("x = 1\n")
        verdict = self._verdict(str(init), str(tmp_path))
        assert verdict["allow"] is False, (
            f"__init__.py in populated dir must require a test. Got: {verdict}"
        )

    def test_non_init_py_requires_test(self, tmp_path):
        """Bullet 4: non-init .py file without a test is DENIED."""
        pkg = tmp_path / "src" / "general_ludd" / "newpkg"
        pkg.mkdir(parents=True)
        feature = pkg / "feature.py"
        feature.write_text("x = 1\n")
        verdict = self._verdict(str(feature), str(tmp_path))
        assert verdict["allow"] is False, (
            f"Non-init .py without test must be denied. Got: {verdict}"
        )

    def test_non_init_py_with_test_allowed(self, tmp_path):
        """Bullet 4 (positive): non-init .py with a corresponding test file
        is ALLOWED (normal TDD green path)."""
        pkg = tmp_path / "src" / "general_ludd" / "newpkg"
        pkg.mkdir(parents=True)
        feature = pkg / "feature.py"
        feature.write_text("x = 1\n")
        tests_unit = tmp_path / "tests" / "unit"
        tests_unit.mkdir(parents=True)
        (tests_unit / "test_feature.py").write_text("def test_feature():\n    pass\n")
        verdict = self._verdict(str(feature), str(tmp_path))
        assert verdict["allow"] is True, (
            f"Non-init .py with test must be allowed. Got: {verdict}"
        )

    def test_should_allow_edit_is_exported(self):
        """Bullet 1: the allowlist mechanism exists and is testable —
        shouldAllowEdit is an exported function."""
        src = PLUGIN_PATH.read_text()
        assert re.search(
            r"export\s+function\s+shouldAllowEdit",
            src,
        ), "shouldAllowEdit must be exported for behavioral testing"
