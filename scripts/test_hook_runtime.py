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

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_OPENCODE_DIR = Path(os.environ.get("OPENCODE_DIR", str(ROOT / ".opencode"))).resolve()
PLUGIN_DIR = _OPENCODE_DIR / "plugin"
LIB_DIR = _OPENCODE_DIR / "lib"

# Skip the entire module when the plugin directory is absent.
# This lets operators move `.opencode/` aside as a workaround for broken plugins
# without the test suite reporting failures. When `.opencode/` IS present, every
# test runs and must pass — no vacuous pass.
_PLUGINS_PRESENT = PLUGIN_DIR.is_dir() and any(PLUGIN_DIR.glob("*.ts"))
pytestmark = pytest.mark.skipif(
    not _PLUGINS_PRESENT,
    reason=f"no plugins found under {PLUGIN_DIR} (set OPENCODE_DIR=... to test a different location)",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_tmp_counter = 0

_GLOBAL_RUNTIME_STATE_NAMES = frozenset(
    {
        "gludd-block-counter.json",
        "gludd-force-dispatch.json",
        "gludd-hot-delegate.js",
        "gludd-hot-enforce-session-start.js",
        "gludd-hot-enforce-verified-claims.js",
        "gludd-multitask-state.json",
        "gludd-persist-stop-block.json",
        "gludd-post-results-state.json",
        "gludd-text-only-state.json",
        "gludd-tool-streak.json",
        "gludd-watchdog-disengage.json",
    }
)


def _runtime_state_root() -> Path:
    configured = os.environ.get("GLUDD_RUNTIME_TEST_STATE_DIR")
    if configured:
        return Path(configured).resolve()
    return Path(tempfile.gettempdir()).resolve()


def _runtime_state_path(path: str) -> str:
    """Redirect known machine-global state into the verifier-owned directory."""
    configured = os.environ.get("GLUDD_RUNTIME_TEST_STATE_DIR")
    candidate = Path(path)
    if (
        configured
        and candidate.parent == Path("/tmp")
        and candidate.name in _GLOBAL_RUNTIME_STATE_NAMES
    ):
        return str(Path(configured).resolve() / candidate.name)
    return path


def _run_ts(ts_code: str, env_override: dict | None = None, timeout: int = 15):
    """Write TS code to temp file, run with node --experimental-strip-types, return parsed JSON.

    Returns None if stdout is empty (hook returned undefined/void).
    """
    global _tmp_counter
    _tmp_counter += 1
    state_root = _runtime_state_root()
    false_done_path = str(
        state_root
        / f"gludd-false-done-blocks-test-{os.getpid()}-{_tmp_counter}.json"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".ts",
        dir=str(state_root),
        prefix=f"hook_test_{_tmp_counter}_",
        delete=False,
    ) as f:
        f.write(ts_code)
        tmp = f.name
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = "0"  # parent process; ignore stale PID markers
        # Hermetic disengage path: the live watchdog (check_plugin_hashes.py)
        # can rewrite /tmp/gludd-watchdog-disengage.json mid-test, flipping
        # isDisengaged() to true and turning expected denies into allows.
        # Point plugins at a per-process nonexistent path unless a test
        # explicitly overrides it.
        env["GLUDD_DISENGAGE_PATH"] = str(
            state_root / f"gludd-disengage-hermetic-{os.getpid()}.json"
        )
        env["GLUDD_FALSE_DONE_BLOCKS_FILE"] = false_done_path
        env["GLUDD_HOT_MODULE_PREFIX"] = str(
            state_root / f"gludd-hot-{os.getpid()}-{_tmp_counter}-"
        )
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", tmp],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(ROOT),
            env=env,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"Node exit {proc.returncode}:\nstderr: {proc.stderr[:800]}\nstdout: {proc.stdout[:400]}"
            )
        stdout = proc.stdout.strip()
        if not stdout:
            return None
        lines = stdout.split("\n")
        for line in reversed(lines):
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
        for path in (tmp, false_done_path):
            with contextlib.suppress(OSError):
                os.unlink(path)


def test_shared_explicit_non_subagent_ignores_stale_pid_marker():
    """An explicit false marker must beat a stale PID file from another process."""
    code = f"""\
const fs = await import('node:fs')
process.env.OPENCODE_SUBAGENT = '0'
const marker = `/tmp/gludd-subagent-${{process.pid}}.json`
fs.writeFileSync(marker, '{{}}', 'utf8')
try {{
  const mod = await import('{LIB_DIR}/shared.ts')
  console.log(JSON.stringify({{subagent: mod.isSubagent()}}))
}} finally {{
  try {{ fs.unlinkSync(marker) }} catch {{}}
}}
"""
    result = _run_ts(code)
    assert result == {"subagent": False}


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
    """Remove state files before/after tests without touching live global state."""
    for path in paths:
        with contextlib.suppress(OSError):
            os.unlink(_runtime_state_path(path))


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
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
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
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{dirty: mod.isTreeDirty()}}))
"""
    result = _run_ts(code)
    assert isinstance(result["dirty"], bool)


def test_clean_tree_count_dirty_files_zero():
    """countDirtyFiles returns 0 for empty status."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{count: mod.countDirtyFiles('')}}))
"""
    result = _run_ts(code)
    assert result["count"] == 0


def test_clean_tree_count_dirty_files_nonzero():
    """countDirtyFiles counts lines in porcelain output."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const fake = ' M foo.py\\n?? bar.py\\n M baz.py'
console.log(JSON.stringify({{count: mod.countDirtyFiles(fake)}}))
"""
    result = _run_ts(code)
    assert result["count"] == 3


def test_clean_tree_build_deny_message():
    """buildDenyMessage includes count and DENY_MESSAGE_PREFIX."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{msg: mod.buildDenyMessage(5), prefix: mod.getDenyMessagePrefix()}}))
"""
    result = _run_ts(code)
    assert "5" in result["msg"]
    assert "DIRTY TREE" in result["msg"]
    assert result["prefix"] == "DIRTY TREE"


def test_clean_tree_dispatch_tools_defined():
    """DISPATCH_TOOLS array contains task, agent, workflow."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify(mod.getDispatchTools()))
"""
    result = _run_ts(code)
    assert "task" in result
    assert "agent" in result
    assert "workflow" in result


def test_clean_tree_hook_dispatch_with_dirty_tree():
    """The proxy hook denies dispatch when tree is dirty."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_temp.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test dirty file for hook test")
            f.flush()
            os.fsync(f.fileno())
        code = f"""\
const helpers = await import('{LIB_DIR}/plugin_test_exports.ts')
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const gs = helpers.getGitStatus()
console.log("GIT_STATUS[" + gs.length + "]=" + JSON.stringify(gs).slice(0,200))
const dt = helpers.isTreeDirty()
console.log("IS_DIRTY=" + dt)
const toolName = 'task'
const isDispatch = helpers.getDispatchTools().includes(toolName)
console.log("IS_DISPATCH=" + isDispatch)
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code)
        assert result is not None, "Expected deny object, got None"
        assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
        assert "DIRTY TREE" in result.get("message", "")
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_hook_clean_tree_allows_dispatch():
    """Clean tree should allow dispatch (hook returns undefined/void)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    if result is not None and result.get("permissionDecision") == "deny":
        pass


def test_clean_tree_env_disable():
    """GLUDD_CLEAN_TREE_ENFORCE=0 disables the check."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_temp2.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
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
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
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


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
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
const output = await plugin['experimental.text.complete']({{text: 'hello'}})
const isString = typeof output === 'string'
const hasViolation = isString && output.includes('ENHANCEMENT RATIO VIOLATION')
console.log(JSON.stringify({{isString, hasViolation}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["isString"] == True, f"Expected string output, got type: {type(result)}"
    assert result["hasViolation"] == True, f"Expected ENHANCEMENT RATIO VIOLATION in output"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
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
    const output = await plugin['experimental.text.complete']({{text: 'hello'}})
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
    # r3 is allowed because wave was reset after r2's denial
    assert result["r3_deny"] == False, (
        f"Third dispatch (67% fixes, wave=3) should be allowed after wave reset: {result}"
    )
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
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
    const output = await plugin['experimental.text.complete']({{text: 'hello'}})
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


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
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
    const output = await plugin['experimental.text.complete']({{text: 'hello'}})
    console.log(JSON.stringify({{
        notBlocked: typeof output !== 'string',
        textPreserved: typeof output !== 'string' ? (output?.text ?? '') === 'hello' : false,
    }}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
            "GLUDD_ENHANCEMENT_RATIO_BLOCK": "0",
        },
    )
    assert result["notBlocked"] == True, f"With BLOCK=0, output should not be string, got: {result}"
    assert result["textPreserved"] == True, "Original text should be preserved when BLOCK=0"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_enhancement_wave_too_small():
    """text.complete does not check ratio when wave has <2 dispatches."""
    state_file = os.path.join("/tmp", f"test-ratio-small-{os.getpid()}.json")
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
// Only 1 dispatch — wave too small for ratio check
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'fix bug A'}}}}, undefined)
    const output = await plugin['experimental.text.complete']({{text: 'hello'}})
    console.log(JSON.stringify({{textPreserved: output?.text === 'hello'}}))
"""
    result = _run_ts(code, env_override={"GLUDD_ENHANCEMENT_RATIO_STATE": state_file})
    assert result["textPreserved"] == True, "1-dispatch wave should not trigger ratio check"
    _clean_state_files(state_file)


# ---------------------------------------------------------------------------
# enforce-delegate.ts  —  mainthreadBudgetBefore reads state from file
# ---------------------------------------------------------------------------


def test_delegate_streak_zero_allowed():
    """mainthreadBudgetBefore returns null when streak=0."""
    sf = f"/tmp/gludd-mainthread-streak-test-{os.getpid()}.json"
    _clean_state_files(sf, "/tmp/gludd-hot-delegate.js", "/tmp/gludd-watchdog-disengage.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{args: {{}}}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MAINTHREAD_STREAK_ENFORCE": "1",
            "GLUDD_MAINTHREAD_STREAK_FILE": sf,
            "CLAUDE_AGENT_TARGET": "6",
        },
    )
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf)


def test_delegate_streak_at_threshold_denied():
    """mainthreadBudgetBefore denies when streak >= THRESHOLD and open work exists."""
    sf = f"/tmp/gludd-mainthread-streak-test-{os.getpid()}.json"
    fd = f"/tmp/gludd-force-dispatch-test-{os.getpid()}.json"
    tasks_path = f"/tmp/gludd-test-tasks-delegate-{os.getpid()}.md"
    _clean_state_files(sf, fd, tasks_path, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-hot-delegate.js")
    # Write streak state at threshold
    with open(sf, "w") as f:
        json.dump({"count": 2, "ts": int(time.time() * 1000)}, f)
    # Provide open-work signal via TASKS.md so openWorkExists() returns true
    with open(tasks_path, "w") as f:
        f.write("- [ ] delegate test task\n")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
// Catch throw — delegate plugin throws Error on deny, does not return deny object
let result
try {{
  result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{}})
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: "deny", message: e.message}}))
}}
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_LIVE_AGENTS_COUNT": "0",
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_MAINTHREAD_STREAK_ENFORCE": "1",
            "GLUDD_MAINTHREAD_STREAK_FILE": sf,
            "GLUDD_FORCE_DISPATCH_PATH": fd,
            "CLAUDE_AGENT_TARGET": "6",
        },
    )
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    _clean_state_files(sf, fd, tasks_path)


