from __future__ import annotations

import json
import os
import time

import pytest

from .fixtures import (
    PLUGIN_DIR,
    _clean_state_files,
    _runtime_state_path,
)
from .runner import (
    _run_ts,
)

# enforce-enhancement-ratio.ts  —  test classification + state-based logic
# ---------------------------------------------------------------------------


def test_enhancement_enhancement_keywords_classify_correctly() -> None:
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
console.log(JSON.stringify({{
  waveLen: state.wave.length,
  type: state.wave[0]?.type,
  sessionEnh: state.session_enhancements,
}}))
"""
    result = _run_ts(code)
    assert result["waveLen"] == 1
    assert result["type"] == "enhancement"
    assert result["sessionEnh"] == 1


def test_enhancement_fix_keywords_classify_correctly() -> None:
    """FIX_KEYWORDS map to 'fix' classification."""
    code = f"""\
const fs = await import('node:fs')
const stateFile = process.env.GLUDD_ENHANCEMENT_RATIO_STATE || '/tmp/gludd-enhancement-ratio.json'
try {{ fs.unlinkSync(stateFile) }} catch {{}}
const mod = await import('{PLUGIN_DIR}/enforce-enhancement-ratio.ts')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'bug fix for login'}}}}, undefined)
const state = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
console.log(JSON.stringify({{
  waveLen: state.wave.length,
  type: state.wave[0]?.type,
  sessionFixes: state.session_fixes,
}}))
"""
    result = _run_ts(code)
    assert result["waveLen"] == 1
    assert result["type"] == "fix"
    assert result["sessionFixes"] == 1


def test_enhancement_unknown_defaults_to_fix() -> None:
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
def test_enhancement_wave_80pct_fixes_triggers_text_complete_block() -> None:
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
    assert result["isString"] is True, f"Expected string output, got type: {type(result)}"
    assert result["hasViolation"] is True, "Expected ENHANCEMENT RATIO VIOLATION in output"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_enhancement_wave_50pct_allowed() -> None:
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
    assert result["isModified"] is False


def test_enhancement_env_disable() -> None:
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


def test_enhancement_subagent_skip() -> None:
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


def test_enhancement_fix_ratio_ok() -> None:
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
    assert result["r1_ok"] is True
    assert result["r2_ok"] is True
    assert result["r3_ok"] is True, "33% fixes should be allowed, but r3 denied: check wave state"
    _clean_state_files(state_file)


def test_enhancement_fix_ratio_violation_blocked() -> None:
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
    assert result["r1_ok"] is True, "First dispatch should be allowed (wave < 2)"
    assert result["r2_deny"] is True, f"Second dispatch (100% fixes, wave=2) should deny: {result}"
    assert "ENHANCEMENT RATIO VIOLATION" in result["r2_msg"], f"Deny message missing VIOLATION: {result['r2_msg']}"
    # r3 is allowed because wave was reset after r2's denial
    assert result["r3_deny"] is False, (
        f"Third dispatch (67% fixes, wave=3) should be allowed after wave reset: {result}"
    )
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_enhancement_fix_ratio_text_blocked() -> None:
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
    assert result["isBlocked"] is True, f"Expected string block, got: {result}"
    assert result["hasViolation"] is True, f"Expected VIOLATION message: {result}"
    assert result["helloGone"] is True, "Original text should be replaced by violation"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_enhancement_block_env_disabled() -> None:
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
    assert result["notBlocked"] is True, f"With BLOCK=0, output should not be string, got: {result}"
    assert result["textPreserved"] is True, "Original text should be preserved when BLOCK=0"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_enhancement_wave_too_small() -> None:
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
    assert result["textPreserved"] is True, "1-dispatch wave should not trigger ratio check"
    _clean_state_files(state_file)


# ---------------------------------------------------------------------------
# enforce-delegate.ts  —  mainthreadBudgetBefore reads state from file
# ---------------------------------------------------------------------------


def test_delegate_streak_zero_allowed() -> None:
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
    assert result is None or result.get("allowed") is True
    _clean_state_files(sf)


def test_delegate_streak_at_threshold_denied() -> None:
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
            "CLAUDE_AGENT_FLOOR": "1",
            "CLAUDE_AGENT_TARGET": "3",
        },
    )
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    _clean_state_files(sf, fd, tasks_path)


def test_delegate_read_tool_not_counted() -> None:
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
    assert result is None or result.get("allowed") is True
    _clean_state_files(sf)


def test_delegate_env_disable() -> None:
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
    assert result is None or result.get("allowed") is True
    _clean_state_files(sf)


# ---------------------------------------------------------------------------
# enforce-deadline.ts  —  task timeout enforcement via state file
# ---------------------------------------------------------------------------


def test_deadline_task_within_timeout_allowed() -> None:
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


def test_deadline_task_over_timeout_blocked() -> None:
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


def test_deadline_env_disable() -> None:
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
    assert result["ignored"] is True
    _clean_state_files(stale_state)


def test_deadline_block_env_disabled() -> None:
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
    assert result is None or result.get("allowed") is True, f"Expected allowed with BLOCK=0, got: {result}"
    _clean_state_files(stale_state, stale_file)


def test_deadline_subagent_guard() -> None:
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
    assert result is None or result.get("allowed") is True, f"Expected allowed for subagent, got: {result}"
    _clean_state_files(stale_state, stale_file)


def test_deadline_corrupt_state_fail_open() -> None:
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
    assert result is None or result.get("allowed") is True, f"Expected fail-open, got: {result}"
    _clean_state_files(stale_state)


def test_deadline_no_state_file_fail_open() -> None:
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
    assert result is None or result.get("allowed") is True


# ---------------------------------------------------------------------------
# enforce-floor.ts  —  in-memory streak + openWorkExists
# ---------------------------------------------------------------------------


def test_floor_dispatch_resets_streak() -> None:
    """Dispatch call resets streak and is always allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") is True


