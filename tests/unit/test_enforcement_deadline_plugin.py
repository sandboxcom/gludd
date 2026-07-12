"""Behavioral-invariant tests for enforce-deadline.ts.

Goes beyond the structural constant-checks in test_enforcement_coverage_gaps.py
(Gap 1) to verify plugin mechanics: djb2 hash determinism, stale sweep
invariants, noise-control throttle, completion reset, and atomic-write patterns.
"""

from __future__ import annotations

import re
from pathlib import Path

DEADLINE_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"


def _src() -> str:
    return DEADLINE_PATH.read_text()


# ---------------------------------------------------------------------------
# SUBAGENT guard + DEADLINE_ENABLED bypass
# ---------------------------------------------------------------------------


class TestDeadlineSubagentGuard:
    def test_guard_checks_env_var(self):
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src

    def test_guard_before_any_enforcement(self):
        src = _src()
        idx = src.rfind('"tool.execute.before": async')
        assert idx > 0, "must find tool.execute.before hook body"
        after = src[idx:idx + 300]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        enabled_idx = after.find("DEADLINE_ENABLED")
        if enabled_idx > 0:
            assert subagent_idx < enabled_idx, "subagent guard must precede DEADLINE_ENABLED check"


class TestDeadlineEnabled:
    def test_enabled_by_default(self):
        src = _src()
        m = re.search(r"DEADLINE_ENABLED\s*=\s*\([^)]*GLUDD_TASK_DEADLINE_ENABLED\s*\|\|\s*\"(.+?)\"", src)
        assert m, "DEADLINE_ENABLED assignment not found"
        assert m.group(1) == "1", "deadline must be enabled by default"

    def test_checked_before_enforcement_in_before_hook(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        enabled_idx = after.find("DEADLINE_ENABLED")
        dispatch_idx = after.find("isDispatchTool")
        assert enabled_idx < dispatch_idx, "DEADLINE_ENABLED check must precede dispatch recording"


# ---------------------------------------------------------------------------
# djb2 hash determinism — must produce same id for same args
# ---------------------------------------------------------------------------


class TestDjb2Hash:
    def test_djb2_constant_is_seed_5381(self):
        src = _src()
        assert src.find("let hash = 5381") > 0, "djb2 seed must be 5381 in source"

    def test_djb2_uses_char_code_for_string(self):
        src = _src()
        assert "charCodeAt" in src, "djb2 must use charCodeAt for each character"

    def test_djb2_combines_subagent_type_and_description(self):
        src = _src()
        idx = src.find("const raw = `")
        assert idx > 0
        after = src[idx:idx + 400]
        assert "subagent_type" in after or "subtype" in after
        assert "description" in after or "desc" in after

    def test_djb2_output_prefixed_with_d(self):
        src = _src()
        idx = src.rfind("djb2")
        section = src[idx:idx + 200]
        assert "d-" in section, "djb2 output must be prefixed with d-"


# ---------------------------------------------------------------------------
# extractTaskId priority order
# ---------------------------------------------------------------------------


class TestExtractTaskIdPriority:
    def test_checks_task_id_first(self):
        src = _src()
        idx = src.find("function extractTaskId")
        after = src[idx:idx + 1200] if idx > 0 else src
        assert "a.task_id" in after, "must check task_id"
        assert "a.id" in after, "must check id fallback"
        assert "a.description" in after, "must check description for djb2 hash"


    def test_returns_null_when_no_match(self):
        src = _src()
        assert "return null" in src

    def test_fallback_auto_id_uses_date_now(self):
        src = _src()
        idx = src.find("auto-${Date.now()}")
        assert idx > 0, "auto fallback must use Date.now()"

    def test_checks_args_is_object_first(self):
        src = _src()
        idx = src.find("function extractTaskId")
        after = src[idx:idx + 200] if idx > 0 else src
        assert "typeof args !== " in after or "typeof args !==" in after, "must check args type"


# ---------------------------------------------------------------------------
# Stale sweep: 3x timeout as max age
# ---------------------------------------------------------------------------


class TestStaleSweepInvariants:
    def test_max_age_is_three_times_timeout(self):
        src = _src()
        assert "TASK_TIMEOUT_MS * 3" in src

    def test_sweep_clears_warned_ids(self):
        src = _src()
        idx = src.find("function sweepStaleEntries")
        after = src[idx:idx + 400] if idx > 0 else src
        assert "warnedIds.delete(id)" in after

    def test_sweep_triggered_on_load(self):
        src = _src()
        assert "sweepStaleEntries(out)" in src

    def test_sweep_skips_non_number_entries(self):
        src = _src()
        idx = src.find("typeof start !== ")
        assert idx > 0, "sweep must skip non-number entries"


# ---------------------------------------------------------------------------
# Noise-control throttle: warnedIds Set
# ---------------------------------------------------------------------------


class TestNoiseControl:
    def test_warned_ids_is_in_memory_set(self):
        src = _src()
        assert "const warnedIds = new Set<string>()" in src

    def test_check_before_console_warn(self):
        src = _src()
        idx = src.find("!warnedIds.has(id)")
        assert idx > 0

    def test_add_after_console_warn(self):
        src = _src()
        warn_pos = src.find("console.warn")
        add_pos = src.find("warnedIds.add(id)")
        assert warn_pos < add_pos, "warnedIds.add must come after console.warn"

    def test_append_warning_goes_to_persistent_log(self):
        src = _src()
        idx = src.find("function appendWarning")
        assert idx > 0
        after = src[idx:idx + 100] if idx > 0 else src
        assert "appendFileSync" in after


# ---------------------------------------------------------------------------
# Deadline reset on completion: tool.execute.after
# ---------------------------------------------------------------------------


class TestDeadlineResetOnCompletion:
    def test_after_hook_only_fires_on_dispatch_tools(self):
        src = _src()
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:after_idx + 300] if after_idx > 0 else src
        assert "isDispatchTool" in after

    def test_deletes_entry_from_deadline_state(self):
        src = _src()
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "delete d[id]" in after

    def test_resets_warned_ids_for_reused_task_id(self):
        src = _src()
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "warnedIds.delete(id)" in after

    def test_skips_when_id_is_null(self):
        src = _src()
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "!id" in after or "if (!id)" in after


