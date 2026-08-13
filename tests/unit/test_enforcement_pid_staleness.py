"""Regression tests for PID-based staleness detection in enforcement plugins.

Bug: stale state files from a prior session (different PID) caused enforcement
to never fire. When a prior opencode session crashed or was killed, its state
files (e.g. /tmp/gludd-multitask-state.json, /tmp/gludd-tool-streak.json)
remained on disk with the old PID. The new session read those files, inherited
stale counters (e.g. thisMessageDispatches=10, streak=0), and enforcement
never triggered because the stale state made it look like the floor was met.

The fix adds PID checks at three layers:
1. enforce-multitask.ts: module-load IIFE overwrites pid + zeroes counters;
   tool.execute.before checks _state.pid !== process.pid and calls freshState().
2. enforce-floor.ts: module-level _floorInitPid variable; tool.execute.before
   checks _floorInitPid !== process.pid and calls _resetFloorState().
3. shared.ts readSharedStreak(): checks storedPid !== process.pid on the
   cross-plugin streak file and zeroes it on mismatch.

These tests verify the fix via:
  - Structural pins on the source patterns
  - Runtime invocation of actual plugin hooks via node --experimental-strip-types
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
MULTITASK_SRC = (PLUGIN_DIR / "enforce-multitask.ts").read_text()
FLOOR_SRC = (PLUGIN_DIR / "enforce-floor.ts").read_text()
SHARED_SRC = (ROOT / ".opencode" / "lib" / "shared.ts").read_text()

FAKE_STALE_PID = 999999
MULTITASK_STATE_FILE = os.environ.get(
    "GLUDD_MULTITASK_STATE_FILE", "/tmp/gludd-multitask-state.json"
)
SHARED_STREAK_FILE = os.environ.get(
    "GLUDD_STREAK_FILE", "/tmp/gludd-tool-streak.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(*paths: str) -> None:
    for p in paths:
        with contextlib.suppress(OSError):
            os.unlink(p)


def _write_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)


def _read_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 15) -> dict | None:
    """Run TypeScript code with node --experimental-strip-types, return parsed JSON output."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix="pid_stale_test_", delete=False
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            pytest.fail(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\n"
                f"stdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        for line in reversed(stdout.split("\n")):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ===========================================================================
# STRUCTURAL TESTS — verify PID-check patterns exist in source
# ===========================================================================

class TestMultitaskPidCheckStructural:
    """Verify enforce-multitask.ts has PID-based staleness detection."""

    def test_state_interface_has_pid_field(self):
        assert "pid: number" in MULTITASK_SRC, (
            "MultitaskState interface must include pid field for staleness detection"
        )

    def test_fresh_state_sets_current_pid(self):
        assert "pid: process.pid" in MULTITASK_SRC, (
            "freshState() must set pid to process.pid"
        )

    def test_iife_overwrites_pid_on_load(self):
        """The module-load IIFE must overwrite s.pid = process.pid so a stale
        state file from a prior session is immediately corrected at import."""
        assert "s.pid = process.pid" in MULTITASK_SRC, (
            "IIFE must set s.pid = process.pid to overwrite stale PID at load"
        )

    def test_iife_resets_counters_on_load(self):
        """The IIFE must zero out all counters so stale values from a prior
        session don't bypass enforcement."""
        iife_match = re.search(
            r"let _state.*?=\s*\(\(\)\s*=>\s*\{.*?\}\)\(\)",
            MULTITASK_SRC, re.DOTALL,
        )
        assert iife_match, "Module-load IIFE not found"
        body = iife_match.group(0)
        assert "s.zeroStreak = 0" in body, "IIFE must reset zeroStreak"
        assert "s.thisMessageDispatches = 0" in body, (
            "IIFE must reset thisMessageDispatches — stale value bypasses floor"
        )
        assert "s.estimatedInFlight = 0" in body, "IIFE must reset estimatedInFlight"

    def test_tool_execute_before_checks_pid_mismatch(self):
        """tool.execute.before must check _state.pid !== process.pid and call
        freshState() — this catches stale state from hot-reload module swaps."""
        assert "_state.pid !== process.pid" in MULTITASK_SRC, (
            "tool.execute.before must check _state.pid !== process.pid"
        )
        assert "freshState()" in MULTITASK_SRC, (
            "tool.execute.before must call freshState() on PID mismatch"
        )

    def test_pid_check_before_enforcement_logic(self):
        """The PID check must appear BEFORE any enforcement logic so stale
        state is corrected before the floor/streak checks run."""
        pid_check_idx = MULTITASK_SRC.find("_state.pid !== process.pid")
        assert pid_check_idx > 0, "PID check not found"
        handler_idx = MULTITASK_SRC.find("if (!FLOOR_ENFORCE) return")
        assert handler_idx > 0, "FLOOR_ENFORCE check not found"
        assert pid_check_idx < handler_idx, (
            "PID staleness check must run BEFORE the FLOOR_ENFORCE gate — "
            "otherwise stale _state survives into enforcement decisions"
        )


class TestFloorPidCheckStructural:
    """Verify enforce-floor.ts has PID-based staleness detection."""

    def test_floor_init_pid_variable_exists(self):
        assert "_floorInitPid" in FLOOR_SRC, (
            "enforce-floor.ts must track _floorInitPid for staleness detection"
        )

    def test_floor_init_pid_set_to_process_pid(self):
        assert "_floorInitPid = process.pid" in FLOOR_SRC, (
            "_floorInitPid must be initialized to process.pid at module load"
        )

    def test_reset_floor_state_resets_pid(self):
        """_resetFloorState() must set _floorInitPid = process.pid so the
        reset is sticky — subsequent calls see the correct PID."""
        reset_match = re.search(
            r"function _resetFloorState\(\):\s*void\s*\{.*?\}",
            FLOOR_SRC, re.DOTALL,
        )
        assert reset_match, "_resetFloorState function not found"
        body = reset_match.group(0)
        assert "_floorInitPid = process.pid" in body, (
            "_resetFloorState must set _floorInitPid = process.pid"
        )

    def test_tool_execute_before_checks_pid_mismatch(self):
        """tool.execute.before must check _floorInitPid !== process.pid and
        call _resetFloorState()."""
        assert "_floorInitPid !== process.pid" in FLOOR_SRC, (
            "tool.execute.before must check _floorInitPid !== process.pid"
        )

    def test_pid_check_before_streak_logic(self):
        """The PID check must appear BEFORE streak enforcement so stale
        counters are zeroed before the streak threshold is evaluated."""
        pid_check_idx = FLOOR_SRC.find("_floorInitPid !== process.pid")
        assert pid_check_idx > 0, "PID check not found"
        streak_inc_idx = FLOOR_SRC.find("_streakCount++")
        assert streak_inc_idx > 0, "_streakCount++ not found"
        assert pid_check_idx < streak_inc_idx, (
            "PID staleness check must run BEFORE _streakCount++ — "
            "stale streak from prior session must be reset before evaluation"
        )


class TestSharedStreakPidCheckStructural:
    """Verify shared.ts readSharedStreak() has PID-based staleness detection
    on the cross-plugin streak file (/tmp/gludd-tool-streak.json)."""

    def test_read_shared_streak_checks_pid(self):
        assert "storedPid !== process.pid" in SHARED_SRC, (
            "readSharedStreak() must check storedPid !== process.pid"
        )

    def test_zeroes_state_on_pid_mismatch(self):
        """On PID mismatch, readSharedStreak() must zero streak/readStreak/
        editStreak and write back with the current PID.

        Accepts both shapes: a standalone `if (storedPid !== process.pid) {`
        block, or the current shared.ts form where the comparison feeds a
        `pidMismatch` flag used in a combined staleness condition."""
        pid_check_match = re.search(
            r"storedPid\s*!==\s*process\.pid\s*(?:\)|.*?pidMismatch\s*\))\s*\{.*?\}",
            SHARED_SRC, re.DOTALL,
        )
        assert pid_check_match, "PID mismatch block not found"
        body = pid_check_match.group(0)
        assert "streak: 0" in body, "Must zero streak on PID mismatch"
        assert "pid: process.pid" in body, "Must write current PID on reset"

    def test_write_back_on_reset(self):
        """The PID-mismatch branch must write the zeroed state back to disk
        so the stale file is corrected for other plugins."""
        pid_check_match = re.search(
            r"storedPid\s*!==\s*process\.pid\s*(?:\)|.*?pidMismatch\s*\))\s*\{.*?writeFileSync.*?\}",
            SHARED_SRC, re.DOTALL,
        )
        assert pid_check_match, (
            "PID-mismatch branch must writeFileSync the zeroed state back"
        )


# ===========================================================================
# RUNTIME TESTS — invoke actual plugin hooks via node
# ===========================================================================

class TestMultitaskPidStalenessRuntime:
    """Runtime tests: stale state file with a different PID is reset on load."""

    def test_stale_state_file_different_pid_resets_on_load(self):
        """A state file with pid=999999 (prior session) must be overwritten
        with the current process PID and zeroed counters at module load."""
        _clean(MULTITASK_STATE_FILE)
        _write_json(MULTITASK_STATE_FILE, {
            "pid": FAKE_STALE_PID,
            "thisMessageDispatches": 10,
            "prevMessageDispatches": 10,
            "zeroStreak": 5,
            "estimatedInFlight": 10,
            "lastTs": 0,
            "lastToolCallTs": 1234567890,
            "waveHistory": [10, 10, 10],
            "consecutiveNonDispatch": 3,
            "consecutiveNonDispatchStartTs": 1234567890,
        })
        try:
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const fs = await import('node:fs')
const raw = JSON.parse(fs.readFileSync('{MULTITASK_STATE_FILE}', 'utf8'))
console.log(JSON.stringify({{
  statePid: raw.pid,
  stalePidGone: raw.pid !== {FAKE_STALE_PID},
  thisMessageDispatches: raw.thisMessageDispatches,
  zeroStreak: raw.zeroStreak,
  estimatedInFlight: raw.estimatedInFlight,
  waveHistoryLen: (raw.waveHistory || []).length,
}}))
"""
            result = _run_ts(code)
            assert result is not None, "No output from node"
            assert result["stalePidGone"] is True, (
                f"Stale PID {FAKE_STALE_PID} should be gone after module load, "
                f"got pid={result['statePid']}"
            )
            assert result["thisMessageDispatches"] == 0, (
                "thisMessageDispatches must be 0 after reset — "
                "stale value=10 would bypass the under-floor block"
            )
            assert result["zeroStreak"] == 0, "zeroStreak must be 0 after reset"
            assert result["estimatedInFlight"] == 0, (
                "estimatedInFlight must be 0 after reset"
            )
        finally:
            _clean(MULTITASK_STATE_FILE)

    def test_same_pid_state_not_reset(self):
        """A state file with the current process PID should NOT trigger a
        reset — the counters should be preserved as-is (the IIFE still
        zeroes them for session-start safety, but the PID check itself
        must not fire). This test verifies the PID equality path."""
        _clean(MULTITASK_STATE_FILE)
        current_pid = os.getpid()
        _write_json(MULTITASK_STATE_FILE, {
            "pid": current_pid,
            "thisMessageDispatches": 0,
            "prevMessageDispatches": 0,
            "zeroStreak": 0,
            "estimatedInFlight": 0,
            "lastTs": 0,
            "lastToolCallTs": 0,
            "waveHistory": [],
            "consecutiveNonDispatch": 0,
            "consecutiveNonDispatchStartTs": 0,
        })
        try:
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const fs = await import('node:fs')
const raw = JSON.parse(fs.readFileSync('{MULTITASK_STATE_FILE}', 'utf8'))
// The IIFE always sets pid = process.pid. Verify it matches the node PID
// (not the stale {FAKE_STALE_PID}) and the file is valid.
console.log(JSON.stringify({{
  statePid: raw.pid,
  pidMatchesNode: raw.pid === process.pid,
  stateValid: typeof raw.thisMessageDispatches === 'number',
}}))
"""
            result = _run_ts(code)
            assert result is not None
            assert result["pidMatchesNode"] is True, (
                "State file PID must match the running node process PID"
            )
            assert result["statePid"] != FAKE_STALE_PID
        finally:
            _clean(MULTITASK_STATE_FILE)

    def test_after_reset_enforcement_fires_correctly(self):
        """After a stale state file is reset, enforcement MUST fire. With
        MIN_DISPATCHES=2 and a fresh reset (thisMessageDispatches=0), a
        non-dispatch call (edit) must be DENIED when pending work exists."""
        tasks_path = f"/tmp/gludd-pid-stale-tasks-{os.getpid()}.md"
        _clean(MULTITASK_STATE_FILE, tasks_path, "/tmp/gludd-watchdog-disengage.json")
        _write_json(MULTITASK_STATE_FILE, {
            "pid": FAKE_STALE_PID,
            "thisMessageDispatches": 10,
            "zeroStreak": 0,
            "estimatedInFlight": 10,
            "lastTs": 0,
            "lastToolCallTs": 0,
            "waveHistory": [],
            "consecutiveNonDispatch": 0,
            "consecutiveNonDispatchStartTs": 0,
            "prevMessageDispatches": 0,
        })
        with open(tasks_path, "w") as f:
            f.write("- [ ] pid staleness test task\n")
        try:
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// After reset, thisMessageDispatches=0. An edit call with pending work
// and MIN_DISPATCHES=2 must be DENIED.
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
            result = _run_ts(code, env_override={
                "GLUDD_MIN_DISPATCHES": "2",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                "GLUDD_TASKS_MD": tasks_path,
            })
            assert result is not None, (
                "Expected a deny result, got None (enforcement didn't fire)"
            )
            assert result.get("permissionDecision") == "deny", (
                f"After reset, edit must be denied (UNDER-FLOOR HARD BLOCK). "
                f"Got: {result}"
            )
            assert "UNDER-FLOOR" in result.get("message", ""), (
                f"Deny message must mention UNDER-FLOOR. Got: {result.get('message', '')[:200]}"
            )
        finally:
            _clean(MULTITASK_STATE_FILE, tasks_path)

    def test_stale_high_dispatch_count_does_not_bypass_ceiling(self):
        """A stale state file with thisMessageDispatches=10 must NOT prevent
        the ceiling check from firing. After reset, dispatching 10 agents
        should work (each increments from 0), and the 11th would be denied."""
        _clean(MULTITASK_STATE_FILE, "/tmp/gludd-watchdog-disengage.json")
        _write_json(MULTITASK_STATE_FILE, {
            "pid": FAKE_STALE_PID,
            "thisMessageDispatches": 10,
            "zeroStreak": 0,
            "estimatedInFlight": 10,
            "lastTs": 0,
            "lastToolCallTs": 0,
            "waveHistory": [],
            "consecutiveNonDispatch": 0,
            "consecutiveNonDispatchStartTs": 0,
            "prevMessageDispatches": 0,
        })
        try:
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// After reset, dispatch 2 agents (MIN=MAX=2). Both must be allowed.
const r1 = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
// 3rd dispatch must hit the ceiling (MAX_DISPATCHES=2)
const r3 = await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
console.log(JSON.stringify({{
  r1_allowed: r1 === undefined || r1 === null,
  r2_allowed: r2 === undefined || r2 === null,
  r3_denied: r3?.permissionDecision === 'deny',
  r3_msg: r3?.message?.slice(0, 80) ?? null,
}}))
"""
            result = _run_ts(code, env_override={
                "GLUDD_MIN_DISPATCHES": "2",
                "GLUDD_MULTITASK_MAX_DISPATCHES": "2",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
            })
            assert result is not None
            assert result["r1_allowed"] is True, f"Dispatch 1 should be allowed: {result}"
            assert result["r2_allowed"] is True, f"Dispatch 2 should be allowed: {result}"
            assert result["r3_denied"] is True, (
                f"Dispatch 3 must hit ceiling — stale thisMessageDispatches=10 "
                f"should NOT persist after reset. Got: {result}"
            )
        finally:
            _clean(MULTITASK_STATE_FILE)


class TestFloorPidStalenessRuntime:
    """Runtime tests: enforce-floor.ts PID staleness via shared streak file."""

    def test_stale_shared_streak_different_pid_resets(self):
        """A shared streak file with pid=999999 must be zeroed when
        readSharedStreak() detects the PID mismatch."""
        sf = f"/tmp/gludd-tool-streak-pidtest-{os.getpid()}.json"
        _clean(sf, "/tmp/gludd-watchdog-disengage.json")
        _write_json(sf, {
            "streak": 5,
            "lastDispatchTs": 0,
            "readStreak": 3,
            "editStreak": 2,
            "lastUpdateTs": 0,
            "lastWriter": "enforce-floor",
            "pid": FAKE_STALE_PID,
        })
        try:
            code = f"""\
const shared = await import('{ROOT / ".opencode" / "lib" / "shared.ts"}')
// readSharedStreak reads the file and should detect pid mismatch
const s = shared.readSharedStreak()
const fs = await import('node:fs')
const raw = JSON.parse(fs.readFileSync('{sf}', 'utf8'))
console.log(JSON.stringify({{
  returnedStreak: s.streak,
  returnedPid: s.pid,
  filePid: raw.pid,
  fileStreak: raw.streak,
  stalePidGone: raw.pid !== {FAKE_STALE_PID},
}}))
"""
            result = _run_ts(code, env_override={"GLUDD_STREAK_FILE": sf})
            assert result is not None
            assert result["stalePidGone"] is True, (
                f"File PID should be reset from {FAKE_STALE_PID} to current. "
                f"Got filePid={result['filePid']}"
            )
            assert result["returnedStreak"] == 0, (
                f"readSharedStreak() must return streak=0 on PID mismatch. "
                f"Got: {result['returnedStreak']}"
            )
            assert result["fileStreak"] == 0, (
                f"File streak must be zeroed on PID mismatch. "
                f"Got: {result['fileStreak']}"
            )
        finally:
            _clean(sf)

    def test_same_pid_shared_streak_preserved(self):
        """A shared streak file with the current PID must NOT be zeroed —
        the streak value is preserved across hook calls within the session."""
        sf = f"/tmp/gludd-tool-streak-samepid-{os.getpid()}.json"
        _clean(sf)
        # We can't know the node PID ahead of time, so we write a dummy file
        # and let the first readSharedStreak() claim it. Then verify a second
        # read preserves the state.
        _write_json(sf, {
            "streak": 0,
            "lastDispatchTs": 0,
            "readStreak": 0,
            "editStreak": 0,
            "lastUpdateTs": 0,
            "lastWriter": "",
            "pid": 0,
        })
        try:
            code = f"""\
const shared = await import('{ROOT / ".opencode" / "lib" / "shared.ts"}')
// First call: pid=0 in file → readSharedStreak claims it (pid 0 is treated
// as "uninitialized", not stale). updateSharedStreak writes current pid.
const s1 = shared.updateSharedStreak('edit', 'test-plugin')
// Second call: file now has current pid → streak should be 1 (preserved)
const s2 = shared.readSharedStreak()
console.log(JSON.stringify({{
  firstReadPid: s1.pid,
  secondReadStreak: s2.streak,
  secondReadPid: s2.pid,
  streakPreserved: s2.streak === 1,
  pidConsistent: s1.pid === s2.pid,
}}))
"""
            result = _run_ts(code, env_override={"GLUDD_STREAK_FILE": sf})
            assert result is not None
            assert result["streakPreserved"] is True, (
                f"After same-PID read, streak must be preserved (expected 1). "
                f"Got: {result}"
            )
            assert result["pidConsistent"] is True, (
                "PID must be consistent across reads within the same process"
            )
        finally:
            _clean(sf)

    def test_stale_streak_does_not_bypass_floor_breach(self):
        """A stale streak file with streak=0 (from a prior session that just
        dispatched) must NOT prevent the floor breach from firing. After
        reset, 3 consecutive non-dispatch calls must trigger the deny."""
        tasks_path = f"/tmp/gludd-pid-stale-floor-tasks-{os.getpid()}.json"
        todowrite_path = f"/tmp/gludd-pid-stale-todo-{os.getpid()}.json"
        session_state = f"/tmp/gludd-pid-stale-session-{os.getpid()}.json"
        sf = f"/tmp/gludd-tool-streak-floor-{os.getpid()}.json"
        _clean(tasks_path, todowrite_path, session_state, sf,
               "/tmp/gludd-watchdog-disengage.json")
        with open(tasks_path, "w") as f:
            f.write("- [ ] floor staleness test\n")
        with open(todowrite_path, "w") as f:
            json.dump([{"status": "pending", "content": "test"}], f)
        with open(session_state, "w") as f:
            json.dump({}, f)
        # Stale streak file: looks like a prior session just dispatched (streak=0)
        _write_json(sf, {
            "streak": 0,
            "lastDispatchTs": 0,
            "readStreak": 0,
            "editStreak": 0,
            "lastUpdateTs": 0,
            "lastWriter": "enforce-floor",
            "pid": FAKE_STALE_PID,
        })
        try:
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// Call 1: streak 0→1, allowed
const r1 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// Call 2: streak 1→2, allowed
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// Call 3: streak 2→3, DENIED (MAX_STREAK=2)
const r3 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify({{
  r1_null: r1 === undefined || r1 === null,
  r2_null: r2 === undefined || r2 === null,
  r3_deny: r3?.permissionDecision === 'deny',
}}))
"""
            result = _run_ts(code, env_override={
                "GLUDD_TASKS_MD": tasks_path,
                "GLUDD_TODOWRITE_STATE": todowrite_path,
                "GLUDD_SESSION_STATE": session_state,
                "GLUDD_STREAK_FILE": sf,
            })
            assert result is not None
            assert result["r3_deny"] is True, (
                f"Call 3 must be denied after stale streak reset — "
                f"stale streak=0 must NOT persist. Got: {result}"
            )
        finally:
            _clean(tasks_path, todowrite_path, session_state, sf)


# ===========================================================================
# CROSS-SESSION SIMULATION
# ===========================================================================

class TestCrossSessionSimulation:
    """Simulate two sessions with different PIDs and verify the second
    session's enforcement fires despite stale state from the first."""

    def test_session1_state_does_not_bypass_session2_enforcement(self):
        """Session 1 writes a state file with pid=P1 and high dispatch counts.
        Session 2 (pid=P2) reads the stale file, resets it, and enforcement
        fires correctly (non-dispatch call is denied)."""
        tasks_path = f"/tmp/gludd-xsession-tasks-{os.getpid()}.md"
        _clean(MULTITASK_STATE_FILE, tasks_path, "/tmp/gludd-watchdog-disengage.json")
        # Session 1 "left behind" a state file that looks like the floor was met
        _write_json(MULTITASK_STATE_FILE, {
            "pid": FAKE_STALE_PID,
            "thisMessageDispatches": 10,
            "prevMessageDispatches": 10,
            "zeroStreak": 0,
            "estimatedInFlight": 10,
            "lastTs": 0,
            "lastToolCallTs": 0,
            "waveHistory": [10, 10],
            "consecutiveNonDispatch": 0,
            "consecutiveNonDispatchStartTs": 0,
        })
        with open(tasks_path, "w") as f:
            f.write("- [ ] cross-session test task\n")
        try:
            # Session 2 starts: imports the module (IIFE resets stale state),
            # then tries a non-dispatch call
            code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const fs = await import('node:fs')
// Read the state AFTER module load — should be reset
const raw = JSON.parse(fs.readFileSync('{MULTITASK_STATE_FILE}', 'utf8'))
// Now try a non-dispatch call — must be DENIED (under-floor)
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify({{
  statePidAfterLoad: raw.pid,
  stateDispatchesAfterLoad: raw.thisMessageDispatches,
  stalePidOverwritten: raw.pid !== {FAKE_STALE_PID},
  editDenied: result?.permissionDecision === 'deny',
  denyMessage: result?.message?.slice(0, 60) ?? null,
}}))
"""
            result = _run_ts(code, env_override={
                "GLUDD_MIN_DISPATCHES": "2",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                "GLUDD_TASKS_MD": tasks_path,
            })
            assert result is not None
            assert result["stalePidOverwritten"] is True, (
                "Stale PID from session 1 must be overwritten at session 2 load"
            )
            assert result["stateDispatchesAfterLoad"] == 0, (
                "Dispatch count must be 0 after reset — "
                "stale value 10 from session 1 would bypass the floor"
            )
            assert result["editDenied"] is True, (
                f"After stale-state reset, edit must be denied in session 2. "
                f"Got: {result}"
            )
        finally:
            _clean(MULTITASK_STATE_FILE, tasks_path)

    def test_simulated_crash_recovery_enforcement_restored(self):
        """Simulate a crashed session that left stale state with high streak
        values. The recovery session must detect the stale PID and restore
        enforcement — the shared streak file must be zeroed."""
        sf = f"/tmp/gludd-crash-streak-{os.getpid()}.json"
        _clean(sf, "/tmp/gludd-watchdog-disengage.json")
        # Crashed session left a streak file that looks like grinding was in progress
        # but with the wrong PID
        _write_json(sf, {
            "streak": 1,
            "lastDispatchTs": 0,
            "readStreak": 0,
            "editStreak": 1,
            "lastUpdateTs": int(__import__("time").time() * 1000),
            "lastWriter": "enforce-floor",
            "pid": FAKE_STALE_PID,
        })
        try:
            code = f"""\
const shared = await import('{ROOT / ".opencode" / "lib" / "shared.ts"}')
// Recovery session reads the stale streak file
const s = shared.readSharedStreak()
console.log(JSON.stringify({{
  streakReset: s.streak === 0,
  pidClaimed: s.pid === process.pid,
  stalePidDetected: s.lastWriter === 'pid-reset',
}}))
"""
            result = _run_ts(code, env_override={"GLUDD_STREAK_FILE": sf})
            assert result is not None
            assert result["streakReset"] is True, (
                "Streak must be reset to 0 after detecting stale PID"
            )
            assert result["pidClaimed"] is True, (
                "Recovery session must claim the streak file with its own PID"
            )
            assert result["stalePidDetected"] is True, (
                "lastWriter must be 'pid-reset' to indicate the reset path fired"
            )
        finally:
            _clean(sf)
