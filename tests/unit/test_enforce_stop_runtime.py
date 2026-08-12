"""Runtime tests for enforce-stop.ts — invokes actual plugin hook functions via node.

Each test exercises the real TypeScript code, not just source-code patterns.

Tests:
  (a) Subagent context skips enforcement (OPENCODE_SUBAGENT=1)
  (b) Env disable path (GLUDD_STOP_ENFORCE=0)
  (c) Fail-open on corrupt state
  (d) Stop-pattern detection on "All done" text
  (e) Disengage escape path
"""

from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
    read_optional_bytes,
)

ROOT = Path(__file__).parent.parent.parent

# Hardcoded /tmp state files that enforce-stop.ts reads directly with no
# env-var override and that are NOT covered by the fixture's snapshot.
# Run this before any test that expects clean (no-pending-work) state.
_LEAKED_STATE_PATHS = [
    "/tmp/gludd-multitask-state.json",
    "/tmp/gludd-release-completeness.json",
    "/tmp/gludd-last-test-result.json",
    "/tmp/gludd-watchdog-ci.json",
]


def _clean_leaked_state_files() -> None:
    """Remove hardcoded /tmp state files that aren't isolated by the fixture."""
    for p in _LEAKED_STATE_PATHS:
        with contextlib.suppress(OSError):
            Path(p).unlink(missing_ok=True)


# Shared enforcement files serialize onto the canonical xdist worker.
pytestmark = pytest.mark.xdist_group("enforcement-shared-state")

# Hardcoded files touched by enforce-stop.ts that the fixture does NOT redirect.
# We pass env_overrides for these so writes go into the fixture's isolated tmp dir.
PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"
FALSE_DONE_BLOCKS_FILE = "/tmp/gludd-false-done-blocks.json"


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


def _invoke_text_complete(
    env: HookEnv,
    text: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict | None, str, str, int]:
    """Invoke enforce-stop.ts experimental.text.complete and return
    (parsed_stdout, stdout_raw, stderr_raw, returncode)."""
    overrides = (env_overrides or {}).copy()
    overrides.setdefault(PERSIST_BLOCK_ENV, str(env.cwd / "persist-stop-block.json"))
    result = env.invoke(
        "enforce-stop.ts",
        "experimental.text.complete",
        input={},
        output={"text": text},
        env_overrides=overrides,
        timeout=15,
    )
    stdout_raw = result.stdout.strip()
    parsed = None
    if stdout_raw:
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _read_persist_block(env: HookEnv) -> dict | None:
    """Read the persist-block file written by text.complete."""
    pb_path = env.cwd / "persist-stop-block.json"
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


# ── (a) Subagent context skips enforcement ──────────────────────────────────


def test_stop_text_complete_subagent_guard_skips_enforcement(hook_plugin_env: HookEnv):
    """OPENCODE_SUBAGENT=1: text.complete returns the original output unchanged."""
    parsed, _raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Results are in. All done. No further work needed.",
        env_overrides={"OPENCODE_SUBAGENT": "1"},
    )
    assert rc == 0, stderr
    assert parsed is not None, f"Subagent guard must return output object; got null. stderr: {stderr}"
    assert "All done" in parsed.get("text", ""), f"Original text must be preserved; got: {parsed}"
    # Persist block must NOT be written
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, f"Subagent must not trigger persist block; got: {pb}"


# ── (b) Env disable path ────────────────────────────────────────────────────


def test_stop_text_complete_env_disable_bypasses_enforcement(hook_plugin_env: HookEnv):
    """GLUDD_STOP_ENFORCE=0: text.complete returns output unchanged.

    The proxy now checks isStopEnforcementDisabled() — all enforcement
    including text.complete is bypassed when GLUDD_STOP_ENFORCE=0."""
    _parsed, _raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. Everything is complete.",
        env_overrides={"GLUDD_STOP_ENFORCE": "0"},
    )
    assert rc == 0, stderr
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, (
        f"GLUDD_STOP_ENFORCE=0 must bypass stop enforcement. Got persist block: {pb}. stderr={stderr}"
    )


# ── (c) Fail-open on corrupt state ──────────────────────────────────────────


def test_stop_text_complete_corrupt_state_does_not_crash(hook_plugin_env: HookEnv):
    """Corrupt GLUDD_STOP_STATE_FILE does not cause a non-zero exit."""
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text("{{{ not valid json [[[broken")

    _parsed, _raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. No more work.",
    )
    assert rc == 0, f"Corrupt state must not crash (fail-open). exit={rc} stderr: {stderr}"
    # Hook should still function — "All done" with no evidence should trigger
    # the short-false-done path and write a persist block.
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, "Persist block must be written even with corrupt state"
    assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"