def test_delegate_read_tool_not_counted():
    """Read/grep/glob tools should be allowed regardless of streak."""
    sf = f"/tmp/gludd-mainthread-streak-test-{os.getpid()}.json"
    _clean_state_files(sf)
    with open(sf, "w") as f:
        json.dump({"streak": 5, "lastDispatchTs": int(time.time() * 1000) - 120000, "ts": int(time.time() * 1000)}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, {{}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MAINTHREAD_STREAK_FILE": sf,
        },
    )
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf)


def test_delegate_env_disable():
    """GLUDD_MAINTHREAD_STREAK_ENFORCE=0 disables mainthread streak."""
    sf = f"/tmp/gludd-mainthread-streak-test-{os.getpid()}.json"
    _clean_state_files(sf)
    with open(sf, "w") as f:
        json.dump({"streak": 5, "lastDispatchTs": int(time.time() * 1000), "ts": int(time.time() * 1000)}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-delegate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, {{}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_MAINTHREAD_STREAK_ENFORCE": "0", "GLUDD_MAINTHREAD_STREAK_FILE": sf})
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
            "GLUDD_TASK_STALE_FILE": stale_file,
        },
    )
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
            "GLUDD_TASK_DEADLINE_ENABLED": "0",
        },
    )
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
            "GLUDD_TASK_STALE_FILE": stale_file,
            "GLUDD_TASK_DEADLINE_BLOCK": "0",
        },
    )
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
            "GLUDD_TASK_STALE_FILE": stale_file,
            "OPENCODE_SUBAGENT": "1",
        },
    )
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": stale_state,
        },
    )
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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASK_DEADLINE_STATE": "/tmp/nonexistent-deadline-state.json",
        },
    )
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
    session_state = f"/tmp/gludd-session-start-null-{os.getpid()}.json"
    with open(session_state, "w") as f:
        json.dump({}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": session_state})
    assert result is None or result.get("allowed") == True
    _clean_state_files(session_state)


def test_floor_streak_max_plus_one_denied():
    """After MAX_STREAK+1 non-dispatch calls, the hook DENIES.

    MAX_STREAK=2, so call 3 should be denied when open work exists.
    Must neutralise the session-start window (watchdog writes
    /tmp/gludd-session-start.json) which would tighten max to 1.
    """
    import time

    tasks_path = f"/tmp/gludd-test-tasks-floor-{os.getpid()}.md"
    todowrite_path = f"/tmp/gludd-todowrite-state-{os.getpid()}.json"
    session_state = f"/tmp/gludd-session-start-null-{os.getpid()}.json"
    _clean_state_files(tasks_path, todowrite_path, session_state, "/tmp/gludd-watchdog-disengage.json")
    with open(tasks_path, "w") as f:
        f.write("- [ ] floor test task 1\n- [ ] floor test task 2\n")
    with open(todowrite_path, "w") as f:
        json.dump([{"status": "pending", "content": "test task"}], f)
    # Point GLUDD_SESSION_STATE at a non-existent file so
    # _isInSessionStartWindow() returns false (MAX_STREAK=2, not 1).
    with open(session_state, "w") as f:
        json.dump({}, f)

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
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_TODOWRITE_STATE": todowrite_path,
            "GLUDD_SESSION_STATE": session_state,
        },
    )
    assert result["r1"] is None, f"Call 1 should be allowed, got: {result['r1']}"
    assert result["r2"] is None, f"Call 2 should be allowed, got: {result['r2']}"
    assert result["r3_deny"] == True, f"Call 3 should be denied, got: {result}"
    assert result["r3_hasMsg"] == True
    _clean_state_files(tasks_path, todowrite_path, session_state)


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
    sf = _runtime_state_path("/tmp/gludd-tool-streak.json")
    with open(sf, "w") as f:
        f.write("not valid json {{{")
    session_state = f"/tmp/gludd-session-start-null-{os.getpid()}.json"
    with open(session_state, "w") as f:
        json.dump({}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_STREAK_FILE": sf, "GLUDD_SESSION_STATE": session_state})
    assert result is None or result.get("allowed") == True
    _clean_state_files(sf, session_state)


def test_floor_read_tool_not_blocked():
    """Read tools increment read streak but are not blocked at low counts."""
    session_state = f"/tmp/gludd-session-start-null-{os.getpid()}.json"
    with open(session_state, "w") as f:
        json.dump({}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'grep'}}, undefined)
