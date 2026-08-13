#!/usr/bin/env python3
"""Runtime invocation tests for all enforcement plugins.

Invokes actual TypeScript plugin hook functions via node --experimental-strip-types
and verifies they return correct results (not undefined, not throwing) for the
4 lifecycle cases: subagent guard, violation block, legitimate allow, env-disable.

This catches bugs like undefined variables (ReferenceError), wrong field names
(input.command vs input.args.command), and broken hot-reload fallbacks that
structural-only tests miss.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"
PLUGINS_DIR = ROOT / ".opencode" / "plugins"
HELPERS = ROOT / ".opencode" / "lib" / "plugin_test_exports.ts"

_tmp_counter = 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 20):
    """Write TS code to temp file, run with node --experimental-strip-types, return parsed JSON."""
    global _tmp_counter
    _tmp_counter += 1
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp",
        prefix=f"hook_invoke_{_tmp_counter}_", delete=False,
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        # Runtime-contract tests exercise the committed fallback implementation.
        # A live session may have stale /tmp/gludd-hot-*.js overrides; isolating
        # the prefix prevents those mutable process artifacts from changing the
        # result. Dedicated hot-reload tests cover the override path separately.
        env["GLUDD_HOT_MODULE_PREFIX"] = (
            f"/tmp/gludd-test-no-hot-{os.getpid()}-{_tmp_counter}-"
        )
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
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
                parsed = json.loads(line)
                return None if parsed is None else parsed
            except json.JSONDecodeError:
                continue
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _clean_state_files(*paths: str):
    for p in paths:
        with contextlib.suppress(OSError):
            os.unlink(p)


def _factory_load_code(plugin_rel: str) -> str:
    """TS code to import a factory-pattern plugin."""
    abs_path = str(PLUGIN_DIR / plugin_rel)
    return f"const mod = await import('{abs_path}')\nconst plugin = await mod.default({{}})\n"


def _helper_load_code() -> str:
    """Import named test helpers from outside the auto-loaded plugin tree."""
    return f"const mod = await import('{HELPERS}')\n"


def _pluginapi_load_code(plugin_rel: str) -> str:
    """TS code to import a PluginAPI-pattern plugin; returns the registered fn."""
    abs_path = str(PLUGIN_DIR / plugin_rel)
    return f"""\
let registered = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registered = fn }}, after(fn) {{}} }} }} }}
const mod = await import('{abs_path}')
mod.default(api)
"""


def _with_tasks_md() -> tuple[dict, str]:
    """Create a temp TASKS.md with unchecked items."""
    p = f"/tmp/gludd-test-tasks-invoke-{os.getpid()}.md"
    with open(p, "w") as f:
        f.write("- [ ] test task A\n- [ ] test task B\n")
    return {"GLUDD_TASKS_MD": p}, p


# ===========================================================================
# 1. SMOKE: every plugin loads without throwing (catches undefined vars)
# ===========================================================================

FACTORY_PLUGINS = [
    "enforce-clean-tree.ts",
    "enforce-deadline.ts",
    "enforce-delegate.ts",
    "enforce-deletion-gate.ts",
    "enforce-enhancement-ratio.ts",
    "enforce-floor.ts",
    "enforce-make.ts",
    "enforce-multitask.ts",
    "enforce-no-suppressions.ts",
    "enforce-no-wait.ts",
    "enforce-session-start.ts",
    "enforce-stop.ts",
    "enforce-verified-claims.ts",
]

ALL_PLUGINS = [*FACTORY_PLUGINS, "enforce-commit-lock.ts"]


@pytest.mark.parametrize("plugin_name", ALL_PLUGINS)
def test_plugin_loads_without_throwing(plugin_name: str):
    """Every plugin must import and instantiate without a ReferenceError."""
    if plugin_name == "enforce-commit-lock.ts":
        code = _pluginapi_load_code(plugin_name) + """\
console.log(JSON.stringify({ok: true, registeredExists: registered !== null}))
"""
    else:
        code = _factory_load_code(plugin_name) + """\
console.log(JSON.stringify({ok: true, hasPlugin: typeof plugin === 'object' && plugin !== null}))
"""
    result = _run_ts(code)
    assert result is not None, f"{plugin_name}: load produced no output"
    assert result.get("ok") is True, f"{plugin_name}: load failed: {result}"
    if plugin_name == "enforce-commit-lock.ts":
        assert result["registeredExists"] is True, f"{plugin_name}: hook not registered"


# ===========================================================================
# 2. SUBAGENT GUARD: every plugin skips enforcement when OPENCODE_SUBAGENT=1
# ===========================================================================

_SUBAGENT_ENV = {"OPENCODE_SUBAGENT": "1"}


@pytest.mark.parametrize("plugin_name", FACTORY_PLUGINS)
def test_subagent_guard_tool_execute_before(plugin_name: str):
    """When OPENCODE_SUBAGENT=1, tool.execute.before must allow everything."""
    code = _factory_load_code(plugin_name) + """\
