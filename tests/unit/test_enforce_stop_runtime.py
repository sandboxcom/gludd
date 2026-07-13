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

import json
import time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
)

ROOT = Path(__file__).parent.parent.parent

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
        try:
            parsed = json.loads(stdout_raw)
        except json.JSONDecodeError:
            pass
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
    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Results are in. All done. No further work needed.",
        env_overrides={"OPENCODE_SUBAGENT": "1"},
    )
    assert rc == 0, stderr
    assert parsed is not None, (
        f"Subagent guard must return output object; got null. stderr: {stderr}"
    )
    assert "All done" in parsed.get("text", ""), (
        f"Original text must be preserved; got: {parsed}"
    )
    # Persist block must NOT be written
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, (
        f"Subagent must not trigger persist block; got: {pb}"
    )


# ── (b) Env disable path ────────────────────────────────────────────────────


def test_stop_text_complete_env_disable_bypasses_enforcement(hook_plugin_env: HookEnv):
    """GLUDD_STOP_ENFORCE=0: text.complete returns undefined (allows text through)."""
    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. Everything is complete.",
        env_overrides={"GLUDD_STOP_ENFORCE": "0"},
    )
    assert rc == 0, stderr
    # When disabled, the hook returns undefined → harness prints null
    assert parsed is None, (
        f"Hook must return undefined when disabled; got: {parsed}"
    )


# ── (c) Fail-open on corrupt state ──────────────────────────────────────────


def test_stop_text_complete_corrupt_state_does_not_crash(hook_plugin_env: HookEnv):
    """Corrupt GLUDD_STOP_STATE_FILE does not cause a non-zero exit."""
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text("{{{ not valid json [[[broken")

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. No more work.",
    )
    assert rc == 0, (
        f"Corrupt state must not crash (fail-open). exit={rc} stderr: {stderr}"
    )
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
    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done.",
    )
    assert rc == 0, stderr
    # The hook returns undefined after blocking (inconsistent with some paths
    # that return output), so parsed will be null.
    # Key evidence: the persist block file must be written.
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, (
        f"Persist block file must be written by short-false-done path. "
        f"stdout={raw} stderr={stderr}"
    )
    assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
    assert "short-false-done" in pb.get("reason", ""), (
        f"Reason must be 'short-false-done'; got: {pb}"
    )


def test_stop_text_complete_false_done_with_evidence_passes(hook_plugin_env: HookEnv):
    """False-done phrase WITH commit hash + pass count evidence is NOT blocked.

    The narrowing at line 1244 checks hasStructuredEvidence — a commit hash
    plus a pass count with text length < 500 bypasses the false-done block.
    """
    text_with_evidence = (
        "All done. Fixed the bug in src/daemon.py.\n"
        "commit abc1234 — 42 tests passed."
    )
    assert len(text_with_evidence) < 500, "test setup: text must be <500 chars"
    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        text_with_evidence,
    )
    assert rc == 0, stderr
    # Should NOT be blocked — evidence bypasses the false-done check.
    pb = _read_persist_block(hook_plugin_env)
    if pb and pb.get("blocked"):
        # Evidence should have prevented this
        pytest.fail(
            f"Evidence should bypass false-done block. "
            f"persist_block={pb} stdout={raw}"
        )


def test_stop_text_complete_qa_response_pattern_blocked(hook_plugin_env: HookEnv):
    """QA_RESPONSE_PATTERNS like 'completed in this session' are blocked.

    These patterns fire the qa-response-summary-stop path when hasLocalWork
    or ciVerdictPendingOrRed is true. To make that true, we pre-seed a state
    file with tasksMdUnchecked=true.
    """
    # Pre-seed shared state with pending work
    state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(json.dumps({
        "ts": int(time.time() * 1000),
        "ratchetEntries": 0,
        "tasksMdUnchecked": True,
        "gateStatusRed": False,
        "repoPending": False,
        "hasPendingWork": True,
        "hasLocalWork": True,
        "ciVerdictPendingOrRed": False,
        "healthScore": 70,
    }))

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "Here is a summary of what was completed in this session:\n"
        "- Fixed bug A\n- Added feature B\n"
        "Everything committed and merged. Ready to move on.",
    )
    assert rc == 0, stderr
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, f"QA response must be blocked; stdout={raw}"
    assert pb.get("blocked") is True, f"Block must be recorded; got: {pb}"
    assert "qa-response-summary-stop" in pb.get("reason", ""), (
        f"Reason must be 'qa-response-summary-stop'; got: {pb}"
    )


# ── (e) Disengage escape path ────────────────────────────────────────────────


def test_stop_text_complete_disengage_allows_through(hook_plugin_env: HookEnv):
    """When block counter has disengageUntil in the future, isDisengaged()
    returns true and text.complete returns without blocking."""
    # Write block counter with disengageUntil 5 minutes in the future
    block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
    block_counter_path.write_text(json.dumps({
        "consecutiveBlocks": 0,
        "totalBlocks": 0,
        "lastBlockTs": 0,
        "disengageUntil": int(time.time() * 1000) + 300_000,
    }))

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done. Everything is complete.",
    )
    assert rc == 0, stderr
    # When disengaged, the hook returns early (line 1066); the false-done
    # detection should NOT fire. parsed will be null (undefined return).
    # The key: persist block must NOT be written.
    pb = _read_persist_block(hook_plugin_env)
    assert pb is None or pb.get("blocked") is not True, (
        f"Disengage must prevent block; got persist_block={pb}"
    )


def test_stop_text_complete_disengage_past_expired(hook_plugin_env: HookEnv):
    """When disengageUntil is in the past, isDisengaged() returns false,
    and blocking resumes normally."""
    # Write block counter with disengageUntil 5 minutes in the PAST
    block_counter_path = hook_plugin_env.state_path("GLUDD_BLOCK_COUNTER_FILE")
    block_counter_path.write_text(json.dumps({
        "consecutiveBlocks": 0,
        "totalBlocks": 0,
        "lastBlockTs": 0,
        "disengageUntil": int(time.time() * 1000) - 300_000,
    }))

    parsed, raw, stderr, rc = _invoke_text_complete(
        hook_plugin_env,
        "All done.",
    )
    assert rc == 0, stderr
    # Disengage expired — false-done detection must fire
    pb = _read_persist_block(hook_plugin_env)
    assert pb is not None, (
        f"Expired disengage must not prevent block. stdout={raw}"
    )
    assert pb.get("blocked") is True, (
        f"Block must be recorded when disengage is expired; got: {pb}"
    )
