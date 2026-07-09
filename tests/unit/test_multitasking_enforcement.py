"""Multitasking enforcement verification (P1+P3 audit fix).

THE BUG (2026-07-09 multitasking audit): the agent grinds on the main thread with
serial read/edit/bash calls and the plugins either exempt reads from ALL counters
or have thresholds so high enforcement is inert.

THE FIX (this commit):
  P1: reads/greps/globs now increment _readStreak with time-based detection
      - warn at 5+ reads AND >30s since dispatch
      - deny at 10+ reads AND >60s since dispatch
  P3: enforce-stop.ts reads the shared /tmp/gludd-tool-streak.json and
      - injects "DELEGATE-FIRST" at streak > 8 via text.complete
      - denies non-dispatch mutations at streak > 12 via tool.execute.before

Tests 1-2 verify the P1 counter mechanics (increment on read, reset on dispatch).
Tests 3-4 verify the P1 thresholds (warn at 5, deny at 10).
Test 5 verifies P3 cross-call grinding detection in enforce-stop.ts.
Tests 6-7 verify the time gate (reads under 30s/60s allowed, over blocked).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
ENFORCE_FLOOR = PLUGIN_DIR / "enforce-floor.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"


def _floor_src() -> str:
    return ENFORCE_FLOOR.read_text()


def _stop_src() -> str:
    return ENFORCE_STOP.read_text()


# ============================================================================
# P1 TESTS — Read-streak counter mechanics
# ============================================================================


class TestP1ReadStreakIncrementsOnRead:
    """P1: When a read/grep/glob tool fires, _readStreak MUST increment.

    THE BUG: isReadTool() returned early, exempting reads from ALL counters.
    100 serial reads went undetected — the primary mechanical failure."""

    def test_read_streak_increments_on_read(self):
        src = _floor_src()
        m = re.search(
            r'if\s*\(\s*isReadTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*?)\}',
            src,
            re.DOTALL,
        )
        assert m, "isReadTool(tool) branch inside tool.execute.before not found"
        branch_body = m.group(1)
        assert re.search(r"_readStreak\s*(\+\+|\+=\s*1|\s*=\s*_readStreak\s*\+\s*1)", branch_body), (
            "isReadTool branch must INCREMENT _readStreak — the old bare return "
            "exemption let reads bypass all tracking"
        )

    def test_read_streak_resets_on_dispatch(self):
        """P1: A dispatch MUST reset _readStreak to 0."""
        src = _floor_src()
        m = re.search(
            r'if\s*\(\s*isDispatchTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*?)\}',
            src,
            re.DOTALL,
        )
        assert m, "isDispatchTool(tool) branch not found"
        dispatch_body = m.group(1)
        assert "_readStreak" in dispatch_body and (
            "_readStreak = 0" in dispatch_body or
            "_readStreak=0" in dispatch_body
        ), (
            "isDispatchTool branch must reset _readStreak = 0 — a dispatch "
            "clears the read-grind counter so the next investigation burst "
            "starts fresh"
        )

    def test_last_dispatch_ts_updated_on_dispatch(self):
        """P1: A dispatch MUST update _lastDispatchTs = Date.now() so the
        time-based detection has a fresh baseline."""
        src = _floor_src()
        m = re.search(
            r'if\s*\(\s*isDispatchTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*?)\}',
            src,
            re.DOTALL,
        )
        assert m, "isDispatchTool(tool) branch not found"
        dispatch_body = m.group(1)
        assert re.search(
            r"_lastDispatchTs\s*=\s*Date\.now\s*\(\s*\)",
            dispatch_body,
        ), (
            "isDispatchTool must update _lastDispatchTs = Date.now() — without "
            "this the time gate uses a stale timestamp"
        )


# ============================================================================
# P1 TESTS — Threshold enforcement (warn at 5, deny at 10)
# ============================================================================


class TestP1ReadGrindingWarnedAt5:
    """P1: At >5 reads AND >30s since dispatch, an ADVISORY fires."""

    def test_warn_threshold_5_reads(self):
        src = _floor_src()
        assert re.search(r"READ_GRIND_ADVISORY_COUNT.*?5|readStreak\s*>\s*5", src) or \
               re.search(r"\b5\b.*?_readStreak", src, re.DOTALL), (
            "Advisory must fire at 5 reads (NOT 10 as before the P1 fix)"
        )

    def test_warn_time_threshold_30s(self):
        src = _floor_src()
        assert ("30_000" in src or "30000" in src or
                re.search(r"30\s*\*\s*1000", src)), (
            "Advisory time threshold must be 30s/30000ms (NOT 60s as before)"
        )

    def test_warn_uses_console_warn(self):
        """The advisory must surface — NOT be silent."""
        src = _floor_src()
        assert "console.warn" in src, (
            "Advisory must use console.warn — a silent advisory is no advisory"
        )

    def test_warn_requires_both_count_and_time(self):
        """The advisory must AND both conditions: count > 5 AND time > 30s."""
        src = _floor_src()
        m = re.search(
            r"readStreak\s*>\s*5\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs",
            src,
        )
        assert m, (
            "Advisory must AND the count (>5) and time (>30s) conditions with &&"
        )


class TestP1ReadGrindingDeniedAt10:
    """P1: At >10 reads AND >60s since dispatch, reads are DENIED."""

    def test_deny_threshold_10_reads(self):
        src = _floor_src()
        assert re.search(r"READ_GRIND_DENY_COUNT.*?10|readStreak\s*>\s*10", src) or \
               re.search(r"\b10\b.*?_readStreak", src, re.DOTALL), (
            "Deny must fire at 10 reads (NOT 20 as before the P1 fix)"
        )

    def test_deny_time_threshold_60s(self):
        src = _floor_src()
        assert ("60_000" in src or "60000" in src or
                re.search(r"60\s*\*\s*1000", src)), (
            "Deny time threshold must be 60s/60000ms (NOT 120s as before)"
        )

    def test_deny_returns_permission_decision_deny(self):
        src = _floor_src()
        has_deny = (re.search(r'permissionDecision:\s*"deny"', src) or
                    re.search(r"permissionDecision:\s*'deny'", src))
        assert has_deny, (
            "Hard-deny path must return permissionDecision:'deny'"
        )

    def test_deny_requires_both_count_and_time(self):
        """The deny must AND both conditions: count > 10 AND time > 60s."""
        src = _floor_src()
        m = re.search(
            r"readStreak\s*>\s*10\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs",
            src,
        )
        assert m, (
            "Deny must AND the count (>10) and time (>60s) conditions with &&"
        )


# ============================================================================
# P3 TESTS — enforce-stop.ts cross-call grinding detection
# ============================================================================


class TestP3EnforceStopCrossCallGrinding:
    """P3: enforce-stop.ts reads the shared /tmp/gludd-tool-streak.json and
    acts on the cumulative streak (not just its own in-memory state)."""

    def test_stop_reads_shared_streak_file(self):
        """enforce-stop.ts MUST reference the shared streak file."""
        src = _stop_src()
        assert "/tmp/gludd-tool-streak.json" in src or "GLUDD_STREAK_FILE" in src, (
            "enforce-stop.ts must read /tmp/gludd-tool-streak.json — without "
            "the shared file, it has zero cross-call grinding detection (P3)."
        )

    def test_stop_has_delegate_first_threshold(self):
        """The DELEGATE-FIRST threshold (8) must be a named constant."""
        src = _stop_src()
        has_literal = "> 8" in src and "DELEGATE" in src
        has_constant = "DELEGATE_FIRST_THRESHOLD" in src
        assert has_literal or has_constant, (
            "enforce-stop.ts must define DELEGATE_FIRST_THRESHOLD (8) for the "
            "DELEGATE-FIRST directive at streak > 8"
        )

    def test_stop_has_grinding_hard_deny_threshold(self):
        """The GRINDING HARD DENY threshold (12) must be present."""
        src = _stop_src()
        has_literal = "> 12" in src
        has_constant = "GRINDING_HARD_DENY_THRESHOLD" in src
        assert has_literal or has_constant, (
            "enforce-stop.ts must define GRINDING_HARD_DENY_THRESHOLD (12) for "
            "the hard-deny at streak > 12"
        )

    def test_stop_injects_delegate_first_in_text_complete(self):
        """P3: At streak > 8, DELEGATE-FIRST must be injected via text.complete
        (a nag, not a tool-call deny — the agent needs reads to prepare the
        next dispatch wave)."""
        src = _stop_src()
        assert "DELEGATE-FIRST" in src, (
            "DELEGATE-FIRST directive must appear in source"
        )
        # The DELEGATE-FIRST injection in text.complete reads the shared streak
        # and prepends a nag to the output text. Must reference the threshold.
        assert "DELEGATE_FIRST_THRESHOLD" in src or "> 8" in src, (
            "text.complete must reference the DELEGATE_FIRST_THRESHOLD to know "
            "when to inject the nag"
        )

    def test_stop_denies_grinding_at_12_in_tool_execute(self):
        """P3: At streak > 12, non-dispatch mutations must be denied in
        tool.execute.before."""
        src = _stop_src()
        assert "MAIN-THREAD GRINDING DETECTED" in src, (
            "enforce-stop.ts must have the GRINDING DETECTED deny message for "
            "streak > 12"
        )
        # The deny at 12 must return permissionDecision:'deny'
        has_deny = re.search(r'permissionDecision:\s*"deny"', src)
        assert has_deny, (
            "The streak > 12 path must return permissionDecision:'deny' — an "
            "advisory at 12+ consecutive non-dispatch calls is insufficient"
        )

    def test_reads_never_denied_by_grinding_detection(self):
        """P3: Reads are NEVER denied by grinding detection. The agent needs
        reads to prepare dispatch waves. Denying reads would wedge the session."""
        src = _stop_src()
        # The isMutationTool check must exclude reads.
        # Pattern: const isMutationTool = !DISPATCH_TOOLS.has(tool)
        #   && !isStreakReadTool(tool) && tool !== "question"
        m = re.search(
            r"isMutationTool\s*=.*isStreakReadTool",
            src,
            re.DOTALL,
        )
        assert m, (
            "isMutationTool must exclude read tools via isStreakReadTool — "
            "denying reads would wedge the session"
        )


# ============================================================================
# TIME GATE TESTS — investigation vs grinding
# ============================================================================


class TestTimeBasedGrindingAllowsInvestigation:
    """Legitimate investigation bursts must NOT be blocked.

    An agent that just dispatched and now reads 8 files in 10s to digest results
    must pass through cleanly. The AND logic (count threshold + time since
    dispatch) is the load-bearing distinction between investigation and grinding.
    """

    def test_reads_under_30s_since_dispatch_allowed(self):
        """When time since last dispatch is <30s, reads should NOT trigger the
        advisory even after 15+ reads — it's a legitimate investigation burst."""
        src = _floor_src()
        # The advisory check must AND both conditions (>5 reads AND >30s).
        # Pattern: _readStreak > 5 && (Date.now() - _lastDispatchTs) > 30_000
        m = re.search(
            r"readStreak\s*>\s*5\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs\s*\)\s*>\s*30_000",
            src,
        )
        assert m, (
            "Advisory must AND count (>5) AND time (>30s) — reads under 30s "
            "since dispatch must be allowed (investigation, not grinding)"
        )

    def test_reads_under_60s_since_dispatch_allowed(self):
        """When time since last dispatch is <60s, reads should NOT be denied
        even after 15+ reads — investigation is fine."""
        src = _floor_src()
        m = re.search(
            r"readStreak\s*>\s*10\s*&&\s*\(?Date\.now\s*\(\s*\)\s*-\s*_lastDispatchTs\s*\)\s*>\s*60_000",
            src,
        )
        assert m, (
            "Deny must AND count (>10) AND time (>60s) — reads under 60s "
            "since dispatch must be allowed (investigation, not grinding)"
        )

    def test_deny_message_describes_grinding_not_investigation(self):
        """The deny message must make clear this is grinding, not investigation."""
        src = _floor_src()
        assert "grinding" in src.lower(), (
            "Deny message must reference 'grinding' so the agent knows this "
            "is not a legitimate investigation pattern"
        )

    def test_dispatch_resets_time_baseline(self):
        """Every dispatch must update _lastDispatchTs so the next investigation
        burst gets a fresh time baseline."""
        src = _floor_src()
        m = re.search(
            r'if\s*\(\s*isDispatchTool\s*\(\s*tool\s*\)\s*\)\s*\{([^}]*?)\}',
            src,
            re.DOTALL,
        )
        assert m, "isDispatchTool(tool) branch not found"
        dispatch_body = m.group(1)
        assert "_lastDispatchTs" in dispatch_body and "Date.now" in dispatch_body, (
            "Dispatch must set _lastDispatchTs = Date.now() to reset the "
            "time baseline for the next investigation burst"
        )


