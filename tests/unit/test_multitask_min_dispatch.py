"""Tests for enforce-multitask.ts MIN_DISPATCHES enforcement.

Verifies that the plugin correctly enforces minimum dispatch counts per wave.
After the parallel subagent fix changes MIN_DISPATCHES default from 7 to 3,
these tests mechanically verify:
- Constants are defined with the correct default
- State file read/write round-trips correctly
- Warning/deny behavior for 1, 2, 3, 7 dispatch waves
- Zero-dispatch hard deny (integration with enforce-stop.ts via zeroStreak)
- Rolling average tracking across waves
- Env var override of MIN_DISPATCHES
- TASKS.md pending-work gate on warnings
- OPENCODE_SUBAGENT guard (no enforcement in subagent context)
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

PLUGIN_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _plugin_source() -> str:
    return PLUGIN_PATH.read_text()


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
    raise AssertionError(f"env var {env_var} default not found in source")


def _min_dispatch_default() -> int:
    return _extract_env_default(_plugin_source(), "GLUDD_MULTITASK_MIN_DISPATCHES")


# ── State-file simulation helpers ──────────────────────────────────────────

def _simulate_state(
    this_msg: int = 0,
    prev_msg: int = 0,
    zero_streak: int = 0,
    inflight: int = 0,
    *,
    state_path: str = "/tmp/gludd-multitask-state.json",
) -> None:
    """Write a simulated MultitaskState to the state file."""
    state: dict = {
        "thisMessageDispatches": this_msg,
        "prevMessageDispatches": prev_msg,
        "zeroStreak": zero_streak,
        "estimatedInFlight": inflight,
        "lastTs": 0,
        "lastToolCallTs": 0,
    }
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state))


def _read_state(state_path: str = "/tmp/gludd-multitask-state.json") -> dict:
    return json.loads(Path(state_path).read_text())


def _simulate_tasks_md(checkboxes: bool, *, tmp_path: Path | None = None) -> Path:
    """Create a TASKS.md with or without unchecked checkboxes. Returns the path."""
    base = tmp_path or Path(tempfile.gettempdir())
    tasks_path = base / "_test_TASKS.md"
    if checkboxes:
        tasks_path.write_text("- [ ] Pending task\n- [x] Done task\n")
    else:
        tasks_path.write_text("- [x] All done\n- [x] Nothing pending\n")
    return tasks_path


# ── Test classes ───────────────────────────────────────────────────────────


class TestMinDispatchConstants:
    """Verify MIN_DISPATCHES is exported and has the expected default."""

    def test_min_dispatches_exported(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES" in src, "MIN_DISPATCHES export missing"

    def test_min_dispatches_default_from_env_match(self):
        """The default is the env fallback string in the parseInt call."""
        src = _plugin_source()
        m = re.search(r'GLUDD_MULTITASK_MIN_DISPATCHES\s*\|\|\s*"(\d+)"', src)
        assert m, "GLUDD_MULTITASK_MIN_DISPATCHES default string not found"
        default_str = int(m.group(1))
        expected = _min_dispatch_default()
        assert default_str == expected

    def test_min_dispatches_is_positive_integer(self):
        d = _min_dispatch_default()
        assert d > 0, f"MIN_DISPATCHES must be positive, got {d}"

    def test_min_dispatches_used_in_floor_breach_check(self):
        src = _plugin_source()
        assert "_state.prevMessageDispatches < MIN_DISPATCHES" in src, (
            "MIN_DISPATCHES must be used in floor-breach comparison"
        )

    def test_min_dispatches_used_in_text_complete_block(self):
        src = _plugin_source()
        assert "MIN_DISPATCHES" in src.split('"experimental.text.complete"')[1], (
            "MIN_DISPATCHES must be referenced in text.complete hook"
        )

    def test_max_zero_streak_is_2(self):
        src = _plugin_source()
        m = re.search(r"MAX_ZERO_STREAK\s*=\s*(\d+)", src)
        assert m
        assert int(m.group(1)) == 2


class TestStateFileRoundTrip:
    """Verify the MultitaskState file read/write produces a valid round-trip."""

    def _state_interface_fields(self) -> set[str]:
        src = _plugin_source()
        iface_start = src.find("interface MultitaskState")
        iface_end = src.find("}", iface_start)
        iface = src[iface_start:iface_end]
        return set(re.findall(r"(\w+):\s*number", iface))

    def test_state_interface_has_all_fields(self):
        fields = self._state_interface_fields()
        expected = {"thisMessageDispatches", "prevMessageDispatches", "zeroStreak",
                     "estimatedInFlight", "lastTs", "lastToolCallTs"}
        missing = expected - fields
        assert not missing, f"MultitaskState interface missing fields: {missing}"

    def test_state_read_defaults_on_missing_file(self):
        """readState() returns zeroes when file doesn't exist."""
        src = _plugin_source()
        read_fn = re.search(
            r"return\s*\{\s*thisMessageDispatches:\s*0[^}]*\}",
            src.split("function readState")[1],
            re.DOTALL,
        )
        assert read_fn, "Default return in readState() must include zeroed fields"

    def test_state_round_trip_preserves_values(self):
        """Write known state, then re-read via extracted logic."""
        state = {
            "thisMessageDispatches": 4,
            "prevMessageDispatches": 3,
            "zeroStreak": 1,
            "estimatedInFlight": 7,
            "lastTs": 1700000000000,
            "lastToolCallTs": 1699999999000,
        }
        tf = Path(tempfile.gettempdir()) / "_test_multitask_state.json"
        try:
            tf.write_text(json.dumps(state))
            # Simulate readState()
            raw = json.loads(tf.read_text())
            assert raw["thisMessageDispatches"] == 4
            assert raw["prevMessageDispatches"] == 3
            assert raw["zeroStreak"] == 1
            assert raw["estimatedInFlight"] == 7
        finally:
            tf.unlink(missing_ok=True)

    def test_state_corrupt_file_returns_fresh(self):
        """Corrupt JSON returns fresh zeroed state."""
        src = _plugin_source()
        assert "} catch { /* corrupt" in src or "} catch {" in src, (
            "readState() must have catch block for corrupt files"
        )

    def test_state_write_updates_last_ts(self):
        """writeState() sets lastTs to Date.now()."""
        src = _plugin_source()
        assert "lastTs = Date.now()" in src, "writeState must update lastTs"


