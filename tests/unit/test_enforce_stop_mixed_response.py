"""Prove the enforce-stop.ts gap: status-summary text with tool calls.

BUG (2026-07-15): The text.complete hook may only block PURE text responses
(text with zero tool calls). A response that carries BOTH status-summary text
AND tool calls can slip through — the text is rendered to the user, the tool
calls execute, and the agent appears to be "working" while actually sending a
stop-pattern summary.

The enforcement gap is two-layered:
  1. FRAMEWORK: text.complete may not fire when tool calls are present in the
     same response — the framework sees "has tool calls" and skips text.complete
     entirely. (Can't test through harness; documented as KNOWN GAP.)
  2. DETECTION: Neither QA_RESPONSE_PATTERNS nor COMPLETION_SMELL_RE currently
     catch "final status" / "session N final status" / bolded-header
     status-summary patterns. These are the exact stop-pattern shapes that
     mixed-response text carries.

Tests:
  (a) "final status" phrases currently NOT in any detection regex → gap
  (b) "Here's the session N final status" NOT detected → gap
  (c) Status-summary text with pending work → hasRealPendingWork block fires
  (d) Text without pending work → allowed through
  (e) COMPLETION_SMELL_RE coverage of status-summary vocabulary
  (f) Runtime: text.complete blanks status-summary text when pending work exists
  (g) Runtime: QA_RESPONSE_PATTERNS applied to bolded-header status text
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
)

ROOT = Path(__file__).parent.parent.parent
PLUGIN = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"
PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


# ── Ported regexes (must stay in sync with enforce-stop.ts) ──────────────────

def _src() -> str:
    return PLUGIN.read_text()


def _extract_qa_patterns() -> re.Pattern:
    """Extract QA_RESPONSE_PATTERNS regex body from enforce-stop.ts."""
    src = _src()
    m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "QA_RESPONSE_PATTERNS regex literal not found"
    flags = re.IGNORECASE if "i" in m.group(2) else 0
    return re.compile(m.group(1), flags)


def _extract_completion_smell() -> re.Pattern:
    """Extract COMPLETION_SMELL_RE regex body from enforce-stop.ts."""
    src = _src()
    m = re.search(r"COMPLETION_SMELL_RE\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "COMPLETION_SMELL_RE regex literal not found"
    flags = re.IGNORECASE if "i" in m.group(2) else 0
    return re.compile(m.group(1), flags)


def _extract_completion_words() -> re.Pattern:
    src = _src()
    m = re.search(r"COMPLETION_WORDS_RE\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "COMPLETION_WORDS_RE regex literal not found"
    return re.compile(m.group(1))


# ── Harness helpers ──────────────────────────────────────────────────────────

def _invoke_text_complete(
    env: HookEnv,
    text: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict | None, str, str, int]:
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
        import contextlib
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(stdout_raw)
    return parsed, stdout_raw, result.stderr, result.returncode


def _read_persist_block(env: HookEnv) -> dict | None:
    pb_path = env.cwd / "persist-stop-block.json"
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


def _seed_pending_work(env: HookEnv, **overrides) -> None:
    """Write a state file with hasPendingWork=true."""
    import time
    state = {
        "ts": int(time.time() * 1000),
        "tasksMdUnchecked": True,
        "tasksMdUncheckedCount": 5,
        "ratchetEntries": 2,
        "bugsOpen": False,
        "gateStatusMissing": False,
        "gateStale": False,
        "gateStatusRed": True,
        "ciVerdictPendingOrRed": True,
        "releaseIncomplete": False,
        "testFailures": False,
        "repoPending": False,
        "hasPendingWork": True,
        "hasLocalWork": True,
        "healthScore": 40,
    }
    state.update(overrides)
    state_path = env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(json.dumps(state))


# ═══════════════════════════════════════════════════════════════════════════════
# (a) "final status" and similar patterns — detection gap
# ─── ──────────────────────────────────────────────────────────────────────────

STATUS_SUMMARY_PHRASES = [
    # The canonical mixed-response stop patterns that slip through today.
    # Each is text the agent sends alongside tool calls to summarize state
    # before continuing — a stop pattern in disguise.
    "Here's the session 36 final status",
    "Session 36 final status before continuing",
    "final status summary for session 36",
    "Here is the final status of all items",
    "Session status overview",
    "Final status before next wave",
    "status summary before continuing work",
    "Current session status before dispatching",
    "Pre-wave status recap",
    "Session progress summary",
    "Here's where things stand before the next dispatch",
    "Status check before continuing",
]


class TestStatusSummaryDetectionGap:
    """Prove that "final status" and session-status-summary patterns are NOT
    caught by any existing detection regex in enforce-stop.ts."""

    def test_qa_patterns_miss_final_status(self):
        """QA_RESPONSE_PATTERNS does not match 'final status' phrasing."""
        qa = _extract_qa_patterns()
        for phrase in STATUS_SUMMARY_PHRASES:
            assert not qa.search(phrase), (
                f"QA_RESPONSE_PATTERNS MUST NOT already match '{phrase}' — "
                f"this test proves the DETECTION GAP. If it matches, the gap is "
                f"already closed and this test assertion should be flipped."
            )

    def test_completion_smell_misses_status(self):
        """COMPLETION_SMELL_RE does not match 'status' or 'session ... summary'."""
        cs = _extract_completion_smell()
        # "summary" / "status" / "recap" / "session" are NOT in COMPLETION_SMELL_RE
        assert not cs.search("session status summary"), (
            "COMPLETION_SMELL_RE must NOT contain 'summary' or 'status' — "
            "this proves the gap."
        )
        assert not cs.search("status recap"), (
            "COMPLETION_SMELL_RE must NOT contain 'recap' — proves the gap."
        )

    def test_completion_words_miss_final_status(self):
        """COMPLETION_WORDS_RE does not match 'final status'."""
        cw = _extract_completion_words()
        assert not cw.search("final status"), (
            "COMPLETION_WORDS_RE must NOT contain 'final' — proves gap."
        )

    def test_final_is_not_in_completion_smell(self):
        """'final' alone is not in COMPLETION_SMELL_RE."""
        cs = _extract_completion_smell()
        # Must fail: 'final' is a completion-adjacent word that should be caught
        assert not cs.search("final"), (
            "'final' is NOT in COMPLETION_SMELL_RE — this is the detection gap. "
            "If it already matches, flip this assertion."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (b) "Here's the session N final status" specifically NOT detected
# ─── ──────────────────────────────────────────────────────────────────────────

class TestSessionFinalStatusNotDetected:
    """The exact phrase pattern from the incident is NOT matched by any regex."""

    def test_session_final_status_not_in_qa(self):
        qa = _extract_qa_patterns()
        assert not qa.search("Here's the session 36 final status"), (
            "QA_RESPONSE_PATTERNS must NOT already match this — proves gap."
        )

    def test_session_final_status_not_in_completion_smell(self):
        cs = _extract_completion_smell()
        assert not cs.search("Here's the session 36 final status"), (
            "COMPLETION_SMELL_RE must NOT already match this — proves gap."
        )

    def test_every_phrase_misses_all_regexes(self):
        """For every phrase in STATUS_SUMMARY_PHRASES, verify at least one
        regex SHOULD catch it but currently does not."""
        qa = _extract_qa_patterns()
        cs = _extract_completion_smell()
        cw = _extract_completion_words()

        missed_by_all = []
        for phrase in STATUS_SUMMARY_PHRASES:
            if qa.search(phrase) or cs.search(phrase) or cw.search(phrase):
                missed_by_all.append(phrase)

        # If all are missed, the gap is proven — no regex catches any of them.
        # If some ARE caught, those specific phrases already have coverage;
        # the gap is in the ones that are missed.
        assert len(missed_by_all) == 0, (
            f"{len(missed_by_all)}/{len(STATUS_SUMMARY_PHRASES)} phrases ALREADY "
            f"matched by existing regexes (pre-gap-closure): {missed_by_all}. "
            f"If any match, the gap test assertions above need review."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (c) Runtime: text.complete blanks status-summary text when pending work exists
# ─── ──────────────────────────────────────────────────────────────────────────

class TestRuntimeStatusSummaryBlanked:
    """The hasRealPendingWork text-only block (line 776) fires on status-summary
    text when pending work exists, regardless of whether tool calls are present.

    NOTE: This test exercises the hook's TEXT DETECTION logic in isolation.
    The framework-level gap (text.complete not firing on text+tool responses)
    is documented as a KNOWN GAP. If the framework fixed that gap, this hook's
    detection logic would correctly blank the text.
    """

    def test_status_summary_with_pending_work_is_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """Status-summary text with pending work → text.complete blocks it."""
        _seed_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the session 36 final status — all work items dispatched, "
            "CI queued, continuing with remaining tasks. Session complete "
            "and ready for next wave.",
        )
        assert rc == 0, stderr

        # Must be blocked by SOME path:
        #   - COMPLETION_SMELL: "complete", "ready" trigger
        #   - hasRealPendingWork text-only block
        #   - QA_RESPONSE_PATTERNS if applicable
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"BUG: Status-summary text with pending work MUST be blocked. "
                f"parsed={parsed}, persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True, (
                f"Persist block must be recorded. Got: {pb}"
            )
        else:
            block_text = parsed.get("text", "")
            assert "BLOCKED" in block_text.upper(), (
                f"BUG: Status-summary text with pending work MUST be blocked. "
                f"Response: {raw[:300]}"
            )

    def test_final_status_without_pending_work_passes(
        self, hook_plugin_env: HookEnv,
    ):
        """Status-summary text with NO pending work → allowed through."""
        import time
        # Seed clean state — no pending work
        state_path = hook_plugin_env.state_path("GLUDD_STOP_STATE_FILE")
        state_path.write_text(json.dumps({
            "ts": int(time.time() * 1000),
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
        }))
        # Also need clean CI cache
        from pathlib import Path
        ci_path = Path("/tmp/gludd-watchdog-ci.json")
        _old_ci = ci_path.read_bytes() if ci_path.exists() else None
        ci_path.write_text(json.dumps({
            "last_ci_check": int(time.time() * 1000),
            "last_ci_status": "SUCCESS",
            "run_id": "000000",
            "head_sha": "000000000",
        }))
        try:
            _parsed, _raw, stderr, rc = _invoke_text_complete(
                hook_plugin_env,
                "Here's the session 36 final status — all work complete, "
                "CI green, nothing left to do.",
            )
            assert rc == 0, stderr

            # Should NOT be blocked — no pending work
            pb = _read_persist_block(hook_plugin_env)
            assert pb is None or pb.get("blocked") is not True, (
                f"No pending work → must not block. persist_block={pb}"
            )
        finally:
            if _old_ci is not None:
                ci_path.write_bytes(_old_ci)
            elif ci_path.exists():
                ci_path.unlink()

    def test_bolded_header_status_summary_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """Bolded-header status summary ('**Session Status**') with pending
        work is blocked by COMPLETION_SMELL (matches 'complete', 'done', etc.)."""
        _seed_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "**Session 36 Final Status**\n\n"
            "**Completed:** Bug A fixed, Feature B added.\n"
            "**In Progress:** Pipeline optimization.\n"
            "**Blocked:** None.\n"
            "**Next:** Continue with Phase D items.\n\n"
            "Everything is complete and ready for the next session.",
        )
        assert rc == 0, stderr

        # "complete", "completed", "ready" are all COMPLETION_SMELL matches
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Bolded-header status summary with pending work MUST be blocked. "
                f"persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True, f"Got: {pb}"
        else:
            block_text = parsed.get("text", "")
            assert "BLOCKED" in block_text.upper(), (
                f"Bolded-header status summary MUST be blocked. "
                f"Response: {raw[:300]}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# (d) Structural coverage: COMPLETION_SMELL_RE covers status-summary vocabulary
# ─── ──────────────────────────────────────────────────────────────────────────

class TestCompletionSmellStatusCoverage:
    """COMPLETION_SMELL_RE must cover status-summary vocabulary so that even
    when the text is accompanied by tool calls, the text is blocked."""

    def test_completion_words_in_completion_smell(self):
        """Every COMPLETION_WORDS term that is a single word must be in
        COMPLETION_SMELL_RE (which is checked later and catches long text
        that the short-false-done path misses)."""
        cs = _extract_completion_smell()
        cw = _extract_completion_words()

        # Single-word COMPLETION_WORDS entries (extracted from regex alternation)
        cw_body = cw.pattern
        single_words = re.findall(r"\b(\w[\w\s]*?)\b", cw_body)
        missing = []
        for w in single_words:
            w = w.strip()
            if len(w) < 2:
                continue
            # Check if this word (or its stem) is in COMPLETION_SMELL_RE
            if not cs.search(w):
                missing.append(w)

        # All COMPLETION_WORDS single words should be covered
        assert len(missing) == 0, (
            f"COMPLETION_SMELL_RE missing words also in COMPLETION_WORDS_RE: "
            f"{missing}. Every completion word should be detected by both regexes "
            f"so short AND long texts with pending work are blocked."
        )

    def test_qa_response_patterns_structural(self):
        """QA_RESPONSE_PATTERNS is consulted in text.complete hook (structural)."""
        src = _src()
        assert re.search(r"QA_RESPONSE_PATTERNS\.test\s*\(", src), (
            "QA_RESPONSE_PATTERNS.test() must be called in text.complete hook."
        )

    def test_completion_smell_consulted_in_text_complete(self):
        """COMPLETION_SMELL_RE is checked in text.complete."""
        src = _src()
        assert re.search(r"COMPLETION_SMELL_RE\.test\s*\(", src), (
            "COMPLETION_SMELL_RE.test() must be called in text.complete hook."
        )

    def test_has_real_pending_work_block_exists(self):
        """The hasRealPendingWork text-only block at line 776 exists."""
        src = _src()
        assert "workState.hasPendingWork && !hasStructuredEvidence(text)" in src, (
            "The hasRealPendingWork text-only block must exist in text.complete."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (e) Runtime: QA summary patterns in mixed-response text
# ─── ──────────────────────────────────────────────────────────────────────────

class TestRuntimeQaPatternsInStatusText:
    """QA_RESPONSE_PATTERNS applied to status-summary text that could appear
    alongside tool calls."""

    def test_completed_in_this_session_blocked(self, hook_plugin_env: HookEnv):
        """'completed in this session' is in QA_RESPONSE_PATTERNS."""
        _seed_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's what was completed in this session: 3 subagents returned, "
            "2 commits landed, CI queued. Continuing with next wave.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"'completed in this session' with pending work MUST be blocked. "
                f"persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), (
                f"MUST be blocked. Got: {raw[:300]}"
            )

    def test_what_changed_bolded_header_blocked(self, hook_plugin_env: HookEnv):
        """'**What changed?**' bolded question-header is in QA_RESPONSE_PATTERNS."""
        _seed_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "**What changed?** The pipeline now auto-recovers from OOM.\n"
            "**What's left?** Integration tests for the new recovery path.\n"
            "Continuing with the next wave of subagents.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Bolded Q&A headers with pending work MUST be blocked. "
                f"persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper(), (
                f"MUST be blocked. Got: {raw[:300]}"
            )

    def test_everything_committed_summary_blocked(self, hook_plugin_env: HookEnv):
        """'Everything committed and merged' is in QA_RESPONSE_PATTERNS."""
        _seed_pending_work(hook_plugin_env)

        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Everything committed and merged. Session is wrapping up. "
            "Will continue the remaining work in the next session.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"'Everything committed and merged' with pending work "
                f"MUST be blocked. persist_block={pb}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()


# ═══════════════════════════════════════════════════════════════════════════════
# (f) Runtime: Persist block carries through to tool.execute.before
# ─── ──────────────────────────────────────────────────────────────────────────

class TestPersistBlockCarryForward:
    """When text.complete writes a persist block, tool.execute.before denies
    the next non-dispatch tool call."""

    def test_persist_block_denies_next_non_dispatch_tool(
        self, hook_plugin_env: HookEnv,
    ):
        """After text.complete blocks status-summary text, the next write/edit
        call is denied until a dispatch is made."""
        _seed_pending_work(hook_plugin_env)

        # Step 1: text.complete blocks the status text
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the final status summary. Everything is done.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Persist block must be written. raw={raw}"
        assert pb.get("blocked") is True

        # Step 2: tool.execute.before should deny a non-dispatch tool
        result = hook_plugin_env.invoke(
            "enforce-stop.ts",
            "tool.execute.before",
            input={"tool": "write"},
            output={},
        )
        # tool.execute.before denies by returning {permissionDecision: "deny", ...}
        # It does NOT throw (that's question denial).
        out = result.stdout.strip()
        if out:
            import contextlib
            parsed_out = None
            with contextlib.suppress(json.JSONDecodeError):
                parsed_out = json.loads(out)
            if parsed_out and isinstance(parsed_out, dict):
                assert parsed_out.get("permissionDecision") == "deny", (
                    f"Write after persist block must be denied. Got: {parsed_out}"
                )

    def test_persist_block_cleared_by_dispatch(self, hook_plugin_env: HookEnv):
        """A task/agent/workflow dispatch clears the persist block."""
        _seed_pending_work(hook_plugin_env)

        # Step 1: text.complete blocks the status text
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "All done. Session complete.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None
        assert pb.get("blocked") is True

        # Step 2: dispatch a task — should clear the persist block
        result = hook_plugin_env.invoke(
            "enforce-stop.ts",
            "tool.execute.before",
            input={"tool": "task"},
            output={},
        )
        # After dispatch, persist block should be cleared
        pb_after = _read_persist_block(hook_plugin_env)
        assert pb_after is None or pb_after.get("blocked") is not True, (
            f"Dispatch must clear persist block. pb_after={pb_after}"
        )
        out = result.stdout.strip()
        if out:
            import contextlib
            parsed_out = None
            with contextlib.suppress(json.JSONDecodeError):
                parsed_out = json.loads(out)
            # Dispatch should not be denied
            if parsed_out and isinstance(parsed_out, dict):
                assert parsed_out.get("permissionDecision") != "deny", (
                    f"Dispatch must not be denied. Got: {parsed_out}"
                )
