"""Failing tests for bugs found in enforcement plugin source code.

Each test asserts on the CORRECT behavior, which will FAIL until the bug is fixed.
All tests are structural source-parsing only (no runtime execution).
"""

from __future__ import annotations

import re
from pathlib import Path

FLOOR_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-floor.ts"
SESSION_START_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-session-start.ts"
MULTITASK_PATH = Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-multitask.ts"


def _src(path: Path) -> str:
    return path.read_text()


# ===============================================================================
# BUG 1: enforce-multitask.ts — logically impossible floor breach condition
#
# FIXED (2026-07-18 shared.ts refactoring): The contradictory condition
# (prevMessageDispatches > 0 && zeroStreak > 0) was removed when the floor
# breach logic was restructured. The old tool.execute.before dead-code check
# was replaced by two cleanly separated phases:
#   1. handleMessageBoundary(s) properly resets zeroStreak when dispatches > 0
#   2. text.complete directly checks thisMessageDispatches against MIN_DISPATCHES
# The thin-wave block now works correctly — no contradictory condition.
#
# These tests now verify the FIX structure rather than the old broken pattern.
# ===============================================================================


class TestMultitaskDeadFloorBreach:
    """Floor breach for 1-2 dispatches was unreachable dead code — FIXED."""

    def test_zero_streak_reset_on_dispatch_is_correct(self):
        """FIX VERIFICATION: handleMessageBoundary correctly resets zeroStreak
        when thisMessageDispatches > 0 (uses local `s` var, not `_state`)."""
        src = _src(MULTITASK_PATH)

        # The zeroStreak reset path in handleMessageBoundary: when thisMessageDispatches
        # !== 0 (i.e. the message had dispatches), zeroStreak = 0
        start = src.index("function handleMessageBoundary")
        end = src.index("const defaultImpl", start)
        boundary = src[start:end]
        assert "s.prevMessageDispatches = s.thisMessageDispatches" in boundary
        assert "if (s.thisMessageDispatches === 0)" in boundary
        assert "s.zeroStreak++" in boundary
        assert "s.zeroStreak = 0" in boundary, (
            "handleMessageBoundary zeroStreak reset-on-dispatch logic not found "
            "(state var is `s` post-refactoring, not `_state`)"
        )

    def test_floor_breach_uses_session_aware_effective_floor(self):
        """FIX VERIFICATION: The floor breach for thin waves now uses a direct
        _state.thisMessageDispatches check in text.complete — NOT the old
        contradictory prevMessageDispatches + zeroStreak condition."""
        src = _src(MULTITASK_PATH)

        # The old buggy condition (prevMessageDispatches > 0 && zeroStreak > 0)
        # should NOT exist in the refactored code.
        old_buggy = re.search(
            r"prevMessageDispatches\s*>\s*0.*?zeroStreak\s*>\s*0",
            src,
            re.DOTALL,
        )
        assert old_buggy is None, (
            "BUG FIX VERIFIED: the contradictory prevMessageDispatches > 0 && "
            "zeroStreak > 0 condition has been REMOVED from the refactored code."
        )

        # The replacement: a pressure-adjusted, session-aware boundary check.
        assert "_tef = REQUIRED_DISPATCHES > 0" in src
        assert "getPressureReleaseFloor(REQUIRED_DISPATCHES)" in src
        condition = "_state.thisMessageDispatches < _tef && _state.sessionDispatchTotal > 0"
        assert condition in src
        assert "_state.thisMessageDispatches > 0 && _state.thisMessageDispatches < _tef" not in src


# ===============================================================================
# BUG 2: enforce-session-start.ts — isTaskFileRead only checks nested args.filePath
#
# FIXED (2026-07-18 shared.ts refactoring): The isTaskFileRead function now
# accepts a third `output` parameter and checks SIX input shapes in priority
# order: outputArgs?.filePath, outputArgs?.path, inputArgs?.filePath,
# inputArgs?.path, inp?.filePath, toolInput?.filePath. The top-level
# input.filePath gap is closed.
#
# These tests now verify the FIX structure rather than the old gap.
# ===============================================================================


class TestTaskFileReadInputShape:
    """isTaskFileRead now checks both nested and top-level filePath shapes — FIXED."""

    def test_is_task_file_read_checks_both_nested_and_top_path(self):
        """FIX VERIFICATION: isTaskFileRead checks multiple filePath locations
        including top-level inp?.filePath (not just nested args?.filePath)."""
        src = _src(SESSION_START_PATH)

        # Extract the isTaskFileRead function body (now has output?: unknown param)
        match = re.search(
            r"function isTaskFileRead\(tool:\s*string,\s*input:\s*unknown,\s*output\?:\s*unknown\):\s*boolean\s*\{",
            src,
            re.DOTALL,
        )
        assert match, (
            "isTaskFileRead function not found — signature may have changed. "
            "Expected: (tool: string, input: unknown, output?: unknown): boolean"
        )

        # The function now checks BOTH nested args?.filePath AND top-level inp?.filePath
        checks_inp_top_path = "inp?.filePath" in src
        checks_tool_input_path = "toolInput?.filePath" in src

        assert checks_inp_top_path, "FIX MISSING: isTaskFileRead should check top-level input.filePath (inp?.filePath)"
        assert checks_tool_input_path, (
            "FIX VERIFICATION: isTaskFileRead should also check tool_input?.filePath as a fallback shape"
        )

    def test_stringify_fallback_is_brittle(self):
        """The JSON.stringify fallback catches filePath but is overly broad —
        it matches any input containing 'tasks.md' anywhere, including error
        messages or other filePaths."""
        src = _src(SESSION_START_PATH)
        assert "JSON.stringify(input" in src, (
            "The JSON.stringify fallback exists but is brittle — it matches "
            "task file names anywhere in the input, including error messages"
        )