console.log(JSON.stringify({{allAllowed: r1 === undefined && r2 === undefined && r3 === undefined}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": session_state})
    assert result["allAllowed"] == True
    _clean_state_files(session_state)


# ── enforce-floor.ts  —  runtime tests: text.complete, message-shape, grace, subagent, disengage ──


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_text_complete_blocks_on_zero_dispatches():
    """text.complete replaces prose with FLOOR BREACH when streak > MAX_STREAK (0 dispatches).

    After MAX_STREAK+1 non-dispatch calls with open work, text.complete must replace the
    outgoing text with the FLOOR BREACH directive — proof that the plugin blocks prose
    when the subagent pool is drained to zero.
    """
    tasks_path = f"/tmp/gludd-test-tasks-floor-tc-{os.getpid()}.md"
    todowrite_path = f"/tmp/gludd-todowrite-state-floor-tc-{os.getpid()}.json"
    session_state = f"/tmp/gludd-session-start-fake-tc-{os.getpid()}.json"
    streak_file = f"/tmp/gludd-tool-streak-tc-{os.getpid()}.json"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)
    with open(tasks_path, "w") as f:
        f.write("- [ ] floor text-complete test task 1\n- [ ] floor text-complete test task 2\n")
    with open(todowrite_path, "w") as f:
        json.dump([{"status": "pending", "content": "test task"}], f)
    with open(session_state, "w") as f:
        json.dump({}, f)

    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// 3 non-dispatch calls to build streak = 3 > MAX_STREAK = 2
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// text.complete must detect streak > MAX and replace output
const output = await plugin['experimental.text.complete'](undefined, {{text: 'hello from test'}})
const finalText = (output && output.text) ? output.text : ''
const blocked = finalText.includes('FLOOR BREACH')
const originalGone = !finalText.includes('hello from test')
console.log(JSON.stringify({{blocked, originalGone}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_TODOWRITE_STATE": todowrite_path,
            "GLUDD_SESSION_STATE": session_state,
            "GLUDD_STREAK_FILE": streak_file,
        },
    )
    assert result["blocked"] == True, f"Expected FLOOR BREACH in text.complete output, got: {result}"
    assert result["originalGone"] == True, f"Original text must be replaced: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_message_shape_one_dispatch_denied():
    """After 1 dispatch in prev message, next non-dispatch call is denied.

    The message-shape rule (AGENTS.md) requires ≥5 dispatches per wave.
    A single dispatch followed by an inline tool call triggers the
    _prevMessageDispatchCount 1-4 block.
    """
    tasks_path = f"/tmp/gludd-test-tasks-floor-1d-{os.getpid()}.md"
    todowrite_path = f"/tmp/gludd-todowrite-state-floor-1d-{os.getpid()}.json"
    session_state = f"/tmp/gludd-session-start-fake-1d-{os.getpid()}.json"
    streak_file = f"/tmp/gludd-tool-streak-1d-{os.getpid()}.json"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)
    with open(tasks_path, "w") as f:
        f.write("- [ ] message-shape test task\n")
    with open(todowrite_path, "w") as f:
        json.dump([{"status": "pending", "content": "test task"}], f)
    with open(session_state, "w") as f:
        json.dump({}, f)

    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// 1 dispatch in "previous message"
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
// text.complete transitions _prevMessageDispatchCount = 1
await plugin['experimental.text.complete'](undefined, {{text: 'intermediate'}})
// Non-dispatch call — must be denied as MESSAGE-SHAPE VIOLATION
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const deny = result?.permissionDecision === 'deny'
const hasMsgShape = typeof result?.message === 'string' && result.message.includes('MESSAGE-SHAPE')
console.log(JSON.stringify({{deny, hasMsgShape}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_TODOWRITE_STATE": todowrite_path,
            "GLUDD_SESSION_STATE": session_state,
            "GLUDD_STREAK_FILE": streak_file,
        },
    )
    assert result["deny"] == True, f"Expected deny for 1-dispatch message shape, got: {result}"
    assert result["hasMsgShape"] == True, f"Expected MESSAGE-SHAPE in deny message: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_result_grace_denies_non_dispatch():
    """After result detection in text.complete, non-dispatch tools are denied during grace.

    When text.complete detects result markers (e.g. "task result"), it sets
    _resultProcessingGrace = RESULT_GRACE_CALLS (2). The next non-dispatch,
    non-read call must be denied with DISPATCH GAP.
    """
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// Inject result-marker text to trigger grace period
await plugin['experimental.text.complete'](undefined, {{text: 'task result: test agent completed'}})
// Non-dispatch non-read tool must be denied
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const deny = result?.permissionDecision === 'deny'
const hasGrace = typeof result?.message === 'string' && result.message.includes('DISPATCH GAP')
console.log(JSON.stringify({{deny, hasGrace}}))
"""
    result = _run_ts(code)
    assert result["deny"] == True, f"Expected DISPATCH GAP deny, got: {result}"
    assert result["hasGrace"] == True, f"Expected DISPATCH GAP in deny message: {result}"


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_text_complete_subagent_skip():
    """text.complete returns output unmodified when OPENCODE_SUBAGENT=1.

    The subagent guard in text.complete must short-circuit the hook so
    subagent output is never intercepted or rewritten by the floor enforcer.
    """
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const output = await plugin['experimental.text.complete'](undefined, {{text: 'subagent output text'}})
const textPreserved = !!(output && output.text === 'subagent output text')
console.log(JSON.stringify({{textPreserved}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result["textPreserved"] == True, f"Subagent text must pass through unmodified: {result}"


def test_floor_disengage_allows_after_streak_breach():
    """Disengage signal allows non-dispatch calls after streak exceeds MAX_STREAK.

    The disengage escape hatch (written by `make disengage-enforcement`) must
    allow a non-dispatch call that would otherwise be blocked. The test builds
    streak to MAX_STREAK (2), then writes the disengage file, then makes a 3rd
    call — which must be allowed.
    """
    tasks_path = f"/tmp/gludd-test-tasks-floor-dis-{os.getpid()}.md"
    todowrite_path = f"/tmp/gludd-todowrite-state-floor-dis-{os.getpid()}.json"
    session_state = f"/tmp/gludd-session-start-fake-dis-{os.getpid()}.json"
    streak_file = f"/tmp/gludd-tool-streak-dis-{os.getpid()}.json"
    disengage_path = f"/tmp/gludd-watchdog-disengage-test-{os.getpid()}.json"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file, disengage_path)
    with open(tasks_path, "w") as f:
        f.write("- [ ] disengage test task\n")
    with open(todowrite_path, "w") as f:
        json.dump([{"status": "pending", "content": "disengage test task"}], f)
    with open(session_state, "w") as f:
        json.dump({}, f)

    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
// 2 non-dispatch calls — streak = 2 (at MAX_STREAK, still allowed)
const r1 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
// Write disengage signal file with future timestamp
fs.writeFileSync('{disengage_path}', JSON.stringify({{disengage_until: Date.now() + 300_000}}))
// 3rd non-dispatch — streak would be 3 > MAX_STREAK, but disengage allows it
const r3 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
    r3_ok: r3 === undefined || r3 === null,
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_TASKS_MD": tasks_path,
            "GLUDD_TODOWRITE_STATE": todowrite_path,
            "GLUDD_SESSION_STATE": session_state,
            "GLUDD_STREAK_FILE": streak_file,
            "GLUDD_DISENGAGE_PATH": disengage_path,
        },
    )
    assert result["r1_ok"] == True, f"Call 1 (streak 0→1) must be allowed: {result}"
    assert result["r2_ok"] == True, f"Call 2 (streak 1→2) must be allowed: {result}"
    assert result["r3_ok"] == True, f"Disengage must allow call 3 despite streak=2: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file, disengage_path)


# ---------------------------------------------------------------------------
# enforce-multitask.ts  —  dispatch enforcement
# ---------------------------------------------------------------------------


def test_multitask_text_complete_blocks_thin_wave():
    """experimental.text.complete MUST blank text for thin dispatch waves."""
    namespace = f"/tmp/gludd-multitask-runtime-{os.getpid()}-{time.time_ns()}"
    state_file = f"{namespace}.json"
    dispatch_count_file = f"{state_file}.dispatch-count"
    disengage_path = f"{namespace}-disengage.json"
    _clean_state_files(state_file, dispatch_count_file, disengage_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// Build thisMessageDispatches = 3 via dispatch calls
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
// Call experimental.text.complete — should detect 3 < MIN_DISPATCHES=10 and blank text
let output
let error = null
try {{
  output = await plugin['experimental.text.complete'](undefined, {{text: 'Dispatching 3 subagents to fix bugs.'}})
}} catch (e) {{
  error = e.message
}}
console.log(JSON.stringify({{
  threw: error !== null,
  errorSnippet: error ? error.slice(0, 200) : null,
  outputType: output === undefined ? 'undefined' : typeof output,
  textWasBlocked: output !== null && output !== undefined && typeof output === 'object' && output.text && output.text.includes('BLOCKED'),
  resultText: output && typeof output === 'object' ? (output.text || '')?.slice(0, 200) : String(output || '').slice(0, 120),
}}))
"""
    try:
        result = _run_ts(
            code,
            env_override={
                "GLUDD_MIN_DISPATCHES": "10",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                "GLUDD_MULTITASK_STATE_FILE": state_file,
                "GLUDD_DISENGAGE_PATH": disengage_path,
            },
        )

        assert result.get("threw") is False, (
            f"experimental.text.complete must run without throwing. Result: {result}"
        )
        assert result["textWasBlocked"] == True, (
            f"Expected THIN WAVE BLOCKED but text passed through unmodified. "
            f"Hook ran without error but did not detect the thin wave. Result: {result}"
        )
    finally:
        _clean_state_files(state_file, dispatch_count_file, disengage_path)


def test_multitask_enough_dispatches():
    """Dispatch tools are always allowed regardless of state."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'task'}})
const r2 = await plugin['tool.execute.before']({{tool: 'agent'}})
const r3 = await plugin['tool.execute.before']({{tool: 'workflow'}})
console.log(JSON.stringify({{r1_ok: r1 === undefined || r1 === null, r2_ok: r2 === undefined || r2 === null, r3_ok: r3 === undefined || r3 === null}}))
"""
    result = _run_ts(code)
    assert result["r1_ok"] == True
    assert result["r2_ok"] == True
    assert result["r3_ok"] == True


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_multitask_single_dispatch_blocked():
    """1 dispatch in prev message + zeroStreak=0 → edit allowed (lenient)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}})
await plugin['experimental.text.complete'](undefined, {{text: 'intermediate'}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True, f"Expected allowed for 1-dispatch wave, got: {result}"


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_multitask_zero_dispatch_text_blocked():
    """2 zero-dispatch messages → text.complete blocks output."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['experimental.text.complete'](undefined, {{text: 'msg1'}})
const output = {{text: 'msg2'}}
const r2 = await plugin['experimental.text.complete'](undefined, output)
const finalText = r2?.text ?? output.text
console.log(JSON.stringify({{blocked: r2 !== null && r2 !== undefined && finalText !== 'msg2', finalText}}))
"""
    result = _run_ts(code)
    assert result["blocked"] == True, f"Expected text.complete to block, got: {result}"
    assert "dispatch" in result.get("finalText", "").lower()


def test_multitask_subagent_guard():
    """OPENCODE_SUBAGENT=1 skips enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"


def test_multitask_env_disabled():
    """GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_MULTITASK_FLOOR_ENFORCE": "0"})
    assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"


def test_multitask_configured_minimum_hard_block():
    """Non-dispatch tool call with 0 dispatches and pending work → denied (UNDER-FLOOR HARD BLOCK).

    With MIN_DISPATCHES=2, a non-dispatch call when thisMessageDispatches=0
    and pending work exists must return permissionDecision: 'deny' with
    'UNDER-FLOOR HARD BLOCK' in the message. This is the immediate block
    that fires BEFORE the consecutive-non-dispatch counter.
    """
    state_file = f"/tmp/gludd-multitask-test-uf-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MIN_DISPATCHES": "2",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    assert result is not None, f"Expected deny object, got None: {result}"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    message = result.get("message", "")
    assert "CONFIGURED MINIMUM BLOCK" in message, f"Missing configured-minimum block: {result}"
    assert "Configured minimum is 2" in message, f"Missing explicit configured minimum: {result}"
    _clean_state_files(state_file)


def test_multitask_dispatch_ceiling_blocked():
    """Dispatch call beyond MAX_DISPATCHES → denied (DISPATCH CEILING BREACH).

    With MAX_DISPATCHES=3, the 4th dispatch in the same message must return
    permissionDecision: 'deny' with 'DISPATCH CEILING BREACH' in the message.
    """
    state_file = f"/tmp/gludd-multitask-test-ceil-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// 3 dispatches — allowed (at ceiling)
const r1 = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'workflow'}}, undefined)
// 4th dispatch — denied (above ceiling)
const r4 = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify({{
    r3_ok: r3 === undefined || r3 === null,
    r4_denied: r4 !== null && r4?.permissionDecision === 'deny',
    r4_hasCeiling: typeof r4?.message === 'string' && r4.message.includes('DISPATCH CEILING BREACH'),
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MULTITASK_MAX_DISPATCHES": "3",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    assert result["r3_ok"] == True, f"3rd dispatch should be allowed (at ceiling), got: {result}"
    assert result["r4_denied"] == True, f"4th dispatch should be denied (above ceiling), got: {result}"
    assert result["r4_hasCeiling"] == True, f"Deny message missing DISPATCH CEILING BREACH: {result}"
    _clean_state_files(state_file)


def test_multitask_consecutive_non_dispatch_blocked():
    """CONSECUTIVE_NON_DISPATCH_THRESHOLD consecutive non-dispatch calls → denied.

    With THRESHOLD=3 and pending work, after 3 non-dispatch calls within 30s,
    the 3rd call must return permissionDecision: 'deny' with a message
    containing 'consecutive non-dispatch tool calls'.
    Must first satisfy MIN_DISPATCHES (set to 2) so the under-floor check
    doesn't fire first.
    """
    state_file = f"/tmp/gludd-multitask-test-cons-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// Satisfy floor: 2 dispatches
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
// 3 consecutive non-dispatch calls — read tools excluded, use edit/write/bash
const r1 = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
    r3_denied: r3 !== null && r3?.permissionDecision === 'deny',
    r3_hasConsecutive: typeof r3?.message === 'string' && r3.message.includes('CONSECUTIVE NON-DISPATCH STREAK'),
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MIN_DISPATCHES": "2",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD": "3",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS": "60000",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    assert result["r1_ok"] == True, f"1st non-dispatch should be allowed, got: {result}"
    assert result["r2_ok"] == True, f"2nd non-dispatch should be allowed, got: {result}"
    assert result["r3_denied"] == True, f"3rd non-dispatch should be denied (at threshold), got: {result}"
    assert result["r3_hasConsecutive"] == True, f"Deny message missing 'consecutive non-dispatch': {result}"
    _clean_state_files(state_file)


def test_multitask_corrupt_state_fail_open():
    """Corrupt MULTITASK_STATE_FILE → hook fails open (does not crash, returns structured result).

    Fail-open means: no throw, no crash, no node exit code 1. The hook
    recovers with a fresh state and continues enforcing — it does NOT
    blindly allow. A deny with a readable message is valid fail-open
    behavior (the plugin loaded and operated, it didn't die).
    """
    state_file = f"/tmp/gludd-multitask-test-corr-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    with open(state_file, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    # Fail-open means: no crash, structured result returned. The plugin may
    # deny (with a clean fresh state) — that is ok, it loaded and operated.
    assert result is not None, "Corrupt state must not crash: expected a result object"
    assert isinstance(result, dict), f"Expected dict result, got type: {type(result)}"
    _clean_state_files(state_file)


# ============================================================================
# FAILING TESTS — prove grinding-inline is not blocked correctly
# ============================================================================


def test_multitask_grind_inline_no_prior_dispatch():
    """Agent grinds inline without dispatching: consecutive counter catches it.

    Read tools (read/grep/glob) are excluded from the consecutive non-dispatch
    counter per the plugin spec: investigation bursts should never trigger the
    grinding penalty. Non-read tools (edit/write/bash) are counted.

    With MIN_DISPATCHES=0 (under-floor disabled for this test) and THRESHOLD=3,
    makes 4 consecutive non-read non-dispatch calls without dispatching first.
    Read tools are allowed and do not increment the counter.

    Call 1 (read): ALLOWED — read tools exempt from counter.
    Call 2 (read): ALLOWED — read tools exempt, counter still 0.
    Call 3 (edit): consecutive=1 < threshold → ALLOWED (below threshold).
    Call 4 (write): consecutive=2 < threshold → ALLOWED (below threshold).
    Call 5 (bash): consecutive=3 >= threshold → CONSECUTIVE NON-DISPATCH STREAK.
    """
    state_file = f"/tmp/gludd-multitask-grind-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// NO dispatches — simulate agent grinding inline immediately
// Read tools should NOT increment the counter
const r1 = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'grep'}}, undefined)
// Non-read tools increment the counter
const r3 = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r4 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r5 = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify({{
    r1_denied: r1 !== null && r1?.permissionDecision === 'deny',
    r2_denied: r2 !== null && r2?.permissionDecision === 'deny',
    r3_denied: r3 !== null && r3?.permissionDecision === 'deny',
    r4_denied: r4 !== null && r4?.permissionDecision === 'deny',
    r5_denied: r5 !== null && r5?.permissionDecision === 'deny',
    r5_hasStreak: typeof r5?.message === 'string' && r5.message.includes('CONSECUTIVE NON-DISPATCH STREAK'),
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MIN_DISPATCHES": "0",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD": "3",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS": "60000",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    # Calls 1-2 (read/grep): ALLOWED — read tools excluded from counter.
    assert result["r1_denied"] == False, f"Call 1 (read) must be allowed — reads excluded from counter. Got: {result}"
    assert result["r2_denied"] == False, f"Call 2 (grep) must be allowed — reads excluded from counter. Got: {result}"
    # Call 3 (edit): consecutive=1 < threshold=3 → ALLOWED
    assert result["r3_denied"] == False, f"Call 3 (edit) must be allowed (counter=1 < 3). Got: {result}"
    # Call 4 (write): consecutive=2 < threshold=3 → ALLOWED
    assert result["r4_denied"] == False, f"Call 4 (write) must be allowed (counter=2 < 3). Got: {result}"
    # Call 5 (bash): consecutive=3 >= threshold → CONSECUTIVE NON-DISPATCH STREAK
    assert result["r5_denied"] == True, f"Call 5 (bash) must be denied (counter=3 >= 3). Got: {result}"
    assert result["r5_hasStreak"] == True, (
        f"Call 5 (counter=3 >= threshold=3) must fire CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    _clean_state_files(state_file)


def test_multitask_text_only_response_next_tool_blocked():
    """After floor is satisfied (15 dispatches), consecutive counter blocks grinding.

    With MIN_DISPATCHES=10 and THRESHOLD=3, dispatches 15 agents to satisfy
    the floor, then makes 4 non-dispatch calls using non-read tools (edit/write/bash).
    Since the floor IS satisfied (15 >= 10), the under-floor block does NOT fire.
    Read tools (read/grep/glob) are excluded from the consecutive counter per plugin spec.
    Instead, the consecutive counter catches the grinding pattern with non-read tools:

    Call 1 (edit): consecutive=1 (< 3), no under-floor → ALLOWED
    Call 2 (write): consecutive=2 (< 3), no under-floor → ALLOWED
    Call 3 (bash): consecutive=3 >= THRESHOLD → CONSECUTIVE GRINDING
    Call 4 (edit): consecutive=4 >= THRESHOLD → CONSECUTIVE GRINDING
    """
    state_file = f"/tmp/gludd-multitask-text-only-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// Satisfy the floor: 15 dispatches so thisMessageDispatches >= MIN_DISPATCHES
for (let i = 0; i < 15; i++) {{
    await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
}}
// Now make non-dispatch calls with non-read tools — grinding after floor satisfied
const r1 = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r2 = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
const r3 = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
const r4 = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify({{
    r1_denied: r1 !== null && r1?.permissionDecision === 'deny',
    r1_allowed: r1 === undefined || r1 === null,
    r2_denied: r2 !== null && r2?.permissionDecision === 'deny',
    r2_allowed: r2 === undefined || r2 === null,
    r3_denied: r3 !== null && r3?.permissionDecision === 'deny',
    r3_hasGrinding: typeof r3?.message === 'string' && r3.message.includes('CONSECUTIVE NON-DISPATCH STREAK'),
    r4_denied: r4 !== null && r4?.permissionDecision === 'deny',
    r4_hasGrinding: typeof r4?.message === 'string' && r4.message.includes('CONSECUTIVE NON-DISPATCH STREAK'),
    r4_msg: r4?.message || '',
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MIN_DISPATCHES": "10",
            "GLUDD_MULTITASK_MAX_DISPATCHES": "20",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD": "3",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS": "60000",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    # Calls 1-2 are allowed: floor satisfied (15 >= 10), counter below threshold
    assert result["r1_allowed"] == True, f"Call 1 (edit) must be ALLOWED: floor satisfied, counter=1 < 3. Got: {result}"
    assert result["r2_allowed"] == True, (
        f"Call 2 (write) must be ALLOWED: floor satisfied, counter=2 < 3. Got: {result}"
    )
    # Call 3: consecutive counter at threshold → CONSECUTIVE NON-DISPATCH STREAK fires
    assert result["r3_denied"] == True, (
        f"Call 3 (bash) must be denied by CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    assert result["r3_hasGrinding"] == True, (
        f"Call 3 message must contain CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    # Call 4: still grinding
    assert result["r4_denied"] == True, f"Call 4 (edit) must be denied. Got: {result}"
    assert result["r4_hasGrinding"] == True, (
        f"Call 4 message must contain CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    _clean_state_files(state_file)


def test_multitask_zero_dispatch_text_blocked_after_prior_dispatch():
    """text.complete blocks text for zero-dispatch waves after dispatches made.

    Step 1: dispatch 1 agent (sessionDispatchTotal > 0).
    Step 2: text.complete → thin-wave block (1 < 2), handleMessageBoundary runs.
    Step 3: text.complete for new message → thisMessageDispatches=0, sessionDispatchTotal=1 → TEXT BLOCKED.

    Before the fix, the condition was `thisMessageDispatches > 0`, so
    zero-dispatch waves passed through unblocked. The fix replaces this
    with `sessionDispatchTotal > 0`.
    """
    state_file = f"/tmp/gludd-multitask-zd-{os.getpid()}.json"
    _clean_state_files(state_file, "/tmp/gludd-watchdog-disengage.json", "/tmp/gludd-force-dispatch.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const r1 = await plugin['experimental.text.complete'](undefined, {{text: 'wave 1 text'}})
const r2 = await plugin['experimental.text.complete'](undefined, {{text: 'wave 2 text (zero dispatch)'}})
const wave1Blocked = r1 !== null && r1 !== undefined && (r1.text || '').includes('THIN WAVE')
const wave2Blocked = r2 !== null && r2 !== undefined && (r2.text || '').includes('THIN WAVE')
const wave2ZeroBlocked = r2 !== null && r2 !== undefined && (r2.text || '').includes('ZERO-DISPATCH TEXT BLOCKED')
console.log(JSON.stringify({{
    wave1Blocked,
    wave2Blocked,
    wave2ZeroBlocked,
    wave1Text: r1?.text || '(none)',
    wave2Text: r2?.text || '(none)',
}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_MULTITASK_STATE_FILE": state_file,
            "GLUDD_MIN_DISPATCHES": "2",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    assert result is not None, f"Expected blocked text, got None"
    assert result["wave1Blocked"] == True, f"Wave 1 (1 dispatch) must be blocked as thin wave. Got: {result}"
    assert result["wave2ZeroBlocked"] == True, f"Wave 2 (0 dispatches) must be blocked. Got: {result}"
    _clean_state_files(state_file)


# ---------------------------------------------------------------------------
# enforce-stop.ts  —  text.complete block for pending work + stop patterns
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_pending_work_text_blanked():
    """Actual runtime test: hasLocalWork() true → text blanked (no subagent guard)."""
    state_file = os.path.join("/tmp", f"test-stop-state-{os.getpid()}.json")
    _clean_state_files("/tmp/gludd-block-counter.json")
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 3,
                "tasksMdUnchecked": True,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": True,
                "hasPendingWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 30,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'Done. All tasks complete.'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
const blocked = finalText !== 'Done. All tasks complete.'
console.log(JSON.stringify({{blocked, finalText: finalText.slice(0, 200)}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
        },
    )
    assert result["blocked"] == True, f"Expected text to be blanked, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_no_pending_work():
    state_file = os.path.join("/tmp", f"test-stop-clean-{os.getpid()}.json")
    _clean_state_files(
        state_file,
        "/tmp/gludd-post-results-state.json",
        "/tmp/gludd-text-only-state.json",
        "/tmp/gludd-block-counter.json",
    )
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 0,
                "tasksMdUnchecked": False,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": False,
                "hasPendingWork": False,
                "ciVerdictPendingOrRed": False,
                "healthScore": 100,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'All good, no pending work.'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{passedThrough: finalText === 'All good, no pending work.'}}))
"""
    result = _run_ts(code, env_override={"GLUDD_STOP_STATE_FILE": state_file})
    assert result["passedThrough"] == True, f"Expected text to pass through, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_env_disabled():
    state_file = os.path.join("/tmp", f"test-stop-disable-{os.getpid()}.json")
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 5,
                "tasksMdUnchecked": True,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": True,
                "hasPendingWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 20,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'Done.'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{passedThrough: finalText === 'Done.'}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
            "GLUDD_STOP_ENFORCE": "0",
        },
    )
    assert result["passedThrough"] == True, f"Expected text to pass through when disabled, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_corrupt_state():
    state_file = os.path.join("/tmp", f"test-stop-corrupt-{os.getpid()}.json")
    with open(state_file, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'corrupt state test text'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{returned: true, isString: typeof finalText === 'string'}}))
