"""Behavioral-invariant tests for watchdog.ts.

The watchdog plugin lives at .opencode/plugins/watchdog.ts (the only plugin in
the `plugins/` dir — all others are in `plugin/`). It is an event-only plugin
that auto-launches the background watchdog daemon on session/server start and
cleans up PID files on session deletion. It also emits a periodic heartbeat
(_reportAlive) so the plugin liveness checker can confirm it is still executing.

Test categories:
  1. File existence + opencode.json registration
  2. Key constants and env-var overrides
  3. Fail-open behavior (try/catch count, per-function wrapping)
  4. SUBAGENT guard (intentionally absent for this plugin)
  5. Plugin shape (export type, hook registry, hook types)
  6. Session lifecycle hooks (created, connected, deleted)
  7. _reportAlive heartbeat mechanics
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

    def test_alive_file_path(self):
        src = _src()
        assert "/tmp/gludd-plugin-alive.json" in src, (
            "_reportAlive must write to /tmp/gludd-plugin-alive.json"
        )


# ---------------------------------------------------------------------------
# 3. Fail-open behavior
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_report_alive_wrapped_in_try_catch(self):
        src = _src()
        idx = src.find("function _reportAlive")
        assert idx > 0, "_reportAlive function not found"
        after = src[idx:]
        assert "try {" in after, "_reportAlive body must have try block"
        assert "catch" in after, "_reportAlive must have catch for fail-open"

    def test_report_alive_inner_try_catch_for_existing_file(self):
        """Reading the existing alive JSON is also try/catch wrapped."""
        src = _src()
        idx = src.find("function _reportAlive")
        after = src[idx:idx + 500] if idx > 0 else src
        assert after.count("try {") >= 2, (
            "_reportAlive must have nested try for reading existing JSON"
        )

    def test_session_deleted_wrapped_in_try_catch(self):
        src = _src()
        idx = src.find('"session.deleted"')
        assert idx > 0, "session.deleted handler not found"
        after = src[idx:]
        assert "try {" in after, "session.deleted must have try block"

    def test_session_deleted_pid_cleanup_nested_try_catch(self):
        """Each file operation in session.deleted is individually try/catch wrapped."""
        src = _src()
        idx = src.find('"session.deleted"')
        after = src[idx:idx + 500] if idx > 0 else src
        assert after.count("try {") >= 3, (
            "session.deleted must wrap each file op in its own try/catch"
        )

    def test_total_catch_blocks(self):
        src = _src()
        catch_count = len(re.findall(r"\bcatch\b", src))
        assert catch_count >= 7, (
            f"expected >=7 catch blocks for fail-open, found {catch_count}"
        )

    def test_no_bare_throw(self):
        src = _src()
        assert "throw new Error" not in src and "throw " not in src, (
            "watchdog.ts must never throw — it is a background service, not an enforcer"
        )

    def test_every_make_watchdog_auto_wrapped_in_catch(self):
        """Both `make watchdog-auto` invocations are try/catch wrapped."""
        src = _src()
        calls = len(re.findall(r"make watchdog-auto", src))
        assert calls >= 2, (
            f"expected >=2 'make watchdog-auto' references, found {calls}"
        )
        # Each call site must be inside a try block
        lines = src.split("\n")
        in_try = 0
        found_calls = 0
        for line in lines:
            if "try {" in line:
                in_try += 1
            if "make watchdog-auto" in line and in_try > 0:
                found_calls += 1
            if "} catch" in line:
                in_try = max(0, in_try - 1)
        assert found_calls >= 2, (
            f"At least 2 'make watchdog-auto' calls must be inside try blocks; "
            f"found {found_calls} call(s) in try context"
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
# 5. Plugin shape (export type, hook registry)
# ---------------------------------------------------------------------------


class TestPluginShape:
    def test_export_is_default_async_factory(self):
        src = _src()
        assert "export default" in src, "must have default export"
        assert "async" in src, "factory must be async"

    def test_satisfies_plugin_type(self):
        src = _src()
        assert "satisfies Plugin" in src, (
            "must use 'satisfies Plugin' type assertion"
        )

    def test_factory_receives_dollar_shell_proxy(self):
        src = _src()
        assert "({ $" in src or "({$" in src, (
            "factory must destructure $ shell proxy for `make watchdog-auto`"
        )

    def test_registers_event_hook_only(self):
        """watchdog is an event-only plugin — no tool.execute hooks."""
        src = _src()
        assert "event:" in src, "must register event hook"
        assert '"tool.execute.before"' not in src, (
            "must NOT register tool.execute.before — watchdog is not an enforcer"
        )
        assert '"tool.execute.after"' not in src, (
            "must NOT register tool.execute.after"
        )

    def test_event_handler_is_async(self):
        src = _src()
        idx = src.find("event: async")
        assert idx > 0, "event handler must be async"

    def test_returns_only_event_hook(self):
        src = _src()
        idx = src.find("return {")
        assert idx > 0, "must have return statement"
        returns = src[idx:idx + 100] if idx > 0 else src
        assert returns.count(",") == 0, (
            "return block must have exactly one hook (event) — no extra hooks"
        )


# ---------------------------------------------------------------------------
# 6. Session lifecycle hooks
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    def test_handles_session_created(self):
        src = _src()
        assert '"session.created"' in src, (
            "must listen for session.created event"
        )

    def test_handles_server_connected(self):
        src = _src()
        assert '"server.connected"' in src, (
            "must listen for server.connected event"
        )

    def test_handles_session_deleted(self):
        src = _src()
        assert '"session.deleted"' in src, (
            "must listen for session.deleted event"
        )

    def test_session_created_triggers_watchdog_auto(self):
        src = _src()
        idx = src.find("session.created")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "make watchdog-auto" in after, (
            "session.created must trigger `make watchdog-auto`"
        )

    def test_server_connected_triggers_watchdog_auto(self):
        src = _src()
        idx = src.find("server.connected")
        assert idx > 0
        after = src[idx:idx + 200]
        assert "make watchdog-auto" in after, (
            "server.connected must trigger `make watchdog-auto`"
        )

    def test_session_deleted_kills_pid_via_sigterm(self):
        src = _src()
        idx = src.find("session.deleted")
        assert idx > 0
        after = src[idx:idx + 500]
        assert "SIGTERM" in after, (
            "session.deleted must kill PID files via SIGTERM"
        )

    def test_session_deleted_unlinks_pid_files(self):
        src = _src()
        idx = src.find("session.deleted")
        assert idx > 0
        after = src[idx:idx + 500]
        assert "unlinkSync" in after, (
            "session.deleted must remove PID files via unlinkSync"
        )

    def test_session_deleted_cleans_both_pid_files(self):
        src = _src()
        idx = src.find("session.deleted")
        assert idx > 0
        after = src[idx:idx + 500]
        assert "PID_FILE" in after, "session.deleted must reference PID_FILE"
        assert "TASK_PID_FILE" in after, "session.deleted must reference TASK_PID_FILE"

    def test_session_created_syncs_literal_pid_to_env_override(self):
        """session.created copies .gate-logs/watchdog.pid to PID_FILE when they differ.

        This handles test-mode isolation where PID_FILE is redirected via env var
        but `make watchdog-auto` writes to the literal path.
        """
        src = _src()
        idx = src.find("session.created")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "literalPid" in after, (
            "session.created must sync literal PID path to env-override PID_FILE"
        )
        assert "existsSync(literalPid)" in after or "existsSync" in after, (
            "session.created must check if literal PID file exists before syncing"
        )

    def test_calls_report_alive_on_every_event(self):
        src = _src()
        idx = src.find("event: async")
        assert idx > 0
        after = src[idx:idx + 150]
        assert "_reportAlive()" in after, (
            "_reportAlive must be called on every event (before dispatch)"
        )


# ---------------------------------------------------------------------------
# 7. _reportAlive heartbeat mechanics
# ---------------------------------------------------------------------------


class TestReportAlive:
    def test_report_alive_function_exists(self):
        src = _src()
        assert "function _reportAlive" in src, (
            "_reportAlive function must be defined"
        )

    def test_report_alive_writes_json_with_last_seen(self):
        src = _src()
        idx = src.find("function _reportAlive")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "last_seen" in after, (
            "_reportAlive must write 'last_seen' field"
        )
        assert "Date.now()" in after, (
            "_reportAlive must timestamp with Date.now()"
        )

    def test_report_alive_under_watchdog_key(self):
        src = _src()
        idx = src.find("function _reportAlive")
        assert idx > 0
        after = src[idx:idx + 400]
        assert '"watchdog"' in after, (
            "_reportAlive must nest data under 'watchdog' key"
        )

    def test_report_alive_merges_existing_json(self):
        src = _src()
        idx = src.find("function _reportAlive")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "Object.assign" in after, (
            "_reportAlive must merge existing alive.json data via Object.assign"
        )

    def test_report_alive_guards_against_absent_existing_file(self):
        src = _src()
        idx = src.find("function _reportAlive")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "existsSync" in after, (
            "_reportAlive must guard read with existsSync"
        )

    def test_report_alive_writes_to_tmp(self):
        src = _src()
        assert "writeFileSync" in src, (
            "_reportAlive must use writeFileSync to persist heartbeat"
        )


# ---------------------------------------------------------------------------
# 8. Imports
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

    def test_no_unused_imports(self):
        src = _src()
        import_lines = [
            l for l in src.split("\n")
            if l.strip().startswith("import ")
        ]
        assert len(import_lines) == 2, (
            f"expected 2 imports (Plugin + node:fs), found {len(import_lines)}"
        )
