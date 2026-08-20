"""E2E test for explicitly configured enforce-multitask wave enforcement.

Verifies: subagent guard, env disable, enough dispatches allowed,
single dispatch blocked, zero-streak text blocked, dispatch resets streak.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

from tests.e2e.state_isolation import build_state_environment

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"

# opencode >=1.17.9 removed the `text.complete` hook, so enforce-multitask.ts
# detects agent-message boundaries from the idle gap between tool.execute.before
# calls (GLUDD_MSG_GAP_MS, default 5000ms in production). Tests shrink the gap so
# they can cross REAL message boundaries without 5s sleeps.
_GAP_MS = 500
_GAP_ENV = {"GLUDD_MSG_GAP_MS": str(_GAP_MS)}
_GAP_SLEEP_JS = f"await new Promise(res => setTimeout(res, {_GAP_MS * 2}))"
_MUTABLE_STATE_FILENAMES = {
    "GLUDD_ALIVE_PATH": "alive.json",
    "GLUDD_CI_CACHE_PATH": "ci.json",
    "GLUDD_DISENGAGE_PATH": "disengage.json",
    "GLUDD_DISPATCH_OUTCOMES_FILE": "dispatch-outcomes.json",
    "GLUDD_HOT_MODULE_PREFIX": "hot-",
    "GLUDD_MULTITASK_DISPATCH_COUNT_FILE": "dispatch-count.json",
    "GLUDD_MULTITASK_STATE_FILE": "multitask.json",
    "GLUDD_RELEASE_COMPLETENESS_FILE": "release.json",
    "GLUDD_STOP_STATE_PATH": "stop.json",
    "GLUDD_TODOWRITE_STATE_PATH": "todo.json",
}
_MUTABLE_STATE_ENV_KEYS = tuple(_MUTABLE_STATE_FILENAMES)

def _run_plugin(
    ts_code: str,
    env_override: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> str:
    with tempfile.TemporaryDirectory(prefix="gludd-multitask-e2e-") as state_dir_text:
        state_dir = Path(state_dir_text)
        tmp = state_dir / "runner.ts"
        tmp.write_text(ts_code, encoding="utf-8")
        env = build_state_environment(
            state_dir,
            _MUTABLE_STATE_FILENAMES,
            extra={
                "OPENCODE_SUBAGENT": "",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                "GLUDD_MIN_DISPATCHES": "10",
                "GLUDD_PROJECT_ROOT": str(Path(cwd or ROOT)),
            },
        )
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or str(ROOT),
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        return proc.stdout.strip()


def _last_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return cast(dict[str, Any], value)
        except json.JSONDecodeError:
            continue
    return None


def test_plugin_runner_namespaces_every_mutable_state_path(tmp_path: Path) -> None:
    """Concurrent workers must never read or write another session's state."""
    workspace = tmp_path / "isolated-state"
    workspace.mkdir()
    keys = json.dumps(_MUTABLE_STATE_ENV_KEYS)
    code = f"""\
const keys = {keys}
console.log(JSON.stringify(Object.fromEntries(keys.map(key => [key, process.env[key] ?? null]))))
"""

    result = _last_json(_run_plugin(code, cwd=str(workspace)))

    assert result is not None
    assert all(isinstance(result[key], str) and result[key] for key in _MUTABLE_STATE_ENV_KEYS)
    state_parents = {Path(cast(str, result[key])).parent for key in _MUTABLE_STATE_ENV_KEYS}
    assert len(state_parents) == 1
    assert not state_parents.pop().exists()


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

    # Three zero-dispatch messages would normally trip the zero-dispatch streak.
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={"GLUDD_MULTITASK_FLOOR_ENFORCE": "0", **_GAP_ENV},
        cwd=str(ws),
    )
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
    """1 dispatch → message boundary → non-dispatch tool: DENIED (prev=1 < MIN_DISPATCHES)."""
    ws = tmp_path / "single"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Single dispatch should block, got: {r}"
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_single_dispatch_then_bash_blocked(tmp_path):
    """1 dispatch → message boundary → bash: DENIED."""
    ws = tmp_path / "single-bash"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Bash should be blocked, got: {r}"