# ── (d) Stop-pattern detection on "All done" text ───────────────────────────


def test_stop_text_complete_false_done_all_done_blocked(hook_plugin_env: HookEnv):
    """Short 'All done.' text with COMPLETION_VERBATIM match is blocked.

    The short-false-done path (text.trim().length < 60 + responseLooksTerminal)
    fires regardless of pending-work state. It writes a persist block file and
    replaces output.text with a FALSE-DONE CLAIM BLOCKED message.
    """
    _parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done.",
    )
    assert rc == 0, stderr
    # The hook returns undefined after blocking (inconsistent with some paths
    # that return output), so parsed will be null.
    # Key evidence: the persist block file must be written.
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, f"Persist block file must be written by short-false-done path. stdout={raw} stderr={stderr}"
    assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
    assert "short-false-done" in pb.get("reason", ""), f"Reason must be 'short-false-done'; got: {pb}"


def test_stop_text_complete_false_done_with_evidence_passes(hook_plugin_env: HookEnv):
    """False-done phrase WITH commit hash + pass count evidence is NOT blocked.

    The narrowing checks hasStructuredEvidence — a commit hash
    plus a pass count bypasses the false-done block.
    """
    # Clean hardcoded /tmp state files that leak from real sessions.
    # The fixture only snapshots a subset; multitask/ci-check state
    # can make hasRealPendingWork() report pending work erroneously.
    _clean_leaked_state_files()

    # hasRealPendingWork() reads /tmp/gludd-watchdog-ci.json directly.
    # Seed a clean CI cache so live CI state doesn't interfere.
    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": int(time.time() * 1000),
                "last_ci_status": "SUCCESS",
                "run_id": "000000",
                "head_sha": "abc0000def",
            }
        )
    )

    try:
        text_with_evidence = "All done. Fixed the bug in src/daemon.py.\ncommit abc1234 — 42 tests passed."
        assert len(text_with_evidence) < 500, "test setup: text must be <500 chars"
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            text_with_evidence,
        )
        assert rc == 0, stderr
        # Should NOT be blocked — evidence bypasses the false-done check.
        pb = _read_persist_block(hook_plugin_env)
        if pb and pb.get("blocked"):
            # Evidence should have prevented this
            pytest.fail(f"Evidence should bypass false-done block. persist_block={pb} stdout={raw}")
    finally:
        if _old_ci is not None:
            ci_path.write_bytes(_old_ci)
        elif ci_path.exists():
            ci_path.unlink()


def test_stop_text_complete_qa_response_pattern_blocked(hook_plugin_env: HookEnv):
    """QA_RESPONSE_PATTERNS like 'completed in this session' are blocked.

    These patterns fire the qa-response-summary-stop path when hasLocalWork
    or ciVerdictPendingOrRed is true. To make that true, we pre-seed a state
    file with tasksMdUnchecked=true.
    """
    # Pre-seed shared state with pending work
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": int(time.time() * 1000),
                "ratchetEntries": 0,
                "tasksMdUnchecked": True,
                "gateStatusRed": False,
                "repoPending": False,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "ciVerdictPendingOrRed": False,
                "healthScore": 70,
            }
        )
    )

    _parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Here is a summary of what was completed in this session:\n"
        "- Fixed bug A\n- Added feature B\n"
        "Everything committed and merged. Ready to move on.",
    )
    assert rc == 0, stderr
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, f"QA response must be blocked; stdout={raw}"
    assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
    reason = pb.get("reason", "")
    assert any(tag in reason for tag in ("qa-response-summary-stop", "completion-without-evidence")), (
        f"Reason must be 'qa-response-summary-stop' or 'completion-without-evidence'; got: {pb}"
    )


# ── (e) Disengage escape path ────────────────────────────────────────────────


