"""E2e test for enforce-floor.ts: streak-based 10-agent floor enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated temp dirs, verifying key behaviors of the floor enforcement hook.
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
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-floor.ts"

# opencode >=1.17.9 removed the `text.complete` hook, so enforce-floor.ts detects
# agent-message boundaries from the idle gap between tool.execute.before calls
# (GLUDD_MESSAGE_BOUNDARY_MS, default 5000ms in production). Tests shrink the gap
# so they can cross a REAL boundary without a 5s sleep.
_BOUNDARY_MS = 500
_BOUNDARY_ENV = {"GLUDD_MESSAGE_BOUNDARY_MS": str(_BOUNDARY_MS)}
_BOUNDARY_SLEEP_JS = (
    f"await new Promise(res => setTimeout(res, {_BOUNDARY_MS * 2}))"
)

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> str:
    """Write TS to temp file, run via node, return stdout."""
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"floor_e2e_{_ts_counter}_"))
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
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        return proc.stdout.strip()
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _last_json(stdout: str) -> dict | None:
    """Parse the last JSON line from stdout."""
    for line in reversed(stdout.split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _make_working_workspace(path: Path) -> None:
    """Create a workspace where openWorkExists() returns true (unchecked TASKS.md item)."""
    (path / "TASKS.md").write_text("- [ ] test item\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    # Don't commit TASKS.md — git status will show dirty, which also triggers openWorkExists


def _make_clean_workspace(path: Path) -> None:
    """Create a workspace where openWorkExists() returns false (nothing pending)."""
    (path / "TASKS.md").write_text("No pending items\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    (path / "config").mkdir(exist_ok=True)
    (path / "config" / "ratchet.yml").write_text("# no entries\n")
    subprocess.run(["git", "add", "TASKS.md", "config/ratchet.yml"], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, capture_output=True)


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_enforcement(tmp_path):
    """OPENCODE_SUBAGENT=1 bypasses all enforcement."""
    ws = tmp_path / "subagent"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Subagent should skip check, got: {r}"
    )


# ─── Env disable ────────────────────────────────────────────────────────────


def test_env_disable_skips_enforcement(tmp_path):
    """GLUDD_FLOOR_ENFORCE=0 disables all enforcement."""
    ws = tmp_path / "env-off"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_FLOOR_ENFORCE": "0"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Env disable should allow all calls, got: {r}"
    )


# ─── Dispatch resets streak ─────────────────────────────────────────────────


def test_dispatch_resets_streak(tmp_path):
    """Dispatching (task/agent/workflow) resets the non-dispatch streak to 0."""
    ws = tmp_path / "dispatch-reset"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Dispatch should reset streak; next non-dispatch allowed, got: {r}"
    )


def test_agent_dispatch_also_resets_streak(tmp_path):
    """agent tool dispatch also resets streak."""
    ws = tmp_path / "agent-reset"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny"


def test_workflow_dispatch_also_resets_streak(tmp_path):
    """workflow tool dispatch also resets streak."""
    ws = tmp_path / "workflow-reset"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny"


# ─── Max streak blocks non-dispatch ─────────────────────────────────────────


def test_max_streak_blocks_non_dispatch(tmp_path):
    """3rd consecutive non-dispatch call (streak 3 > MAX_STREAK 2) is denied."""
    ws = tmp_path / "streak-block"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"3rd non-dispatch should be blocked, got: {r}"
    )
    assert "FLOOR BREACH" in r.get("message", "")


def test_second_non_dispatch_not_yet_blocked(tmp_path):
    """2nd non-dispatch (streak 2 = MAX_STREAK) is still allowed."""
    ws = tmp_path / "streak-ok"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"2nd non-dispatch should be allowed (streak 2 <= MAX), got: {r}"
    )


# ─── Read tools not counted for streak ──────────────────────────────────────


def test_read_tools_not_counted_for_streak(tmp_path):
    """read/grep/glob calls do NOT increment the non-dispatch streak."""
    ws = tmp_path / "read-streak"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'grep'}}, undefined)
await plugin['tool.execute.before']({{tool: 'glob'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Reads-should-not-count; streak from writes alone should be 2 <= MAX, got: {r}"
    )


# ─── Disengage escape ───────────────────────────────────────────────────────


def test_disengage_escape_resets_streak(tmp_path):
    """isDisengaged() resets streaks and allows calls."""
    ws = tmp_path / "disengage"
    ws.mkdir()
    _make_working_workspace(ws)

    disengage_path = "/tmp/gludd-e2e-floor-disengage.json"
    disengage_data = {"disengage_until": int(time.time() * 1000) + 300_000}
    with open(disengage_path, "w") as f:
        json.dump(disengage_data, f)

    try:
        code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
        result = _run_plugin(code, env_override={
            "GLUDD_DISENGAGE_PATH": disengage_path,
        }, cwd=str(ws))
        r = _last_json(result)
        assert r is None or r.get("permissionDecision") != "deny", (
            f"Disengage should skip enforcement, got: {r}"
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(disengage_path)


# ─── Message shape rule ─────────────────────────────────────────────────────


def test_message_shape_blocks_after_thin_dispatch_wave(tmp_path):
    """After a 1-dispatch wave, next non-dispatch tool call is blocked."""
    ws = tmp_path / "msg-shape"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
// Message 1: dispatch exactly 1 task → _thisMessageDispatchCount = 1
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
// Message boundary: idle longer than GLUDD_MESSAGE_BOUNDARY_MS
{_BOUNDARY_SLEEP_JS}
// Message 2: boundary rolls _prevMessageDispatchCount to 1 → non-dispatch denied
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_BOUNDARY_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Message-shape violation should block, got: {r}"
    )
    assert "MESSAGE-SHAPE" in r.get("message", "")


def test_message_shape_allows_after_5plus_dispatch_wave(tmp_path):
    """After a 5-dispatch wave (non-thin), next non-dispatch is allowed."""
    ws = tmp_path / "msg-ok"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
{_BOUNDARY_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_BOUNDARY_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"5+ dispatch wave should allow next non-dispatch, got: {r}"
    )


# ─── Compulsive-check block ─────────────────────────────────────────────────


def test_compulsive_check_blocked_with_open_work(tmp_path):
    """Standalone make git-log/bash call blocked when open work exists."""
    ws = tmp_path / "comp-check"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before'](
  {{tool: 'bash'}},
  {{args: {{command: 'make git-log'}}}},
)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Compulsive-check should be blocked, got: {r}"
    )
    assert "COMPULSIVE-CHECK" in r.get("message", "")


def test_compulsive_check_ci_verdict_blocked(tmp_path):
    """make ci-verdict blocked when open work exists."""
    ws = tmp_path / "comp-ci"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before'](
  {{tool: 'bash'}},
  {{args: {{command: 'make ci-verdict'}}}},
)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"ci-verdict should be blocked, got: {r}"
    )


def test_compulsive_check_git_diff_blocked(tmp_path):
    """make git-diff blocked when open work exists."""
    ws = tmp_path / "comp-diff"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before'](
  {{tool: 'bash'}},
  {{args: {{command: 'make git-diff'}}}},
)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny"


def test_compulsive_check_not_blocked_without_open_work(tmp_path):
    """Compulsive-check not blocked when openWorkExists() returns false."""
    ws = tmp_path / "comp-ok"
    ws.mkdir()
    _make_clean_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before'](
  {{tool: 'bash'}},
  {{args: {{command: 'make git-log'}}}},
)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Compulsive-check allowed without open work, got: {r}"
    )


# ─── No block when no open work ─────────────────────────────────────────────


def test_no_block_when_no_open_work(tmp_path):
    """When openWorkExists() returns false, streak is reset and call allowed."""
    ws = tmp_path / "no-work"
    ws.mkdir()
    _make_clean_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"No block without open work, got: {r}"
    )


# ─── Fail-open: exception → allow ───────────────────────────────────────────


def test_non_git_dir_fails_open(tmp_path):
    """Non-git/non-project directory should fail-open (allow dispatch)."""
    ws = tmp_path / "nonrepo"
    ws.mkdir()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Non-git dir should fail-open, got: {r}"
    )


# ─── Block message content ──────────────────────────────────────────────────


def test_floor_breach_message_includes_streak_count(tmp_path):
    """Block message reports the streak count and floor value."""
    ws = tmp_path / "msg-count"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    msg = r.get("message", "")
    assert "FLOOR BREACH" in msg
    assert "3" in msg  # streak count = 3


def test_floor_breach_message_includes_disable_instruction(tmp_path):
    """Block message tells user how to disable enforcement."""
    ws = tmp_path / "msg-disable"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    msg = r.get("message", "")
    assert "GLUDD_FLOOR_ENFORCE=0" in msg
