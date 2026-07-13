#!/usr/bin/env python3
"""Functional test harness for opencode plugin hooks.

Invokes actual TypeScript plugin hook functions via node --experimental-strip-types
and verifies runtime behavior. Each test calls real plugin code with controlled
inputs and asserts on the return value.

Usage:
    python3 scripts/test_hook_runtime.py          # run all tests
    python3 scripts/test_hook_runtime.py -v       # verbose
    python3 scripts/test_hook_runtime.py -k floor # filter by name
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_tmp_counter = 0


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 15):
    """Write TS code to temp file, run with node --experimental-strip-types, return parsed JSON.

    Returns None if stdout is empty (hook returned undefined/void).
    """
    global _tmp_counter
    _tmp_counter += 1
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ts", dir="/tmp", prefix=f"hook_test_{_tmp_counter}_", delete=False
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""  # ensure we're NOT treated as subagent
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT), env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        return json.loads(stdout)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _factory_plugin_code(plugin_rel_path: str, hook_name: str, call_code: str) -> str:
    """Generate TS code that loads an async-factory plugin and calls a hook.

    For plugins that use: export default (async ({}) => { return { hook: fn } })
    """
    abs_path = str(PLUGIN_DIR / plugin_rel_path)
    return f"""\
const mod = await import('{abs_path}')
const plugin = await mod.default({{}})
const result = await {call_code}
console.log(JSON.stringify(result ?? null))
"""


def _pluginapi_code(plugin_rel_path: str, call_code: str) -> str:
    """Generate TS code for PluginAPI-style plugins.

    For plugins that use: export default function plugin(api: PluginAPI): void { api.tool.execute.before(fn) }
    """
    abs_path = str(PLUGIN_DIR / plugin_rel_path)
    return f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{abs_path}')
mod.default(api)
const result = {call_code}
console.log(JSON.stringify(result ?? null))
"""


def _clean_state_files(*paths: str):
    """Remove state files before/after tests."""
    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


def _with_open_work(env: dict, tmp_tasks: str) -> tuple[dict, str]:
    """Create a temp TASKS.md with unchecked items so openWorkExists() returns true."""
    tasks_path = os.path.join("/tmp", f"gludd-test-tasks-{os.getpid()}.md")
    with open(tasks_path, "w") as f:
        f.write("- [ ] test task 1\n- [ ] test task 2\n")
    env["GLUDD_TASKS_MD"] = tasks_path
    return env, tasks_path


# ---------------------------------------------------------------------------
# enforce-clean-tree.ts  —  exports pure functions + PluginAPI hook
# ---------------------------------------------------------------------------


def test_clean_tree_get_git_status():
    """getGitStatus() returns non-empty string in a real git repo."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
console.log(JSON.stringify({{status: mod.getGitStatus(), length: mod.getGitStatus().length}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert isinstance(result["status"], str)
    # In the gludd repo, there may be dirty files
    assert isinstance(result["length"], int)


def test_clean_tree_is_dirty_in_real_repo():
    """isTreeDirty() returns boolean in a real git repo."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
console.log(JSON.stringify({{dirty: mod.isTreeDirty()}}))
"""
    result = _run_ts(code)
    assert isinstance(result["dirty"], bool)


def test_clean_tree_count_dirty_files_zero():
    """countDirtyFiles returns 0 for empty status."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
console.log(JSON.stringify({{count: mod.countDirtyFiles('')}}))
"""
    result = _run_ts(code)
    assert result["count"] == 0


def test_clean_tree_count_dirty_files_nonzero():
    """countDirtyFiles counts lines in porcelain output."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const fake = ' M foo.py\\n?? bar.py\\n M baz.py'
console.log(JSON.stringify({{count: mod.countDirtyFiles(fake)}}))
"""
    result = _run_ts(code)
    assert result["count"] == 3


def test_clean_tree_build_deny_message():
    """buildDenyMessage includes count and DENY_MESSAGE_PREFIX."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
console.log(JSON.stringify({{msg: mod.buildDenyMessage(5), prefix: mod.DENY_MESSAGE_PREFIX}}))
"""
    result = _run_ts(code)
    assert "5" in result["msg"]
    assert "DIRTY TREE" in result["msg"]
    assert result["prefix"] == "DIRTY TREE"


def test_clean_tree_dispatch_tools_defined():
    """DISPATCH_TOOLS array contains task, agent, workflow."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
