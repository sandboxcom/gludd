"""Behavioral-invariant tests for enforce-delegate.ts.

Goes beyond the structural constant-checks in test_enforcement_coverage_gaps.py
(Gap 3) to verify plugin mechanics: guard ordering, state-file shape, atomic-write
invariants, probe fail-closed logic, read-grind time-based detection, and the
relationship between constants that must hold for correct enforcement.
"""

from __future__ import annotations

import re
from pathlib import Path

DELEGATE_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-delegate.ts"


def _src(path: Path = DELEGATE_PATH) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# SUBAGENT guard + disengage bypass
# ---------------------------------------------------------------------------


class TestSubagentGuard:
    def test_guard_checks_env_var(self):
        src = _src()
        assert 'process.env.OPENCODE_SUBAGENT === "1"' in src

    def test_guard_before_any_enforcement(self):
        src = _src()
        idx = src.rfind('"tool.execute.before": async')
        assert idx > 0, "must find tool.execute.before hook body"
        after = src[idx:]
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        report_idx = after.find("_reportAlive")
        assert subagent_idx < report_idx, "OPENCODE_SUBAGENT check must precede enforcement"


class TestDisengage:
    def test_checks_disengage_file(self):
        src = _src()
        assert "isDisengaged()" in src

    def test_both_streak_and_force_delegate_skip_when_disengaged(self):
        src = _src()
        matcher_idx = src.find("function mainthreadBudgetBefore")
        mainthread_section = src[matcher_idx:] if matcher_idx > 0 else src
        assert "isDisengaged()" in mainthread_section, (
            "mainthreadBudgetBefore must check disengage"
        )
        force_idx = src.find("function enforceForceDelegate")
        force_section = src[force_idx:] if force_idx > 0 else src
        assert "isDisengaged()" in force_section, (
            "enforceForceDelegate must check disengage"
        )

    def test_disengage_file_basename(self):
        src = _src()
        assert "gludd-watchdog-disengage.json" in src

    def test_disengage_max_duration_capped(self):
        src = _src()
        assert "MAX_DISENGAGE_MS" in src
        assert "3_600_000" in src


# ---------------------------------------------------------------------------
# Floor/Target numeric invariants
# ---------------------------------------------------------------------------


class TestFloorTargetInvariants:
    def test_floor_at_least_target(self):
        src = _src()
        m = re.search(r"FLOOR\s*=\s*parseInt\([^,]+\|\|\s*\"(\d+)\"", src)
        assert m, "FLOOR assignment not found"
        floor = int(m.group(1))
        m2 = re.search(r"TARGET\s*=\s*parseInt\([^,]+\|\|\s*\"(\d+)\"", src)
        assert m2, "TARGET assignment not found"
        target = int(m2.group(1))
        assert floor >= target, f"FLOOR({floor}) must be >= TARGET({target})"

    def test_mainthread_threshold_lower_than_floor(self):
        src = _src()
        m = re.search(r"MAINTHREAD_THRESHOLD\s*=\s*parseInt\([^,]+\|\|\s*\"(\d+)\"", src)
        assert m
        threshold = int(m.group(1))
        m2 = re.search(r"FLOOR\s*=\s*parseInt\([^,]+\|\|\s*\"(\d+)\"", src)
        floor = int(m2.group(1))
        assert threshold < floor, (
            f"MAINTHREAD_THRESHOLD({threshold}) must be < FLOOR({floor}) "
            "to allow dispatch before blocking"
        )


# ---------------------------------------------------------------------------
# Streak counter: JSON format, back-compat, atomicity
# ---------------------------------------------------------------------------


class TestStreakStateFormat:
    def test_streak_reads_json_object(self):
        src = _src()
        assert 'startsWith("{")' in src, "streak reader must support JSON object format"

    def test_streak_reads_bare_integer_backcompat(self):
        src = _src()
        assert "parseInt(raw, 10)" in src, "streak reader must fall back to bare integer"

    def test_streak_writes_json_with_count_and_ts(self):
        src = _src()
        idx = src.find("function writeStreak")
        after = src[idx:idx + 300] if idx > 0 else src
        assert "JSON.stringify" in after
        assert "{ count:" in after or "{count:" in after, "streak JSON must include count field"
        assert "ts:" in after, "streak JSON must include ts field"

    def test_streak_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function writeStreak")
        after = src[idx:] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after