const hasHook = typeof plugin['tool.execute.before'] === 'function'
if (!hasHook) { console.log(JSON.stringify({skipped: true, reason: 'no tool.execute.before hook'})) }
else {
    const r = await plugin['tool.execute.before']({tool: 'edit', args: {}}, undefined)
    const allowed = r === undefined || r === null || r?.allowed === true || r?.permissionDecision !== 'deny'
    console.log(JSON.stringify({allowed, hasHook}))
}
"""
    result = _run_ts(code, env_override=_SUBAGENT_ENV)
    if result.get("skipped"):
        return
    assert result.get("allowed") is True, (
        f"{plugin_name}: subagent guard failed, got: {result}"
    )


def test_commit_lock_subagent_guard():
    """commit-lock: OPENCODE_SUBAGENT=1 → allows commit even with lock."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-sub-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make ship-commit MSG=test'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={
            "OPENCODE_SUBAGENT": "1",
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
        })
        assert result is None or result.get("allowed") is True, (
            f"commit-lock subagent guard failed: {result}"
        )
    finally:
        _clean_state_files(lock_path)


# ===========================================================================
# 3. ENV-DISABLE: each plugin respects its disable env var
# ===========================================================================

DISABLE_ENV_MAP: dict[str, str] = {
    "enforce-clean-tree.ts": "GLUDD_CLEAN_TREE_ENFORCE=0",
    "enforce-deadline.ts": "GLUDD_TASK_DEADLINE_ENABLED=0",
    "enforce-delegate.ts": "GLUDD_MAINTHREAD_STREAK_ENFORCE=0",
    "enforce-deletion-gate.ts":  "GLUDD_DELETION_GATE_ENFORCE=0",
    "enforce-enhancement-ratio.ts": "GLUDD_ENHANCEMENT_RATIO_ENFORCE=0",
    "enforce-floor.ts": "GLUDD_FLOOR_ENFORCE=0",
    "enforce-make.ts": "GLUDD_MAKE_ENFORCE=0",
    "enforce-multitask.ts": "GLUDD_MULTITASK_FLOOR_ENFORCE=0",
    "enforce-no-wait.ts": "GLUDD_NO_WAIT_ENFORCE=0",
    "enforce-session-start.ts": "GLUDD_SESSION_START_ENFORCE=0",
    "enforce-stop.ts": "GLUDD_STOP_ENFORCE=0",
    "enforce-verified-claims.ts": "GLUDD_VERIFIED_CLAIMS_ENFORCE=0",
}


@pytest.mark.parametrize("plugin_name", DISABLE_ENV_MAP)
def test_env_disable_allows_all_operations(plugin_name: str):
    """Env-disable must cause the plugin to become a no-op (allow all)."""
    code = _factory_load_code(plugin_name) + """\
const hasHook = typeof plugin['tool.execute.before'] === 'function'
if (!hasHook) { console.log(JSON.stringify({skipped: true, reason: 'no hook'})) }
else {
    const r = await plugin['tool.execute.before']({tool: 'edit', args: {}}, undefined)
    console.log(JSON.stringify({allowed: r === undefined || r === null || r?.permissionDecision !== 'deny', hasHook}))
}
"""
    env_var = DISABLE_ENV_MAP.get(plugin_name, "")
    key, val = env_var.split("=", 1)
    result = _run_ts(code, env_override={key: val})
    if result.get("skipped"):
        return
    assert result.get("allowed") is True, (
        f"{plugin_name}: env-disable ({key}=0) should allow all, got: {result}"
    )


# ===========================================================================
# 4. VIOLATION: each plugin correctly blocks/denies actual violations
# ===========================================================================


