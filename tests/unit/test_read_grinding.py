"""Behavior pin for the read-grinding enforcement in enforce-floor.ts and
enforce-delegate.ts.

THE BUG (multitasking audit P1): read/grep/glob tools were EXEMPT from ALL
streak counters. An agent could do 100 serial reads with zero dispatches and
no plugin would catch it — the primary mechanical failure of the floor guard.

THE FIX: a SEPARATE read-streak counter with time-based detection. Reads
don't count toward the edit/write/bash streak (they're legitimate during
investigation), but they DO count toward a read-streak that:
  - emits an ADVISORY after 10 reads + 60s since last dispatch
  - HARD-DENIES after 20 reads + 120s since last dispatch
  - resets to 0 on any dispatch

These tests are STRUCTURAL (we cannot execute TypeScript from Python). They
read the plugin source as text and assert the load-bearing pieces exist.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
ENFORCE_FLOOR = PLUGIN_DIR / "enforce-floor.ts"
ENFORCE_DELEGATE = PLUGIN_DIR / "enforce-delegate.ts"


def _floor_src() -> str:
    return ENFORCE_FLOOR.read_text()


def _delegate_src() -> str:
    return ENFORCE_DELEGATE.read_text()


# --------------------------------------------------------------------------- #
# enforce-floor.ts — read-streak counter
# --------------------------------------------------------------------------- #
class TestEnforceFloorReadStreakCounter:
    """enforce-floor.ts must carry a SEPARATE read-streak counter distinct from
    the edit/write/bash streak. The old `isReadTool(...) return` exemption
    closed the hole where 100 serial reads went undetected."""

    def test_read_streak_counter_variable_exists(self):
        src = _floor_src()
        assert re.search(r"\b_readStreak\b", src), (
            "enforce-floor.ts must declare a _readStreak counter. The old "
            "isReadTool() early-return exempted reads from ALL tracking — "
            "the primary mechanical failure of the floor guard."
        )

    def test_last_dispatch_timestamp_tracked(self):
        src = _floor_src()
        assert re.search(r"\b_lastDispatchTs\b", src), (
            "enforce-floor.ts must track _lastDispatchTs (ms timestamp of the "
            "last task/agent/workflow dispatch) so time-based detection can "
            "distinguish a legitimate investigation burst from a read-grinding "
            "spree."
        )

    def test_read_streak_increments_on_read_tool(self):
        """When a read/grep/glob tool fires, _readStreak MUST increment — not
        return early as the old isReadTool() exemption did."""
        src = _floor_src()
        # The isReadTool branch must NOT be a bare `return`. It must increment
        # _readStreak before any return.
        # Find the isReadTool branch inside tool.execute.before.
        m = re.search(
            r'if\s*\(\s*isReadTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*)\}',
            src,
            re.DOTALL,
        )
        assert m, (
            "isReadTool(tool) branch inside tool.execute.before not found — "
            "the read-tool path must exist and increment _readStreak"
        )
        branch_body = m.group(1)
        assert "_readStreak" in branch_body, (
            "isReadTool branch must reference _readStreak — the old bare "
            "`return` exemption let reads bypass all tracking"
        )
        assert re.search(r"_readStreak\s*(\+\+|\+=\s*1|\s*=\s*_readStreak\s*\+\s*1)", branch_body), (
            "isReadTool branch must INCREMENT _readStreak (++, += 1, or = _readStreak + 1) — "
            "not just reference it"
        )

    def test_read_streak_resets_on_dispatch(self):
        """A task/agent/workflow dispatch MUST reset _readStreak to 0 — same
        as the edit/write/bash streak. Otherwise an agent that dispatched
        once and then ground through 20 reads over 2 minutes is wrongly
        blocked when it next legitimately dispatches."""
        src = _floor_src()
        # Find the isDispatchTool branch.
        m = re.search(
            r'if\s*\(\s*isDispatchTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*?)\}',
            src,
            re.DOTALL,
        )
        assert m, "isDispatchTool(tool) branch not found"
        dispatch_body = m.group(1)
        assert "_readStreak" in dispatch_body, (
            "isDispatchTool branch must reset _readStreak — a dispatch clears "
            "the read-grind counter so the next investigation burst starts fresh"
        )
        # Must also update _lastDispatchTs on dispatch.
        assert "_lastDispatchTs" in dispatch_body, (
            "isDispatchTool branch must update _lastDispatchTs = Date.now() — "
            "the time-based detection key"
        )


class TestEnforceFloorReadGrindingAdvisoryThreshold:
    """At >10 reads AND >60s since last dispatch, an ADVISORY must fire."""

    def test_advisory_threshold_constants_exist(self):
        src = _floor_src()
        # The advisory threshold is 10 reads; the time threshold is 60s.
        # Accept named constants OR inline literals (60000 ms OR 60 seconds).
        assert re.search(r"READ_GRIND_ADVISORY_COUNT|readStreak\s*>\s*10", src) or \
               re.search(r"READ_GRIND.*?COUNT.*?=\s*10", src, re.DOTALL) or \
               re.search(r"\b10\b.*?read", src.lower()), (
            "Advisory read-count threshold (10) not found — the advisory must "
            "fire after 10+ reads"
        )

    def test_advisory_time_threshold_60s(self):
        src = _floor_src()
        # 60s = 60000 ms. Accept either form OR a named constant referencing 60.
        assert ("60_000" in src or "60000" in src or
                re.search(r"READ_GRIND_ADVISORY_MS.*?=\s*60", src, re.DOTALL) or
                re.search(r"60\s*\*\s*1000", src)), (
            "Advisory time threshold (60s / 60000ms) not found — the advisory "
            "must only fire after >60s since the last dispatch"
        )

    def test_advisory_message_text_present(self):
        src = _floor_src()
        # The advisory message must contain READ-GRINDING (case-insensitive)
        # and direct the agent to DISPATCH.
        assert re.search(r"READ[- ]GRIND", src, re.IGNORECASE), (
            "Advisory must contain 'READ-GRINDING DETECTED' or similar so the "
            "agent recognizes the failure mode"
        )
        assert "DISPATCH" in src.upper(), (
            "Advisory must direct the agent to DISPATCH WORK"
        )

    def test_advisory_uses_console_warn_or_return_message(self):
        """The advisory must surface via console.warn OR a returned message —
        NOT be silent. A silent advisory is no advisory."""
        src = _floor_src()
        # The advisory branch must reference console.warn OR return a message
        # object (not just `return` with no side effect).
        # Find any block that mentions READ-GRIND.
        m = re.search(r"READ[- ]GRIND.*?(?=return\s*\{|^\s*\}\s*$|$)", src, re.IGNORECASE | re.DOTALL)
        assert m, "READ-GRIND advisory block not found"
        block = m.group(0)
        assert "console.warn" in block or "console.error" in block or \
               re.search(r'return\s*\{', block), (
            "Advisory must use console.warn OR return a message object — a "
            "silent `return` is no advisory"
        )


class TestEnforceFloorReadGrindingDenyThreshold:
    """At >20 reads AND >120s since last dispatch, the read MUST be DENIED."""

    def test_deny_threshold_20_reads(self):
        src = _floor_src()
        assert re.search(r"READ_GRIND_DENY_COUNT|readStreak\s*>\s*20", src) or \
               re.search(r"READ_GRIND.*?DENY.*?COUNT.*?=\s*20", src, re.DOTALL) or \
               re.search(r"\b20\b.*?read", src.lower()), (
            "Deny read-count threshold (20) not found — the hard deny must "
            "fire after 20+ reads"
        )

    def test_deny_time_threshold_120s(self):
        src = _floor_src()
        assert ("120_000" in src or "120000" in src or
                re.search(r"READ_GRIND_DENY_MS.*?=\s*120", src, re.DOTALL) or
                re.search(r"120\s*\*\s*1000", src)), (
            "Deny time threshold (120s / 120000ms) not found — the hard deny "
            "must only fire after >120s since the last dispatch"
        )

    def test_deny_returns_permission_decision_deny(self):
        """At the deny threshold, the read tool MUST return
        permissionDecision:'deny' — not just an advisory."""
        src = _floor_src()
        has_deny = re.search(r'permissionDecision:\s*"deny"', src) or \
                   re.search(r"permissionDecision:\s*'deny'", src)
        assert has_deny, (
            "Hard-deny path must return permissionDecision:'deny' — an advisory "
            "at the 20-read threshold is insufficient"
        )


class TestEnforceFloorReadGrindingTimeGate:
    """Reads under the time threshold MUST be allowed (investigation is OK).

    An agent that just dispatched and is now reading 5 files to digest results
    must NOT be blocked — the time gate distinguishes investigation from
    grinding."""

    def test_time_gate_uses_last_dispatch_ts(self):
        """The time check must compute (now - _lastDispatchTs), not use a
        static counter alone."""
        src = _floor_src()
        assert re.search(r"Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs", src) or \
               re.search(r"now\s*-\s*_lastDispatchTs", src), (
            "Time gate must compute (Date.now() - _lastDispatchTs) — without "
            "this the plugin cannot distinguish a fresh investigation burst "
            "from a grinding spree"
        )

    def test_advisory_requires_both_count_and_time(self):
        """The advisory must AND the count and time conditions — NOT OR. A
        short investigation burst (10 reads in 10s) must NOT fire."""
        src = _floor_src()
        # Find the advisory threshold check. It must combine both conditions
        # with && (AND), not || (OR).
        # Look for a pattern like: readStreak > 10 && (now - _lastDispatchTs) > 60000
        m = re.search(
            r"readStreak\s*>\s*10\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs",
            src,
        )
        assert m, (
            "Advisory must AND the count (>10) and time (>60s) conditions with "
            "&& — OR would fire on a legitimate fast investigation burst"
        )

    def test_deny_requires_both_count_and_time(self):
        """The deny must AND the count and time conditions — NOT OR."""
        src = _floor_src()
        m = re.search(
            r"readStreak\s*>\s*20\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs",
            src,
        )
        assert m, (
            "Deny must AND the count (>20) and time (>120s) conditions with "
            "&& — OR would deny a legitimate fast investigation burst"
        )


# --------------------------------------------------------------------------- #
# enforce-delegate.ts — read-streak tracking
# --------------------------------------------------------------------------- #
class TestEnforceDelegateReadStreak:
    """enforce-delegate.ts must also track read-streak — but separately from
    the edit/write/bash mainthread streak. Reads don't count toward the
    edit streak, but they DO count toward a read-streak that triggers at
    a higher threshold."""

    def test_read_streak_counter_exists_in_delegate(self):
        src = _delegate_src()
        # enforce-delegate uses a JSON state file for the mainthread streak.
        # The read streak may use the same pattern OR an in-memory variable.
        # Accept either, but it MUST be distinct from the edit streak.
        assert re.search(r"readStreak|read_streak|_readStreak", src), (
            "enforce-delegate.ts must track a read-streak counter distinct "
            "from the edit/write/bash streak — reads must not be invisible "
            "to the delegation discipline"
        )

    def test_reads_do_not_count_toward_edit_streak(self):
        """isMainthreadTool() must NOT include read/grep/glob — reads are
        gated by the SEPARATE read-streak, not the edit streak."""
        src = _delegate_src()
        # Note: the function has a return type annotation (`: boolean`) between
        # `)` and `{`, so we skip any non-`{` chars before the opening brace.
        m = re.search(r"function isMainthreadTool\([^)]*\)[^{]*\{([^}]*)\}", src, re.DOTALL)
        assert m, "isMainthreadTool function not found"
        body = m.group(1)
        # The list must include edit/write/bash...
        assert "edit" in body and "write" in body and "bash" in body, (
            "isMainthreadTool must include edit/write/bash"
        )
        # ...and must NOT include read/grep/glob (those are gated separately).
        assert "read" not in body.lower(), (
            "isMainthreadTool must NOT include 'read' — reads are gated by "
            "the separate read-streak counter, not the edit streak"
        )

    def test_read_streak_resets_on_dispatch_in_delegate(self):
        """A task/agent/workflow dispatch MUST reset the read-streak in
        enforce-delegate too — otherwise the streak never clears and the
        agent is permanently blocked."""
        src = _delegate_src()
        # The dispatch branch must call the reset function. We search the
        # full source (not just the function body — nested braces break the
        # body-extraction regex) for saveReadGrindState(0, ...).
        assert re.search(r"saveReadGrindState\s*\(\s*0\s*,", src), (
            "mainthreadBudgetAfter must reset the read-grind counter on "
            "dispatch (saveReadGrindState(0, Date.now())) — otherwise the "
            "streak never clears"
        )

    def test_read_streak_advisory_or_block_in_delegate(self):
        """enforce-delegate must surface an advisory OR block at the read-grind
        threshold. A silent read-streak that never fires is dead code."""
        src = _delegate_src()
        # The read-streak must be CONSULTED somewhere (not just declared and
        # reset). Look for a comparison or a throw/return based on it.
        # Accept: a comparison against a threshold, OR a throw, OR a return.
        m = re.search(r"readStreak|read_streak", src)
        assert m, "read-streak reference not found"
        # Find all read-streak usages and confirm at least one is a comparison
        # or block (not just declaration/reset).
        usages = re.findall(r"readStreak[\w]*|read_streak[\w]*", src)
        assert len(usages) >= 2, (
            "read-streak must be referenced in at least 2 places (declaration "
            "+ a comparison/block) — a single declaration is dead code"
        )

    def test_delegate_has_time_based_detection(self):
        """enforce-delegate must use time-based detection for read-grinding,
        not just a count — same pattern as enforce-floor."""
        src = _delegate_src()
        # Must track lastDispatchTs OR equivalent time-based state.
        assert re.search(r"lastDispatch|last_dispatch|DispatchTs", src), (
            "enforce-delegate must track lastDispatchTs (or equivalent) for "
            "time-based read-grinding detection — count alone would block "
            "legitimate investigation bursts"
        )


# --------------------------------------------------------------------------- #
# Edit streak independence — the fix must NOT break existing edit-streak behavior
# --------------------------------------------------------------------------- #
class TestEditStreakStillWorksIndependently:
    """The read-streak counter must be SEPARATE from the edit/write/bash
    streak. Adding read tracking must NOT change the edit-streak behavior."""

    def test_floor_edit_streak_counter_unchanged(self):
        """The _streakCount counter (edit/write/bash) must still exist and
        be incremented on non-read, non-dispatch tools."""
        src = _floor_src()
        assert re.search(r"\b_streakCount\b", src), (
            "_streakCount (edit/write/bash streak) must still exist in "
            "enforce-floor.ts — the read-streak is ADDITIVE, not a replacement"
        )
        assert re.search(r"_streakCount\s*(\+\+|\+=\s*1)", src), (
            "_streakCount must still be incremented — the read-streak fix "
            "must not break existing edit-streak tracking"
        )

    def test_delegate_mainthread_streak_unchanged(self):
        """The mainthread streak (MAINTHREAD_THRESHOLD, readStreak in
        delegate) must still exist and gate edit/write/bash at threshold 4."""
        src = _delegate_src()
        assert "MAINTHREAD_THRESHOLD" in src, (
            "MAINTHREAD_THRESHOLD must still exist in enforce-delegate.ts"
        )
        m = re.search(
            r'MAINTHREAD_THRESHOLD\s*=\s*parseInt\s*\(\s*process\.env\.GLUDD_MAINTHREAD_THRESHOLD\s*\|\|\s*["\'](\d+)["\']',
            src,
        )
        assert m and m.group(1) == "4", (
            "MAINTHREAD_THRESHOLD must still default to 4 — the read-streak "
            "fix must not change the edit-streak threshold"
        )


# --------------------------------------------------------------------------- #
# Fail-open: the read-streak logic must NEVER wedge the session
# --------------------------------------------------------------------------- #
class TestReadGrindingFailOpen:
    """The read-streak enforcement must fail-open on any internal error."""

    def test_floor_read_streak_wrapped_in_try_catch(self):
        """tool.execute.before must have a top-level try/catch so a read-streak
        bug never wedges the session."""
        src = _floor_src()
        assert "catch" in src.lower(), (
            "enforce-floor.ts tool.execute.before must have try/catch — a "
            "read-streak bug must fail-open, not wedge the session"
        )

    def test_delegate_read_streak_fail_open(self):
        """enforce-delegate must fail-open on read-streak errors too."""
        src = _delegate_src()
        # mainthreadBudgetBefore and mainthreadBudgetAfter both have try/catch.
        # Note: the function has a return type annotation before `{`.
        m = re.search(r"function mainthreadBudgetBefore\([^)]*\)[^{]*\{([^}]*)\}", src, re.DOTALL)
        assert m, "mainthreadBudgetBefore function not found"
        body = m.group(1)
        assert "catch" in body or "return null" in body, (
            "mainthreadBudgetBefore must fail-open (try/catch or return null) "
            "so a read-streak bug never wedges the session"
        )