def test_full_dispatch_wave_unblocks_non_dispatch(tmp_path):
    """A thin prior wave is forgiven once this message dispatches a full wave."""
    ws = tmp_path / "wave-unblock"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(10))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
{_GAP_SLEEP_JS}
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Full wave in this message should unblock non-dispatch tools, got: {r}"
    )


# ─── Zero-streak text blocked ───────────────────────────────────────────────


def test_zero_dispatch_streak_blocks_tool_call(tmp_path):
    """After MAX_ZERO_STREAK zero-dispatch messages, the next tool call is denied."""
    ws = tmp_path / "zero-streak"
    ws.mkdir()
    _make_working_workspace(ws)

    # Three messages, each with 0 dispatches → zeroStreak reaches MAX_ZERO_STREAK (2).
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Zero-dispatch streak should block, got: {r}"
    assert "ZERO-DISPATCH STREAK" in r.get("message", "")


# ─── Dispatch resets streak ─────────────────────────────────────────────────


def test_dispatch_resets_zero_streak(tmp_path):
    """A full dispatch wave resets zeroStreak, so the next message is not blocked."""
    ws = tmp_path / "reset"
    ws.mkdir()
    _make_working_workspace(ws)

    # Same shape as the zero-streak test, except message 3 dispatches a full wave.
    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(10))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Full dispatch wave should reset zero streak (write in same msg as dispatches), got: {r}"
    )


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
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    msg = r.get("message", "")
    assert "GLUDD_MULTITASK_FLOOR_ENFORCE=0" in msg


# ─── Rapid Grinding Bypass Bug ────────────────────────────────────────────────
# These tests pin the bug: MSG_GAP_MS-based message boundary detection means
# calls within <MSG_GAP_MS never fire a boundary, so zeroStreak never increments
# and ALL enforcement (floor breach, per-message, zero-streak) is blind to
# rapid inline grinding.  The agent can make 100 non-dispatch calls in a row
# without triggering any block as long as they are <MSG_GAP_MS apart.


def test_rapid_grinding_never_increments_zero_streak(tmp_path):
    """BUG: 5 rapid non-dispatch calls within MSG_GAP_MS.
    No message boundary fires, so zeroStreak stays 0.
    Proves the agent can grind inline indefinitely."""
    ws = tmp_path / "rapid-grind"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
import * as fs from 'node:fs'
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const state = JSON.parse(fs.readFileSync(process.env.GLUDD_MULTITASK_STATE_FILE, 'utf8'))
console.log(JSON.stringify({{
    zeroStreak: state.zeroStreak,
    thisMessageDispatches: state.thisMessageDispatches,
    prevMessageDispatches: state.prevMessageDispatches,
}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r["zeroStreak"] == 0, (
        f"BUG: zeroStreak stays 0 after 5 non-dispatch calls within MSG_GAP_MS. "
        f"Enforcement is blind to rapid grinding. State: {r}"
    )
    assert r["thisMessageDispatches"] == 0


def test_rapid_grinding_should_block_write_with_pending_work(tmp_path):
    """3 rapid write calls with pending work. SHOULD be blocked by enforcement.
    Currently all are allowed — proving the bug."""
    ws = tmp_path / "grind-write"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"3 rapid writes with pending work SHOULD be blocked, got: {r}"
    )


def test_consecutive_non_dispatch_within_time_window(tmp_path):
    """4 consecutive non-dispatch calls with 100ms sleeps between them
    (all within MSG_GAP_MS=500). CONFIGURED MINIMUM BLOCK now fires on
    the first write because thisMessageDispatches=0 < floor=10.
    Previously all were allowed; now the plugin catches them."""
    ws = tmp_path / "time-window"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await new Promise(res => setTimeout(res, 100))
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await new Promise(res => setTimeout(res, 100))
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await new Promise(res => setTimeout(res, 100))
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Second write should be blocked by CONFIGURED MINIMUM BLOCK, got: {r}"
    )
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_first_call_bypass_no_prior_dispatch_with_pending_work(tmp_path):
    """First tool call is Write with pending work. Currently allowed because
    lastToolCallTs=0 (no boundary) and prevMessageDispatches=0 (no floor breach).
    Should detect this as 'first call with pending work and no dispatch'."""
    ws = tmp_path / "first-bypass"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"First write with pending work SHOULD be blocked, got: {r}"
    )


