"""E2e test for enforce-session-start.ts: session-start protocol enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types
with per-test state files, verifying deny/allow/subagent/disable/fail-open.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-session-start.ts"

_ts_counter = 0
_state_counter = 0


def _run_plugin(ts_code, env_override=None, cwd=None, timeout=15):
    """Write TS to temp file, run via node, return (ok, stdout, stderr)."""
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"sess_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd or str(ROOT), env=env,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _make_state_file(state: dict | None = None) -> str:
    """Create a temp state file. If state is None, writes a corrupt file."""
    global _state_counter
    _state_counter += 1
    fpath = f"/tmp/gludd-session-e2e-{os.getpid()}-{_state_counter}.json"
    if state is None:
        Path(fpath).write_text("NOT VALID JSON {{{")
    else:
        Path(fpath).write_text(json.dumps(state))
    return fpath


def _fresh_state(started_at_ms: float | None = None) -> dict:
    """Return a fresh (unprimed) session state dict."""
    return {
        "started_at": started_at_ms or int(time.time() * 1000),
        "readsDone": False,
        "dispatches": 0,
        "timeGateReset": False,
    }


def _primed_state() -> dict:
    """Return a primed session state dict."""
    return {
        "started_at": int(time.time() * 1000) - 60_000,
        "readsDone": True,
        "dispatches": 10,
        "timeGateReset": True,
    }


def _hook_code(tool: str) -> str:
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const input = {{tool: '{tool}'}}
try {{
  await plugin['tool.execute.before'](input, undefined)
  console.log('ALLOWED')
}} catch (e) {{
  console.log('BLOCKED')
  console.log('MSG:' + e.message)
}}
"""


# ─── Fresh session blocks mutations (edit/write/bash) ────────────────────


def test_fresh_session_blocks_edit(tmp_path):
    """Fresh session (no reads, no dispatches) denies 'edit' tool."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"Plugin should NOT crash; stderr: {stderr[:400]}"
    assert "BLOCKED" in stdout, f"Expected BLOCKED, got stdout: {stdout[:200]}"
    assert "SESSION START PROTOCOL" in stdout, f"Deny message missing protocol tag: {stdout[:200]}"


def test_fresh_session_blocks_write(tmp_path):
    """Fresh session denies 'write' tool."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("write")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "BLOCKED" in stdout


def test_fresh_session_blocks_bash(tmp_path):
    """Fresh session denies 'bash' tool."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("bash")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "BLOCKED" in stdout


# ─── Read tools always allowed ───────────────────────────────────────────


def test_read_tool_allowed_in_fresh_session(tmp_path):
    """'read' tool is always allowed, even in fresh session."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("read")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


def test_glob_tool_allowed_in_fresh_session(tmp_path):
    """'glob' tool is always allowed."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("glob")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


def test_grep_tool_allowed_in_fresh_session(tmp_path):
    """'grep' tool is always allowed."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("grep")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


# ─── Dispatch tools always allowed ───────────────────────────────────────


def test_task_dispatch_allowed(tmp_path):
    """'task' dispatch tool is always allowed."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("task")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


def test_agent_dispatch_allowed(tmp_path):
    """'agent' dispatch tool is always allowed."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("agent")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


def test_workflow_dispatch_allowed(tmp_path):
    """'workflow' dispatch tool is always allowed."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("workflow")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={"GLUDD_SESSION_STATE": state_path},
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


# ─── Subagent guard ──────────────────────────────────────────────────────


def test_subagent_skips_enforcement(tmp_path):
    """OPENCODE_SUBAGENT=1 bypasses session-start enforcement."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "OPENCODE_SUBAGENT": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout, f"Subagent should skip check, got: {stdout[:200]}"


# ─── Env-var disable ─────────────────────────────────────────────────────


