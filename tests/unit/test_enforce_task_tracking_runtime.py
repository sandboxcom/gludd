"""Runtime behavioral tests for the enforce-task-tracking plugin.

These tests run the REAL plugin hook (compiled from TypeScript via esbuild)
and assert on actual permission decisions. This complements the 22 structural
tests in ``test_enforce_task_tracking_plugin.py``, which verify source-code
patterns but never invoke the hook function.

The behavioral tests live in the companion file
``.opencode/plugin/enforce-task-tracking.test.node.mjs``. This Python module
invokes that runner via ``node --test`` and asserts on the results.

Runner: ``node --test .opencode/plugin/enforce-task-tracking.test.node.mjs``
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_MJS = ROOT / ".opencode" / "plugin" / "enforce-task-tracking.test.node.mjs"


class TestTaskTrackingRuntime:
    """Run the .node.mjs behavioral test suite and assert all pass."""

    def test_mjs_test_file_exists(self):
        assert TEST_MJS.exists(), (
            "enforce-task-tracking.test.node.mjs must exist alongside the "
            "plugin — this is the runtime behavioral test suite"
        )

    def test_node_test_suite_passes(self):
        """The full node --test suite must exit 0 with all tests passing."""
        result = subprocess.run(
            ["node", "--test", str(TEST_MJS)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        message_parts = [
            "node --test enforce-task-tracking.test.node.mjs",
            f"exit code: {result.returncode}",
            f"stdout:\n{result.stdout[-3000:]}",
            f"stderr:\n{result.stderr[-2000:]}",
        ]
        message = "\n".join(message_parts)
        assert result.returncode == 0, message

    def test_all_expected_tests_present(self):
        """Verify the .mjs test file covers the test IDs from the plugin spec."""
        content = TEST_MJS.read_text()
        expected_tests = [
            "T1:",
            "T2:",
            "T3:",
            "T3b:",
            "T4:",
            "T4b:",
            "T4c:",
            "T5:",
            "T6:",
            "T7:",
            "T8:",
            "T9:",
            "T10:",
            "T11:",
            "T12a:",
            "T12b:",
            "T12c:",
            "T12d:",
            "T13:",
            "T14a:",
            "T14b:",
            "T14c:",
        ]
        missing = [t for t in expected_tests if t not in content]
        assert not missing, f"Missing test IDs in .mjs: {missing}"

    def test_assert_helpers_defined(self):
        """The .mjs must define assertDeny and assertAllow helpers."""
        content = TEST_MJS.read_text()
        assert "function assertDeny" in content, "must define assertDeny helper for consistent deny assertions"
        assert "function assertAllow" in content, "must define assertAllow helper for consistent allow assertions"
        assert "permissionDecision" in content, "must check permissionDecision on deny verdicts"

    def test_validates_bypass_paths(self):
        """Must test subagent guard (OPENCODE_SUBAGENT=1) and env-var disable."""
        content = TEST_MJS.read_text()
        assert "OPENCODE_SUBAGENT" in content, "must test subagent bypass (OPENCODE_SUBAGENT=1)"
        assert "GLUDD_TASK_TRACKING_ENFORCE" in content, "must test env-var disable (GLUDD_TASK_TRACKING_ENFORCE=0)"

    def test_validates_fail_open(self):
        """Must test that garbage/corrupt input does not throw or block."""
        content = TEST_MJS.read_text()
        assert "T12a" in content, "missing T12a: no-args fail-open"
        assert "T12b" in content, "missing T12b: empty-args fail-open"
        assert "T12c" in content, "missing T12c: null-tool fail-open"
        assert "T12d" in content, "missing T12d: undefined-filePath fail-open"

    def test_validates_core_deny_path(self):
        """Must test that stale TASKS.md mtime causes DENY."""
        content = TEST_MJS.read_text()
        assert "T4:" in content, "missing T4: core deny-path test"
        assert "assertDeny" in content, "must use assertDeny"


class TestTaskTrackingParity:
    """The .mjs test IDs and the structural test file must agree on spec."""

    STRUCT = ROOT / "tests" / "unit" / "test_enforce_task_tracking_plugin.py"

    def test_structural_tests_exist(self):
        assert self.STRUCT.exists(), "structural test_enforce_task_tracking_plugin.py must exist"

    def test_both_files_reference_same_state_file(self):
        struct_content = self.STRUCT.read_text()
        mjs_content = TEST_MJS.read_text()
        assert "gludd-task-tracking.json" in struct_content
        assert "gludd-task-tracking.json" in mjs_content, "both test files must reference /tmp/gludd-task-tracking.json"

    def test_both_files_reference_same_scope(self):
        struct_content = self.STRUCT.read_text()
        mjs_content = TEST_MJS.read_text()
        assert "src/general_ludd/" in struct_content
        assert "src/general_ludd" in mjs_content, "both test files must reference the src/general_ludd/ scope"