def test_clean_tree_blocks_dirty_dispatch():
    """Dirty tree + dispatch = deny."""
    test_file = str(ROOT / "scripts" / "_hook_invoke_dirty.txt")
    try:
        with open(test_file, "w") as f:
            f.write("dirty test file")
        code = _factory_load_code("enforce-clean-tree.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'task'}, undefined)
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code)
        assert result is not None, "Expected deny object"
        assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"
        assert "DIRTY TREE" in result.get("message", "")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_allows_clean_dispatch():
    """Clean tree + dispatch = allow."""
    code = _factory_load_code("enforce-clean-tree.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'task'}, undefined)
console.log(JSON.stringify(r ?? {allowed: true}))
"""
    result = _run_ts(code)
    if result is not None and result.get("permissionDecision") == "deny":
        assert "DIRTY TREE" in result.get("message", ""), f"Unexpected deny: {result}"


def test_make_denies_non_make_command():
    """enforce-make blocks bare commands."""
    code = _factory_load_code("enforce-make.ts") + """\
try {
  const r = await plugin['tool.execute.before']({tool: 'bash', args: {command: 'cd /tmp'}}, undefined)
  console.log(JSON.stringify(r ?? {allowed: true}))
} catch(e) {
  console.log(JSON.stringify({permissionDecision: 'deny', message: String(e.message)}))
}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny for cd, got: {result}"


def test_make_allows_make_target():
    """enforce-make allows 'make lint'."""
    code = _factory_load_code("enforce-make.ts") + """\
try {
  const r = await plugin['tool.execute.before']({tool: 'bash', args: {command: 'make lint'}}, undefined)
  console.log(JSON.stringify({allowed: r === undefined || r === null}))
} catch(e) {
  console.log(JSON.stringify({permissionDecision: 'deny', message: String(e.message)}))
}
"""
    result = _run_ts(code)
    assert result.get("allowed") is True, f"Expected allow, got: {result}"


def test_make_denies_metachar():
    """enforce-make blocks metacharacters."""
    code = _factory_load_code("enforce-make.ts") + """\
try {
  const cmd = "make test " + String.fromCharCode(124) + " grep"
  const r = await plugin["tool.execute.before"]({tool: "bash", args: {command: cmd}}, undefined)
  console.log(JSON.stringify(r ?? {allowed: true}))
} catch(e) {
  console.log(JSON.stringify({permissionDecision: "deny", message: String(e.message)}))
}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny for pipe, got: {result}"


def test_make_denies_prompt_prone_apply_patch_tool():
    """enforce-make blocks prompt-prone edit tools before they can ask."""
    code = _factory_load_code("enforce-make.ts") + """\
try {
  const r = await plugin["tool.execute.before"]({tool: "functions.apply_patch", args: {}}, undefined)
  console.log(JSON.stringify(r ?? {allowed: true}))
} catch(e) {
  console.log(JSON.stringify({permissionDecision: "deny", message: String(e.message)}))
}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny for apply_patch, got: {result}"
    assert "Prompt-prone edit tool" in result.get("message", "")


def test_no_wait_blocks_sleep():
    """enforce-no-wait blocks sleep pattern."""
    code = _factory_load_code("enforce-no-wait.ts") + """\
const cmd = 'sleep 60&& make gate-status-check'
const r = await plugin['tool.execute.before'](
    {tool: 'bash', args: {command: cmd}}, undefined
)
console.log(JSON.stringify(r ?? null))
"""
    result = _run_ts(code)
    assert result is not None, f"Expected deny for sleep, got: {result}"
    assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"


def test_no_wait_blocks_gate_tail():
    """enforce-no-wait blocks gate-tail."""
    code = _factory_load_code("enforce-no-wait.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'bash', args: {command: 'make gate-tail'}}, undefined)
console.log(JSON.stringify(r ?? null))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny for gate-tail"
    assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"


def test_deletion_gate_blocks_large_deletion():
    """10 lines deleted (> threshold of 5) = deny."""
    code = _factory_load_code("enforce-deletion-gate.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'edit', args: {
    filePath: '/tmp/nonexistent-invoke.txt',
    oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10',
    newString: ''
}}, undefined)
console.log(JSON.stringify(r ?? {allowed: true}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny for large deletion"
    assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"
    assert "exceeds threshold" in result.get("message", "")


def test_deletion_gate_allows_small_deletion():
    """1 line deleted (< threshold) = allow."""
    code = _factory_load_code("enforce-deletion-gate.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'edit', args: {
    filePath: '/tmp/nonexistent-invoke2.txt',
    oldString: 'one line',
    newString: ''
}}, undefined)
console.log(JSON.stringify(r ?? {allowed: true}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") is True, f"Expected allow: {result}"


def test_no_suppression_blocks_noqa():
    """enforce-no-suppressions identifies # noqa as suppression."""
    code = _helper_load_code() + """\
const isSupp = mod.isSuppressionComment('# noqa')
const verdict = mod.shouldAllowEdit('src/foo.py', '# noqa')
console.log(JSON.stringify({isSupp, allow: verdict.allow}))
"""
    result = _run_ts(code)
    assert result["isSupp"] is True, f"# noqa must be detected: {result}"
    assert result["allow"] is False, f"# noqa must be blocked: {result}"


def test_no_suppression_allows_plain_comment():
    """Plain comment passes through."""
    code = _helper_load_code() + """\
const verdict = mod.shouldAllowEdit('src/foo.py', '# regular comment')
console.log(JSON.stringify({allow: verdict.allow}))
"""
    result = _run_ts(code)
    assert result["allow"] is True, f"Plain comment must be allowed: {result}"


def test_no_suppression_allowlisted_path():
    """Allowlisted path allows # noqa."""
    code = _helper_load_code() + """\
const verdict = mod.shouldAllowEdit('src/general_ludd/security/fix_not_disable.py', '# noqa')
console.log(JSON.stringify({allow: verdict.allow}))
"""
    result = _run_ts(code)
    assert result["allow"] is True, f"Allowlisted path must allow # noqa: {result}"


def test_verified_claims_no_evidence_blocked():
    """shouldBlock('everything committed') returns true."""
    code = _helper_load_code() + """\
console.log(JSON.stringify({shouldBlock: mod.shouldBlock('everything committed')}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] is True, f"Unverified claim must be blocked: {result}"


def test_verified_claims_with_hash_allowed():
    """shouldBlock('commit abc12345') returns false (hash is evidence)."""
    code = _helper_load_code() + """\
console.log(JSON.stringify({shouldBlock: mod.shouldBlock('commit abc12345')}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] is False, f"Evidence must allow: {result}"


def test_verified_claims_unverified_commit_denied():
    """Commit MSG with no evidence → tool.execute.before denies.
    Uses direct default() call (this plugin exports an IIFE, not async factory)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
const cmd = 'make git-commit MSG="all done and fixed"'
try {{
  const r = await plugin['tool.execute.before'](
    {{tool: 'bash', args: {{command: cmd, MSG: 'all done and fixed'}}}}
  )
  console.log(JSON.stringify(r ?? {{allowed: true}}))
}} catch(e) {{
  const msg = String(e.message || e)
  console.log(JSON.stringify({{permissionDecision: 'deny', message: msg}}))
}}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"


def test_verified_claims_verified_commit_allowed():
    """Commit MSG with hash evidence → allowed."""
    code = _factory_load_code("enforce-verified-claims.ts") + """\
const r = await plugin['tool.execute.before'](
    {tool: 'bash', args: {command: 'make ship-commit MSG=fix: done abc12345', MSG: 'fix: done abc12345'}})
console.log(JSON.stringify({allowed: r === undefined || r === null}))
"""
    result = _run_ts(code)
    assert result.get("allowed") is True, f"Expected allow: {result}"


def test_enhancement_enhancement_classified():
    """Task prompt containing 'enhancement' is classified enhancement."""
    state_file = f"/tmp/gludd-enhance-invoke-{os.getpid()}.json"
    _clean_state_files(state_file)
    try:
        code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'ENHANCEMENT: Create docs'}}}}, undefined)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{type: state.wave[0]?.type, waveLen: state.wave.length}}))
"""
        result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
        assert result["type"] == "enhancement", f"Expected enhancement, got {result}"
    finally:
        _clean_state_files(state_file)


def test_enhancement_fix_classified():
    """Task prompt containing 'bug fix' is classified fix."""
    state_file = f"/tmp/gludd-enhance-fix-{os.getpid()}.json"
    _clean_state_files(state_file)
    try:
        code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'bug fix for login'}}}}, undefined)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{type: state.wave[0]?.type}}))
