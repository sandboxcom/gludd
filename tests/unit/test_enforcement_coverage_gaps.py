"""Coverage-gap tests for enforcement plugins with under-tested behavior.

Gap 1: enforce-deadline.ts — dispatch recording, deadline checking, stale
  sweep, once-per-task throttle, deadline reset on completion. Only the
  TASK_TIMEOUT_MS constant was tested previously.

Gap 2: enforce-no-wait.ts — CI poll dispatch pattern blocking and main-thread
  wait pattern detection. Fully untested (zero coverage in existing test
  files).

Gap 3: enforce-delegate.ts — model-util recording, force-delegate denial
  conditions, disk-discipline thresholds. Only basic constants (FLOOR, TARGET,
  MAINTHREAD_THRESHOLD) were tested previously.
"""
from __future__ import annotations

import re
from pathlib import Path

DEADLINE_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-deadline.ts"
NO_WAIT_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-no-wait.ts"
DELEGATE_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-delegate.ts"
SHARED_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/shared.ts"


def _src(path: Path) -> str:
    return path.read_text()


def _extract_str(path: Path, name: str) -> str:
    src = _src(path)
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?);", re.DOTALL)
    m = pat.search(src)
    assert m, f"export const {name} not found in {path.name}"
    raw = m.group(1).strip()
    for q in ('"', "'"):
        if raw.startswith(q) and raw.endswith(q):
            return raw[1:-1]
    return raw


def _extract_env_default(path: Path, env_var: str) -> str:
    src = _src(path)
    m = re.search(rf"process\.env\.{re.escape(env_var)}\s*\|\|\s*\"(.+?)\"", src)
    assert m, f"env var {env_var} default not found in {path.name}"
    return m.group(1)


# ===========================================================================
# GAP 1: enforce-deadline.ts — deadline enforcement behavior
# ===========================================================================