def test_env_disable_allows_mutation(tmp_path):
    """GLUDD_SESSION_START_ENFORCE=0 makes enforcement advisory (no throw)."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "0",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout, (
        f"Env disable should allow mutation, got: {stdout[:200]}"
    )


# ─── Corrupt state: fail-open ────────────────────────────────────────────


def test_corrupt_state_fails_open(tmp_path):
    """Corrupt state file (invalid JSON) → fail-open (allow all)."""
    state_path = _make_state_file(None)  # corrupt JSON
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout, (
        f"Corrupt state must fail-open (allow), got: {stdout[:200]}"
    )


# ─── Primed state: reads done + dispatches ≥ EFFECTIVE_MIN ───────────────


def test_primed_state_allows_mutation(tmp_path):
    """After reads + dispatches, mutations are allowed."""
    state_path = _make_state_file(_primed_state())
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_MIN_DISPATCHES": "1",
            "CLAUDE_AGENT_FLOOR": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout, (
        f"Primed state must allow mutation, got: {stdout[:200]}"
    )


def test_primed_with_hard_floor_allows_mutation(tmp_path):
    """The hard floor is 10 dispatches even when env vars are lower."""
    small_state = {
        "started_at": int(time.time() * 1000) - 60_000,
        "readsDone": True,
        "dispatches": 10,
        "timeGateReset": True,
    }
    state_path = _make_state_file(small_state)
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_MIN_DISPATCHES": "1",
            "CLAUDE_AGENT_FLOOR": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "ALLOWED" in stdout


def test_reads_done_but_no_dispatches_still_blocks(tmp_path):
    """readsDone=true + dispatches=0 (below EFFECTIVE_MIN) → still blocked."""
    partial_state = {
        "started_at": int(time.time() * 1000),
        "readsDone": True,
        "dispatches": 0,
        "timeGateReset": False,
    }
    state_path = _make_state_file(partial_state)
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "BLOCKED" in stdout, (
        f"readsDone without dispatches must still block, got: {stdout[:200]}"
    )


# ─── system.transform banner injection ───────────────────────────────────


def test_system_transform_injects_session_start_banner(tmp_path):
    """experimental.chat.system.transform prepends SESSION START PROTOCOL."""
    state_path = _make_state_file(_fresh_state())
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['experimental.chat.system.transform'](null, "ORIGINAL PROMPT")
console.log(result)
"""
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_MIN_DISPATCHES": "10",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "SESSION START PROTOCOL" in stdout, (
        f"system.transform must inject banner. Got: {stdout[:400]}"
    )
    assert "STEP 1" in stdout
    assert "STEP 2" in stdout
    assert "ORIGINAL PROMPT" in stdout, (
        f"Original prompt must be preserved: {stdout[:400]}"
    )


def test_system_transform_subagent_skips_banner(tmp_path):
    """Subagent (OPENCODE_SUBAGENT=1) returns output unchanged."""
    state_path = _make_state_file(_fresh_state())
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['experimental.chat.system.transform'](null, "ORIGINAL PROMPT")
console.log('RESULT:' + result)
"""
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "OPENCODE_SUBAGENT": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "RESULT:ORIGINAL PROMPT" in stdout, (
        f"Subagent must skip banner, got: {stdout[:300]}"
    )


# ─── Deny message content ────────────────────────────────────────────────


def test_deny_message_includes_state_info(tmp_path):
    """Deny message contains readsDone, dispatches/cap, and disable hint."""
    state_path = _make_state_file(_fresh_state())
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "readsDone" in stdout
    assert "dispatches" in stdout
    assert "GLUDD_SESSION_START_ENFORCE=0" in stdout


# ─── Stale session: outside fresh window, mutations allowed ──────────────


def test_stale_session_with_timegate_reset_is_recovered_and_blocks(tmp_path):
    """Session older than stale threshold is reset before mutation checks."""
    old_state = {
        "started_at": int(time.time() * 1000) - 700_000,
        "readsDone": False,
        "dispatches": 0,
        "timeGateReset": True,
    }
    state_path = _make_state_file(old_state)
    code = _hook_code("edit")
    ok, stdout, stderr = _run_plugin(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, f"stderr: {stderr[:400]}"
    assert "BLOCKED" in stdout, (
        f"Stale session must reset and block mutation, got: {stdout[:200]}"
    )


# ─── Time gate: dispatch within window resets the gate ───────────────────


def test_dispatch_resets_time_gate(tmp_path):
    """First dispatch sets timeGateReset, subsequent mutation allowed."""
    state_path = _make_state_file({
        "started_at": int(time.time() * 1000),
        "readsDone": False,
        "dispatches": 0,
        "timeGateReset": False,
    })
    # Step 1: dispatch 'task' to reset the time gate + increment count
    code_dispatch = _hook_code("task")
    ok, _, _ = _run_plugin(
        code_dispatch,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
            "GLUDD_SESSION_START_MIN_DISPATCHES": "1",
            "CLAUDE_AGENT_FLOOR": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok, "Dispatch must be allowed"
    # Step 2: the state file now has dispatches=1, timeGateReset=true
    # Session is still fresh (started recently), but readsDone=false.
    # With EFFECTIVE_MIN=1, one dispatch isn't enough; we need readsDone too.
    # So edit should still be blocked because readsDone is false.
    code_edit = _hook_code("edit")
    ok2, stdout2, stderr2 = _run_plugin(
        code_edit,
        env_override={
            "GLUDD_SESSION_STATE": state_path,
            "GLUDD_SESSION_START_ENFORCE": "1",
            "GLUDD_SESSION_START_MIN_DISPATCHES": "1",
            "CLAUDE_AGENT_FLOOR": "1",
        },
        cwd=str(tmp_path),
    )
    assert ok2, f"stderr: {stderr2[:400]}"
    # Still blocked because readsDone is false despite dispatches increment
    assert "BLOCKED" in stdout2, (
        f"No reads → still blocked even after dispatch, got: {stdout2[:200]}"
    )
