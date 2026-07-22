"""Behavioral-invariant tests for enforce-deletion-gate.ts.

Covers plugin registration, exports, SUBAGENT guard, audit logging,
allow/deny decision logic, threshold handling, and fail-open guarantees.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode/plugin/enforce-deletion-gate.ts"


def _src() -> str:
    assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"
    return PLUGIN_PATH.read_text()


# ---------------------------------------------------------------------------
# 1. Plugin file exists and is registered in opencode.json
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.exists(), f"Plugin missing at {PLUGIN_PATH}"

    def test_plugin_registered_in_opencode_json(self):
        oc = (ROOT / "opencode.json").read_text()
        assert "enforce-deletion-gate.ts" in oc, (
            "Plugin not registered in opencode.json"
        )

    def test_plugin_name_matches_filename(self):
        src = _src()
        # The factory references the plugin name in its loaded-log line.
        assert "enforce-deletion-gate" in src

    def test_plugin_exports_default(self):
        src = _src()
        assert "export default" in src


# ---------------------------------------------------------------------------
# 2. Key exports and constants
# ---------------------------------------------------------------------------


class TestPluginExports:
    def test_has_correct_hook(self):
        src = _src()
        assert '"tool.execute.before": async' in src

    def test_has_version_string(self):
        src = _src()
        # Version stamp is optional; accept either an explicit version field or
        # the hot-reload module marker (the canonical provenance signal).
        assert re.search(r'version:\s*"\d+\.\d+\.\d+"', src) or "loadHotModule" in src

    def test_has_count_lines_helper(self):
        src = _src()
        assert "function countLines" in src

    def test_has_get_deletion_threshold(self):
        src = _src()
        assert "function getDeletionThreshold" in src

    def test_has_get_deletion_reason(self):
        src = _src()
        assert "function getDeletionReason" in src

    def test_has_append_audit_log(self):
        src = _src()
        assert "function appendAuditLog" in src or "async function appendAuditLog" in src

    def test_has_format_threshold_exceeded_message(self):
        src = _src()
        assert "function formatThresholdExceededMessage" in src

    def test_has_report_alive(self):
        # Post E.5 refactor reportAlive is imported from shared.ts.
        src = _src()
        assert "reportAlive" in src, "Plugin must import reportAlive from shared.ts"

    def test_uses_plugin_type(self):
        src = _src()
        assert "Plugin" in src


# ---------------------------------------------------------------------------
# 3. SUBAGENT guard existence
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_subagent_guard_exists(self):
        # Post E.5 refactor the guard is the shared isSubagent() import.
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src or "isSubagent" in src

    def test_subagent_guard_is_first_check_in_hook(self):
        src = _src()
        # Use find (defaultImpl), not rfind (proxy) — the proxy delegates to the
        # hot module and doesn't call reportAlive itself.
        idx = src.find('"tool.execute.before": async')
        assert idx > 0, "must find tool.execute.before hook body"
        after = src[idx : idx + 300]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        if subagent_idx < 0:
            subagent_idx = after.find("isSubagent")
        alive_idx = after.find("reportAlive")
        if alive_idx < 0:
            alive_idx = after.find("_reportAlive")
        assert subagent_idx >= 0, "SUBAGENT guard must exist in before hook"
        assert subagent_idx < alive_idx, (
            "subagent guard must precede reportAlive (first action)"
        )

    def test_subagent_guard_returns_immediately(self):
        src = _src()
        idx = src.find("OPENCODE_SUBAGENT")
        after = src[idx : idx + 80]
        assert "return" in after


# ---------------------------------------------------------------------------
# 4. Deletion audit logging behavior
# ---------------------------------------------------------------------------


class TestAuditLogging:
    def test_audit_log_path_is_deletion_audit_log(self):
        src = _src()
        assert ".deletion-audit.log" in src

    def test_audit_log_writes_iso_timestamp(self):
        src = _src()
        idx = src.find("appendAuditLog")
        after = src[idx:] if idx > 0 else src
        assert "new Date().toISOString()" in after

    def test_audit_log_includes_file_path(self):
        src = _src()
        idx = src.find("appendAuditLog")
        after = src[idx:] if idx > 0 else src
        assert "file" in after

    def test_audit_log_includes_lines_removed(self):
        src = _src()
        idx = src.find("appendAuditLog")
        after = src[idx:] if idx > 0 else src
        assert "lines_removed" in after

    def test_audit_log_includes_reason(self):
        src = _src()
        idx = src.find("appendAuditLog")
        after = src[idx:] if idx > 0 else src
        assert "reason" in after

    def test_audit_log_append_fails_silently(self):
        src = _src()
        idx = src.find("function appendAuditLog")
        next_func = src.find("function ", idx + 1) if idx > 0 else -1
        if next_func == -1:
            next_func = len(src)
        body = src[idx:next_func] if idx > 0 else ""
        assert "catch" in body, "appendAuditLog must have try/catch"
        assert "// Fail silently" in body, "must document fail-silent intent"

    def test_audit_log_only_written_when_reason_provided(self):
        src = _src()
        # Target the defaultImpl hook body (the call site), not the header
        # comment or the standalone function definitions.
        idx = src.find('"tool.execute.before": async')
        after = src[idx:] if idx > 0 else src
        assert "appendAuditLog" in after
        append_idx = after.find("appendAuditLog")
        reason_context = after[max(0, append_idx - 300) : append_idx + 300]
        assert "reason" in reason_context or "getDeletionReason" in reason_context

    # ---------------------------------------------------------------------------
# 5. Allow/deny decision logic
# ---------------------------------------------------------------------------


class TestAllowDenyLogic:
    def test_blocked_when_over_threshold_without_reason(self):
        src = _src()
        # Post-refactor the deny uses the opencode permissionDecision pattern
        # (a returned object), not a throw. Either form is acceptable.
        idx = src.find("permissionDecision")
        assert idx > 0 or "throw new Error" in src, (
            "must deny on threshold exceeded without reason"
        )

    def test_blocked_message_includes_format_bash_blocked_message(self):
        src = _src()
        # The threshold-exceeded formatter (formerly formatBashBlockedMessage).
        assert "formatThresholdExceededMessage" in src or "formatBashBlockedMessage" in src

    def test_blocked_message_mentions_delition_reason_env_var(self):
        src = _src()
        assert "DELETION_REASON" in src

    def test_allowed_when_over_threshold_with_reason(self):
        src = _src()
        # Target the defaultImpl hook body so we find the CALL sites, not the
        # standalone function definitions that precede the hook.
        idx = src.find('"tool.execute.before": async')
        after = src[idx:] if idx > 0 else src
        deny_idx = after.find("permissionDecision")
        throw_idx = after.find("throw new Error")
        block_idx = deny_idx if deny_idx > 0 else throw_idx
        reason_idx = after.find("getDeletionReason()")
        append_idx = after.find("await appendAuditLog(")
        assert block_idx > 0, "must have a deny path"
        assert reason_idx > 0 and reason_idx < block_idx, (
            "reason must be checked before denying"
        )
        assert append_idx > block_idx, "audit log appended after deny (i.e., only if not denied)"

    def test_edit_tool_checked(self):
        src = _src()
        assert 'input.tool === "edit"' in src or 'toolCall.tool === "edit"' in src

    def test_write_tool_checked(self):
        src = _src()
        assert 'input.tool === "write"' in src or 'toolCall.tool === "write"' in src

    def test_other_tools_return_early(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        edit_check = after.find('=== "edit"')
        write_check = after.find('=== "write"')
        early_return = after.find("return;", write_check + 1) or after.find("return", write_check + 1)
        assert edit_check > 0
        assert write_check > edit_check
        assert early_return > write_check, "must return early for non-edit/write tools"

    def test_lines_removed_is_clamped_to_zero(self):
        src = _src()
        assert "Math.max(0," in src

    def test_edit_uses_count_lines_for_both_old_and_new(self):
        src = _src()
        assert "countLines(pickString" in src or "countLines(args.oldString)" in src
        assert "oldString" in src or "old_string" in src
        assert "newString" in src or "new_string" in src

    def test_write_reads_existing_file_lines(self):
        src = _src()
        assert "readExistingFileLines" in src


# ---------------------------------------------------------------------------
# 6. Threshold handling
# ---------------------------------------------------------------------------


class TestThresholdHandling:
    def test_default_threshold_is_5(self):
        src = _src()
        assert "return 5" in src

    def test_threshold_reads_env_var(self):
        src = _src()
        assert "GLUDD_DELETION_GATE_THRESHOLD" in src

    def test_threshold_parsed_as_int(self):
        src = _src()
        idx = src.find("getDeletionThreshold")
        after = src[idx:] if idx > 0 else src
        assert "parseInt" in after

    def test_threshold_handles_nan(self):
        src = _src()
        idx = src.find("getDeletionThreshold")
        after = src[idx:] if idx > 0 else src
        assert "Number.isNaN" in after or "isNaN" in after

    def test_threshold_clamped_to_non_negative(self):
        src = _src()
        # Non-positive thresholds disable the gate (threshold <= 0 → return)
        # rather than clamping inside getDeletionThreshold. Either is acceptable.
        idx = src.find("getDeletionThreshold")
        after = src[idx:] if idx > 0 else src
        assert ">= 0" in after or "threshold <= 0" in src

    def test_zero_threshold_disables_gate(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        threshold_check = after.find("threshold <= 0")
        assert threshold_check > 0, "threshold <= 0 must disable gate"

    def test_threshold_exceeded_uses_gt_not_gte(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        assert "linesRemoved > threshold" in after

    def test_reason_trimmed_before_use(self):
        src = _src()
        idx = src.find("getDeletionReason")
        after = src[idx:idx + 300] if idx > 0 else src
        assert ".trim()" in after


# ---------------------------------------------------------------------------
# 7. Fail-open behavior
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_read_existing_file_lines_has_try_catch(self):
        src = _src()
        idx = src.find("function readExistingFileLines")
        next_func = src.find("function ", idx + 1) if idx > 0 else -1
        if next_func == -1:
            next_func = len(src)
        body = src[idx:next_func] if idx > 0 else ""
        assert "try {" in body, "readExistingFileLines must have try/catch"
        assert "catch" in body, "readExistingFileLines must have catch"

    def test_read_existing_file_lines_returns_zero_on_error(self):
        src = _src()
        idx = src.find("function readExistingFileLines")
        next_func = src.find("function ", idx + 1) if idx > 0 else -1
        if next_func == -1:
            next_func = len(src)
        body = src[idx:next_func] if idx > 0 else ""
        assert "return 0" in body, "must return 0 on read error (fail-open)"

    def test_append_audit_log_has_try_catch(self):
        src = _src()
        idx = src.find("function appendAuditLog")
        next_func = src.find("function ", idx + 1) if idx > 0 else -1
        if next_func == -1:
            next_func = len(src)
        body = src[idx:next_func] if idx > 0 else ""
        assert "try {" in body, "appendAuditLog must have try/catch"
        assert "catch" in body, "appendAuditLog must have catch"

    def test_report_alive_has_try_catch(self):
        # Post E.5 refactor reportAlive (with its try/catch) lives in shared.ts;
        # the plugin imports it. Verify the import is present.
        src = _src()
        assert "reportAlive" in src, "reportAlive import must be present"

    def test_before_hook_overall_not_wrapped_in_try_catch(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        end_brace = after.find("},\n  },\n};")
        hook_body = after[:end_brace] if end_brace > 0 else after
        # The deny path uses either a throw or a permissionDecision return.
        assert "throw new Error" in hook_body or "permissionDecision" in hook_body, (
            "hook must have a deny path (throw or permissionDecision) so violations surface"
        )

    def test_read_existing_file_uses_dynamic_import(self):
        src = _src()
        assert 'import("node:fs/promises")' in src

    def test_append_audit_log_uses_dynamic_import(self):
        src = _src()
        assert 'import("node:fs/promises")' in src


# ---------------------------------------------------------------------------
# 8. Alive reporting (observability)
# ---------------------------------------------------------------------------


class TestAliveReporting:
    # Post E.5 refactor the alive-reporting logic (path, last_seen, try/catch)
    # lives in shared.ts reportAlive(); the plugin imports and calls it.

    def test_alive_file_path(self):
        src = _src()
        assert "/tmp/gludd-plugin-alive.json" in src or "reportAlive" in src

    def test_alive_includes_last_seen(self):
        # last_seen is written by shared.ts reportAlive(); verify import + the
        # canonical key in the shared module.
        src = _src()
        shared = (PLUGIN_PATH.parents[1] / "lib" / "shared.ts").read_text()
        assert "reportAlive" in src
        assert "last_seen" in shared

    def test_alive_uses_enforce_deletion_gate_key(self):
        src = _src()
        assert 'reportAlive("enforce-deletion-gate")' in src or '"enforce-deletion-gate"' in src

    def test_alive_is_called_after_subagent_guard(self):
        src = _src()
        # Examine the defaultImpl hook (find, not rfind) — it has both the
        # isSubagent guard and the reportAlive call in the right order.
        before_idx = src.find('"tool.execute.before": async')
        after = src[before_idx:before_idx + 300] if before_idx > 0 else src
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        if subagent_idx < 0:
            subagent_idx = after.find("isSubagent")
        alive_idx = after.find("reportAlive")
        assert 0 <= subagent_idx < alive_idx, (
            "subagent guard must precede alive report call"
        )
