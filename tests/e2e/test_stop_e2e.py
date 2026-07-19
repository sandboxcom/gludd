"""E2e test for enforce-stop.ts: text.complete blocking, subagent guard, env disable, fail-open, false-done phrases.

Invokes the actual TypeScript plugin via node --experimental-strip-types
in isolated temp directories, verifying the text.complete hook behaviors.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"

_ts_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> dict | None:
    """Write TS to temp file, run via node, return last JSON line of stdout."""
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"stop_e2e_{_ts_counter}_"))
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_NO_WAIT_ENFORCE"] = "1"
        env["GLUDD_STOP_TEXT_COMPLETE_COUNT"] = (
            f"/tmp/gludd-stop-text-complete-count-e2e-{_ts_counter}.json"
        )
        env["GLUDD_STOP_STATE_FILE"] = f"/tmp/gludd-stop-state-e2e-{_ts_counter}.json"
        env["GLUDD_PERSIST_STOP_BLOCK_FILE"] = (
            f"/tmp/gludd-persist-stop-block-e2e-{_ts_counter}.json"
        )
        env["GLUDD_POST_RESULTS_STATE_FILE"] = (
            f"/tmp/gludd-post-results-state-e2e-{_ts_counter}.json"
        )
        env["GLUDD_TEXT_ONLY_STATE_FILE"] = (
            f"/tmp/gludd-text-only-state-e2e-{_ts_counter}.json"
        )
        env["GLUDD_STOP_TOOL_COUNTS_FILE"] = (
            f"/tmp/gludd-stop-tool-counts-e2e-{_ts_counter}.json"
        )
        env["GLUDD_STREAK_FILE"] = f"/tmp/gludd-tool-streak-e2e-{_ts_counter}.json"
        env["GLUDD_DISENGAGE_PATH"] = (
            f"/tmp/gludd-watchdog-disengage-e2e-{_ts_counter}.json"
        )
        env["GLUDD_BLOCK_COUNTER_FILE"] = (
            f"/tmp/gludd-stop-block-counter-e2e-{_ts_counter}.json"
        )
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


def _setup_pending_work_dir(tmp_path: Path) -> Path:
    """Create a directory with config/ratchet.yml that has non-zero entries."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "ratchet.yml").write_text("refactor_floor_ts: pending\n  src::foo\n")
    return tmp_path


# ─── Pending work blocks text-only responses ─────────────────────────────────


def test_text_complete_blocks_when_ratchet_has_entries(tmp_path):
    """config/ratchet.yml with entries -> text.complete returns blocked text."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'Detailed status message describing the current work state with sufficient length.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None, "Expected result from text.complete hook, got None"
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = res_text or out_text
    assert "BLOCKED" in text, (
        f"Expected blocked text, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


def test_text_complete_blocks_when_tasks_md_has_unchecked(tmp_path):
    """TASKS.md with unchecked items -> text.complete blocks."""
    cwd = tmp_path
    (cwd / "TASKS.md").write_text("- [ ] pending item\n- [x] done item\n")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'Another detailed status message describing current work state with sufficient length.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = res_text or out_text
    assert "BLOCK" in text, (
        f"Expected 'BLOCK' in text, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


# ─── No pending work allows text through ─────────────────────────────────────


def test_text_complete_allows_when_no_pending_work(tmp_path):
    """No ratchet entries, no TASKS.md unchecked items -> output passes through."""
    cwd = tmp_path

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'Just a normal factual message with no pending work to worry about.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    assert out_text == "Just a normal factual message with no pending work to worry about.", (
        f"Expected unmodified, got output_text={out_text!r} result_text={res_text!r}"
    )


# ─── Subagent guard skips enforcement ────────────────────────────────────────


def test_subagent_guard_skips_text_complete(tmp_path):
    """OPENCODE_SUBAGENT=1 -> text.complete returns output unchanged."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'This message would be blocked in non-subagent mode.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, env_override={"OPENCODE_SUBAGENT": "1"}, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    assert out_text == "This message would be blocked in non-subagent mode.", (
        f"Subagent should skip enforcement, got output_text={out_text!r} result_text={res_text!r}"
    )


# ─── Env var disable skips enforcement ───────────────────────────────────────


