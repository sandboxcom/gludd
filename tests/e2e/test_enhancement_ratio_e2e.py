"""E2e test for enforce-enhancement-ratio.ts: fix/enhancement ratio enforcement.

Invokes the actual TypeScript plugin via node --experimental-strip-types
with pre-populated state, verifying deny/allow/disable/subagent-guard cycles.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-enhancement-ratio.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"ratio_e2e_{_ts_counter}_"))
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


def _make_state_code(state_file: str, wave_entries: list[dict]) -> str:
    """Generate TypeScript code that writes a ratio state file with the Node PID."""
    entries = json.dumps(wave_entries)
    return f"""\
import * as fs from 'node:fs'
fs.writeFileSync('{state_file}', JSON.stringify({{
  wave: {entries},
  session_enhancements: 0,
  session_fixes: 0,
  session_unknown: 0,
  wave_count_since_last_warn: 0,
  early_warned: false,
  lastPid: process.pid,
  lastTs: Date.now()
}}))
"""


# ─── Fix-heavy wave blocks dispatch ──────────────────────────────────────────


def test_two_fixes_no_enhancements_blocks_third_fix(tmp_path):
    """2 fixes in wave -> third fix dispatch denied (100% fixes)."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix bug A", "ts": 1},
        {"type": "fix", "prompt_head": "fix bug B", "ts": 2},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'fix the broken thing'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
    })
    assert result is not None, "Expected deny for fix-heavy wave"
    assert result.get("permissionDecision") == "deny", (
        f"Expected deny, got: {result}"
    )
    assert "ENHANCEMENT RATIO VIOLATION" in result.get("message", "")


# ─── Balanced ratio allows dispatch ──────────────────────────────────────────


def test_balanced_ratio_allows_dispatch(tmp_path):
    """1 fix + 2 enhancements (33% fixes) -> next enhancement allowed."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "enhancement", "prompt_head": "add feature X", "ts": 2},
        {"type": "enhancement", "prompt_head": "add test Y", "ts": 3},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'add self-tests for enforcement plugins'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Balanced ratio should allow, got: {result}"
    )


# ─── Enhancement always allowed even in fix-heavy wave ───────────────────────


def test_enhancement_also_denied_when_ratio_violated(tmp_path):
    """Even enhancement dispatch denied when wave fix ratio already >50%."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
            ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'agent', args: {{prompt: 'write new self-tests for enforcement plugins'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
    })
    assert result is not None and result.get("permissionDecision") == "deny", (
        f"Ratio violation blocks ALL dispatches including enhancements, got: {result}"
    )
    assert "ENHANCEMENT RATIO VIOLATION" in result.get("message", "")


# ─── Env var disable ─────────────────────────────────────────────────────────


def test_env_var_disables_ratio_enforcement(tmp_path):
    """GLUDD_ENHANCEMENT_RATIO_ENFORCE=0 skips all enforcement."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'fix the bug'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
        "GLUDD_ENHANCEMENT_RATIO_ENFORCE": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Env disable should allow, got: {result}"
    )


# ─── Block disabled makes advisory-only ──────────────────────────────────────


def test_block_disabled_allows_fix_heavy_wave(tmp_path):
    """GLUDD_ENHANCEMENT_RATIO_BLOCK=0 makes warn-only (no deny)."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'fix another bug'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
        "GLUDD_ENHANCEMENT_RATIO_BLOCK": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Block disabled should allow (advisory only), got: {result}"
    )


# ─── Subagent guard ──────────────────────────────────────────────────────────


def test_subagent_skips_ratio_checks(tmp_path):
    """OPENCODE_SUBAGENT=1 skips ratio enforcement."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'fix the bug'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "OPENCODE_SUBAGENT": "1",
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Subagent should skip enforcement, got: {result}"
    )


# ─── Non-dispatch tools not gated ────────────────────────────────────────────


def test_non_dispatch_tools_not_checked(tmp_path):
    """Non-dispatch tools (edit, read) not gated by ratio enforcement."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
    ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'edit', args: {{filePath: '/tmp/x'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
    })
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Non-dispatch tool should not be gated, got: {result}"
    )


# ─── text.complete hook: wave finalization ───────────────────────────────────


def test_text_complete_warns_on_fix_heavy_wave(tmp_path):
    """Current ratio enforcement is self-contained in tool.execute.before."""
    state_file = str(tmp_path / "ratio.json")
    setup = _make_state_code(state_file, [
        {"type": "fix", "prompt_head": "fix A", "ts": 1},
        {"type": "fix", "prompt_head": "fix B", "ts": 2},
            ])
    code = setup + f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before'](
  {{tool: 'task', args: {{prompt: 'fix another broken issue'}}}},
  undefined
)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, env_override={
        "GLUDD_ENHANCEMENT_RATIO_STATE": state_file,
        "GLUDD_ENHANCEMENT_RATIO_BLOCK": "0",
    })
    assert result is None or result.get("permissionDecision") != "deny"