def test_mixed_read_write_grinding_with_pending_work(tmp_path):
    """Read→Write→Read→Write in rapid succession with pending work.
    The writes are now blocked by CONFIGURED MINIMUM BLOCK (0 dispatches).
    Previously they all bypassed enforcement; now the plugin catches them."""
    ws = tmp_path / "mixed-grind"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Write with 0 dispatches should be blocked by CONFIGURED MINIMUM BLOCK, got: {r}"
    )
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_time_boundary_exactly_at_threshold(tmp_path):
    """Calls spaced exactly MSG_GAP_MS+50ms apart. After 3 messages
    with 0 dispatches, zeroStreak=2 → 3rd message's call blocked.
    Proves boundary detection WORKS when the gap IS exceeded —
    the bug is only that rapid calls stay inside the gap."""
    ws = tmp_path / "exact-boundary"
    ws.mkdir()
    _make_working_workspace(ws)

    gap = _GAP_MS + 50
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await new Promise(res => setTimeout(res, {gap}))
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await new Promise(res => setTimeout(res, {gap}))
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Call 3 at MSG_GAP_MS+50ms should be blocked (zeroStreak=2), got: {r}"
    )
    assert "ZERO-DISPATCH STREAK" in r.get("message", "")


def test_partial_wave_then_grind(tmp_path):
    """Dispatch 5 tasks, then after boundary, make non-dispatch call.
    Floor breach blocks because prevMessageDispatches=5 < MIN(10) and
    thisMessageDispatches=0.  First non-dispatch call after the boundary
    IS blocked — this proves boundary detection works, just not for
    calls within the gap."""
    ws = tmp_path / "partial-grind"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(5))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Partial wave floor breach should block, got: {r}"
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_hot_module_applies_same_message_boundary(tmp_path):
    """Verify the hot-reload module (if present at /tmp/gludd-hot-multitask.js)
    applies the same MSG_GAP_MS message boundary detection as the default.
    Uses the proxy pattern: dispatch through the plugin, which delegates to
    whichever module (hot or default) is active."""
    ws = tmp_path / "hot-module"
    ws.mkdir()
    _make_working_workspace(ws)

    hot_path = Path("/tmp/gludd-hot-multitask.js")
    has_hot = hot_path.exists()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Message boundary must apply with {'hot' if has_hot else 'default'} module, got: {r}"
    )
    assert "ZERO-DISPATCH STREAK" in r.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE 1: Main-thread grinding bypasses enforcement
# ─── 20 rapid non-dispatch calls with pending work ────────────────────────────


