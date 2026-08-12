"""E2E behavioral tests catching ALL stop/grind/thin-wave violations.

Tests invoke actual plugin hooks and FAIL before code fixes (bugs exist).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_STOP = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
PLUGIN_MULTITASK = ROOT / ".opencode" / "plugin" / "enforce-multitask.ts"

_GAP_MS = 500
_GAP_ENV = {"GLUDD_MSG_GAP_MS": str(_GAP_MS)}
_GAP_SLEEP_JS = f"await new Promise(res => setTimeout(res, {_GAP_MS * 2}))"

_ts_counter = 0

# Disengage guard: the real /tmp/gludd-watchdog-disengage.json from a live
# opencode session leaks into test processes, skipping enforcement silently.
# Redirect to a non-existent path per test so disengage is always "off" unless
# a specific test sets a valid disengage file.
_SAFE_DISENGAGE = "/tmp/gludd-watchdog-disengage-NOEXIST-test.json"
_SAFE_BASE = {
    "GLUDD_DISENGAGE_PATH": _SAFE_DISENGAGE,
    "GLUDD_BLOCK_COUNTER_FILE": "",
    "GLUDD_BLOCK_REASON_FILE": "",
}


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    global _ts_counter
    _ts_counter += 1
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"beh_enforce_{_ts_counter}_"))
    multitask_state = tmp.with_suffix(".multitask-state.json")
    hot_module_prefix = tmp.with_suffix(".hot-")
    tmp.write_text(ts_code)
    try:
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env.update(_SAFE_BASE)
        env["GLUDD_MULTITASK_STATE_FILE"] = str(multitask_state)
        env["GLUDD_HOT_MODULE_PREFIX"] = str(hot_module_prefix)
        if env_override:
            env.update(env_override)
        proc = subprocess.run(
            ["node", "--experimental-strip-types", str(tmp)],
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd or str(ROOT), env=env,
        )
        return proc
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
        with contextlib.suppress(OSError):
            multitask_state.unlink()


def _last_json(stdout: str) -> dict | None:
    for line in reversed(stdout.split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _assert_deny_with(r: dict, expected_phrase: str):
    assert r is not None, "No JSON in output"
    assert r.get("permissionDecision") == "deny", (
        f"Expected deny, got: {r}"
    )
    assert expected_phrase in r.get("message", ""), (
        f"Expected '{expected_phrase}' in deny message, got: {r}"
    )


def _assert_text_stop_blocked(result_text: str) -> None:
    assert (
        "TEXT-ONLY RESPONSE BLOCKED" in result_text
        or "CONSECUTIVE TEXT-ONLY RESPONSES BLOCKED" in result_text
        or "CI RED/PENDING COMPLETION CLAIM BLOCKED" in result_text
    ), f"Expected text-only pending-work block, got: {result_text[:300]!r}"


def _setup_pending_work_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "TASKS.md").write_text("- [ ] pending item\n- [x] done item\n")
    return path


def _setup_pending_work_git_dir(path: Path) -> Path:
    _setup_pending_work_dir(path)
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    return path


def _make_disengage_file(path: Path, duration_sec: int = 3600) -> Path:
    f = path / "disengage.json"
    f.write_text(json.dumps({"disengage_until": int((time.time() + duration_sec) * 1000)}))
    return f


def _env_stop(tmp_path: Path) -> dict:
    return {
        "GLUDD_STOP_TEXT_COMPLETE_COUNT": str(tmp_path / "tc.json"),
        "GLUDD_STOP_STATE_FILE": str(tmp_path / "state.json"),
        "GLUDD_STREAK_FILE": str(tmp_path / "streak.json"),
        "GLUDD_PERSIST_STOP_BLOCK_FILE": str(tmp_path / "persist.json"),
        "GLUDD_STOP_TOOL_COUNTS_FILE": str(tmp_path / "counts.json"),
        "GLUDD_BLOCK_COUNTER_FILE": str(tmp_path / "blockcnt.json"),
        "GLUDD_BLOCK_REASON_FILE": str(tmp_path / "blockreason.json"),
        "GLUDD_FALSE_DONE_BLOCKS_FILE": str(tmp_path / "false-done.json"),
        "GLUDD_FORCE_DISPATCH_PATH": str(tmp_path / "force-dispatch.json"),
        "GLUDD_RELEASE_COMPLETENESS_FILE": str(tmp_path / "release.json"),
        "GLUDD_LAST_TEST_RESULT_FILE": str(tmp_path / "last-test.json"),
        "GLUDD_POST_RESULTS_STATE_FILE": str(tmp_path / "post-results.json"),
        "GLUDD_TEXT_ONLY_STATE_FILE": str(tmp_path / "text-only.json"),
        "GLUDD_WATCHDOG_CI_FILE": str(tmp_path / "watchdog-ci.json"),
    }


# ─── 1. text-only-stop ───────────────────────────────────────────────────────


def test_text_only_stop_blocked_by_pending_work(tmp_path):
    """Text-only response with pending TASKS.md items -> TEXT-ONLY RESPONSE BLOCKED."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_STOP}')
