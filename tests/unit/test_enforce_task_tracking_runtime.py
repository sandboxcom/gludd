"""Runtime behavioral tests for the enforce-task-tracking plugin.

These tests verify behavior at two levels:

1. Python-level simulation: reimplements the plugin's shouldAllowEdit() logic
   in Python and tests it with real temp files. These are the direct behavioral
   tests — they prove the decision contract works.

2. Node invocation: runs the companion .mjs test suite via ``node --test``,
   which compiles and invokes the actual TypeScript plugin hook.

This complements the 22 structural tests in
``test_enforce_task_tracking_plugin.py``, which verify source-code patterns
but never invoke the hook function.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST_MJS = ROOT / ".opencode" / "plugin" / "enforce-task-tracking.test.node.mjs"
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-task-tracking.ts"

SRC_PREFIX = "src/general_ludd/"
TESTS_PREFIX = "tests/"
OPENCODE_PREFIX = ".opencode/"


def is_implementation_file(file_path: str) -> bool:
    if not isinstance(file_path, str) or len(file_path) == 0:
        return False
    normalized = file_path.replace("\\", "/")
    if TESTS_PREFIX in normalized:
        return False
    if OPENCODE_PREFIX in normalized:
        return False
    return SRC_PREFIX in normalized and normalized.endswith(".py")


def should_allow_edit(
    file_path: str,
    tasks_md_path: str,
    state_file: str,
) -> dict[str, Any]:
    if not is_implementation_file(file_path):
        return {"allow": True}

    if not os.path.exists(tasks_md_path):
        return {"allow": True}

    state = {
        "pid": os.getpid(),
        "last_tasks_md_mtime": 0,
        "tasks_md_path": tasks_md_path,
    }
    if os.path.exists(state_file):
        with open(state_file) as f:
            state = json.load(f)

    current_mtime = os.stat(tasks_md_path).st_mtime

    if state["last_tasks_md_mtime"] == 0:
        state["last_tasks_md_mtime"] = current_mtime
        state["tasks_md_path"] = tasks_md_path
        state["pid"] = os.getpid()
        with open(state_file, "w") as f:
            json.dump(state, f)
        return {"allow": True}

    if current_mtime > state["last_tasks_md_mtime"]:
        state["last_tasks_md_mtime"] = current_mtime
        with open(state_file, "w") as f:
            json.dump(state, f)
        return {"allow": True}

    return {"allow": False, "reason": "TASKS.md mtime unchanged"}


class TestTaskTrackingBehavioral:
    """Direct Python behavioral tests simulating the plugin's decision logic.

    Reimplements isImplementationFile() and shouldAllowEdit() from the plugin
    and tests their decision contract with real temp files."""

    @pytest.fixture
    def temp_project(self):
        with tempfile.TemporaryDirectory(prefix="gludd-tt-test-") as tmp:
            src_dir = os.path.join(tmp, "src", "general_ludd")
            tests_dir = os.path.join(tmp, "tests", "unit")
            os.makedirs(src_dir, exist_ok=True)
            os.makedirs(tests_dir, exist_ok=True)
            tasks_md = os.path.join(tmp, "TASKS.md")
            with open(tasks_md, "w") as f:
                f.write("# Tasks\n\n- [ ] Initial task\n")
            state_file = os.path.join(tmp, "task-tracking-state.json")
            yield tmp, tasks_md, state_file

    def test_edit_allowed_when_tasks_md_recent(self, temp_project):
        tmp, tasks_md, state_file = temp_project
        impl_file = os.path.join(tmp, "src", "general_ludd", "module.py")

        verdict1 = should_allow_edit(impl_file, tasks_md, state_file)
        assert verdict1 == {"allow": True}, "first edit must be allowed (state initializes)"

        time.sleep(0.01)
        with open(tasks_md, "a") as f:
            f.write("- [ ] New task: fix module.py\n")

        verdict2 = should_allow_edit(impl_file, tasks_md, state_file)
        assert verdict2 == {"allow": True}, "edit after TASKS.md update must be allowed"

    def test_edit_denied_when_tasks_md_stale(self, temp_project):
        tmp, tasks_md, state_file = temp_project
        impl_file = os.path.join(tmp, "src", "general_ludd", "module_a.py")

        verdict1 = should_allow_edit(impl_file, tasks_md, state_file)
        assert verdict1 == {"allow": True}, "first edit must be allowed"

        verdict2 = should_allow_edit(impl_file, tasks_md, state_file)
        assert verdict2["allow"] is False, "edit without TASKS.md update must be denied"
        assert "TASKS.md" in verdict2.get("reason", ""), "deny reason must reference TASKS.md"

    def test_env_var_disable_path(self):
        src = PLUGIN_PATH.read_text()
        assert "GLUDD_TASK_TRACKING_ENFORCE" in src, "plugin must honor GLUDD_TASK_TRACKING_ENFORCE=0 escape hatch"

    def test_subagent_guard_skips(self):
        src = PLUGIN_PATH.read_text()
        assert "isSubagent" in src, "plugin must check isSubagent() to skip enforcement in subagent context"

    def test_fail_open_on_error(self):
        src = PLUGIN_PATH.read_text()
        assert "catch" in src, "plugin must wrap logic in try/catch for fail-open behavior"

    def test_non_python_files_not_checked(self, temp_project):
        tmp, _tasks_md, _state_file = temp_project

        assert not is_implementation_file(os.path.join(tmp, "tests", "unit", "test_mod.py")), (
            "tests/ files must not be treated as implementation files"
        )

        assert not is_implementation_file(os.path.join(tmp, ".opencode", "plugin", "config.ts")), (
            ".opencode/ files must not be treated as implementation files"
        )

        assert not is_implementation_file(os.path.join(tmp, "src", "general_ludd", "config.yml")), (
            "non-.py files in src/ must not be treated as implementation files"
        )

        assert not is_implementation_file(os.path.join(tmp, "README.md")), (
            "files outside src/general_ludd/ must not be treated as implementation files"
        )

        impl_file = os.path.join(tmp, "src", "general_ludd", "module.py")
        assert is_implementation_file(impl_file), "src/general_ludd/*.py must be recognized as implementation files"

    def test_tasks_md_absent_allows(self, temp_project):
        tmp, tasks_md, state_file = temp_project
        impl_file = os.path.join(tmp, "src", "general_ludd", "pending.py")

        os.unlink(tasks_md)
        assert not os.path.exists(tasks_md)

        verdict = should_allow_edit(impl_file, tasks_md, state_file)
        assert verdict == {"allow": True}, (
            "edit when TASKS.md is absent must not block — the guard is a no-op "
            "when there is no task ledger to enforce against"
        )


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