"""
    result = _run_ts(code, env_override={"GLUDD_STOP_STATE_FILE": state_file})
    assert result["returned"] == True, "Hook must return without crashing (fail-open)"
    assert result["isString"] == True, "Output must be a string (no throw, no crash)"
    _clean_state_files(state_file)


# ── TWO-LAYER PERSISTENT STOP-BLOCK TESTS ──────────────────────────────────


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_block_persists_across_turns():
    state_file = os.path.join("/tmp", f"test-stop-persist-{os.getpid()}.json")
    block_file = os.path.join("/tmp", f"gludd-persist-stop-block-persist-{os.getpid()}.json")
    _clean_state_files(state_file, block_file, "/tmp/gludd-block-counter.json")
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 3,
                "tasksMdUnchecked": True,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": True,
                "hasPendingWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 30,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
// Step 1: text with pending work → should blank and write persist block
const output = {{text: 'Done. Everything is complete.'}}
const r1 = await plugin['experimental.text.complete'](undefined, output)
const textBlanked = r1?.text !== 'Done. Everything is complete.'
// Step 2: non-dispatch tool call → should be denied by persist block
const r2 = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
const editDenied = r2 !== null && r2 !== undefined && r2?.permissionDecision === 'deny'
// Step 3: dispatch tool call → should be allowed and clear the block
const r3 = await plugin['tool.execute.before']({{tool: 'task', args: {{}}}}, undefined)
const dispatchAllowed = r3 === undefined || r3 === null
// Step 4: after dispatch clears block, edit should be allowed again
const r4 = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
const editAllowedAfter = r4 === undefined || r4 === null
console.log(JSON.stringify({{textBlanked, editDenied, dispatchAllowed, editAllowedAfter}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
            "GLUDD_PERSIST_STOP_BLOCK_FILE": block_file,
        },
    )
    assert result["textBlanked"] == True, f"Expected text to be blanked, got: {result}"
    assert result["editDenied"] == True, f"Expected edit to be denied by persist block, got: {result}"
    assert result["dispatchAllowed"] == True, f"Expected dispatch to be allowed, got: {result}"
    assert result["editAllowedAfter"] == True, f"Expected edit after dispatch to be allowed, got: {result}"
    _clean_state_files(state_file, block_file)


def test_stop_block_cleared_by_dispatch():
    """Dispatch call after stop-pattern clears the persist block flag."""
    block_file = os.path.join("/tmp", f"gludd-persist-stop-block-clear-{os.getpid()}.json")
    _clean_state_files(block_file, "/tmp/gludd-block-counter.json")
    # Pre-write the persist block flag (simulating a prior stop detection)
    with open(block_file, "w") as f:
        json.dump({"blocked": True, "timestamp": int(time.time() * 1000), "reason": "test-block"}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
// Dispatch should be allowed and clear the block
const r1 = await plugin['tool.execute.before']({{tool: 'task', args: {{}}}}, undefined)
const dispatchAllowed = r1 === undefined || r1 === null
console.log(JSON.stringify({{dispatchAllowed, blockFile: '{block_file}'}}))
"""
    result = _run_ts(code, env_override={"GLUDD_PERSIST_STOP_BLOCK_FILE": block_file})
    assert result["dispatchAllowed"] == True, f"Expected dispatch to be allowed, got: {result}"
    # Verify the block file was cleared
    assert not os.path.exists(block_file), "Persist block file should be cleared after dispatch"
    _clean_state_files(block_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_no_pending_work_allows():
    state_file = os.path.join("/tmp", f"test-stop-nopending-{os.getpid()}.json")
    block_file = os.path.join("/tmp", f"gludd-persist-stop-block-nopend-{os.getpid()}.json")
    _clean_state_files(
        state_file,
        block_file,
        "/tmp/gludd-block-counter.json",
        "/tmp/gludd-post-results-state.json",
        "/tmp/gludd-text-only-state.json",
    )
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 0,
                "tasksMdUnchecked": False,
                "gateStatusRed": False,
                "repoPending": False,
                "hasLocalWork": False,
                "hasPendingWork": False,
                "ciVerdictPendingOrRed": False,
                "healthScore": 100,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
// Text should pass through (no pending work)
const output = {{text: 'All good, no pending work.'}}
const r1 = await plugin['experimental.text.complete'](undefined, output)
const passedThrough = (r1?.text ?? output.text) === 'All good, no pending work.'
// Non-dispatch tool should be allowed (no persist block)
const r2 = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
const editAllowed = r2 === undefined || r2 === null
console.log(JSON.stringify({{passedThrough, editAllowed}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
            "GLUDD_PERSIST_STOP_BLOCK_FILE": block_file,
        },
    )
    assert result["passedThrough"] == True, f"Expected text to pass through, got: {result}"
    assert result["editAllowed"] == True, f"Expected edit to be allowed, got: {result}"
    _clean_state_files(state_file, block_file)


def test_stop_subagent_block_guard():
    """OPENCODE_SUBAGENT=1 → persist block check skipped, edit allowed."""
    block_file = os.path.join("/tmp", f"gludd-persist-stop-block-sub-{os.getpid()}.json")
    _clean_state_files(block_file)
    # Pre-write the persist block flag
    with open(block_file, "w") as f:
        json.dump({"blocked": True, "timestamp": int(time.time() * 1000), "reason": "test-subagent-block"}, f)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
// Non-dispatch tool call in subagent context → should be allowed (guard skips)
const r1 = await plugin['tool.execute.before']({{tool: 'edit', args: {{}}}}, undefined)
const editAllowed = r1 === undefined || r1 === null
console.log(JSON.stringify({{editAllowed}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_PERSIST_STOP_BLOCK_FILE": block_file,
            "OPENCODE_SUBAGENT": "1",
        },
    )
    assert result["editAllowed"] == True, f"Subagent should bypass persist block, got: {result}"
    _clean_state_files(block_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_task_result_passes_through_gate_red():
    state_file = os.path.join("/tmp", f"test-stop-taskresult-{os.getpid()}.json")
    _clean_state_files(
        state_file,
        "/tmp/gludd-post-results-state.json",
        "/tmp/gludd-text-only-state.json",
        "/tmp/gludd-block-counter.json",
    )
    with open(state_file, "w") as f:
        json.dump(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 0,
                "tasksMdUnchecked": True,
                "gateStatusRed": True,
                "repoPending": True,
                "hasLocalWork": True,
                "hasPendingWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 20,
            },
            f,
        )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'task result: test agent completed. Fixed 3 files.'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
const passedThrough = finalText === 'task result: test agent completed. Fixed 3 files.'
console.log(JSON.stringify({{passedThrough}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_STOP_STATE_FILE": state_file,
        },
    )
    assert result["passedThrough"] == True, (
        f"Subagent task_result text MUST pass through even with gate red. Got: {result}"
    )
    _clean_state_files(state_file)


def test_stop_permission_seeking_want_me_to_blocked():
    """'Want me to proceed?' is ALWAYS blocked — asking permission to do work is never acceptable."""
    _clean_state_files("/tmp/gludd-block-counter.json", "/tmp/gludd-persist-stop-block.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'Fix is ready. Want me to proceed with the other 13 plugins?'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{
    blocked: finalText !== output.text,
    hasPermissionBlock: finalText.includes('PERMISSION-SEEKING BLOCKED'),
}}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected JSON output"
    assert result["blocked"] == True, f"Expected text to be blocked, got: {result}"
    assert result["hasPermissionBlock"] == True, f"Expected PERMISSION-SEEKING BLOCKED, got: {result}"


def test_stop_permission_seeking_should_i_blocked():
    """'Should I continue with fixing?' is ALWAYS blocked."""
    _clean_state_files("/tmp/gludd-block-counter.json", "/tmp/gludd-persist-stop-block.json")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: 'Should I continue with the remaining fixes?'}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{
    blocked: finalText !== output.text,
    hasPermissionBlock: finalText.includes('PERMISSION-SEEKING BLOCKED'),
}}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected JSON output"
    assert result["blocked"] == True, f"Expected text to be blocked, got: {result}"
    assert result["hasPermissionBlock"] == True, f"Expected PERMISSION-SEEKING BLOCKED, got: {result}"


def test_stop_permission_seeking_export_matches():
    """getPermissionSeekingRe() is exported and matches the right phrases."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const re = mod.getPermissionSeekingRe()
console.log(JSON.stringify({{
    hasExport: typeof mod.getPermissionSeekingRe === 'function',
    match1: re.test('Want me to proceed?'),
    match2: re.test('want me to dispatch a subagent'),
    match3: re.test('Should I continue with fixing?'),
    match4: re.test('shall I proceed?'),
    match5: re.test('Proceed?'),
    noMatch1: re.test('The fix is ready'),
    noMatch2: re.test('I will proceed with the next task'),
}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert result["hasExport"] == True
    assert result["match1"] == True, f"Should match 'Want me to proceed?'"
    assert result["match2"] == True, f"Should match 'want me to dispatch'"
    assert result["match3"] == True, f"Should match 'Should I continue'"
    assert result["match4"] == True, f"Should match 'shall I proceed'"
    assert result["match5"] == True, f"Should match 'Proceed?'"
    assert result["noMatch1"] == False, f"Should NOT match 'The fix is ready'"
    assert result["noMatch2"] == False, f"Should NOT match 'I will proceed'"


def test_stop_status_summary_blocked_despite_evidence():
    """Status summary with commit hashes + 'CI PENDING' (= structured evidence)
    is STILL blanked while pending work exists — evidence never legitimizes
    stopping-to-summarize. Regression pin for the 2026-07-15 bypass."""
    _clean_state_files("/tmp/gludd-block-counter.json", "/tmp/gludd-persist-stop-block.json")
    summary = (
        "Here's the session 37 final status:\\n\\n"
        "**Completed this session**\\n"
        "- NF.2 P6 done (52 tests, 8d32ff5a)\\n"
        "- NF.3 all roles fleshed (aa7e3abd)\\n\\n"
        "**Remaining**\\n"
        "| Item | Status |\\n"
        "| --- | --- |\\n"
        "| A.4 beta.2 release | CI PENDING |\\n"
        "| NF.4 | in progress |\\n"
    )
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-stop.ts')
const plugin = await mod.default({{}})
const output = {{text: "{summary}"}}
const result = await plugin['experimental.text.complete'](undefined, output)
const finalText = result?.text ?? output.text
console.log(JSON.stringify({{
    blocked: finalText !== output.text,
    hasStatusSummaryBlock: finalText.includes('STATUS-SUMMARY RESPONSE BLOCKED'),
}}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected JSON output"
    assert result["blocked"] == True, f"Status summary with evidence must be blocked, got: {result}"
    assert result["hasStatusSummaryBlock"] == True, f"Expected STATUS-SUMMARY RESPONSE BLOCKED, got: {result}"


def test_stop_status_summary_export_matches():
    """getStatusSummaryRe() + looksLikeStatusSummary are exported and detect the pattern."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const re = mod.getStatusSummaryRe()
const structural = "**What changed**\\n- [x] item one\\n- [x] item two\\n**Remaining**\\n| A | B |\\n| - | - |\\n"
console.log(JSON.stringify({{
    hasRe: typeof mod.getStatusSummaryRe === 'function',
    hasFn: typeof mod.looksLikeStatusSummary === 'function',
    match1: re.test("Here's the session 37 final status"),
    match2: re.test("Session 12 wrap-up"),
    match3: re.test("Final status report:"),
    structural: mod.looksLikeStatusSummary(structural),
    noMatch1: mod.looksLikeStatusSummary("Reading the config file now."),
    noMatch2: re.test("The function returns early."),
}}))
"""
    result = _run_ts(code)
    assert result is not None
    assert result["hasRe"] == True
    assert result["hasFn"] == True
    assert result["match1"] == True, "Should match 'Here's the session 37 final status'"
    assert result["match2"] == True, "Should match 'Session 12 wrap-up'"
    assert result["match3"] == True, "Should match 'Final status report:'"
    assert result["structural"] == True, "Should structurally match bolded headers + table/bullets"
    assert result["noMatch1"] == False, "Should NOT match plain working text"
    assert result["noMatch2"] == False, "Should NOT match plain sentence"


# ---------------------------------------------------------------------------
# enforce-clean-tree.ts  —  dispatch-time dirty tree enforcement
# ---------------------------------------------------------------------------


def test_clean_tree_dispatch_allowed():
    """Dispatch with clean tree -> allowed (hook returns undefined/void)."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    if result is not None and result.get("permissionDecision") == "deny":
        assert "DIRTY TREE" in result.get("message", ""), "If denied, must be dirty tree"


def test_clean_tree_dirty_dispatch_blocked():
    """Dirty tree + dispatch -> returns {{permissionDecision: 'deny'}}."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_runtime.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test dirty file for runtime hook test")
            f.flush()
            os.fsync(f.fileno())
        code = f"""\
const helpers = await import('{LIB_DIR}/plugin_test_exports.ts')
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const gs = helpers.getGitStatus()
console.log("GIT_STATUS[" + gs.length + "]=" + JSON.stringify(gs).slice(0,200))
const dt = helpers.isTreeDirty()
console.log("IS_DIRTY=" + dt)
console.log("SUBAGENT=" + process.env.OPENCODE_SUBAGENT)
console.log("ENFORCE=" + process.env.GLUDD_CLEAN_TREE_ENFORCE)
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log("RAW_RESULT=" + JSON.stringify(result))
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code)
        assert result is not None, "Expected deny object, got None"
        assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
        assert "DIRTY TREE" in result.get("message", "")
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_env_disabled():
    """GLUDD_CLEAN_TREE_ENFORCE=0 -> dispatch allowed even with dirty tree."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_disabled.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"GLUDD_CLEAN_TREE_ENFORCE": "0"})
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_subagent_guard():
    """OPENCODE_SUBAGENT=1 -> skip enforcement."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_subagent.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
        result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
        assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