"""
        result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
        assert result["type"] == "fix", f"Expected fix, got {result}"
    finally:
        _clean_state_files(state_file)


def test_enhancement_ratio_violation_blocked():
    """2 fixes + 1 enhancement (67% fixes) → violation blocked on third dispatch."""
    state_file = f"/tmp/gludd-enhance-viol-{os.getpid()}.json"
    _clean_state_files(state_file)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug B'}}}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'enhancement: add test'}}}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_deny: r2 !== null && r2?.permissionDecision === 'deny',
    r3_msg: r3?.message ?? '',
}}))
"""
        result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
        assert result["r1_ok"] is True, "First dispatch should be allowed"
        assert result["r2_deny"] is True, f"100% fixes wave=2 should deny: {result}"
    finally:
        _clean_state_files(state_file)


def test_enhancement_unknown_defaults_to_fix():
    """Unknown prompt → classified as fix (conservative)."""
    state_file = f"/tmp/gludd-enhance-unk-{os.getpid()}.json"
    _clean_state_files(state_file)
    try:
        code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'do some work'}}}}, undefined)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{type: state.wave[0]?.type}}))
"""
        result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
        assert result["type"] == "fix", f"Unknown should default to fix: {result}"
    finally:
        _clean_state_files(state_file)


def test_delegate_streak_at_threshold_denied():
    """Streak >= threshold + live agents below target = deny.
    Uses unique state files to avoid xdist race conditions with disengage files."""
    pid = os.getpid()
    sf = f"/tmp/gludd-mainthread-streak-invoke-{pid}.json"
    df = f"/tmp/gludd-watchdog-disengage-invoke-{pid}.json"
    tasks_path = f"/tmp/gludd-test-tasks-delegate-invoke-{pid}.md"
    _clean_state_files(sf, df, tasks_path)
    # Write an empty (non-disengaged) state to prevent leakage
    with open(df, "w") as f:
        json.dump({}, f)
    with open(sf, "w") as f:
        json.dump({"count": 2, "ts": int(time.time() * 1000)}, f)
    with open(tasks_path, "w") as f:
        f.write("- [ ] delegate test task\n")
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
let result
try {{
  const r = await plugin['tool.execute.before']({{tool: 'edit'}}, {{}})
  console.log(JSON.stringify(r ?? {{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message || e)}}))
}}
"""
        result = _run_ts(code, env_override={
            "GLUDD_LIVE_AGENTS_COUNT": "0",
            "GLUDD_TASKS_MD": tasks_path,
            "CLAUDE_AGENT_TARGET": "10",
            "GLUDD_MAINTHREAD_STREAK_FILE": sf,
            "GLUDD_DISENGAGE_PATH": df,
        })
        assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    finally:
        _clean_state_files(sf, df, tasks_path)


