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
# Lines 152-167: the check `prevMessageDispatches > 0 && zeroStreak > 0` can
# NEVER be true. The zeroStreak counter is reset to 0 whenever the previous
# message had >0 dispatches (line 128-129). So when prevMessageDispatches > 0,
# zeroStreak is always 0 — the floor breach for 1-2 dispatches per wave is
# dead code that silently allows thin waves.
# ===============================================================================


class TestMultitaskDeadFloorBreach:
    """Floor breach for 1-2 dispatches is unreachable dead code."""

    def test_zero_streak_reset_when_prev_has_dispatches(self):
        """BUG: zeroStreak is reset when prevMessageDispatches > 0, making
        the floor breach check (which requires zeroStreak > 0) unreachable."""
        src = _src(MULTITASK_PATH)

        # The zeroStreak reset path: when thisMessageDispatches !== 0, zeroStreak = 0
        reset_pattern = re.compile(
            r"_state\.thisMessageDispatches\s*===\s*0.*?\{.*?_state\.zeroStreak\+\+.*?\}\s*else\s*\{.*?_state\.zeroStreak\s*=\s*0.*?\}",
            re.DOTALL,
        )
        assert reset_pattern.search(src), "zeroStreak reset-on-dispatch logic not found"

        # The floor breach check requires BOTH prevMessageDispatches > 0 AND zeroStreak > 0
        floor_breach_pattern = re.compile(
            r"_state\.prevMessageDispatches\s*>\s*0\s*&&\s*_state\.prevMessageDispatches\s*<\s*MIN_DISPATCHES\s*&&\s*_state\.zeroStreak\s*>\s*0",
            re.DOTALL,
        )
        assert floor_breach_pattern.search(src), (
            "Floor breach check with contradictory condition not found"
        )

    def test_floor_breach_condition_is_contradictory(self):
        """CORRECT behavior: The floor breach check should NOT require
        zeroStreak > 0 when prevMessageDispatches > 0, because zeroStreak
        is always 0 in that case. A fix would either remove the zeroStreak
        condition or restructure the streak logic."""
        src = _src(MULTITASK_PATH)

        # The bug: zeroStreak > 0 is always false when prevMessageDispatches > 0
        # because the boundary detection code (lines 125-129) sets zeroStreak = 0
        # whenever the previous message had any dispatches (thisMessageDispatches > 0).

        # Extract the exact conditions
        match = re.search(
            r"if\s*\(\s*_state\.prevMessageDispatches\s*>\s*0\s*&&\s*_state\.prevMessageDispatches\s*<\s*MIN_DISPATCHES\s*&&\s*_state\.zeroStreak\s*>\s*0\s*\)",
            src,
            re.DOTALL,
        )
        assert match, "Floor breach block not found"

        condition = match.group(0)

        # The fix should separate these conditions — zeroStreak should not gate
        # the floor breach check, or the zeroStreak logic should be restructured.
        # For now, assert the bug exists (the contradictory condition is present).
        assert "prevMessageDispatches > 0" in condition
        assert "zeroStreak > 0" in condition
        # FAIL: the correct fix removes "zeroStreak > 0" from this condition
        assert "zeroStreak > 0" not in condition, (
            "BUG: zeroStreak > 0 should NOT be a condition for the floor breach "
            "check because zeroStreak is always 0 when prevMessageDispatches > 0. "
            "This is dead code."
        )


# ===============================================================================
# BUG 2: enforce-session-start.ts — isTaskFileRead only checks nested args.filePath
#
# The isTaskFileRead function (lines 229-241) checks input.args?.filePath but
# not input.filePath. The isReadOnlyMakeTarget function (lines 219-226) correctly
# checks BOTH input.args?.command AND input.command. This inconsistency means
# task file reads could be missed if the opencode framework passes filePath at
# the top level of the input object.
# ===============================================================================


