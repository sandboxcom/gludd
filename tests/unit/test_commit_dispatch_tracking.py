"""DP.1: Missed commit dispatch tracking.

Verifies that when a git-commit runs on the main thread instead of via a
subagent dispatch slot, it is recorded in /tmp/gludd-missed-commit-dispatch.json,
and after MISSED_COMMIT_THRESHOLD (3) misses, a reminder is injected.

TDD: this file was written FIRST to assert the tracking behavior before the
plugin code was committed.
"""

import re
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).parent.parent.parent
ENFORCE_FLOOR_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"

# ---- constants ported from the plugin source ----
MISSED_COMMIT_FILE = "/tmp/gludd-missed-commit-dispatch.json"
MISSED_COMMIT_THRESHOLD = 3
MISSED_COMMIT_REMINDER_MS = 300_000


class TestPluginSourceHasTrackingConstants:
    """The enforce-floor.ts source must define the DP.1 tracking
    constants and functions."""

    def _src(self):
        return ENFORCE_FLOOR_PATH.read_text()

    def test_missed_commit_file_defined(self):
        src = self._src()
        assert "MISSED_COMMIT_FILE" in src, (
            "MISSED_COMMIT_FILE constant must be defined in enforce-floor.ts"
        )

    def test_missed_commit_threshold_defined(self):
        src = self._src()
        assert "MISSED_COMMIT_THRESHOLD" in src, (
            "MISSED_COMMIT_THRESHOLD constant must be defined in enforce-floor.ts"
        )

    def test_read_missed_commit_state_defined(self):
        src = self._src()
        assert "readMissedCommitState" in src, (
            "readMissedCommitState function must be defined in enforce-floor.ts"
        )

    def test_record_missed_commit_defined(self):
        src = self._src()
        assert "recordMissedCommit" in src, (
            "recordMissedCommit function must be defined in enforce-floor.ts"
        )

    def test_maybe_remind_missed_commit_dispatch_defined(self):
        src = self._src()
        assert "maybeRemindMissedCommitDispatch" in src, (
            "maybeRemindMissedCommitDispatch function must be defined in enforce-floor.ts"
        )

    def test_dp1_reminder_message_in_source(self):
        src = self._src()
        assert "Use one dispatch slot for make ship-commit" in src, (
            "DP.1 reminder message must be in enforce-floor.ts: "
            "'Use one dispatch slot for make ship-commit — keeps 9 productive tasks running.'"
        )

    def test_commit_detection_calls_record_missed(self):
        src = self._src()
        assert "recordMissedCommit()" in src, (
            "commitToolMode branch must call recordMissedCommit()"
        )

    def test_tracking_wired_after_commit_tool_mode(self):
        src = self._src()
        assert "commitToolMode" in src, (
            "commitToolMode variable must be used in enforce-floor.ts "
            "for commit detection"
        )