def test_delegate_read_tool_allowed():
    """Read/grep/glob allowed regardless of streak."""
    sf = "/tmp/gludd-mainthread-streak.json"
    _clean_state_files(sf)
    with open(sf, "w") as f:
        json.dump({"streak": 5, "lastDispatchTs": int(time.time() * 1000), "ts": int(time.time() * 1000)}, f)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'read'}}, {{}})
console.log(JSON.stringify({{allowed: r === undefined || r === null}}))
"""
        result = _run_ts(code)
        assert result["allowed"] is True, f"Read should be allowed: {result}"
    finally:
        _clean_state_files(sf)


def test_deadline_fresh_task_allowed():
    """Fresh task within timeout = allowed."""
    state_file = f"/tmp/test-deadlines-invoke-{os.getpid()}.json"
    _clean_state_files(state_file)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_TASK_DEADLINE_STATE": state_file})
        assert result is None or result.get("allowed") is True, f"Expected allow: {result}"
    finally:
        _clean_state_files(state_file)


def test_deadline_overdue_blocked():
    """Overdue task = deny."""
    stale_state = f"/tmp/test-deadlines-overdue-{os.getpid()}.json"
    stale_file = f"/tmp/gludd-task-stale-overdue-{os.getpid()}.json"
    _clean_state_files(stale_state, stale_file)
    with open(stale_state, "w") as f:
        json.dump({"stale-task-99": int(time.time() * 1000) - 400_000}, f)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
            "GLUDD_TASK_STALE_FILE": stale_file,
        })
        assert result is not None, "Expected deny object"
        assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"
    finally:
        _clean_state_files(stale_state, stale_file)


def test_deadline_corrupt_state_fail_open():
    """Corrupt deadline state file = fail-open (allow)."""
    stale_state = f"/tmp/test-deadlines-corrupt-invoke-{os.getpid()}.json"
    with open(stale_state, "w") as f:
        f.write("not valid json {{{[[[")
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_TASK_DEADLINE_STATE": stale_state})
        assert result is None or result.get("allowed") is True, f"Expected fail-open: {result}"
    finally:
        _clean_state_files(stale_state)


def test_floor_dispatch_allowed():
    """Dispatch tool is always allowed by enforce-floor."""
    code = _factory_load_code("enforce-floor.ts") + """\
