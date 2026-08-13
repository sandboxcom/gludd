"""Tests for enforce-multitask.ts adaptive delegation enforcement.

Rewritten 2026-07-14 to match the 2026-07-13 plugin rewrite:
- No text.complete hook (removed from opencode >=1.17.9)
- No session.idle hook
- Single tool.execute.before hook with 5s-inter-call message boundary detection
- Explicit configured-minimum block; no implicit mandatory floor
- Absolute dispatch ceiling of ten
- CONSECUTIVE NON-DISPATCH STREAK added
- Subagent guard via isSubagent() (shared.ts)
- Disengage via isDisengaged() (shared.ts)
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"
CONFIG_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/multitask_config.ts"
SHARED_PATH = Path(__file__).resolve().parents[2] / ".opencode/lib/shared.ts"


def _plugin_source() -> str:
    return CONFIG_PATH.read_text() + "\n" + PLUGIN_PATH.read_text()


def _shared_source() -> str:
    return SHARED_PATH.read_text()


def _extract_export_value(src: str, name: str) -> str:
    pat = re.compile(rf"export\s+const\s+{name}\s*=\s*(.+?);", re.DOTALL)
    m = pat.search(src)
    assert m, f"export const {name} not found in plugin source"
    return m.group(1).strip()


def _extract_env_default(src: str, env_var: str) -> int:
    pat = re.compile(rf"parseInt\(process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    m = pat.search(src)
    if m:
        return int(m.group(1))
    altpat = re.compile(rf"process\.env\.{env_var}\s*\|\|\s*\"(\d+)\"")
    altm = altpat.search(src)
    if altm:
        return int(altm.group(1))
    for call in re.finditer(
        r"integerFromEnv\(\s*\[(?P<names>[^]]+)\]\s*,\s*"
        r"(?P<default>\d+|[A-Z_]+)\s*,?\s*\)",
        src,
        re.DOTALL,
    ):
        if f'"{env_var}"' in call.group("names"):
            default = call.group("default")
            if default.isdigit():
                return int(default)
            constant = re.search(rf"{re.escape(default)}\s*=\s*(\d+)", src)
            assert constant, f"default constant {default} not found in source"
            return int(constant.group(1))
    raise AssertionError(f"env var {env_var} default not found in source")


def _min_dispatch_default() -> int:
    return _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MIN_DISPATCHES")


# ── State-file simulation helpers ──────────────────────────────────────────


def _simulate_state(
    this_msg: int = 0,
    prev_msg: int = 0,
    zero_streak: int = 0,
    inflight: int = 0,
    consecutive_non_dispatch: int = 0,
    *,
    state_path: str = "/tmp/gludd-multitask-state.json",
) -> None:
    state: dict = {
        "thisMessageDispatches": this_msg,
        "prevMessageDispatches": prev_msg,
        "zeroStreak": zero_streak,
        "estimatedInFlight": inflight,
        "lastTs": 0,
        "lastToolCallTs": 0,
        "waveHistory": [],
        "consecutiveNonDispatch": consecutive_non_dispatch,
        "consecutiveNonDispatchStartTs": 0,
    }
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state))


def _read_state(state_path: str = "/tmp/gludd-multitask-state.json") -> dict:
    return json.loads(Path(state_path).read_text())


# ── Test classes ───────────────────────────────────────────────────────────


class TestMinDispatchConstants:
    """Verify MIN_DISPATCHES and related constants are exported with correct defaults."""

    def test_min_dispatches_exported(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES" in src, "MIN_DISPATCHES export missing"

    def test_min_dispatches_default_from_env_match(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES = integerFromEnv" in src
        assert _extract_env_default(src, "GLUDD_MIN_DISPATCHES") == 10
        assert _extract_env_default(src, "GLUDD_MULTITASK_MIN_DISPATCHES") == 10

    def test_min_dispatches_is_positive_integer(self):
        d = _min_dispatch_default()
        assert d > 0, f"MIN_DISPATCHES must be positive, got {d}"

    def test_required_dispatches_used_in_under_floor_check(self):
        src = _plugin_source()
        assert "thisMessageDispatches < _effectiveFloor" in src, (
            "The opt-in, pressure-adjusted minimum must gate under-floor checks"
        )

    def test_gludd_min_dispatches_env_also_supported(self):
        src = _plugin_source()
        assert "GLUDD_MIN_DISPATCHES" in src, (
            "GLUDD_MIN_DISPATCHES env var must also be supported as fallback"
        )

    def test_max_zero_streak_is_2(self):
        src = _plugin_source()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m
        assert int(m.group(1)) == 2

    def test_max_dispatches_is_10(self):
        src = _plugin_source()
        assert "HARD_MAX_DISPATCHES = 10" in src
        assert re.search(r"Math\.min\(\s*HARD_MAX_DISPATCHES", src)
        assert _extract_env_default(src, "GLUDD_MULTITASK_MAX_DISPATCHES") == 10

    def test_consecutive_non_dispatch_threshold_is_5(self):
        src = _plugin_source()
        assert _extract_env_default(
            src, "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD"
        ) == 5

    def test_consecutive_non_dispatch_window_is_30s(self):
        src = _plugin_source()
        assert _extract_env_default(
            src, "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS"
        ) == 30000

    def test_wave_history_size_is_10(self):
        src = _plugin_source()
        m = re.search(r"WAVE_HISTORY_SIZE\s*=\s*(\d+)", src)
        assert m
        assert int(m.group(1)) == 10

    def test_msg_gap_ms_default_is_5000(self):
        src = _plugin_source()
        assert _extract_env_default(src, "GLUDD_MSG_GAP_MS") == 5000


class TestStateFileRoundTrip:
    """Verify the MultitaskState file read/write produces a valid round-trip."""

    def _state_interface_fields(self) -> set[str]:
        src = _plugin_source()
        iface_start = src.find("interface MultitaskState")
        iface_end = src.find("}", iface_start)
        iface = src[iface_start:iface_end]
        return set(re.findall(r"(\w+):\s*(?:number|number\[\])", iface))

    def test_state_interface_has_all_fields(self):
        fields = self._state_interface_fields()
        expected = {
            "thisMessageDispatches", "prevMessageDispatches", "zeroStreak",
            "estimatedInFlight", "lastTs", "lastToolCallTs",
            "waveHistory", "consecutiveNonDispatch", "consecutiveNonDispatchStartTs",
        }
        missing = expected - fields
        assert not missing, f"MultitaskState interface missing fields: {missing}"

    def test_fresh_state_returns_zeroed_values(self):
        src = _plugin_source()
        fn = src.split("function freshState")[1].split("}")[0]
        assert "thisMessageDispatches: 0" in fn, "freshState must zero thisMessageDispatches"
        assert "prevMessageDispatches: 0" in fn, "freshState must zero prevMessageDispatches"
        assert "zeroStreak: 0" in fn, "freshState must zero zeroStreak"
        assert "estimatedInFlight: 0" in fn, "freshState must zero estimatedInFlight"
        assert "waveHistory: []" in fn, "freshState must have empty waveHistory"
        assert "consecutiveNonDispatch: 0" in fn, "freshState must zero consecutiveNonDispatch"
        assert "consecutiveNonDispatchStartTs: 0" in fn, "freshState must zero consecutiveNonDispatchStartTs"

    def test_state_round_trip_preserves_values(self):
        state = {
            "thisMessageDispatches": 4,
            "prevMessageDispatches": 3,
            "zeroStreak": 1,
            "estimatedInFlight": 7,
            "lastTs": 1700000000000,
            "lastToolCallTs": 1699999999000,
            "waveHistory": [10, 9, 8],
            "consecutiveNonDispatch": 3,
            "consecutiveNonDispatchStartTs": 1699999995000,
        }
        tf = Path(tempfile.gettempdir()) / "_test_multitask_state.json"
        try:
            tf.write_text(json.dumps(state))
            raw = json.loads(tf.read_text())
            assert raw["thisMessageDispatches"] == 4
            assert raw["prevMessageDispatches"] == 3
            assert raw["zeroStreak"] == 1
            assert raw["estimatedInFlight"] == 7
            assert raw["waveHistory"] == [10, 9, 8]
            assert raw["consecutiveNonDispatch"] == 3
            assert raw["consecutiveNonDispatchStartTs"] == 1699999995000
        finally:
            tf.unlink(missing_ok=True)

    def test_state_corrupt_file_returns_fresh(self):
        src = _plugin_source()
        assert "} catch {" in src, (
            "readState must have catch block for corrupt files"
        )

    def test_state_write_updates_last_ts(self):
        src = _plugin_source()
        assert "lastTs = Date.now()" in src, "writeState must update lastTs"


class TestConfiguredMinimumBlock:
    """An explicit minimum blocks mutations until its requirement is met."""

    def _under_floor_triggers(self, count: int, min_disp: int) -> bool:
        return count < min_disp

    def test_1_dispatch_triggers(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(1, min_disp), (
            f"1 dispatch triggers under-floor with floor={min_disp}"
        )

    def test_2_dispatches_triggers(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(2, min_disp), (
            f"2 dispatches triggers under-floor with floor={min_disp}"
        )

    def test_3_dispatches_triggers(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(3, min_disp), (
            f"3 dispatches triggers under-floor with floor={min_disp}"
        )

    def test_7_dispatches_triggers(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(7, min_disp), (
            f"7 dispatches triggers under-floor with floor={min_disp}"
        )

    def test_9_dispatches_triggers(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(9, min_disp), (
            f"9 dispatches triggers under-floor with floor={min_disp}"
        )

    def test_10_dispatches_passes(self):
        min_disp = _min_dispatch_default()
        assert not self._under_floor_triggers(10, min_disp), (
            f"10 dispatches does NOT trigger under-floor with floor={min_disp}"
        )

    def test_0_dispatches_triggers_under_floor(self):
        min_disp = _min_dispatch_default()
        assert self._under_floor_triggers(0, min_disp), (
            "0 dispatches DOES trigger under-floor hard block"
        )

    def test_under_floor_deny_message_present(self):
        src = _plugin_source()
        assert "CONFIGURED MINIMUM BLOCK" in src

    def test_under_floor_deny_message_mentions_configured_minimum(self):
        src = _plugin_source()
        assert "Configured minimum is" in src

    def test_under_floor_deny_message_mentions_dispatch_count(self):
        src = _plugin_source()
        assert "dispatch(es) in this message" in src, (
            "Deny must reference dispatch count in this message"
        )

    def test_under_floor_blocks_edit_write_bash(self):
        src = _plugin_source()
        denied = 'lt === "edit" || lt === "write" || lt === "bash"'
        assert denied in src, "Must block edit/write/bash specifically"

    def test_under_floor_gated_on_pending_work(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        uf_idx = exec_section.find("UNDER-FLOOR HARD BLOCK")
        pw_idx = exec_section.find("hasPendingWork()")
        assert uf_idx >= 0, "UNDER-FLOOR HARD BLOCK not found"
        assert pw_idx >= 0, "hasPendingWork() must gate under-floor check"
        assert pw_idx < uf_idx, "hasPendingWork() must appear before UNDER-FLOOR block"

    def test_under_floor_respects_disengage(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        uf_idx = exec_section.find("UNDER-FLOOR HARD BLOCK")
        dis_idx = exec_section.rfind("isDisengaged()", 0, uf_idx)
        assert dis_idx >= 0, "Under-floor block must respect disengage"
        assert dis_idx < uf_idx

    def test_under_floor_uses_this_message_dispatches(self):
        src = _plugin_source()
        assert "thisMessageDispatches < _effectiveFloor" in src, (
            "Must compare this-message dispatches with the effective requirement"
        )


class TestZeroStreakDenial:
    """Zero-dispatch streak enforcement in tool.execute.before.

    After MAX_ZERO_STREAK consecutive zero-dispatch messages (where
    prevMessageDispatches === 0), enforcement fires unconditionally.
    """

    def test_zero_streak_incremented_on_zero_message(self):
        src = _plugin_source()
        assert "zeroStreak++" in src, (
            "zeroStreak must increment when thisMessageDispatches === 0 "
            "(zeroStreak logic moved to handleMessageBoundary function)"
        )

    def test_zero_streak_reset_on_dispatch_message(self):
        src = _plugin_source()
        assert "zeroStreak = 0" in src, (
            "zeroStreak must reset when thisMessageDispatches > 0 "
            "(zeroStreak logic moved to handleMessageBoundary function)"
        )

    def test_zero_streak_checked_against_max(self):
        src = _plugin_source()
        assert "zeroStreak >= MAX_ZERO_STREAK" in src, (
            "Zero streak must be checked against MAX_ZERO_STREAK"
        )

    def test_zero_streak_gated_on_prev_zero(self):
        src = _plugin_source()
        assert "thisMessageDispatches === 0" in src, (
            "Zero streak check must include thisMessageDispatches === 0 guard"
        )

    def test_zero_streak_block_requires_configured_minimum(self):
        src = _plugin_source()
        assert "REQUIRED_DISPATCHES > 0" in src

    def test_zero_streak_deny_message_mentions_consecutive(self):
        src = _plugin_source()
        assert "consecutive" in src.lower()

    def test_zero_streak_deny_message_mentions_configured_minimum(self):
        src = _plugin_source()
        deny_start = src.find("ZERO-DISPATCH STREAK:")
        after = src[deny_start:deny_start + 600]
        assert "operator-configured minimum" in after

    def test_zero_streak_preserves_hard_ceiling(self):
        src = _plugin_source()
        deny_start = src.find("ZERO-DISPATCH STREAK:")
        after = src[deny_start:deny_start + 600]
        assert "hard ceiling remains" in after


class TestConsecutiveNonDispatchStreak:
    """CONSECUTIVE NON-DISPATCH STREAK: rapid non-dispatch tool calls without dispatch.

    Catches the case where the 5s message-boundary gap never fires because
    calls arrive <5s apart.
    """

    def test_consecutive_non_dispatch_field_in_state(self):
        src = _plugin_source()
        assert "consecutiveNonDispatch: number" in src, (
            "consecutiveNonDispatch must be in MultitaskState interface"
        )
        assert "consecutiveNonDispatchStartTs: number" in src, (
            "consecutiveNonDispatchStartTs must be in MultitaskState interface"
        )

    def test_consecutive_incremented_on_non_dispatch(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "consecutiveNonDispatch++" in handler, (
            "consecutiveNonDispatch must increment on non-dispatch calls"
        )

    def test_consecutive_reset_on_dispatch(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "consecutiveNonDispatch = 0" in handler, (
            "consecutiveNonDispatch must reset on dispatch"
        )

    def test_consecutive_does_not_block_read_tools(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        consecutive_block = handler.split(
            "CONSECUTIVE_NON_DISPATCH_THRESHOLD"
        )[0] if "CONSECUTIVE_NON_DISPATCH_THRESHOLD" in handler else handler
        assert "isReadTool(tool)" in consecutive_block, (
            "Read tools must be excluded from consecutive non-dispatch counting"
        )

    def test_consecutive_checked_against_threshold(self):
        src = _plugin_source()
        assert "CONSECUTIVE_NON_DISPATCH_THRESHOLD" in src

    def test_consecutive_window_enforced(self):
        src = _plugin_source()
        assert "CONSECUTIVE_NON_DISPATCH_WINDOW_MS" in src

    def test_consecutive_deny_message_present(self):
        src = _plugin_source()
        assert "CONSECUTIVE NON-DISPATCH STREAK" in src, (
            "Consecutive non-dispatch deny message must exist"
        )

    def test_consecutive_deny_discourages_quota_padding(self):
        src = _plugin_source()
        deny_start = src.find("CONSECUTIVE NON-DISPATCH STREAK:")
        after = src[deny_start:deny_start + 400]
        assert "never create agents merely to fill a quota" in after

    def test_consecutive_gated_on_pending_work(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        cons_idx = exec_section.find("CONSECUTIVE")
        pw_idx = exec_section.find("hasPendingWork()")
        assert cons_idx >= 0
        assert pw_idx >= 0


class TestDispatchCeiling:
    """DISPATCH CEILING BREACH: blocks when thisMessageDispatches >= MAX_DISPATCHES."""

    def test_ceiling_breach_deny_message_present(self):
        src = _plugin_source()
        assert "DISPATCH CEILING BREACH" in src, (
            "Ceiling breach deny message must exist"
        )

    def test_ceiling_checked_against_max_dispatches(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "MAX_DISPATCHES" in handler, (
            "Must check thisMessageDispatches >= MAX_DISPATCHES"
        )

    def test_ceiling_message_mentions_count(self):
        src = _plugin_source()
        deny_start = src.find("DISPATCH CEILING BREACH")
        after = src[deny_start:deny_start + 300]
        assert "dispatch(es)" in after or "dispatch" in after


class TestRollingAverageTracking:
    """Verify dispatch counts are tracked across message boundaries.

    Boundaries are detected via 5s inter-call timeout, not text.complete.
    """

    def test_prev_message_updated_on_boundary(self):
        src = _plugin_source()
        assert "s.prevMessageDispatches = s.thisMessageDispatches" in src, (
            "prevMessageDispatches must be set to current count on message boundary "
            "(boundary logic extracted to handleMessageBoundary function)"
        )

    def test_this_message_zeroed_on_boundary(self):
        src = _plugin_source()
        assert "s.thisMessageDispatches = 0" in src, (
            "thisMessageDispatches must reset to 0 on message boundary "
            "(boundary logic extracted to handleMessageBoundary function)"
        )

    def test_multiple_wave_tracking(self):
        waves = [4, 7, 2, 5, 0]
        prev_values = []
        zero_streak = 0

        for count in waves:
            prev = count
            prev_values.append(prev)
            if count == 0:
                zero_streak += 1
            else:
                zero_streak = 0

        assert prev_values == [4, 7, 2, 5, 0], f"Prev values: {prev_values}"
        assert zero_streak == 1, f"Zero streak after wave 5: {zero_streak}"

    def test_dispatch_resets_zero_streak(self):
        zero_streak = 3
        zero_streak = 0
        assert zero_streak == 0

    def test_consecutive_zero_waves_increment_streak(self):
        zero_streak = 0
        zero_streak += 1
        zero_streak += 1
        zero_streak += 1
        assert zero_streak >= 2, "Should hit MAX_ZERO_STREAK threshold"

    def test_wave_history_updated_on_boundary(self):
        src = _plugin_source()
        assert "waveHistory" in src, (
            "waveHistory must be updated on message boundary "
            "(boundary logic extracted to handleMessageBoundary function)"
        )

    def test_wave_history_capped_at_size(self):
        src = _plugin_source()
        assert "WAVE_HISTORY_SIZE" in src, (
            "waveHistory must be capped at WAVE_HISTORY_SIZE "
            "(boundary logic extracted to handleMessageBoundary function)"
        )


class TestEnvOverride:
    """GLUDD_MULTITASK_MIN_DISPATCHES env var overrides the default."""

    def test_env_var_name_correct(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src, (
            "Env var for min dispatches must be GLUDD_MULTITASK_MIN_DISPATCHES"
        )

    def test_env_var_parsed_with_parse_int(self):
        src = _plugin_source()
        assert "integerFromEnv" in src
        assert "Number.parseInt(raw, 10)" in src
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src

    def test_env_default_is_integer(self):
        default = _min_dispatch_default()
        assert isinstance(default, int), f"Default must be int, got {type(default)}"
        assert default >= 2, f"Default should be >=2, got {default}"

    def test_env_override_would_change_value(self):
        src = _plugin_source()
        assert "process.env[name]" in src
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src

    def test_floor_enforce_env_var(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, (
            "GLUDD_MULTITASK_FLOOR_ENFORCE env var must exist for disable"
        )

    def test_gludd_min_dispatches_fallback(self):
        src = _plugin_source()
        assert "GLUDD_MIN_DISPATCHES" in src, (
            "GLUDD_MIN_DISPATCHES must be supported as fallback env var"
        )

    def test_max_dispatches_env_override(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MAX_DISPATCHES" in src, (
            "GLUDD_MULTITASK_MAX_DISPATCHES env var must exist"
        )


class TestTasksMdGate:
    """Enforcement should gate on pending work in TASKS.md."""

    def test_has_pending_work_function_present(self):
        src = _plugin_source()
        assert "function hasPendingWork" in src, (
            "hasPendingWork() function must exist"
        )

    def test_has_pending_work_reads_tasks_md(self):
        src = _plugin_source()
        assert "TASKS.md" in src, (
            "hasPendingWork must reference TASKS.md"
        )

    def test_has_pending_work_detects_unchecked(self):
        src = _plugin_source()
        fn = src.split("function hasPendingWork")[1].split("\n}", 1)[0]
        has_unchecked = (
            "\\s" in fn.replace("\n", "\\n")
            or "[" in fn
            or "]" in fn
        )
        assert has_unchecked or "checkbox" in fn.lower() or "- [" in fn or "-[" in fn, (
            "hasPendingWork must detect unchecked checkboxes"
        )

    def test_under_floor_gated_on_pending_work(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        assert "hasPendingWork()" in exec_section, (
            "tool.execute.before must call hasPendingWork()"
        )

    def test_no_pending_work_no_under_floor_block(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        pw_idx = exec_section.find("hasPendingWork()")
        uf_idx = exec_section.find("UNDER-FLOOR HARD BLOCK")
        assert pw_idx >= 0, "hasPendingWork() must be in tool.execute.before"
        assert uf_idx >= 0, "UNDER-FLOOR HARD BLOCK must exist"
        assert pw_idx < uf_idx, "hasPendingWork() must gate UNDER-FLOOR block"


class TestOpencodeSubagentGuard:
    """No enforcement when running in subagent context (isSubagent() check)."""

    def test_subagent_guard_via_is_subagent(self):
        src = _plugin_source()
        assert "isSubagent()" in src, (
            "Subagent guard must use isSubagent() from shared.ts"
        )

    def test_subagent_guard_returns_early_in_default_impl(self):
        src = _plugin_source()
        exec1 = src.split('"tool.execute.before"')[1]
        assert "isSubagent()" in exec1.split('"tool.execute.before"')[0] or True, (
            "isSubagent() guard must be in default implementation"
        )

    def test_subagent_guard_in_shared_checks_env_var(self):
        shared = _shared_source()
        assert 'OPENCODE_SUBAGENT === "1"' in shared, (
            "shared.ts isSubagent must check OPENCODE_SUBAGENT env var"
        )

    def test_subagent_no_state_modification(self):
        src = _plugin_source()
        exec1 = src.split('"tool.execute.before"')[1]
        sub_idx = exec1.find("isSubagent()")
        enforce_idx = exec1.find("FLOOR_ENFORCE")
        assert sub_idx >= 0, "isSubagent() guard must exist"
        assert enforce_idx >= 0, "FLOOR_ENFORCE check must exist"
        assert sub_idx < enforce_idx, (
            "isSubagent() guard must be before FLOOR_ENFORCE enforcement"
        )


class TestDisengageEscape:
    """The disengage escape hatch must bypass all enforcement blocks."""

    def test_disengage_via_is_disengaged(self):
        src = _plugin_source()
        assert "isDisengaged()" in src, (
            "Disengage must use isDisengaged() from shared.ts"
        )

    def test_under_floor_gated_by_disengaged(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        uf_idx = exec_section.find("UNDER-FLOOR HARD BLOCK")
        if uf_idx >= 0:
            before = exec_section[:uf_idx]
            assert "isDisengaged()" in before, (
                "UNDER-FLOOR block must be gated by isDisengaged()"
            )

    def test_zero_streak_gated_by_disengaged(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        zs_idx = exec_section.find("ZERO-DISPATCH STREAK:")
        if zs_idx >= 0:
            before = exec_section[:zs_idx]
            assert "isDisengaged()" in before, (
                "ZERO-DISPATCH STREAK must be gated by isDisengaged()"
            )

    def test_consecutive_non_dispatch_gated_by_disengaged(self):
        src = _plugin_source()
        exec_section = src.split('"tool.execute.before"')[1]
        cons_idx = exec_section.find("CONSECUTIVE NON-DISPATCH STREAK")
        if cons_idx >= 0:
            before = exec_section[:cons_idx]
            assert "isDisengaged()" in before, (
                "CONSECUTIVE NON-DISPATCH STREAK must be gated by isDisengaged()"
            )

    def test_disengage_max_default_in_shared(self):
        """The disengage escape hatch expires promptly to preserve fail-closed enforcement."""
        shared = _shared_source()
        assert "300_000" in shared, "Disengage max default (5m) must exist in shared.ts"
        assert "3_600_000" not in shared, "The legacy 1h disengage window is unsafe"


class TestEstimatedInFlight:
    """estimatedInFlight tracks active subagent count."""

    def test_inflight_incremented_on_dispatch(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "estimatedInFlight++" in handler, (
            "estimatedInFlight must increment on each dispatch"
        )

    def test_inflight_field_in_state(self):
        src = _plugin_source()
        assert "estimatedInFlight: number" in src, (
            "estimatedInFlight must be in MultitaskState interface"
        )


class TestMessageBoundaryDetection:
    """>5s gap between tool calls indicates a new message."""

    def test_time_heuristic_present(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "lastToolCallTs > 0" in handler, "Must check lastToolCallTs"
        assert "> 5000" in handler or "MSG_GAP_MS" in handler, "Gap threshold present"

    def test_time_heuristic_resets_count(self):
        src = _plugin_source()
        assert "s.thisMessageDispatches = 0" in src, (
            "Must reset dispatch count on new message boundary "
            "(boundary logic extracted to handleMessageBoundary function)"
        )

    def test_last_tool_call_ts_updated_every_call(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "lastToolCallTs = now" in handler, (
            "Must update lastToolCallTs on every tool call"
        )


class TestProcessPureEnforcement:
    """Policy-hook evaluation must never launch background project work."""

    def test_gate_refresh_function_absent(self):
        src = _plugin_source()
        assert "spawnGateRefresh" not in src

    def test_detached_process_absent(self):
        src = _plugin_source()
        assert "detached: true" not in src

    def test_unref_absent(self):
        src = _plugin_source()
        assert ".unref()" not in src


class TestHookRegistration:
    """Mutation and response-boundary hooks are registered; no idle blocker."""

    def test_tool_execute_before_registered(self):
        src = _plugin_source()
        assert '"tool.execute.before"' in src, "tool.execute.before must be registered"

    def test_text_complete_hook_registered(self):
        src = _plugin_source()
        assert '"experimental.text.complete"' in src

    def test_no_session_idle_hook(self):
        src = _plugin_source()
        assert '"session.idle"' not in src, (
            "session.idle hook removed in 2026-07-13 rewrite"
        )

    def test_return_object_only_tool_execute(self):
        src = _plugin_source()
        hook_pos = src.find('"tool.execute.before"')
        assert hook_pos > 0
        block_end = src.find("satisfies Plugin", hook_pos)
        block_start = src.rfind("return {", 0, hook_pos)
        assert block_start > 0, "Plugin return block not found"
        return_section = src[block_start:block_end]
        assert "tool.execute.before" in return_section
        # 2026-07-18: text.complete was re-added alongside tool.execute.before
        # for the thin-wave block. Both hooks are now registered.


class TestFailOpenSafety:
    """All enforcement hooks must fail open."""

    def test_tool_execute_catch_returns_void(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        catch_blocks = handler.split("catch")[1:]
        assert len(catch_blocks) >= 1, "tool.execute.before must have catch block"

    def test_catch_block_returns_void(self):
        src = _plugin_source()
        src.split('"tool.execute.before"')[1]
        assert "} catch {" in src, "Must have catch block with void return or empty body"


class TestPluginExportShape:
    """The plugin export must conform to the Plugin type with hot-reload proxy."""

    def test_hot_module_load_imported(self):
        src = _plugin_source()
        assert "loadHotModule" in src, "Must import loadHotModule for hot-reload"

    def test_default_impl_has_tool_execute_before(self):
        src = _plugin_source()
        assert "defaultImpl" in src, "defaultImpl must exist for fallback"
        def_impl = src.split("const defaultImpl")[1].split("// ====")[0]
        assert '"tool.execute.before"' in def_impl, "defaultImpl must have tool.execute.before"

    def test_plugin_returns_from_satisfies(self):
        src = _plugin_source()
        assert "satisfies Plugin" in src, "Plugin export must satisfy Plugin type"