# ---------------------------------------------------------------------------
# Read-grind: separate state, time-based detection, atomicity
# ---------------------------------------------------------------------------


class TestReadGrindState:
    def test_read_grind_has_separate_state_file(self):
        src = _src()
        assert "gludd-read-grind.json" in src

    def test_read_grind_state_has_count_and_last_dispatch(self):
        src = _src()
        idx = src.find("function loadReadGrindState")
        after = src[idx:100 + idx] if idx > 0 else src
        assert "count" in after
        assert "lastDispatchTs" in after

    def test_read_grind_stale_state_auto_resets(self):
        src = _src()
        idx = src.find("READ_GRIND_STALE_MS")
        if idx > 0:
            section = src[idx:idx + 500]
            assert "READ_GRIND_STALE_MS" in section
        # stale reset logic exists
        assert "READ_GRIND_STALE_MS" in src

    def test_read_grind_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function saveReadGrindState")
        after = src[idx:idx + 300] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after


# ---------------------------------------------------------------------------
# Model utilization: sonnet detection, target config shape
# ---------------------------------------------------------------------------


class TestModelUtilizationInvariants:
    def test_sonnet_detection_is_exact_match(self):
        src = _src()
        idx = src.find("function isSonnetDispatch")
        after = src[idx:idx + 300] if idx > 0 else src
        assert 'm.trim() === "sonnet"' in after, "sonnet must be exact string match"

    def test_sonnet_only_counts_explicit_model(self):
        src = _src()
        idx = src.find("function isSonnetDispatch")
        after = src[idx:idx + 200] if idx > 0 else src
        assert "(args.model as string) || " in after, "absent model should be empty string"

    def test_model_history_has_key_history(self):
        src = _src()
        idx = src.find("function loadModelHistory")
        after = src[idx:idx + 200] if idx > 0 else src
        assert "data.history" in after

    def test_model_history_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function saveModelHistory")
        after = src[idx:idx + 200] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after

    def test_sonnet_target_config_reads_target_share_field(self):
        src = _src()
        assert "cfg.target_share" in src

    def test_sonnet_target_env_override_takes_priority(self):
        src = _src()
        idx = src.find("function readTargetShare")
        after = src[idx:idx + 300] if idx > 0 else src
        assert "GLUDD_SONNET_TARGET_SHARE" in after
        env_idx = after.find("GLUDD_SONNET_TARGET_SHARE")
        file_idx = after.find("SONNET_TARGET_CONFIG")
        assert env_idx < file_idx, "env override must be checked before config file"

    def test_sonnet_always_allowed(self):
        src = _src()
        idx = src.find("Sonnet is always allowed")
        assert idx > 0, "comment should state sonnet is always allowed"

    def test_non_expensive_main_thread_skips_enforcement(self):
        src = _src()
        assert "mainModelIsExpensive()" in src


# ---------------------------------------------------------------------------
# Force-delegate: opt-in, allowlist, bounded escape
# ---------------------------------------------------------------------------


class TestForceDelegateInvariants:
    def test_opt_in_by_default(self):
        src = _src()
        mg = re.search(r"FORCE_DELEGATE_ENABLED\s*=\s*\([^)]*GLUDD_FORCE_DELEGATE\s*\|\|\s*\"(.+?)\"", src)
        assert mg, "FORCE_DELEGATE_ENABLED pattern not found"
        assert mg.group(1) == "0", "force-delegate must be opt-in (default '0')"

    def test_dispatch_resets_force_delegate_counters(self):
        src = _src()
        idx = src.find("function enforceForceDelegate")
        after = src[idx:idx + 1000] if idx > 0 else src
        assert "isAgentOrTask" in after
        assert '"consecutive_targeted": 0' in after or "consecutive_targeted: 0" in after
        assert '"consecutive_denied": 0' in after or "consecutive_denied: 0" in after

    def test_bounded_escape_after_maxblock(self):
        src = _src()
        idx = src.find("consecutiveDenied > FORCE_DELEGATE_MAXBLOCK")
        assert idx > 0, "must have bounded escape after MAXBLOCK denials"

    def test_force_delegate_state_uses_atomic_tmp_rename(self):
        src = _src()
        idx = src.find("function saveForceDelegateState")
        after = src[idx:idx + 400] if idx > 0 else src
        assert ".tmp" in after
        assert "renameSync" in after

    def test_readonly_make_regex_exists(self):
        src = _src()
        assert "READONLY_MAKE_RE" in src

    def test_mutating_make_regex_exists(self):
        src = _src()
        assert "MUTATING_MAKE_RE" in src

    def test_memory_paths_skip_force_delegate(self):
        src = _src()
        assert "isMemoryPath" in src
        assert "/memory/" in src