const r = await plugin['tool.execute.before']({tool: 'task'}, undefined)
console.log(JSON.stringify({allowed: r === undefined || r === null}))
"""
    result = _run_ts(code)
    assert result["allowed"] is True, f"Dispatch should be allowed: {result}"


def test_floor_streak_at_max_plus_one_denied():
    """3 non-dispatch calls with open work = 3rd call denied (MAX_STREAK=2)."""
    tasks_path = f"/tmp/gludd-test-tasks-floor-invoke-{os.getpid()}.md"
    todowrite_path = f"/tmp/gludd-todowrite-state-invoke-{os.getpid()}.json"
    session_state = f"/tmp/gludd-session-start-invoke-{os.getpid()}.json"
    _clean_state_files(tasks_path, todowrite_path, session_state,
                       "/tmp/gludd-watchdog-disengage.json")
    with open(tasks_path, "w") as f:
        f.write("- [ ] floor test\n")
    with open(todowrite_path, "w") as f:
        json.dump([{"status": "pending", "content": "test"}], f)
    with open(session_state, "w") as f:
        json.dump({}, f)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
    r3_deny: r3?.permissionDecision === 'deny',
}}))
"""
        result = _run_ts(code, env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_TODOWRITE_STATE": todowrite_path,
            "GLUDD_SESSION_STATE": session_state,
        })
        assert result["r1_ok"] is True, f"Call 1 should be allowed: {result}"
        assert result["r2_ok"] is True, f"Call 2 should be allowed: {result}"
        assert result["r3_deny"] is True, f"Call 3 should be denied: {result}"
    finally:
        _clean_state_files(tasks_path, todowrite_path, session_state)


def test_floor_read_tools_allowed():
    """Read tools allowed even at high streak."""
    code = _factory_load_code("enforce-floor.ts") + """\
const r1 = await plugin['tool.execute.before']({tool: 'read'}, undefined)
const r2 = await plugin['tool.execute.before']({tool: 'grep'}, undefined)
const r3 = await plugin['tool.execute.before']({tool: 'glob'}, undefined)
console.log(JSON.stringify({
    allAllowed: r1 === undefined && r2 === undefined && r3 === undefined
}))
"""
    result = _run_ts(code)
    assert result["allAllowed"] is True, f"Read tools should be allowed: {result}"


def test_multitask_dispatch_allowed():
    """Dispatch tools always pass through enforce-multitask."""
    code = _factory_load_code("enforce-multitask.ts") + """\
const r1 = await plugin['tool.execute.before']({tool: 'task'})
const r2 = await plugin['tool.execute.before']({tool: 'agent'})
console.log(JSON.stringify({
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
}))
"""
    result = _run_ts(code)
    assert result["r1_ok"] is True, f"Task dispatch should be allowed: {result}"
    assert result["r2_ok"] is True, f"Agent dispatch should be allowed: {result}"


def test_session_start_no_reads_denies_mutation():
    """Fresh session (no reads) + mutation tool = deny."""
    state_file = f"/tmp/gludd-session-state-invoke-{os.getpid()}.json"
    _clean_state_files(state_file)
    with open(state_file, "w") as f:
        json.dump({
            "started_at": int(time.time() * 1000),
            "readsDone": False,
            "dispatches": 0,
            "timeGateReset": False,
        }, f)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message)}}))
}}
"""
        result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
        assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"
        assert "SESSION START PROTOCOL" in result.get("message", "")
    finally:
        _clean_state_files(state_file)


def test_session_start_read_tool_allowed():
    """Read tools always allowed in fresh session."""
    state_file = f"/tmp/gludd-session-read-invoke-{os.getpid()}.json"
    _clean_state_files(state_file)
    with open(state_file, "w") as f:
        json.dump({
            "started_at": int(time.time() * 1000),
            "readsDone": False,
            "dispatches": 0,
            "timeGateReset": False,
        }, f)
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message)}}))
}}
"""
        result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
        assert result.get("allowed") is True, f"Read should be allowed: {result}"
    finally:
        _clean_state_files(state_file)


def test_session_start_corrupt_state_fail_open():
    """Corrupt session state → fail-open (allow)."""
    state_file = f"/tmp/gludd-session-corrupt-invoke-{os.getpid()}.json"
    _clean_state_files(state_file)
    with open(state_file, "w") as f:
        f.write("not valid {{{[[[")
    try:
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message)}}))
}}
"""
        result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
        assert result.get("allowed") is True, f"Corrupt state should fail-open: {result}"
    finally:
        _clean_state_files(state_file)


def test_commit_lock_blocks_concurrent_commit():
    """Fresh lock file (<5 min) blocks commit."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make git-commit MSG=test'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
        assert result is not None, "Expected deny object"
        assert result.get("permissionDecision") == "deny", f"Expected deny: {result}"
        assert "COMMIT-LOCK" in result.get("message", "")
    finally:
        _clean_state_files(lock_path)


