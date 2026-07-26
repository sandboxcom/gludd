"""Structural tests for the enforce-task-tracking plugin and check_task_integrity script.

Verifies:
  - Plugin file exists and exports required constants/hooks
  - Plugin is registered in opencode.json
  - Plugin follows Node v26 compatibility rules
  - Plugin uses shared.ts helpers (isSubagent, reportAlive, getProjectRoot)
  - Plugin handles: unchecked detection, read-allow, task-file-allow,
    source-deny on zero unchecked, corruption detection, fail-open
  - check_task_integrity.py exists and runs as a standalone script
  - Makefile has check-task-integrity target wired into gate
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-task-tracking.ts"
OPECODE_JSON = ROOT / "opencode.json"
MAKEFILE_PATH = ROOT / "Makefile"
SCRIPT_PATH = ROOT / "scripts" / "check_task_integrity.py"


def read_plugin_src() -> str:
    assert PLUGIN_PATH.exists(), "Plugin file must exist before running these tests"
    return PLUGIN_PATH.read_text()


class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-task-tracking.ts must exist at .opencode/plugin/"
        )

    def test_plugin_registered_in_opencode_json(self):
        cfg = json.loads(OPECODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-task-tracking" in p for p in plugins), (
            "enforce-task-tracking.ts must be registered in opencode.json plugin[] array"
        )

    def test_exports_task_tracking_file_constant(self):
        src = read_plugin_src()
        assert "TASK_TRACKING_FILE" in src, (
            "Plugin must export TASK_TRACKING_FILE constant"
        )
        assert 'export const TASK_TRACKING_FILE_EXPORT' in src or \
               'TASK_TRACKING_FILE = "TASKS.md"' in src or \
               "TASK_TRACKING_FILE = 'TASKS.md'" in src, (
            "Plugin must export TASK_TRACKING_FILE with value 'TASKS.md'"
        )

    def test_tool_execute_before_hook_registered(self):
        src = read_plugin_src()
        assert '"tool.execute.before"' in src, (
            "Plugin must register a tool.execute.before hook"
        )

    def test_text_complete_hook_registered(self):
        src = read_plugin_src()
        assert '"experimental.text.complete"' in src or \
               '"text.complete"' in src, (
            "Plugin must register a text.complete hook"
        )

    def test_imports_from_lib_shared(self):
        src = read_plugin_src()
        assert 'isSubagent' in src, "Plugin must import isSubagent from shared.ts"
        assert 'reportAlive' in src, "Plugin must import reportAlive from shared.ts"

    def test_imports_from_lib_hot_reload(self):
        src = read_plugin_src()
        assert 'loadHotModule' in src, "Plugin must import loadHotModule from hot_reload.ts"
        assert 'HotModule' in src, "Plugin must import HotModule type"

    def test_has_subagent_guard(self):
        src = read_plugin_src()
        assert 'if (isSubagent()) return' in src, (
            "Plugin hook functions must have isSubagent() guard"
        )

    def test_has_enable_env_var(self):
        src = read_plugin_src()
        assert 'GLUDD_TASK_TRACKING_ENFORCE' in src, (
            "Plugin must respect GLUDD_TASK_TRACKING_ENFORCE env var"
        )

    def test_fail_open_pattern(self):
        src = read_plugin_src()
        assert '} catch {' in src or '} catch (' in src, (
            "Plugin must fail-open: every critical code path wrapped in try-catch"
        )

    def test_has_default_impl(self):
        src = read_plugin_src()
        assert 'defaultImpl' in src, "Plugin must define defaultImpl fallback"

    def test_satisfies_plugin_interface(self):
        src = read_plugin_src()
        assert 'satisfies Plugin' in src, (
            "Plugin must use 'satisfies Plugin' type annotation"
        )

    def test_node_v26_compatible(self):
        """All .opencode/plugin/*.ts must pass Node v26 --experimental-strip-types."""
        src = read_plugin_src()
        assert "catch { try" not in src, (
            "Forbidden pattern: try inside catch block (Node v26 parse error)"
        )
        assert re.search(r"catch\s*\([^)]*:.*\)\s*\{", src) is None, (
            "Forbidden pattern: typed catch variable (Node v26 parse error)"
        )

    def test_task_id_parser_avoids_unsupported_string_matchall(self):
        """The embedded OpenCode runtime does not expose String.matchAll."""
        src = read_plugin_src()
        assert ".matchAll(" not in src, (
            "Task ID parsing must use a runtime-compatible regex iteration"
        )

    def test_read_tools_allowed_unconditionally(self):
        src = read_plugin_src()
        assert 'isReadTool' in src or (
            '"read"' in src and '"grep"' in src and '"glob"' in src
        ), "Plugin must allow read/grep/glob tools unconditionally"

    def test_task_file_writes_allowed(self):
        src = read_plugin_src()
        assert 'isTaskFile' in src, (
            "Plugin must detect writes to TASKS.md and allow them"
        )

    def test_source_deny_on_zero_unchecked(self):
        src = read_plugin_src()
        assert 'NO TASK ENTRY' in src, (
            "Plugin must deny source edits when zero unchecked items exist"
        )
        assert 'permissionDecision' in src, (
            "Plugin must use permissionDecision: deny pattern"
        )

    def test_corruption_detection(self):
        src = read_plugin_src()
        assert 'CORRUPTION' in src or 'corruption' in src.lower(), (
            "Plugin must detect TASKS.md corruption attempts"
        )

    def test_uses_getProjectRoot(self):
        src = read_plugin_src()
        assert 'getProjectRoot' in src, (
            "Plugin must use getProjectRoot() for workspace-relative paths"
        )

    def test_hard_registration_guard_is_present(self):
        """Source writes must prove a concrete TASKS.md registration."""
        src = read_plugin_src()
        assert "isRegisteredTaskPath" in src, (
            "Plugin must verify the edited path against TASKS.md registrations"
        )
        assert "TASK REGISTRATION REQUIRED" in src, (
            "Unregistered source edits must be denied with an actionable message"
        )

    def test_registration_guard_accepts_declared_task_id(self):
        """Delegated writes may carry a declared task ID as registration evidence."""
        src = read_plugin_src()
        assert "declaredTaskIds" in src, (
            "Plugin must parse declared task IDs from TASKS.md"
        )
        assert "taskId" in src or "task_id" in src, (
            "Plugin must inspect task ID metadata on write requests"
        )

    def test_registration_guard_fails_closed_for_missing_path(self):
        """A write without a target path must not bypass registration checks."""
        src = read_plugin_src()
        assert "WRITE TARGET PATH MISSING" in src, (
            "Pathless write operations must be denied by the registration guard"
        )


class TestCheckTaskIntegrityScript:
    def test_script_exists(self):
        assert SCRIPT_PATH.exists(), (
            "scripts/check_task_integrity.py must exist"
        )

    def test_script_is_executable(self):
        import stat
        mode = SCRIPT_PATH.stat().st_mode
        assert mode & stat.S_IXUSR, "Script must be user-executable"

    def test_script_runs_without_crashing(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True, text=True, timeout=10,
            cwd=str(ROOT),
        )
        assert result.returncode in (0, 1), (
            f"Script must exit 0 or 1, got {result.returncode}. stderr: {result.stderr}"
        )

    def test_script_prints_violations_on_stdout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True, text=True, timeout=10,
            cwd=str(ROOT),
        )
        out = result.stdout + result.stderr
        assert "TASKS.md integrity check" in out, (
            "Script must print integrity check header to output"
        )


class TestMakefileIntegration:
    def test_check_task_integrity_target_exists(self):
        content = MAKEFILE_PATH.read_text()
        assert "check-task-integrity:" in content or "check-task-integrity :" in content, (
            "Makefile must have a check-task-integrity target"
        )

    def test_wired_into_gate(self):
        content = MAKEFILE_PATH.read_text()
        gate_line = ""
        for line in content.split("\n"):
            # Match the actual target declaration, not help text such as
            # `@echo "  gate-all ..."` that merely contains `gate:`.
            if re.match(r"^gate\s*:", line):
                gate_line = line.strip()
                break
        assert gate_line, "Could not find gate: target in Makefile"
        assert "check-task-integrity" in gate_line, (
            "check-task-integrity must be a prerequisite of gate: target"
        )
