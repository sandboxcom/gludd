"""E2e test for enforce-verified-claims.ts: done-claims without evidence blocked.

Invokes the TypeScript helper and actual plugin via node
``--experimental-strip-types``. Tests the shared ``shouldBlock`` classifier and
the plugin's commit-message and text-completion enforcement surfaces.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-verified-claims.ts"
HELPERS_PATH = ROOT / ".opencode" / "lib" / "plugin_test_exports.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    timeout: int = 15,
) -> dict | None:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"vclaims_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
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


def _tool_hook_code(command: str) -> str:
    command_json = json.dumps(command)
    return f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
try {{
  const result = await plugin['tool.execute.before'](
    {{tool: 'bash', args: {{command: {command_json}}}}},
    undefined
  )
  console.log(JSON.stringify({{blocked: false, result: result ?? null}}))
}} catch (e) {{
  console.log(JSON.stringify({{
    blocked: true,
    message: e.message,
    permissionDecision: e.permissionDecision ?? null
  }}))
}}
"""


# ─── shouldBlock: done-word without evidence → blocked ──────────────────────


def test_shouldblock_done_without_evidence_blocked():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("commit landed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_done_with_commit_hash_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("commit landed abc1234")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_verified_token_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("pushed, VERIFIED master@abc1234")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_test_count_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("tests passing, 10 passed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_ci_green_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("CI GREEN — change landed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_gate_passed_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("=== GATE: PASSED ===\\nfeature shipped")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_no_done_word_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("continuing work on the fix")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_passing_alone_blocked():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("tests are passing")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_working_as_state_claim_blocked():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("it's working now")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_green_alone_blocked():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("the gate is green")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


# ─── tool.execute.before: commit done-claims require evidence ────────────────


def test_tool_hook_blocks_done_without_evidence():
    code = _tool_hook_code("make ship-commit MSG='commit landed'")
    result = _run_plugin(code)
    assert result is not None
    assert result["blocked"] is True
    assert result["permissionDecision"] == "deny"
    assert "BLOCKED" in result.get("message", "")


def test_tool_hook_allows_done_with_evidence():
    code = _tool_hook_code("make ship-commit MSG='commit landed abc1234'")
    result = _run_plugin(code)
    assert result is not None
    assert result["blocked"] is False


def test_tool_hook_allows_non_commit_bash():
    code = _tool_hook_code("make test")
    result = _run_plugin(code)
    assert result is not None
    assert result["blocked"] is False


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_enforcement():
    code = _tool_hook_code("make ship-commit MSG='commit landed'")
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is not None
    assert result["blocked"] is False, (
        f"Subagent should skip enforcement, got: {result}"
    )


# ─── Env var disable ────────────────────────────────────────────────────────


def test_env_var_disables_enforcement():
    code = _tool_hook_code("make ship-commit MSG='commit landed'")
    result = _run_plugin(code, env_override={"GLUDD_VERIFIED_CLAIMS_ENFORCE": "0"})
    assert result is not None
    assert result["blocked"] is False, (
        f"Env disable should allow unverified claim, got: {result}"
    )


# ─── Coverage text hook surface ──────────────────────────────────────────────


def test_plugin_has_no_text_complete_hook():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
console.log(JSON.stringify({{
  hasExperimentalTextComplete: typeof plugin['experimental.text.complete'] === 'function',
  hasTextComplete: typeof plugin['text.complete'] === 'function'
}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["hasExperimentalTextComplete"] is True
    assert result["hasTextComplete"] is False


# ─── Fail-open: empty/null text ─────────────────────────────────────────────


def test_shouldblock_empty_text_allowed():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Empty text must not block, got: {result}"


def test_tool_hook_empty_commit_message_allowed():
    code = _tool_hook_code("make ship-commit MSG=''")
    result = _run_plugin(code)
    assert result is not None
    assert result["blocked"] is False, f"Empty message must be allowed, got: {result}"


# ─── NOT_DONE_PHRASES scrubber ──────────────────────────────────────────────


def test_working_on_not_counted_as_done():
    code = f"""\
const mod = await import('{HELPERS_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("working on the fix now")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, (
        f"'working on' must not be blocked, got: {result}"
    )
