"""Structural + behavioral tests for .opencode/plugin/shared.ts (E.5 refactor).

Verifies the shared module exports the 5 key functions + 2 path constants
that were extracted from duplicated patterns across 14 enforce-*.ts plugins.
Also verifies enforce-floor.ts correctly imports and uses the shared module.
Covers: isSubagent, isDisengaged, readJsonFile, writeJsonFile, reportAlive,
path constants, and the enforce-floor.ts integration.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED_PATH = ROOT / ".opencode" / "plugin" / "shared.ts"
FLOOR_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"


def _src(path: Path = SHARED_PATH) -> str:
    return path.read_text()


# ── File existence ────────────────────────────────────────────────────────


class TestSharedModuleExists:
    def test_file_exists(self):
        assert SHARED_PATH.is_file(), "shared.ts must exist"

    def test_is_valid_typescript(self):
        src = _src()
        assert "import * as fs" in src
        assert "export" in src


# ── isSubagent export ─────────────────────────────────────────────────────


class TestIsSubagent:
    def test_function_exported(self):
        src = _src()
        assert "export function isSubagent" in src

    def test_checks_env_var(self):
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src

    def test_file_based_fallback(self):
        src = _src()
        assert "SUBAGENT_MARKER" in src
        assert "existsSync" in src

    def test_returns_boolean(self):
        src = _src()
        idx = src.find("export function isSubagent")
        after = src[idx:]
        assert ": boolean" in after[:200]

    def test_fail_open_on_error(self):
        src = _src()
        idx = src.find("export function isSubagent")
        after = src[idx:idx + 300]
        assert "catch" in after


# ── SUBAGENT_MARKER constant ──────────────────────────────────────────────


class TestSubagentMarker:
    def test_exported(self):
        src = _src()
        assert "export const SUBAGENT_MARKER" in src

    def test_uses_pid_in_path(self):
        src = _src()
        assert "gludd-subagent-" in src
        assert "${pid}" in src


# ── isDisengaged export ───────────────────────────────────────────────────


class TestIsDisengaged:
    def test_function_exported(self):
        src = _src()
        assert "export function isDisengaged" in src

    def test_reads_disengage_path(self):
        src = _src()
        assert "DISENGAGE_PATH" in src

    def test_checks_disengage_until_field(self):
        src = _src()
        assert "disengage_until" in src

    def test_clamped_by_max_ms(self):
        src = _src()
        idx = src.find("export function isDisengaged")
        after = src[idx:idx + 500]
        assert "Math.min" in after

    def test_return_boolean(self):
        src = _src()
        idx = src.find("export function isDisengaged")
        after = src[idx:idx + 200]
        assert ": boolean" in after

    def test_fail_open_returns_false(self):
        src = _src()
        idx = src.find("export function isDisengaged")
        after = src[idx:idx + 500]
        catches = after.count("catch")
        assert catches >= 1, "isDisengaged must have catch blocks"
        assert "return false" in after

    def test_default_max_ms_is_1_hour(self):
        src = _src()
        idx = src.find("isDisengaged")
        after = src[idx:idx + 300]
        assert "3_600_000" in after

    def test_disengage_opts_interface(self):
        src = _src()
        assert "DisengageOpts" in src
        assert "maxMs" in src

    def test_disengage_path_constant(self):
        src = _src()
        assert "DISENGAGE_PATH" in src
        assert "gludd-watchdog-disengage.json" in src

    def test_disengage_path_env_override(self):
        src = _src()
        idx = src.find("DISENGAGE_PATH")
        after = src[idx:idx + 150]
        assert "GLUDD_DISENGAGE_PATH" in after


# ── readJsonFile / writeJsonFile exports ──────────────────────────────────


class TestJsonHelpers:
    def test_read_exported(self):
        src = _src()
        assert "export function readJsonFile" in src

    def test_read_is_generic(self):
        src = _src()
        assert "readJsonFile<T>" in src

    def test_read_returns_default_on_missing(self):
        src = _src()
        idx = src.find("export function readJsonFile")
        after = src[idx:idx + 400]
        assert "return defaultVal" in after

    def test_read_fail_open_on_corrupt(self):
        src = _src()
        idx = src.find("export function readJsonFile")
        after = src[idx:idx + 400]
        assert "catch" in after

    def test_write_exported(self):
        src = _src()
        assert "export function writeJsonFile" in src

    def test_write_accepts_unknown_data(self):
        src = _src()
        idx = src.find("export function writeJsonFile")
        after = src[idx:idx + 150]
        assert "unknown" in after

    def test_write_fail_open(self):
        src = _src()
        idx = src.find("export function writeJsonFile")
        after = src[idx:idx + 200]
        assert "catch" in after


# ── reportAlive export ────────────────────────────────────────────────────


class TestReportAlive:
    def test_function_exported(self):
        src = _src()
        assert "export function reportAlive" in src

    def test_accepts_plugin_name(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 100]
        assert "pluginName" in after

    def test_writes_to_alive_path(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 300]
        assert "ALIVE_PATH" in after

    def test_uses_read_json_file(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 300]
        assert "readJsonFile" in after

    def test_uses_write_json_file(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 300]
        assert "writeJsonFile" in after

    def test_sets_last_seen_timestamp(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 300]
        assert "last_seen" in after
        assert "Date.now()" in after

    def test_fail_open(self):
        src = _src()
        idx = src.find("export function reportAlive")
        after = src[idx:idx + 400]
        assert "catch" in after


# ── ALIVE_PATH constant ───────────────────────────────────────────────────


class TestAlivePath:
    def test_exported(self):
        src = _src()
        assert "export const ALIVE_PATH" in src

    def test_includes_alive_file(self):
        src = _src()
        assert "gludd-plugin-alive.json" in src

    def test_env_override(self):
        src = _src()
        idx = src.find("ALIVE_PATH")
        after = src[idx:idx + 150]
        assert "GLUDD_ALIVE_PATH" in after


# ── No default export (this is a library, not a plugin) ───────────────────


class TestNotAPlugin:
    def test_no_default_export(self):
        src = _src()
        assert "export default" not in src, "shared.ts is a library, not a plugin"

    def test_no_plugin_satisfies(self):
        src = _src()
        assert "satisfies Plugin" not in src

    def test_no_hooks(self):
        src = _src()
        assert '"tool.execute.before"' not in src
        assert '"text.complete"' not in src


# ── enforce-floor.ts integration ──────────────────────────────────────────


class TestEnforceFloorUsesShared:
    def test_imports_shared_helpers(self):
        src = FLOOR_PATH.read_text()
        assert 'import {' in src
        assert '"./shared.ts"' in src

    def test_imports_is_subagent(self):
        src = FLOOR_PATH.read_text()
        assert "isSubagent" in src

    def test_imports_is_disengaged(self):
        src = FLOOR_PATH.read_text()
        assert "isDisengaged" in src

    def test_imports_report_alive(self):
        src = FLOOR_PATH.read_text()
        assert "reportAlive" in src

    def test_imports_read_json_file(self):
        src = FLOOR_PATH.read_text()
        assert "readJsonFile" in src

    def test_imports_write_json_file(self):
        src = FLOOR_PATH.read_text()
        assert "writeJsonFile" in src

    def test_imports_alive_path(self):
        src = FLOOR_PATH.read_text()
        assert "ALIVE_PATH" in src

    def test_imports_disengage_path(self):
        src = FLOOR_PATH.read_text()
        assert "DISENGAGE_PATH" in src

    def test_no_duplicate_is_subagent_definition(self):
        src = FLOOR_PATH.read_text()
        # After refactor, the inline _isSubagent function should be replaced
        # by a note. There should not be a function body.
        assert "function _isSubagent(): boolean" not in src
        assert "function _isSubagent():" in src or "isSubagent imported" in src

    def test_no_duplicate_report_alive_definition(self):
        src = FLOOR_PATH.read_text()
        assert "function _reportAlive()" not in src

    def test_uses_is_subagent_in_before_hook(self):
        src = FLOOR_PATH.read_text()
        idx = src.find('"tool.execute.before": async')
        after = src[idx:idx + 300]
        assert "isSubagent()" in after

    def test_uses_is_subagent_in_text_complete(self):
        src = FLOOR_PATH.read_text()
        idx = src.find('"experimental.text.complete": async')
        after = src[idx:idx + 300]
        assert "isSubagent()" in after

    def test_uses_is_disengaged_in_replace_of_old_blocks(self):
        src = FLOOR_PATH.read_text()
        # All 3 old disengage blocks should be replaced by isDisengaged() calls
        assert src.count("isDisengaged()") >= 3

    def test_no_raw_disengage_json_read(self):
        src = FLOOR_PATH.read_text()
        # Old pattern: reading disengage file directly should be gone
        assert 'fs.existsSync(disPath)' not in src
        assert "gludd-watchdog-disengage.json" not in src

    def test_uses_report_alive_in_before_hook(self):
        src = FLOOR_PATH.read_text()
        idx = src.find('"tool.execute.before": async')
        after = src[idx:idx + 300]
        assert 'reportAlive("enforce-floor")' in after

    def test_still_exports_satisfies_plugin(self):
        src = FLOOR_PATH.read_text()
        assert "satisfies Plugin" in src

    def test_still_exports_default(self):
        src = FLOOR_PATH.read_text()
        assert "export default" in src

    def test_still_has_all_three_hooks(self):
        src = FLOOR_PATH.read_text()
        assert '"tool.execute.before"' in src
        assert '"session.idle"' in src
        assert '"experimental.text.complete"' in src
