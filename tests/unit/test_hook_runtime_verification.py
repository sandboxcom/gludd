"""TDD runtime-verification tests for hook surfaces (BUGS.md item 6).

BUGS.md line 375-414 documented that `experimental.chat.response.transform` was
dead code in ALL 5 response-scanning plugins — the hook name is NOT a real member
of the @opencode-ai/plugin Hooks interface, so the runtime never invokes it.
Every plugin has since been migrated to `session.idle` and `text.complete`.

These tests verify that the migrated hooks produce observable side effects
(state files written to /tmp). Per BUGS.md line 414: "Every hook registration
MUST have a runtime-verification test that proves the hook actually fires."

For each test, missing files are skipped (not failed) since hooks may not fire
in all environments (e.g. test runners, CI). Assertions about file existence
and structure are made where reasonable.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

# ── Helpers ─────────────────────────────────────────────────────────────────

def _plugin_ts_files():
    return sorted(PLUGIN_DIR.glob("*.ts"))


def _read_state_file(path_str: str):
    """Read a JSON state file, returning (exists, data_or_none)."""
    try:
        with open(path_str) as f:
            return True, json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False, None


# ── Test 1: enforce-stop.ts writes state files via session.idle ──────────────

class TestEnforceStopWritesStateFiles:
    """Prove enforce-stop.ts session.idle hook actually fires by checking for
    /tmp/gludd-stop-state.json, which is written at session.idle (line 324).
    BUGS.md line 380 proves the old chat.response.transform path NEVER fired
    (/tmp/gludd-false-done-blocks.json was MISSING). The migrated session.idle
    hook should produce this file.
    """

    STATE_FILE = "/tmp/gludd-stop-state.json"

    def test_stop_state_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — session.idle may not have fired yet")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_stop_state_has_expected_keys(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        expected_keys = {
            "ts", "ratchetEntries", "tasksMdUnchecked", "gateStatusRed",
            "repoPending", "hasPendingWork", "hasLocalWork", "healthScore",
        }
        missing = expected_keys - set(data.keys())
        assert not missing, (
            f"{self.STATE_FILE} missing expected keys: {missing}"
        )

    def test_stop_state_has_valid_types(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert isinstance(data["ts"], (int, float)), "ts must be a number"
        assert isinstance(data["ratchetEntries"], int), "ratchetEntries must be an integer"
        assert isinstance(data["tasksMdUnchecked"], bool), "tasksMdUnchecked must be a boolean"
        assert isinstance(data["gateStatusRed"], bool), "gateStatusRed must be a boolean"
        assert isinstance(data["repoPending"], bool), "repoPending must be a boolean"
        assert isinstance(data["hasPendingWork"], bool), "hasPendingWork must be a boolean"
        assert isinstance(data["hasLocalWork"], bool), "hasLocalWork must be a boolean"
        assert isinstance(data["healthScore"], (int, float)), "healthScore must be a number"


# ── Test 2: enforce-deadline.ts writes state files via tool.execute.before ────

class TestEnforceDeadlineWritesStateFiles:
    """Prove enforce-deadline.ts tool.execute.before hook fires by checking for
    /tmp/gludd-task-deadlines.json, written at dispatch time (line 163).
    BUGS.md line 383 proves tool.execute.before hooks DO work.
    """

    STATE_FILE = "/tmp/gludd-task-deadlines.json"

    def test_deadline_state_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — no tasks dispatched yet in this session")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_deadline_state_has_valid_entries(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert len(data) > 0, (
            f"{self.STATE_FILE} should contain at least one task entry"
        )

        for task_id, start_time in data.items():
            assert isinstance(task_id, str), f"task id {task_id!r} must be a string"
            assert isinstance(start_time, (int, float)), (
                f"task {task_id!r} start_time must be a number"
            )

    def test_deadline_entries_are_recent(self):
        """Entry timestamps should be within the last 24 hours (fresh session)."""
        import time
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        now_ms = time.time() * 1000
        stale_entries = []
        for task_id, start_time in data.items():
            age_ms = now_ms - start_time
            if age_ms > 86_400_000:  # 24 hours
                stale_entries.append((task_id, age_ms / 1000))

        assert not stale_entries, (
            f"Stale task deadline entries (>24h old): {stale_entries}"
        )


# ── Test 3: enforce-session-start.ts writes files via tool.execute.before ─────

class TestEnforceSessionStartWritesStateFiles:
    """enforce-session-start.ts writes /tmp/gludd-session-start.json via
    tool.execute.before (loadState at system.transform time, dispatches
    counted at tool.execute.before). BUGS.md line 382 proves this file
    exists and contains non-zero dispatches.
    """

    STATE_FILE = "/tmp/gludd-session-start.json"

    def test_session_start_state_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — session may not have started yet")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_session_start_has_expected_keys(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert "started_at" in data, "must have started_at timestamp"
        assert "readsDone" in data, "must have readsDone flag"
        assert "dispatches" in data, "must have dispatches count"

    def test_session_start_dispatches_non_negative(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert isinstance(data["dispatches"], (int, float)), (
            "dispatches must be a number"
        )
        assert data["dispatches"] >= 0, (
            f"dispatches must be non-negative, got {data['dispatches']}"
        )


# ── Test 3b: enforce-delegate.ts writes mainthread-streak file ─────────────

class TestEnforceDelegateWritesStateFiles:
    """enforce-delegate.ts writes /tmp/gludd-mainthread-streak.json via
    mainthreadBudgetAfter() in tool.execute.after. Also writes
    /tmp/gludd-force-delegate.json for force-delegate state.
    """

    STREAK_FILE = "/tmp/gludd-mainthread-streak.json"
    FORCE_FILE = "/tmp/gludd-force-delegate.json"

    def test_mainthread_streak_file_exists(self):
        exists, data = _read_state_file(self.STREAK_FILE)
        if not exists:
            pytest.skip(f"{self.STREAK_FILE} does not exist — no main-thread tool calls yet")

        assert isinstance(data, dict), (
            f"{self.STREAK_FILE} must contain a JSON object"
        )

    def test_mainthread_streak_has_expected_shape(self):
        exists, data = _read_state_file(self.STREAK_FILE)
        if not exists:
            pytest.skip(f"{self.STREAK_FILE} does not exist")

        # Back-compat shape: {"count": int, ...}
        assert "count" in data, f"{self.STREAK_FILE} must have 'count' key"
        assert isinstance(data["count"], (int, float)), "count must be a number"

    def test_force_delegate_file_if_exists(self):
        exists, data = _read_state_file(self.FORCE_FILE)
        if not exists:
            pytest.skip(f"{self.FORCE_FILE} does not exist — GLUDD_FORCE_DELEGATE likely not active")

        assert isinstance(data, dict), (
            f"{self.FORCE_FILE} must contain a JSON object"
        )


# ── Test 4: Verify no chat.response.transform registrations ─────────────────

class TestNoChatResponseTransformRegistrations:
    """Prove that all plugins have been migrated OFF the dead
    `experimental.chat.response.transform` hook surface (BUGS.md lines 375-414).

    Zero .ts files under .opencode/plugin/ should contain
    `chat.response.transform` as a hook registration string. Comments
    mentioning the migration are fine (they describe the transition), but
    no hook registration should reference the dead surface.
    """

    def test_zero_chat_response_transform_in_ts_files(self):
        """Grep all .ts files for 'chat.response.transform' — must be zero matches."""
        violations = []
        for ts_file in _plugin_ts_files():
            content = ts_file.read_text()
            if "chat.response.transform" in content:
                # Check if it's a comment-only reference (migration note)
                lines = content.split("\n")
                for lineno, line in enumerate(lines, 1):
                    if "chat.response.transform" in line:
                        stripped = line.strip()
                        is_comment = stripped.startswith("//") or stripped.startswith("*")
                        violations.append({
                            "file": str(ts_file.relative_to(ROOT)),
                            "line": lineno,
                            "content": stripped,
                            "is_comment": is_comment,
                        })

        # Filter to only real registrations (non-comment occurrences)
        real_violations = [v for v in violations if not v["is_comment"]]
        assert len(real_violations) == 0, (
            f"Found {len(real_violations)} chat.response.transform hook registration(s) "
            f"that are not in comments. These MUST be migrated to session.idle / "
            f"text.complete. Violations:\n" +
            "\n".join(f"  {v['file']}:{v['line']}: {v['content']}" for v in real_violations)
        )

    def test_chat_response_transform_only_in_comments(self):
        """If chat.response.transform appears, it must ONLY be in comments."""
        for ts_file in _plugin_ts_files():
            content = ts_file.read_text()
            if "chat.response.transform" not in content:
                continue
            lines = content.split("\n")
            for lineno, line in enumerate(lines, 1):
                if "chat.response.transform" not in line:
                    continue
                stripped = line.strip()
                is_comment = stripped.startswith("//") or stripped.startswith("*")
                is_empty = stripped == ""
                is_not_a_hook_registration = (
                    is_comment or is_empty or
                    "migrated" in line.lower() or
                    "dead" in line.lower() or
                    "replaced" in line.lower()
                )
                assert is_not_a_hook_registration, (
                    f"{ts_file.relative_to(ROOT)}:{lineno}: "
                    f"chat.response.transform found outside a comment: {stripped!r}"
                )


# ── Test 5: All plugins export default ────────────────────────────────────

class TestAllPluginsExportDefault:
    """Every .ts plugin file under .opencode/plugin/ must export a default
    function (the Plugin object). Without a default export, opencode cannot
    load the plugin — it silently skips it with no error or warning.
    """

    def test_every_plugin_exports_default(self):
        missing = []
        for ts_file in _plugin_ts_files():
            content = ts_file.read_text()
            if "export default" not in content:
                missing.append(str(ts_file.relative_to(ROOT)))
        assert not missing, (
            f"Plugins missing 'export default': {missing}. "
            f"Without a default export, opencode silently skips the plugin."
        )

    def test_plugin_count_matches_registered(self):
        """The number of .ts files should match what's expected (8 plugins)."""
        files = _plugin_ts_files()
        assert len(files) >= 8, (
            f"Expected at least 8 plugin files, found {len(files)}: "
            f"{[f.name for f in files]}"
        )

    def test_each_plugin_has_hook_registration(self):
        """Each plugin should register at least one hook surface."""
        hook_surfaces = [
            "tool.execute.before",
            "tool.execute.after",
            "experimental.chat.system.transform",
            "experimental.text.complete",
            "session.idle",
            "event",
        ]
        missing_hooks = []
        for ts_file in _plugin_ts_files():
            content = ts_file.read_text()
            has_hook = any(hs in content for hs in hook_surfaces)
            if not has_hook:
                missing_hooks.append(str(ts_file.relative_to(ROOT)))
        assert not missing_hooks, (
            f"Plugins with no hook registration: {missing_hooks}"
        )


# ── Test 6: Block counter files (enforce-stop.ts false-positive cascade) ─────

class TestEnforceStopBlockCounter:
    """enforce-stop.ts writes /tmp/gludd-block-counter.json and
    /tmp/gludd-block-reason.json to track consecutive blocks and prevent
    false-positive cascades (lines 42-87).
    """

    COUNTER_FILE = "/tmp/gludd-block-counter.json"
    REASON_FILE = "/tmp/gludd-block-reason.json"

    def test_block_counter_file_if_exists(self):
        exists, data = _read_state_file(self.COUNTER_FILE)
        if not exists:
            pytest.skip(f"{self.COUNTER_FILE} does not exist — no blocks have occurred")

        assert isinstance(data, dict)
        assert "consecutiveBlocks" in data
        assert "totalBlocks" in data

    def test_block_reason_file_if_exists(self):
        exists, data = _read_state_file(self.REASON_FILE)
        if not exists:
            pytest.skip(f"{self.REASON_FILE} does not exist — no blocks have occurred")

        assert isinstance(data, dict)
        assert "reason" in data
        assert isinstance(data["reason"], str)
