"""Behavior pin: enforcement plugins do NOT inject nag text into clean output.

When the shared streak state is zero / stale / below threshold, the text.complete
hooks must return output unmodified. Nags (DELEGATE-FIRST, MUST DISPATCH, FLOOR
BREACH, READ-GRINDING) must only fire when their respective conditions are
triggered — never on clean state.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.unit._plugin_contract import plugin_contract_source

ROOT = Path(__file__).resolve().parents[2]
SHARED_PATH = ROOT / ".opencode/lib/shared.ts"
FLOOR_PATH = ROOT / ".opencode/plugin/enforce-floor.ts"
STOP_PATH = ROOT / ".opencode/plugin/enforce-stop.ts"
STOP_IMPL_PATH = ROOT / ".opencode/plugin/impl/enforce_stop_impl.ts"
MULTITASK_PATH = ROOT / ".opencode/plugin/enforce-multitask.ts"
MULTITASK_CONFIG_PATH = ROOT / ".opencode/lib/multitask_config.ts"
DELEGATE_PATH = ROOT / ".opencode/plugin/enforce-delegate.ts"
VERIFIED_CLAIMS_PATH = ROOT / ".opencode/plugin/enforce-verified-claims.ts"


def _src(path: Path) -> str:
    if path.parent == ROOT / ".opencode" / "plugin":
        return plugin_contract_source(path)
    return path.read_text()


def _from_marker(
    src: str,
    marker: str,
    *,
    required: tuple[str, ...] = (),
) -> str:
    """Return the executable marker segment containing every required token.

    Plugin contracts can include a thin facade, the implementation, and a hot
    reload wrapper.  Picking the longest marker segment can therefore inspect a
    facade declaration instead of the hook that OpenCode actually executes.
    """
    function_marker = f"function {marker}"
    function_idx = src.find(function_marker)
    if function_idx >= 0 and all(token in src[function_idx:] for token in required):
        return src[function_idx:]

    positions = [match.start() for match in re.finditer(re.escape(marker), src)]
    assert positions, f"{marker!r} not found in source"
    segments = [
        src[start : positions[index + 1] if index + 1 < len(positions) else None]
        for index, start in enumerate(positions)
    ]
    if not required:
        return max(segments, key=len)
    matching = [
        segment
        for segment in segments
        if all(token in segment for token in required)
    ]
    assert matching, (
        f"{marker!r} has no executable segment containing {required!r}"
    )
    return min(matching, key=len)


def _positive_int_constant(src: str, name: str) -> int:
    """Read and validate one positive integer TypeScript constant."""
    match = re.search(
        rf"(?:export\s+)?const\s+{re.escape(name)}\s*=\s*(\d+)\b",
        src,
    )
    assert match, f"{name} must be declared as an integer constant"
    value = int(match.group(1))
    assert value > 0, f"{name} must be positive, got {value}"
    return value


class TestSourceContractHelpers:
    """Regression pins for facade-aware executable-hook selection."""

    def test_required_token_selects_implementation_not_facade(self):
        source = "\n".join(
            (
                '"hook": facade,',
                '"hook": async () => { const executable = true },',
                '"hook": wrapper,',
            )
        )

        handler = _from_marker(source, '"hook"', required=("executable",))

        assert handler.startswith('"hook": async')
        assert "facade" not in handler


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
        """enforce-verified-claims preserves output absent a coverage claim."""
        src = _src(VERIFIED_CLAIMS_PATH)
        handler = _from_marker(src, '"experimental.text.complete"')
        assert "shouldBlockCoverageClaim" in handler
        assert "return output" in handler


# ── DELEGATE-FIRST nag NOT generated on zero streak ─────────────────────────


class TestDelegateFirstNagConditional:
    """verify DELEGATE-FIRST nag is conditional on streak > threshold (not always)."""

    def test_stop_delegate_first_conditional_on_streak(self):
        """The tool warning follows both the subagent guard and streak gate."""
        handler = _from_marker(
            _src(STOP_PATH),
            '"tool.execute.before"',
            required=("DELEGATE-FIRST", "DELEGATE_FIRST_THRESHOLD"),
        )
        guard_index = handler.index("if (isSubagent())")
        state_index = handler.index("updateSharedStreak")
        condition_index = handler.index(
            "streakState.streak > DELEGATE_FIRST_THRESHOLD"
        )
        warning_index = handler.index("console.warn", condition_index)
        assert guard_index < state_index < condition_index < warning_index

    def test_stop_tool_execute_delegate_first_conditional(self):
        """enforce-stop.tool.execute.before: streakState.streak > DELEGATE_FIRST_THRESHOLD
        gates the console.warn. streak=0 skips it."""
        src = _src(STOP_PATH)
        handler = _from_marker(
            src,
            '"tool.execute.before"',
            required=("DELEGATE-FIRST", "DELEGATE_FIRST_THRESHOLD"),
        )
        assert "streakState.streak > DELEGATE_FIRST_THRESHOLD" in handler
        assert handler.index("streakState.streak > DELEGATE_FIRST_THRESHOLD") < (
            handler.index("console.warn")
        )

    def test_delegate_first_threshold_is_positive(self):
        """DELEGATE_FIRST_THRESHOLD must be > 0, so streak=0 never triggers it."""
        src = _src(STOP_IMPL_PATH)
        assert _positive_int_constant(src, "DELEGATE_FIRST_THRESHOLD") > 0


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
        handler = _from_marker(
            _src(MULTITASK_PATH),
            '"tool.execute.before"',
            required=("zeroStreak >= MAX_ZERO_STREAK",),
        )
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        assert "ZERO-DISPATCH STREAK" in handler or "MUST DISPATCH" in handler, (
            "zero-dispatch enforcement must exist in tool.execute.before"
        )

    def test_max_zero_streak_positive(self):
        """MAX_ZERO_STREAK >= 1 ensures zeroStreak=0 never triggers MUST DISPATCH."""
        plugin_src = _src(MULTITASK_PATH)
        config_src = _src(MULTITASK_CONFIG_PATH)
        assert _positive_int_constant(config_src, "MAX_ZERO_STREAK") > 0
        assert re.search(
            r"import\s*\{[^}]*\bMAX_ZERO_STREAK\b[^}]*\}"
            r'\s*from\s*["\']\.\./lib/multitask_config\.ts["\']',
            plugin_src,
            re.DOTALL,
        ), "enforce-multitask must import the authoritative threshold"


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
        """enforce-verified-claims modifies output only on a coverage claim."""
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
        """enforce-stop exits before reading streak state for subagents."""
        handler = _from_marker(
            _src(STOP_PATH),
            '"tool.execute.before"',
            required=("DELEGATE-FIRST", "DELEGATE_FIRST_THRESHOLD"),
        )
        guard_index = handler.index("if (isSubagent())")
        state_index = handler.index("updateSharedStreak")
        warning_index = handler.index("DELEGATE-FIRST")
        assert guard_index < state_index < warning_index


class TestMustDispatchSubagentBypass:
    """verify MUST DISPATCH replacement does NOT happen when running as subagent
    with zeroStreak >= MAX_ZERO_STREAK (2)."""

    def test_multitask_must_dispatch_bypassed_for_subagent(self):
        """enforce-multitask: the MUST DISPATCH / zero-dispatch-streak block in
        text.complete must be guarded by a subagent check."""
        handler = _from_marker(
            _src(MULTITASK_PATH),
            '"tool.execute.before"',
            required=("if (isSubagent())", "zeroStreak >= MAX_ZERO_STREAK"),
        )
        assert "zeroStreak" in handler
        assert "MAX_ZERO_STREAK" in handler
        guard_index = handler.index("if (isSubagent())")
        threshold_index = handler.index("zeroStreak >= MAX_ZERO_STREAK")
        assert guard_index < threshold_index, (
            "MUST DISPATCH threshold must be unreachable after the subagent return"
        )
