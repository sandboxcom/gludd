"""CI-runnable hook-liveness tests for hook surfaces (BUGS.md item 6, Wave E).

BUGS.md line 375-414 documented that `experimental.chat.response.transform` was
dead code in ALL 5 response-scanning plugins — the hook name is NOT a real member
of the @opencode-ai/plugin Hooks interface, so the runtime never invokes it.
Every plugin has since been migrated to `session.idle` and `text.complete`.

Per BUGS.md line 414: "Every hook registration MUST have a runtime-verification
test that proves the hook actually fires." The original version of this module
proved that only by CHECKING FOR SIDE-EFFECT FILES LEFT BY A PRIOR LIVE SESSION
— every assertion was gated behind `pytest.skip` if the file happened to be
absent, so the suite could pass green forever without ever actually exercising
a hook.

This version (Wave E) ACTUALLY INVOKES each plugin's hook handler via
scripts/hook_plugin_harness.mjs (node --experimental-strip-types — no npm
install needed, see that script's docstring) through the `hook_plugin_env`
fixture in `_hook_fixtures.py`, and asserts on the resulting state file with a
hard (non-skippable) assertion. The only remaining skip is the fixture's own
node-version probe (`pytest.skip` when node < 22.6 / absent on this machine —
see HOOK_LIVE_SKIP_REASON), which is an environment precondition, not a soft
"maybe it fired" check.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
    invoke_text_complete_and_confirm_increment,
    teardown_watchdog_session,
)

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _plugin_ts_files():
    return sorted(list(PLUGIN_DIR.glob("*.ts")) + list(PLUGINS_DIR.glob("*.ts")))


# ── Test 1: enforce-stop.ts writes state files via session.idle ──────────────


class TestEnforceStopWritesStateFiles:
    """enforce-stop.ts's `event` handler writes GLUDD_STOP_STATE_FILE on a
    `session.idle` event (BUGS.md line 324's migrated equivalent). Drive the
    hook directly and assert the resulting state file's shape."""

    def test_stop_state_file_exists_and_is_dict(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke("enforce-stop.ts", "event", input={"event": {"type": "session.idle"}})
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_STOP_STATE_FILE")
        assert isinstance(data, dict), f"expected a dict, got {data!r}"

    def test_stop_state_has_expected_keys_and_types(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke("enforce-stop.ts", "event", input={"event": {"type": "session.idle"}})
        data = hook_plugin_env.read_json("GLUDD_STOP_STATE_FILE")
        expected_keys = {
            "ts",
            "ratchetEntries",
            "tasksMdUnchecked",
            "gateStatusRed",
            "repoPending",
            "hasPendingWork",
            "hasLocalWork",
            "healthScore",
        }
        missing = expected_keys - set(data.keys())
        assert not missing, f"missing expected keys: {missing}"
        assert isinstance(data["ts"], (int, float))
        assert isinstance(data["ratchetEntries"], int)
        assert isinstance(data["tasksMdUnchecked"], bool)
        assert isinstance(data["gateStatusRed"], bool)
        assert isinstance(data["repoPending"], bool)
        assert isinstance(data["hasPendingWork"], bool)
        assert isinstance(data["hasLocalWork"], bool)
        assert isinstance(data["healthScore"], (int, float))


# ── Test 2: enforce-deadline.ts writes state files via tool.execute.before ────


class TestEnforceDeadlineWritesStateFiles:
    """enforce-deadline.ts's tool.execute.before records a dispatch timestamp
    for task/agent/workflow tool calls into GLUDD_TASK_DEADLINE_STATE."""

    def test_deadline_state_file_written_on_task_dispatch(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            "enforce-deadline.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"description": "hook-liveness smoke", "subagent_type": "general-purpose"}},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_TASK_DEADLINE_STATE")
        assert isinstance(data, dict)
        assert len(data) == 1, f"expected exactly one recorded dispatch, got {data}"

    def test_deadline_state_has_valid_entries(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke(
            "enforce-deadline.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"description": "hook-liveness smoke", "subagent_type": "general-purpose"}},
        )
        data = hook_plugin_env.read_json("GLUDD_TASK_DEADLINE_STATE")
        for task_id, start_time in data.items():
            assert isinstance(task_id, str)
            assert isinstance(start_time, (int, float))

    def test_deadline_entries_are_recent(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke(
            "enforce-deadline.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"description": "hook-liveness smoke", "subagent_type": "general-purpose"}},
        )
        data = hook_plugin_env.read_json("GLUDD_TASK_DEADLINE_STATE")
        now_ms = time.time() * 1000
        for task_id, start_time in data.items():
            age_ms = now_ms - start_time
            assert age_ms < 60_000, f"task {task_id!r} timestamp is {age_ms / 1000:.1f}s old"


# ── Test 3: enforce-session-start.ts writes files via tool.execute.before ─────


class TestEnforceSessionStartWritesStateFiles:
    """enforce-session-start.ts writes GLUDD_SESSION_STATE via
    tool.execute.before. A `read` of TASKS.md marks readsDone=true (see
    isTaskFileRead()); this is also the deduped conversion of the two
    identical hook-state tests formerly duplicated in test_process_overhead.py
    (see that file's TestGuardrailEffectivenessRatio)."""

    def test_session_start_state_file_written_and_shape(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            "enforce-session-start.ts",
            "tool.execute.before",
            input={"tool": "read", "filePath": "TASKS.md"},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_SESSION_STATE")
        assert isinstance(data, dict)
        assert "started_at" in data
        assert "readsDone" in data
        assert "dispatches" in data
        assert data["readsDone"] is True, "reading TASKS.md must set readsDone=true"

    def test_session_start_dispatches_non_negative(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke(
            "enforce-session-start.ts",
            "tool.execute.before",
            input={"tool": "read", "filePath": "TASKS.md"},
        )
        data = hook_plugin_env.read_json("GLUDD_SESSION_STATE")
        assert isinstance(data["dispatches"], (int, float))
        assert data["dispatches"] >= 0


# ── Test 3b: enforce-delegate.ts writes mainthread-streak + force-delegate files


class TestEnforceDelegateWritesStateFiles:
    """enforce-delegate.ts writes GLUDD_MAINTHREAD_STREAK_FILE via
    tool.execute.after (mainthreadBudgetAfter), and GLUDD_FORCE_DELEGATE_STATE
    via tool.execute.before (enforceForceDelegate, opt-in GLUDD_FORCE_DELEGATE=1)."""

    def test_mainthread_streak_file_increments(self, hook_plugin_env: HookEnv):
        for _ in range(3):
            result = hook_plugin_env.invoke("enforce-delegate.ts", "tool.execute.after", input={"tool": "edit"})
            assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_MAINTHREAD_STREAK_FILE")
        assert isinstance(data, dict)
        assert data["count"] == 3, f"expected count==3 after 3 edit calls, got {data}"

    def test_force_delegate_state_accumulates_consecutive_targeted(self, hook_plugin_env: HookEnv):
        # FORCE_DELEGATE_GRACE defaults to 3; drive grace+1 = 4 targeted edit
        # calls (opt-in via GLUDD_FORCE_DELEGATE=1) against a non-memory file
        # path and assert the state file's consecutive_targeted counter.
        scratch_file = str(hook_plugin_env.cwd / "scratch.py")
        grace_plus_one = 4
        last = None
        for _ in range(grace_plus_one):
            last = hook_plugin_env.invoke(
                "enforce-delegate.ts",
                "tool.execute.before",
                input={"tool": "edit"},
                output={"args": {"filePath": scratch_file}},
                env_overrides={"GLUDD_FORCE_DELEGATE": "1"},
            )
        assert last is not None and last.returncode == 0, last.stderr if last else "no calls made"
        data = hook_plugin_env.read_json("GLUDD_FORCE_DELEGATE_STATE")
        assert isinstance(data, dict)
        assert data["consecutive_targeted"] == grace_plus_one, (
            f"expected consecutive_targeted=={grace_plus_one}, got {data}"
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
                lines = content.split("\n")
                for lineno, line in enumerate(lines, 1):
                    if "chat.response.transform" in line:
                        stripped = line.strip()
                        is_comment = stripped.startswith("//") or stripped.startswith("*")
                        violations.append(
                            {
                                "file": str(ts_file.relative_to(ROOT)),
                                "line": lineno,
                                "content": stripped,
                                "is_comment": is_comment,
                            }
                        )

        real_violations = [v for v in violations if not v["is_comment"]]
        assert len(real_violations) == 0, (
            f"Found {len(real_violations)} chat.response.transform hook registration(s) "
            f"that are not in comments. These MUST be migrated to session.idle / "
            f"text.complete. Violations:\n"
            + "\n".join(f"  {v['file']}:{v['line']}: {v['content']}" for v in real_violations)
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
                    is_comment
                    or is_empty
                    or "migrated" in line.lower()
                    or "dead" in line.lower()
                    or "replaced" in line.lower()
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
        assert len(files) >= 8, f"Expected at least 8 plugin files, found {len(files)}: {[f.name for f in files]}"

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
        assert not missing_hooks, f"Plugins with no hook registration: {missing_hooks}"


# ── Test 6: Block counter files (enforce-stop.ts false-positive cascade) ─────


class TestEnforceStopBlockCounter:
    """enforce-stop.ts's main-thread-grinding guard (GRINDING_HARD_DENY_THRESHOLD
    = 12) returns a `permissionDecision: "deny"` and calls recordBlock(), which
    writes /tmp/gludd-block-counter.json and /tmp/gludd-block-reason.json.
    These two paths are HARDCODED in enforce-stop.ts (no env override) — the
    hook_plugin_env fixture snapshots/restores them around this test (see
    _hook_fixtures.py HARDCODED_TMP_PATHS) so this never leaks into a live
    session's shared state.

    Pre-seed GLUDD_STREAK_FILE (env-overridable) with streak=13 (> threshold)
    so a single `edit` tool.execute.before call is denied deterministically.
    """

    BLOCK_COUNTER_FILE = "/tmp/gludd-block-counter.json"
    BLOCK_REASON_FILE = "/tmp/gludd-block-reason.json"

    def _seed_streak(self, hook_plugin_env: HookEnv, streak: int) -> None:
        streak_path = hook_plugin_env.state_path("GLUDD_STREAK_FILE")
        streak_path.write_text(
            json.dumps(
                {
                    "streak": streak,
                    "lastDispatchTs": 0,
                    "readStreak": 0,
                    "editStreak": streak,
                    "lastUpdateTs": 0,
                    "lastWriter": "",
                }
            )
        )

    def test_grinding_streak_denies_and_writes_block_counter(self, hook_plugin_env: HookEnv):
        self._seed_streak(hook_plugin_env, 13)
        result = hook_plugin_env.invoke("enforce-stop.ts", "tool.execute.before", input={"tool": "edit"})
        assert result.returncode == 0, result.stderr  # deny is a RETURN value, not a throw
        payload = json.loads(result.stdout)
        assert payload is not None and payload.get("permissionDecision") == "deny", payload

        data = json.loads(Path(hook_plugin_env.env["GLUDD_BLOCK_COUNTER_FILE"]).read_text())
        assert isinstance(data, dict)
        assert "consecutiveBlocks" in data
        assert "totalBlocks" in data
        assert data["totalBlocks"] >= 1

    def test_grinding_streak_writes_block_reason(self, hook_plugin_env: HookEnv):
        self._seed_streak(hook_plugin_env, 13)
        hook_plugin_env.invoke("enforce-stop.ts", "tool.execute.before", input={"tool": "edit"})
        data = json.loads(Path(hook_plugin_env.env["GLUDD_BLOCK_REASON_FILE"]).read_text())
        assert isinstance(data, dict)
        assert data["reason"] == "main-thread-grinding"
        assert isinstance(data["reason"], str)


# ── Test 7 / 8: stop / floor text.complete counters increment across calls ──


class TestEnforceStopTextComplete:
    """enforce-stop.ts's experimental.text.complete writes
    /tmp/gludd-stop-text-complete-count.json (HARDCODED path — see
    HARDCODED_TMP_PATHS). Drive it twice and assert the count strictly
    increases between calls. Retried (see
    invoke_text_complete_and_confirm_increment) because this hardcoded path
    is shared with any live opencode session on this machine, which can
    occasionally race a single pair of calls to a tie."""

    STATE_FILE = "/tmp/gludd-stop-text-complete-count.json"

    def test_text_complete_count_increments_across_two_calls(self, hook_plugin_env: HookEnv):
        invoke_text_complete_and_confirm_increment(
            hook_plugin_env,
            "enforce-stop.ts",
            hook_plugin_env.env["GLUDD_STOP_TEXT_COMPLETE_COUNT"],
        )


class TestEnforceFloorTextComplete:
    """enforce-floor.ts declares experimental.text.complete as an intentional
    pass-through (commit 19701a336): enforcement stays self-contained in
    tool.execute.before, while the supported-hooks surface keeps the pinned
    plugin contract. The pass-through returns output unchanged when no hot
    module supplies a text.complete implementation."""

    def test_floor_text_complete_hook_is_passthrough(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            "enforce-floor.ts",
            "experimental.text.complete",
            input={},
            output={"text": "test response"},
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload.get("text") == "test response", f"expected pass-through, got {payload!r}"


# ── Test 9: enforce-stop.ts writes tool-counts file via tool.execute.before ─


class TestEnforceStopToolCounts:
    """enforce-stop.ts writes /tmp/gludd-stop-tool-counts.json (HARDCODED
    path) via tool.execute.before on every call, tracking per-outcome counts."""

    STATE_FILE = "/tmp/gludd-stop-tool-counts.json"

    def test_tool_counts_file_written_and_shape(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke("enforce-stop.ts", "tool.execute.before", input={"tool": "read"})
        assert result.returncode == 0, result.stderr
        data = json.loads(Path(hook_plugin_env.env["GLUDD_STOP_TOOL_COUNTS_FILE"]).read_text())
        assert isinstance(data, dict)
        assert data.get("allowed", 0) >= 1
        assert "last_allowed" in data

    def test_tool_counts_positive_total(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke("enforce-stop.ts", "tool.execute.before", input={"tool": "read"})
        data = json.loads(Path(hook_plugin_env.env["GLUDD_STOP_TOOL_COUNTS_FILE"]).read_text())
        numeric_counts = [v for v in data.values() if isinstance(v, (int, float))]
        assert numeric_counts and sum(numeric_counts) > 0


# ── Test 10: enforce-delegate.ts writes model-util file via tool.execute.before


class TestEnforceDelegateModelUtil:
    """enforce-delegate.ts writes GLUDD_MODEL_UTIL_STATE via
    tool.execute.before when a task/agent/workflow tool is dispatched with an
    explicit model. A model:"sonnet" dispatch is always recorded and allowed."""

    def test_model_util_file_written_on_sonnet_dispatch(self, hook_plugin_env: HookEnv):
        result = hook_plugin_env.invoke(
            "enforce-delegate.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"model": "sonnet", "description": "hook-liveness smoke"}},
        )
        assert result.returncode == 0, result.stderr
        data = hook_plugin_env.read_json("GLUDD_MODEL_UTIL_STATE")
        assert isinstance(data, dict)
        assert data["history"] == ["sonnet"], data

    def test_model_util_last_model_is_string(self, hook_plugin_env: HookEnv):
        hook_plugin_env.invoke(
            "enforce-delegate.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={"args": {"model": "sonnet", "description": "hook-liveness smoke"}},
        )
        data = hook_plugin_env.read_json("GLUDD_MODEL_UTIL_STATE")
        assert all(isinstance(m, str) for m in data["history"])


# ── Test 11: watchdog.ts writes PID file via event/session.created ──────────


class TestWatchdogPidFile:
    """watchdog.ts's `session.created` event handler spawns
    `python3 scripts/agent_watchdog.py` and writes its PID to the HARDCODED
    /tmp/gludd-watchdog.pid. SAFETY: the hook is invoked with cwd=the
    fixture's isolated tmp_path (no scripts/ dir there), so the spawned
    python3 process exits almost immediately with a "file not found" error
    instead of becoming a real long-running watchdog daemon. The real PID
    file is ALSO snapshotted/restored by hook_plugin_env, and — belt and
    braces — pre-seeded with an unreachable placeholder PID before the call
    so the plugin's "kill any existing watchdog" step can never touch a real
    process on this machine. session.deleted teardown is MANDATORY (fires
    below) so the test-spawned PID is always reaped."""

    PID_FILE = "/tmp/gludd-watchdog.pid"

    def test_watchdog_pid_file_written_and_valid(self, hook_plugin_env: HookEnv):
        # The plugin reads GLUDD_WATCHDOG_PID_FILE (env-redirected per-test to an
        # isolated tmp file by hook_plugin_env), falling back to the literal
        # /tmp/gludd-watchdog.pid only in prod. Read back the SAME redirected
        # path — the shared literal raced across xdist workers (CI 29123726253
        # unit-2 FileNotFoundError) when a sibling's _restore() unlinked it.
        pid_file = Path(hook_plugin_env.env["GLUDD_WATCHDOG_PID_FILE"])
        # Defense in depth: neutralize any real watchdog PID recorded on this
        # machine before the plugin's "kill existing watchdog" step runs.
        pid_file.write_text("2147483647")
        try:
            result = hook_plugin_env.invoke(
                ".opencode/plugins/watchdog.ts",
                "event",
                input={"event": {"type": "session.created"}},
            )
            assert result.returncode == 0, result.stderr
            content = pid_file.read_text().strip()
            assert content, "PID file must not be empty"
            assert content.isdigit(), f"PID file must contain digits, got {content!r}"
            pid = int(content)
            assert pid > 0
        finally:
            # MANDATORY teardown: fire session.deleted so the test-spawned
            # (already-exited) child is reaped and the PID file is removed.
            teardown_watchdog_session(hook_plugin_env)


# ── Test 12: enforce-deletion-gate.ts writes audit log via tool.execute.before


class TestDeletionGateAuditLog:
    """enforce-deletion-gate.ts's tool.execute.before appends to
    `.deletion-audit.log` (CWD-RELATIVE — resolves inside the fixture's
    isolated tmp cwd, no hardcoded-path guard needed) when a deletion exceeds
    GLUDD_DELETION_GATE_THRESHOLD AND DELETION_REASON is set."""

    def test_audit_log_written_with_reason_above_threshold(self, hook_plugin_env: HookEnv):
        old_string = "\n".join(f"line {i}" for i in range(20))  # 20 lines
        new_string = "line 0"  # 1 line -> 19 lines removed
        result = hook_plugin_env.invoke(
            "enforce-deletion-gate.ts",
            "tool.execute.before",
            input={
                "tool": "edit",
                "args": {
                    "file_path": "scratch.py",
                    "old_string": old_string,
                    "new_string": new_string,
                },
            },
            env_overrides={
                "GLUDD_DELETION_GATE_THRESHOLD": "5",
                "DELETION_REASON": "hook-liveness-test",
            },
        )
        assert result.returncode == 0, result.stderr

        audit_log = hook_plugin_env.cwd / ".deletion-audit.log"
        assert audit_log.exists(), "deletion audit log must be written"
        content = audit_log.read_text().strip()
        assert content
        assert "lines_removed=19" in content
        assert 'reason="hook-liveness-test"' in content

    def test_deletion_denied_without_reason(self, hook_plugin_env: HookEnv):
        old_string = "\n".join(f"line {i}" for i in range(20))
        new_string = "line 0"
        result = hook_plugin_env.invoke(
            "enforce-deletion-gate.ts",
            "tool.execute.before",
            input={
                "tool": "edit",
                "args": {
                    "file_path": "scratch.py",
                    "old_string": old_string,
                    "new_string": new_string,
                },
            },
            env_overrides={"GLUDD_DELETION_GATE_THRESHOLD": "5"},
        )
        payload = json.loads(result.stdout)
        assert payload.get("permissionDecision") == "deny", (
            f"expected deny for deletion above threshold without reason, got {payload}"
        )
        assert "threshold" in payload.get("message", "").lower()


# ── Test 13: opt-in live smoke test against a real opencode binary ──────────


@pytest.mark.hook_live
class TestOpencodeBinaryLive:
    """Opt-in (GLUDD_HOOK_LIVE=1 + OPENCODE_BIN) end-to-end smoke test against
    a REAL opencode CLI binary, distinct from the harness-based tests above
    (which invoke plugin hooks directly via node, never through opencode
    itself). Excluded from the default run by pyproject.toml's
    `-m "not hook_live"` addopts; run via `make test-hooks-live`."""

    def test_opencode_binary_runs(self):
        import os
        import shutil
        import subprocess

        if os.environ.get("GLUDD_HOOK_LIVE") != "1":
            pytest.skip("set GLUDD_HOOK_LIVE=1 to run the live opencode binary smoke test")
        opencode_bin = os.environ.get("OPENCODE_BIN")
        if not opencode_bin or not shutil.which(opencode_bin):
            pytest.skip("OPENCODE_BIN not set to a resolvable executable")

        result = subprocess.run([opencode_bin, "--help"], capture_output=True, text=True, timeout=30)
        # Some CLIs exit non-zero on --help while still printing usage; either
        # a clean exit OR non-empty output proves the binary actually ran.
        assert result.returncode == 0 or result.stdout.strip() or result.stderr.strip(), (
            "opencode binary produced no output and a non-zero exit"
        )