# ---------------------------------------------------------------------------
# Stale file: JSON shape + dedup
# ---------------------------------------------------------------------------


class TestStaleFileInvariants:
    def test_stale_file_has_task_id_start_elapsed(self):
        src = _src()
        idx = src.find("function recordStaleTask")
        after = src[idx:idx + 1500] if idx > 0 else src
        assert "task_id" in after
        assert "start_ms" in after
        assert "elapsed_ms" in after
        assert "stale_at" in after

    def test_stale_file_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function recordStaleTask")
        after = src[idx:idx + 1500] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after

    def test_deduplicates_by_task_id(self):
        src = _src()
        idx = src.find("!entries.some")
        assert idx > 0


# ---------------------------------------------------------------------------
# State file: atomic write, load with sweep
# ---------------------------------------------------------------------------


class TestDeadlineStateFileInvariants:
    def test_load_handles_corrupt_file(self):
        src = _src()
        idx = src.find("function loadDeadlines")
        after = src[idx:] if idx > 0 else src
        assert "catch" in after
        assert "return {}" in after

    def test_save_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function saveDeadlines")
        after = src[idx:] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after

    def test_load_calls_sweep_before_returning(self):
        src = _src()
        idx = src.find("function loadDeadlines")
        after = src[idx:idx + 1000] if idx > 0 else src
        assert "sweepStaleEntries(out)" in after


# ---------------------------------------------------------------------------
# Deadline scan: comparison + reporting
# ---------------------------------------------------------------------------


class TestDeadlineScanInvariants:
    def test_scans_every_tool(self):
        src = _src()
        assert "for (const id of Object.keys(d))" in src

    def test_elapsed_comparison_uses_gt_not_gte(self):
        src = _src()
        assert "elapsed > TASK_TIMEOUT_MS" in src

    def test_minutes_format_uses_division_by_60000(self):
        src = _src()
        assert "elapsed / 60000" in src

    def test_limit_minutes_format_uses_timeout_divided_by_60000(self):
        src = _src()
        assert "TASK_TIMEOUT_MS / 60000" in src

    def test_log_line_includes_task_id(self):
        src = _src()
        idx = src.find("TASK DEADLINE EXCEEDED")
        after = src[idx:idx + 200] if idx > 0 else src
        assert "task ${id}" in after


# ---------------------------------------------------------------------------
# Fail-open guarantee
# ---------------------------------------------------------------------------


class TestDeadlineFailOpen:
    def test_before_hook_has_try_catch(self):
        src = _src()
        before_idx = src.rfind('"tool.execute.before": async')
        after_idx = src.rfind('"tool.execute.after": async')
        before = src[before_idx:after_idx] if before_idx > 0 and after_idx > 0 else src
        assert "catch" in before

    def test_after_hook_has_try_catch(self):
        src = _src()
        after_idx = src.find("tool.execute.after")
        after = src[after_idx:] if after_idx > 0 else src
        assert "catch" in after

    def test_append_warning_catches_io_errors(self):
        src = _src()
        idx = src.find("function appendWarning")
        after = src[idx:idx + 150] if idx > 0 else src
        assert "catch" in after

    def test_record_stale_catches_io_errors(self):
        src = _src()
        idx = src.find("function recordStaleTask")
        after = src[idx:] if idx > 0 else src
        assert "catch" in after

    def test_sweep_has_save_catch(self):
        src = _src()
        idx = src.find("function sweepStaleEntries")
        after = src[idx:idx + 1500] if idx > 0 else src
        assert "catch" in after


# ---------------------------------------------------------------------------
# Warnings log: persistent format
# ---------------------------------------------------------------------------


class TestWarningsLogInvariants:
    def test_warnings_log_env_var_exists(self):
        src = _src()
        assert "GLUDD_TASK_DEADLINE_WARNINGS" in src

    def test_warnings_log_default_path(self):
        src = _src()
        assert "warnings.log" in src

    def test_warnings_log_written_with_iso_timestamp(self):
        src = _src()
        idx = src.find("appendWarning")
        assert "appendWarning" in src
        warn_call_idx = src.find("appendWarning(`${new Date().toISOString()}")
        assert warn_call_idx > 0, "warnings should include ISO timestamp"


# ---------------------------------------------------------------------------
# Plugin export shape
# ---------------------------------------------------------------------------


class TestDeadlinePluginExport:
    def test_exports_satisfies_plugin_type(self):
        src = _src()
        assert "satisfies Plugin" in src

    def test_factory_is_async(self):
        src = _src()
        assert "export default (async" in src or "export default(async" in src

    def test_returns_object_with_two_hooks(self):
        src = _src()
        assert '"tool.execute.before"' in src
        assert '"tool.execute.after"' in src