# ============================================================================
# enfore-delegate.ts — read-grind tracking (P1)
# ============================================================================


class TestEnforceDelegateReadGrinding:
    """enforce-delegate.ts must also track read-grinding with time-based
    detection at the same thresholds as enforce-floor.ts."""

    DELEGATE_PATH = PLUGIN_DIR / "enforce-delegate.ts"

    def _delegate_src(self) -> str:
        return self.DELEGATE_PATH.read_text()

    def test_delegate_advisory_threshold_5_reads(self):
        src = self._delegate_src()
        assert "READ_GRIND_ADVISORY_COUNT" in src and "5" in src, (
            "enforce-delegate.ts advisory must fire at 5 reads (not 10)"
        )

    def test_delegate_advisory_time_30s(self):
        src = self._delegate_src()
        assert "READ_GRIND_ADVISORY_MS" in src and "30000" in src, (
            "enforce-delegate.ts advisory time threshold must be 30s (not 60s)"
        )

    def test_delegate_deny_threshold_10_reads(self):
        src = self._delegate_src()
        assert "READ_GRIND_DENY_COUNT" in src and "10" in src, (
            "enforce-delegate.ts deny must fire at 10 reads (not 20)"
        )

    def test_delegate_deny_time_60s(self):
        src = self._delegate_src()
        assert "READ_GRIND_DENY_MS" in src and "60000" in src, (
            "enforce-delegate.ts deny time threshold must be 60s (not 120s)"
        )

    def test_delegate_read_streak_resets_on_dispatch(self):
        src = self._delegate_src()
        assert "saveReadGrindState(0" in src or "saveReadGrindState( 0" in src, (
            "enforce-delegate must reset read-grind counter to 0 on dispatch — "
            "otherwise the streak never clears"
        )