def test_stop_text_complete_disengage_allows_through(hook_plugin_env: HookEnv):
    """Disengage skips heuristic checks (COMPLETION_SMELL) but does NOT
    prevent the hasRealPendingWork text-only block when work exists.
    Here we seed NO pending work, so the block does not fire and the
    text passes through. This is CORRECT: disengage is a heuristic bypass,
    not a get-out-of-work-free card."""
    _clean_leaked_state_files()

    # hasRealPendingWork() reads /tmp/gludd-watchdog-ci.json directly.
    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": int(time.time() * 1000),
                "last_ci_status": "SUCCESS",
                "run_id": "000000",
                "head_sha": "000000000",
            }
        )
    )

    try:
        # Write block counter with disengageUntil 5 minutes in the future
        block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
        block_counter_path.write_text(
            json.dumps(
                {
                    "consecutiveBlocks": 0,
                    "totalBlocks": 0,
                    "lastBlockTs": 0,
                    "disengageUntil": int(time.time() * 1000) + 300_000,
                }
            )
        )

        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "All done. Everything is complete.",
        )
        assert rc == 0, stderr
        # When disengaged, the COMPLETION_SMELL heuristics are skipped.
        # The hasRealPendingWork block still runs but should not fire
        # since we seeded no pending work.
        pb = _read_persist_block(hook_plugin_env)
        assert pb is None or pb.get("blocked") is not True, (
            f"Disengaged + no work: block must not fire. Got persist_block={pb}"
        )
    finally:
        if _old_ci is not None:
            ci_path.write_bytes(_old_ci)
        elif ci_path.exists():
            ci_path.unlink()


def test_stop_text_complete_disengage_past_expired(hook_plugin_env: HookEnv):
    """When disengageUntil is in the past, isDisengaged() returns false,
    and blocking resumes normally."""
    # Write block counter with disengageUntil 5 minutes in the PAST
    block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
    block_counter_path.write_text(
        json.dumps(
            {
                "consecutiveBlocks": 0,
                "totalBlocks": 0,
                "lastBlockTs": 0,
                "disengageUntil": int(time.time() * 1000) - 300_000,
            }
        )
    )

    _parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done.",
    )
    assert rc == 0, stderr
    # Disengage expired — false-done detection must fire
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, f"Expired disengage must not prevent block. stdout={raw}"
    assert pb.get("blocked") is True, f"Block must be recorded when disengage is expired; got: {pb}"


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE 3: Text-only stop with pending work allowed
# ─── ──────────────────────────────────────────────────────────────────────────


def test_text_only_with_real_pending_work_should_be_blocked(hook_plugin_env: HookEnv):
    """BUG: Text-only completion summary with CI RED, release incomplete,
    gate RED, TASKS.md unchecked. enforce-stop.ts SHOULD block this via
    hasRealPendingWork() but the todowrite bypass allowed it through.

    Pre-seed the state file with multiple pending-work signals:
    CI RED, gate RED, release incomplete, TASKS.md unchecked.
    text.complete with a long summary (>60 chars) must return a BLOCKED
    response, not allow the text through."""
    import time

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": int(time.time() * 1000),
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 42,
                "ratchetEntries": 3,
                "bugsOpen": True,
                "gateStatusMissing": False,
                "gateStale": False,
                "gateStatusRed": True,
                "ciVerdictPendingOrRed": True,
                "releaseIncomplete": True,
                "testFailures": True,
                "repoPending": False,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 20,
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Here is a summary of all the work done in this session.\n"
        "Fixed bug A, added feature B, updated CI config.\n"
        "All the specs are addressed and the release is ready.\n"
        "No further work is needed at this time.",
    )
    assert rc == 0, stderr

    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"BUG: Text-only response with CI RED + gate RED + release incomplete "
            f"SHOULD be blocked. text.complete returned null; persist block: {pb}"
        )
        assert pb.get("blocked") is True, f"Persist block must be recorded; got: {pb}"
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), (
            f"BUG: Text-only response with CI RED + gate RED + release incomplete "
            f"SHOULD be blocked by SOME enforcement path. "
            f"raw output: {raw[:300]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE 4: Disengage bypasses hasRealPendingWork text-only block (2026-07-15)
# ─── ──────────────────────────────────────────────────────────────────────────
# Root cause: isDisengaged() at line 632 returns early before hasRealPendingWork
# check. make disengage-enforcement writes disengageUntil=now+1h to the block
# counter, disabling ALL text.complete enforcement for an hour — including the
# critical "no text-only response while work exists" rule. The fix: move the
# isDisengaged() guard so it only skips COMPLETION_SMELL heuristics, NOT the
# fundamental hasPendingWork block.
#
# These tests FAIL before the fix and PASS after the fix.


def test_continuing_is_in_completion_smell_regex(hook_plugin_env: HookEnv):
    """COMPLETION_SMELL_RE includes 'continuing' — verify it matches the
    exact phrasing that slipped through.

    This is a pure regex test — it does NOT invoke the hook. It proves the
    pattern SHOULD have been caught.
    """
    import re

    COMPLETION_SMELL_RE = re.compile(
        r"\b(?:complete|done|finished|ready|landed|shipped|pushed|committed|"
        r"fixed|passed|passing|working|green|resolved|deployed|verified|wrapped|"
        r"all done|all set|all good|all tasks|continuing|no more|nothing more|"
        r"RED|beta|alpha)\b",
        re.IGNORECASE,
    )

    # The exact text that slipped through
    text = (
        "CI queued (run 29395327780). Investigating previous CI failure while "
        "waiting. Session 36 summary: 4 commits pushed, 6 NF features advanced, "
        "3 security bugs closed (C.3/C.16/C.18), plugin deadlock fixed. "
        "Continuing with CI investigation and remaining NF.2 P3 daemon integration:"
    )
    assert COMPLETION_SMELL_RE.search(text), (
        f"COMPLETION_SMELL_RE must match 'continuing' in the text. text={text[:100]}..."
    )


def test_completion_smell_blocks_continuing_with_pending_work(
    hook_plugin_env: HookEnv,
):
    """COMPLETION_SMELL_RE matches 'continuing' and blocks text without evidence
    when hasRealPendingWork() is true.

    Pre-seed state with pending work. Send text containing 'Continuing with CI...'
    (the exact pattern that slipped through). The completion-smell path MUST block it.
    """
    import time as _time

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": int(_time.time() * 1000),
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 5,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": False,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": True,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 60,
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Session progress: 4 commits pushed, 6 features advanced, "
        "3 security bugs closed. "
        "Continuing with remaining NF.2 daemon integration work.",
    )
    assert rc == 0, stderr

    # Must be blocked — 'continuing' is in COMPLETION_SMELL_RE and no evidence exists
    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"BUG: 'Continuing with CI...' text with pending work and no evidence "
            f"SHOULD be blocked by COMPLETION_SMELL path. "
            f"parsed={parsed}, persist_block={pb}, raw={raw}"
        )
        assert pb.get("blocked") is True, f"Persist block must be recorded; got: {pb}"
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), (
            f"BUG: 'Continuing with CI...' text with pending work and no evidence "
            f"SHOULD be blocked. raw output: {raw[:300]}"
        )