# ===============================================================================
# BUG 3: enforce-floor.ts — BUGS.md incident resolution detection is heading-only
#
# openWorkExists lines 77-86: the second filter (lines 83) checks if the
# heading line contains "resolved"/"fixed"/"closed" etc. But if a BUGS.md
# incident header does NOT contain the status and instead lists it on a
# sub-header line:
#   ### 2026-07-10 - Bug Title
#   Status: resolved
# The function counts it as an open (unresolved) incident — a false positive.
# ===============================================================================


class TestBugsMDOpenWorkDetection:
    """BUGS.md incident resolution should check the heading body, not just heading text."""

    def test_incident_filter_only_checks_heading_line(self):
        """Resolved/fixed markers are evaluated across each incident body."""
        src = _src(FLOOR_PATH)
        assert "function countOpenBugIncidents" in src
        assert "incidentSections" in src
        assert "current.join" in src
        assert "countOpenBugIncidents(fs.readFileSync" in src


# ===============================================================================
# BUG 4: enforce-floor.ts — openWorkExists does not treat RUNNING gate as pending
#
# openWorkExists lines 87-95: the .gate-status check only returns true for
# FAIL or incomplete lines. A gate that is still RUNNING has no FAIL/incomplete
# marker, so openWorkExists returns false — meaning a running gate is NOT
# considered pending work. This is incorrect: while the gate is running, the
# agent should still be forced to dispatch.
# ===============================================================================


class TestRunningGateIsPending:
    """A RUNNING gate should be detected as pending work."""

    def test_running_gate_not_considered_pending(self):
        """BUG: .gate-status RUNNING is not treated as pending work."""
        src = _src(FLOOR_PATH)

        # Find the gate status check block
        re.search(r"\.gate-status.*?=>.*?(?:\n\s+)", src, re.DOTALL)
        # More precise: find the gatePath check
        gate_match = re.search(
            r"const gatePath.*?\.gate-status.*?\{.*?\n\s+\}",
            src,
            re.DOTALL,
        )
        assert gate_match, "Gate status check block not found"
        block = gate_match.group(0)

        # The check only looks for FAIL or incomplete
        assert "/FAIL/" in block or "/FAIL/i" in block, "FAIL check not found in gate block"
        assert "incomplete" in block.lower(), "incomplete check not found in gate block"

        # FAIL: "RUNNING" should also be treated as pending
        has_running_check = "RUNNING" in block or "running" in block
        assert has_running_check, (
            "BUG: openWorkExists does NOT treat a RUNNING gate as pending work. "
            "A gate in progress (RUNNING status) means work is unfinished, but the "
            "function returns false for RUNNING gates. 'RUNNING' should be added to "
            "the set of detected pending-work states."
        )


# ===============================================================================
# BUG 5: enforce-floor.ts — git index mtime check triggers false positives
#
# openWorkExists lines 114-121: the check `Math.abs(idxMtime - refMtime) > 2000`
# compares git index mtime to refs/heads/master mtime. Running `git status`
# refreshes the index, changing its mtime, but the ref mtime only changes on
# commits. So after `git status` on a clean tree, the index mtime differs from
# the ref mtime, and openWorkExists falsely returns true.
# ===============================================================================


class TestGitIndexMtimeFalsePositive:
    """Git index mtime comparison produces false-positive pending work."""

    def test_index_mtime_compared_to_ref_mtime(self):
        """Pending-work detection uses porcelain status, never index mtimes."""
        src = _src(FLOOR_PATH)
        assert "idxMtime" not in src
        assert "refMtime" not in src
        assert "git status --porcelain" in src


# ===============================================================================
# BUG 6: enforce-floor.ts — typo "DISPTACH" in refill-needed console.warn
#
# Line 498 (approx): console.warn message contains "DISPTACH" instead of "DISPATCH"
# ===============================================================================


class TestDispatchTypo:
    """Console.warn message has a typo: 'DISPTACH' should be 'DISPATCH'."""

    def test_disptach_typo_in_refill_warning(self):
        """BUG: 'DISPTACH' typo in console.warn message."""
        src = _src(FLOOR_PATH)

        assert "DISPTACH" not in src, (
            "BUG: Typo 'DISPTACH' in refill-needed console.warn message. Should be 'DISPATCH'."
        )