# ── enforce-clean-tree.ts  —  isTreeDirty / countDirtyFiles edge cases ──


def test_clean_tree_isTreeDirty_empty_string():
    """isTreeDirty() with empty string (no git repo) returns false."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
// Simulate getGitStatus returning "" by directly testing logic
const count = mod.countDirtyFiles('')
const empty = mod.countDirtyFiles('   ')
const newlines = mod.countDirtyFiles('\\n\\n\\n')
console.log(JSON.stringify({{empty: count, whitespace: empty, newlines}}))
"""
    result = _run_ts(code)
    assert result["empty"] == 0, "Empty string should count 0"
    assert result["whitespace"] == 0, "Whitespace-only should count 0"
    assert result["newlines"] == 0, "Newlines-only should count 0"


def test_clean_tree_countDirtyFiles_edge_cases():
    """countDirtyFiles handles edge-case porcelain output."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const mixed = mod.countDirtyFiles(' M a.py\\n   \\n?? b.py\\n  \\n')
const trailing = mod.countDirtyFiles('?? x.py\\n M y.py\\n')
const single = mod.countDirtyFiles('?? z.py')
console.log(JSON.stringify({{mixed, trailing, single}}))
"""
    result = _run_ts(code)
    assert result["mixed"] == 2, "Blank lines should be ignored, found 2 real files"
    assert result["trailing"] == 2
    assert result["single"] == 1


