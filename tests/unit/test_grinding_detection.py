"""Tests for cross-call main-thread grinding detection (P3 from multitasking audit).

THE BUG: ``enforce-stop.ts`` caught false-done phrases + commit-shaped blocks +
blocking questions, but had ZERO awareness of whether the agent was grinding on
the main thread (serial read/edit/bash with no dispatch). The streak counter
lived only in ``enforce-floor.ts`` in-memory state — ``enforce-stop.ts`` could
not see it.

THE FIX: a SHARED streak state file ``/tmp/gludd-tool-streak.json`` that both
``enforce-floor.ts`` and ``enforce-stop.ts`` read + write. Either plugin can
catch the grinding. ``enforce-stop.ts`` checks two thresholds:

  - streak >  8 → inject a "DELEGATE-FIRST" directive via the deny message
  - streak > 12 → hard deny non-dispatch mutations with
    "MAIN-THREAD GRINDING DETECTED: N consecutive non-dispatch calls."

TDD: this file was written FIRST and run RED against the missing implementation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
STOP_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
FLOOR_PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"

SHARED_STREAK_FILE = "/tmp/gludd-tool-streak.json"

# Thresholds pinned from the spec (P3 audit fix).
DELEGATE_FIRST_THRESHOLD = 8
GRINDING_HARD_DENY_THRESHOLD = 12

DISPATCH_TOOLS = {"task", "agent", "workflow"}
READ_TOOLS = {"read", "grep", "glob"}


# ============================================================================
# STRUCTURAL TESTS — pin the plugin source so a silent regression (deleted
# shared-file reference, removed threshold, missing deny message) is caught
# at gate time.
# ============================================================================


class TestSharedStreakFileReferenced:
    """Both plugins MUST reference the shared streak state file.

    Without the shared file, each plugin tracks the streak independently and
    neither can catch grinding the other misses (e.g. if floor's hook throws
    and falls through its fail-open, stop still sees the accumulated streak).
    """

    def test_stop_plugin_references_shared_file(self):
        src = STOP_PLUGIN.read_text()
        assert SHARED_STREAK_FILE in src, (
            "enforce-stop.ts must reference the shared streak file "
            f"{SHARED_STREAK_FILE} — without it the plugin has zero "
            "cross-call grinding detection (P3 audit finding)."
        )

    def test_floor_plugin_references_shared_file(self):
        src = FLOOR_PLUGIN.read_text()
        assert SHARED_STREAK_FILE in src, (
            "enforce-floor.ts must reference the shared streak file "
            f"{SHARED_STREAK_FILE} so either plugin can catch grinding."
        )

    def test_shared_file_env_override_in_stop(self):
        """The shared file path should be overridable via env var for testing."""
        src = STOP_PLUGIN.read_text()
        # The env override pattern (GLUDD_STREAK_FILE or similar) lets tests
        # isolate the streak state. Must be present so tests don't pollute /
        # read real session state.
        assert "GLUDD_STREAK_FILE" in src or "GLUDD_TOOL_STREAK_FILE" in src, (
            "enforce-stop.ts must allow overriding the streak file path via "
            "an env var (GLUDD_STREAK_FILE) for test isolation."
        )

    def test_shared_file_env_override_in_floor(self):
        src = FLOOR_PLUGIN.read_text()
        assert "GLUDD_STREAK_FILE" in src or "GLUDD_TOOL_STREAK_FILE" in src, (
            "enforce-floor.ts must allow overriding the streak file path via "
            "an env var (GLUDD_STREAK_FILE) for test isolation."
        )


class TestSharedStreakSchema:
    """The shared file MUST carry the documented fields.

    The schema is the contract between the two plugins — if a field is
    missing or renamed unilaterally, the other plugin reads stale data.
    """

    def test_stop_writes_all_fields(self):
        src = STOP_PLUGIN.read_text()
        for field in ["streak", "lastDispatchTs", "readStreak", "editStreak"]:
            assert field in src, (
                f"enforce-stop.ts must write field '{field}' to the shared "
                f"streak file — it is part of the cross-plugin schema."
            )

    def test_floor_writes_all_fields(self):
        src = FLOOR_PLUGIN.read_text()
        for field in ["streak", "lastDispatchTs", "readStreak", "editStreak"]:
            assert field in src, (
                f"enforce-floor.ts must write field '{field}' to the shared "
                f"streak file — it is part of the cross-plugin schema."
            )


class TestThresholdConstants:
    """enforce-stop.ts MUST define the two grinding thresholds.

    These are the load-bearing constants — if they drift or are deleted, the
    grinding detection either never fires or fires too aggressively.
    Accepts either a literal comparison (``> 8``) or a named constant
    (``DELEGATE_FIRST_THRESHOLD = 8``) — the constant form is preferred for
    readability but the value must still be pinned.
    """

    def test_delegate_first_threshold_present(self):
        src = STOP_PLUGIN.read_text()
        has_literal = "> 8" in src or ">= 9" in src
        has_constant = "DELEGATE_FIRST_THRESHOLD = 8" in src or "DELEGATE_FIRST_THRESHOLD=8" in src
        assert has_literal or has_constant, (
            "enforce-stop.ts must define the DELEGATE-FIRST threshold as 8 "
            "(either `> 8` literal or `DELEGATE_FIRST_THRESHOLD = 8` constant). "
            "Without it, grinding escalates to the hard-deny without the "
            "intermediate advisory."
        )

    def test_grinding_hard_deny_threshold_present(self):
        src = STOP_PLUGIN.read_text()
        has_literal = "> 12" in src or ">= 13" in src
        has_constant = "GRINDING_HARD_DENY_THRESHOLD = 12" in src or "GRINDING_HARD_DENY_THRESHOLD=12" in src
        assert has_literal or has_constant, (
            "enforce-stop.ts must define the hard-deny threshold as 12 "
            "(either `> 12` literal or `GRINDING_HARD_DENY_THRESHOLD = 12` "
            "constant). Without it, there is no structural block on prolonged "
            "grinding."
        )


class TestDenyMessages:
    """The deny messages MUST contain the spec-mandated directives.

    These exact phrases are the contract — the agent (and tests) grep for them
    to know grinding was detected.
    """

    def test_delegate_first_message_present(self):
        src = STOP_PLUGIN.read_text()
        assert "DELEGATE-FIRST" in src, (
            "enforce-stop.ts must emit a 'DELEGATE-FIRST' directive in the "
            "streak > 8 deny message (spec P3)."
        )

    def test_grinding_detected_message_present(self):
        src = STOP_PLUGIN.read_text()
        assert "MAIN-THREAD GRINDING DETECTED" in src, (
            "enforce-stop.ts must emit 'MAIN-THREAD GRINDING DETECTED' in the "
            "streak > 12 hard-deny message (spec P3)."
        )

    def test_grinding_message_mentions_consecutive_count(self):
        """The hard-deny message MUST report the streak count so the agent
        knows how deep the grinding goes."""
        src = STOP_PLUGIN.read_text()
        # The message template references the streak variable
        m = re.search(r"MAIN-THREAD GRINDING DETECTED.*?(\$\{[^}]+\}|streak)", src, re.DOTALL)
        assert m, (
            "The MAIN-THREAD GRINDING message must reference the streak count "
            "so the agent sees how many consecutive non-dispatch calls occurred."
        )


class TestFailOpen:
    """Grinding detection MUST fail open — never wedge the session."""

    def test_stop_fail_open_on_error(self):
        src = STOP_PLUGIN.read_text()
        # The grinding check must be inside a try/catch that fails open.
        assert "catch" in src.lower(), (
            "enforce-stop.ts grinding detection must fail open (try/catch) "
            "— never wedge the session on a plugin bug."
        )


class TestDispatchResetsStreak:
    """A dispatch (task/agent/workflow) MUST reset the streak to 0.

    Without the reset, the streak climbs forever and a single dispatch after
    a long read sequence still trips the threshold.
    """

    def test_reset_on_dispatch_in_stop(self):
        src = STOP_PLUGIN.read_text()
        # Look for the dispatch→reset logic referencing the shared file
        assert "task" in src and ("streak = 0" in src or "streak: 0" in src), (
            "enforce-stop.ts must reset the shared streak to 0 on a dispatch "
            "tool call (task/agent/workflow)."
        )


# ============================================================================
# BEHAVIORAL TESTS — Python port of the streak update + threshold logic.
# These model the TS updateSharedStreak() function and exercise the
# threshold transitions with realistic call sequences.
# ============================================================================


class SharedStreakSimulator:
    """Python port of the TS shared-streak update logic.

    Models the cross-plugin dedup window: if the OTHER plugin already updated
    the streak for the same tool call (within DEDUP_WINDOW_MS), this call
    does not re-increment.
    """

    DEDUP_WINDOW_MS = 500

    def __init__(self, state_path: Path | None = None):
        self.state_path = state_path
        self.state: dict = {
            "streak": 0,
            "lastDispatchTs": 0,
            "readStreak": 0,
            "editStreak": 0,
            "lastUpdateTs": 0,
            "lastWriter": "",
        }
        if state_path and state_path.exists():
            try:
                saved = json.loads(state_path.read_text())
                self.state.update(saved)
            except Exception:
                pass

    def _persist(self):
        if self.state_path:
            self.state_path.write_text(json.dumps(self.state))

    def update(self, tool: str, writer: str, now_ms: int) -> dict:
        """Mirror of the TS updateSharedStreak(tool, writer, now)."""
        is_dispatch = tool in DISPATCH_TOOLS
        is_read = tool in READ_TOOLS
        already_counted = (
            (now_ms - self.state["lastUpdateTs"]) < self.DEDUP_WINDOW_MS
            and self.state["lastWriter"] != writer
            and self.state["lastWriter"] != ""
        )
        if is_dispatch:
            self.state["streak"] = 0
            self.state["readStreak"] = 0
            self.state["editStreak"] = 0
            self.state["lastDispatchTs"] = now_ms
        elif not already_counted:
            self.state["streak"] += 1
            if is_read:
                self.state["readStreak"] += 1
            else:
                self.state["editStreak"] += 1
        self.state["lastUpdateTs"] = now_ms
        self.state["lastWriter"] = writer
        self._persist()
        return dict(self.state)

    def deny_decision(self, tool: str) -> tuple[str | None, str | None]:
        """Mirror of the TS threshold check in enforce-stop.ts.

        Returns (decision, message) where decision is 'deny' or None (allow).
        Reads are NEVER denied (they don't mutate state); only mutations are
        blocked so the session doesn't wedge.
        """
        if tool in DISPATCH_TOOLS:
            return (None, None)
        if tool in READ_TOOLS:
            return (None, None)
        streak = self.state["streak"]
        if streak > GRINDING_HARD_DENY_THRESHOLD:
            return (
                "deny",
                f"MAIN-THREAD GRINDING DETECTED: {streak} consecutive "
                "non-dispatch calls. DISPATCH WORK or justify.",
            )
        if streak > DELEGATE_FIRST_THRESHOLD:
            return (
                "deny",
                f"DELEGATE-FIRST: {streak} consecutive non-dispatch calls. "
                "DISPATCH WORK via task/agent/workflow before continuing.",
            )
        return (None, None)


class TestStreakIncrement:
    """Consecutive non-dispatch calls increment the streak."""

    def test_single_edit_increments_to_1(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        s = sim.update("edit", "enforce-stop", now_ms=1000)
        assert s["streak"] == 1

    def test_multiple_edits_climb(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(5):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 5

    def test_reads_increment_read_streak(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("read", "enforce-stop", now_ms=1000)
        sim.update("read", "enforce-stop", now_ms=2000)
        assert sim.state["streak"] == 2
        assert sim.state["readStreak"] == 2
        assert sim.state["editStreak"] == 0

    def test_bash_increments_edit_streak(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("bash", "enforce-stop", now_ms=1000)
        assert sim.state["streak"] == 1
        assert sim.state["editStreak"] == 1


class TestDispatchReset:
    """A dispatch resets the streak to 0."""

    def test_task_resets(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("edit", "enforce-stop", now_ms=1000)
        sim.update("edit", "enforce-stop", now_ms=2000)
        assert sim.state["streak"] == 2
        sim.update("task", "enforce-stop", now_ms=3000)
        assert sim.state["streak"] == 0
        assert sim.state["readStreak"] == 0
        assert sim.state["editStreak"] == 0

    def test_agent_resets(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(10):
            sim.update("read", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 10
        sim.update("agent", "enforce-stop", now_ms=20000)
        assert sim.state["streak"] == 0

    def test_workflow_resets(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("bash", "enforce-stop", now_ms=1000)
        sim.update("workflow", "enforce-stop", now_ms=2000)
        assert sim.state["streak"] == 0

    def test_streak_climbs_again_after_reset(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("edit", "enforce-stop", now_ms=1000)
        sim.update("task", "enforce-stop", now_ms=2000)
        sim.update("edit", "enforce-stop", now_ms=3000)
        assert sim.state["streak"] == 1


class TestCrossPluginDedup:
    """Both plugins updating within the dedup window does NOT double-count.

    This is the critical correctness property: floor.ts and stop.ts both fire
    on the same tool.execute.before event. Without dedup, every call would
    increment the streak twice, tripping thresholds at half the real count.
    """

    def test_both_plugins_same_call_no_double_count(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        # Floor fires first for an edit at t=1000
        sim.update("edit", "enforce-floor", now_ms=1000)
        assert sim.state["streak"] == 1
        # Stop fires 2ms later for the SAME call → deduped
        sim.update("edit", "enforce-stop", now_ms=1002)
        assert sim.state["streak"] == 1  # NOT 2

    def test_both_plugins_separate_calls_increment(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        # Call 1: floor then stop (deduped)
        sim.update("edit", "enforce-floor", now_ms=1000)
        sim.update("edit", "enforce-stop", now_ms=1002)
        # Call 2: floor then stop (>500ms later → not deduped)
        sim.update("edit", "enforce-floor", now_ms=2000)
        assert sim.state["streak"] == 2  # Two real calls

    def test_dispatch_idempotent_across_plugins(self, tmp_path):
        """Resetting on dispatch twice (once per plugin) is idempotent."""
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        sim.update("edit", "enforce-floor", now_ms=1000)
        sim.update("edit", "enforce-stop", now_ms=1002)
        assert sim.state["streak"] == 1
        # Both plugins see the dispatch — both reset (idempotent)
        sim.update("task", "enforce-floor", now_ms=2000)
        sim.update("task", "enforce-stop", now_ms=2002)
        assert sim.state["streak"] == 0


class TestThresholdEnforcement:
    """The deny decision transitions at the two thresholds."""

    def test_below_delegate_threshold_allows(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(8):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 8
        decision, _msg = sim.deny_decision("edit")
        assert decision is None, "streak=8 is NOT > 8 → allow"

    def test_above_delegate_threshold_denies_with_directive(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(9):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 9
        decision, msg = sim.deny_decision("edit")
        assert decision == "deny"
        assert "DELEGATE-FIRST" in msg

    def test_above_hard_deny_threshold_denies_with_grinding(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(13):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 13
        decision, msg = sim.deny_decision("edit")
        assert decision == "deny"
        assert "MAIN-THREAD GRINDING DETECTED" in msg

    def test_grinding_message_includes_count(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(15):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        _decision, msg = sim.deny_decision("edit")
        assert "15" in msg, "Message must include the streak count"

    def test_bash_denied_at_grinding_threshold(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(13):
            sim.update("bash", "enforce-stop", now_ms=1000 + i * 1000)
        decision, _msg = sim.deny_decision("bash")
        assert decision == "deny"

    def test_write_denied_at_delegate_threshold(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(9):
            sim.update("write", "enforce-stop", now_ms=1000 + i * 1000)
        decision, msg = sim.deny_decision("write")
        assert decision == "deny"
        assert "DELEGATE-FIRST" in msg


class TestReadsNotDenied:
    """Reads contribute to the streak but are NEVER denied.

    Denying reads would wedge the session — the agent needs to read files to
    prepare the next dispatch wave. The streak still counts reads so the
    threshold accurately reflects grinding.
    """

    def test_read_allowed_even_at_grinding_threshold(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(20):
            sim.update("read", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 20
        decision, _msg = sim.deny_decision("read")
        assert decision is None, (
            "Reads must NEVER be denied by grinding detection — denying them "
            "would wedge the session (the agent can't read to prepare a dispatch)."
        )

    def test_grep_allowed_at_grinding_threshold(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(15):
            sim.update("grep", "enforce-stop", now_ms=1000 + i * 1000)
        decision, _msg = sim.deny_decision("grep")
        assert decision is None

    def test_dispatch_always_allowed(self, tmp_path):
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(20):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        decision, _msg = sim.deny_decision("task")
        assert decision is None, "Dispatch must ALWAYS be allowed."


class TestRealisticGrindingSequence:
    """End-to-end: a realistic grinding sequence triggers escalating denys."""

    def test_eight_reads_then_edit_allowed(self, tmp_path):
        """8 reads + 1 edit = streak 9 at the edit → DELEGATE-FIRST."""
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(8):
            sim.update("read", "enforce-stop", now_ms=1000 + i * 1000)
        # Reads all allowed
        for _i in range(8):
            d, _ = sim.deny_decision("read")
            assert d is None
        # 9th call is an edit → streak becomes 9 > 8 → deny
        sim.update("edit", "enforce-stop", now_ms=9000)
        decision, msg = sim.deny_decision("edit")
        assert decision == "deny"
        assert "DELEGATE-FIRST" in msg

    def test_dispatch_breaks_the_grind(self, tmp_path):
        """A dispatch in the middle resets the streak — no false deny."""
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(7):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        assert sim.state["streak"] == 7
        # Agent dispatches (correct behavior) → streak resets
        sim.update("task", "enforce-stop", now_ms=8000)
        assert sim.state["streak"] == 0
        # Next edit is streak 1 → allowed
        sim.update("edit", "enforce-stop", now_ms=9000)
        decision, _msg = sim.deny_decision("edit")
        assert decision is None

    def test_escalation_delegate_then_grinding(self, tmp_path):
        """streak 9 → DELEGATE-FIRST, then climbs to 13 → GRINDING."""
        sim = SharedStreakSimulator(tmp_path / "streak.json")
        for i in range(9):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        d1, m1 = sim.deny_decision("edit")
        assert d1 == "deny" and "DELEGATE-FIRST" in m1
        # Agent ignores the directive and keeps grinding
        for i in range(9, 13):
            sim.update("edit", "enforce-stop", now_ms=1000 + i * 1000)
        d2, m2 = sim.deny_decision("edit")
        assert d2 == "deny" and "MAIN-THREAD GRINDING DETECTED" in m2