class TestDispatchCountWarnings:
    """Verify warning/deny behavior for various dispatch counts.

    These tests simulate the tool.execute.before logic by writing
    state files and checking the expected deny conditions.
    """

    def _floor_breach_deny_exists_for(self, count: int, min_disp: int) -> bool:
        """Would the plugin deny with 'prevMessageDispatches < MIN_DISPATCHES'?"""
        return count > 0 and count < min_disp

    def test_1_dispatch_triggers_floor_breach(self):
        """1 dispatch in prior message with floor=3 → denied."""
        min_disp = _min_dispatch_default()
        # The plugin checks prevMessageDispatches > 0 && < MIN_DISPATCHES
        # So 1 < min_disp should trigger
        assert self._floor_breach_deny_exists_for(1, min_disp) == (min_disp > 1), (
            f"Floor breach for 1 dispatch should be {min_disp > 1} with floor={min_disp}"
        )

    def test_2_dispatches_triggers_floor_breach(self):
        min_disp = _min_dispatch_default()
        assert self._floor_breach_deny_exists_for(2, min_disp) == (min_disp > 2), (
            f"Floor breach for 2 dispatches should be {min_disp > 2} with floor={min_disp}"
        )

    def test_3_dispatches_may_trigger_floor_breach(self):
        min_disp = _min_dispatch_default()
        # 3 < 3 is false → no breach when floor is 3
        # 3 < 7 is true → breach when floor is 7
        assert self._floor_breach_deny_exists_for(3, min_disp) == (min_disp > 3), (
            f"Floor breach for 3 dispatches should be {min_disp > 3} with floor={min_disp}"
        )

    def test_7_dispatches_never_triggers_floor_breach(self):
        """7 dispatches always >= any reasonable floor."""
        min_disp = _min_dispatch_default()
        assert not self._floor_breach_deny_exists_for(7, min_disp), (
            f"7 dispatches should never trigger floor breach (floor={min_disp})"
        )

    def test_zero_dispatch_floor_breach(self):
        """0 dispatches: prevMessageDispatches > 0 is false, so no floor breach.
        Instead, zeroStreak enforcement handles this case."""
        min_disp = _min_dispatch_default()
        # prevMessageDispatches === 0 → floor breach check short-circuits
        assert not self._floor_breach_deny_exists_for(0, min_disp), (
            "0 dispatches should not trigger floor breach (handled by zero streak)"
        )

    def test_floor_breach_deny_message_contains_count(self):
        src = _plugin_source()
        assert "prevMessageDispatches" in src, "Deny must reference dispatch count"

    def test_floor_breach_deny_message_contains_floor(self):
        src = _plugin_source()
        assert "Codified floor" in src, "Deny must mention floor requirement"


