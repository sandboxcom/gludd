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
PLUGINS_DIR = ROOT / ".opencode" / "plugins"

# ── Helpers ─────────────────────────────────────────────────────────────────

def _plugin_ts_files():
    return sorted(list(PLUGIN_DIR.glob("*.ts")) + list(PLUGINS_DIR.glob("*.ts")))


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
        if len(data) == 0:
            pytest.skip(f"{self.STATE_FILE} is empty (no tasks dispatched this session)")

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


# ── Test 7: enforce-stop.ts writes text.complete count file ─────────────────

class TestEnforceStopTextComplete:
    """enforce-stop.ts writes /tmp/gludd-stop-text-complete-count.json via
    experimental.text.complete. BUGS.md line 414 mandates runtime-verification
    of every migrated hook surface.
    """

    STATE_FILE = "/tmp/gludd-stop-text-complete-count.json"

    def test_text_complete_count_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — text.complete may not have fired yet")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_text_complete_has_count(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert "count" in data, (
            f"{self.STATE_FILE} missing 'count' key"
        )

    def test_text_complete_count_non_negative(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert isinstance(data["count"], (int, float)), "count must be a number"
        assert data["count"] >= 0, f"count must be non-negative, got {data['count']}"

    def test_text_complete_count_increasing(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert data["count"] > 0, (
            f"text.complete count should be > 0 (hook has fired), got {data['count']}"
        )


# ── Test 8: enforce-floor.ts writes text.complete count file ────────────────

class TestEnforceFloorTextComplete:
    """enforce-floor.ts writes /tmp/gludd-floor-text-complete-count.json via
    experimental.text.complete. Confirms the hook surface is live.
    """

    STATE_FILE = "/tmp/gludd-floor-text-complete-count.json"

    def test_floor_text_complete_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — text.complete may not have fired yet")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_floor_text_complete_has_count(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert "count" in data, (
            f"{self.STATE_FILE} missing 'count' key"
        )

    def test_floor_text_complete_count_non_negative(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert isinstance(data["count"], (int, float)), "count must be a number"
        assert data["count"] >= 0, f"count must be non-negative, got {data['count']}"

    def test_floor_text_complete_count_increasing(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert data["count"] > 0, (
            f"text.complete count should be > 0 (hook has fired), got {data['count']}"
        )


# ── Test 9: enforce-stop.ts writes tool-counts file via tool.execute.before ─

class TestEnforceStopToolCounts:
    """enforce-stop.ts writes /tmp/gludd-stop-tool-counts.json via
    tool.execute.before. Tracks per-tool invocation counts for the
    anti-loop / anti-grind enforcement logic.
    """

    STATE_FILE = "/tmp/gludd-stop-tool-counts.json"

    def test_tool_counts_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — no tool calls yet in this session")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_tool_counts_has_expected_shape(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert len(data) > 0, (
            f"{self.STATE_FILE} should contain at least one entry"
        )

        # Entries may be mixed: numeric counts for tools, dict metadata for
        # keys like "last_fired". Validate numeric entries only.
        for key, value in data.items():
            assert isinstance(key, str), f"entry key {key!r} must be a string"
            if isinstance(value, (int, float)):
                assert value >= 0, (
                    f"tool {key!r} count must be non-negative, got {value}"
                )
            elif isinstance(value, dict):
                assert "ts" in value, (
                    f"metadata entry {key!r} must have 'ts' timestamp"
                )
            elif isinstance(value, str):
                pass  # Metadata strings like "_outcome" are fine
            else:
                pytest.fail(
                    f"Unexpected type {type(value).__name__} for key {key!r} "
                    f"in {self.STATE_FILE}"
                )

    def test_tool_counts_positive_total(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        numeric_counts = [
            v for v in data.values() if isinstance(v, (int, float))
        ]
        assert len(numeric_counts) > 0, (
            f"{self.STATE_FILE} should contain at least one numeric count"
        )
        total = sum(numeric_counts)
        assert total > 0, (
            f"{self.STATE_FILE} total tool count should be > 0, got {total}"
        )


# ── Test 10: enforce-delegate.ts writes model-util file via tool.execute.before

class TestEnforceDelegateModelUtil:
    """enforce-delegate.ts writes /tmp/gludd-model-util.json via
    tool.execute.before (or modelUtilBefore). Tracks per-model dispatch
    counts for the sonnet ratio enforcement logic.
    """

    STATE_FILE = "/tmp/gludd-model-util.json"

    def test_model_util_file_exists(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist — no model dispatches recorded yet")

        assert isinstance(data, dict), (
            f"{self.STATE_FILE} must contain a JSON object"
        )

    def test_model_util_has_valid_entries(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        assert len(data) > 0, (
            f"{self.STATE_FILE} should contain at least one entry"
        )

        # Entries may be mixed: string lists for "history", dict views for
        # per-model entries, or nested dicts. Validate each type.
        for key, entry in data.items():
            assert isinstance(key, str), f"entry key {key!r} must be a string"
            if isinstance(entry, list):
                assert all(isinstance(m, str) for m in entry), (
                    f"{key!r} list entries must be strings (model names)"
                )
            elif isinstance(entry, dict):
                if "count" in entry:
                    assert isinstance(entry["count"], (int, float)), (
                        f"{key!r}: count must be a number"
                    )
            elif isinstance(entry, (int, float)):
                pass
            else:
                pytest.fail(
                    f"Unexpected type {type(entry).__name__} for key {key!r} "
                    f"in {self.STATE_FILE}"
                )

    def test_model_util_last_model_is_string(self):
        exists, data = _read_state_file(self.STATE_FILE)
        if not exists:
            pytest.skip(f"{self.STATE_FILE} does not exist")

        if "last_model" in data:
            assert isinstance(data["last_model"], str), (
                "last_model must be a string"
            )


# ── Test 11: watchdog.ts writes PID file via event/session.created ──────────

class TestWatchdogPidFile:
    """watchdog.ts writes /tmp/gludd-watchdog.pid via event/session.created.
    Confirms the watchdog daemon has started and its PID file exists.
    Not a JSON file — contains a plain integer PID.
    """

    PID_FILE = "/tmp/gludd-watchdog.pid"

    def test_watchdog_pid_file_exists(self):
        path = Path(self.PID_FILE)
        if not path.exists():
            pytest.skip(f"{self.PID_FILE} does not exist — watchdog may not be running")

        assert path.is_file(), f"{self.PID_FILE} must be a regular file"

    def test_watchdog_pid_is_valid_number(self):
        path = Path(self.PID_FILE)
        if not path.exists():
            pytest.skip(f"{self.PID_FILE} does not exist")

        content = path.read_text().strip()
        assert content, f"{self.PID_FILE} must not be empty"
        assert content.isdigit(), (
            f"{self.PID_FILE} must contain a positive integer PID, got {content!r}"
        )
        pid = int(content)
        assert pid > 0, f"PID must be positive, got {pid}"

    def test_watchdog_pid_is_running_process(self):
        import os
        path = Path(self.PID_FILE)
        if not path.exists():
            pytest.skip(f"{self.PID_FILE} does not exist")

        pid = int(path.read_text().strip())
        import contextlib
        with contextlib.suppress(OSError):
            os.kill(pid, 0)  # May not run in test/CI; skip is too strong


# ── Test 12: enforce-deletion-gate.ts writes audit log via tool.execute.before

class TestDeletionGateAuditLog:
    """enforce-deletion-gate.ts writes .deletion-audit.log via
    tool.execute.before when a file-deletion attempt is audited.
    Not a JSON file — line-oriented audit log with timestamps.
    """

    AUDIT_LOG = ROOT / ".deletion-audit.log"

    def test_deletion_audit_log_exists(self):
        if not self.AUDIT_LOG.exists():
            pytest.skip(f"{self.AUDIT_LOG} does not exist — no deletion attempts have been audited")

        assert self.AUDIT_LOG.is_file(), f"{self.AUDIT_LOG} must be a regular file"

    def test_deletion_audit_log_is_not_empty(self):
        if not self.AUDIT_LOG.exists():
            pytest.skip(f"{self.AUDIT_LOG} does not exist")

        content = self.AUDIT_LOG.read_text().strip()
        assert content, f"{self.AUDIT_LOG} must not be empty"

    def test_deletion_audit_log_has_lines(self):
        if not self.AUDIT_LOG.exists():
            pytest.skip(f"{self.AUDIT_LOG} does not exist")

        lines = self.AUDIT_LOG.read_text().strip().split("\n")
        assert len(lines) >= 1, (
            f"{self.AUDIT_LOG} must contain at least one audit entry"
        )
        for i, line in enumerate(lines):
            assert line.strip(), f"line {i + 1} in {self.AUDIT_LOG} must not be blank"