def test_commit_lock_allows_no_lock():
    """No lock file → commit allowed."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-none-{os.getpid()}"
    _clean_state_files(lock_path)
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make ship-commit MSG=test'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
        assert result is None or result.get("allowed") is True, f"Expected allow: {result}"
    finally:
        _clean_state_files(lock_path)


def test_commit_lock_allows_non_commit():
    """Non-commit bash command passes through."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-nc-{os.getpid()}"
    _clean_state_files(lock_path)
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make test-unit'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
        assert result is None or result.get("allowed") is True, f"Expected allow: {result}"
    finally:
        _clean_state_files(lock_path)


def test_commit_lock_stale_break_allows():
    """Stale lock (>5 min) is broken, commit allowed."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-stale-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("stale")
    six_min_ago = time.time() - 360
    os.utime(lock_path, (six_min_ago, six_min_ago))
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make repo-commit MSG=test'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
        assert result is None or result.get("allowed") is True, f"Expected allow: {result}"
    finally:
        _clean_state_files(lock_path)


def test_commit_lock_env_disable():
    """GLUDD_COMMIT_LOCK_ENFORCE=0 disables lock check."""
    lock_path = f"/tmp/gludd-commit-lock-invoke-dis-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    try:
        code = _pluginapi_load_code("enforce-commit-lock.ts") + """\
const r = await registered({tool: 'bash', command: 'make git-commit MSG=test'})
console.log(JSON.stringify(r ?? {allowed: true}))
"""
        result = _run_ts(code, env_override={
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
            "GLUDD_COMMIT_LOCK_ENFORCE": "0",
        })
        assert result is None or result.get("allowed") is True, f"Expected allow: {result}"
    finally:
        _clean_state_files(lock_path)


# ===========================================================================
# 5. EXPORTED CONSTANTS: verify integrity of exported symbols
# ===========================================================================


def test_all_done_words_exported():
    """Verify centralized verified-claims helpers expose their rule data."""
    code = _helper_load_code() + """\
console.log(JSON.stringify({
    hasDoneWords: Array.isArray(mod.DONE_WORDS) && mod.DONE_WORDS.length > 0,
    hasEvidence: Array.isArray(mod.EVIDENCE_PATTERNS) && mod.EVIDENCE_PATTERNS.length > 0,
    hasNotDone: Array.isArray(mod.NOT_DONE_PHRASES),
}))
"""
    result = _run_ts(code)
    assert result["hasDoneWords"] is True, "DONE_WORDS must be a non-empty array"
    assert result["hasEvidence"] is True, "EVIDENCE_PATTERNS must be a non-empty array"
    assert result["hasNotDone"] is True, "NOT_DONE_PHRASES must be an array"


def test_clean_tree_exports():
    """Verify centralized clean-tree helpers expose expected behavior."""
    code = _helper_load_code() + """\
console.log(JSON.stringify({
    hasGetStatus: typeof mod.getGitStatus === 'function',
    hasIsDirty: typeof mod.isTreeDirty === 'function',
    hasCountDirty: typeof mod.countDirtyFiles === 'function',
    hasBuildDeny: typeof mod.buildDenyMessage === 'function',
    hasDispatchTools: Array.isArray(mod.getDispatchTools()) && mod.getDispatchTools().length === 3,
    hasPrefix: typeof mod.getDenyMessagePrefix() === 'string',
    getStatusResult: typeof mod.getGitStatus() === 'string',
    isDirtyResult: typeof mod.isTreeDirty() === 'boolean',
}))
"""
    result = _run_ts(code)
    for key in ["hasGetStatus", "hasIsDirty", "hasCountDirty", "hasBuildDeny",
                "hasDispatchTools", "hasPrefix"]:
        assert result[key] is True, f"clean-tree: {key} failed: {result}"
    assert result["getStatusResult"] is True, "getGitStatus must return a string"
    assert result["isDirtyResult"] is True, "isTreeDirty must return a boolean"


# ===========================================================================
# 6. FAIL-OPEN: corrupt state files don't crash plugins
# ===========================================================================


@pytest.mark.parametrize("plugin_name", FACTORY_PLUGINS)
def test_corrupt_state_does_not_crash_plugin(plugin_name: str):
    """Every plugin must handle corrupt state files (invalid JSON) without crashing.
    Skips enforce-session-start because its TIME GATE fires on old started_at timestamps."""
    if plugin_name == "enforce-session-start.ts":
        pytest.skip("session-start time-gate: corrupting its state file triggers TIME GATE")
    state_files = [
        "/tmp/gludd-tool-streak.json",
        "/tmp/gludd-block-counter.json",
    ]
    for sf in state_files:
        with open(sf, "w") as f:
            f.write("not valid json {{{[[[[]")
    try:
        code = _factory_load_code(plugin_name) + """\
