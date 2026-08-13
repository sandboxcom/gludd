"""E2e test for enforce-deadline.ts: task deadline timeout enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types,
verifying the deny/allow/env-disable/subagent-guard/fail-open cycle.
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
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-deadline.ts"

_ts_counter = 0

_STATE_NAMESPACE = f"{os.getpid()}-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"
_STATE_ROOT = Path(tempfile.gettempdir())
DEADLINE_STATE = str(
    _STATE_ROOT / f"gludd-task-deadlines-e2e-{_STATE_NAMESPACE}.json"
)
STALE_FILE = str(_STATE_ROOT / f"gludd-task-stale-e2e-{_STATE_NAMESPACE}.json")
WARNINGS_LOG = str(
    _STATE_ROOT / f"gludd-task-deadlines-e2e-{_STATE_NAMESPACE}.warnings.log"
)


def test_deadline_state_paths_are_process_isolated() -> None:
    """Parallel pytest workers must never share mutable deadline state."""
    process_token = str(os.getpid())
    for state_path in (DEADLINE_STATE, STALE_FILE, WARNINGS_LOG):
        assert process_token in state_path

# The plugin intentionally persists deadline state across hook invocations.
# Keep this module on one xdist worker so its fixed, shared E2E paths cannot be
# cleaned by a neighboring test while another hook is recording a breach.
pytestmark = pytest.mark.xdist_group("enforcement-shared-state")


def _clean_state() -> None:
    for f in (DEADLINE_STATE, STALE_FILE, WARNINGS_LOG):
        with contextlib.suppress(OSError):
            Path(f).unlink()


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"deadline_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env.update({
            "GLUDD_TASK_DEADLINE_STATE": DEADLINE_STATE,
            "GLUDD_TASK_STALE_FILE": STALE_FILE,
            "GLUDD_TASK_DEADLINE_WARNINGS": WARNINGS_LOG,
        })
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
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
            tmp.unlink()


# ─── Within timeout allows dispatch ─────────────────────────────────────────


def test_within_timeout_allows_non_dispatch_tool():
    """Tasks within TASK_TIMEOUT_MS do NOT block tool calls."""
    _clean_state()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_TASK_TIMEOUT_MS": "600000"})
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Within timeout should allow, got: {result}"
    )


# ─── Over timeout blocks non-dispatch tools ──────────────────────────────────


def test_over_timeout_blocks_subsequent_tool():
    """Task exceeding timeout -> hook denies subsequent tool calls when BLOCK=1."""
    _clean_state()

    raw_id = "md5:test-task-over-timeout"
    old = int((time.time() - 2) * 1000)
    Path(DEADLINE_STATE).write_text(json.dumps({raw_id: old}))

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_TASK_TIMEOUT_MS": "1000",
        "GLUDD_TASK_DEADLINE_BLOCK": "1",
    })
    assert result is not None, "Expected deny result for over-timeout task"
    assert result.get("permissionDecision") == "deny", (
        f"Expected deny, got: {result}"
    )
    assert "TASK DEADLINE EXCEEDED" in result.get("message", "")


def test_over_timeout_with_block_disabled_does_not_block():
    """BLOCK=0: warns but returns void (no deny)."""
    _clean_state()

    raw_id = "md5:test-block-disabled"
    old = int((time.time() - 2) * 1000)
    Path(DEADLINE_STATE).write_text(json.dumps({raw_id: old}))

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_TASK_TIMEOUT_MS": "1000",
        "GLUDD_TASK_DEADLINE_BLOCK": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"BLOCK=0 should not deny; got: {result}"
    )


# ─── Env var disable ─────────────────────────────────────────────────────────


def test_deadline_disabled_env_var():
    """GLUDD_TASK_DEADLINE_ENABLED=0 skips all enforcement."""
    _clean_state()

    raw_id = "md5:test-disabled"
    old = int((time.time() - 2) * 1000)
    Path(DEADLINE_STATE).write_text(json.dumps({raw_id: old}))

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_TASK_TIMEOUT_MS": "1000",
        "GLUDD_TASK_DEADLINE_BLOCK": "1",
        "GLUDD_TASK_DEADLINE_ENABLED": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"ENABLED=0 should skip enforcement, got: {result}"
    )


# ─── Subagent guard ──────────────────────────────────────────────────────────


def test_subagent_skips_deadline_check():
    """OPENCODE_SUBAGENT=1 bypasses all deadline enforcement."""
    _clean_state()

    raw_id = "md5:test-subagent"
    old = int((time.time() - 2) * 1000)
    Path(DEADLINE_STATE).write_text(json.dumps({raw_id: old}))

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_TASK_TIMEOUT_MS": "1000",
        "GLUDD_TASK_DEADLINE_BLOCK": "1",
        "OPENCODE_SUBAGENT": "1",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip enforcement, got: {result}"
    )


# ─── Dispatch recording ──────────────────────────────────────────────────────


def test_dispatch_recorded_in_state_file():
    """Dispatching a task records a start timestamp in the state file."""
    _clean_state()

    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const args = {{task_id: 'e2e-task-1', description: 'test dispatch recording'}}
await plugin['tool.execute.before']({{tool: 'task'}}, {{args}})
const raw = fs.readFileSync('{DEADLINE_STATE}', 'utf8')
const state = JSON.parse(raw)
console.log(JSON.stringify({{state, task_id_exists: 'e2e-task-1' in state}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result.get("task_id_exists") is True, (
        f"Expected task 'e2e-task-1' recorded in state, got: {result}"
    )


# ─── On-completion cleanup ───────────────────────────────────────────────────


def test_completion_removes_task_from_state():
    """tool.execute.after on a dispatch tool removes the task entry."""
    _clean_state()

    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const args = {{task_id: 'e2e-complete-me', description: 'test completion cleanup'}}
await plugin['tool.execute.before']({{tool: 'task'}}, {{args}})
await plugin['tool.execute.after']({{tool: 'task', args}}, undefined)
const raw = fs.readFileSync('{DEADLINE_STATE}', 'utf8')
const state = JSON.parse(raw)
console.log(JSON.stringify({{state, task_id_exists: 'e2e-complete-me' in state}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result.get("task_id_exists") is False, (
        f"Completed task should be removed from state, got: {result}"
    )


# ─── Fail-open: no env override, no state file ───────────────────────────────


def test_fail_open_no_state_file():
    """No state file -> fail-open: does not crash, allows tools."""
    _clean_state()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'grep'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"No state file should fail-open, got: {result}"
    )


def test_fail_open_corrupt_state_file():
    """Corrupt state file -> fail-open, does not crash."""
    _clean_state()
    Path(DEADLINE_STATE).write_text("not valid json{{{")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code)
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Corrupt state should fail-open, got: {result}"
    )


# ─── State file writes breached task to STALE_FILE ───────────────────────────


def test_over_timeout_writes_stale_file():
    """Breached tasks are recorded in the stale-file for the watchdog."""
    _clean_state()

    raw_id = "md5:test-stale-record"
    old = int((time.time() - 2) * 1000)
    Path(DEADLINE_STATE).write_text(json.dumps({raw_id: old}))

    code = f"""\
const fs = await import('node:fs')
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'glob'}}, undefined)
let staleEntries = []
try {{
    staleEntries = JSON.parse(fs.readFileSync('{STALE_FILE}', 'utf8'))
}} catch (e) {{}}
const found = staleEntries.some((e) => e && e.task_id === '{raw_id}')
console.log(JSON.stringify({{stale_entries_count: staleEntries.length, found}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_TASK_TIMEOUT_MS": "1000",
        "GLUDD_TASK_DEADLINE_BLOCK": "1",
    })
    assert result is not None
    assert result.get("found") is True, (
        f"Stale task should be recorded in STALE_FILE, got: {result}"
    )
