"""E2e test for enforce-verified-claims.ts: done-claims without evidence blocked.

Invokes the actual TypeScript plugin via node --experimental-strip-types.
Tests shouldBlock function and experimental.text.complete hook surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-verified-claims.ts"

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
        try:
            tmp.unlink()
        except OSError:
            pass


# ─── shouldBlock: done-word without evidence → blocked ──────────────────────


def test_shouldblock_done_without_evidence_blocked():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("commit landed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_done_with_commit_hash_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("commit landed abc1234")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_verified_token_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("pushed, VERIFIED master@abc1234")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_test_count_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("tests passing, 10 passed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_ci_green_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("CI GREEN — change landed")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_done_with_gate_passed_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("=== GATE: PASSED ===\\nfeature shipped")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_no_done_word_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("continuing work on the fix")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Expected allow, got: {result}"


def test_shouldblock_passing_alone_blocked():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("tests are passing")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_working_as_state_claim_blocked():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("it's working now")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


def test_shouldblock_green_alone_blocked():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("the gate is green")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is True, f"Expected block, got: {result}"


# ─── text.complete: done-word without evidence → output blocked ──────────────


def test_text_complete_blocks_done_without_evidence():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "commit landed" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text, hasReturn: result !== undefined }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert "BLOCKED" in result.get("text", ""), (
        f"Expected BLOCK_MESSAGE, got: {result}"
    )


def test_text_complete_allows_done_with_evidence():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "commit landed abc1234" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result.get("text") == "commit landed abc1234", (
        f"Text should be unchanged, got: {result}"
    )


def test_text_complete_allows_innocent_text():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "continuing work on the next feature" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result.get("text") == "continuing work on the next feature", (
        f"Text should be unchanged, got: {result}"
    )


# ─── Subagent guard ─────────────────────────────────────────────────────────


def test_subagent_skips_enforcement():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "commit landed" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text, hasReturn: result !== undefined }}))
"""
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"})
    assert result is not None
    assert result.get("text") == "commit landed", (
        f"Subagent should skip enforcement, got: {result}"
    )


# ─── Env var disable ────────────────────────────────────────────────────────


def test_env_var_disables_enforcement():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "commit landed" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text, hasReturn: result !== undefined }}))
"""
    result = _run_plugin(code, env_override={"GLUDD_VERIFIED_CLAIMS_ENFORCE": "0"})
    assert result is not None
    assert result.get("text") == "commit landed", (
        f"Env disable should allow unverified claim, got: {result}"
    )


# ─── System directives are never blocked ─────────────────────────────────────


def test_system_directive_bypasses_block():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "HARD STOP — commit landed. Do not block this." }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert "HARD STOP" in result.get("text", ""), (
        f"System directive must not be blocked, got: {result}"
    )


def test_block_message_directive_bypasses_block():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "BLOCKED: commit landed" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert "BLOCKED:" in result.get("text", ""), (
        f"BLOCKED: prefix must not be double-blocked, got: {result}"
    )


# ─── Fail-open: empty/null text ─────────────────────────────────────────────


def test_shouldblock_empty_text_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, f"Empty text must not block, got: {result}"


def test_text_complete_empty_output_allowed():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: "" }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ text: output.text }}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result.get("text") == "", f"Empty text must be unchanged, got: {result}"


# ─── NOT_DONE_PHRASES scrubber ──────────────────────────────────────────────


def test_working_on_not_counted_as_done():
    code = f"""\
const mod = await import('{PLUGIN_PATH}')
console.log(JSON.stringify({{shouldBlock: mod.shouldBlock("working on the fix now")}}))
"""
    result = _run_plugin(code)
    assert result is not None
    assert result["shouldBlock"] is False, (
        f"'working on' must not be blocked, got: {result}"
    )