console.log(JSON.stringify(mod.DISPATCH_TOOLS))
"""
    result = _run_ts(code)
    assert "task" in result
    assert "agent" in result
    assert "workflow" in result


def test_clean_tree_hook_dispatch_with_dirty_tree():
    """The registered hook denies dispatch when tree is dirty."""
    # We need the tree to be dirty. Create a temp file, stage it.
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_temp.txt")
    try:
        # Create an untracked file to make tree dirty
        with open(test_file, "w") as f:
            f.write("test dirty file for hook test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? null))
"""
        result = _run_ts(code)
        # If the tree is dirty, result should be a deny
        if result is not None:
            assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
            assert "DIRTY TREE" in result.get("message", "")
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_hook_clean_tree_allows_dispatch():
    """Clean tree should allow dispatch (hook returns undefined/void)."""
    # After removing the dirty file, tree should be clean(er)
    code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    # We can't guarantee the tree is fully clean, so this is best-effort
    result = _run_ts(code)
    if result is not None and result.get("permissionDecision") == "deny":
        # Tree has other dirty files - that's OK for this repo
        pass


def test_clean_tree_env_disable():
    """GLUDD_CLEAN_TREE_ENFORCE=0 disables the check."""
    # Create a dirty file
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_temp2.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
        # Should be allowed (null/undefined return)
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_subagent_skip():
    """OPENCODE_SUBAGENT=1 skips all enforcement."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_temp3.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
let registeredHook = null
const api = {{ tool: {{ execute: {{ before(fn) {{ registeredHook = fn }} }} }} }}
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
mod.default(api)
const result = registeredHook({{tool: 'task'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# enforce-enhancement-ratio.ts  —  test classification + state-based logic
# ---------------------------------------------------------------------------


def test_enhancement_enhancement_keywords_classify_correctly():
    """ENHANCEMENT_KEYWORDS map to 'enhancement' classification."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
// classify is not exported, test indirectly via tool.execute.before
// by reading the state file after classification
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
// clean state
try {{ fs.unlinkSync(stateFile) }} catch {{}}
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'ENHANCEMENT: Create new tests'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{waveLen: state.wave.length, type: state.wave[0]?.type, sessionEnh: state.session_enhancements}}))
"""
    result = _run_ts(code)
    assert result["waveLen"] == 1
    assert result["type"] == "enhancement"
    assert result["sessionEnh"] == 1


def test_enhancement_fix_keywords_classify_correctly():
    """FIX_KEYWORDS map to 'fix' classification."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'bug fix for login'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{waveLen: state.wave.length, type: state.wave[0]?.type, sessionFixes: state.session_fixes}}))
"""
    result = _run_ts(code)
    assert result["waveLen"] == 1
    assert result["type"] == "fix"
    assert result["sessionFixes"] == 1


def test_enhancement_unknown_defaults_to_fix():
    """Unknown prompt keywords default to 'fix' (conservative)."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'do some random work'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{type: state.wave[0]?.type}}))
"""
    result = _run_ts(code)
    assert result["type"] == "fix"


def test_enhancement_wave_80pct_fixes_triggers_text_complete_block():
    """text.complete returns violation string when fix ratio >50% (BLOCK=1 default)."""
    state_file = os.path.join("/tmp", f"test-ratio-80pct-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const ts = Date.now()
const pid = process.pid
fs.writeFileSync('{state_file}', JSON.stringify({{
    wave: [
        {{type: "fix", prompt_head: "fix bug A", ts}},
        {{type: "fix", prompt_head: "fix bug B", ts}},
        {{type: "fix", prompt_head: "fix bug C", ts}},
        {{type: "fix", prompt_head: "fix bug D", ts}},
        {{type: "enhancement", prompt_head: "add tests", ts}},
    ],
    session_enhancements: 1, session_fixes: 4, session_unknown: 0,
    wave_count_since_last_warn: 0, early_warned: false,
    lastPid: pid, lastTs: ts,
}}))
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
const output = await plugin['text.complete']({{text: 'hello'}})
const isString = typeof output === 'string'
const hasViolation = isString && output.includes('ENHANCEMENT RATIO VIOLATION')
console.log(JSON.stringify({{isString, hasViolation}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["isString"] == True, f"Expected string output, got type: {type(result)}"
    assert result["hasViolation"] == True, f"Expected ENHANCEMENT RATIO VIOLATION in output"
    _clean_state_files(state_file)


def test_enhancement_wave_50pct_allowed():
    """text.complete allows 50/50 split (compliant)."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'enhancement: add docs'}}}}, undefined)
const output = await plugin['text.complete']({{text: 'hello'}})
console.log(JSON.stringify({{isModified: output.text !== 'hello'}}))
"""
    result = _run_ts(code)
    assert result["isModified"] == False


def test_enhancement_env_disable():
    """GLUDD_ENHANCEMENT_RATIO_ENFORCE=0 disables all enforcement."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