const plugin = await mod.default({{}})
const output = {{ text: 'Here is my status update on the current work that needs attention.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ result_text: result?.text }}))
"""
    proc = _run_plugin(code, env_override=_env_stop(tmp_path), cwd=str(cwd))
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    result_text = r.get("result_text", "")
    _assert_text_stop_blocked(result_text)


# ─── 2. completion-smell-stop ────────────────────────────────────────────────


def test_completion_smell_blocks_continuing_pattern(tmp_path):
    """'Continuing with CI investigation...' WITH pending work -> COMPLETION_SMELL blocks."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_STOP}')
const plugin = await mod.default({{}})
const output = {{ text: 'Continuing with CI investigation and remaining work:' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ result_text: result?.text }}))
"""
    proc = _run_plugin(code, env_override=_env_stop(tmp_path), cwd=str(cwd))
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    result_text = r.get("result_text", "")
    assert "completion-adjacent" in result_text or "BLOCKED" in result_text, (
        f"Expected COMPLETION_SMELL block for 'Continuing...', got: {result_text[:300]!r}"
    )


# ─── 3. disengage-does-not-bypass-stop ───────────────────────────────────────


def test_disengage_does_not_bypass_pending_work_block(tmp_path):
    """Disengage active + pending work + text-only -> hasRealPendingWork STILL blocks."""
    cwd = _setup_pending_work_dir(tmp_path)
    disengage_file = _make_disengage_file(tmp_path)
    stop_env = _env_stop(tmp_path)
    # disengage CAN bypass stop's own isDisengaged() but NOT the hasRealPendingWork block
    stop_env["GLUDD_DISENGAGE_PATH"] = str(disengage_file)

    code = f"""\
