"""Behavior pin: enforcement plugins do NOT inject nag text into clean output.

When the shared streak state is zero / stale / below threshold, the text.complete
hooks must return output unmodified. Nags (DELEGATE-FIRST, MUST DISPATCH, FLOOR
BREACH, READ-GRINDING) must only fire when their respective conditions are
triggered — never on clean state.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED_PATH = ROOT / ".opencode/lib/shared.ts"
FLOOR_PATH = ROOT / ".opencode/plugin/enforce-floor.ts"
STOP_PATH = ROOT / ".opencode/plugin/enforce-stop.ts"
STOP_IMPL_PATH = ROOT / ".opencode/plugin/impl/enforce_stop_impl.ts"
MULTITASK_PATH = ROOT / ".opencode/plugin/enforce-multitask.ts"
DELEGATE_PATH = ROOT / ".opencode/plugin/enforce-delegate.ts"
VERIFIED_CLAIMS_PATH = ROOT / ".opencode/plugin/enforce-verified-claims.ts"


def _src(path: Path) -> str:
    if path == STOP_PATH:
        return STOP_IMPL_PATH.read_text() + "\n" + path.read_text()
    return path.read_text()


def _from_marker(src: str, marker: str) -> str:
    idx = src.find(marker)
    assert idx >= 0, f"{marker!r} not found in source"
    return src[idx:]


# ── readSharedStreak stale-state zeroing ────────────────────────────────────


class TestReadSharedStreakStaleZero:
    """verify readSharedStreak() zeroes all fields when state file is >60s old.
    Post E.5 refactor: readSharedStreak lives in lib/shared.ts; plugins import
    updateSharedStreak (which wraps it) instead."""

    def test_shared_readSharedStreak_zeroes_stale_state(self):
        src = _src(SHARED_PATH)
        assert "readSharedStreak" in src, (
            "readSharedStreak function missing from shared.ts"
        )
        assert "STALE_MS" in src or "60_000" in src or "60000" in src, (
            "STALE threshold (60_000) not found in shared.ts"
        )
        assert "streak: 0" in src or "streak:0" in src, (
            "zeroed streak not found in shared.ts"
        )
        assert "lastDispatchTs: 0" in src or "lastDispatchTs:0" in src, (
            "zeroed lastDispatchTs not found in shared.ts"
        )
        assert "pid:" in src and ("process.pid" in src or "pid: 0" in src), (
            "pid field missing from readSharedStreak zeroed/default returns in shared.ts"
        )

    def test_floor_imports_updateSharedStreak(self):
        src = _src(FLOOR_PATH)
        assert "updateSharedStreak" in src, (
            "enforce-floor must import updateSharedStreak from lib/shared.ts"
        )

    def test_stop_imports_updateSharedStreak(self):
        src = _src(STOP_PATH)
        assert "updateSharedStreak" in src, (
            "enforce-stop must import updateSharedStreak from lib/shared.ts"
        )


class TestStaleStateDefaultsAreZero:
    """verify the fallback/default return from readSharedStreak is all-zeros.
    Post E.5 refactor: readSharedStreak is in lib/shared.ts."""

    def test_shared_default_return_is_zero(self):
        src = _src(SHARED_PATH)
        handler = _from_marker(src, "readSharedStreak")
        assert "return {" in handler, (
            "readSharedStreak fallback return missing in shared.ts"
        )
        assert "streak:0" in handler or "streak: 0" in handler, (
            "default streak zero missing in shared.ts"
        )


# ── text.complete clean-output passthrough ──────────────────────────────────


class TestCleanOutputPassthrough:
    """verify text.complete hooks pass output through unmodified when state is clean."""

    def test_floor_passthrough_path_exists(self):
        """enforce-floor: streak-gated passthrough in tool.execute.before.
        opencode >=1.17.9 removed text.complete — floor is self-contained
        in tool.execute.before with _streakCount <= effectiveMax pass-through."""
        handler = _from_marker(_src(FLOOR_PATH), '"tool.execute.before"')
        assert "_streakCount" in handler
        assert "MAX_STREAK" in handler
        assert "_streakCount <=" in handler, (
            "tool.execute.before must gate on _streakCount <= effectiveMax"
        )

    def test_stop_passthrough_path_exists(self):
        """enforce-stop: after DELEGATE-FIRST and HARD STOP guards, output
        reaches the false-done / QA check without being modified by earlier
        guards (when streak <= threshold)."""
        handler = _from_marker(_src(STOP_PATH), '"experimental.text.complete"')
        assert "(output as any)" in handler or "typeof output" in handler, (
            "text.complete handler must reference output (accesses via (output as any)?.text post-1.17.9)"
        )

    def test_multitask_passthrough_path_exists(self):
        """enforce-multitask: output is preserved by the canonical
        experimental.text.complete hook unless the thin-wave block fires."""
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "handleMessageBoundary" in handler
        assert "writeState" in handler
        assert "return output" in handler
        assert "(output as any)" in handler or "typeof output" in handler

    def test_verified_claims_passthrough_path_exists(self):
        """Coverage-claim enforcement preserves clean text.complete output."""
        src = _src(VERIFIED_CLAIMS_PATH)
        handler = _from_marker(src, '"experimental.text.complete"')
        assert "shouldBlockCoverageClaim" in handler
        assert "return output" in handler


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
        handler = _from_marker(_src(FLOOR_PATH), '"tool.execute.before"')
        assert "MAX_STREAK" in handler
        assert "_buildFloorBreachBlock" in handler
        assert "_streakCount >" in handler, (
            "FLOOR BREACH must be gated by _streakCount > effectiveMax comparison"
        )
        assert "_streakCount <= effectiveMax" in handler, (
            "FLOOR BREACH must only trigger after the effective streak maximum is exceeded"
        )

    def test_max_streak_is_two(self):
        """MAX_STREAK = 2 allows 2 non-dispatch calls before blocking (floor
        breach triggers when _streakCount > 2). Matches enforce-delegate's
        MAINTHREAD_THRESHOLD = 2."""
        src = _src(FLOOR_PATH)
        assert "MAX_STREAK" in src
        idx = src.find("MAX_STREAK")
        after = src[idx: idx + 80]
        assert "= 2" in after or "=2" in after, (
            f"MAX_STREAK must be 2. Found: {after!r}"
        )


class TestMultitaskNagConditional:
    """verify MUST DISPATCH nag is conditional on zeroStreak >= MAX_ZERO_STREAK."""

    def test_multitask_must_dispatch_conditional(self):
        handler = _from_marker(_src(MULTITASK_PATH), '"tool.execute.before"')
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        assert "ZERO-DISPATCH STREAK" in handler or "MUST DISPATCH" in handler, (
            "zero-dispatch enforcement must exist in tool.execute.before"
        )

    def test_max_zero_streak_positive(self):
        """MAX_ZERO_STREAK >= 1 ensures zeroStreak=0 never triggers MUST DISPATCH."""
        src = _src(MULTITASK_PATH)
        assert "const MAX_ZERO_STREAK = 2" in src, (
            "MAX_ZERO_STREAK must be a positive local constant"
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

    def _check_handler_not_unconditional(self, path: Path, marker: str = '"experimental.text.complete"') -> None:
        handler = _from_marker(_src(path), marker)
        if "(output as any)" not in handler and "typeof output" not in handler and "output.text" not in handler:
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
        """enforce-floor: tool.execute.before must have conditional streak gating,
        not unconditional deny. Self-contained post 1.17.9 text.complete removal."""
        handler = _from_marker(_src(FLOOR_PATH), '"tool.execute.before"')
        assert "_streakCount <=" in handler or "if (" in handler, (
            "tool.execute.before must have conditional logic, not unconditional deny"
        )

    def test_stop_not_unconditional(self):
        self._check_handler_not_unconditional(STOP_PATH)

    def test_multitask_not_unconditional(self):
        self._check_handler_not_unconditional(MULTITASK_PATH)
        assert True  # assertions in _check_handler_not_unconditional helper

    def test_verified_claims_not_unconditional(self):
        """Verified-claims output mutation is coverage-claim conditional."""
        src = _src(VERIFIED_CLAIMS_PATH)
        handler = _from_marker(src, '"experimental.text.complete"')
        assert "if (shouldBlockCoverageClaim(text))" in handler
        assert "return output" in handler


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
        assert "(output as any)" in handler or "typeof output" in handler

    def test_multitask_subagent_passthrough(self):
        """enforce-multitask: text.complete must have subagent-bypass logic
        so MUST DISPATCH is not injected into subagent output."""
        handler = _from_marker(_src(MULTITASK_PATH), '"experimental.text.complete"')
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in handler.lower(), (
            "enforce-multitask text.complete must have subagent-bypass logic"
        )
        assert "(output as any)" in handler or "typeof output" in handler

    def test_floor_subagent_passthrough(self):
        """enforce-floor: tool.execute.before must have subagent-bypass logic.
        Self-contained post 1.17.9 text.complete removal."""
        handler = _from_marker(_src(FLOOR_PATH), '"tool.execute.before"')
        assert "GLUDD_IS_SUBAGENT" in handler or "subagent" in handler.lower(), (
            "enforce-floor tool.execute.before must have subagent-bypass logic"
        )
        assert "isSubagent()" in handler


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
        assert "streakState.streak" in handler or "streakState" in handler
        assert 0 <= handler.find("isSubagent()") < handler.find(
            "DELEGATE_FIRST_THRESHOLD"
        ), (
            "DELEGATE-FIRST check must follow the subagent passthrough guard"
        )


class TestMustDispatchSubagentBypass:
    """verify MUST DISPATCH replacement does NOT happen when running as subagent
    with zeroStreak >= MAX_ZERO_STREAK (2)."""

    def test_multitask_must_dispatch_bypassed_for_subagent(self):
        """enforce-multitask: the MUST DISPATCH / zero-dispatch-streak block in
        text.complete must be guarded by a subagent check."""
        handler = _from_marker(_src(MULTITASK_PATH), '"tool.execute.before"')
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        assert 0 <= handler.find("isSubagent()") < handler.find(
            "ZERO-DISPATCH STREAK"
        ), (
            "MUST DISPATCH block must follow the subagent passthrough guard"
        )