def test_rapid_grinding_20_calls_with_pending_work_should_be_blocked(tmp_path):
    """BUG: 20 non-dispatch calls rapidly (<500ms) with pending work.
    In a real session the agent made 20+ calls without dispatching.
    The time-based message boundary never fires, so zeroStreak stays 0.
    The CONSECUTIVE_NON_DISPATCH counter (threshold=5) SHOULD block after
    5 non-read mutating calls, but reads/greps/globs don't count.
    Proves: reads slip through; writes after 5 should be blocked."""
    ws = tmp_path / "grind-20"
    ws.mkdir()
    _make_working_workspace(ws)

    # 20 calls: 15 reads that bypass enforcement + 5 writes that trigger it
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"BUG: 20 rapid non-dispatch calls (15 reads + 5 writes) with pending work. "
        f"At least the 5th write (and subsequent reads) should be blocked by "
        f"CONSECUTIVE NON-DISPATCH STREAK or CONFIGURED MINIMUM BLOCK. "
        f"Got: {r}"
    )
    block_msg = r.get("message", "")
    assert "CONSECUTIVE NON-DISPATCH STREAK" in block_msg or "CONFIGURED MINIMUM BLOCK" in block_msg, (
        f"Expected enforcement block, got message: {block_msg}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE 2: Less-than-10 dispatches doesn't block non-dispatch tools
# ─── 8 dispatches then write with pending work ────────────────────────────────


def test_eight_dispatches_then_write_should_be_blocked(tmp_path):
    """BUG: Dispatch 8 tasks (< floor of 10), then write with pending work.
    In the real session, the agent dispatched 7-8 subagents and then used
    edit/write/bash freely. The CONFIGURED MINIMUM BLOCK should have prevented
    this but message boundary detection made it unreliable.
    Test: 8 dispatches, message boundary (sleep > MSG_GAP_MS), then write."""
    ws = tmp_path / "eight-dispatch"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(8))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"BUG: 8 dispatches then write with pending work SHOULD be blocked. 8 < floor=10. Got: {r}"
    )
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", ""), (
        f"Expected CONFIGURED MINIMUM BLOCK, got: {r.get('message', '')}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# New tests: 10-agent floor enforcement ──────────────────────────────────


def test_exactly_ten_dispatches_allows_non_dispatch(tmp_path):
    """Dispatch exactly 10 tasks, then in the SAME message, a non-dispatch tool
    is allowed (thisMessageDispatches=10 >= MIN_DISPATCHES=10)."""
    ws = tmp_path / "exact-10"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(10))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"10 dispatches in same message should allow write, got: {r}"
    )


def test_nine_dispatches_blocks_non_dispatch(tmp_path):
    """Dispatch 9 tasks (< floor of 10) then write in same message -> DENIED
    with CONFIGURED MINIMUM BLOCK message."""
    ws = tmp_path / "nine"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(9))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"9 dispatches (<10 floor) should block, got: {r}"
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_zero_dispatches_then_write_with_pending_work(tmp_path):
    """Fresh session, no dispatches, pending work, write tool -> blocked by
    CONFIGURED MINIMUM BLOCK regardless of zeroStreak or prevMessageDispatches."""
    ws = tmp_path / "zero-dispatch-write"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"First write with pending work MUST be blocked, got: {r}"
    )
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


def test_consecutive_non_dispatch_blocked_at_five(tmp_path):
    """5 consecutive non-dispatch mutating tool calls rapidly (within window)
    with pending work -> the 5th is BLOCKED by consecutive-non-dispatch counter
    (the first 4 are also blocked by UNDER-FLOOR, but the counter still
    increments, so call 5 hits the CONSECUTIVE NON-DISPATCH STREAK check)."""
    ws = tmp_path / "consec-5"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"5th consecutive non-dispatch write should be blocked, got: {r}"
    )
    assert "CONSECUTIVE NON-DISPATCH STREAK" in r.get("message", "")


def test_consecutive_non_dispatch_resets_on_dispatch(tmp_path):
    """Make 4 non-dispatch calls (consecutiveNonDispatch=4), dispatch 1 task
    (resets counter to 0), then verify the state file shows counter=0."""
    ws = tmp_path / "consec-reset"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