def test_gludd_stop_enforce_zero_skips_text_complete(tmp_path):
    """GLUDD_STOP_ENFORCE=0 -> text.complete returns output unchanged."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'This message would be blocked when enforcement is enabled.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, env_override={"GLUDD_STOP_ENFORCE": "0"}, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    assert out_text == "This message would be blocked when enforcement is enabled.", (
        f"Enforce=0 should skip, got output_text={out_text!r} result_text={res_text!r}"
    )


# ─── False-done completion phrases blocked without evidence ──────────────────


def test_false_done_all_done_blocked_without_evidence(tmp_path):
    """'All done' in text + ratchet entries + no evidence -> blocked."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'All done' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = out_text or res_text
    assert "FALSE-DONE" in text, (
        f"Expected FALSE-DONE block, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


def test_false_done_checkmark_blocked_without_evidence(tmp_path):
    """Checkmark in text + ratchet entries + no evidence -> blocked."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: '\\u2705 Everything is done' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = out_text or res_text
    assert "FALSE-DONE" in text, (
        f"Expected FALSE-DONE block, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


def test_false_done_ready_for_review_blocked_without_evidence(tmp_path):
    """'Ready for review' + ratchet entries + no evidence -> blocked."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'The work is ready for review now.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = out_text or res_text
    assert "FALSE-DONE" in text, (
        f"Expected FALSE-DONE block, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


# ─── QA summary patterns blocked ─────────────────────────────────────────────


def test_qa_summary_blocked_when_pending_work(tmp_path):
    """'completed in this session' + TASKS.md unchecked -> QA summary blocked."""
    cwd = tmp_path
    (cwd / "TASKS.md").write_text("- [ ] pending item\n")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'Everything was completed in this session so far.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    text = res_text or out_text
    assert "BLOCKED" in text, (
        f"Expected blocked text, got output_text={out_text[:200]!r} result_text={res_text[:200]!r}"
    )


# ─── Already-blocked output passes through (idempotency) ─────────────────────


def test_already_blocked_output_passes_through(tmp_path):
    """Output already starting with ⛔ passes through unchanged."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: '\\u26d4 BLOCKED: something else already blocked this' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None
    out_text = result.get("output_text", "")
    assert out_text == "\u26d4 BLOCKED: something else already blocked this", (
        f"Already-blocked should pass through, got output_text={out_text!r}"
    )


# ─── tool.execute.before: question tool is denied ────────────────────────────


def test_question_tool_is_denied(tmp_path):
    """tool.execute.before denies 'question' tool."""
    cwd = tmp_path

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
let caught = null
try {{
    const result = await plugin['tool.execute.before']({{tool: 'question'}}, undefined)
    console.log(JSON.stringify({{ result: result }}))
}} catch (e) {{
    caught = e.message
    console.log(JSON.stringify({{ error: caught }}))
}}
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is not None, "Expected result from tool.execute.before, got None"
    error = result.get("error", "")
    assert "BLOCKING" in error, (
        f"Expected BLOCKING in error, got: {result}"
    )


# ─── Disengaged watchdog skips enforcement ───────────────────────────────────


def test_disengaged_watchdog_skips_text_complete(tmp_path):
    """Valid disengage file -> text.complete returns output unchanged."""
    cwd = _setup_pending_work_dir(tmp_path)
    import time

    disengage_file = tmp_path / "disengage.json"
    disengage_file.write_text(
        json.dumps({"disengage_until": int((time.time() + 3600) * 1000)})
    )

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'This message should pass through when disengaged.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    result = _run_plugin(
        code,
        env_override={"GLUDD_DISENGAGE_PATH": str(disengage_file)},
        cwd=str(cwd),
    )
    assert result is not None
    out_text = result.get("output_text", "")
    assert out_text == "This message should pass through when disengaged.", (
        f"Disengaged should skip enforcement, got output_text={out_text!r}"
    )


# ─── Full cycle: pending -> no pending ───────────────────────────────────────


def test_full_cycle_pending_then_clean(tmp_path):
    """Pending work blocks; remove ratchet; subsequent text passes through."""
    cwd = tmp_path
    config_dir = cwd / "config"
    config_dir.mkdir()

    # 1. Write ratchet -> blocked
    (config_dir / "ratchet.yml").write_text("refactor_floor_ts: pending\n  src::foo\n")

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const output = {{ text: 'Detail status message with enough length to pass short-text check.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    r1 = _run_plugin(code, cwd=str(cwd))
    assert r1 is not None, "Expected result from step 1, got None"
    text1 = r1.get("result_text", "") or r1.get("output_text", "")
    assert "BLOCKED" in text1, (
        f"Step 1: pending ratchet must block, "
        f"got output_text={r1.get('output_text', '')!r} "
        f"result_text={r1.get('result_text', '')!r}"
    )

    # 2. Remove ratchet -> allowed
    (config_dir / "ratchet.yml").unlink()

    r2 = _run_plugin(code, cwd=str(cwd))
    assert r2 is not None, "Expected result from step 2, got None"
    text2 = r2.get("output_text", "")
    assert "BLOCKED" not in text2, (
        f"Step 2: clean state must allow, got output_text={text2!r}"
    )


# ─── tool.execute.before: non-dispatch tools not blocked ─────────────────────


def test_read_tool_allowed_with_pending_work(tmp_path):
    """tool.execute.before allows 'read' tool even with pending work."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_PATH}')
const plugin = await mod.default({{}})
const result = await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
console.log(JSON.stringify(result ?? {{allowed: true}}))
"""
    result = _run_plugin(code, cwd=str(cwd))
    assert result is None or result.get("permissionDecision") != "deny", (
        f"Read tool should not be denied, got: {result}"
    )