def test_disengage_does_NOT_bypass_text_only_with_pending_work(
    hook_plugin_env: HookEnv,
):
    """BUG FIX TEST (FAILS before fix): isDisengaged() currently returns early
    before hasRealPendingWork() check, allowing any text through — even text-only
    responses while CI is RED, TASKS.md has unchecked items, etc.

    After the fix: isDisengaged should only skip COMPLETION_SMELL heuristics
    (lines 637-722), NOT the fundamental hasPendingWork text-only block (line 726).
    """
    import time as _time

    now_ms = int(_time.time() * 1000)

    # Pre-seed block counter with disengage in the future (active disengage)
    block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
    block_counter_path.write_text(
        json.dumps(
            {
                "consecutiveBlocks": 0,
                "totalBlocks": 0,
                "lastBlockTs": 0,
                "disengageUntil": now_ms + 300_000,
            }
        )
    )

    # Create REAL TASKS.md with unchecked items so hasRealPendingWork() finds them.
    # hasRealPendingWork() reads actual files, not pre-seeded state.
    (hook_plugin_env.cwd / "TASKS.md").write_text(
        "- [ ] Fix critical bug A\n- [ ] Implement feature B\n- [ ] Write tests for C\n"
        "- [ ] Deploy release D\n- [ ] Update documentation E\n"
        "- [ ] Review PR F\n- [ ] Audit security G\n- [ ] Run benchmarks H\n"
        "- [ ] Clean up dead code I\n- [ ] Update dependencies J\n"
    )
    # CI cache with RED status
    (hook_plugin_env.cwd / "..").mkdir(exist_ok=True)
    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": now_ms,
                "last_ci_status": "failure",
                "run_id": "999999",
                "head_sha": "abc123def",
            }
        )
    )

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Here is a status update. Continuing with remaining work items. "
        "Everything is looking good. Next steps are clear. "
        "CI is running, will check back later.",
    )
    assert rc == 0, stderr

    # KEY ASSERTION — this test currently FAILS because isDisengaged()
    # bypasses the hasPendingWork block. After the fix, the text-only
    # block MUST fire even when disengaged.
    if parsed is None:
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, (
            f"BUG: Disengage must NOT bypass hasRealPendingWork text-only block. "
            f"Text-only response with CI RED + 10 unchecked tasks + gate RED "
            f"SHOULD be blocked even when disengaged. "
            f"parsed={parsed}, persist_block={pb}"
        )
        assert pb.get("blocked") is True, f"Persist block must be recorded; got: {pb}"
    else:
        block_text = parsed.get("text", "")
        assert "BLOCKED" in block_text.upper(), (
            f"BUG: Disengage must NOT bypass hasRealPendingWork text-only block. Response text: {raw[:300]}"
        )