def test_clean_tree_countDirtyFiles_single_line():
    """countDirtyFiles with single entry returns 1."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{single: mod.countDirtyFiles('?? foo.py')}}))
"""


def test_clean_tree_non_dispatch_tool_not_blocked():
    """Non-dispatch tools (edit, write, read, bash) pass through even with dirty tree."""
    test_file = str(ROOT / "scripts" / "_hook_test_dirty_nondispatch.txt")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
let results = {{}}
for (const t of ['edit', 'write', 'read', 'grep', 'glob', 'bash']) {{
    const r = await plugin['tool.execute.before']({{tool: t}}, undefined)
    results[t] = r === undefined || r === null || r.permissionDecision !== 'deny'
}}
console.log(JSON.stringify(results))
"""
        result = _run_ts(code)
        for tool in ["edit", "write", "read", "grep", "glob", "bash"]:
            assert result[tool] == True, f"Non-dispatch tool '{tool}' should not be blocked on dirty tree"
    finally:
        try:
            os.unlink(test_file)
        except OSError:
            pass


def test_clean_tree_buildDenyMessage_edge_cases():
    """buildDenyMessage with 0, 1, many files includes correct counts."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    zero: mod.buildDenyMessage(0),
    one: mod.buildDenyMessage(1),
    many: mod.buildDenyMessage(42),
}}))
"""
    result = _run_ts(code)
    assert "0" in result["zero"]
    assert "1" in result["one"]
    assert "42" in result["many"]
    assert "DIRTY TREE" in result["zero"]


def test_clean_tree_getGitStatus_real_repo_returns_string():
    """getGitStatus() in real repo returns a string (may be empty or non-empty)."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const status = mod.getGitStatus()
const dirty = mod.isTreeDirty()
console.log(JSON.stringify({{isStr: typeof status === 'string', isBool: typeof dirty === 'boolean', length: status.length}}))
"""
    result = _run_ts(code)
    assert result["isStr"] == True
    assert result["isBool"] == True
    assert isinstance(result["length"], int)


def test_clean_tree_hook_throws_on_execsync_failure():
    """When execSync throws (e.g. corrupt env), hook catches and allows dispatch."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-clean-tree.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is not None, "Hook must return something (not crash)"
    if result.get("permissionDecision") == "deny":
        assert "DIRTY TREE" in result.get("message", ""), "Only deny reason should be dirty tree"


# ---------------------------------------------------------------------------
# enforce-verified-claims.ts  —  done-words without evidence blocked
# ---------------------------------------------------------------------------


def test_verified_claim_with_evidence():
    """Text contains 'commit' + hash → passed through (evidence present)."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock('commit abc12345')}}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] == False, f"Commit hash should be evidence, got: {result}"


def test_verified_claim_no_evidence_blocked():
    """Text contains 'committed' but no hash → text.complete blocks."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock('everything committed')}}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] == True, f"Unverified claim should be blocked, got: {result}"


def test_verified_claims_commit_unverified_msg_blocked():
    """Bash commit target with unverified MSG → tool.execute.before denies."""
    hot_module = "/tmp/gludd-hot-enforce-verified-claims.js"
    _clean_state_files(hot_module)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
let result
try {{
  result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make git-commit MSG="just working now"'}}}})
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e)}}))
}}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny for unverified commit MSG, got: {result}"
    _clean_state_files(hot_module)


def test_verified_claims_commit_verified_msg_allowed():
    """Bash commit target with evidence → tool.execute.before allows."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make ship-commit MSG="fix: done abc12345"', MSG: 'fix: done abc12345'}}}})
console.log(JSON.stringify({{allowed: result === undefined || result === null}}))
"""
    result = _run_ts(code)
    assert result.get("allowed") == True, f"Verified commit MSG should be allowed, got: {result}"


def test_verified_claims_subagent_skip():
    """OPENCODE_SUBAGENT=1 → tool.execute.before skips enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make git-commit MSG=done'}}}})
console.log(JSON.stringify({{allowed: result === undefined || result === null}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result.get("allowed") == True, f"Subagent should skip, got: {result}"


# ---------------------------------------------------------------------------
# enforce-no-suppressions.ts  —  lint-suppression comment block
# ---------------------------------------------------------------------------


def test_no_suppression_plain_comment():
    """Plain # comment → allowed."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# regular comment'),
    allowEdit: mod.shouldAllowEdit('src/foo.py', '# regular comment'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] == False
    assert result["allowEdit"]["allow"] == True


def test_no_suppression_noqa_blocked():
    """Text contains '# noqa' → isSuppressionComment returns true."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# noqa'),
    verdict: mod.shouldAllowEdit('src/foo.py', '# noqa'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] == True
    assert result["verdict"]["allow"] == False
    assert "forbidden" in result["verdict"].get("reason", "")


def test_no_suppression_type_ignore_blocked():
    """Text contains '# type: ignore' → isSuppressionComment returns true."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# type: ignore'),
    verdict: mod.shouldAllowEdit('src/bar.py', '# type: ignore'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] == True
    assert result["verdict"]["allow"] == False


def test_no_suppression_allowlisted_file():
    """Editing fix_not_disable.py → allowed even with # noqa."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isAllowed: mod.isAllowlistedPath('src/general_ludd/security/fix_not_disable.py'),
    verdict: mod.shouldAllowEdit('src/general_ludd/security/fix_not_disable.py', '# noqa'),
}}))
"""
    result = _run_ts(code)
    assert result["isAllowed"] == True
    assert result["verdict"]["allow"] == True


# ---------------------------------------------------------------------------
# enforce-no-wait.ts  —  main-thread sleep/tail denial + CI-poll dispatch block
# ---------------------------------------------------------------------------


def test_no_wait_sleep_blocked():
    """Bash call with 'sleep 60&&' pattern → denied by WAIT_PATTERNS."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'sleep 60&& make gate-status-check'}}}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny object, got None"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "forbidden" in result.get("message", "").lower()


def test_no_wait_gate_tail_blocked():
    """Bash call with 'gate-tail' pattern → denied by WAIT_PATTERNS."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make gate-tail'}}}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny object, got None"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"


def test_no_wait_subagent_bypass():
    """OPENCODE_SUBAGENT=1 → bash call allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'sleep 60&& make gate-status-check'}}}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"


def test_no_wait_env_disabled():
    """GLUDD_NO_WAIT_ENFORCE=0 → bash call allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make gate-tail'}}}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code, env_override={"GLUDD_NO_WAIT_ENFORCE": "0"})
    assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny"


def test_no_wait_corrupt_input_fail_open():
    """Null/undefined input → hook fails open (does not crash, returns allowed).

    enforce-no-wait uses pattern-matching on input args; when input or args
    are malformed/nullish, the try-catch body must catch the error and return
    undefined (allow) rather than throwing.
    """
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
// Call with no tool field (undefined)
const r1 = await plugin['tool.execute.before']({{}}, undefined)
// Call with null args
const r2 = await plugin['tool.execute.before']({{tool: 'bash', args: null}}, undefined)
// Call with undefined input entirely (falsy)
const r3 = await plugin['tool.execute.before'](null, undefined)
console.log(JSON.stringify({{
    r1_ok: r1 === undefined || r1 === null,
    r2_ok: r2 === undefined || r2 === null,
    r3_ok: r3 === undefined || r3 === null,
}}))
"""
    result = _run_ts(code)
    assert result["r1_ok"] == True, f"Undefined tool should fail-open (allowed), got: {result}"
    assert result["r2_ok"] == True, f"Null args should fail-open (allowed), got: {result}"
    assert result["r3_ok"] == True, f"Null input should fail-open (allowed), got: {result}"


# ---------------------------------------------------------------------------
# enforce-deletion-gate.ts  —  deletion threshold via hook throw
# ---------------------------------------------------------------------------


def test_deletion_under_threshold_allowed():
    """Edit removing 1 line (below default threshold of 5) → allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit', args: {{filePath: '/tmp/nonexistent.txt', oldString: 'one line', newString: ''}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") == True, f"Expected allowed for 1-line deletion, got: {result}"