class TestTaskFileReadInputShape:
    """isTaskFileRead should check both input.args.filePath AND input.filePath."""

    def test_is_task_file_read_only_checks_nested_path(self):
        """BUG: isTaskFileRead only accesses args?.filePath, missing top-level filePath."""
        src = _src(SESSION_START_PATH)

        # Extract the isTaskFileRead function body
        match = re.search(
            r"function isTaskFileRead\(tool: string, input: unknown\): boolean \{(.+?)\n\}",
            src,
            re.DOTALL,
        )
        assert match, "isTaskFileRead function not found"
        body = match.group(1)

        # It should check BOTH args?.filePath AND input?.filePath
        checks_args_path = "args?.filePath" in body or "args.filePath" in body
        checks_top_path = "inp?.filePath" in body or "input?.filePath" in body

        assert checks_args_path, "isTaskFileRead should check args?.filePath"
        # FAIL: currently only checks nested, not top-level
        assert checks_top_path, (
            "BUG: isTaskFileRead does NOT check filePath at the top level of input. "
            "If opencode passes filePath as a top-level property (not nested under args), "
            "task-file reads are missed. The isReadOnlyMakeTarget function correctly "
            "checks BOTH input.args?.command AND input?.command."
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
        """BUG: The resolved/fixed filter is applied to the heading line only."""
        src = _src(FLOOR_PATH)

        # Find the BUGS.md detection block
        match = re.search(
            r"const bugsMd.*?const openIncidents.*?\.filter\(.*?\)",
            src,
            re.DOTALL,
        )
        assert match, "BUGS.md openWorkExists detection not found"
        block = match.group(0)

        # The filters run on individual lines from the heading regex match
        # The heading regex matches line-by-line (split then filter)
        header_filter = re.search(
            r"\.filter\(\s*l\s*=>\s*/\^###\\s\+\\d\{4\}-\d\{2\}-\d\{2\}\\s\+\[-—\].*?\)",
            block,
            re.DOTALL,
        )
        assert header_filter, "Heading filter not found"

        # But the second filter only checks the SAME line (l) for resolved/fixed
        # This misses sub-header status markers
        second_filter = re.search(
            r"\.filter\(\s*l\s*=>\s*!/\\b\(resolved\|fixed\|closed\|wontfix\|duplicate\)\\b/i",
            block,
            re.DOTALL,
        )
        assert second_filter, "Status filter not found"

        # FAIL: The correct behavior is to check the incident body (the lines
        # between this heading and the next heading) for resolution markers,
        # not just the heading line itself.
        raise AssertionError(
            "BUG: BUGS.md incident resolution detection only checks heading text. "
            "If a resolved incident lists its status on a sub-line instead of the "
            "heading, it is falsely counted as open. The filter should scan the "
            "incident body (between headings) for resolution markers."
        )


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
        re.search(
            r"\.gate-status.*?=>.*?(?:\n\s+)", src, re.DOTALL
        )
        # More precise: find the gatePath check
        gate_match = re.search(
            r'const gatePath.*?\.gate-status.*?\{.*?\n\s+\}',
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
        """BUG: index mtime drifts from ref mtime after git status refresh."""
        src = _src(FLOOR_PATH)

        match = re.search(
            r"const idxMtime\s*=\s*fs\.statSync\(index\)\.mtimeMs\s*\n"
            r"\s*const refMtime\s*=\s*fs\.statSync\(headRef\)\.mtimeMs",
            src,
            re.DOTALL,
        )
        assert match, "Index/ref mtime comparison not found"

        # The comparison with threshold 2000ms is there
        threshold_match = re.search(
            r"Math\.abs\(idxMtime\s*-\s*refMtime\)\s*>\s*2000",
            src,
        )
        assert threshold_match, "Mtime threshold (2000) not found"

        # FAIL: mtime-based comparison is unreliable; git status refreshes index
        raise AssertionError(
            "BUG: git index mtime comparison in openWorkExists produces false "
            "positives. Running 'git status' refreshes the index, changing its "
            "mtime, while refs/heads/master mtime only changes on commits. After "
            "'git status' on a clean tree, the mtime difference exceeds 2000ms, "
            "and openWorkExists falsely reports pending work. The index/ref check "
            "should be replaced with git status --porcelain (already present in "
            "the next try-catch block) or removed."
        )


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

        assert "DISPTACH" in src, "Typo 'DISPTACH' not found in source"

        # FAIL: should use "DISPATCH" not "DISPTACH"
        assert "DISPTACH" not in src, (
            "BUG: Typo 'DISPTACH' in refill-needed console.warn message. "
            "Should be 'DISPATCH'."
        )