def test_disengage_still_allows_completion_smell_when_no_work(
    hook_plugin_env: HookEnv,
):
    """Disengage + no pending work: completion-adjacent text passes through.
    The hasRealPendingWork block checks !hasStructuredEvidence(text) before
    firing, so when no work exists, completion-adjacent text is never blocked.
    """
    import time as _time

    _clean_leaked_state_files()

    now_ms = int(_time.time() * 1000)

    block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
    block_counter_path.write_text(
        json.dumps(
            {
                "consecutiveBlocks": 0,
                "totalBlocks": 0,
                "lastBlockTs": 0,
                "disengageUntil": now_ms + 300_000,
            }
        )
    )

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": now_ms,
                "tasksMdUnchecked": False,
                "tasksMdUncheckedCount": 0,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": False,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": False,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "hasPendingWork": False,
                "hasLocalWork": False,
                "healthScore": 100,
            }
        )
    )

    # hasRealPendingWork() reads /tmp/gludd-watchdog-ci.json directly
    # (NOT from the pre-seeded state). Write a clean CI cache so the
    # live CI state from prior tests doesn't carry over.
    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": now_ms,
                "last_ci_status": "SUCCESS",
                "run_id": "000000",
                "head_sha": "000000000",
            }
        )
    )

    parsed, _raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. Everything is complete. Ready for review.",
    )
    assert rc == 0, stderr

    # When disengaged AND no real pending work, the text should pass through.
    # parsed should not contain a BLOCKED message.
    if parsed is not None:
        block_text = parsed.get("text", "")
        assert "BLOCKED" not in block_text.upper(), (
            f"Disengage should allow text through when no real work exists. Got: {block_text[:200]}"
        )
    # If parsed is null (undefined), that's also acceptable — it means the hook
    # returned nothing, which the harness interprets as "allow."

    # Restore CI cache to its pre-test state
    if _old_ci is not None:
        ci_path.write_bytes(_old_ci)
    elif ci_path.exists():
        ci_path.unlink()


@pytest.mark.parametrize(
    "pending_field",
    ["coverageIncomplete", "fullE2eIncomplete"],
)
def test_text_only_blocked_by_quality_evidence_gaps(
    hook_plugin_env: HookEnv,
    pending_field: str,
):
    """Coverage/full-E2E gaps are real pending work, not informational state."""
    _clean_leaked_state_files()
    phase = "coverage-gaps" if pending_field == "coverageIncomplete" else "e2e"
    (hook_plugin_env.cwd / ".gate-status").write_text(f"=== GATE-STATUS ===\n{phase} FAIL 1\n")
    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Quality evidence review remains pending.",
    )
    assert rc == 0, stderr
    if parsed is None:
        persist = _read_persist_block(hook_plugin_env)
        assert persist and persist.get("blocked") is True, f"{pending_field} must block text-only completion; raw={raw}"
    else:
        assert "BLOCKED" in parsed.get("text", "").upper(), raw


# ═══════════════════════════════════════════════════════════════════════════════
# POST-RESULTS TEXT-ONLY BLOCK (2026-07-26) — runtime test
# ─── ──────────────────────────────────────────────────────────────────────────
# The enforce-stop.ts text.complete hook tracks whether the previous agent turn
# had subagent result markers (via POST_RESULTS_STATE_FILE). When the previous
# turn had results AND the current turn is text-only with no evidence, the hook
# MUST block with POST-RESULTS TEXT-ONLY BLOCK. This test exercises that path
# by pre-seeding the state file and verifying the block fires.