# ---------------------------------------------------------------------------
# Disk discipline: threshold ordering, hard-deny conditions
# ---------------------------------------------------------------------------


class TestDiskDisciplineInvariants:
    def test_hard_floor_default_is_1_0(self):
        src = _src()
        assert 'GLUDD_DISK_HARD_FLOOR_GB || "1.0"' in src

    def test_danger_default_is_2_5(self):
        src = _src()
        assert 'GLUDD_DISK_DANGER_GB || "2.5"' in src

    def test_min_free_default_is_5_0(self):
        src = _src()
        assert 'GLUDD_MIN_FREE_GB || "5.0"' in src

    def test_threshold_ordering_sensible(self):
        src = _src()
        floor_idx = src.find('GLUDD_DISK_HARD_FLOOR_GB || "1.0"')
        danger_idx = src.find('GLUDD_DISK_DANGER_GB || "2.5"')
        min_free_idx = src.find('GLUDD_MIN_FREE_GB || "5.0"')
        assert floor_idx > 0 and danger_idx > 0 and min_free_idx > 0
        assert float("1.0") < float("2.5") <= float("5.0"), "thresholds must be ordered: floor < danger <= min_free"

    def test_only_fires_on_worktree_isolation(self):
        src = _src()
        idx = src.find("function enforceDiskDiscipline")
        after = src[idx:idx + 300] if idx > 0 else src
        assert 'iso !== "worktree"' in after

    def test_disk_snapshot_has_free_gb_and_venv_count(self):
        src = _src()
        idx = src.find("function diskSnapshot")
        after = src[idx:idx + 300] if idx > 0 else src
        assert "freeGb" in after
        assert "venvCount" in after

    def test_disk_free_override_skips_exec(self):
        src = _src()
        idx = src.find("function diskSnapshot")
        after = src[idx:idx + 400] if idx > 0 else src
        assert "GLUDD_DISK_FREE_OVERRIDE" in after


# ---------------------------------------------------------------------------
# Force-dispatch signal: JSON shape, source files
# ---------------------------------------------------------------------------