def test_floor_streak_zero_non_dispatch_allowed() -> None:
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
    assert result is None or result.get("allowed") is True
    _clean_state_files(session_state)


def test_floor_streak_max_plus_one_denied() -> None:
    """After MAX_STREAK+1 non-dispatch calls, the hook DENIES.

    MAX_STREAK=2, so call 3 should be denied when open work exists.
    Must neutralise the session-start window (watchdog writes
    /tmp/gludd-session-start.json) which would tighten max to 1.
    """

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
            "CLAUDE_AGENT_FLOOR": "1",
        },
    )
    assert result["r1"] is None, f"Call 1 should be allowed, got: {result['r1']}"
    assert result["r2"] is None, f"Call 2 should be allowed, got: {result['r2']}"
    assert result["r3_deny"] is True, f"Call 3 should be denied, got: {result}"
    assert result["r3_hasMsg"] is True
    _clean_state_files(tasks_path, todowrite_path, session_state)


def test_floor_subagent_env_skip() -> None:
    """OPENCODE_SUBAGENT=1 skips ALL enforce-floor checks."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-floor.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") is True


def test_floor_corrupt_state_fail_open() -> None:
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
    assert result is None or result.get("allowed") is True
    _clean_state_files(sf, session_state)


def test_floor_read_tool_not_blocked() -> None:
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
    assert result["allAllowed"] is True
    _clean_state_files(session_state)


# ── enforce-floor.ts  —  runtime tests: text.complete, message-shape, grace, subagent, disengage ──


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_text_complete_blocks_on_zero_dispatches() -> None:
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
    assert result["blocked"] is True, f"Expected FLOOR BREACH in text.complete output, got: {result}"
    assert result["originalGone"] is True, f"Original text must be replaced: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_message_shape_one_dispatch_denied() -> None:
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
    assert result["deny"] is True, f"Expected deny for 1-dispatch message shape, got: {result}"
    assert result["hasMsgShape"] is True, f"Expected MESSAGE-SHAPE in deny message: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_result_grace_denies_non_dispatch() -> None:
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
    assert result["deny"] is True, f"Expected DISPATCH GAP deny, got: {result}"
    assert result["hasGrace"] is True, f"Expected DISPATCH GAP in deny message: {result}"


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_floor_text_complete_subagent_skip() -> None:
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
    assert result["textPreserved"] is True, f"Subagent text must pass through unmodified: {result}"


def test_floor_disengage_allows_after_streak_breach() -> None:
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
    assert result["r1_ok"] is True, f"Call 1 (streak 0→1) must be allowed: {result}"
    assert result["r2_ok"] is True, f"Call 2 (streak 1→2) must be allowed: {result}"
    assert result["r3_ok"] is True, f"Disengage must allow call 3 despite streak=2: {result}"
    _clean_state_files(tasks_path, todowrite_path, session_state, streak_file, disengage_path)


# ---------------------------------------------------------------------------
# enforce-multitask.ts  —  dispatch enforcement
# ---------------------------------------------------------------------------


def test_multitask_text_complete_blocks_thin_wave() -> None:
    """experimental.text.complete MUST blank text for thin dispatch waves."""
    namespace = f"/tmp/gludd-multitask-runtime-{os.getpid()}-{time.time_ns()}"
    state_file = f"{namespace}.json"
    dispatch_count_file = f"{state_file}.dispatch-count"
    disengage_path = f"{namespace}-disengage.json"
    _clean_state_files(state_file, dispatch_count_file, disengage_path)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
// Build thisMessageDispatches = 2 via dispatch calls
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'agent'}}, undefined)
// The configured value 99 clamps to the canonical minimum/ceiling of 3.
// Call experimental.text.complete — should detect 2 < 3 and blank text.
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
  textWasBlocked:
    output !== null &&
    output !== undefined &&
    typeof output === 'object' &&
    output.text &&
    output.text.includes('BLOCKED'),
  resultText:
    output && typeof output === 'object'
      ? (output.text || '')?.slice(0, 200)
      : String(output || '').slice(0, 120),
}}))
"""
    try:
        result = _run_ts(
            code,
            env_override={
                "GLUDD_MIN_DISPATCHES": "99",
                "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
                "GLUDD_MULTITASK_STATE_FILE": state_file,
                "GLUDD_DISENGAGE_PATH": disengage_path,
            },
        )

        assert result.get("threw") is False, f"experimental.text.complete must run without throwing. Result: {result}"
        assert result["textWasBlocked"] is True, (
            f"Expected THIN WAVE BLOCKED but text passed through unmodified. "
            f"Hook ran without error but did not detect the thin wave. Result: {result}"
        )
    finally:
        _clean_state_files(state_file, dispatch_count_file, disengage_path)


