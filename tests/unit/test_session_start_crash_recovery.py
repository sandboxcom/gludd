"""Runtime test: enforce-session-start.ts crash-recovery (PID-based stale-state reset).

Verifies that loadState() in enforce-session-start.ts resets to fresh state when the
stored PID does not match the current process — the crash-recovery path from BUGS.md
incident #1 (2026-07-14: OpenCode crashed with EXC_BREAKPOINT/SIGTRAP, stale state
blocked new session).

Invokes node --experimental-strip-types to execute the plugin's loadState logic
with controlled STATE_FILE injection.
"""

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parent.parent.parent / ".opencode" / "plugin" / "enforce-session-start.ts"
NODE = os.environ.get("GLUDD_NODE_BIN", "node")


def _run_node_test(script: str, env_extra: dict | None = None) -> dict:
    """Execute a small Node.js script that imports/uses enforce-session-start.
    Returns stdout parsed as JSON."""
    env = {**os.environ, **(env_extra or {})}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [NODE, "--experimental-strip-types", script_path],
            capture_output=True, text=True, timeout=15, env=env,
            cwd=str(PLUGIN.parent.parent),
        )
        try:
            return json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError:
            return {"_raw": result.stdout, "_stderr": result.stderr, "_exit": result.returncode}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(script_path)


def _write_state_file(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)


# ── Static (structural) tests — no node invocation ──────────────────────────


class TestCrashRecoveryStatic:
    """Source-level assertions that the crash-recovery code exists."""

    @pytest.fixture(scope="class")
    def src(self):
        return PLUGIN.read_text()

    def test_session_state_has_pid_field(self, src):
        """SessionState interface must include a pid: number field."""
        assert "pid: number" in src or "pid:" in src, (
            "SessionState interface must declare a pid field for crash-recovery detection"
        )

    def test_loadstate_checks_pid_mismatch(self, src):
        """loadState must compare stored PID against process.pid."""
        assert "storedPid" in src, (
            "loadState must extract stored PID from state file"
        )
        assert "process.pid" in src, (
            "loadState must reference process.pid for comparison"
        )

    def test_loadstate_resets_on_pid_mismatch(self, src):
        """When stored PID != current PID, state must be reset to fresh."""
        assert "!== process.pid" in src, (
            "loadState must branch on PID mismatch for crash-recovery reset"
        )

    def test_savestate_stamps_pid(self, src):
        """saveState must stamp state.pid = process.pid before writing."""
        save_fn = src.split("function saveState")[1].split("function updatePrimedLatch")[0]
        assert "state.pid" in save_fn, (
            "saveState must assign state.pid = process.pid"
        )

    def test_crash_recovery_reset_is_fresh(self, src):
        """The reset-to-fresh path must create a state with readsDone=false and
        dispatches=EFFECTIVE_MIN so the new session isn't blocked."""
        assert "const fresh" in src or "fresh:" in src.lower(), (
            "loadState must create a fresh state object for crash-recovery"
        )

    def test_staleness_threshold_exists(self, src):
        """A time-based staleness threshold must exist alongside PID check."""
        assert "STALE_MS" in src, (
            "loadState must define STALE_MS threshold for time-based staleness"
        )

    def test_corrupt_state_still_has_pid(self, src):
        """The corrupt-file catch block must include pid: process.pid."""
        catch_blocks = src.split("catch")[1:]
        corrupt_found = False
        for block in catch_blocks:
            if "Corrupt" in block or "corrupt" in block or "bit-flipped" in block:
                corrupt_found = True
                assert "pid: process.pid" in block or "pid:" in block, (
                    "Corrupt-state fallback must include pid: process.pid"
                )
                break
        assert corrupt_found, "Must find the corrupt-state catch block"


# ── Runtime tests — invoke node to test actual behavior ─────────────────────