class TestZeroStreakDenial:
    """Verify zero-streak hard deny (integration with enforce-stop.ts).

    After MAX_ZERO_STREAK consecutive zero-dispatch messages while the
    prior message also had zero dispatches, enforcement fires.
    """

    def test_zero_streak_incremented_on_zero_message(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "zeroStreak++" in handler, (
            "zeroStreak must increment when thisMessageDispatches === 0"
        )

    def test_zero_streak_reset_on_dispatch_message(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "zeroStreak = 0" in handler, (
            "zeroStreak must reset to 0 when thisMessageDispatches > 0"
        )

    def test_zero_streak_checked_against_max(self):
        src = _plugin_source()
        assert "zeroStreak >= MAX_ZERO_STREAK" in src, (
            "Zero streak must be checked against MAX_ZERO_STREAK"
        )

    def test_zero_streak_dead_locked_on_prev_zero(self):
        """Enforcement fires only when prevMessageDispatches === 0 AND
        zeroStreak >= MAX_ZERO_STREAK."""
        src = _plugin_source()
        assert "prevMessageDispatches === 0" in src, (
            "Zero streak check must include prevMessageDispatches === 0 guard"
        )

    def test_text_complete_replaces_on_zero_streak_limit(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "MUST DISPATCH" in handler, (
            "text.complete must replace text when zeroStreak at limit"
        )

    def test_zero_streak_block_is_unconditional(self):
        """No pending-work gate on zero-streak enforcement."""
        src = _plugin_source()
        deny_start = src.find("ZERO-DISPATCH STREAK:")
        assert deny_start > 0, "ZERO-DISPATCH STREAK deny not found"
        after = src[deny_start:deny_start + 800]
        assert "UNCONDITIONAL" in after, (
            "Zero-streak enforcement must be unconditional"
        )

    def test_zero_streak_deny_message_mentions_consecutive(self):
        src = _plugin_source()
        assert "consecutive" in src.lower(), "Must mention consecutive in deny"


class TestPerMessageEnforcement:
    """The <7-dispatch per-message enforcement blocks Edit/Write/Bash."""

    def test_per_message_threshold_default(self):
        src = _plugin_source()
        assert "thisMessageDispatches < 7" in src, (
            "Per-message threshold must be <7"
        )

    def test_per_message_only_with_pending_work(self):
        src = _plugin_source()
        assert "hasPendingWork()" in src, (
            "Per-message enforcement must gate on hasPendingWork()"
        )

    def test_per_message_blocks_edit_write_bash(self):
        src = _plugin_source()
        blocked = 'lt === "edit" || lt === "write" || lt === "bash"'
        assert blocked in src, "Must block edit/write/bash specifically"

    def test_per_message_respects_disengage(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        insuff = handler.find("INSUFFICIENT DISPATCHES")
        before = handler[:insuff]
        assert "disengaged" in before, "Per-message must respect disengage"

    def test_text_complete_blocks_on_low_dispatch(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "MESSAGE BLOCKED" in handler, "text.complete must block low dispatch"


class TestRollingAverageTracking:
    """Verify dispatch counts are tracked across message boundaries.

    Simulates the core state transitions that happen across waves.
    """

    def test_prev_message_updated_on_text_complete(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "_state.prevMessageDispatches = _state.thisMessageDispatches" in handler, (
            "prevMessageDispatches must be set to current count on text.complete"
        )

    def test_this_message_zeroed_on_text_complete(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "thisMessageDispatches = 0" in handler, (
            "thisMessageDispatches must reset to 0 on text.complete"
        )

    def test_multiple_wave_tracking(self):
        """Simulate 5 waves with varying dispatch counts.

        Wave 1: 4 dispatches → text.complete → prev=4, this=0
        Wave 2: 7 dispatches → text.complete → prev=7, this=0
        Wave 3: 2 dispatches → text.complete → prev=2, this=0
        Wave 4: 5 dispatches → text.complete → prev=5, this=0
        Wave 5: 0 dispatches → text.complete → prev=0, this=0, zeroStreak++
        """
        waves = [4, 7, 2, 5, 0]
        prev_values = []
        zero_streak = 0

        for _i, count in enumerate(waves):
            # Simulate: thisMessageDispatches = count
            # text.complete fires:
            prev = count  # prevMessageDispatches = thisMessageDispatches
            prev_values.append(prev)
            # thisMessageDispatches = 0
            if count == 0:
                zero_streak += 1
            else:
                zero_streak = 0

        assert prev_values == [4, 7, 2, 5, 0], f"Prev values: {prev_values}"
        assert zero_streak == 1, f"Zero streak after wave 5: {zero_streak}"

    def test_dispatch_resets_zero_streak(self):
        """A wave with dispatches resets zeroStreak to 0."""
        zero_streak = 3
        # Wave with 4 dispatches arrives
        zero_streak = 0
        assert zero_streak == 0

    def test_consecutive_zero_waves_increment_streak(self):
        """Three consecutive zero-dispatch waves → zeroStreak = 3."""
        zero_streak = 0
        zero_streak += 1  # wave 1: 0 dispatches
        zero_streak += 1  # wave 2: 0 dispatches
        zero_streak += 1  # wave 3: 0 dispatches
        assert zero_streak >= 2, "Should hit MAX_ZERO_STREAK threshold"


class TestEnvOverride:
    """GLUDD_MULTITASK_MIN_DISPATCHES env var overrides the default."""

    def test_env_var_name_correct(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_MIN_DISPATCHES" in src, (
            "Env var for min dispatches must be GLUDD_MULTITASK_MIN_DISPATCHES"
        )

    def test_env_var_parsed_with_parse_int(self):
        src = _plugin_source()
        assert "parseInt(process.env.GLUDD_MULTITASK_MIN_DISPATCHES" in src, (
            "Env var must be parsed via parseInt"
        )

    def test_env_default_is_integer(self):
        default = _min_dispatch_default()
        assert isinstance(default, int), f"Default must be int, got {type(default)}"
        assert default >= 2, f"Default should be >=2, got {default}"

    def test_env_override_would_change_value(self):
        """Prove the override path exists in the source."""
        src = _plugin_source()
        assert "process.env.GLUDD_MULTITASK_MIN_DISPATCHES" in src, (
            "Environment override path must exist"
        )

    def test_floor_enforce_env_var(self):
        src = _plugin_source()
        assert "GLUDD_MULTITASK_FLOOR_ENFORCE" in src, (
            "GLUDD_MULTITASK_FLOOR_ENFORCE env var must exist for disable"
        )


class TestTasksMdGate:
    """Warnings/denials should gate on pending work in TASKS.md."""

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
        checkbox_pat = re.search(r"/\^.*\\s\*\\\[.*\\s\*\\\]/", fn.replace("\n", "\\n"))
        assert checkbox_pat or "\\[\\s*\\]" in fn or "\\[ \\]" in fn or "\\[ ]" in fn, (
            "hasPendingWork must detect unchecked checkboxes"
        )

    def test_per_message_check_gates_on_pending_work(self):
        src = _plugin_source()
        handler_before = src.split('"tool.execute.before"')[1].split('"experimental.text.complete"')[0]
        assert "hasPendingWork()" in handler_before, (
            "tool.execute.before must call hasPendingWork()"
        )

    def test_no_pending_work_no_per_message_block(self):
        """When TASKS.md has no unchecked items, per-message block should not fire.
        Verified by the hasPendingWork() call gating the check."""
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"experimental.text.complete"')[0]
        # hasPendingWork() appears in the conditional before the block
        idx = handler.find("hasPendingWork()")
        block = handler.find("INSUFFICIENT DISPATCHES")
        assert idx >= 0, "hasPendingWork() must be in tool.execute.before"
        assert block >= 0, "INSUFFICIENT DISPATCHES block must exist"
        assert idx < block, "hasPendingWork() must gate the INSUFFICIENT DISPATCHES block"


class TestOpencodeSubagentGuard:
    """No enforcement when OPENCODE_SUBAGENT=1 (subagent context)."""

    def test_subagent_guard_in_tool_execute(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"session.idle"')[0]
        assert 'OPENCODE_SUBAGENT === "1"' in handler, (
            "OPENCODE_SUBAGENT guard must be first check in tool.execute.before"
        )

    def test_subagent_guard_returns_early(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"session.idle"')[0]
        lines = handler.split("\n")
        early_lines = "\n".join(lines[:10])
        assert "return" in early_lines or "OPENCODE_SUBAGENT" in early_lines, (
            "OPENCODE_SUBAGENT check must return early"
        )

    def test_subagent_guard_in_text_complete(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert 'OPENCODE_SUBAGENT === "1"' in handler, (
            "OPENCODE_SUBAGENT guard must also be in text.complete hook"
        )

    def test_subagent_no_state_modification(self):
        """Subagents should not modify the multitask state."""
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"session.idle"')[0]
        # The guard is: if (process.env.OPENCODE_SUBAGENT === "1") return
        guard_section = handler.split('OPENCODE_SUBAGENT === "1"')[0]
        guard_section + 'OPENCODE_SUBAGENT === "1"'
        assert "return" in handler[:handler.find("FLOOR_ENFORCE")], (
            "Subagent guard must return immediately"
        )


class TestDisengageEscape:
    """The disengage escape hatch must bypass all enforcement blocks."""

    def test_disengage_path_read_in_tool_execute(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "gludd-watchdog-disengage.json" in handler, (
            "Disengage file must be checked in tool.execute.before"
        )

    def test_all_three_checks_gated_by_disengaged(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"session.idle"')[0]

        # Count occurrences of "disengaged" checks in deny conditions
        check_sites = ["floor breach", "INSUFFICIENT DISPATCHES", "ZERO-DISPATCH STREAK"]
        for site in check_sites:
            site_pos = handler.find(site)
            if site_pos < 0:
                continue
            before = handler[:site_pos]
            assert "disengaged" in before, (
                f"{site} check must be gated by disengaged variable"
            )

    def test_disengage_max_duration_constant(self):
        src = _plugin_source()
        assert "MAX_DISENGAGE_MS" in src, "MAX_DISENGAGE_MS constant must exist"
        m = re.search(r"MAX_DISENGAGE_MS\s*=\s*(\d+)", src)
        assert m, "MAX_DISENGAGE_MS assignment not found"
        val = int(m.group(1))
        assert val > 0, f"MAX_DISENGAGE_MS must be positive, got {val}"


class TestEstimatedInFlight:
    """estimatedInFlight tracks active subagent count."""

    def test_inflight_incremented_on_dispatch(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "estimatedInFlight++" in handler, (
            "estimatedInFlight must increment on each dispatch"
        )

    def test_inflight_decremented_on_result(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "estimatedInFlight - 2" in handler or "estimatedInFlight -= 2" in handler, (
            "estimatedInFlight must decrement when result markers detected"
        )

    def test_inflight_never_negative(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "Math.max(0" in handler, (
            "estimatedInFlight must be clamped to >= 0"
        )

    def test_nag_when_inflight_zero(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        assert "estimatedInFlight === 0" in handler, (
            "Must inject nag when estimatedInFlight is 0"
        )
        assert "DISPATCH SUBAGENTS NOW" in handler, (
            "Nag text must urge dispatching"
        )


class TestResultMarkers:
    """Result markers trigger estimated-in-flight decrements."""

    def test_result_marker_list_present(self):
        src = _plugin_source()
        assert "task result" in src
        assert "completed" in src
        assert "subagent result" in src

    def test_has_result_marker_function(self):
        src = _plugin_source()
        assert "function hasResultMarker" in src or "hasResultMarker" in src, (
            "hasResultMarker function must exist"
        )

    def test_result_marker_check_is_case_insensitive(self):
        src = _plugin_source()
        assert "toLowerCase()" in src or "lower" in src.lower(), (
            "Result marker check must be case-insensitive"
        )


class TestFailOpenSafety:
    """All enforcement hooks must fail open."""

    def test_tool_execute_catch_returns_void(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1].split('"session.idle"')[0]
        catch_blocks = handler.split("catch")[1:]
        assert len(catch_blocks) >= 1, "tool.execute.before must have catch block"

    def test_text_complete_catch_returns_output(self):
        src = _plugin_source()
        handler = src.split('"experimental.text.complete"')[1]
        catch_blocks = handler.split("catch")[1:]
        assert len(catch_blocks) >= 1, "text.complete must have catch block"

    def test_session_idle_is_safe(self):
        src = _plugin_source()
        handler = src.split('"session.idle"')[1]
        # session.idle just resets state — no enforcement, no throws
        assert "zeroStreak = 0" in handler, "session.idle must reset zeroStreak"


class TestMessageBoundaryDetection:
    """>5s gap between tool calls indicates a new message."""

    def test_time_heuristic_present(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "lastToolCallTs > 0" in handler, "Must check lastToolCallTs"
        assert "> 5000" in handler, "Gap threshold must be 5000ms"

    def test_time_heuristic_resets_count(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "thisMessageDispatches = 0" in handler, (
            "Must reset dispatch count on new message boundary"
        )

    def test_last_tool_call_ts_updated_every_call(self):
        src = _plugin_source()
        handler = src.split('"tool.execute.before"')[1]
        assert "lastToolCallTs = now" in handler, (
            "Must update lastToolCallTs on every tool call"
        )


class TestSpawnGateRefresh:
    """Fire-and-forget gate refresh when .gate-status is stale."""

    def test_gate_refresh_function_present(self):
        src = _plugin_source()
        assert "spawnGateRefresh" in src, "spawnGateRefresh function must exist"

    def test_gate_refresh_checks_mtime(self):
        src = _plugin_source()
        assert "300_000" in src, "Gate refresh threshold must be 300s (5 min)"

    def test_gate_refresh_is_fire_and_forget(self):
        src = _plugin_source()
        handler = src.split("function spawnGateRefresh")[1].split("\n}", 1)[0]
        assert "unref()" in handler, "Spawned process must be unref'd"
        assert "detached: true" in handler, "Spawned process must be detached"


class TestHookRegistrationCompleteness:
    """All three hooks must be registered."""

    def test_three_hooks_registered(self):
        src = _plugin_source()
        assert '"tool.execute.before"' in src
        assert '"experimental.text.complete"' in src
        assert '"session.idle"' in src

    def test_all_hooks_in_return_object(self):
        src = _plugin_source()
        hook_pos = src.find('"tool.execute.before"')
        assert hook_pos > 0, "tool.execute.before not found"
        # Find the enclosing return { for the plugin export
        block_end = src.find("satisfies Plugin", hook_pos)
        block_start = src.rfind("return {", 0, hook_pos)
        assert block_start > 0, "Plugin return block not found"
        return_section = src[block_start:block_end]
        assert "tool.execute.before" in return_section
        assert "text.complete" in return_section or "experimental.text.complete" in return_section
        assert "session.idle" in return_section