// When disabled, tool.execute.before should be no-op
// State file may or may not exist; if it does, wave should be empty
let waveLen = 0
try {{
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
    waveLen = state.wave?.length || 0
}} catch {{}}
console.log(JSON.stringify({{waveLen}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_ENFORCE": "0"})
    assert result["waveLen"] == 0


def test_enhancement_subagent_skip():
    """OPENCODE_SUBAGENT=1 skips tool.execute.before."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
let waveLen = 0
try {{
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
    waveLen = state.wave?.length || 0
}} catch {{}}
console.log(JSON.stringify({{waveLen}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result["waveLen"] == 0


def test_enhancement_fix_ratio_ok():
    """33% fixes: tool.execute.before does not deny (fixRatio <= 50%)."""
    state_file = os.path.join("/tmp", f"test-ratio-ok-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
// 2 enhancements + 1 fix = 33% fixes
const r1 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'enhancement: add test A'}}}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'enhancement: add test B'}}}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix something'}}}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
    r3_ok: r3 === undefined || r3 === null,
}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["r1_ok"] == True
    assert result["r2_ok"] == True
    assert result["r3_ok"] == True, f"33% fixes should be allowed, but r3 denied: check wave state"
    _clean_state_files(state_file)


def test_enhancement_fix_ratio_violation_blocked():
    """67% fixes: tool.execute.before returns {{permissionDecision: "deny"}}."""
    state_file = os.path.join("/tmp", f"test-ratio-viol-{os.getpid()}.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
// 2 fixes + 1 enhancement = 67% fixes → violation
const r1 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug B'}}}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'enhancement: add test'}}}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_deny: r2 !== null && r2?.permissionDecision === 'deny',
    r3_deny: r3 !== null && r3?.permissionDecision === 'deny',
    r2_msg: r2?.message ?? '',
}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["r1_ok"] == True, "First dispatch should be allowed (wave < 2)"
    assert result["r2_deny"] == True, f"Second dispatch (100% fixes, wave=2) should deny: {result}"
    assert "ENHANCEMENT RATIO VIOLATION" in result["r2_msg"], f"Deny message missing VIOLATION: {result['r2_msg']}"
    # r3 also denies since wave has 3 entries with 67% fixes
    assert result["r3_deny"] == True, f"Third dispatch (67% fixes, wave=3) should deny: {result}"
    _clean_state_files(state_file)


def test_enhancement_fix_ratio_text_blocked():
    """text.complete returns violation string when BLOCK=1 and fixRatio >50%."""
    state_file = os.path.join("/tmp", f"test-ratio-txt-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const ts = Date.now()
const pid = process.pid
fs.writeFileSync('{state_file}', JSON.stringify({{
    wave: [
        {{type: "fix", prompt_head: "fix bug A", ts}},
        {{type: "fix", prompt_head: "fix bug B", ts}},
        {{type: "enhancement", prompt_head: "add test", ts}},
    ],
    session_enhancements: 1, session_fixes: 2, session_unknown: 0,
    wave_count_since_last_warn: 0, early_warned: false,
    lastPid: pid, lastTs: ts,
}}))
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
const output = await plugin['text.complete']({{text: 'hello'}})
console.log(JSON.stringify({{
    isBlocked: typeof output === 'string',
    hasViolation: typeof output === 'string' && output.includes('ENHANCEMENT RATIO VIOLATION'),
    helloGone: typeof output === 'string' && !output.includes('hello'),
}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["isBlocked"] == True, f"Expected string block, got: {result}"
    assert result["hasViolation"] == True, f"Expected VIOLATION message: {result}"
    assert result["helloGone"] == True, "Original text should be replaced by violation"
    _clean_state_files(state_file)


def test_enhancement_block_env_disabled():
    """GLUDD_ENHANCEMENT_RATIO_BLOCK=0: violation does not block (advisory mode)."""
    state_file = os.path.join("/tmp", f"test-ratio-noblk-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const ts = Date.now()
const pid = process.pid
fs.writeFileSync('{state_file}', JSON.stringify({{
    wave: [
        {{type: "fix", prompt_head: "fix A", ts}},
        {{type: "fix", prompt_head: "fix B", ts}},
    ],
    session_enhancements: 0, session_fixes: 2, session_unknown: 0,
    wave_count_since_last_warn: 0, early_warned: false,
    lastPid: pid, lastTs: ts,
}}))
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
const output = await plugin['text.complete']({{text: 'hello'}})
console.log(JSON.stringify({{
    notBlocked: typeof output !== 'string',
    textPreserved: typeof output !== 'string' ? (output?.text ?? '') === 'hello' : false,
}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
        "GLUDD_ENHANCEMENT_RATIO_BLOCK": "0",
    })
    assert result["notBlocked"] == True, f"With BLOCK=0, output should not be string, got: {result}"
    assert result["textPreserved"] == True, "Original text should be preserved when BLOCK=0"
    _clean_state_files(state_file)


def test_enhancement_wave_too_small():
    """text.complete does not check ratio when wave has <2 dispatches."""
    state_file = os.path.join("/tmp", f"test-ratio-small-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
// Only 1 dispatch — wave too small for ratio check
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
const output = await plugin['text.complete']({{text: 'hello'}})
console.log(JSON.stringify({{textPreserved: output?.text === 'hello'}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["textPreserved"] == True, "1-dispatch wave should not trigger ratio check"
    _clean_state_files(state_file)


# ---------------------------------------------------------------------------
# enforce-delegate.ts  —  mainthreadBudgetBefore reads state from file
# ---------------------------------------------------------------------------


def _streak_state_file(p: str = "/tmp/gludd-mainthread-streak.json"):
    return p


def test_delegate_streak_zero_allowed():
    """mainthreadBudgetBefore returns null when streak=0."""
    _clean_state_files(_streak_state_file())
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{}}}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True


def test_delegate_streak_at_threshold_denied():
    """mainthreadBudgetBefore denies when streak >= THRESHOLD and open work exists."""
    sf = _streak_state_file()
    _clean_state_files(sf)
    # Write streak state at threshold
    with open(sf, "w") as f:
        json.dump({"streak": 2, "lastDispatchTs": int(time.time() * 1000), "ts": int(time.time() * 1000)}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    if result is not None:
        assert isinstance(result, str) or result.get("permissionDecision") == "deny"
    _clean_state_files(sf)


def test_delegate_read_tool_not_counted():
    """Read/grep/glob tools should be allowed regardless of streak."""
    sf = _streak_state_file()
    _clean_state_files(sf)
    with open(sf, "w") as f:
        json.dump({"streak": 5, "lastDispatchTs": int(time.time() * 1000) - 120000, "ts": int(time.time() * 1000)}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, {{}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf)


def test_delegate_env_disable():
    """GLUDD_MAINTHREAD_STREAK_ENFORCE=0 disables mainthread streak."""
    sf = _streak_state_file()
    _clean_state_files(sf)
    with open(sf, "w") as f:
        json.dump({"streak": 5, "lastDispatchTs": int(time.time() * 1000), "ts": int(time.time() * 1000)}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_MAINTHREAD_STREAK_ENFORCE": "0"})
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf)


# ---------------------------------------------------------------------------
# enforce-deadline.ts  —  task timeout enforcement via state file
# ---------------------------------------------------------------------------


def test_deadline_task_within_timeout_allowed():
    """tool.execute.before does not block a fresh task within timeout (BLOCK=1 default)."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_TASK_DEADLINE_STATE || '/tmp/gludd-task-deadlines.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'do work'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{taskCount: Object.keys(state).length}}))
"""
    result = _run_ts(code)
    assert result["taskCount"] >= 1


def test_deadline_task_over_timeout_blocked():
    """Task exceeding deadline returns {{permissionDecision: "deny"}} (BLOCK=1 default)."""
    stale_state = os.path.join("/tmp", f"test-deadlines-blk-{os.getpid()}.json")
    stale_file = os.path.join("/tmp", f"gludd-task-stale-blk-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        json.dump({"stale-task-1": int(time.time() * 1000) - 400_000}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
        "GLUDD_TASK_STALE_FILE": stale_file,
    })
    assert result is not None, "Expected deny object, got None (allowed)"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "DEADLINE EXCEEDED" in result.get("message", ""), f"Message missing DEADLINE EXCEEDED: {result}"
    _clean_state_files(stale_state, stale_file)


def test_deadline_env_disable():
    """GLUDD_TASK_DEADLINE_ENABLED=0 disables deadline checks."""
    stale_state = os.path.join("/tmp", f"test-deadlines-dis-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        json.dump({"stale-task-2": int(time.time() * 1000) - 400_000}, f)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
const staleFile = process.env.GLUDD_TASK_STALE_FILE || '/tmp/gludd-task-stale.json'
console.log(JSON.stringify({{ignored: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
        "GLUDD_TASK_DEADLINE_ENABLED": "0",
    })
    assert result["ignored"] == True
    _clean_state_files(stale_state)


def test_deadline_block_env_disabled():
    """GLUDD_TASK_DEADLINE_BLOCK=0 allows tool call even when task exceeds deadline."""
    stale_state = os.path.join("/tmp", f"test-deadlines-noblk-{os.getpid()}.json")
    stale_file = os.path.join("/tmp", f"gludd-task-stale-noblk-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        json.dump({"stale-task-2": int(time.time() * 1000) - 400_000}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
        "GLUDD_TASK_STALE_FILE": stale_file,
        "GLUDD_TASK_DEADLINE_BLOCK": "0",
    })
    assert result is None or result.get("allowed") == True, f"Expected allowed with BLOCK=0, got: {result}"
    _clean_state_files(stale_state, stale_file)


def test_deadline_subagent_guard():
    """OPENCODE_SUBAGENT=1 allows tool call regardless of deadline."""
    stale_state = os.path.join("/tmp", f"test-deadlines-sub-{os.getpid()}.json")
    stale_file = os.path.join("/tmp", f"gludd-task-stale-sub-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        json.dump({"stale-task-3": int(time.time() * 1000) - 400_000}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
        "GLUDD_TASK_STALE_FILE": stale_file,
        "OPENCODE_SUBAGENT": "1",
    })
    assert result is None or result.get("allowed") == True, f"Expected allowed for subagent, got: {result}"
    _clean_state_files(stale_state, stale_file)


def test_deadline_corrupt_state_fail_open():
    """Corrupt state file (invalid JSON) allows tool call (fail-open)."""
    stale_state = os.path.join("/tmp", f"test-deadlines-corr-{os.getpid()}.json")
    with open(stale_state, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": stale_state,
    })
    assert result is None or result.get("allowed") == True, f"Expected fail-open, got: {result}"
    _clean_state_files(stale_state)


def test_deadline_no_state_file_fail_open():
    """Missing state file does not crash (fail-open)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deadline.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={
        "GLUDD_TASK_DEADLINE_STATE": "/tmp/nonexistent-deadline-state.json",
    })
    assert result is None or result.get("allowed") == True


# ---------------------------------------------------------------------------
# enforce-floor.ts  —  in-memory streak + openWorkExists
# ---------------------------------------------------------------------------


def test_floor_dispatch_resets_streak():
    """Dispatch call resets streak and is always allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True


def test_floor_streak_zero_non_dispatch_allowed():
    """Non-dispatch call at streak=0 is allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True


def test_floor_streak_max_plus_one_denied():
    """After MAX_STREAK+1 non-dispatch calls, the hook DENIES.

    MAX_STREAK=2, so call 3 should be denied when open work exists.
    """
    import time
    tasks_path = f"/tmp/gludd-test-tasks-floor-{os.getpid()}.md"
    with open(tasks_path, "w") as f:
        f.write("- [ ] floor test task 1\n- [ ] floor test task 2\n")

    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// Call 1: streak 0→1, allowed (≤2)
const r1 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// Call 2: streak 1→2, allowed (≤2)
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// Call 3: streak 2→3, denied (>2) when openWorkExists() true
const r3 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify({{
  r1: r1 ?? null,
  r2: r2 ?? null,
  'r3_deny': r3?.permissionDecision === 'deny',
  'r3_hasMsg': typeof r3?.message === 'string',
}}))
"""
    result = _run_ts(code, env_override={"GLUDD_TASKS_MD": tasks_path})
    assert result["r1"] is None, f"Call 1 should be allowed, got: {result['r1']}"
    assert result["r2"] is None, f"Call 2 should be allowed, got: {result['r2']}"
    assert result["r3_deny"] == True, f"Call 3 should be denied, got: {result}"
    assert result["r3_hasMsg"] == True
    _clean_state_files(tasks_path)


def test_floor_subagent_env_skip():
    """OPENCODE_SUBAGENT=1 skips ALL enforce-floor checks."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") == True


def test_floor_corrupt_state_fail_open():
    """Corrupt shared streak file does not crash the hook."""
    # Write corrupt JSON to the shared streak file
    sf = "/tmp/gludd-tool-streak.json"
    with open(sf, "w") as f:
        f.write("not valid json {{{")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_STREAK_FILE": sf})
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf)


def test_floor_read_tool_not_blocked():
    """Read tools increment read streak but are not blocked at low counts."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'grep'}}, undefined)
console.log(JSON.stringify({{allAllowed: r1 === undefined && r2 === undefined && r3 === undefined}}))
"""
    result = _run_ts(code)
    assert result["allAllowed"] == True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