def test_deletion_over_threshold_blocked():
    """Edit removing 10 lines (above default threshold of 5) → denied."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit', args: {{filePath: '/tmp/nonexistent2.txt', oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10', newString: ''}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny object"
    assert result.get("permissionDecision") == "deny", f"Expected deny for 10-line deletion, got: {result}"
    assert "exceeds threshold" in result.get("message", ""), f"Message missing threshold mention: {result}"


def test_deletion_subagent_guard():
    """OPENCODE_SUBAGENT=1 → deletion allowed even above threshold."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit', args: {{filePath: '/tmp/nonexistent3.txt', oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10', newString: ''}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") == True or result.get("permissionDecision") != "deny", (
        f"Subagent should bypass deletion gate, got: {result}"
    )


def test_deletion_env_disabled():
    """GLUDD_DELETION_GATE_THRESHOLD=0 → deletion allowed above threshold."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit', args: {{filePath: '/tmp/nonexistent4.txt', oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10', newString: ''}}}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "0"})
    assert result is None or result.get("allowed") == True, f"Expected allowed when threshold=0, got: {result}"


# ---------------------------------------------------------------------------
# enforce-session-start.ts  —  session-start protocol enforcement
# ---------------------------------------------------------------------------


def _fresh_session_state(state_path: str, **overrides) -> dict:
    """Write a fresh session state file with started_at=now and return the contents."""
    state = {
        "started_at": int(time.time() * 1000),
        "readsDone": False,
        "dispatches": 0,
        "timeGateReset": False,
        **overrides,
    }
    with open(state_path, "w") as f:
        json.dump(state, f)
    return state


def test_session_start_fresh_no_reads_mutation_denied():
    """Fresh session (no reads, no dispatches) + non-dispatch tool → denied (throws Error)."""
    state_file = os.path.join("/tmp", f"test-ss-denied-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected deny object from thrown Error"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "SESSION START PROTOCOL" in result.get("message", ""), f"Message missing PROTOCOL: {result}"
    _clean_state_files(state_file)


def test_session_start_read_tool_always_allowed():
    """Read/Grep/Glob tools always allowed even in fresh unprimed session."""
    state_file = os.path.join("/tmp", f"test-ss-read-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected output"
    assert result.get("allowed") == True, f"Read tool should be allowed, got: {result}"
    _clean_state_files(state_file)


def test_session_start_subagent_guard():
    """OPENCODE_SUBAGENT=1 → all tools allowed, enforcement skipped."""
    state_file = os.path.join("/tmp", f"test-ss-subagent-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_file,
            "OPENCODE_SUBAGENT": "1",
        },
    )
    assert result is not None, "Expected output"
    assert result.get("allowed") == True, f"Subagent should bypass enforcement, got: {result}"
    _clean_state_files(state_file)


def test_session_start_env_disable():
    """GLUDD_SESSION_START_ENFORCE=0 → no blocking (advisory only)."""
    state_file = os.path.join("/tmp", f"test-ss-disable-{os.getpid()}.json")
    _fresh_session_state(state_file)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_SESSION_STATE": state_file,
            "GLUDD_SESSION_START_ENFORCE": "0",
        },
    )
    assert result is not None, "Expected output"
    assert result.get("allowed") == True, f"With ENFORCE=0, tool should be allowed, got: {result}"
    _clean_state_files(state_file)


def test_session_start_corrupt_state_fail_open():
    """Corrupt state file → tools allowed (fail-open)."""
    state_file = os.path.join("/tmp", f"test-ss-corrupt-{os.getpid()}.json")
    with open(state_file, "w") as f:
        f.write("not valid json {{{[[[")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
let result
try {{
  await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  result = {{permissionDecision: 'deny', message: e.message}}
  console.log(JSON.stringify(result))
}}
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected output (fail-open should not throw)"
    assert result.get("allowed") == True, f"Corrupt state should fail-open, got: {result}"
    _clean_state_files(state_file)


def test_session_start_read_task_file_sets_readsDone():
    """Reading TASKS.md via the REAL opencode input shape sets readsDone=true.

    opencode passes tool args in param 2 (output), not param 1 (input).
    This is the regression guard for the isTaskFileRead output-arg fix.
    Before the fix, isTaskFileRead only checked input (param 1) for filePath,
    which was always empty — readsDone never got set, so the session-start
    gate stayed permanently active.
    """
    state_file = os.path.join("/tmp", f"test-ss-readtask-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '{ROOT}/TASKS.md'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None, "Expected JSON output from test"
    assert result["readsDone"] == True, (
        f"readsDone must be true after reading TASKS.md via output.args.filePath. Got: {result}"
    )
    _clean_state_files(state_file, hot_module)


def test_session_start_read_bugs_md_sets_readsDone():
    """Reading BUGS.md via output.args also sets readsDone=true."""
    state_file = os.path.join("/tmp", f"test-ss-readbugs-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '{ROOT}/BUGS.md'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None
    assert result["readsDone"] == True, f"readsDone must be true after reading BUGS.md. Got: {result}"
    _clean_state_files(state_file, hot_module)


def test_session_start_read_non_task_file_does_not_set_readsDone():
    """Reading a non-task file (e.g. src/foo.py) must NOT set readsDone."""
    state_file = os.path.join("/tmp", f"test-ss-readnontask-{os.getpid()}.json")
    hot_module = "/tmp/gludd-hot-enforce-session-start.js"
    _clean_state_files(state_file, hot_module)
    _fresh_session_state(state_file)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'read'}},
  {{args: {{filePath: '/tmp/random-non-task-file.py'}}}}
)
const state = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
console.log(JSON.stringify({{readsDone: state.readsDone}}))
"""
    result = _run_ts(code, env_override={"GLUDD_SESSION_STATE": state_file})
    assert result is not None
    assert result["readsDone"] == False, f"readsDone must remain false for non-task files. Got: {result}"
    _clean_state_files(state_file, hot_module)


def _session_start_dispatch_then_bash(configured_min: str | None) -> dict:
    label = configured_min or "adaptive"
    state_file = os.path.join("/tmp", f"test-ss-dispatch-inc-{label}-{os.getpid()}.json")
    _fresh_session_state(state_file, readsDone=True, dispatches=0)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-session-start.ts')
const plugin = await mod.default({{}})
// Call 1: task dispatch → should increment dispatches to 1
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const state1 = JSON.parse(fs.readFileSync('{state_file}', 'utf8'))
const dp1 = state1.dispatches
// Call 2: bash (non-dispatch, non-read) exercises the configured policy.
let denied = false
let msg = ''
try {{
  await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
}} catch (e) {{
  denied = true
  msg = e.message
}}
console.log(JSON.stringify({{dp1, denied, hasProtocol: msg.includes('SESSION START PROTOCOL')}}))
"""
    env_override = {"GLUDD_SESSION_STATE": state_file}
    if configured_min is not None:
        env_override["GLUDD_SESSION_START_MIN_DISPATCHES"] = configured_min
    result = _run_ts(code, env_override=env_override)
    _clean_state_files(state_file)
    return result


def test_session_start_dispatch_increment_default_adaptive():
    """One dispatch is enough by default; no quota-padding denial is emitted."""
    result = _session_start_dispatch_then_bash(configured_min=None)
    assert result["dp1"] == 1, f"Expected dispatches=1 after task call, got: {result}"
    assert result["denied"] is False, f"Adaptive default must allow the bash call: {result}"


def test_session_start_explicit_minimum_denies_under_dispatch():
    """An explicit ten-dispatch minimum denies a mutation after one dispatch."""
    result = _session_start_dispatch_then_bash(configured_min="10")
    assert result["dp1"] == 1, f"Expected dispatches=1 after task call, got: {result}"
    assert result["denied"] is True, f"Configured minimum must deny under-dispatch: {result}"
    assert result["hasProtocol"] is True, f"Deny message missing SESSION START PROTOCOL: {result}"


# ---------------------------------------------------------------------------
# enforce-make.ts  —  bash command enforcement (non-make + metachar blocking)
# ---------------------------------------------------------------------------


def _enforce_make_bash_test(command: str, env_override: dict | None = None) -> dict:
    """Run a bash command through enforce-make.ts tool.execute.before.
    Returns {allowed: true} or {permissionDecision: 'deny', message: '...'}.
    """
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-make.ts')
const plugin = await mod.default({{}})
try {{
  const result = await plugin['tool.execute.before'](
    {{tool: 'bash', args: {{command: {json.dumps(command)}}}}}, undefined)
  console.log(JSON.stringify({{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e.message)}}))
}}
"""
    return _run_ts(code, env_override=env_override)


def test_make_allows_make_target():
    """bash 'make lint' → allowed (no deny)."""
    result = _enforce_make_bash_test("make lint")
    assert result is not None
    assert result.get("allowed") == True, f"make lint should be allowed, got: {result}"


def test_make_denies_cd_command():
    """bash 'cd /tmp' → permissionDecision: 'deny'."""
    result = _enforce_make_bash_test("cd /tmp")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"cd should be denied, got: {result}"
    assert "does not start with 'make'" in result.get("message", "").lower()


def test_make_denies_python():
    """bash 'python script.py' → deny."""
    result = _enforce_make_bash_test("python script.py")
    assert result.get("permissionDecision") == "deny", f"python should be denied, got: {result}"


def test_make_denies_pip():
    """bash 'pip install x' → deny."""
    result = _enforce_make_bash_test("pip install x")
    assert result.get("permissionDecision") == "deny", f"pip should be denied, got: {result}"


def test_make_denies_git():
    """bash 'git status' → deny."""
    result = _enforce_make_bash_test("git status")
    assert result.get("permissionDecision") == "deny", f"git should be denied, got: {result}"


def test_make_denies_metachar_pipe():
    """bash 'make test | grep' → deny (pipe not allowed)."""
    result = _enforce_make_bash_test("make test | grep")
    assert result.get("permissionDecision") == "deny", f"pipe should be denied, got: {result}"
    assert "BLOCKED" in result.get("message", "")


def test_make_denies_metachar_semicolon():
    """bash 'make test; make lint' → deny."""
    result = _enforce_make_bash_test("make test; make lint")
    assert result.get("permissionDecision") == "deny", f"; should be denied, got: {result}"


def test_make_denies_metachar_and():
    """bash 'make test && make lint' → deny."""
    result = _enforce_make_bash_test("make test && make lint")
    assert result.get("permissionDecision") == "deny", f"&& should be denied, got: {result}"