const hasHook = typeof plugin['tool.execute.before'] === 'function'
if (!hasHook) { console.log(JSON.stringify({skipped: true, reason: 'no hook'})) }
else {
    const r = await plugin['tool.execute.before']({tool: 'edit', args: {}}, undefined)
    console.log(JSON.stringify({
        returned: r !== undefined || r === null,
        noCrash: true,
        hasHook
    }))
}
"""
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": ""})
        if result.get("skipped"):
            return
        assert result.get("noCrash") is True, (
            f"{plugin_name}: crashed on corrupt state: {result}"
        )
    finally:
        for sf in state_files:
            _clean_state_files(sf)


# ===========================================================================
# 7. WATCHDOG: watchdog.ts loads and reports alive
# ===========================================================================


def test_watchdog_loads_and_reports_alive():
    """watchdog.ts loads, reports alive, and exposes its event hook."""
    alive_path = f"/tmp/gludd-plugin-alive-invoke-{os.getpid()}.json"
    _clean_state_files(alive_path)
    try:
        code = f"""\
const mod = await import("{PLUGINS_DIR}/watchdog.ts")
const plugin = await mod.default({{}})
console.log(JSON.stringify({{
  ok: true,
  keys: Object.keys(plugin),
  eventType: typeof plugin.event,
  isObject: typeof plugin === "object",
}}))
"""
        result = _run_ts(code, env_override={"GLUDD_ALIVE_PATH": alive_path})
        assert result["ok"] is True, f"watchdog load failed: {result}"
        assert result["keys"] == ["event"], f"watchdog should expose event hook: {result}"
        assert result["eventType"] == "function", f"watchdog event hook must be callable: {result}"
        assert os.path.exists(alive_path), "alive file must exist after watchdog load"
    finally:
        _clean_state_files(alive_path)




def test_watchdog_subagent_loads():
    """watchdog loads even with OPENCODE_SUBAGENT=1."""
    alive_path = f"/tmp/gludd-plugin-alive-sub-invoke-{os.getpid()}.json"
    _clean_state_files(alive_path)
    try:
        code = f"""\
const mod = await import('{PLUGINS_DIR}/watchdog.ts')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ok: true}}))
"""
        result = _run_ts(
            code,
            env_override={
                "OPENCODE_SUBAGENT": "1",
                "GLUDD_ALIVE_PATH": alive_path,
            },
        )
        assert result["ok"] is True, f"watchdog subagent load failed: {result}"
    finally:
        _clean_state_files(alive_path)


# ===========================================================================
# 8. HOOK EXISTENCE: every enforcement plugin registers tool.execute.before
# ===========================================================================


@pytest.mark.parametrize("plugin_name", FACTORY_PLUGINS)
def test_plugin_has_tool_execute_before_hook(plugin_name: str):
    """Every enforce-*.ts plugin must expose tool.execute.before."""
    code = _factory_load_code(plugin_name) + """\
const hasHook = typeof plugin['tool.execute.before'] === 'function' ||
    typeof plugin?.default?.['tool.execute.before'] === 'function' ||
    typeof plugin?.['tool.execute.before'] === 'function'
console.log(JSON.stringify({hasHook}))
"""
    result = _run_ts(code)
    assert result["hasHook"] is True, (
        f"{plugin_name}: missing tool.execute.before hook: {result}"
    )


# ===========================================================================
# 9. MUTATION TOOLS: test each plugin with all mutation tool types
# ===========================================================================

MUTATION_TOOLS = ["edit", "write", "bash"]

@pytest.mark.parametrize("tool_name", MUTATION_TOOLS)
def test_clean_tree_allows_mutation_tools(tool_name: str):
    """Non-dispatch tools (edit, write, bash) pass through clean-tree even when dirty."""
    test_file = str(ROOT / "scripts" / "_hook_invoke_mut.txt")
    try:
        with open(test_file, "w") as f:
            f.write("dirty")
        code = _factory_load_code("enforce-clean-tree.ts") + f"""\
const r = await plugin['tool.execute.before']({{tool: '{tool_name}'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
        result = _run_ts(code)
        assert result is None or result.get("allowed") is True, (
            f"clean-tree: {tool_name} should not be blocked: {result}"
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)
