"""E2E test: invoke enforce-stop.ts text.complete hook via Node to verify it
block text-only responses when pending work exists.

This test DIRECTLY invokes the ACTUAL TypeScript plugin via
`node --experimental-strip-types` in isolated temp directories — the closest
available analog to a running opencode session. If the plugin has a code bug
that causes the hook to pass through text while work exists, this test WILL
catch it — no Python re-implementation, no regex-only matching, no pre-seeded
JSON state. The gap is proven real by a failing test.

Covers:
  1. TASKS.md unchecked items → text blocked
  2. config/ratchet.yml entries → text blocked
  3. Both TASKS.md + ratchet.yml → text blocked
  4. No pending work → text passes through
  5. OPENCODE_SUBAGENT=1 → bypasses
  6. GLUDD_STOP_ENFORCE=0 → bypasses
  7. Status summary ("Here's the session N final status") → blocked regardless of evidence
  8. Short false-done claim ("All done.") → blocked
  9. QA response pattern ("completed in this session") → blocked
 10. Clean text with pending work but no stop pattern → passes text.complete checks
     (the text-only-while-work-exists block fires at the tail end)
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

_counter = 0


def _run_plugin(
    ts_code: str,
    env_override: dict | None = None,
    cwd: str | None = None,
    timeout: int = 20,
) -> dict | None:
    """Write TS code to temp file, run via node --experimental-strip-types."""
    global _counter
    _counter += 1
    n = _counter
    tmp = Path(tempfile.mktemp(suffix=".ts", prefix=f"e_stop_live_{n}_"))
    tmp.write_text(ts_code)
    try:
        state_prefix = tmp.with_suffix("")
        env = os.environ.copy()
        env["OPENCODE_SUBAGENT"] = ""
        env["GLUDD_NO_WAIT_ENFORCE"] = "1"
        env["GLUDD_STOP_TEXT_COMPLETE_COUNT"] = (
            f"{state_prefix}-text-complete-count.json"
        )
        env["GLUDD_STOP_STATE_FILE"] = f"{state_prefix}-stop-state.json"
        env["GLUDD_PERSIST_STOP_BLOCK_FILE"] = (
            f"{state_prefix}-persist-stop-block.json"
        )
        env["GLUDD_POST_RESULTS_STATE_FILE"] = (
            f"{state_prefix}-post-results-state.json"
        )
        env["GLUDD_TEXT_ONLY_STATE_FILE"] = (
            f"{state_prefix}-text-only-state.json"
        )
        env["GLUDD_STOP_TOOL_COUNTS_FILE"] = (
            f"{state_prefix}-stop-tool-counts.json"
        )
        env["GLUDD_STREAK_FILE"] = f"{state_prefix}-tool-streak.json"
        env["GLUDD_DISENGAGE_PATH"] = (
            f"{state_prefix}-watchdog-disengage.json"
        )
        env["GLUDD_BLOCK_COUNTER_FILE"] = (
            f"{state_prefix}-stop-block-counter.json"
        )
        env["GLUDD_RELEASE_COMPLETENESS_FILE"] = (
            f"{state_prefix}-release-completeness.json"
        )
        env["GLUDD_LAST_TEST_RESULT_FILE"] = (
            f"{state_prefix}-last-test-result.json"
        )
        env["GLUDD_MULTITASK_STATE_FILE"] = (
            f"{state_prefix}-multitask-state.json"
        )
        env["GLUDD_WATCHDOG_CI_FILE"] = f"{state_prefix}-watchdog-ci.json"
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


def _invoke_text_complete(
    cwd: Path,
    text: str,
    env_override: dict | None = None,
) -> dict | None:
    """Invoke enforce-stop.ts experimental.text.complete hook with given text."""
    code = f"""\
