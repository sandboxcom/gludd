"""E2E test for enforce-multitask.ts: dispatch-per-wave enforcement.

Verifies: subagent guard, env disable, enough dispatches allowed,
single dispatch blocked, zero-streak text blocked, dispatch resets streak.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> str:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"multitask_e2e_{_ts_counter}_"))
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
        try:
            tmp.unlink()
        except OSError:
            pass


def _last_json(stdout: str) -> dict | None:
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
    (path / "TASKS.md").write_text("- [ ] test item\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)


def _make_clean_workspace(path: Path) -> None:
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
    assert r is None or r.get("permissionDecision") != "deny", f"Subagent should skip, got: {r}"


# ─── Env disable ────────────────────────────────────────────────────────────


def test_env_disable_skips_enforcement(tmp_path):
    """GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables all enforcement."""
    ws = tmp_path / "env-off"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
// Send text.complete with 0 dispatches three times to build zeroStreak
await plugin['experimental.text.complete'](undefined, {{text: 'msg1'}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg2'}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg3'}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_MULTITASK_FLOOR_ENFORCE": "0"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Env disable should allow, got: {r}"


# ─── Enough dispatches allows ───────────────────────────────────────────────


def test_enough_dispatches_allows_non_dispatch(tmp_path):
    """After 10 dispatches in the SAME message (before text.complete),
    non-dispatch tools are allowed because thisMessageDispatches >= MIN_DISPATCHES."""
    ws = tmp_path / "enough"
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
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"10 dispatches same-msg should allow, got: {r}"


# ─── Single dispatch blocked ────────────────────────────────────────────────


def test_single_dispatch_then_non_dispatch_blocked(tmp_path):
    """1 dispatch → text.complete → non-dispatch tool: DENIED (prevMessageDispatches=1 < 5)."""
    ws = tmp_path / "single"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['experimental.text.complete'](undefined, {{text: 'ok'}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Single dispatch should block, got: {r}"
    assert "dispatch(es) in prior message" in r.get("message", "")


def test_single_dispatch_then_bash_blocked(tmp_path):
    """1 dispatch → text.complete → bash: DENIED."""
    ws = tmp_path / "single-bash"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['experimental.text.complete'](undefined, {{text: 'ok'}})
const r = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Bash should be blocked, got: {r}"


# ─── Zero-streak text blocked ───────────────────────────────────────────────


def test_zero_dispatch_streak_blocks_text(tmp_path):
    """After 3 text.completes with 0 dispatches, text is blocked (zeroStreak >= 2)."""
    ws = tmp_path / "zero-streak"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg1'}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg2'}})
const r = await plugin['experimental.text.complete'](undefined, {{text: 'msg3'}})
console.log(JSON.stringify(r ?? {{unchanged: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and "MUST DISPATCH" in r.get("text", ""), f"Text should be blocked, got: {r}"


# ─── Dispatch resets streak ─────────────────────────────────────────────────


def test_dispatch_resets_zero_streak(tmp_path):
    """A dispatch between text.completes resets zeroStreak to 0."""
    ws = tmp_path / "reset"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg1'}})
await plugin['experimental.text.complete'](undefined, {{text: 'msg2'}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['experimental.text.complete'](undefined, {{text: 'msg3'}})
const r = await plugin['experimental.text.complete'](undefined, {{text: 'msg4'}})
console.log(JSON.stringify(r ?? {{unchanged: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    # After dispatch, zeroStreak resets → subsequent text.completes start from 0
    # msg3 after dispatch: zeroStreak=0, msg4: zeroStreak=1 (not yet blocked)
    assert r is None or "MUST DISPATCH" not in r.get("text", ""), f"Dispatch should reset streak, got: {r}"


# ─── Read tools allowed in fresh state with pending work ────────────────────


def test_read_tool_allowed_when_no_prev_message_dispatches(tmp_path):
    """With prevMessageDispatches=0 and pending work, read tool is not blocked
    (second check only gates edit/write/bash)."""
    ws = tmp_path / "read-ok"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Read tools allowed fresh, got: {r}"


# ─── No block when no open work ─────────────────────────────────────────────


def test_no_block_when_no_open_work(tmp_path):
    """With no pending work and fresh state, non-dispatch tools are not blocked."""
    ws = tmp_path / "no-work"
    ws.mkdir()
    _make_clean_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"No block without work, got: {r}"


# ─── Fail-open ──────────────────────────────────────────────────────────────


def test_non_git_dir_fails_open(tmp_path):
    """Non-git directory should fail-open (allow dispatch)."""
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
    assert r is None or r.get("permissionDecision") != "deny", f"Non-git dir should fail-open, got: {r}"


# ─── Block message content ──────────────────────────────────────────────────


def test_block_message_includes_disable_instruction(tmp_path):
    """Deny message tells user how to disable."""
    ws = tmp_path / "msg"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['experimental.text.complete'](undefined, {{text: 'ok'}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    msg = r.get("message", "")
    assert "GLUDD_MULTITASK_FLOOR_ENFORCE=0" in msg
