"""BP.13 + BP.16: PID-scoped streak isolation + force-dispatch signal cleanup.

BP.13 — Streak counter PID-scoped isolation:
    /tmp/gludd-mainthread-streak.json includes a ``pid`` field. On readStreak(),
    if stored pid != process.pid, count resets to 0 (prevents cross-session
    contamination when opencode restarts without crash-recovery).

BP.16 — Force-dispatch signal cleanup:
    /tmp/gludd-force-dispatch.json is consumed (read + deleted) by
    consumeForceDispatchSignal() at the top of mainthreadBudgetBefore(),
    preventing stale dispatch commands from being re-injected by the watchdog
    on its next poll cycle.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-delegate.ts"


def _src() -> str:
    return PLUGIN_PATH.read_text()


# ============================================================================
# BP.13 — Structural tests: PID field in streak state
# ============================================================================


class TestStreakPidStructural:
    """The MainthreadStreakState interface and read/write functions use pid."""

    def test_interface_has_pid_field(self):
        src = _src()
        assert "interface MainthreadStreakState" in src
        assert "pid: number" in src, (
            "MainthreadStreakState must include 'pid: number' for PID isolation"
        )

    def test_read_streak_checks_pid_mismatch(self):
        src = _src()
        assert "storedPid !== process.pid" in src, (
            "readStreak must check storedPid against process.pid"
        )
        assert "storedPid !== 0" in src, (
            "must skip PID check when storedPid is 0 (legacy data)"
        )

    def test_pid_mismatch_resets_count_to_zero(self):
        src = _src()
        idx = src.index("if (storedPid !== 0 && storedPid !== process.pid)")
        snippet = src[idx:idx + 120]
        assert "count: 0" in snippet, (
            f"count must reset to 0 on PID mismatch. Snippet: {snippet}"
        )

    def test_write_streak_stores_current_pid(self):
        src = _src()
        write_idx = src.index("function writeStreak")
        write_fn = src[write_idx:write_idx + 300]
        assert "pid: process.pid" in write_fn, (
            "writeStreak must persist process.pid in the merged state"
        )

    def test_default_state_uses_current_pid(self):
        src = _src()
        catch_idx = src.rfind("return { count: 0, ts: 0, pid: process.pid }")
        assert catch_idx > 0, (
            "catch/parse-failure path must return pid: process.pid"
        )


# ============================================================================
# BP.13 — Behavioral tests: PID isolation via Node runtime
# ============================================================================


_READ_STREAK_IMPL = """
function readStreak() {
  try {
    const raw = fs.readFileSync(FILE, "utf8").trim();
    if (raw.startsWith("{")) {
      const obj = JSON.parse(raw);
      const storedPid = parseInt(obj.pid, 10) || 0;
      const count = parseInt(obj.count, 10) || 0;
      const ts = parseInt(obj.ts, 10) || 0;
      if (storedPid !== 0 && storedPid !== process.pid) {
        return { count: 0, ts: 0, pid: process.pid };
      }
      return { count, ts, pid: storedPid || process.pid };
    }
    const n = parseInt(raw, 10);
    return { count: Number.isNaN(n) ? 0 : n, ts: 0, pid: process.pid };
  } catch {
    return { count: 0, ts: 0, pid: process.pid };
  }
}
function writeStreak(partial) {
  const current = readStreak();
  const merged = { ...current, ...partial, ts: Date.now(), pid: process.pid };
  const tmp = FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(merged));
  fs.renameSync(tmp, FILE);
}
"""


class TestStreakPidBehavioral:
    """Exercise readStreak/writeStreak through Node to verify PID isolation."""

    def test_state_file_includes_pid_after_write(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write('{"count": 0, "ts": 0, "pid": 0}')
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{streak_path}";
        {_READ_STREAK_IMPL}
        writeStreak({{ count: 5 }});
        const data = JSON.parse(fs.readFileSync(FILE, "utf8"));
        console.log(JSON.stringify({{ result: data, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, f"Node failed: {result.stderr}"
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            node_pid = payload["nodePid"]
            assert "pid" in data, f"State must include pid. Got: {data}"
            assert data["pid"] == node_pid
            assert data["count"] == 5
        finally:
            os.unlink(streak_path)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(streak_path + ".tmp")

    def test_pid_mismatch_resets_count(self):
        """A streak file from a different PID resets count to 0."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(json.dumps({"count": 10, "ts": 1000, "pid": 99999}))
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{streak_path}";
        {_READ_STREAK_IMPL}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, f"Node failed: {result.stderr}"
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            assert data["count"] == 0, (
                f"Mismatched PID must reset count to 0. Got: {data['count']}"
            )
            assert data["pid"] == payload["nodePid"]
        finally:
            os.unlink(streak_path)

    def test_same_pid_preserves_count(self):
        """A streak file from the same PID preserves the count."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{}")
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{streak_path}";
        fs.writeFileSync(FILE, JSON.stringify({{ count: 7, ts: 2000, pid: process.pid }}));
        {_READ_STREAK_IMPL}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, f"Node failed: {result.stderr}"
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            assert data["count"] == 7, (
                f"Same PID must preserve count. Got: {data['count']}"
            )
        finally:
            os.unlink(streak_path)

    def test_legacy_no_pid_field_preserves_count(self):
        """Legacy state without pid field (parsed as 0) skips the PID check."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(json.dumps({"count": 3, "ts": 5000}))
            streak_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{streak_path}";
        {_READ_STREAK_IMPL}
        const result = readStreak();
        console.log(JSON.stringify({{ result, nodePid: process.pid }}));
        """
        try:
            result = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 0, f"Node failed: {result.stderr}"
            payload = json.loads(result.stdout.strip())
            data = payload["result"]
            assert data["count"] == 3, (
                f"Legacy data (pid=0) must preserve count. Got: {data['count']}"
            )
        finally:
            os.unlink(streak_path)


# ============================================================================
# BP.16 — Structural tests: consumeForceDispatchSignal
# ============================================================================


class TestConsumeForceDispatchStructural:
    """consumeForceDispatchSignal exists, reads, and deletes the file."""

    def test_function_exists(self):
        src = _src()
        assert "function consumeForceDispatchSignal" in src, (
            "consumeForceDispatchSignal function must exist"
        )

    def test_delete_helper_calls_unlink_sync(self):
        src = _src()
        idx = src.index("function deleteForceDispatchSignal")
        fn_body = src[idx:idx + 250]
        assert "fs.unlinkSync(FORCE_DISPATCH_FILE)" in fn_body, (
            "deleteForceDispatchSignal must call fs.unlinkSync to delete the file"
        )

    def test_unlink_is_try_caught(self):
        src = _src()
        idx = src.index("function deleteForceDispatchSignal")
        fn_body = src[idx:idx + 250]
        unlink_idx = fn_body.index("unlinkSync")
        before_unlink = fn_body[:unlink_idx]
        assert "try" in before_unlink or "catch" in fn_body[unlink_idx:], (
            "unlinkSync must be wrapped in try/catch for fail-open"
        )

    def test_called_in_mainthread_budget_before(self):
        src = _src()
        before_idx = src.index("function mainthreadBudgetBefore")
        before_fn = src[before_idx:before_idx + 600]
        assert "consumeForceDispatchSignal()" in before_fn, (
            "mainthreadBudgetBefore must call consumeForceDispatchSignal()"
        )

    def test_called_before_git_shipping_check(self):
        """Consume must happen before any early-return checks."""
        src = _src()
        before_idx = src.index("function mainthreadBudgetBefore")
        before_fn = src[before_idx:before_idx + 600]
        consume_idx = before_fn.index("consumeForceDispatchSignal()")
        git_idx = before_fn.find("isGitShippingTarget")
        assert git_idx == -1 or consume_idx < git_idx, (
            "consumeForceDispatchSignal must be called before git-shipping check"
        )

    def test_reads_file_before_deleting(self):
        src = _src()
        idx = src.index("function consumeForceDispatchSignal")
        fn_body = src[idx:idx + 500]
        read_idx = fn_body.find("readFileSync")
        delete_idx = fn_body.find("deleteForceDispatchSignal")
        assert read_idx > 0 and delete_idx > 0, (
            "Must both read and delete the file"
        )
        assert read_idx < delete_idx, (
            "Must read the file BEFORE deleting it"
        )

    def test_malformed_signal_is_deleted(self):
        src = _src()
        idx = src.index("function consumeForceDispatchSignal")
        fn_body = src[idx:idx + 500]
        catch_idx = fn_body.index("catch")
        assert "deleteForceDispatchSignal()" in fn_body[catch_idx:], (
            "Malformed force-dispatch signals must be deleted in the catch path"
        )

    def test_returns_null_when_file_absent(self):
        src = _src()
        idx = src.index("function consumeForceDispatchSignal")
        fn_body = src[idx:idx + 500]
        assert "existsSync" in fn_body, (
            "Must check existsSync to handle absent file"
        )
        assert "return null" in fn_body, (
            "Must return null when file doesn't exist"
        )


# ============================================================================
# BP.16 — Behavioral tests: file deleted after consume
# ============================================================================


_CONSUME_IMPL = """
function consumeForceDispatchSignal() {
  try {
    if (!fs.existsSync(FILE)) return null;
    const data = JSON.parse(fs.readFileSync(FILE, "utf8"));
    try { fs.unlinkSync(FILE); } catch {}
    return Array.isArray(data.dispatch_commands) ? data.dispatch_commands : null;
  } catch {
    try { fs.unlinkSync(FILE); } catch {}
    return null;
  }
}
"""


class TestConsumeForceDispatchBehavioral:
    """Exercise consumeForceDispatchSignal through Node."""

    def test_file_deleted_after_consume(self):
        """The force-dispatch file is unlinked after consumeForceDispatchSignal reads it."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(json.dumps({
                "level": 3,
                "dispatch_count": 2,
                "dispatch_commands": [
                    {"index": 1, "task_item": "fix bug", "tool": "task",
                     "command": "dispatch subagent: fix bug"},
                ],
                "reason": "mainthread_streak_block",
                "ts": 1234567890,
            }))
            force_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{force_path}";
        {_CONSUME_IMPL}
        const result = consumeForceDispatchSignal();
        const exists = fs.existsSync(FILE);
        console.log(JSON.stringify({{ result, exists }}));
        """
        try:
            r = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert r.returncode == 0, f"Node failed: {r.stderr}"
            payload = json.loads(r.stdout.strip())
            assert payload["exists"] is False, (
                "force-dispatch file must NOT exist after consume"
            )
            assert payload["result"] is not None, (
                "consume must return the parsed dispatch_commands"
            )
            assert len(payload["result"]) == 1
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(force_path)

    def test_missing_file_handled_gracefully(self):
        """consumeForceDispatchSignal on absent file returns null without crashing."""
        absent_path = os.path.join(
            tempfile.gettempdir(), f"gludd-nonexistent-force-{os.getpid()}.json"
        )
        with contextlib.suppress(FileNotFoundError):
            os.unlink(absent_path)

        script = f"""
        const fs = require("node:fs");
        const FILE = "{absent_path}";
        {_CONSUME_IMPL}
        const result = consumeForceDispatchSignal();
        console.log(JSON.stringify({{ result, crashed: false }}));
        """
        r = subprocess.run(
            ["node", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, f"Node failed: {r.stderr}"
        payload = json.loads(r.stdout.strip())
        assert payload["crashed"] is False
        assert payload["result"] is None, (
            "Absent file must return null, not throw"
        )

    def test_corrupt_json_still_deletes_file(self):
        """A corrupt force-dispatch file is still cleaned up (no stale re-injection)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("NOT VALID JSON {{{")
            corrupt_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{corrupt_path}";
        {_CONSUME_IMPL}
        const result = consumeForceDispatchSignal();
        const exists = fs.existsSync(FILE);
        console.log(JSON.stringify({{ result, exists }}));
        """
        try:
            r = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert r.returncode == 0, f"Node failed: {r.stderr}"
            payload = json.loads(r.stdout.strip())
            assert payload["exists"] is False, (
                "Corrupt file must still be deleted"
            )
            assert payload["result"] is None, (
                "Corrupt file must return null"
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(corrupt_path)

    def test_consume_returns_dispatch_commands(self):
        """consumeForceDispatchSignal returns the dispatch_commands array."""
        cmds = [
            {"index": 1, "task_item": "task A", "tool": "task",
             "command": "dispatch: task A"},
            {"index": 2, "task_item": "task B", "tool": "task",
             "command": "dispatch: task B"},
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(json.dumps({
                "dispatch_commands": cmds,
                "ts": 1234567890,
            }))
            force_path = f.name

        script = f"""
        const fs = require("node:fs");
        const FILE = "{force_path}";
        {_CONSUME_IMPL}
        const result = consumeForceDispatchSignal();
        console.log(JSON.stringify({{ result }}));
        """
        try:
            r = subprocess.run(
                ["node", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            assert r.returncode == 0, f"Node failed: {r.stderr}"
            payload = json.loads(r.stdout.strip())
            assert payload["result"] is not None
            assert len(payload["result"]) == 2
            assert payload["result"][0]["task_item"] == "task A"
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(force_path)


# ============================================================================
# Integration: consume happens before write in mainthreadBudgetBefore
# ============================================================================


class TestConsumeBeforeWrite:
    """consumeForceDispatchSignal is called BEFORE writeForceDispatchSignal,
    ensuring a stale signal from cycle N-1 is cleaned before cycle N writes
    a fresh one.
    """

    def test_consume_before_write_in_source(self):
        src = _src()
        before_idx = src.index("function mainthreadBudgetBefore")
        before_fn = src[before_idx:before_idx + 800]
        consume_pos = before_fn.find("consumeForceDispatchSignal()")
        write_pos = before_fn.find("writeForceDispatchSignal")
        # writeForceDispatchSignal is called deeper in the function body
        # (only when streak >= threshold). consume must come first.
        if write_pos > 0:
            assert consume_pos < write_pos, (
                "consume must precede write to clean stale signals first"
            )
        else:
            # writeForceDispatchSignal may not be in the first 800 chars
            # but consume must be present
            assert consume_pos > 0

    def test_consume_at_top_of_before_function(self):
        """consume is called right after enabled/disengaged checks, before
        the git-shipping / lint / read-grind early returns."""
        src = _src()
        before_idx = src.index("function mainthreadBudgetBefore")
        before_fn = src[before_idx:before_idx + 600]
        consume_pos = before_fn.find("consumeForceDispatchSignal()")
        disengage_pos = before_fn.find("isDisengaged()")
        assert consume_pos > 0, "consumeForceDispatchSignal must be called"
        assert disengage_pos > 0, "isDisengaged check must exist"
        assert consume_pos > disengage_pos, (
            "consume must come after the disengage check"
        )
        git_pos = before_fn.find("isGitShippingTarget")
        assert git_pos == -1 or consume_pos < git_pos, (
            "consume must come before git-shipping check"
        )