import * as fs from 'node:fs'
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const beforeDispatch = JSON.parse(fs.readFileSync(process.env.GLUDD_MULTITASK_STATE_FILE, 'utf8'))
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const afterDispatch = JSON.parse(fs.readFileSync(process.env.GLUDD_MULTITASK_STATE_FILE, 'utf8'))
console.log(JSON.stringify({{
  consecutiveBefore: beforeDispatch.consecutiveNonDispatch,
  consecutiveAfter: afterDispatch.consecutiveNonDispatch
}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r.get("consecutiveBefore") == 4, f"Before dispatch: consecutiveNonDispatch should be 4, got: {r}"
    assert r.get("consecutiveAfter") == 0, f"After dispatch: consecutiveNonDispatch should be reset to 0, got: {r}"


def test_read_tools_dont_count_toward_consecutive_streak(tmp_path):
    """10 read tool calls in rapid succession -> should NOT trigger the
    consecutive non-dispatch block (reads don't count toward the counter)."""
    ws = tmp_path / "reads-no-streak"
    ws.mkdir()
    _make_working_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Reads should not increment consecutive non-dispatch counter, got: {r}"
    )


def test_env_disable_skips_under_floor_check(tmp_path):
    """GLUDD_MULTITASK_FLOOR_ENFORCE=0: 9 dispatches (< floor) + write does
    NOT block because enforcement is entirely disabled."""
    ws = tmp_path / "env-disable-under"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(9))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={**_GAP_ENV, "GLUDD_MULTITASK_FLOOR_ENFORCE": "0"},
        cwd=str(ws),
    )
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Env disable should skip under-floor check, got: {r}"


def test_subagent_skips_under_floor_check(tmp_path):
    """OPENCODE_SUBAGENT=1: 9 dispatches (< floor) + write does NOT block
    because subagent context skips ALL enforcement."""
    ws = tmp_path / "subagent-under"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(9))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={**_GAP_ENV, "OPENCODE_SUBAGENT": "1"},
        cwd=str(ws),
    )
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Subagent context should skip under-floor check, got: {r}"
    )


def test_ten_dispatches_required_message_explicit(tmp_path):
    """The deny message for under-floor waves explicitly says '10' (the literal
    number) and contains 'CONFIGURED MINIMUM BLOCK'."""
    ws = tmp_path / "msg-explicit"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(9))
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    msg = r.get("message", "")
    assert "CONFIGURED MINIMUM BLOCK" in msg, f"Expected CONFIGURED MINIMUM BLOCK, got: {msg}"
    assert "10" in msg, f"Deny message must mention floor=10 explicitly, got: {msg}"