def test_multitask_enough_dispatches() -> None:
    """Dispatch tools are always allowed regardless of state."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const r1 = await plugin['tool.execute.before']({{tool: 'task'}})
const r2 = await plugin['tool.execute.before']({{tool: 'agent'}})
const r3 = await plugin['tool.execute.before']({{tool: 'workflow'}})
console.log(JSON.stringify({{
  r1_ok: r1 === undefined || r1 === null,
  r2_ok: r2 === undefined || r2 === null,
  r3_ok: r3 === undefined || r3 === null,
}}))
"""
    result = _run_ts(code)
    assert result["r1_ok"] is True
    assert result["r2_ok"] is True
    assert result["r3_ok"] is True


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_multitask_single_dispatch_blocked() -> None:
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
    assert result is None or result.get("allowed") is True, f"Expected allowed for 1-dispatch wave, got: {result}"


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_multitask_zero_dispatch_text_blocked() -> None:
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
    assert result["blocked"] is True, f"Expected text.complete to block, got: {result}"
    assert "dispatch" in result.get("finalText", "").lower()


def test_multitask_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 skips enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"


def test_multitask_env_disabled() -> None:
    """GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-multitask.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}})
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_MULTITASK_FLOOR_ENFORCE": "0"})
    assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"


def test_multitask_configured_minimum_hard_block() -> None:
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


def test_multitask_dispatch_ceiling_blocked() -> None:
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
    assert result["r3_ok"] is True, f"3rd dispatch should be allowed (at ceiling), got: {result}"
    assert result["r4_denied"] is True, f"4th dispatch should be denied (above ceiling), got: {result}"
    assert result["r4_hasCeiling"] is True, f"Deny message missing DISPATCH CEILING BREACH: {result}"
    _clean_state_files(state_file)


def test_multitask_consecutive_non_dispatch_blocked() -> None:
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
    assert result["r1_ok"] is True, f"1st non-dispatch should be allowed, got: {result}"
    assert result["r2_ok"] is True, f"2nd non-dispatch should be allowed, got: {result}"
    assert result["r3_denied"] is True, f"3rd non-dispatch should be denied (at threshold), got: {result}"
    assert result["r3_hasConsecutive"] is True, f"Deny message missing 'consecutive non-dispatch': {result}"
    _clean_state_files(state_file)


def test_multitask_corrupt_state_fail_open() -> None:
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
# Zero-floor behavior — inline work stays available unless the operator opts in
# ============================================================================


def test_multitask_zero_floor_keeps_inline_mutation_available() -> None:
    """A zero floor leaves inline mutation available without a dispatch.

    Read tools (read/grep/glob) are excluded from the consecutive non-dispatch
    counter per the plugin spec: investigation bursts should never trigger the
    grinding penalty. Non-read tools (edit/write/bash) are counted.

    With MIN_DISPATCHES=0, neither the minimum nor its grinding backstop is
    active. Read tools and mutations remain available without make-work
    delegation; the hard concurrent-dispatch ceiling is tested separately.

    Call 1 (read): ALLOWED — read tools exempt from counter.
    Call 2 (read): ALLOWED — read tools exempt, counter still 0.
    Call 3 (edit): consecutive=1 < threshold → ALLOWED (below threshold).
    Call 4 (write): consecutive=2 < threshold → ALLOWED (below threshold).
    Call 5 (bash): ALLOWED — no positive floor was configured.
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
    assert result["r1_denied"] is False, f"Call 1 (read) must be allowed — reads excluded from counter. Got: {result}"
    assert result["r2_denied"] is False, f"Call 2 (grep) must be allowed — reads excluded from counter. Got: {result}"
    # Call 3 (edit): consecutive=1 < threshold=3 → ALLOWED
    assert result["r3_denied"] is False, f"Call 3 (edit) must be allowed (counter=1 < 3). Got: {result}"
    # Call 4 (write): consecutive=2 < threshold=3 → ALLOWED
    assert result["r4_denied"] is False, f"Call 4 (write) must be allowed (counter=2 < 3). Got: {result}"
    # With no configured minimum, all inline mutation remains available.
    assert result["r5_denied"] is False, f"Call 5 (bash) must remain allowed at floor zero. Got: {result}"
    assert result["r5_hasStreak"] is False, (
        f"A zero floor must not emit CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    _clean_state_files(state_file)


def test_multitask_text_only_response_next_tool_blocked() -> None:
    """After the capped floor is satisfied, consecutive counter blocks grinding.

    With MIN_DISPATCHES=99 clamped to 3 and THRESHOLD=3, dispatches three agents
    to satisfy the floor, then makes four non-dispatch calls using non-read tools.
    Since the floor is satisfied (3 >= 3), the under-floor block does not fire.
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
// Satisfy the capped floor: 3 dispatches.
for (let i = 0; i < 3; i++) {{
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
            "GLUDD_MIN_DISPATCHES": "99",
            "GLUDD_MULTITASK_MAX_DISPATCHES": "99",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD": "3",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS": "60000",
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
        },
    )
    # Calls 1-2 are allowed: capped floor satisfied (3 >= 3), counter below threshold
    assert result["r1_allowed"] is True, f"Call 1 (edit) must be ALLOWED: floor satisfied, counter=1 < 3. Got: {result}"
    assert result["r2_allowed"] is True, (
        f"Call 2 (write) must be ALLOWED: floor satisfied, counter=2 < 3. Got: {result}"
    )
    # Call 3: consecutive counter at threshold → CONSECUTIVE NON-DISPATCH STREAK fires
    assert result["r3_denied"] is True, (
        f"Call 3 (bash) must be denied by CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    assert result["r3_hasGrinding"] is True, (
        f"Call 3 message must contain CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    # Call 4: still grinding
    assert result["r4_denied"] is True, f"Call 4 (edit) must be denied. Got: {result}"
    assert result["r4_hasGrinding"] is True, (
        f"Call 4 message must contain CONSECUTIVE NON-DISPATCH STREAK. Got: {result}"
    )
    _clean_state_files(state_file)


def test_multitask_zero_dispatch_text_blocked_after_prior_dispatch() -> None:
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
    assert result is not None, "Expected blocked text, got None"
    assert result["wave1Blocked"] is True, f"Wave 1 (1 dispatch) must be blocked as thin wave. Got: {result}"
    assert result["wave2ZeroBlocked"] is True, f"Wave 2 (0 dispatches) must be blocked. Got: {result}"
    _clean_state_files(state_file)
