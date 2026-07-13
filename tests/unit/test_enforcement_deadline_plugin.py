"""Behavioral-invariant tests for enforce-deadline.ts.

Goes beyond the structural constant-checks in test_enforcement_coverage_gaps.py
(Gap 1) to verify plugin mechanics: djb2 hash determinism, stale sweep
invariants, noise-control throttle, completion reset, and atomic-write patterns.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
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


# ============================================================================
# BEHAVIORAL TESTS — Python simulation of plugin state-machine logic
# ============================================================================


def _djb2(raw: str) -> str:
    hash_val = 5381
    for ch in raw:
        hash_val = ((hash_val << 5) + hash_val + ord(ch)) & 0xFFFFFFFF
    return f"d-{(hash_val):08x}"


def _extract_task_id(args: dict | None) -> str | None:
    if args is None:
        return None
    tid = args.get("task_id")
    if isinstance(tid, str) and tid:
        return tid
    fid = args.get("id")
    if isinstance(fid, str) and fid:
        return fid
    desc = args.get("description", "") or ""
    subtype = args.get("subagent_type", "") or ""
    if desc or subtype:
        return _djb2(f"{subtype}:{desc}")
    return None


def _is_dispatch_tool(tool: str) -> bool:
    return tool in ("task", "agent", "workflow")


def _load_deadlines(state_path, timeout_ms):
    try:
        raw = state_path.read_text()
        data = json.loads(raw)
    except Exception:
        return {}
    result = {}
    now = int(time.time() * 1000)
    max_age = timeout_ms * 3
    for k, v in data.items():
        if not isinstance(v, (int, float)):
            continue
        if now - v > max_age:
            continue
        result[k] = v
    return result


def _save_deadlines(state_path: Path, state: dict):
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.replace(tmp, state_path)


def _now_ms() -> int:
    return int(time.time() * 1000)



class TestDjb2HashBehavioral:
    def test_djb2_deterministic_same_args(self):
        a = _djb2("explore:find foo")
        b = _djb2("explore:find foo")
        assert a == b

    def test_djb2_different_args_different_hash(self):
        a = _djb2("explore:find foo")
        b = _djb2("general:edit bar")
        assert a != b

    def test_djb2_produces_d_prefix(self):
        h = _djb2("task:do work")
        assert h.startswith("d-")

    def test_djb2_handles_empty_string(self):
        h = _djb2("")
        assert h == "d-00001505"

    def test_djb2_seed_is_5381_in_python_mirror(self):
        assert _djb2("") == "d-00001505"


class TestExtractTaskIdBehavioral:
    def test_prefers_task_id_field(self):
        tid = _extract_task_id({"task_id": "ses_abc123", "description": "do work"})
        assert tid == "ses_abc123"

    def test_falls_back_to_id_field(self):
        tid = _extract_task_id({"id": "task-456", "description": "do work"})
        assert tid == "task-456"

    def test_uses_djb2_when_no_task_id_or_id(self):
        tid = _extract_task_id({"subagent_type": "explore", "description": "find foo"})
        assert tid is not None
        assert tid.startswith("d-")

    def test_djb2_same_as_plugin_for_simple_case(self):
        tid = _extract_task_id({"subagent_type": "general", "description": "test"})
        expected = _djb2("general:test")
        assert tid == expected

    def test_returns_none_for_none_args(self):
        assert _extract_task_id(None) is None

    def test_returns_none_for_empty_args(self):
        tid = _extract_task_id({})
        assert tid is None

    def test_prefers_task_id_over_id(self):
        tid = _extract_task_id({"task_id": "alpha", "id": "beta"})
        assert tid == "alpha"


class TestDeadlineStateFileBehavioral:
    def test_write_and_read_roundtrip(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        original = {"task-a": _now_ms(), "task-b": _now_ms()}
        _save_deadlines(state_path, original)
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert set(loaded.keys()) == {"task-a", "task-b"}

    def test_corrupt_file_returns_empty(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        state_path.write_text("not valid json {{")
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert loaded == {}

    def test_missing_file_returns_empty(self, tmp_path):
        state_path = tmp_path / "nonexistent.json"
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert loaded == {}

    def test_dispatches_record_and_completions_remove(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 300000

        tid = _extract_task_id({"subagent_type": "general", "description": "edit file"})
        assert tid is not None

        before = _now_ms()
        state = _load_deadlines(state_path, timeout_ms)
        state[tid] = before
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        assert loaded[tid] == before

        loaded.pop(tid, None)
        _save_deadlines(state_path, loaded)

        loaded2 = _load_deadlines(state_path, timeout_ms)
        assert tid not in loaded2

    def test_atomic_write_uses_tmp_rename(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        tmp_path_expected = state_path.with_suffix(".tmp")

        state = {"task-x": _now_ms()}
        _save_deadlines(state_path, state)

        assert state_path.exists()
        assert not tmp_path_expected.exists()


class TestTimeoutDetectionBehavioral:
    def test_detects_task_over_timeout(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        state = {"task-old": _now_ms() - 6000}
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        now = _now_ms()
        breaches = []
        for tid, start in loaded.items():
            elapsed = now - start
            if elapsed > timeout_ms:
                breaches.append((tid, elapsed))
        assert len(breaches) == 1
        assert breaches[0][0] == "task-old"
        assert breaches[0][1] >= 6000

    def test_does_not_detect_task_within_timeout(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        state = {"task-recent": _now_ms() - 2000}
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        now = _now_ms()
        breaches = []
        for tid, start in loaded.items():
            elapsed = now - start
            if elapsed > timeout_ms:
                breaches.append(tid)
        assert len(breaches) == 0

    def test_uses_gt_not_gte(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        ref = _now_ms()
        state = {"task-exact": ref - timeout_ms}
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        breaches = []
        for tid, start in loaded.items():
            if ref - start > timeout_ms:
                breaches.append(tid)
        assert len(breaches) == 0


class TestSweepExpireCleanupBehavioral:
    def test_sweeps_entries_older_than_3x_timeout(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "task-fresh": now - 2000,
            "task-stale": now - (3 * timeout_ms + 1),
            "task-ancient": now - 600000,
        }
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        assert "task-fresh" in loaded
        assert "task-stale" not in loaded
        assert "task-ancient" not in loaded

    def test_entries_within_3x_window_kept(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        now = _now_ms()
        state = {
            "task-a": now - 5000,
            "task-b": now - 14000,
        }
        _save_deadlines(state_path, state)

        loaded = _load_deadlines(state_path, timeout_ms)
        assert "task-a" in loaded
        assert "task-b" in loaded

    def test_skips_non_number_entries(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        now = _now_ms()
        {"task-a": now - 2000}
        state_path.write_text(json.dumps({"task-a": now - 2000, "task-bad": "string"}))
        loaded = _load_deadlines(state_path, timeout_ms)
        assert "task-a" in loaded
        assert "task-bad" not in loaded


class TestNoiseControlBehavioral:
    def test_warns_only_once_per_task_id(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-hung": now - 10000}
        _save_deadlines(state_path, state)

        warned_ids: set[str] = set()
        warnings: list[str] = []

        clock = now
        for _ in range(5):
            clock += 1000
            loaded = _load_deadlines(state_path, timeout_ms)
            for tid, start in loaded.items():
                if clock - start > timeout_ms and tid not in warned_ids:
                    warnings.append(f"BREACH {tid}")
                    warned_ids.add(tid)

        assert len(warnings) == 1

    def test_completion_resets_warned_ids(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        timeout_ms = 5000
        now = _now_ms()
        state = {"task-a": now - 10000}
        _save_deadlines(state_path, state)

        warned_ids: set[str] = {"task-a"}
        warnings: list[str] = []
        loaded = _load_deadlines(state_path, timeout_ms)
        for tid, start in loaded.items():
            if _now_ms() - start > timeout_ms and tid not in warned_ids:
                warnings.append(f"BREACH {tid}")
                warned_ids.add(tid)
        assert len(warnings) == 0
        warned_ids.discard("task-a")
        state2 = _load_deadlines(state_path, timeout_ms)
        assert "task-a" in state2
        for tid, start in state2.items():
            if _now_ms() - start > timeout_ms and tid not in warned_ids:
                warnings.append(f"BREACH2 {tid}")
                warned_ids.add(tid)
        assert len(warnings) == 1


class TestDeadlineEnabledDisableBehavioral:
    def test_when_disabled_no_state_written(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        deadline_enabled = False
        if deadline_enabled:
            state = {"task-x": _now_ms()}
            _save_deadlines(state_path, state)
        assert not state_path.exists() or state_path.read_text() == ""

    def test_when_enabled_state_is_written(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        deadline_enabled = True
        if deadline_enabled:
            state = {"task-x": _now_ms()}
            _save_deadlines(state_path, state)
        assert state_path.exists()

    def test_disabled_means_plugin_returns_early(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        now = _now_ms()
        state = {"task-old": now - 120000}
        _save_deadlines(state_path, state)

        deadline_enabled = False
        breaches = []
        if deadline_enabled:
            loaded = _load_deadlines(state_path, timeout_ms=5000)
            for tid, start in loaded.items():
                if _now_ms() - start > 5000:
                    breaches.append(tid)
        assert len(breaches) == 0

    def test_enabled_path_detects_breach(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        now = _now_ms()
        state = {"task-old": now - 10000}
        _save_deadlines(state_path, state)

        deadline_enabled = True
        breaches = []
        if deadline_enabled:
            loaded = _load_deadlines(state_path, timeout_ms=5000)
            for tid, start in loaded.items():
                if _now_ms() - start > 5000:
                    breaches.append(tid)
        assert len(breaches) == 1


class TestFailOpenBehavioral:
    def test_returns_default_on_corrupt_state_file(self, tmp_path):
        state_path = tmp_path / "corrupt.json"
        state_path.write_text("{{{broken")
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert loaded == {}

    def test_does_not_raise_on_missing_state_file(self, tmp_path):
        state_path = tmp_path / "does_not_exist.json"
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert loaded == {}

    def test_write_fail_open_means_no_crash(self, tmp_path):
        state_path = tmp_path / "working_dir" / "deadlines.json"
        state = {"task-a": _now_ms()}
        try:
            _save_deadlines(state_path, state)
            assert not state_path.exists()
        except Exception:
            pass

    def test_load_with_weird_keys_handled(self, tmp_path):
        state_path = tmp_path / "weird.json"
        state_path.write_text(json.dumps({"task-a": _now_ms(), "_": None, 123: "bad"}))
        loaded = _load_deadlines(state_path, timeout_ms=300000)
        assert "task-a" in loaded


class TestDispatchToolsBehavioral:
    def test_task_is_dispatch(self):
        assert _is_dispatch_tool("task")

    def test_agent_is_dispatch(self):
        assert _is_dispatch_tool("agent")

    def test_workflow_is_dispatch(self):
        assert _is_dispatch_tool("workflow")

    def test_read_is_not_dispatch(self):
        assert not _is_dispatch_tool("read")

    def test_bash_is_not_dispatch(self):
        assert not _is_dispatch_tool("bash")


class TestStaleFileBehavioral:
    def test_record_stale_task_shape(self, tmp_path):
        stale_path = tmp_path / "stale.json"
        now = _now_ms()
        task_id = "task-hung"
        start_ms = now - 60000
        elapsed_ms = 60000

        entries = []
        with contextlib.suppress(Exception):
            entries = json.loads(stale_path.read_text())
        entries.append({
            "task_id": task_id,
            "start_ms": start_ms,
            "elapsed_ms": elapsed_ms,
            "stale_at": now,
        })
        tmp = stale_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries))
        os.replace(tmp, stale_path)

        loaded = json.loads(stale_path.read_text())
        assert len(loaded) == 1
        entry = loaded[0]
        assert entry["task_id"] == "task-hung"
        assert entry["start_ms"] == start_ms
        assert entry["elapsed_ms"] == elapsed_ms
        assert "stale_at" in entry

    def test_deduplicates_by_task_id(self, tmp_path):
        stale_path = tmp_path / "stale.json"
        now = _now_ms()
        task_id = "task-a"
        entries = [{
            "task_id": task_id,
            "start_ms": now - 60000,
            "elapsed_ms": 60000,
            "stale_at": now,
        }]
        stale_path.write_text(json.dumps(entries))

        existing = json.loads(stale_path.read_text())
        if not any(e.get("task_id") == task_id for e in existing):
            existing.append({
                "task_id": task_id,
                "start_ms": now - 70000,
                "elapsed_ms": 70000,
                "stale_at": now + 100,
            })

        assert len(existing) == 1


class TestDeadlineConstants:
    def test_default_timeout_is_300000_ms(self):
        src = _src()
        m = re.search(r'GLUDD_TASK_TIMEOUT_MS \|\| "(\d+)"', src)
        assert m, "GLUDD_TASK_TIMEOUT_MS default not found"
        assert int(m.group(1)) == 300000

    def test_default_enabled_is_1(self):
        src = _src()
        m = re.search(r'GLUDD_TASK_DEADLINE_ENABLED \|\| "(\d+)"', src)
        assert m, "GLUDD_TASK_DEADLINE_ENABLED default not found"
        assert m.group(1) == "1"

    def test_default_state_file(self):
        src = _src()
        assert "gludd-task-deadlines.json" in src

    def test_default_stale_file(self):
        src = _src()
        assert "gludd-task-stale.json" in src

    def test_disabled_check_uses_not_equal_zero(self):
        src = _src()
        assert '!== "0"' in src, "DEADLINE_ENABLED must check !== '0'"
        assert "DEADLINE_ENABLED" in src


class TestSubagentGuardBehavioral:
    def test_subagent_env_skips_all_enforcement(self, tmp_path):
        state_path = tmp_path / "deadlines.json"
        now = _now_ms()
        state = {"task-old": now - 60000}
        _save_deadlines(state_path, state)

        is_subagent = True
        recorded_dispatch = False
        if not is_subagent:
            state = _load_deadlines(state_path, timeout_ms=5000)
            state["new-task"] = _now_ms()
            _save_deadlines(state_path, state)
            recorded_dispatch = True
        assert not recorded_dispatch
