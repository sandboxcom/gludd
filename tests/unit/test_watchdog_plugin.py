"""Behavioral-invariant tests for watchdog.ts.

The watchdog plugin lives at .opencode/plugins/watchdog.ts (the only plugin in
the `plugins/` dir — all others are in `plugin/`). As of 2026-07-19 it is a
minimal stub: all event hooks were removed because opencode 1.17.9 crashes on
unknown hook types. It imports reportAlive from shared.ts (E.5 refactor) and
calls it at load time to prove the plugin is alive. The actual _reportAlive
logic lives in .opencode/lib/shared.ts.

Test categories:
  1. File existence + opencode.json registration
  2. Key constants and env-var overrides
  3. Plugin shape (export type, hook registry — stub)
  4. SUBAGENT guard (intentionally absent for this plugin)
  5. Import correctness (shared.ts refactor)
  6. No leftover event handler / session lifecycle code
  7. reportAlive stub mechanics (import + call)
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG_PATH = ROOT / ".opencode" / "plugins" / "watchdog.ts"
OPENCODE_JSON_PATH = ROOT / "opencode.json"


def _src(path: Path = WATCHDOG_PATH) -> str:
    assert path.exists(), f"Plugin missing at {path}"
    return path.read_text()


# ---------------------------------------------------------------------------
# 1. File existence + opencode.json registration
# ---------------------------------------------------------------------------


class TestWatchdogFileExists:
    def test_plugin_file_exists(self):
        assert WATCHDOG_PATH.exists(), f"watchdog.ts not found at {WATCHDOG_PATH}"

    def test_plugin_not_in_main_plugin_dir(self):
        """watchdog.ts is in .opencode/plugins/, not .opencode/plugin/."""
        main_plugin_dir = ROOT / ".opencode" / "plugin" / "watchdog.ts"
        assert not main_plugin_dir.exists(), (
            "watchdog.ts should be in .opencode/plugins/ not .opencode/plugin/"
        )

    def test_registered_in_opencode_json(self):
        content = OPENCODE_JSON_PATH.read_text()
        assert "watchdog.ts" in content, (
            "watchdog.ts must be listed in opencode.json plugin array"
        )

    def test_registration_path_matches_plugins_dir(self):
        content = OPENCODE_JSON_PATH.read_text()
        assert "./.opencode/plugins/watchdog.ts" in content, (
            "watchdog.ts registration must use the ./plugins/ (not ./plugin/) path"
        )


# ---------------------------------------------------------------------------
# 2. Key constants and env-var overrides
# ---------------------------------------------------------------------------


class TestWatchdogConstants:
    def test_pid_file_constant_exists(self):
        src = _src()
        assert re.search(r"const\s+PID_FILE\s*=", src), "PID_FILE constant missing"

    def test_pid_file_default(self):
        src = _src()
        assert ".gate-logs/watchdog.pid" in src, (
            "PID_FILE must default to .gate-logs/watchdog.pid"
        )

    def test_pid_file_env_override(self):
        src = _src()
        assert "GLUDD_WATCHDOG_PID_FILE" in src, (
            "PID_FILE must be overridable via GLUDD_WATCHDOG_PID_FILE"
        )

    def test_task_pid_file_constant_exists(self):
        src = _src()
        assert "TASK_PID_FILE" in src, "TASK_PID_FILE constant missing"

    def test_task_pid_file_value(self):
        src = _src()
        assert ".gate-logs/task-watchdog.pid" in src, (
            "TASK_PID_FILE must point to .gate-logs/task-watchdog.pid"
        )

    def test_alive_path_in_shared_import(self):
        """Post E.5 refactor: reportAlive lives in shared.ts, not watchdog.ts."""
        src = _src()
        assert "reportAlive" in src, (
            "watchdog.ts must import reportAlive from ../lib/shared.ts"
        )


# ---------------------------------------------------------------------------
# 3. Plugin shape (stub — no event hooks, no async, no tool hooks)
# ---------------------------------------------------------------------------


class TestPluginShape:
    def test_export_is_default_factory(self):
        src = _src()
        assert "export default" in src, "must have default export"

    def test_satisfies_plugin_type(self):
        src = _src()
        assert "satisfies Plugin" in src, (
            "must use 'satisfies Plugin' type assertion"
        )

    def test_factory_receives_api_proxy(self):
        src = _src()
        assert "_api" in src, (
            "factory must receive API proxy (stub, no $ shell needed)"
        )

    def test_no_tool_hooks(self):
        src = _src()
        assert '"tool.execute.before"' not in src, (
            "must NOT register tool hooks — watchdog is not an enforcer"
        )
        assert '"tool.execute.after"' not in src, (
            "must NOT register tool.execute.after"
        )


# ---------------------------------------------------------------------------
# 4. SUBAGENT guard (intentionally absent)
# ---------------------------------------------------------------------------


class TestSubagentGuardIntentionallyAbsent:
    def test_no_opensubagent_guard(self):
        """watchdog.ts intentionally lacks OPENCODE_SUBAGENT guard.

        Unlike enforcement plugins that should skip execution inside subagents
        (to avoid their enforcement logic firing in sub-agent contexts), the
        watchdog MUST run everywhere — main agent AND subagent contexts — so
        the heartbeat liveness check never goes stale. An absent watchdog in
        a subagent session would look like a dead plugin.
        """
        src = _src()
        assert "OPENCODE_SUBAGENT" not in src, (
            "watchdog.ts must NOT have an OPENCODE_SUBAGENT guard — it needs "
            "to run in all contexts (main + subagents) to keep the heartbeat alive"
        )

    def test_no_subagent_skip_mechanism(self):
        src = _src()
        assert "SUBAGENT" not in src, (
            "watchdog.ts must not reference SUBAGENT at all — no skip mechanism exists"
        )


# ---------------------------------------------------------------------------
# 5. Imports — shared.ts refactor correctness
# ---------------------------------------------------------------------------


class TestImports:
    def test_imports_plugin_type(self):
        src = _src()
        assert '@opencode-ai/plugin' in src, (
            "must import Plugin type from @opencode-ai/plugin"
        )

    def test_imports_node_fs(self):
        src = _src()
        assert re.search(r'from\s+"node:fs"', src) or "'node:fs'" in src, (
            "must import node:fs for file operations"
        )

    def test_imports_report_alive_from_shared(self):
        src = _src()
        assert 'import { reportAlive } from "../lib/shared.ts"' in src, (
            "must import reportAlive from ../lib/shared.ts (E.5 refactor)"
        )

    def test_has_exactly_3_or_4_imports(self):
        src = _src()
        import_lines = [
            line for line in src.split("\n")
            if line.strip().startswith("import ")
        ]
        assert len(import_lines) in (3, 4), (
            f"expected 3 or 4 imports (Plugin + node:fs [+ node:path] + shared/reportAlive), "
            f"found {len(import_lines)}"
        )


# ---------------------------------------------------------------------------
# 6. No leftover event handler / session lifecycle code
# ---------------------------------------------------------------------------


class TestNoLegacyCode:
    """PID file management was re-added for process lifecycle tracking.
    These tests verify no OTHER legacy patterns remain.
    """

    def test_no_server_connected(self):
        src = _src()
        assert '"server.connected"' not in src

    def test_no_legacy_report_alive_definition(self):
        src = _src()
        assert "function _reportAlive" not in src, (
            "_reportAlive was moved to shared.ts (E.5 refactor)"
        )

    def test_no_sigterm(self):
        src = _src()
        assert "SIGTERM" not in src


# ---------------------------------------------------------------------------
# 7. reportAlive stub mechanics
# ---------------------------------------------------------------------------


class TestReportAlive:
    def test_calls_report_alive_at_load_time(self):
        src = _src()
        assert 'reportAlive("watchdog")' in src, (
            "Must call reportAlive('watchdog') at plugin load time to prove "
            "the plugin is alive."
        )

    def test_report_alive_call_not_in_async_context(self):
        """Stub factory is synchronous — reportAlive is called directly."""
        src = _src()
        idx = src.find("export default")
        after = src[idx:idx + 200] if idx > 0 else src
        assert "reportAlive" in after

    def test_no_local_report_alive_definition(self):
        """reportAlive is imported, not defined locally."""
        src = _src()
        assert "function reportAlive" not in src, (
            "reportAlive must be imported from shared.ts, not defined locally"
        )