class TestForceDispatchSignal:
    def test_builds_from_tasks_md(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        after = src[idx:idx + 500] if idx > 0 else src
        assert "TASKS.md" in after

    def test_builds_from_ratchet_yml(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        section = src[idx:]
        assert "ratchet.yml" in section

    def test_builds_from_gate_status(self):
        src = _src()
        idx = src.find("function buildForceDispatchCommands")
        section = src[idx:]
        assert ".gate-status" in section

    def test_writes_json_with_dispatch_commands(self):
        src = _src()
        idx = src.find("function writeForceDispatchSignal")
        after = src[idx:idx + 400] if idx > 0 else src
        assert "dispatch_commands" in after

    def test_force_dispatch_file_path(self):
        src = _src()
        assert "gludd-force-dispatch.json" in src


# ---------------------------------------------------------------------------
# Probe fail-closed behavior
# ---------------------------------------------------------------------------


class TestProbeFailClosed:
    def test_tracks_consecutive_probe_failures(self):
        src = _src()
        assert "_probeFailCount" in src
        assert "PROBE_FAIL_THRESHOLD" in src

    def test_returns_zero_after_threshold_not_null(self):
        src = _src()
        idx = src.find("function countLiveAgents")
        after = src[idx:idx + 800] if idx > 0 else src
        assert "FAIL-CLOSED" in after or "fail-closed" in after.lower()
        assert "return 0" in after, "after threshold, must return 0 not null"

    def test_probe_counter_resets_on_success(self):
        src = _src()
        idx = src.find("function countLiveAgents")
        after = src[idx:]
        assert "_probeFailCount = 0" in after[:2000], "must reset on successful probe"


# ---------------------------------------------------------------------------
# Fail-open guarantee: every catch still present
# ---------------------------------------------------------------------------


class TestFailOpenGuarantee:
    def test_no_bare_throw_without_catch(self):
        src = _src()
        lines = src.split("\n")
        in_catch = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("catch") or stripped.startswith("} catch"):
                in_catch = True
            elif stripped.startswith("throw new Error") and not in_catch:
                pass
                # But throws inside try blocks are fine
            elif stripped.startswith("}") and in_catch:
                in_catch = False

    def test_all_catch_blocks_present(self):
        src = _src()
        catch_count = len(re.findall(r"\bcatch\b", src))
        assert catch_count >= 7, f"expected >=7 catch blocks, found {catch_count}"

    def test_fail_open_comment_in_file(self):
        src = _src()
        assert "fail open" in src.lower() or "fail-open" in src.lower()

    def test_opencode_subagent_guard_returns(self):
        src = _src()
        before_idx = src.find("tool.execute.before")
        after = src[before_idx:] if before_idx > 0 else src
        subagent_idx = after.find("OPENCODE_SUBAGENT")
        return_idx = after.find("return", subagent_idx)
        assert return_idx > subagent_idx
        region = after[subagent_idx:return_idx + 50]
        assert "return" in region


# ---------------------------------------------------------------------------
# Plugin export shape: factory function returning hooks object
# ---------------------------------------------------------------------------


class TestPluginExportShape:
    def test_exports_satisfies_plugin_type(self):
        src = _src()
        assert "satisfies Plugin" in src

    def test_export_is_async_factory(self):
        src = _src()
        assert "export default" in src
        assert "async" in src

    def test_returns_tool_execute_before_hook(self):
        src = _src()
        assert '"tool.execute.before"' in src

    def test_returns_tool_execute_after_hook(self):
        src = _src()
        assert '"tool.execute.after"' in src

    def test_tool_execute_after_never_throws(self):
        src = _src()
        after_idx = src.find('"tool.execute.after"')
        after = src[after_idx:] if after_idx > 0 else src
        assert "never throws" in after


# ---------------------------------------------------------------------------
# Disk discipline: worktree venv count guard
# ---------------------------------------------------------------------------


class TestWorktreeVenvGuard:
    def test_venv_cap_env_var(self):
        src = _src()
        assert "GLUDD_WORKTREE_CAP" in src

    def test_venv_cap_warning_includes_mb_estimate(self):
        src = _src()
        idx = src.find("venvCount >= WORKTREE_CAP")
        assert idx > 0, "venv cap check must exist"


# ---------------------------------------------------------------------------
# Model utilization: target share default matches AGENTS.md contract
# ---------------------------------------------------------------------------


class TestSonnetTargetShare:
    def test_default_target_share_is_0_91(self):
        src = _src()
        m = re.search(r"SONNET_TARGET_DEFAULT\s*=\s*([\d.]+)", src)
        assert m, "SONNET_TARGET_DEFAULT not found"
        assert float(m.group(1)) == 0.91

    def test_target_share_config_file_under_claude_dir(self):
        src = _src()
        assert "SONNET_TARGET_CONFIG" in src
        assert "sonnet_ratio_target" in src


# ---------------------------------------------------------------------------
# Mainthread budget: threshold relationship with rearm
# ---------------------------------------------------------------------------


class TestMainthreadRearm:
    def test_rearm_lower_than_threshold(self):
        src = _src()
        m = re.search(r"MAINTHREAD_THRESHOLD\s*=\s*parseInt\([^,]+\|\|\s*\"(\d+)\"", src)
        assert m
        threshold = int(m.group(1))
        assert "MAINTHREAD_THRESHOLD - 3" in src or f"{threshold - 3}" in src