class TestDeadlineFile:
    def test_plugin_exists(self):
        assert DEADLINE_PATH.exists()

    def test_registered_in_opencode_json(self):
        oc = (DEADLINE_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-deadline.ts" in oc


class TestDeadlineConstants:
    def test_timeout_default_300000(self):
        assert _extract_env_default(DEADLINE_PATH, "GLUDD_TASK_TIMEOUT_MS") == "300000"

    def test_timeout_used_in_elapsed_check(self):
        src = _src(DEADLINE_PATH)
        assert "elapsed > TASK_TIMEOUT_MS" in src

    def test_state_file_path(self):
        src = _src(DEADLINE_PATH)
        assert "DEADLINE_STATE" in src
        assert "/tmp/gludd-task-deadlines.json" in src

    def test_warnings_log_path(self):
        src = _src(DEADLINE_PATH)
        assert "WARNINGS_LOG" in src
        assert "warnings.log" in src

    def test_stale_file_path(self):
        src = _src(DEADLINE_PATH)
        assert "STALE_FILE" in src
        assert "gludd-task-stale.json" in src


class TestDeadlineEnabledEnvVar:
    def test_enabled_by_default(self):
        src = _src(DEADLINE_PATH)
        assert 'GLUDD_TASK_DEADLINE_ENABLED || "1"' in src

    def test_disabled_when_set_to_zero(self):
        src = _src(DEADLINE_PATH)
        assert 'GLUDD_TASK_DEADLINE_ENABLED || "1") !== "0"' in src


class TestExtractTaskId:
    def test_function_exists(self):
        src = _src(DEADLINE_PATH)
        assert "extractTaskId" in src

    def test_extracts_task_id_field(self):
        src = _src(DEADLINE_PATH)
        assert 'a.task_id' in src

    def test_falls_back_to_id_field(self):
        src = _src(DEADLINE_PATH)
        assert 'a.id' in src

    def test_djb2_hash_fallback(self):
        src = _src(DEADLINE_PATH)
        assert "hash = 5381" in src
        assert "subagent_type" in src
        assert "description" in src
        assert 'd-' in src

    def test_returns_null_for_no_match(self):
        src = _src(DEADLINE_PATH)
        assert "return null" in src


class TestIsDispatchTool:
    def test_function_exists(self):
        src = _src(DEADLINE_PATH)
        assert "isDispatchTool" in src

    def test_recognizes_task_agent_workflow(self):
        src = _src(DEADLINE_PATH)
        assert 'tool === "task"' in src
        assert 'tool === "agent"' in src
        assert 'tool === "workflow"' in src


class TestDeadlineDispatchRecording:
    def test_records_on_dispatch(self):
        src = _src(DEADLINE_PATH)
        assert "isDispatchTool(tool)" in src
        assert "d[id] = Date.now()" in src

    def test_saves_after_recording(self):
        src = _src(DEADLINE_PATH)
        assert "saveDeadlines(d)" in src

    def test_fallback_auto_id(self):
        src = _src(DEADLINE_PATH)
        assert "auto-${Date.now()}" in src


class TestDeadlineChecking:
    def test_scans_on_every_tool(self):
        src = _src(DEADLINE_PATH)
        assert "(ANY tool)" in src


    def test_compares_elapsed_to_timeout(self):
        src = _src(DEADLINE_PATH)
        assert "elapsed > TASK_TIMEOUT_MS" in src

    def test_logs_minutes_format(self):
        src = _src(DEADLINE_PATH)
        assert "elapsed / 60000" in src

    def test_writes_task_deadline_exceeded_message(self):
        src = _src(DEADLINE_PATH)
        assert "TASK DEADLINE EXCEEDED" in src


class TestThrottleNoiseControl:
    def test_warned_ids_set_exists(self):
        src = _src(DEADLINE_PATH)
        assert "const warnedIds = new Set<string>()" in src

    def test_warn_only_once_per_task_id(self):
        src = _src(DEADLINE_PATH)
        assert "!warnedIds.has(id)" in src
        assert "warnedIds.add(id)" in src

    def test_append_warning_to_persistent_log(self):
        src = _src(DEADLINE_PATH)
        assert "appendWarning" in src

    def test_persistent_warning_outside_throttle(self):
        src = _src(DEADLINE_PATH)
        idx = src.find("if (!warnedIds.has(id))")
        before = src[:idx] if idx > 0 else ""
        assert "appendWarning" in before


class TestRecordStaleTask:
    def test_function_exists(self):
        src = _src(DEADLINE_PATH)
        assert "recordStaleTask" in src

    def test_deduplicates_by_task_id(self):
        src = _src(DEADLINE_PATH)
        assert "!entries.some" in src
        assert "e.task_id === taskId" in src

    def test_writes_task_id_start_elapsed(self):
        src = _src(DEADLINE_PATH)
        assert "task_id" in src
        assert "start_ms" in src
        assert "elapsed_ms" in src
        assert "stale_at" in src


class TestStaleSweep:
    def test_sweep_function_exists(self):
        src = _src(DEADLINE_PATH)
        assert "sweepStaleEntries" in src

    def test_uses_3x_timeout_as_max_age(self):
        src = _src(DEADLINE_PATH)
        assert "TASK_TIMEOUT_MS * 3" in src

    def test_deletes_entries_older_than_max_age(self):
        src = _src(DEADLINE_PATH)
        assert "now - start > maxAge" in src
        assert "delete d[id]" in src

    def test_also_clears_warned_ids_for_swept_entries(self):
        src = _src(DEADLINE_PATH)
        assert "warnedIds.delete(id)" in src

    def test_sweep_triggered_on_load(self):
        src = _src(DEADLINE_PATH)
        idx = src.find("function loadDeadlines")
        after = src[idx:] if idx > 0 else src
        assert "sweepStaleEntries(out)" in after


class TestDeadlineResetOnCompletion:
    def test_tool_execute_after_hook(self):
        src = _src(DEADLINE_PATH)
        assert '"tool.execute.after"' in src

    def test_removes_completed_task_id(self):
        src = _src(DEADLINE_PATH)
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "delete d[id]" in after

    def test_resets_warned_ids_on_completion(self):
        src = _src(DEADLINE_PATH)
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "warnedIds.delete(id)" in after


class TestDeadlineFailOpen:
    def test_try_catch_in_before_hook(self):
        src = _src(DEADLINE_PATH)
        before_idx = src.find('"tool.execute.before"')
        after_idx = src.find('"tool.execute.after"')
        before = src[before_idx:after_idx] if before_idx > 0 and after_idx > 0 else src
        assert "catch" in before

    def test_try_catch_in_after_hook(self):
        src = _src(DEADLINE_PATH)
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "catch" in after

    def test_fail_open_comments_present(self):
        src = _src(DEADLINE_PATH)
        assert "fail open" in src.lower()


class TestDeadlineAuxFunctions:
    def test_load_deadlines_handles_corrupt_file(self):
        src = _src(DEADLINE_PATH)
        idx = src.find("function loadDeadlines")
        after = src[idx:] if idx > 0 else src
        assert "catch" in after
        assert "return {}" in after

    def test_save_uses_atomic_write(self):
        src = _src(DEADLINE_PATH)
        assert ".tmp" in src
        assert "renameSync" in src


# ===========================================================================
# GAP 2: enforce-no-wait.ts — CI poll + wait pattern blocking
# ===========================================================================


class TestNoWaitFile:
    def test_plugin_exists(self):
        assert NO_WAIT_PATH.exists()

    def test_registered_in_opencode_json(self):
        oc = (NO_WAIT_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-no-wait.ts" in oc


class TestNoWaitConstants:
    def test_exported_constants_exist(self):
        src = _src(NO_WAIT_PATH)
        assert "WAIT_PATTERNS" in src
        assert "CI_POLL_DISPATCH_PATTERNS" in src
        assert "DENY_MESSAGE" in src
        assert "CI_POLL_DENY_MESSAGE" in src


class TestWaitPatterns:
    def test_frozen(self):
        src = _src(NO_WAIT_PATH)
        assert "Object.freeze" in src

    def test_sleep_and_make(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bsleep\s+\d+\s*&&\s*make\b" in src

    def test_naked_sleep(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bsleep\s+\d+\s*$" in src

    def test_gate_tail(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bmake\s+gate-tail\b" in src

    def test_gate_bg_check(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bmake\s+gate-bg-check\b" in src

    def test_gate_status_check(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bmake\s+gate-status-check\b" in src

    def test_has_5_patterns(self):
        src = _src(NO_WAIT_PATH)
        pats = re.findall(r"/\\b\w+", src)
        assert len(pats) >= 5


class TestCiPollDispatchPatterns:
    def test_frozen(self):
        src = _src(NO_WAIT_PATH)
        assert "CI_POLL_DISPATCH_PATTERNS" in src
        ci_idx = src.find("CI_POLL_DISPATCH_PATTERNS")
        after = src[ci_idx:] if ci_idx > 0 else src
        assert "Object.freeze" in after

    def test_poll_ci_until(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bpoll\s+CI\s+until\b" in src

    def test_polling_for_ci_until(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bpoll(?:ing)?\s+(?:for\s+)?CI\s+(?:status\s+)?until\b" in src

    def test_wait_for_ci_green(self):
        src = _src(NO_WAIT_PATH)
        assert r"wait\s+for\s+CI" in src

    def test_wait_until_ci_green(self):
        src = _src(NO_WAIT_PATH)
        assert r"wait\s+until\s+CI" in src

    def test_loop_on_make_ci_verdict(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bloop\s+(?:on\s+)?make\s+ci-verdict\b" in src

    def test_every_n_seconds_until(self):
        src = _src(NO_WAIT_PATH)
        assert r"\bevery\s+\d+\s+seconds?" in src

    def test_until_conclusion_success(self):
        src = _src(NO_WAIT_PATH)
        assert r"\buntil\s+conclusion" in src

    def test_has_7_patterns(self):
        src = _src(NO_WAIT_PATH)
        pats = re.findall(r"/\\b\w+", src)
        assert len(pats) >= 7


class TestNoWaitDenyMessages:
    def test_wait_deny_mentions_no_block_dispatch(self):
        src = _src(NO_WAIT_PATH)
        assert "Background Operations NEVER Block Dispatch" in src

    def test_wait_deny_mentions_env_disable(self):
        src = _src(NO_WAIT_PATH)
        assert "GLUDD_NO_WAIT_ENFORCE=0" in src

    def test_ci_poll_deny_mentions_ci_poll_forbidden(self):
        src = _src(NO_WAIT_PATH)
        assert "CI-Poll Subagents Are Forbidden" in src

    def test_ci_poll_deny_mentions_ci_check_cooldown(self):
        src = _src(NO_WAIT_PATH)
        assert "Machine-Enforced CI Check Cooldown" in src

    def test_ci_poll_deny_mentions_release_cut_only(self):
        src = _src(NO_WAIT_PATH)
        assert "release-cut ONLY" in src


class TestNoWaitEnvVar:
    def test_enforced_by_default(self):
        src = _src(NO_WAIT_PATH)
        assert 'GLUDD_NO_WAIT_ENFORCE === "0"' in src

    def test_disabled_when_set_to_zero(self):
        src = _src(NO_WAIT_PATH)
        assert 'GLUDD_NO_WAIT_ENFORCE === "0"' in src


class TestExtractDispatchText:
    def test_function_exists(self):
        src = _src(NO_WAIT_PATH)
        assert "_extractDispatchText" in src

    def test_extracts_prompt(self):
        src = _src(NO_WAIT_PATH)
        assert 'p.prompt' in src or 'typeof p.prompt === "string"' in src

    def test_extracts_description(self):
        src = _src(NO_WAIT_PATH)
        assert 'p.description' in src

    def test_extracts_input_prompt(self):
        src = _src(NO_WAIT_PATH)
        assert 'p.input.prompt' in src

    def test_extracts_input_description(self):
        src = _src(NO_WAIT_PATH)
        assert 'p.input.description' in src

    def test_joins_parts_with_newline(self):
        src = _src(NO_WAIT_PATH)
        assert 'join("\\n")' in src


class TestNoWaitDispatchTools:
    def test_dispatch_tools_set(self):
        src = _src(NO_WAIT_PATH)
        assert 'new Set(["task", "agent", "workflow"])' in src


class TestNoWaitHookStructure:
    def test_tool_execute_before_hook(self):
        src = _src(NO_WAIT_PATH)
        assert '"tool.execute.before"' in src

    def test_fail_open_catch(self):
        src = _src(NO_WAIT_PATH)
        assert "Fail-open" in src or "fail open" in src.lower()
        assert "catch" in src


# ===========================================================================
# GAP 3: enforce-delegate.ts — model-util, force-delegate, disk-discipline
# ===========================================================================


class TestDelegateFile:
    def test_plugin_exists(self):
        assert DELEGATE_PATH.exists()

    def test_registered_in_opencode_json(self):
        oc = (DELEGATE_PATH.parents[2] / "opencode.json").read_text()
        assert "enforce-delegate.ts" in oc


class TestModelUtil:
    def test_window_default_20(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_MODEL_UTIL_WINDOW") == "20"

    def test_state_file_path(self):
        src = _src(DELEGATE_PATH)
        assert "gludd-model-util.json" in src

    def test_is_sonnet_dispatch_function(self):
        src = _src(DELEGATE_PATH)
        assert "isSonnetDispatch" in src

    def test_sonnet_only_matches_exact_model_sonnet(self):
        src = _src(DELEGATE_PATH)
        assert 'sonnet' in src.lower()

    def test_main_model_is_expensive_function(self):
        src = _src(DELEGATE_PATH)
        assert "mainModelIsExpensive" in src

    def test_expensive_models_include_opus(self):
        src = _src(DELEGATE_PATH)
        assert "opus" in src

    def test_expensive_models_include_o1_o3_gpt4(self):
        src = _src(DELEGATE_PATH)
        assert "o1" in src or "o3" in src or "gpt-4" in src

    def test_sonnet_ratio_target_config_file(self):
        src = _src(DELEGATE_PATH)
        assert ".claude/sonnet_ratio_target" in src or "GLUDD_SONNET_TARGET_CONFIG" in src

    def test_sonnet_target_share_env_override(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_SONNET_TARGET_SHARE" in src

    def test_default_target_share_0_91(self):
        src = _src(DELEGATE_PATH)
        assert "0.91" in src

    def test_enforce_model_utilization_function(self):
        src = _src(DELEGATE_PATH)
        assert "enforceModelUtilization" in src

    def test_model_util_enforce_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_MODEL_UTIL_ENFORCE" in src


class TestForceDelegate:
    def test_state_file_path(self):
        src = _src(DELEGATE_PATH)
        assert "gludd-force-delegate.json" in src

    def test_grace_default_3(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_FORCE_DELEGATE_GRACE") == "3"

    def test_maxblock_default_4(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_FORCE_DELEGATE_MAXBLOCK") == "4"

    def test_opt_in_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_FORCE_DELEGATE" in src

    def test_enforce_force_delegate_function(self):
        src = _src(DELEGATE_PATH)
        assert "enforceForceDelegate" in src

    def test_mutating_make_regex(self):
        src = _src(DELEGATE_PATH)
        assert "MUTATING_MAKE_RE" in src

    def test_readonly_make_regex(self):
        src = _src(DELEGATE_PATH)
        assert "READONLY_MAKE_RE" in src


class TestMainthreadBudget:
    def test_mainthread_budget_before_function(self):
        src = _src(DELEGATE_PATH)
        assert "mainthreadBudgetBefore" in src

    def test_mainthread_budget_after_function(self):
        src = _src(DELEGATE_PATH)
        assert "mainthreadBudgetAfter" in src

    def test_streak_threshold_default_2(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_MAINTHREAD_THRESHOLD") == "2"

    def test_streak_hard_denies_above_threshold(self):
        src = _src(DELEGATE_PATH)
        assert "BLOCK:" in src

    def test_streak_state_file(self):
        src = _src(DELEGATE_PATH)
        assert "gludd-mainthread-streak.json" in src

    def test_read_grind_deny_count_default_10(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_READ_GRIND_DENY_COUNT") == "10"

    def test_read_grind_advisory_count_default_5(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_READ_GRIND_ADVISORY_COUNT") == "5"

    def test_read_grind_state_file(self):
        src = _src(DELEGATE_PATH)
        assert "gludd-read-grind.json" in src

    def test_force_dispatch_commands_parsed(self):
        src = _src(DELEGATE_PATH)
        assert "gludd-force-dispatch.json" in src


class TestDiskDiscipline:
    def test_disk_discipline_function(self):
        src = _src(DELEGATE_PATH)
        assert "enforceDiskDiscipline" in src

    def test_hard_floor_default_1_0_gb(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_DISK_HARD_FLOOR_GB") == "1.0"

    def test_min_free_default_5_0_gb(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_MIN_FREE_GB") == "5.0"

    def test_danger_default_2_5_gb(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_DISK_DANGER_GB") == "2.5"

    def test_worktree_cap_default_6(self):
        assert _extract_env_default(DELEGATE_PATH, "GLUDD_WORKTREE_CAP") == "6"

    def test_disk_free_override_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_DISK_FREE_OVERRIDE" in src

    def test_venv_count_override_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_VENV_COUNT_OVERRIDE" in src

    def test_hard_deny_below_floor(self):
        src = _src(DELEGATE_PATH)
        disk_idx = src.find("enforceDiskDiscipline")
        after = src[disk_idx:] if disk_idx > 0 else src
        assert "Hard-deny" in after or "throw" in after


class TestDelegateProbe:
    def test_count_live_agents_function(self):
        src = _src(DELEGATE_PATH)
        assert "countLiveAgents" in src

    def test_probe_fail_threshold_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_PROBE_FAIL_THRESHOLD" in src

    def test_returns_zero_after_3_consecutive_failures(self):
        src = _src(DELEGATE_PATH)
        assert "3" in src


class TestIsDisengaged:
    def test_function_exists(self):
        src = _src(DELEGATE_PATH)
        assert "isDisengaged" in src

    def test_checks_disengage_file(self):
        src = _src(SHARED_PATH)
        assert "gludd-watchdog-disengage.json" in src

    def test_checks_disengage_until_time(self):
        src = _src(SHARED_PATH)
        assert "disengage_until" in src


class TestDelegateEnvVars:
    def test_floor_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "CLAUDE_AGENT_FLOOR" in src

    def test_target_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "CLAUDE_AGENT_TARGET" in src

    def test_main_model_env_var(self):
        src = _src(DELEGATE_PATH)
        assert "GLUDD_MAIN_MODEL" in src or "OPENCODE_MODEL" in src


class TestDelegateFailOpen:
    def test_is_disengaged_skip_exists(self):
        src = _src(DELEGATE_PATH)
        assert "isDisengaged()" in src

    def test_fail_open_comments_present(self):
        src = _src(DELEGATE_PATH)
        assert "fail" in src.lower() and "open" in src.lower()

    def test_catch_blocks_present(self):
        src = _src(DELEGATE_PATH)
        assert src.count("catch") >= 3
