"""TDD tests for the enforce-task-tracking hard task-registration guard plugin.

This is the mechanical enforcement layer for AGENTS.md's Task Self-Tracking
policy. The plugin blocks edit/write to ``src/general_ludd/**/*.py`` until
TASKS.md has been updated (mtime change detected).

Workflow enforced (the agent MUST follow this order):

1. Add an unchecked entry to TASKS.md describing the work
2. Save TASKS.md (this updates mtime, satisfying the guard)
3. Edit/write ``src/general_ludd/<module>.py`` — ALLOWED

Skip step 1 and step 3 is mechanically DENIED.

This test file was written BEFORE the plugin logic was verified (TDD). It will
fail until the plugin at ``.opencode/plugin/enforce-task-tracking.ts`` exists
and behaves correctly.
"""

import json
import re
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).parent.parent.parent
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-task-tracking.ts"
OPENCODE_JSON = ROOT / "opencode.json"


# --------------------------------------------------------------------------- #
# Structural: plugin file, registration, hook shape.
# --------------------------------------------------------------------------- #
class TestPluginStructure:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), (
            "enforce-task-tracking.ts must exist at .opencode/plugin/. This is "
            "the hard task-registration guard — without it, agents can write "
            "src/ implementation code without first updating TASKS.md."
        )

    def test_plugin_registered_in_opencode_json(self):
        assert OPENCODE_JSON.exists(), "opencode.json must exist"
        cfg = json.loads(OPENCODE_JSON.read_text())
        plugins = cfg.get("plugin", [])
        assert any("enforce-task-tracking" in p for p in plugins), (
            "enforce-task-tracking.ts must be registered in opencode.json plugins array"
        )

    def test_default_export_is_callable(self):
        content = PLUGIN_PATH.read_text()
        assert "export default" in content, "Plugin must have a default export"
        assert "satisfies Plugin" in content, "Plugin must satisfy the Plugin type"

    def test_hot_reload_imports_present(self):
        content = PLUGIN_PATH.read_text()
        assert "loadHotModule" in content, "Plugin must use hot-reload proxy pattern"
        assert "HotModule" in content, "Plugin must import HotModule type"
        assert "defaultImpl" in content, "Plugin must define compiled-in defaultImpl"

    def test_tool_execute_before_hook_registered(self):
        content = PLUGIN_PATH.read_text()
        assert '"tool.execute.before"' in content, "Plugin must register tool.execute.before hook"

    def test_imports_shared_helpers(self):
        content = PLUGIN_PATH.read_text()
        assert "isSubagent" in content, "Plugin must import isSubagent guard"
        assert "reportAlive" in content, "Plugin must import reportAlive helper"
        assert "getProjectRoot" in content, "Plugin must import getProjectRoot helper"
        assert "readJsonFile" in content, "Plugin must import readJsonFile helper"
        assert "writeJsonFile" in content, "Plugin must import writeJsonFile helper"


# --------------------------------------------------------------------------- #
# Guard enforcement patterns.
# --------------------------------------------------------------------------- #
class TestGuardPatterns:
    def test_subagent_guard_present(self):
        content = PLUGIN_PATH.read_text()
        assert "isSubagent()" in content, (
            "Plugin must check isSubagent() at top of every hook to skip enforcement in subagent context"
        )

    def test_env_var_disable_path(self):
        content = PLUGIN_PATH.read_text()
        assert "GLUDD_TASK_TRACKING_ENFORCE" in content, "Plugin must have GLUDD_TASK_TRACKING_ENFORCE env var check"
        assert 'GLUDD_TASK_TRACKING_ENFORCE === "0"' in content or 'GLUDD_TASK_TRACKING_ENFORCE !== "0"' in content, (
            "Plugin must check for env-var disable path"
        )

    def test_fail_open_pattern(self):
        content = PLUGIN_PATH.read_text()
        assert "catch" in content, "Plugin must have catch blocks for fail-open"
        assert "return { allow: true }" in content or "return" in content, "Plugin must fail-open on error"

    def test_tool_filter_edit_write(self):
        content = PLUGIN_PATH.read_text()
        assert 'input?.tool !== "edit"' in content or 'tool !== "edit"' in content, "Plugin must filter for edit tool"
        assert 'input?.tool !== "write"' in content or 'tool !== "write"' in content, (
            "Plugin must filter for write tool"
        )

    def test_permission_decision_deny(self):
        content = PLUGIN_PATH.read_text()
        assert "permissionDecision" in content, "Plugin must return permissionDecision on deny"
        assert '"deny"' in content, "Plugin must use permissionDecision: deny"


# --------------------------------------------------------------------------- #
# Spec: state file, constants, matching patterns.
# --------------------------------------------------------------------------- #
class TestSpecCompliance:
    def test_state_file_constant(self):
        content = PLUGIN_PATH.read_text()
        assert "/tmp/gludd-task-tracking.json" in content, "Plugin must use /tmp/gludd-task-tracking.json as state file"

    def test_src_prefix_scope(self):
        content = PLUGIN_PATH.read_text()
        assert "src/general_ludd/" in content, "Plugin must scope enforcement to src/general_ludd/ files only"

    def test_deny_message_defined(self):
        content = PLUGIN_PATH.read_text()
        assert "DENY_MESSAGE" in content, "Plugin must define DENY_MESSAGE constant for guidance text"
        assert "TASKS.md" in content, "Deny message must reference TASKS.md"

    def test_tasks_md_path_construction(self):
        content = PLUGIN_PATH.read_text()
        assert "TASKS.md" in content, "Plugin must reference TASKS.md"
        assert "getProjectRoot" in content, "Plugin must use getProjectRoot to locate TASKS.md"

    def test_mtime_comparison_logic(self):
        content = PLUGIN_PATH.read_text()
        assert "mtime" in content or "statSync" in content, "Plugin must use mtime/statSync to detect TASKS.md updates"

    def test_implementation_file_filter(self):
        content = PLUGIN_PATH.read_text()
        assert "isImplementationFile" in content or "endsWith" in content, (
            "Plugin must filter for implementation files only"
        )

    def test_tests_directory_exempt(self):
        content = PLUGIN_PATH.read_text()
        assert "tests/" in content, "Plugin must exempt files under tests/ from enforcement"

    def test_opencode_directory_exempt(self):
        content = PLUGIN_PATH.read_text()
        assert ".opencode/" in content, "Plugin must exempt files under .opencode/ from enforcement"

    def test_missing_tasks_md_no_op(self):
        content = PLUGIN_PATH.read_text()
        assert "existsSync" in content, "Plugin must check TASKS.md existence before enforcing"


# --------------------------------------------------------------------------- #
# No lint-suppression comments in the plugin itself.
# --------------------------------------------------------------------------- #
class TestPluginSelfCleanliness:
    SUPPRESSION_PATTERNS: ClassVar[list[str]] = [
        r"#\s*noqa",
        r"#\s*type:\s*ignore",
        r"#\s*pylint:",
        r"#\s*fmt:\s*(?:off|skip|on)",
        r"#\s*isort:\s*skip",
    ]

    def test_no_suppression_comments_in_plugin(self):
        content = PLUGIN_PATH.read_text()
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            for pattern in self.SUPPRESSION_PATTERNS:
                assert not re.search(pattern, stripped), (
                    f"Suppression comment found at line {i}: {stripped!r}. "
                    "Fix the underlying issue; never silence the linter."
                )

    def test_no_require_calls(self):
        content = PLUGIN_PATH.read_text()
        assert "require(" not in content or "createRequire" in content, (
            "Plugin must use ES module imports, not require() calls"
        )