const mod = await import({json.dumps(str(PLUGIN_PATH))})
const plugin = await mod.default({{}})
const output = {{ text: {json.dumps(text)} }}
const result = await plugin['experimental.text.complete'](undefined, output)
console.log(JSON.stringify({{ output_text: output.text, result_text: result?.text ?? null }}))
"""
    return _run_plugin(code, env_override=env_override, cwd=str(cwd))


def _extract_text(result: dict | None) -> str:
    """Extract the blocked/passed-through text from the hook result.

    The text.complete hook returns a NEW object {text: "BLOCKED..."} when
    blocking, leaving the input output.text unchanged. So result_text has
    the blocked text; output_text has the original. Prefer result_text
    (the return value), fall back to output_text (pass-through/unchanged).
    """
    if result is None:
        return ""
    out_text = result.get("output_text", "")
    res_text = result.get("result_text", "")
    return res_text if res_text else out_text


# ═══════════════════════════════════════════════════════════════════════════════
# 1: TASKS.md unchecked items → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


def test_tasks_md_unchecked_blocks_text_only(tmp_path: Path):
    """TASKS.md with `- [ ]` unchecked items → text.complete blocks text-only response."""
    (tmp_path / "TASKS.md").write_text("- [ ] Fix critical auth bug\n- [ ] Write missing tests\n")

    result = _invoke_text_complete(
        tmp_path,
        "Here is a detailed status update describing the current work state with sufficient length and content.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"TASKS.md unchecked items MUST block text-only. Got output_text={result.get('output_text', '')[:200]!r}"
    )


def test_tasks_md_multiple_unchecked_blocks(tmp_path: Path):
    """Multiple unchecked items → text.complete blocks."""
    (tmp_path / "TASKS.md").write_text(
        "- [ ] Task A\n- [x] Done task\n- [ ] Task B\n- [ ] Task C\n"
    )

    result = _invoke_text_complete(
        tmp_path,
        "Working on the remaining items now.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, f"Multiple unchecked must block. Got: {text[:200]!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 2: config/ratchet.yml entries → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


def test_ratchet_yml_entries_block_text_only(tmp_path: Path):
    """config/ratchet.yml with entries → text.complete blocks text-only response."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "ratchet.yml").write_text(
        "refactor_floor_ts: pending\n  src::foo.py\nlint:\n  max_errors: 5\n"
    )

    result = _invoke_text_complete(
        tmp_path,
        "Detailed status message describing the current work state with sufficient length for blocking.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"ratchet.yml entries MUST block. Got output_text={result.get('output_text', '')[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3: Both TASKS.md + ratchet.yml → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


def test_both_tasks_and_ratchet_block(tmp_path: Path):
    """Both TASKS.md unchecked + ratchet.yml entries → text.complete blocks."""
    (tmp_path / "TASKS.md").write_text("- [ ] Fix CI pipeline\n")
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "ratchet.yml").write_text("coverage:\n  min_pct: 85.0\n")

    result = _invoke_text_complete(
        tmp_path,
        "Just another progress message describing current state with enough words to pass short checks.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"Both TASKS.md + ratchet.yml MUST block. Got: {text[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 4: No pending work → text passes through
# ═══════════════════════════════════════════════════════════════════════════════


def test_no_pending_work_allows_text_through(tmp_path: Path):
    """No TASKS.md, no ratchet.yml → text passes through unchanged."""
    result = _invoke_text_complete(
        tmp_path,
        "Just a normal factual message with no pending work to worry about.",
    )
    text = _extract_text(result)
    assert text == "Just a normal factual message with no pending work to worry about.", (
        f"No work must allow text through. Got: {text[:200]!r}"
    )


def test_empty_tasks_md_is_no_pending_work(tmp_path: Path):
    """TASKS.md exists but has no unchecked items → text passes through.
    Text must avoid completion-adjacent words (which trigger hasStructuredEvidence
    check even without pending work)."""
    (tmp_path / "TASKS.md").write_text("- [x] All items completed\n- [x] Everything done\n")

    result = _invoke_text_complete(
        tmp_path,
        "Checking the build output now and will dispatch more subagents next.",
    )
    text = _extract_text(result)
    assert text == "Checking the build output now and will dispatch more subagents next.", (
        f"Checked-only TASKS.md must allow text. Got: {text[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 5: OPENCODE_SUBAGENT=1 → bypasses
# ═══════════════════════════════════════════════════════════════════════════════


def test_subagent_context_bypasses_enforcement(tmp_path: Path):
    """OPENCODE_SUBAGENT=1 → text.complete returns output unchanged."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "This message would be blocked in non-subagent mode but should pass through here.",
        env_override={"OPENCODE_SUBAGENT": "1"},
    )
    text = _extract_text(result)
    assert text == "This message would be blocked in non-subagent mode but should pass through here.", (
        f"Subagent must pass through. Got: {text[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6: GLUDD_STOP_ENFORCE=0 → text-only pending-work block remains active
# ═══════════════════════════════════════════════════════════════════════════════


def test_env_disable_does_not_bypass_enforcement(tmp_path: Path):
    """GLUDD_STOP_ENFORCE=0 cannot bypass the fundamental text-only gate."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "This message should pass through when enforcement is disabled.",
        env_override={"GLUDD_STOP_ENFORCE": "0"},
    )
    assert result is not None, "Hook should output JSON when blocking"
    blocked = result.get("result_text") or result.get("output_text", "")
    assert "TEXT-ONLY RESPONSE BLOCKED" in blocked, (
        "GLUDD_STOP_ENFORCE=0 must not bypass pending-work enforcement. "
        f"Got: {result}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 7: Status summary → BLOCKED regardless of evidence
# ═══════════════════════════════════════════════════════════════════════════════


def test_status_summary_blocked_even_with_evidence(tmp_path: Path):
    """Status summary phrasing + pending work → blocked even with commit hash evidence."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "Here's the final status of the session. commit abc12345 landed. "
        "CI GREEN. 42 passed. Continuing with remaining items.",
    )
    text = _extract_text(result)
    assert "STATUS-SUMMARY" in text or "BLOCKED" in text, (
        f"Status summary with evidence MUST be blocked. Got: {text[:300]!r}"
    )


def test_bolded_header_status_summary_blocked(tmp_path: Path):
    """Bolded headers + status bullets → blocked as status summary."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "**Feature Status:**\n"
        "**CI State:**\n"
        "- [x] Fixed auth\n"
        "- [ ] Missing tests\n"
        "- ✅ Deployed\n"
        "commit abc12345 landed. 42 passed.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"Bolded headers + bullets MUST be blocked. Got: {text[:300]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 8: Short false-done claim → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


def test_short_all_done_blocked(tmp_path: Path):
    """'All done.' with pending work → blocked as false-done claim."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(tmp_path, "All done.")
    text = _extract_text(result)
    assert "FALSE-DONE" in text or "BLOCKED" in text, (
        f"Short 'All done.' MUST block. Got: {text[:200]!r}"
    )


def test_short_ready_for_review_blocked(tmp_path: Path):
    """'Ready for review.' with pending work → blocked."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(tmp_path, "Ready for review.")
    text = _extract_text(result)
    assert "FALSE-DONE" in text or "BLOCKED" in text, (
        f"Short 'Ready for review.' MUST block. Got: {text[:200]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 9: QA response pattern → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


def test_qa_completed_in_this_session_blocked(tmp_path: Path):
    """'completed in this session' + pending work → blocked as QA response summary."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "Everything was completed in this session so far. Fixed the bug and added the feature.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"QA 'completed in this session' MUST block. Got: {text[:300]!r}"
    )


def test_qa_summary_of_what_was_done_blocked(tmp_path: Path):
    """'summary of what was done' + pending work → blocked."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "Here is a summary of what was done in this session. 3 fixes landed.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"QA summary phrase MUST block. Got: {text[:300]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 10: Clean text → still blocked at the text-only-while-work-exists tail
# ═══════════════════════════════════════════════════════════════════════════════


def test_clean_text_with_pending_work_blocked_at_tail(tmp_path: Path):
    """Clean text with no stop patterns but pending work → blocked by
    the text-only-while-work-exists tail gate (hasRealPendingWork + no evidence)."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "I am checking the build output and will dispatch more subagents next. "
        "The system is running normally and there are no issues to report right now.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"Clean text with pending work MUST block at tail. Got: {text[:300]!r}"
    )


def test_clean_text_without_pending_work_passes_through(tmp_path: Path):
    """Clean text with no pending work → passes through entirely."""
    result = _invoke_text_complete(
        tmp_path,
        "I am checking the build output and will dispatch more subagents next. "
        "The system is running normally and there are no issues to report right now.",
    )
    text = _extract_text(result)
    assert text == (
        "I am checking the build output and will dispatch more subagents next. "
        "The system is running normally and there are no issues to report right now."
    ), f"Clean text without work must pass. Got: {text[:200]!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 11: Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


def test_empty_text_not_blocked(tmp_path: Path):
    """Empty text always passes through."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(tmp_path, "")
    assert result is not None
    out_text = result.get("output_text", "")
    assert out_text == "", f"Empty text must pass. Got: {out_text!r}"


def test_text_with_evidence_still_blocked_when_pending_work(tmp_path: Path):
    """Text with commit hash + pass count + gate status but pending work
    → STILL blocked. The 2026-07-16 FIX removed the hasStructuredEvidence
    guard from the tail gate (line 957-962). Evidence in text does NOT
    make sending a text-only response acceptable while work exists."""
    (tmp_path / "TASKS.md").write_text("- [ ] pending work exists\n")

    result = _invoke_text_complete(
        tmp_path,
        "commit a1b2c3d4e5f6a7b8 — 42 passed. === GATE: PASSED ===. "
        "Collection OK. Dispatching next wave.",
    )
    text = _extract_text(result)
    assert "BLOCKED" in text, (
        f"Evidence does NOT bypass the tail gate. Must block. Got: {text[:300]!r}"
    )