def test_under_floor_check_fires_without_zero_streak(tmp_path):
    """Under-floor check fires with 9 dispatches in same message + write.
    zeroStreak is 0 (never incremented because no message boundary was crossed),
    yet the CONFIGURED MINIMUM BLOCK still fires because it does NOT check
    zeroStreak at all."""
    ws = tmp_path / "under-floor-no-streak"
    ws.mkdir()
    _make_working_workspace(ws)

    dispatches = "\n".join("await plugin['tool.execute.before']({tool: 'task'}, undefined)" for _ in range(9))
    code = f"""\
import * as fs from 'node:fs'
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const statePath = process.env.GLUDD_MULTITASK_STATE_FILE
if (!statePath) throw new Error('GLUDD_MULTITASK_STATE_FILE must be set by the harness')
const state = JSON.parse(fs.readFileSync(statePath, 'utf8'))
const r = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify({{...r, zeroStreakBefore: state.zeroStreak}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", f"Under-floor check must fire, got: {r}"
    assert r.get("zeroStreakBefore") == 0, (
        f"zeroStreak should be 0 after 9 dispatches in same message, got: {r.get('zeroStreakBefore')}"
    )
    assert "CONFIGURED MINIMUM BLOCK" in r.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════════
# DIVERSITY ENFORCEMENT — topic concentration detection
# ═══════════════════════════════════════════════════════════════════════════════


def _make_diversity_workspace(path: Path) -> None:
    """Workspace with >=2 unchecked items for diversity check."""
    (path / "TASKS.md").write_text(
        "- [ ] guardrail enforcement plugin hardening\n"
        "- [ ] enforcement stop hook fix\n"
        "- [ ] agent floor enforcement bug\n"
    )
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)


def test_diversity_check_fires_when_wave_is_single_topic(tmp_path):
    """2 dispatches both classified as same topic (guardrails/enforcement)
    with >=2 unchecked items → TOPIC DIVERSITY VIOLATION fires."""
    ws = tmp_path / "diversity-fire"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add enforcement hook for the agent floor guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Diversity check should deny when 2/2 dispatches are same topic, got: {r}"
    )
    assert "TOPIC DIVERSITY VIOLATION" in r.get("message", "")


def test_diversity_allows_diverse_wave(tmp_path):
    """1 enforcement dispatch + 1 testing dispatch = diverse wave (50% each)
    with >=2 unchecked items → NO denial (below 80% threshold)."""
    ws = tmp_path / "diversity-allow"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write pytest unit test for coverage'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Diverse wave (2 topics) should not trigger diversity denial, got: {r}"
    )


def test_diversity_skipped_when_no_unchecked_items(tmp_path):
    """0 unchecked items in TASKS.md → diversity check does not fire
    even when 100% of dispatches are same topic."""
    ws = tmp_path / "diversity-no-items"
    ws.mkdir()
    _make_clean_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add enforcement hook for the agent floor guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"No unchecked items should allow wave, got: {r}"


def test_diversity_skipped_with_single_dispatch(tmp_path):
    """Only 1 dispatch in wave → diversity check requires waveSize >= 2."""
    ws = tmp_path / "diversity-single"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Single dispatch should not trigger diversity check, got: {r}"
    )


def test_diversity_env_disable(tmp_path):
    """GLUDD_MULTITASK_DIVERSITY_ENFORCE=0 disables diversity check."""
    ws = tmp_path / "diversity-env-off"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add enforcement hook for the agent floor guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={**_GAP_ENV, "GLUDD_MULTITASK_DIVERSITY_ENFORCE": "0"},
        cwd=str(ws),
    )
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Env disable should skip diversity check, got: {r}"


def test_diversity_subagent_skips(tmp_path):
    """OPENCODE_SUBAGENT=1 skips diversity enforcement."""
    ws = tmp_path / "diversity-subagent"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add enforcement hook for the agent floor guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(
        code,
        env_override={**_GAP_ENV, "OPENCODE_SUBAGENT": "1"},
        cwd=str(ws),
    )
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Subagent should skip diversity check, got: {r}"


def test_diversity_deny_message_includes_details(tmp_path):
    """Deny message includes topic name, percentage, and in_progress count."""
    ws = tmp_path / "diversity-msg"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add enforcement hook for the agent floor guardrail'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    msg = r.get("message", "")
    assert "TOPIC DIVERSITY VIOLATION" in msg
    assert "100%" in msg
    assert "2/2" in msg
    assert "in_progress" in msg.lower()


def test_diversity_wave_topic_counts_reset_on_boundary(tmp_path):
    """After a message boundary, waveTopicCounts resets and new wave is not
    affected by previous wave's topic distribution."""
    ws = tmp_path / "diversity-boundary"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix the enforce-multitask enforcement plugin guardrail'}}}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write pytest test for coverage'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add new type-safety check'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"After boundary, old topic counts should be reset, got: {r}"
    )


def test_diversity_uncategorized_topic_tracking(tmp_path):
    """Dispatches with no matching topic keywords are classified as 'uncategorized'
    and 2/2 uncategorized dispatches DO trigger the diversity check."""
    ws = tmp_path / "diversity-uncat"
    ws.mkdir()
    _make_diversity_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'xyzzy frob the wibble'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'quux flarn the garble'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws), env_override=_GAP_ENV)
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"2/2 uncategorized dispatches should trigger diversity violation, got: {r}"
    )
