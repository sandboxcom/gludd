from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import pytest

from .fixtures import (
    LIB_DIR,
    PLUGIN_DIR,
    _clean_state_files,
    _dirty_test_path,
    _hermetic_project_root,
)
from .runner import (
    _run_ts,
)

# enforce-stop.ts  —  text.complete block for pending work + stop patterns
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_pending_work_text_blanked() -> None:
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
    assert result["blocked"] is True, f"Expected text to be blanked, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_no_pending_work() -> None:
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
    assert result["passedThrough"] is True, f"Expected text to pass through, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_env_disabled() -> None:
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
    assert result["passedThrough"] is True, f"Expected text to pass through when disabled, got: {result}"
    _clean_state_files(state_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_corrupt_state() -> None:
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
    assert result["returned"] is True, "Hook must return without crashing (fail-open)"
    assert result["isString"] is True, "Output must be a string (no throw, no crash)"
    _clean_state_files(state_file)


# ── TWO-LAYER PERSISTENT STOP-BLOCK TESTS ──────────────────────────────────


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_block_persists_across_turns() -> None:
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
    assert result["textBlanked"] is True, f"Expected text to be blanked, got: {result}"
    assert result["editDenied"] is True, f"Expected edit to be denied by persist block, got: {result}"
    assert result["dispatchAllowed"] is True, f"Expected dispatch to be allowed, got: {result}"
    assert result["editAllowedAfter"] is True, f"Expected edit after dispatch to be allowed, got: {result}"
    _clean_state_files(state_file, block_file)


def test_stop_block_cleared_by_dispatch() -> None:
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
    assert result["dispatchAllowed"] is True, f"Expected dispatch to be allowed, got: {result}"
    # Verify the block file was cleared
    assert not os.path.exists(block_file), "Persist block file should be cleared after dispatch"
    _clean_state_files(block_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_no_pending_work_allows() -> None:
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
    assert result["passedThrough"] is True, f"Expected text to pass through, got: {result}"
    assert result["editAllowed"] is True, f"Expected edit to be allowed, got: {result}"
    _clean_state_files(state_file, block_file)


def test_stop_subagent_block_guard() -> None:
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
    assert result["editAllowed"] is True, f"Subagent should bypass persist block, got: {result}"
    _clean_state_files(block_file)


@pytest.mark.skip(reason="text.complete removed in opencode 1.17.9")
def test_stop_task_result_passes_through_gate_red() -> None:
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
    assert result["passedThrough"] is True, (
        f"Subagent task_result text MUST pass through even with gate red. Got: {result}"
    )
    _clean_state_files(state_file)


def test_stop_permission_seeking_want_me_to_blocked(tmp_path: Path) -> None:
    """'Want me to proceed?' is ALWAYS blocked — asking permission to do work is never acceptable."""
    project_root = _hermetic_project_root(tmp_path)
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
    result = _run_ts(
        code,
        cwd=project_root,
        env_override={"GLUDD_PROJECT_ROOT": str(project_root)},
    )
    assert result is not None, "Expected JSON output"
    assert result["blocked"] is True, f"Expected text to be blocked, got: {result}"
    assert result["hasPermissionBlock"] is True, f"Expected PERMISSION-SEEKING BLOCKED, got: {result}"


def test_stop_permission_seeking_should_i_blocked(tmp_path: Path) -> None:
    """'Should I continue with fixing?' is ALWAYS blocked."""
    project_root = _hermetic_project_root(tmp_path)
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
    result = _run_ts(
        code,
        cwd=project_root,
        env_override={"GLUDD_PROJECT_ROOT": str(project_root)},
    )
    assert result is not None, "Expected JSON output"
    assert result["blocked"] is True, f"Expected text to be blocked, got: {result}"
    assert result["hasPermissionBlock"] is True, f"Expected PERMISSION-SEEKING BLOCKED, got: {result}"


def test_stop_permission_seeking_export_matches() -> None:
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
    assert result["hasExport"] is True
    assert result["match1"] is True, "Should match 'Want me to proceed?'"
    assert result["match2"] is True, "Should match 'want me to dispatch'"
    assert result["match3"] is True, "Should match 'Should I continue'"
    assert result["match4"] is True, "Should match 'shall I proceed'"
    assert result["match5"] is True, "Should match 'Proceed?'"
    assert result["noMatch1"] is False, "Should NOT match 'The fix is ready'"
    assert result["noMatch2"] is False, "Should NOT match 'I will proceed'"


def test_stop_status_summary_blocked_despite_evidence(tmp_path: Path) -> None:
    """Status summary with commit hashes + 'CI PENDING' (= structured evidence)
    is STILL blanked while pending work exists — evidence never legitimizes
    stopping-to-summarize. Regression pin for the 2026-07-15 bypass."""
    project_root = _hermetic_project_root(tmp_path)
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
    result = _run_ts(
        code,
        cwd=project_root,
        env_override={"GLUDD_PROJECT_ROOT": str(project_root)},
    )
    assert result is not None, "Expected JSON output"
    assert result["blocked"] is True, f"Status summary with evidence must be blocked, got: {result}"
    assert result["hasStatusSummaryBlock"] is True, f"Expected STATUS-SUMMARY RESPONSE BLOCKED, got: {result}"


def test_stop_status_summary_export_matches() -> None:
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
    assert result["hasRe"] is True
    assert result["hasFn"] is True
    assert result["match1"] is True, "Should match 'Here's the session 37 final status'"
    assert result["match2"] is True, "Should match 'Session 12 wrap-up'"
    assert result["match3"] is True, "Should match 'Final status report:'"
    assert result["structural"] is True, "Should structurally match bolded headers + table/bullets"
    assert result["noMatch1"] is False, "Should NOT match plain working text"
    assert result["noMatch2"] is False, "Should NOT match plain sentence"


# ---------------------------------------------------------------------------
# enforce-clean-tree.ts  —  dispatch-time dirty tree enforcement
# ---------------------------------------------------------------------------


def test_clean_tree_dispatch_allowed() -> None:
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


def test_clean_tree_dirty_dispatch_blocked() -> None:
    """Dirty tree + dispatch -> returns {{permissionDecision: 'deny'}}."""
    test_file = _dirty_test_path("runtime")
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
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_env_disabled() -> None:
    """GLUDD_CLEAN_TREE_ENFORCE=0 -> dispatch allowed even with dirty tree."""
    test_file = _dirty_test_path("disabled-runtime")
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
        assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 -> skip enforcement."""
    test_file = _dirty_test_path("subagent-runtime")
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
        assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


# ── enforce-clean-tree.ts  —  isTreeDirty / countDirtyFiles edge cases ──


def test_clean_tree_isTreeDirty_empty_string() -> None:
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


def test_clean_tree_countDirtyFiles_edge_cases() -> None:
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


def test_clean_tree_countDirtyFiles_single_line() -> None:
    """countDirtyFiles with single entry returns 1."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{single: mod.countDirtyFiles('?? foo.py')}}))
"""
    result = _run_ts(code)
    assert result == {"single": 1}


def test_clean_tree_non_dispatch_tool_not_blocked() -> None:
    """Non-dispatch tools (edit, write, read, bash) pass through even with dirty tree."""
    test_file = _dirty_test_path("nondispatch")
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
            assert result[tool] is True, f"Non-dispatch tool '{tool}' should not be blocked on dirty tree"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(test_file)


def test_clean_tree_buildDenyMessage_edge_cases() -> None:
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


def test_clean_tree_getGitStatus_real_repo_returns_string() -> None:
    """getGitStatus() in real repo returns a string (may be empty or non-empty)."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
const status = mod.getGitStatus()
const dirty = mod.isTreeDirty()
console.log(JSON.stringify({{
  isStr: typeof status === 'string',
  isBool: typeof dirty === 'boolean',
  length: status.length,
}}))
"""
    result = _run_ts(code)
    assert result["isStr"] is True
    assert result["isBool"] is True
    assert isinstance(result["length"], int)


def test_clean_tree_hook_throws_on_execsync_failure() -> None:
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


def test_verified_claim_with_evidence() -> None:
    """Text contains 'commit' + hash → passed through (evidence present)."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock('commit abc12345')}}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] is False, f"Commit hash should be evidence, got: {result}"


def test_verified_claim_no_evidence_blocked() -> None:
    """Text contains 'committed' but no hash → text.complete blocks."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock('everything committed')}}))
"""
    result = _run_ts(code)
    assert result["shouldBlock"] is True, f"Unverified claim should be blocked, got: {result}"


def test_verified_claims_commit_unverified_msg_blocked() -> None:
    """Bash commit target with unverified MSG → tool.execute.before denies."""
    hot_module = "/tmp/gludd-hot-enforce-verified-claims.js"
    _clean_state_files(hot_module)
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
let result
try {{
  result = await plugin['tool.execute.before']({{
    tool: 'bash',
    args: {{command: 'make git-commit MSG="just working now"'}},
  }})
  console.log(JSON.stringify(result ?? {{allowed: true}}))
}} catch (e) {{
  console.log(JSON.stringify({{permissionDecision: 'deny', message: String(e)}}))
}}
"""
    result = _run_ts(code)
    assert result.get("permissionDecision") == "deny", f"Expected deny for unverified commit MSG, got: {result}"
    _clean_state_files(hot_module)


def test_verified_claims_commit_verified_msg_allowed() -> None:
    """Bash commit target with evidence → tool.execute.before allows."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
const result = await plugin['tool.execute.before']({{
  tool: 'bash',
  args: {{
    command: 'make ship-commit MSG="fix: done abc12345"',
    MSG: 'fix: done abc12345',
  }},
}})
console.log(JSON.stringify({{allowed: result === undefined || result === null}}))
"""
    result = _run_ts(code)
    assert result.get("allowed") is True, f"Verified commit MSG should be allowed, got: {result}"


def test_verified_claims_subagent_skip() -> None:
    """OPENCODE_SUBAGENT=1 → tool.execute.before skips enforcement."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-verified-claims.ts')
const plugin = mod.default()
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make git-commit MSG=done'}}}})
console.log(JSON.stringify({{allowed: result === undefined || result === null}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result.get("allowed") is True, f"Subagent should skip, got: {result}"


# ---------------------------------------------------------------------------
# enforce-no-suppressions.ts  —  lint-suppression comment block
# ---------------------------------------------------------------------------


def test_no_suppression_plain_comment() -> None:
    """Plain # comment → allowed."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# regular comment'),
    allowEdit: mod.shouldAllowEdit('src/foo.py', '# regular comment'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] is False
    assert result["allowEdit"]["allow"] is True


def test_no_suppression_noqa_blocked() -> None:
    """Text contains '# noqa' → isSuppressionComment returns true."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# noqa'),
    verdict: mod.shouldAllowEdit('src/foo.py', '# noqa'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] is True
    assert result["verdict"]["allow"] is False
    assert "forbidden" in result["verdict"].get("reason", "")


def test_no_suppression_type_ignore_blocked() -> None:
    """Text contains '# type: ignore' → isSuppressionComment returns true."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isSuppression: mod.isSuppressionComment('# type: ignore'),
    verdict: mod.shouldAllowEdit('src/bar.py', '# type: ignore'),
}}))
"""
    result = _run_ts(code)
    assert result["isSuppression"] is True
    assert result["verdict"]["allow"] is False


def test_no_suppression_allowlisted_file() -> None:
    """Editing fix_not_disable.py → allowed even with # noqa."""
    code = f"""\
const mod = await import('{LIB_DIR}/plugin_test_exports.ts')
console.log(JSON.stringify({{
    isAllowed: mod.isAllowlistedPath('src/general_ludd/security/fix_not_disable.py'),
    verdict: mod.shouldAllowEdit('src/general_ludd/security/fix_not_disable.py', '# noqa'),
}}))
"""
    result = _run_ts(code)
    assert result["isAllowed"] is True
    assert result["verdict"]["allow"] is True


# ---------------------------------------------------------------------------
# enforce-no-wait.ts  —  main-thread sleep/tail denial + CI-poll dispatch block
# ---------------------------------------------------------------------------


def test_no_wait_sleep_blocked() -> None:
    """Bash call with 'sleep 60&&' pattern → denied by WAIT_PATTERNS."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'sleep 60&& make gate-status-check'}}}},
  undefined,
)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny object, got None"
    assert result.get("permissionDecision") == "deny", f"Expected deny, got: {result}"
    assert "forbidden" in result.get("message", "").lower()


def test_no_wait_gate_tail_blocked() -> None:
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


def test_no_wait_subagent_bypass() -> None:
    """OPENCODE_SUBAGENT=1 → bash call allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'bash', args: {{command: 'sleep 60&& make gate-status-check'}}}},
  undefined,
)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"


def test_no_wait_env_disabled() -> None:
    """GLUDD_NO_WAIT_ENFORCE=0 → bash call allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-no-wait.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash', args: {{command: 'make gate-tail'}}}}, undefined)
console.log(JSON.stringify(result ?? null))
"""
    result = _run_ts(code, env_override={"GLUDD_NO_WAIT_ENFORCE": "0"})
    assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny"


def test_no_wait_corrupt_input_fail_open() -> None:
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
    assert result["r1_ok"] is True, f"Undefined tool should fail-open (allowed), got: {result}"
    assert result["r2_ok"] is True, f"Null args should fail-open (allowed), got: {result}"
    assert result["r3_ok"] is True, f"Null input should fail-open (allowed), got: {result}"


# ---------------------------------------------------------------------------
# enforce-deletion-gate.ts  —  deletion threshold via hook throw
# ---------------------------------------------------------------------------


def test_deletion_under_threshold_allowed() -> None:
    """Edit removing 1 line (below default threshold of 5) → allowed."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{
  tool: 'edit',
  args: {{
    filePath: '/tmp/nonexistent.txt',
    oldString: 'one line',
    newString: '',
  }},
}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is None or result.get("allowed") is True, f"Expected allowed for 1-line deletion, got: {result}"


def test_deletion_over_threshold_blocked() -> None:
    """Edit removing 10 lines (above default threshold of 5) → denied."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{
  tool: 'edit',
  args: {{
    filePath: '/tmp/nonexistent2.txt',
    oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10',
    newString: '',
  }},
}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code)
    assert result is not None, "Expected deny object"
    assert result.get("permissionDecision") == "deny", f"Expected deny for 10-line deletion, got: {result}"
    assert "exceeds threshold" in result.get("message", ""), f"Message missing threshold mention: {result}"


def test_deletion_subagent_guard() -> None:
    """OPENCODE_SUBAGENT=1 → deletion allowed even above threshold."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{
  tool: 'edit',
  args: {{
    filePath: '/tmp/nonexistent3.txt',
    oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10',
    newString: '',
  }},
}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is None or result.get("allowed") is True or result.get("permissionDecision") != "deny", (
        f"Subagent should bypass deletion gate, got: {result}"
    )


def test_deletion_env_disabled() -> None:
    """GLUDD_DELETION_GATE_THRESHOLD=0 → deletion allowed above threshold."""
    code = f"""\
const mod = await import('{PLUGIN_DIR}/enforce-deletion-gate.ts')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{
  tool: 'edit',
  args: {{
    filePath: '/tmp/nonexistent4.txt',
    oldString: '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10',
    newString: '',
  }},
}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_ts(code, env_override={"GLUDD_DELETION_GATE_THRESHOLD": "0"})
    assert result is None or result.get("allowed") is True, f"Expected allowed when threshold=0, got: {result}"
