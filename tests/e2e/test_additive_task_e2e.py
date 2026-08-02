"""E2E tests for enforce-additive-task.ts — per-wave continuation slot enforcement.

Verifies: additive task violation (all new-task with >=2 unchecked),
continuation slot passes (task ID reference present),
subagent guard, env disable, soft mode, exact 10/10 ratio violation,
wave reset after 10 dispatches.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-additive-task.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> str:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"additive_e2e_{_ts_counter}_"))
    state_file = Path(tempfile.mktemp(suffix=".json", prefix=f"gludd-additive-e2e-{_ts_counter}-"))
    hot_module_prefix = state_file.with_name(state_file.stem + "-hot-")
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_ADDITIVE_TASK_ENFORCE"] = "1"
        env["GLUDD_ADDITIVE_TASK_BLOCK"] = "1"
        env["GLUDD_ADDITIVE_TASK_STATE"] = str(state_file)
        env["GLUDD_HOT_MODULE_PREFIX"] = str(hot_module_prefix)
        env["GLUDD_PROJECT_ROOT"] = str(Path(cwd or ROOT))
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
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
        with contextlib.suppress(OSError):
            state_file.unlink()


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


def _make_unchecked_workspace(path: Path, count: int = 3) -> None:
    items = "\n".join(f"- [ ] task item {i}" for i in range(count))
    (path / "TASKS.md").write_text(f"{items}\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)


def _make_clean_workspace(path: Path) -> None:
    (path / "TASKS.md").write_text("No pending items\n")
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)


# ─── Additive task violation: all new-task with unchecked items ──────────────


def test_all_new_task_with_unchecked_denied(tmp_path):
    """2 dispatches, both new-task (no task ID), >=2 unchecked → ADDITIVE TASK VIOLATION."""
    ws = tmp_path / "all-new"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin for guardrails'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test for coverage'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"All new-task dispatch with unchecked items should be denied, got: {r}"
    )
    assert "ADDITIVE TASK VIOLATION" in r.get("message", "")


def test_continuation_slot_passes(tmp_path):
    """1 continuation (has task ID) + 1 new-task with >=2 unchecked → passes."""
    ws = tmp_path / "continuation"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Continue work on SEC.1 - fix guardrail enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test for coverage'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Continuation slot with task ID should pass, got: {r}"


# ─── Exact 10/10 ratio violation ────────────────────────────────────────────


def test_ten_out_of_ten_new_task_ratio_violation(tmp_path):
    """10 dispatches, all new-task, with 0 unchecked items (so Rule 1 doesn't
    fire first) → ADDITIVE TASK RATIO VIOLATION. Rule 1 requires >=2 unchecked
    but Rule 2's ratio check fires regardless when all 10 are new-task."""
    ws = tmp_path / "ten-new-ratio"
    ws.mkdir()
    _make_clean_workspace(ws)

    dispatches = "\n".join(
        f"await plugin['tool.execute.before']("
        f"{{tool: 'task', args: {{prompt: 'New task description {i}'}}}}, undefined)"
        for i in range(9)
    )
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'New task description 9'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"10/10 new-task dispatches should trigger ratio violation, got: {r}"
    )
    assert "ADDITIVE TASK RATIO VIOLATION" in r.get("message", "")


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_enforcement(tmp_path):
    """OPENCODE_SUBAGENT=1 bypasses additive task enforcement."""
    ws = tmp_path / "subagent"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Subagent should skip additive task enforcement, got: {r}"
    )


# ─── Env disable ────────────────────────────────────────────────────────────


def test_env_disable_skips(tmp_path):
    """GLUDD_ADDITIVE_TASK_ENFORCE=0 disables enforcement."""
    ws = tmp_path / "env-off"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_ADDITIVE_TASK_ENFORCE": "0"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Env disable should skip enforcement, got: {r}"


# ─── Soft mode ──────────────────────────────────────────────────────────────


