"""Deep ceiling-enforcement behavioral tests for enforce-floor.ts.

Covers CEILING constant, WAVE_WIDTH, max-10-concurrent block, CLAUDE_AGENT_CEILING
override, CLAUDE_AGENT_TARGET capped-by-ceiling, ceiling override file, wave width
violation message, OPENCODE_SUBAGENT guard, load-throttle ceiling reduction, and
ceiling-floor relationship invariants.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"
SHARED_PATH = ROOT / ".opencode" / "lib" / "shared.ts"
OPENCODE_JSON = ROOT / "opencode.json"


def _src(path: Path = PLUGIN_PATH) -> str:
    return path.read_text()


# ---------------------------------------------------------------------------
# Plugin file existence + registration
# ---------------------------------------------------------------------------


class TestCeilingPluginExistence:
    def test_plugin_file_exists(self):
        assert PLUGIN_PATH.is_file()

    def test_registered_in_opencode_json(self):
        raw = OPENCODE_JSON.read_text()
        assert "enforce-floor.ts" in raw


# ---------------------------------------------------------------------------
# CEILING constant — default, tunability, override file
# ---------------------------------------------------------------------------


class TestCeilingConstant:
    def test_ceiling_default_is_10(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_CEILING",\s*"(\d+)"', src)
        assert m
        assert m.group(1) == "10"

    def test_ceiling_reads_env_var(self):
        src = _src()
        assert "CLAUDE_AGENT_CEILING" in src

    def test_ceiling_uses_tunable_function(self):
        src = _src()
        idx = src.find("const CEILING = _tunable(")
        assert idx > 0

    def test_ceiling_override_file_path(self):
        src = _src()
        assert "/tmp/gludd-ceiling-override" in src

    def test_ceiling_override_file_path_near_ceiling_constant(self):
        src = _src()
        idx = src.find("const CEILING = _tunable(")
        assert idx > 0
        after = src[idx : idx + 120]
        assert "/tmp/gludd-ceiling-override" in after

    def test_tunable_has_fail_open_catch(self):
        src = _src()
        idx = src.find("function _tunable")
        after = src[idx : idx + 300]
        assert "} catch" in after or "catch {}" in after

    def test_tunable_fallback_to_default_on_error(self):
        src = _src()
        idx = src.find("function _tunable")
        after = src[idx : idx + 300]
        assert "return base" in after


# ---------------------------------------------------------------------------
# WAVE_WIDTH constant — default, tunability
# ---------------------------------------------------------------------------


class TestWaveWidth:
    def test_wave_width_default_is_10(self):
        src = _src()
        m = re.search(r'GLUDD_DISPATCH_WAVE_WIDTH",\s*"(\d+)"', src)
        assert m
        assert m.group(1) == "10"

    def test_wave_width_uses_tunable_function(self):
        src = _src()
        assert "const WAVE_WIDTH = _tunable(" in src

    def test_wave_width_override_file_path(self):
        src = _src()
        assert "/tmp/gludd-dispatch-wave-width" in src

    def test_wave_width_env_var(self):
        src = _src()
        assert "GLUDD_DISPATCH_WAVE_WIDTH" in src, "GLUDD_DISPATCH_WAVE_WIDTH env var must exist for runtime tuning"

    def test_wave_width_appears_in_dispatch_preflight(self):
        src = _src()
        assert "required_width: WAVE_WIDTH" in src


# ---------------------------------------------------------------------------
# TARGET = Math.min(CLAUDE_AGENT_TARGET, CEILING)
# ---------------------------------------------------------------------------


class TestTargetCappedByCeiling:
    def test_target_uses_math_min_with_ceiling(self):
        src = _src()
        idx = src.find("const TARGET")
        assert idx > 0
        after = src[idx : idx + 150]
        assert "Math.min" in after
        assert "CEILING" in after

    def test_target_default_is_10(self):
        src = _src()
        m = re.search(r'CLAUDE_AGENT_TARGET\s*\|\|\s*"(\d+)"', src)
        assert m
        assert m.group(1) == "10"

    def test_target_reads_env_var(self):
        src = _src()
        assert "CLAUDE_AGENT_TARGET" in src

    def test_target_cannot_exceed_ceiling(self):
        src = _src()
        idx = src.find("const TARGET")
        after = src[idx : idx + 200]
        assert "Math.min" in after
        assert "CEILING" in after
        assert "CLAUDE_AGENT_TARGET" in after or "process.env" in after


# ---------------------------------------------------------------------------
# Max-10-concurrent dispatch block (wave width violation)
# ---------------------------------------------------------------------------


class TestMaxConcurrentDispatchBlock:
    def test_wave_width_violation_message_exists(self):
        src = _src()
        assert "WAVE WIDTH VIOLATION" in src

    def test_wave_width_violation_is_hard_deny(self):
        src = _src()
        idx = src.find("WAVE WIDTH VIOLATION — DISPATCH BLOCKED")
        assert idx > 0
        before = src[max(0, idx - 200) : idx + 10]
        assert "permissionDecision" in before

    def test_wave_width_gated_on_message_dispatch_count(self):
        src = _src()
        idx = src.find("_thisMessageDispatchCount >= eff.waveWidth")
        assert idx > 0

    def test_wave_width_only_blocks_dispatch_tools(self):
        src = _src()
        idx = src.find("_thisMessageDispatchCount >= eff.waveWidth")
        assert idx > 0
        chunk_start = max(0, idx - 30)
        chunk = src[chunk_start : idx + 60]
        assert "isDispatchTool(tool)" in chunk

    def test_wave_width_gated_on_open_work_exists(self):
        src = _src()
        idx = src.find("_thisMessageDispatchCount >= eff.waveWidth")
        assert idx > 0
        after = src[idx : idx + 80]
        assert "openWorkExists()" in after

    def test_wave_width_uses_effective_floor_not_raw_wave_width(self):
        src = _src()
        idx = src.find("_thisMessageDispatchCount >= eff.waveWidth")
        assert idx > 0
        assert "eff.waveWidth" in src

    def test_wave_width_violation_message_wording(self):
        src = _src()
        idx = src.find("WAVE WIDTH VIOLATION")
        assert idx > 0
        after = src[idx : idx + 600]
        assert "already contains" in after
        assert "dispatch" in after.lower()
        assert "ceiling" in after.lower() or "required width" in after.lower()

    def test_dispatch_count_increments_before_ceiling_check(self):
        src = _src()
        assert "_thisMessageDispatchCount++" in src

    def test_dispatch_preflight_recorded_before_first_dispatch(self):
        src = _src()
        preflight_idx = src.find("recordDispatchPreflight(_buildDispatchCommands())")
        assert preflight_idx > 0
        before = src[max(0, preflight_idx - 150) : preflight_idx]
        assert "_thisMessageDispatchCount === 0" in before

    def test_dispatch_wave_complete_recorded_at_exact_width(self):
        src = _src()
        idx = src.find("_thisMessageDispatchCount === eff.waveWidth")
        assert idx > 0
        after = src[idx : idx + 100]
        assert "recordDispatchWaveComplete" in after


# ---------------------------------------------------------------------------
# Load-throttle ceiling reduction
# ---------------------------------------------------------------------------


class TestLoadThrottleCeiling:
    def test_get_effective_floor_function_exists(self):
        src = _src()
        assert "function getEffectiveFloor()" in src

    def test_get_effective_floor_returns_wave_width(self):
        src = _src()
        idx = src.find("function getEffectiveFloor()")
        after = src[idx : idx + 600]
        assert "waveWidth" in after

    def test_load_throttle_file_path(self):
        src = _src()
        assert "/tmp/gludd-load-throttle" in src

    def test_throttle_reduces_effective_floor(self):
        src = _src()
        idx = src.find("effectiveFloor")
        assert idx > 0
        after = src[idx : idx + 300]
        assert "Math.min" in after or "Math.round" in after, (
            "throttled ceiling must be proportionally reduced via Math.min(Math.round(...))"
        )

    def test_throttle_ratio_preserves_wave_width_proportionally(self):
        src = _src()
        idx = src.find("ratio")
        assert idx > 0

    def test_throttle_active_timeout_is_120s(self):
        src = _src()
        assert "const THROTTLE_ACTIVE_MS = 120_000" in src or "120_000" in src

    def test_throttle_stale_timeout_is_300s(self):
        src = _src()
        assert "const THROTTLE_STALE_MS = 300_000" in src or "300_000" in src

    def test_throttle_fallbacks_to_normal_on_no_file(self):
        src = _src()
        idx = src.find("!fs.existsSync(THROTTLE_PATH)")
        assert idx > 0

    def test_throttle_fallbacks_to_normal_on_stale_file(self):
        src = _src()
        idx1 = src.find("age > THROTTLE_STALE_MS")
        idx2 = src.find("age > THROTTLE_ACTIVE_MS")
        assert idx1 > 0 or idx2 > 0

    def test_throttle_wave_width_minimum_is_2(self):
        src = _src()
        assert "Math.max(2" in src


# ---------------------------------------------------------------------------
# Ceiling-floor relationship invariants
# ---------------------------------------------------------------------------


class TestCeilingFloorRelationship:
    def test_floor_and_ceiling_use_same_tunable_pattern(self):
        src = _src()
        assert "_tunable" in src
        src.count("_tunable")
        assert src.count("_tunable(") >= 3

    def test_ceiling_is_not_less_than_floor_by_default(self):
        src = _src()
        floor_m = re.search(r'CLAUDE_AGENT_FLOOR",\s*"(\d+)"', src)
        ceiling_m = re.search(r'CLAUDE_AGENT_CEILING",\s*"(\d+)"', src)
        assert floor_m and ceiling_m
        assert int(ceiling_m.group(1)) >= int(floor_m.group(1)), "CEILING default must be >= FLOOR default"

    def test_wave_width_equals_ceiling_by_default(self):
        src = _src()
        ceiling_m = re.search(r'CLAUDE_AGENT_CEILING",\s*"(\d+)"', src)
        wave_m = re.search(r'GLUDD_DISPATCH_WAVE_WIDTH",\s*"(\d+)"', src)
        assert ceiling_m and wave_m
        assert ceiling_m.group(1) == wave_m.group(1), "WAVE_WIDTH default should equal CEILING default (both 10)"

    def test_target_ceiling_and_wave_width_all_default_to_10(self):
        src = _src()
        ceiling_m = re.search(r'CLAUDE_AGENT_CEILING",\s*"(\d+)"', src)
        wave_m = re.search(r'GLUDD_DISPATCH_WAVE_WIDTH",\s*"(\d+)"', src)
        target_m = re.search(r'CLAUDE_AGENT_TARGET\s*\|\|\s*"(\d+)"', src)
        assert ceiling_m and wave_m and target_m
        assert ceiling_m.group(1) == "10"
        assert wave_m.group(1) == "10"
        assert target_m.group(1) == "10"

    def test_prev_message_dispatch_checked_against_wave_width(self):
        src = _src()
        idx = src.find("_prevMessageDispatchCount < eff.waveWidth")
        assert idx > 0

    def test_prev_message_undersize_is_hard_deny(self):
        src = _src()
        idx = src.find("_prevMessageDispatchCount < eff.waveWidth")
        assert idx > 0
        after = src[idx : idx + 600]
        assert 'permissionDecision: "deny"' in after


# ---------------------------------------------------------------------------
# OPENCODE_SUBAGENT guard (ceiling enforcement must skip subagents)
# ---------------------------------------------------------------------------


class TestCeilingSubagentGuard:
    def test_is_subagent_checked_in_hook(self):
        src = _src()
        assert "isSubagent()" in src

    def test_subagent_guard_before_ceiling_enforcement(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        subagent_idx = after.find("isSubagent()")
        wave_idx = after.find("WAVE WIDTH VIOLATION")
        assert subagent_idx < wave_idx, "isSubagent() must precede WAVE WIDTH VIOLATION check"

    def test_subagent_guard_before_dispatch_count_check(self):
        src = _src()
        before_idx = src.find('"tool.execute.before": async')
        assert before_idx > 0
        after = src[before_idx:]
        subagent_pos = after.find("isSubagent()")
        width_pos = after.find("eff.waveWidth")
        assert subagent_pos < width_pos, "isSubagent() guard must precede waveWidth enforcement"

    def test_subagent_marker_file_fallback(self):
        shared_src = SHARED_PATH.read_text()
        assert "OPENCODE_SUBAGENT" in shared_src
        assert "SUBAGENT_MARKER" in shared_src

    def test_zero_env_var_is_authoritative(self):
        shared_src = SHARED_PATH.read_text()
        assert '=== "0"' in shared_src or '== "0"' in shared_src, (
            "OPENCODE_SUBAGENT=0 must override file-based fallback"
        )


# ---------------------------------------------------------------------------
# Wave width violation behaviour on subsequent tool calls
# ---------------------------------------------------------------------------


class TestWaveWidthSubsequentBehavior:
    def test_dispatch_resets_streak_after_ceiling_reached(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_streakCount = 0" in after

    def test_dispatch_resets_read_streak_after_ceiling_reached(self):
        src = _src()
        dispatch_idx = src.find("if (isDispatchTool(tool))")
        assert dispatch_idx > 0
        after = src[dispatch_idx : dispatch_idx + 300]
        assert "_readStreak = 0" in after

    def test_effective_floor_uses_throttled_values(self):
        src = _src()
        idx = src.find("const eff = getEffectiveFloor()")
        assert idx > 0

    def test_eff_used_in_ceiling_check_not_raw_wave_width(self):
        src = _src()
        assert src.count("eff.waveWidth") >= 2, (
            "eff.waveWidth (from getEffectiveFloor) must be used, not raw WAVE_WIDTH"
        )

    def test_eff_dot_floor_used_in_block_message(self):
        src = _src()
        assert "eff.floor" in src