const mod = await import('{PLUGIN_STOP}')
const plugin = await mod.default({{}})
const output = {{ text: 'Status update while disengaged but work remains.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ result_text: result?.text }}))
"""
    proc = _run_plugin(code, env_override=stop_env, cwd=str(cwd))
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    result_text = r.get("result_text", "")
    _assert_text_stop_blocked(result_text)


# ─── 4. thin-wave-blocked (BUG: disengage bypasses text.complete in enforce-multitask.ts) ─


def test_thin_wave_blocked_after_dispatching_3(tmp_path):
    """Dispatch 3 subagents (not 10), pending work -> text.complete blocks as THIN WAVE.

    BUG: enforce-multitask.ts text.complete checks isDisengaged() at line 301
    using the real /tmp/gludd-watchdog-disengage.json path. A live disengage
    file causes text to pass through unblocked. This test sets GLUDD_DISENGAGE_PATH
    to a non-existent file to control the behavior.
    """
    ws = _setup_pending_work_git_dir(tmp_path / "thinwave")

    code = f"""\
const mod = await import('{PLUGIN_MULTITASK}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
await plugin['tool.execute.before']({{tool: 'task'}}, undefined)
const result = await plugin['experimental.text.complete'](undefined, {{ text: 'Some thin wave concluding text.' }})
console.log(JSON.stringify({{ result_text: result?.text || result }}))
"""
    proc = _run_plugin(
        code,
        env_override={
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
            "GLUDD_MIN_DISPATCHES": "10",
            **_GAP_ENV,
        },
        cwd=str(ws),
    )
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    result_text = r.get("result_text", "")
    assert "THIN WAVE BLOCKED" in result_text, (
        f"Expected THIN WAVE BLOCKED after only 3 dispatches. Got: {result_text[:300]!r}"
    )


# ─── 5. main-thread-grind-blocked ─────────────────────────────────────────────


def test_consecutive_nondispatch_blocked_after_threshold(tmp_path):
    """Five mutation calls in one message surface the grinding diagnostic.

    Read/grep/glob are deliberately excluded from the mutation streak, so this
    legacy case uses actual mutation calls instead of treating five reads as
    main-thread grinding.
    """
    ws = _setup_pending_work_git_dir(tmp_path / "grind")

    code = f"""\
const mod = await import('{PLUGIN_MULTITASK}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
await plugin['tool.execute.before']({{tool: 'write'}}, undefined)
await plugin['tool.execute.before']({{tool: 'bash'}}, undefined)
await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    proc = _run_plugin(
        code,
        env_override={
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_THRESHOLD": "5",
            "GLUDD_CONSECUTIVE_NON_DISPATCH_WINDOW_MS": "60000",
            "GLUDD_MIN_DISPATCHES": "10",
        },
        cwd=str(ws),
    )
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]!r}"
    assert r.get("permissionDecision") == "deny", (
        f"Expected deny for consecutive grinding, got: {r}"
    )
    msg = r.get("message", "")
    assert "CONSECUTIVE NON-DISPATCH STREAK" in msg, (
        f"Expected consecutive non-dispatch diagnostic. Got: {r}"
    )


# ─── 6. zero-streak-blocked ──────────────────────────────────────────────────


def test_zero_dispatch_streak_blocks_fourth_message(tmp_path):
    """3 zero-dispatch messages -> 4th non-dispatch call blocked with ZERO-DISPATCH STREAK.

    Reads create real message boundaries without incrementing the mutation
    streak. The specialized zero-dispatch diagnostic must win over the generic
    configured-minimum fallback on the fourth message.
    """
    ws = _setup_pending_work_git_dir(tmp_path / "zerostreak")

    code = f"""\
const mod = await import('{PLUGIN_MULTITASK}')
const plugin = await mod.default({{}})
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
await plugin['tool.execute.before']({{tool: 'read'}}, undefined)
{_GAP_SLEEP_JS}
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    proc = _run_plugin(
        code,
        env_override={
            "GLUDD_MULTITASK_FLOOR_ENFORCE": "1",
            "GLUDD_MIN_DISPATCHES": "10",
            **_GAP_ENV,
        },
        cwd=str(ws),
        timeout=30,
    )
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    assert r.get("permissionDecision") == "deny", (
        f"Expected deny after zero-dispatch streak, got: {r}"
    )
    msg = r.get("message", "")
    assert "ZERO-DISPATCH STREAK" in msg, (
        f"Expected ZERO-DISPATCH STREAK diagnostic. Got: {r}"
    )


# ─── 7. under-floor-blocked ──────────────────────────────────────────────────


def test_under_floor_hard_block_after_zero_dispatches(tmp_path):
    """Dispatch 0 subagents pending -> edit file -> configured-minimum fallback.

    This is the correct behavior when no dispatches have been made at all.
    Unlike the specialized streak cases, no prior context takes precedence.
    """
    ws = _setup_pending_work_git_dir(tmp_path / "underfloor")

    code = f"""\
const mod = await import('{PLUGIN_MULTITASK}')
const plugin = await mod.default({{}})
const r = await plugin['tool.execute.before']({{tool: 'edit'}}, undefined)
console.log(JSON.stringify(r ?? {{allowed: true}}))
"""
    proc = _run_plugin(
        code,
        env_override={"GLUDD_MULTITASK_FLOOR_ENFORCE": "1", "GLUDD_MIN_DISPATCHES": "10"},
        cwd=str(ws),
    )
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    _assert_deny_with(r, "CONFIGURED MINIMUM BLOCK")


# ─── 8. evidence-bypass-works ────────────────────────────────────────────────


def test_text_with_evidence_blocked_when_pending_work_remains(tmp_path):
    """Evidence does not bypass the no-text-only rule while work remains."""
    cwd = _setup_pending_work_dir(tmp_path)

    code = f"""\
const mod = await import('{PLUGIN_STOP}')
const plugin = await mod.default({{}})
const output = {{ text: 'Fixed the bug. 42 passed, commit abc123def456.' }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text }}))
"""
    proc = _run_plugin(code, env_override=_env_stop(tmp_path), cwd=str(cwd))
    assert proc.returncode == 0, f"Node exited {proc.returncode}: {proc.stderr[:500]}"
    r = _last_json(proc.stdout)
    assert r is not None, f"No JSON in output: {proc.stdout[:500]}"
    out_text = r.get("output_text", "")
    res_text = r.get("result_text", "")
    assert out_text == "", f"Expected output text to be blanked, got: {out_text!r}"
    _assert_text_stop_blocked(res_text)
