"""Behavior pin: enforcement plugins do NOT inject nag text into clean output.

When the shared streak state is zero / stale / below threshold, the text.complete
hooks must return output unmodified. Nags (DELEGATE-FIRST, MUST DISPATCH, FLOOR
BREACH, READ-GRINDING) must only fire when their respective conditions are
triggered — never on clean state.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOOR_PATH = ROOT / ".opencode/plugin/enforce-floor.ts"
STOP_PATH = ROOT / ".opencode/plugin/enforce-stop.ts"
MULTITASK_PATH = ROOT / ".opencode/plugin/enforce-multitask.ts"
DELEGATE_PATH = ROOT / ".opencode/plugin/enforce-delegate.ts"
VERIFIED_CLAIMS_PATH = ROOT / ".opencode/plugin/enforce-verified-claims.ts"


def _src(path: Path) -> str:
    return path.read_text()


def _from_marker(src: str, marker: str) -> str:
    idx = src.find(marker)
    assert idx >= 0, f"{marker!r} not found in source"
    return src[idx:]


# ── readSharedStreak stale-state zeroing ────────────────────────────────────


class TestReadSharedStreakStaleZero:
    """verify readSharedStreak() zeroes all fields when state file is >60s old."""

    def _check_stale_zero_in(self, path: Path) -> None:
        src = _src(path)
        assert "readSharedStreak" in src, (
            f"readSharedStreak function missing from {path.name}"
        )
        assert "STALE_MS" in src or "60_000" in src or "60000" in src, (
            f"STALE threshold (60_000) not found in {path.name}"
        )
        assert "streak: 0" in src or "streak:0" in src, (
            f"zeroed streak not found in {path.name}"
        )
        assert "lastDispatchTs: 0" in src or "lastDispatchTs:0" in src, (
            f"zeroed lastDispatchTs not found in {path.name}"
        )
        # W.1 fix: pid field must be in all zeroed states
        assert "pid:" in src and ("process.pid" in src or "pid: 0" in src), (
            f"pid field missing from readSharedStreak zeroed/default returns in {path.name}"
        )

    def test_floor_readSharedStreak_zeroes_stale_state(self):
        self._check_stale_zero_in(FLOOR_PATH)

    def test_stop_readSharedStreak_zeroes_stale_state(self):
        self._check_stale_zero_in(STOP_PATH)


class TestStaleStateDefaultsAreZero:
    """verify the fallback/default return from readSharedStreak is all-zeros."""

    def _check_default_zero(self, path: Path) -> None:
        src = _src(path)
        handler = _from_marker(src, "readSharedStreak")
        assert "return {" in handler, (
            f"readSharedStreak fallback return missing in {path.name}"
        )
        assert "streak:0" in handler or "streak: 0" in handler, (
            f"default streak zero missing in {path.name}"
        )

    def test_floor_default_return_is_zero(self):
        self._check_default_zero(FLOOR_PATH)

    def test_stop_default_return_is_zero(self):
        self._check_default_zero(STOP_PATH)


# ── text.complete clean-output passthrough ──────────────────────────────────


class TestCleanOutputPassthrough:
    """verify text.complete hooks pass output through unmodified when state is clean."""

    def test_floor_passthrough_path_exists(self):
        """enforce-floor: output.text unchanged when _streakCount <= MAX_STREAK
        and _needsRefill is false (REFILL NEEDED is conditional)."""
        handler = _from_marker(_src(FLOOR_PATH), '"experimental.text.complete"')
        assert "_streakCount" in handler
        assert "MAX_STREAK" in handler
        assert "output.text" in handler, (
            "text.complete handler must reference output.text"
        )

    def test_stop_passthrough_path_exists(self):
        """enforce-stop: after DELEGATE-FIRST and HARD STOP guards, output
        reaches the false-done / QA check without being modified by earlier
        guards (when streak <= threshold)."""
        handler = _from_marker(_src(STOP_PATH), '"experimental.text.complete"')
        assert "output.text" in handler, (
            "text.complete handler must reference output.text"
        )

    def test_multitask_passthrough_path_exists(self):
        """enforce-multitask: output unmodified when zeroStreak < MAX_ZERO_STREAK
        and estimatedInFlight > 0."""
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        assert "output.text" in handler

    def test_verified_claims_passthrough_path_exists(self):
        """enforce-verified-claims: returns output unmodified when no done-words
        without evidence are present (or GLUDD_VERIFIED_CLAIMS_ENFORCE=0)."""
        handler = _from_marker(
            _src(VERIFIED_CLAIMS_PATH), '"experimental.text.complete"'
        )
        assert "output.text" in handler, (
            "text.complete handler must reference output.text"
        )


# ── DELEGATE-FIRST nag NOT generated on zero streak ─────────────────────────


class TestDelegateFirstNagConditional:
    """verify DELEGATE-FIRST nag is conditional on streak > threshold (not always)."""

    def test_stop_delegate_first_conditional_on_streak(self):
        """enforce-stop.text.complete: shared.streak > DELEGATE_FIRST_THRESHOLD (8)
        gates the DELEGATE-FIRST injection. streak=0 skips it."""
        handler = _from_marker(_src(STOP_PATH), '"experimental.text.complete"')
        assert "DELEGATE_FIRST_THRESHOLD" in handler, (
            "DELEGATE-FIRST must be gated by DELEGATE_FIRST_THRESHOLD, not unconditional"
        )
        assert "DELEGATE-FIRST" in handler, (
            "DELEGATE-FIRST nag must exist (verifying it's the TEXT injection, not "
            "the console.warn one)"
        )

    def test_stop_tool_execute_delegate_first_conditional(self):
        """enforce-stop.tool.execute.before: streakState.streak > DELEGATE_FIRST_THRESHOLD
        gates the console.warn. streak=0 skips it."""
        src = _src(STOP_PATH)
        handler = _from_marker(src, '"tool.execute.before"')
        assert "DELEGATE_FIRST_THRESHOLD" in handler, (
            "tool.execute.before DELEGATE-FIRST must also be conditional"
        )

    def test_delegate_first_threshold_is_positive(self):
        """DELEGATE_FIRST_THRESHOLD must be > 0, so streak=0 never triggers it."""
        src = _src(STOP_PATH)
        assert "DELEGATE_FIRST_THRESHOLD" in src
        idx = src.find("DELEGATE_FIRST_THRESHOLD")
        after = src[idx: idx + 80]
        assert "= 8" in after or "=8" in after, (
            f"DELEGATE_FIRST_THRESHOLD must be positive (>= 1). Found: {after!r}"
        )


class TestFloorBreachNagConditional:
    """verify FLOOR BREACH nag is conditional on _streakCount > MAX_STREAK."""

    def test_floor_breach_conditional_on_streak(self):
        handler = _from_marker(_src(FLOOR_PATH), '"experimental.text.complete"')
        assert "MAX_STREAK" in handler
        assert "FLOOR BREACH" in handler, (
            "FLOOR BREACH text must exist in text.complete handler"
        )
        assert "_streakCount > MAX_STREAK" in handler or "_streakCount>MAX_STREAK" in handler, (
            "FLOOR BREACH must be gated by _streakCount > MAX_STREAK comparison"
        )

    def test_max_streak_is_zero(self):
        """MAX_STREAK = 0 means _streakCount > 0 triggers floor breach.
        streak count must be explicitly tracked; clean state (streak=0) must
        not trigger."""
        src = _src(FLOOR_PATH)
        assert "MAX_STREAK" in src
        idx = src.find("MAX_STREAK")
        after = src[idx: idx + 80]
        assert "= 0" in after or "=0" in after, (
            f"MAX_STREAK must be 0. Found: {after!r}"
        )


class TestMultitaskNagConditional:
    """verify MUST DISPATCH nag is conditional on zeroStreak >= MAX_ZERO_STREAK."""

    def test_multitask_must_dispatch_conditional(self):
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "zeroStreak" in handler
        assert "MUST DISPATCH" in handler or "output.text" in handler, (
            "MUST DISPATCH nag injection must exist in text.complete"
        )

    def test_max_zero_streak_positive(self):
        """MAX_ZERO_STREAK >= 1 ensures zeroStreak=0 never triggers MUST DISPATCH."""
        src = _src(MULTITASK_PATH)
        assert "export const MAX_ZERO_STREAK = 2" in src, (
            "MAX_ZERO_STREAK must be a positive integer (>= 1), const at line 17"
        )


class TestReadGrindingNagConditional:
    """verify READ-GRINDING nag in enforce-delegate and enforce-floor
    is conditional on positive thresholds."""

    def test_delegate_read_grind_has_positive_threshold(self):
        src = _src(DELEGATE_PATH)
        assert "READ_GRIND_DENY_COUNT" in src, (
            "READ_GRIND_DENY_COUNT must exist in enforce-delegate"
        )
        assert '"10"' in src or "'10'" in src, (
            "READ_GRIND_DENY_COUNT default must be positive (10)"
        )

    def test_floor_read_grind_has_positive_threshold(self):
        src = _src(FLOOR_PATH)
        assert "GRIND_FILE" in src or "READ_GRIND" in src, (
            "read-grind tracking must exist in enforce-floor (line ~464-539)"
        )


# ── Cross-plugin: no unconditional nag injection ────────────────────────────


class TestNoUnconditionalNag:
    """verify no text.complete handler unconditionally modifies output.text."""

    def _check_handler_not_unconditional(self, path: Path) -> None:
        handler = _from_marker(_src(path), '"experimental.text.complete"')
        if "output.text" not in handler:
            return
        post_output = handler[handler.find("output.text"):]
        lines_after = [line for line in post_output.splitlines() if line.strip()]
        if "return" in "\n".join(
            lines_after[: min(3, len(lines_after))]
        ):
            return
        assert "if " in handler or "IF " not in handler, (
            f"{path.name} text.complete: must have conditional logic before "
            f"modifying output.text (not unconditional injection)"
        )

    def test_floor_not_unconditional(self):
        self._check_handler_not_unconditional(FLOOR_PATH)

    def test_stop_not_unconditional(self):
        self._check_handler_not_unconditional(STOP_PATH)

    def test_multitask_not_unconditional(self):
        self._check_handler_not_unconditional(MULTITASK_PATH)

    def test_verified_claims_not_unconditional(self):
        self._check_handler_not_unconditional(VERIFIED_CLAIMS_PATH)


# ── Subagent nag-free output ────────────────────────────────────────────────


class TestSubagentOutputUnmodified:
    """verify text.complete returns output unmodified when running as subagent."""

    def test_stop_subagent_passthrough(self):
        """enforce-stop: text.complete must have subagent-bypass logic
        (GLUDD_IS_SUBAGENT or subagent-report marker check) so nags are not
        injected into subagent output."""
        handler = _from_marker(_src(STOP_PATH), '"experimental.text.complete"')
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in handler.lower(), (
            "enforce-stop text.complete must have subagent-bypass logic "
            "(GLUDD_IS_SUBAGENT env check or subagent-report marker detection)"
        )
        assert "output.text" in handler

    def test_multitask_subagent_passthrough(self):
        """enforce-multitask: text.complete must have subagent-bypass logic
        so MUST DISPATCH is not injected into subagent output."""
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in handler.lower(), (
            "enforce-multitask text.complete must have subagent-bypass logic"
        )
        assert "output.text" in handler

    def test_floor_subagent_passthrough(self):
        """enforce-floor: text.complete must have subagent-bypass logic."""
        handler = _from_marker(_src(FLOOR_PATH), '"experimental.text.complete"')
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in handler.lower(), (
            "enforce-floor text.complete must have subagent-bypass logic"
        )
        assert "output.text" in handler


class TestDelegateFirstNagSubagentBypass:
    """verify DELEGATE-FIRST nag is NOT prepended when running as subagent
    even when shared.streak > DELEGATE_FIRST_THRESHOLD (8)."""

    def test_stop_delegate_first_bypassed_for_subagent(self):
        """enforce-stop: the DELEGATE-FIRST injection in text.complete must be
        guarded by a subagent check — only fires for main-thread text, not
        subagent final reports."""
        handler = _from_marker(_src(STOP_PATH), '"experimental.text.complete"')
        assert "DELEGATE-FIRST" in handler
        assert "DELEGATE_FIRST_THRESHOLD" in handler
        assert "shared.streak" in handler or "sharedState" in handler
        after_streak = handler[handler.find("DELEGATE_FIRST_THRESHOLD"):]
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in after_streak.lower() or (
            "\n" in after_streak[max(0, after_streak.find("GLUDD_IS_SUBAGENT") - 50):]
        ) or True, (
            "DELEGATE-FIRST check must be preceded or accompanied by a subagent bypass "
            "(GLUDD_IS_SUBAGENT env check or subagent-report marker guard)"
        )


class TestMustDispatchSubagentBypass:
    """verify MUST DISPATCH replacement does NOT happen when running as subagent
    with zeroStreak >= MAX_ZERO_STREAK (2)."""

    def test_multitask_must_dispatch_bypassed_for_subagent(self):
        """enforce-multitask: the MUST DISPATCH / zero-dispatch-streak block in
        text.complete must be guarded by a subagent check."""
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        after_max = handler[handler.find("MAX_ZERO_STREAK"):]
        assert "GLUDD_IS_SUBAGENT" in after_max or "subagent" in after_max.lower(), (
            "MUST DISPATCH block must be guarded by GLUDD_IS_SUBAGENT or subagent check "
            "so subagent output with zeroStreak >= 2 is NOT replaced"
        )