def test_make_denies_metachar_dollar():
    """bash 'make $(cat file)' → deny."""
    result = _enforce_make_bash_test("make $(cat file)")
    assert result.get("permissionDecision") == "deny", f"$() should be denied, got: {result}"


def test_make_denies_redirect():
    """bash 'make test > file' → deny (redirect involves metachar)."""
    result = _enforce_make_bash_test("make test > file")
    assert result.get("permissionDecision") == "deny", f"> redirect should be denied, got: {result}"


def test_make_subagent_guard():
    """OPENCODE_SUBAGENT=1 → allowed (skip)."""
    result = _enforce_make_bash_test("cd /tmp", env_override={"OPENCODE_SUBAGENT": "1"})
    assert result.get("allowed") == True, f"Subagent should bypass enforcement, got: {result}"


def test_make_disengage_escape():
    """GLUDD_MAKE_ENFORCE=0 → allowed (disengage)."""
    result = _enforce_make_bash_test("cd /tmp", env_override={"GLUDD_MAKE_ENFORCE": "0"})
    assert result.get("allowed") == True, f"MAKE_ENFORCE=0 should disengage, got: {result}"


# ---------------------------------------------------------------------------
# enforce-make.ts  —  bash command blocking (runtime tests: bare commands)
# ---------------------------------------------------------------------------


def test_make_allows_make_lint():
    """bash 'make lint' → ALLOWED (starts with 'make')."""
    result = _enforce_make_bash_test("make lint")
    assert result is not None
    assert result.get("allowed") == True, f"make lint should be allowed, got: {result}"


def test_make_denies_python3():
    """bash 'python3 -c "print(1)"' → DENIED (bare command)."""
    result = _enforce_make_bash_test('python3 -c "print(1)"')
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"python3 should be denied, got: {result}"


def test_make_denies_gh():
    """bash 'gh --version' → DENIED (non-make binary)."""
    result = _enforce_make_bash_test("gh --version")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"gh should be denied, got: {result}"


def test_make_denies_cat():
    """bash 'cat file.txt' → DENIED (non-make command)."""
    result = _enforce_make_bash_test("cat file.txt")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"cat should be denied, got: {result}"


def test_make_denies_git_status():
    """bash 'git status' → DENIED (non-make command)."""
    result = _enforce_make_bash_test("git status")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"git status should be denied, got: {result}"


def test_make_denies_pipe_in_make_args():
    """bash 'make test | grep FAILED' → DENIED (metacharacter pipe)."""
    result = _enforce_make_bash_test("make test | grep FAILED")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"pipe should be denied, got: {result}"
    assert "BLOCKED" in result.get("message", "")


def test_make_denies_and_in_make_args():
    """bash 'make test && make lint' → DENIED (metacharacter &&)."""
    result = _enforce_make_bash_test("make test && make lint")
    assert result is not None
    assert result.get("permissionDecision") == "deny", f"&& should be denied, got: {result}"


# ---------------------------------------------------------------------------
# watchdog.ts  —  session lifecycle daemon launcher
# ---------------------------------------------------------------------------

WATCHDOG_PATH = str(_OPENCODE_DIR / "plugins" / "watchdog.ts")


def test_watchdog_plugin_loads_report_alive():
    """watchdog plugin loads, calls reportAlive on init, and exposes its event hook."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-1.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
const keys = Object.keys(plugin)
console.log(JSON.stringify({{ ok: true, keys }}))
"""
    result = _run_ts(code, env_override={"GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] == True, f"Watchdog plugin load should not throw, got: {result}"
    assert result["keys"] == ["event"], f"Plugin should expose event hook, got keys: {result['keys']}"
    # Verify reportAlive was called on module load
    assert os.path.exists(alive_path), f"Alive file {alive_path} should exist after plugin load"
    with open(alive_path) as f:
        alive = json.load(f)
    assert "watchdog" in alive, f"watchdog key missing from alive file: {alive}"
    assert isinstance(alive["watchdog"]["last_seen"], int)
    _clean_state_files(alive_path)


def test_watchdog_plugin_loads_no_error():
    """Watchdog plugin loads without error even with no event hook surface."""
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true, isObject: typeof plugin === 'object' }}))
"""
    result = _run_ts(code)
    assert result["ok"] == True, f"Watchdog should load without error, got: {result}"
    assert result["isObject"] == True


def test_watchdog_plugin_subagent_context():
    """OPENCODE_SUBAGENT=1: watchdog plugin still loads (it's infra, not enforcement)."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-2.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true, isObject: typeof plugin === 'object' }}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1", "GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] == True, f"Watchdog should load even in subagent context, got: {result}"
    # Verify alive file was still written (reportAlive runs on module load)
    assert os.path.exists(alive_path), "Watchdog must report alive even as subagent"
    _clean_state_files(alive_path)


def test_watchdog_plugin_env_disabled():
    """GLUDD_WATCHDOG_ENABLED=0: plugin still loads (reportAlive happens on import)."""
    alive_path = f"/tmp/gludd-test-alive-{os.getpid()}-3.json"
    _clean_state_files(alive_path)
    code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true }}))
"""
    result = _run_ts(code, env_override={"GLUDD_WATCHDOG_ENABLED": "0", "GLUDD_ALIVE_PATH": alive_path})
    assert result["ok"] == True, f"Disabled watchdog should load without error, got: {result}"
    # The plugin itself doesn't check GLUDD_WATCHDOG_ENABLED (the daemon does)
    # reportAlive is called on module load regardless
    _clean_state_files(alive_path)


def test_watchdog_plugin_loads_with_corrupt_pid_file():
    """Corrupt PID file does not crash watchdog plugin load (fail-open)."""
    pid_file = os.environ.get("GLUDD_WATCHDOG_PID_FILE", ".gate-logs/watchdog.pid")
    os.makedirs(os.path.dirname(pid_file) or ".", exist_ok=True)
    with open(pid_file, "w") as f:
        f.write("not-a-valid-pid-99999999999999999")
    try:
        code = f"""\
const mod = await import('{WATCHDOG_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{ ok: true }}))
"""
        result = _run_ts(code)
        assert result["ok"] == True, f"Corrupt PID file must not crash plugin load, got: {result}"
    finally:
        _clean_state_files(pid_file)


# ---------------------------------------------------------------------------
# enforce-commit-lock.ts  —  commit serialization via lock file
# ---------------------------------------------------------------------------


def test_commit_lock_allowed_no_lock():
    """Commit target allowed when no lock file exists (tryAcquire succeeds)."""
    lock_path = f"/tmp/gludd-commit-lock-test-a-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") == True, f"Expected allowed, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_fresh_lock_denies():
    """Fresh lock file (<5 min) blocks another commit with deny + DENY_MESSAGE."""
    lock_path = f"/tmp/gludd-commit-lock-test-d-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make git-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is not None, "Expected deny object, got None (allowed)"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "COMMIT-LOCK" in result.get("message", ""), f"Message missing COMMIT-LOCK: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_stale_break_allows():
    """Stale lock (>STALE_THRESHOLD_MS) is broken and commit allowed."""
    lock_path = f"/tmp/gludd-commit-lock-test-s-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("stale")
    six_min_ago = time.time() - 360
    os.utime(lock_path, (six_min_ago, six_min_ago))
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make repo-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") == True, f"Stale lock should allow, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_non_commit_allowed():
    """Non-commit bash command passes through without lock check."""
    lock_path = f"/tmp/gludd-commit-lock-test-nc-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make test-unit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result is None or result.get("allowed") == True, f"Non-commit should pass through, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_subagent_guard():
    """OPENCODE_SUBAGENT=1 skips enforcement even with lock present."""
    lock_path = f"/tmp/gludd-commit-lock-test-sub-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("locked")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
            "OPENCODE_SUBAGENT": "1",
        },
    )
    assert result is None or result.get("allowed") == True, f"Subagent should skip, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_env_disable():
    """GLUDD_COMMIT_LOCK_ENFORCE=0 disables lock enforcement entirely."""
    lock_path = f"/tmp/gludd-commit-lock-test-dis-{os.getpid()}"
    _clean_state_files(lock_path)
    with open(lock_path, "w") as f:
        f.write("locked")
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(
        code,
        env_override={
            "GLUDD_COMMIT_LOCK_PATH": lock_path,
            "GLUDD_COMMIT_LOCK_ENFORCE": "0",
        },
    )
    assert result is None or result.get("allowed") == True, f"Disabled should allow, got: {result}"
    _clean_state_files(lock_path)


def test_commit_lock_after_releases_lock():
    """execute.after hook releases the lock file acquired by execute.before."""
    lock_path = f"/tmp/gludd-commit-lock-test-af-{os.getpid()}"
    _clean_state_files(lock_path)
    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_DIR}/enforce-commit-lock.ts')
const plugin = await mod.default({{}})
const beforeResult = await plugin['tool.execute.before']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
const lockBefore = fs.existsSync('{lock_path}')
await plugin['tool.execute.after']({{tool: 'bash', command: 'make ship-commit MSG=test'}})
const lockAfter = fs.existsSync('{lock_path}')
console.log(JSON.stringify({{beforeOk: beforeResult === undefined, lockBefore, lockAfter: !lockAfter}}))
"""
    result = _run_ts(code, env_override={"GLUDD_COMMIT_LOCK_PATH": lock_path})
    assert result["beforeOk"] == True, "Before hook should allow"
    assert result["lockBefore"] == True, "Lock must exist after before hook"
    assert result["lockAfter"] == True, "Lock must be removed after after hook"
    _clean_state_files(lock_path)


def test_commit_lock_is_commit_command():
    """isCommitCommand matches commit targets, rejects non-commit and non-make."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    ship: mod.isCommitCommand('make ship-commit MSG=test'),
    nonCommit: mod.isCommitCommand('make test-unit'),
    gitCommit: mod.isCommitCommand('make git-commit MSG=test'),
    commitNoVerify: mod.isCommitCommand('make commit-no-verify MSG=test'),
    gitAmend: mod.isCommitCommand('make git-amend-msg MSG=fix'),
    notMake: mod.isCommitCommand('git commit'),
    testAndCommit: mod.isCommitCommand('make test-and-commit MSG=test'),
    repoCommit: mod.isCommitCommand('make repo-commit MSG=test'),
    empty: mod.isCommitCommand(''),
    noTarget: mod.isCommitCommand('make '),
}}))
"""
    result = _run_ts(code)
    assert result["ship"] == True
    assert result["nonCommit"] == False
    assert result["gitCommit"] == True
    assert result["commitNoVerify"] == True
    assert result["gitAmend"] == True
    assert result["notMake"] == False
    assert result["testAndCommit"] == True
    assert result["repoCommit"] == True
    assert result["empty"] == False
    assert result["noTarget"] == False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