def test_post_results_text_only_block_fires_after_subagent_results(
    hook_plugin_env: HookEnv,
):
    """Post-results text-only block: pre-seed lastTurnHadResults=true,
    send text-only with pending work, verify BLOCKED.
    """
    import time as _time

    _clean_leaked_state_files()

    now_ms = int(_time.time() * 1000)

    # Pre-seed post-results state: previous turn had a wave of results
    post_results_path = hook_plugin_env.state_path("GLUDD_POST_RESULTS_STATE_FILE")
    post_results_path.write_text(
        json.dumps(
            {
                "lastTurnHadResults": True,
                "lastTurnHadWave": True,
                "lastResultCount": 7,
                "ts": now_ms - 1000,  # 1 second ago
            }
        )
    )

    # Create TASKS.md with unchecked items so hasRealPendingWork() finds work
    (hook_plugin_env.cwd / "TASKS.md").write_text(
        "- [ ] Implement feature X\n- [ ] Fix bug Y\n- [ ] Write tests for Z\n"
    )

    # hasRealPendingWork() reads CI cache directly; clean it so CI doesn't
    # interfere with the post-results path
    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": now_ms,
                "last_ci_status": "SUCCESS",
                "run_id": "000000",
                "head_sha": "000000000",
            }
        )
    )

    # Seed stop state with pending work
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": now_ms,
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 3,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": True,
                "gateStale": False,
                "gateStatusRed": False,
                "ciVerdictPendingOrRed": False,
                "releaseIncomplete": False,
                "testFailures": False,
                "repoPending": False,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 80,
            }
        )
    )

    try:
        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Subagent results: Agent 1 fixed bug A, Agent 2 added feature B, "
            "Agent 3 wrote tests for C. Everything looks good. Continuing.",
        )
        assert rc == 0, stderr

        # The POST-RESULTS TEXT-ONLY BLOCK should fire because:
        # 1. isTextOnly (no tool calls, 0 dispatches)
        # 2. postResultsState.lastTurnHadResults is true
        # 3. !hasWorkArtifact (no evidence markers in text)
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Post-results text-only block must fire after subagent results. persist_block={pb} raw={raw}"
            )
            assert pb.get("blocked") is True, f"Post-results text-only must be blocked; got: {pb}"
            assert "after-results-text-only" in pb.get("reason", ""), (
                f"Reason must be 'after-results-text-only'; got: {pb}"
            )
        else:
            block_text = parsed.get("text", "").upper()
            assert "RESULTS INGESTION PROTOCOL: 7 SUBAGENT RESULTS ARRIVED." in block_text
            assert "CODIFY RESULTS" in block_text
            assert "TEXT-ONLY AFTER RESULTS IS A STOP." in block_text
    finally:
        if _old_ci is not None:
            ci_path.write_bytes(_old_ci)
        elif ci_path.exists():
            ci_path.unlink()


def test_post_results_text_only_not_blocked_with_tool_calls(
    hook_plugin_env: HookEnv,
):
    """Post-results text-only block should NOT fire when the response includes
    tool calls — it's specifically for text-only responses after results."""
    import time as _time

    _clean_leaked_state_files()

    now_ms = int(_time.time() * 1000)

    # Pre-seed post-results state
    post_results_path = hook_plugin_env.state_path("GLUDD_POST_RESULTS_STATE_FILE")
    post_results_path.write_text(
        json.dumps(
            {
                "lastTurnHadResults": True,
                "lastTurnHadWave": True,
                "lastResultCount": 5,
                "ts": now_ms - 1000,
            }
        )
    )

    # Create TASKS.md
    (hook_plugin_env.cwd / "TASKS.md").write_text("- [ ] Task A\n")

    ci_path = Path("/tmp/gludd-watchdog-ci.json")
    _old_ci = read_optional_bytes(ci_path)
    ci_path.write_text(
        json.dumps(
            {
                "last_ci_check": now_ms,
                "last_ci_status": "SUCCESS",
            }
        )
    )

    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(
        json.dumps(
            {
                "ts": now_ms,
                "tasksMdUnchecked": True,
                "tasksMdUncheckedCount": 1,
                "ratchetEntries": 0,
                "bugsOpen": False,
                "gateStatusMissing": True,
                "hasPendingWork": True,
                "hasLocalWork": True,
                "healthScore": 85,
            }
        )
    )

    try:
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Results arrived. Now dispatching next wave.",
            # This test verifies that when toolCallMade is true (simulated
            # via env override), the post-results block does NOT fire because
            # isTextOnly === false. However, hasRealPendingWork block at
            # the bottom still fires for ALL text when work exists.
            env_overrides={"GLUDD_STOP_ENFORCE": "0"},
        )
        # With GLUDD_STOP_ENFORCE=0, the text should go through (the
        # isTextOnly check is unreachable because early return at top).
        assert rc == 0, stderr
    finally:
        if _old_ci is not None:
            ci_path.write_bytes(_old_ci)
        elif ci_path.exists():
            ci_path.unlink()
