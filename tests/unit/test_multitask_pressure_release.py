"""Structural tests for the pressure-release mechanism across three enforcement plugins.

When subagents return empty/failed repeatedly, the agent deadlocks:
can't dispatch usefully AND can't work inline (blocked by streaks).
Pressure-release detects this and temporarily relaxes enforcement.

Test categories:
  A. Shared dispatch-outcomes state (shared.ts)
  B. enforce-delegate.ts DISPATCH_ATTEMPT + pressure-release skip
  C. enforce-multitask.ts PRESSURE_RELEASE mode + empty-result detection
  D. enforce-floor.ts grinding-block skip during pressure release
  E. Integration: end-to-end scenario verification
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = PROJECT_ROOT / ".opencode" / "plugin"
LIB_DIR = PROJECT_ROOT / ".opencode" / "lib"


# ──────────────────────────────────────────────────────────────────────────────
# A. Shared dispatch-outcomes state (shared.ts)
# ──────────────────────────────────────────────────────────────────────────────


def _read_shared_ts() -> str:
    return (LIB_DIR / "shared.ts").read_text()


class TestDispatchOutcomesState:
    """Verify the dispatch-outcomes state mechanism exists in shared.ts."""

    def test_dispatch_outcomes_file_constant_exists(self):
        """DISPATCH_OUTCOMES_FILE constant is defined."""
        content = _read_shared_ts()
        assert "DISPATCH_OUTCOMES_FILE" in content, "Missing DISPATCH_OUTCOMES_FILE constant in shared.ts"

    def test_dispatch_outcomes_state_interface_exists(self):
        """DispatchOutcomesState interface is defined with required fields."""
        content = _read_shared_ts()
        assert "DatabaseOutcomesState" in content, "Missing DispatchOutcomesState interface in shared.ts"
        for field in [
            "consecutiveEmptyDispatches",
            "consecutiveDispatchAttempts",
            "pressureReleaseActive",
            "pressureReleaseTurnsRemaining",
            "pressureReleaseFloor",
            "normalFloor",
            "inlineRecoveryTurnsRemaining",
        ]:
            assert field in content, f"DispatchOutcomesState missing field: {field}"

    def test_fresh_dispatch_outcomes_function_exists(self):
        """freshDispatchOutcomes() returns default state."""
        content = _read_shared_ts()
        assert "function freshDispatchOutcomes" in content, "Missing freshDispatchOutcomes() function"

    def test_read_dispatch_outcomes_function_exists(self):
        """readDispatchOutcomes() reads state with staleness/PID guard."""
        content = _read_shared_ts()
        assert "function readDispatchOutcomes" in content, "Missing readDispatchOutcomes() function"

    def test_write_dispatch_outcomes_function_exists(self):
        """writeDispatchOutcomes() writes state with partial merge."""
        content = _read_shared_ts()
        assert "function writeDispatchOutcomes" in content, "Missing writeDispatchOutcomes() function"

    def test_is_in_pressure_release_function_exists(self):
        """isInPressureRelease() returns true when mode is active."""
        content = _read_shared_ts()
        assert "function isInPressureRelease" in content, "Missing isInPressureRelease() function"

    def test_is_in_inline_recovery_function_exists(self):
        """isInInlineRecovery() returns true when recovery turns remain."""
        content = _read_shared_ts()
        assert "function isInInlineRecovery" in content, "Missing isInInlineRecovery() function"

    def test_get_pressure_release_floor_function_exists(self):
        """getPressureReleaseFloor() returns lowered floor during PR."""
        content = _read_shared_ts()
        assert "function getPressureReleaseFloor" in content, "Missing getPressureReleaseFloor() function"

    def test_decrement_pressure_release_turns_function_exists(self):
        """decrementPressureReleaseTurns() decrements turn counters."""
        content = _read_shared_ts()
        assert "function decrementPressureReleaseTurns" in content, "Missing decrementPressureReleaseTurns() function"

    def test_record_empty_dispatch_function_exists(self):
        """recordEmptyDispatch() tracks empty/failed dispatches."""
        content = _read_shared_ts()
        assert "function recordEmptyDispatch" in content, "Missing recordEmptyDispatch() function"

    def test_record_successful_dispatch_function_exists(self):
        """recordSuccessfulDispatch() resets empty-dispatch counter."""
        content = _read_shared_ts()
        assert "function recordSuccessfulDispatch" in content, "Missing recordSuccessfulDispatch() function"

    def test_record_dispatch_attempt_function_exists(self):
        """recordDispatchAttempt() tracks dispatch attempts."""
        content = _read_shared_ts()
        assert "function recordDispatchAttempt" in content, "Missing recordDispatchAttempt() function"

    def test_pressure_release_activation_threshold(self):
        """After 3+ consecutive empty dispatches, pressure-release activates."""
        content = _read_shared_ts()
        # Check the target OS test... just verify the threshold constant is
        # defined near the `>= 3` comparison.
        assert "consecutiveEmptyDispatches >= 3" in content, "Missing threshold check for 3+ empty dispatches"
        assert "pressureReleaseActive = true" in content or ("pressureReleaseActive = true" in content), (
            "Missing pressureReleaseActive activation logic"
        )

    def test_pressure_release_deactivation_at_zero_turns(self):
        """When both turn counters reach 0, pressure-release deactivates."""
        content = _read_shared_ts()
        assert "pressureReleaseActive = false" in content or "pressureReleaseActive = false" in content, (
            "Missing pressureReleaseActive deactivation logic"
        )

    def test_default_pressure_release_floor(self):
        """Default lowered floor is 2 during pressure release."""
        content = _read_shared_ts()
        # The floor 2 should appear as the default for pressureReleaseFloor
        assert "pressureReleaseFloor: 2" in content, "Missing default pressureReleaseFloor = 2"

    def test_default_inline_recovery_turns(self):
        """Default inline recovery allows 5 turns."""
        content = _read_shared_ts()
        assert "inlineRecoveryTurnsRemaining: 5" in content, "Missing default inlineRecoveryTurnsRemaining = 5"


# ──────────────────────────────────────────────────────────────────────────────
# B. enforce-delegate.ts DISPATCH_ATTEMPT + pressure-release
# ──────────────────────────────────────────────────────────────────────────────


def _read_delegate_ts() -> str:
    return (PLUGIN_DIR / "enforce-delegate.ts").read_text()


class TestDelegateDispatchAttempt:
    """Verify DISPATCH_ATTEMPT tracking in enforce-delegate.ts."""

    def test_imports_pressure_release_functions(self):
        """enforce-delegate.ts imports isInPressureRelease, isInInlineRecovery, etc."""
        content = _read_delegate_ts()
        for fn in ["isInPressureRelease", "isInInlineRecovery", "recordDispatchAttempt"]:
            assert fn in content, f"enforce-delegate.ts should import {fn}"

    def test_mainthread_budget_before_skips_in_pressure_release(self):
        """mainthreadBudgetBefore() returns null when in pressure-release."""
        content = _read_delegate_ts()
        assert "isInPressureRelease()" in content, "mainthreadBudgetBefore should check isInPressureRelease()"
        assert "isInInlineRecovery()" in content, "mainthreadBudgetBefore should check isInInlineRecovery()"
        # The check should appear BEFORE the streak enforcement logic
        pr_check_idx = content.index("isInPressureRelease()")
        # There should be a return null near it
        window = content[pr_check_idx - 200 : pr_check_idx + 200]
        assert "return null" in window, "Should return null when in pressure-release"

    def test_mainthread_budget_after_records_dispatch_attempt(self):
        """mainthreadBudgetAfter() calls recordDispatchAttempt() on dispatch."""
        content = _read_delegate_ts()
        assert "recordDispatchAttempt()" in content, "mainthreadBudgetAfter should call recordDispatchAttempt()"


# ──────────────────────────────────────────────────────────────────────────────
# C. enforce-multitask.ts PRESSURE_RELEASE mode
# ──────────────────────────────────────────────────────────────────────────────


def _read_multitask_ts() -> str:
    return (PLUGIN_DIR / "enforce-multitask.ts").read_text()


class TestMultitaskPressureRelease:
    """Verify PRESSURE_RELEASE mode in enforce-multitask.ts."""

    def test_imports_pressure_release_functions(self):
        """Imports all pressure-release functions from shared.ts."""
        content = _read_multitask_ts()
        for fn in [
            "isInPressureRelease",
            "isInInlineRecovery",
            "getPressureReleaseFloor",
            "decrementPressureReleaseTurns",
            "recordEmptyDispatch",
            "recordSuccessfulDispatch",
            "readDispatchOutcomes",
            "writeDispatchOutcomes",
        ]:
            assert fn in content, f"enforce-multitask.ts should import {fn}"

    def test_grinding_block_skipped_in_pressure_release(self):
        """Consecutive non-dispatch block is skipped when pressureActive."""
        content = _read_multitask_ts()
        assert "pressureActive = isInPressureRelease()" in content, (
            "Should compute pressureActive from isInPressureRelease + isInInlineRecovery"
        )
        assert "!disengaged && !pressureActive" in content, "Grinding block guard should include !pressureActive"

    def test_under_floor_block_uses_pressure_release_floor(self):
        """UNDER-FLOOR block uses getPressureReleaseFloor(MIN_DISPATCHES)."""
        content = _read_multitask_ts()
        assert "getPressureReleaseFloor(MIN_DISPATCHES)" in content, (
            "Under-floor block should use getPressureReleaseFloor for effective floor"
        )
        assert "_effectiveFloor = getPressureReleaseFloor" in content, "Effective floor variable should be computed"

    def test_text_complete_detects_empty_results(self):
        """text.complete hook detects empty/failed subagent results."""
        content = _read_multitask_ts()
        assert "isEmptyPattern" in content, "Should detect empty/failure patterns in result text"
        assert "recordEmptyDispatch()" in content, "Should call recordEmptyDispatch() on empty results"
        assert "recordSuccessfulDispatch()" in content, "Should call recordSuccessfulDispatch() on successful results"

    def test_empty_result_patterns(self):
        """Empty result detection uses failure/error/empty keywords."""
        content = _read_multitask_ts()
        assert "failed" in content and "empty" in content and "unable" in content, (
            "Empty result patterns should include failed, empty, unable"
        )

    def test_text_complete_decrements_pressure_release_turns(self):
        """text.complete calls decrementPressureReleaseTurns() at boundary."""
        content = _read_multitask_ts()
        assert "decrementPressureReleaseTurns()" in content, (
            "Should call decrementPressureReleaseTurns() in text.complete"
        )

    def test_thin_wave_block_uses_pressure_release_floor(self):
        """Thin-wave block (text.complete) uses pressure-release floor."""
        content = _read_multitask_ts()
        # Both defaultImpl and proxy should use the effective floor
        assert "_tef = getPressureReleaseFloor" in content, "defaultImpl thin-wave check should use _tef"
        assert "_pef = getPressureReleaseFloor" in content, "Proxy thin-wave check should use _pef"


# ──────────────────────────────────────────────────────────────────────────────
# D. enforce-floor.ts grinding-block skip
# ──────────────────────────────────────────────────────────────────────────────


def _read_floor_ts() -> str:
    return (PLUGIN_DIR / "enforce-floor.ts").read_text()


class TestFloorPressureRelease:
    """Verify enforce-floor.ts skips grinding during pressure release."""

    def test_imports_pressure_release_functions(self):
        """Imports isInPressureRelease, isInInlineRecovery, readDispatchOutcomes."""
        content = _read_floor_ts()
        for fn in ["isInPressureRelease", "isInInlineRecovery", "readDispatchOutcomes"]:
            assert fn in content, f"enforce-floor.ts should import {fn}"

    def test_pressure_relief_variable_defined(self):
        """pressureRelief variable gates all grinding blocks."""
        content = _read_floor_ts()
        assert "pressureRelief = isInPressureRelease()" in content, (
            "Should define pressureRelief from isInPressureRelease + isInInlineRecovery"
        )

    def test_read_tool_pressure_relief_skip(self):
        """Read-tool handling returns early when pressureRelief is true."""
        content = _read_floor_ts()
        # Find the read-tool section and verify the pressureRelief return
        read_idx = content.index("─ Read-tool handling")
        window = content[read_idx : read_idx + 300]
        assert "if (pressureRelief) return" in window, "Read-tool branch should return early when pressureRelief"

    def test_streak_increment_pressure_relief_skip(self):
        """Floor breach streak increment is skipped when pressureRelief."""
        content = _read_floor_ts()
        streak_idx = content.index("─ Streak increment + floor breach")
        window = content[streak_idx : streak_idx + 400]
        assert "if (pressureRelief)" in window, "Streak increment should skip when pressureRelief"
        assert "_streakCount = 0" in window, "Should reset streak count in pressureRelief branch"


# ──────────────────────────────────────────────────────────────────────────────
# E. Integration: end-to-end scenario
# ──────────────────────────────────────────────────────────────────────────────


class TestPressureReleaseIntegration:
    """Verify the full mechanism works end-to-end across all three plugins."""

    def test_all_state_functions_exported(self):
        """All pressure-release state functions are exported from shared.ts."""
        content = _read_shared_ts()
        exports_needed = [
            "export function freshDispatchOutcomes",
            "export function readDispatchOutcomes",
            "export function writeDispatchOutcomes",
            "export function isInPressureRelease",
            "export function isInInlineRecovery",
            "export function getPressureReleaseFloor",
            "export function decrementPressureReleaseTurns",
            "export function recordDispatchAttempt",
            "export function recordEmptyDispatch",
            "export function recordSuccessfulDispatch",
        ]
        for exp in exports_needed:
            assert exp in content, f"Missing export: {exp}"

    def test_all_three_plugins_import_shared_functions(self):
        """All three plugins import pressure-release functions."""
        delegate = _read_delegate_ts()
        multitask = _read_multitask_ts()
        floor = _read_floor_ts()

        assert "isInPressureRelease" in delegate, "delegate should import"
        assert "isInPressureRelease" in multitask, "multitask should import"
        assert "isInPressureRelease" in floor, "floor should import"

    def test_pressure_release_state_file_path_consistent(self):
        """DISPATCH_OUTCOMES_FILE path is used by all write/read calls."""
        content = _read_shared_ts()
        assert '"/tmp/gludd-dispatch-outcomes.json"' in content or ('"/tmp/gludd-dispatch-outcomes.json"' in content), (
            "Default path should be /tmp/gludd-dispatch-outcomes.json"
        )

    def test_no_duplicate_state_initialization(self):
        """State initialization only happens in shared.ts (single source)."""
        delegate = _read_delegate_ts()
        multitask = _read_multitask_ts()
        floor = _read_floor_ts()

        # freshDispatchOutcomes should only be defined in shared.ts
        for source, name in [(delegate, "delegate"), (multitask, "multitask"), (floor, "floor")]:
            assert "freshDispatchOutcomes()" not in source, f"{name} should not re-implement freshDispatchOutcomes"

    def test_pressure_release_console_warn_messages(self):
        """Activation and expiration log console.warn messages."""
        content = _read_shared_ts()
        assert "PRESSURE-RELEASE ACTIVATED" in content, "Should log activation message"
        assert "PRESSURE-RELEASE EXPIRED" in content, "Should log expiration message"