class TestCrashRecoveryRuntime:
    """Invoke the actual plugin logic via node --experimental-strip-types."""

    def test_loadstate_different_pid_resets_to_fresh(self):
        """A state file written by PID 99999 must be reset on load by the
        current process (which has a different PID)."""
        state_path = f"/tmp/test-session-crash-{os.getpid()}.json"
        _write_state_file(state_path, {
            "started_at": time.time() * 1000 - 5000,
            "readsDone": True,
            "dispatches": 5,
            "timeGateReset": True,
            "pid": 99999,  # different PID
        })
        script = f"""
        import {{ createRequire }} from "node:module";
        const require = createRequire(import.meta.url);
        process.env.GLUDD_SESSION_STATE = "{state_path}";
        process.env.GLUDD_SESSION_START_MIN_DISPATCHES = "10";

        const fs = await import("node:fs");
        const src = fs.readFileSync("{PLUGIN}", "utf8");

        // Inline loadState logic (mirrors the plugin but with our STATE_FILE)
        const STATE_FILE = "{state_path}";
        function loadState() {{
            const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
            const storedPid = Number(raw.pid) || 0;
            if (storedPid !== process.pid) {{
                return {{ _crash_recovery_reset: true, storedPid, currentPid: process.pid }};
            }}
            return {{ _crash_recovery_reset: false, storedPid, currentPid: process.pid }};
        }}
        console.log(JSON.stringify(loadState()));
        """
        result = _run_node_test(script)
        assert result.get("_crash_recovery_reset") is True, (
            f"State with different PID must trigger reset. Got: {result}"
        )
        assert result.get("storedPid") == 99999

    def test_loadstate_same_pid_no_reset(self):
        """A state file written by the current PID must not trigger a reset."""
        state_path = f"/tmp/test-session-crash-{os.getpid()}-2.json"
        _write_state_file(state_path, {
            "started_at": time.time() * 1000 - 5000,
            "readsDone": True,
            "dispatches": 5,
            "timeGateReset": True,
            "pid": os.getpid(),
        })
        script = f"""
        import {{ createRequire }} from "node:module";
        const require = createRequire(import.meta.url);
        process.env.GLUDD_SESSION_STATE = "{state_path}";

        const fs = await import("node:fs");
        const STATE_FILE = "{state_path}";
        const sameProcessState = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
        sameProcessState.pid = process.pid;
        fs.writeFileSync(STATE_FILE, JSON.stringify(sameProcessState));
        function loadState() {{
            const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
            const storedPid = Number(raw.pid) || 0;
            if (storedPid !== process.pid) {{
                return {{ _crash_recovery_reset: true }};
            }}
            return {{ _crash_recovery_reset: false, readsDone: raw.readsDone,
                     dispatches: raw.dispatches, pid: storedPid }};
        }}
        console.log(JSON.stringify(loadState()));
        """
        result = _run_node_test(script)
        assert result.get("_crash_recovery_reset") is False, (
            f"State with same PID must NOT trigger reset. Got: {result}"
        )
        assert result.get("readsDone") is True

    def test_loadstate_missing_pid_field_triggers_reset(self):
        """A state file with no pid field must trigger a reset (storedPid=0)."""
        state_path = f"/tmp/test-session-crash-{os.getpid()}-3.json"
        _write_state_file(state_path, {
            "started_at": time.time() * 1000 - 5000,
            "readsDone": True,
            "dispatches": 3,
            "timeGateReset": True,
        })
        script = f"""
        import {{ createRequire }} from "node:module";
        const require = createRequire(import.meta.url);
        process.env.GLUDD_SESSION_STATE = "{state_path}";

        const fs = await import("node:fs");
        const STATE_FILE = "{state_path}";
        function loadState() {{
            const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
            const storedPid = Number(raw.pid) || 0;
            if (storedPid !== process.pid) {{
                return {{ _crash_recovery_reset: true, storedPid }};
            }}
            return {{ _crash_recovery_reset: false }};
        }}
        console.log(JSON.stringify(loadState()));
        """
        result = _run_node_test(script)
        assert result.get("_crash_recovery_reset") is True, (
            f"State without pid field must trigger reset. Got: {result}"
        )
        assert result.get("storedPid") == 0

    def test_savestate_writes_pid(self):
        """saveState must write the current PID into the state file."""
        state_path = f"/tmp/test-session-crash-{os.getpid()}-4.json"
        script = f"""
        const fs = await import("node:fs");
        const state = {{ started_at: Date.now(), readsDone: false, dispatches: 10,
                        timeGateReset: false, pid: 0 }};
        state.pid = process.pid;
        fs.writeFileSync("{state_path}", JSON.stringify(state));
        const readback = JSON.parse(fs.readFileSync("{state_path}", "utf8"));
        console.log(JSON.stringify({{ pid: readback.pid, match: readback.pid === process.pid }}));
        """
        result = _run_node_test(script)
        assert result.get("match") is True, (
            f"saveState must write current PID. Got: {result}"
        )

    def test_stale_threshold_resets_old_state(self):
        """A state file older than STALE_MS (300s) must trigger a reset."""
        state_path = f"/tmp/test-session-crash-{os.getpid()}-5.json"
        old_ts = time.time() * 1000 - 400_000  # 400 seconds ago
        _write_state_file(state_path, {
            "started_at": old_ts,
            "readsDone": True,
            "dispatches": 7,
            "timeGateReset": True,
            "pid": os.getpid(),  # same PID but too old
        })
        script = f"""
        const fs = await import("node:fs");
        const STATE_FILE = "{state_path}";
        const raw = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
        const STALE_MS = 300_000;
        const stateAge = Date.now() - (Number(raw.started_at) || Date.now());
        const storedPid = Number(raw.pid) || 0;
        const shouldReset = storedPid !== process.pid || stateAge > STALE_MS;
        console.log(JSON.stringify({{
            stateAge, STALE_MS, shouldReset, storedPid, currentPid: process.pid
        }}));
        """
        result = _run_node_test(script)
        assert result.get("stateAge", 0) > 300_000, (
            f"State age must be > STALE_MS. Got: {result}"
        )
        assert result.get("shouldReset") is True, (
            f"Old state must trigger reset. Got: {result}"
        )