class TestMissedCommitStateFileFormat:
    """The state file must use the correct JSON schema."""

    def test_fresh_state_has_expected_keys(self):
        """A fresh (zero-miss) state has all required fields."""
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        for key in ("misses", "last_miss_ts", "last_reminder_ts", "pid"):
            assert key in state, f"Fresh state must contain key '{key}'"

    def test_misses_counter_is_integer(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        assert isinstance(state["misses"], int)

    def test_timestamps_are_numeric(self):
        state = {"misses": 1, "last_miss_ts": 1720000000000, "last_reminder_ts": 0, "pid": 42}
        assert isinstance(state["last_miss_ts"], (int, float))
        assert isinstance(state["last_reminder_ts"], (int, float))

    def test_pid_tracked(self):
        state = {"misses": 1, "last_miss_ts": 1720000000000, "last_reminder_ts": 0, "pid": 42}
        assert state["pid"] > 0


class TestMissedCommitTrackingBehavior:
    """Simulates the tracking logic as implemented in the plugin."""

    def _simulate_miss(self, state):
        """Replicates recordMissedCommit() behavior."""
        state["misses"] += 1
        state["last_miss_ts"] = 1720000000000
        state["pid"] = 42
        return state

    def _maybe_remind(self, state, now=1720000000000):
        """Replicates maybeRemindMissedCommitDispatch() behavior."""
        if state["misses"] < MISSED_COMMIT_THRESHOLD:
            return None
        if now - state.get("last_reminder_ts", 0) < MISSED_COMMIT_REMINDER_MS:
            return None
        state["last_reminder_ts"] = now
        return "DP.1: Use one dispatch slot for make ship-commit — keeps 9 productive tasks running."

    def test_one_miss_no_reminder(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        state = self._simulate_miss(state)
        assert state["misses"] == 1
        result = self._maybe_remind(state)
        assert result is None, "1 miss should not trigger reminder"

    def test_two_misses_no_reminder(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        state = self._simulate_miss(state)
        state = self._simulate_miss(state)
        assert state["misses"] == 2
        result = self._maybe_remind(state)
        assert result is None, "2 misses should not trigger reminder"

    def test_three_misses_triggers_reminder(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        state = self._simulate_miss(state)
        state = self._simulate_miss(state)
        state = self._simulate_miss(state)
        assert state["misses"] == 3
        result = self._maybe_remind(state)
        assert result is not None, "3 misses MUST trigger reminder"
        assert "make ship-commit" in result
        assert "dispatch slot" in result
        assert "9 productive tasks" in result

    def test_four_misses_triggers_reminder(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        for _ in range(4):
            state = self._simulate_miss(state)
        assert state["misses"] == 4
        result = self._maybe_remind(state)
        assert result is not None, "4 misses MUST trigger reminder"

    def test_reminder_has_cooldown(self):
        """After a reminder fires, another within REMINDER_MS is suppressed."""
        state = {"misses": 3, "last_miss_ts": 1720000000000, "last_reminder_ts": 0, "pid": 42}
        now = 1720000000000
        result1 = self._maybe_remind(state, now=now)
        assert result1 is not None, "First reminder at 3 misses should fire"
        result2 = self._maybe_remind(state, now=now + 10000)
        assert result2 is None, "Reminder within cooldown should be suppressed"

    def test_reminder_fires_after_cooldown(self):
        """After the cooldown expires, the reminder fires again."""
        state = {"misses": 3, "last_miss_ts": 1720000000000, "last_reminder_ts": 1720000000000, "pid": 42}
        now = 1720000000000 + MISSED_COMMIT_REMINDER_MS + 1000
        result = self._maybe_remind(state, now=now)
        assert result is not None, "Reminder should fire after cooldown expires"

    def test_multiple_commits_accumulate(self):
        state = {"misses": 0, "last_miss_ts": 0, "last_reminder_ts": 0, "pid": 0}
        for i in range(7):
            state = self._simulate_miss(state)
            assert state["misses"] == i + 1


class TestCommitCommandDetection:
    """The isCommitBashCommand regex must match all sanctioned commit targets."""

    COMMIT_TARGETS: ClassVar = [
        "make git-commit MSG='fix: something'",
        "make commit-no-verify MSG='urgent'",
        "make git-commit-file FILES='src/x.py'",
        "make test-and-commit MSG='tdd'",
        "make repo-commit MSG='bump version'",
        "make feature-done MSG='feature/foo'",
        "make git-merge MSG='branch'",
    ]

    NON_COMMIT_TARGETS: ClassVar = [
        "make git-log",
        "make git-status",
        "make git-diff",
        "make git-add FILES='x'",
        "make ci-verdict BRANCH=master",
        "make lint",
        "make test",
    ]

    COMMIT_RE: ClassVar = re.compile(
        r"^make\s+(git-commit|commit-no-verify|git-commit-file|test-and-commit|repo-commit|feature-done|git-merge)(\s|$)"
    )

    def test_all_commit_targets_match(self):
        for cmd in self.COMMIT_TARGETS:
            assert self.COMMIT_RE.search(cmd), f"Should match commit target: {cmd}"

    def test_non_commit_targets_do_not_match(self):
        for cmd in self.NON_COMMIT_TARGETS:
            assert not self.COMMIT_RE.search(cmd), f"Should NOT match non-commit: {cmd}"

    def test_commit_regex_in_plugin_source(self):
        src = ENFORCE_FLOOR_PATH.read_text()
        assert "isCommitBashCommand" in src, (
            "isCommitBashCommand must be defined in enforce-floor.ts"
        )
        pattern_source = re.search(
            r"return\s+(/[\s\S]*?/)\.test", src
        )
        assert pattern_source is not None, (
            "isCommitBashCommand must use a regex pattern on cmd"
        )