def test_soft_mode_console_warn_not_deny(tmp_path):
    """GLUDD_ADDITIVE_TASK_BLOCK=0: warns but does not deny."""
    ws = tmp_path / "soft"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={"GLUDD_ADDITIVE_TASK_BLOCK": "0"}, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Soft-mode (BLOCK=0) should not deny, got: {r}"


# ─── No block when clean ────────────────────────────────────────────────────


def test_no_block_when_no_unchecked(tmp_path):
    """0 unchecked items → no enforcement even with all-new-task wave."""
    ws = tmp_path / "no-unchecked"
    ws.mkdir()
    _make_clean_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"No unchecked items should allow all-new-task wave, got: {r}"
    )


# ─── Non-dispatch tools pass through ────────────────────────────────────────


def test_non_dispatch_tools_ignored(tmp_path):
    """Read/write/bash tools are NOT dispatch tools → ignored by plugin."""
    ws = tmp_path / "nondispatch"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Non-dispatch tools should pass through, got: {r}"


# ─── Task ID classification ─────────────────────────────────────────────────


def test_continuation_with_different_task_id_formats(tmp_path):
    """Various TASK_ID_RE formats (SEC.1, D-13, MWK.1, FIX-5) are detected as continuation."""
    ws = tmp_path / "task-ids"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Continue work on D-13: fix the database migration'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Fix FIX-5: resolve the config loading bug'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", (
        f"Task IDs D-13 and FIX-5 should classify as continuation, got: {r}"
    )


def test_continuation_with_dot_format(tmp_path):
    """Format SEC.1 (alphanum uppercase + dot + digit) detected as continuation."""
    ws = tmp_path / "dot-format"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'SEC.1: audit the enforcement plugin codebase'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test for coverage'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"SEC.1 continuation should allow mixed wave, got: {r}"


# ─── Fail-open ──────────────────────────────────────────────────────────────


def test_non_git_dir_fails_open(tmp_path):
    """Non-git directory with no TASKS.md: fail-open (allow dispatch)."""
    ws = tmp_path / "nonrepo"
    ws.mkdir()

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is None or r.get("permissionDecision") != "deny", f"Non-git dir should fail-open, got: {r}"


# ─── Deny message content ───────────────────────────────────────────────────


def test_deny_message_mentions_unchecked_count(tmp_path):
    """Deny message includes the unchecked item count from TASKS.md."""
    ws = tmp_path / "deny-msg"
    ws.mkdir()
    _make_unchecked_workspace(ws, count=3)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    msg = r.get("message", "")
    assert "3 items unchecked" in msg, f"Deny message must include unchecked count (3), got: {msg}"


# ─── Wave reset after 10 dispatches ─────────────────────────────────────────


def test_wave_resets_after_ten_dispatches(tmp_path):
    """After 10 dispatches, wave array resets to empty. The 11th dispatch
    (new-task, no continuation) starts a fresh wave. Rule 1 fires because
    cCount=0 and unchecked>=2."""
    ws = tmp_path / "wave-reset"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    dispatches = "\n".join(
        f"await plugin['tool.execute.before']({{tool: 'task', args: {{prompt: 'Continue SEC.1 task {i}'}}}}, undefined)"
        for i in range(10)
    )
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
{dispatches}
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add new feature implementation'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"After wave reset, new-task with unchecked should trigger Rule 1, got: {r}"
    )
    assert "ADDITIVE TASK VIOLATION" in r.get("message", "")


# ─── Enabled by default ─────────────────────────────────────────────────────


def test_enabled_by_default(tmp_path):
    """Without any env override, the plugin is ENABLED (ENABLED defaults to true)."""
    ws = tmp_path / "default-on"
    ws.mkdir()
    _make_unchecked_workspace(ws)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Add a new enforcement plugin'}}}}, undefined)
const r = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'Write a new test'}}}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(ws))
    r = _last_json(result)
    assert r is not None and r.get("permissionDecision") == "deny", (
        f"Plugin should be enabled by default, deny all-new-task wave, got: {r}"
    )
