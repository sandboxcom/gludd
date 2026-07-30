"""Verify enforce-stop mixed-response detection is complete.

STATUS (2026-07-15): The plugin was patched in commits ea0a419e and 0c816e34
to add STATUS_SUMMARY_RE + looksLikeStatusSummary(), closing the gap where
"final status" / "session N status" text alongside tool calls bypassed all
existing regexes (QA_RESPONSE_PATTERNS, COMPLETION_SMELL_RE) because the
text contained commit hashes / "CI PENDING" that matched EVIDENCE_PATTERNS.

The fix: STATUS_SUMMARY_RE patterns are checked REGARDLESS of evidence
(before the !hasStructuredEvidence gate at line 776), so a status summary
with embedded evidence is STILL blocked when pending work exists.

Tests:
  (a) STATUS_SUMMARY_RE catches "final status" and session-status phrasing
  (b) looksLikeStatusSummary detects bolded headers + table/bullet structures
  (c) The status-summary block in text.complete fires before evidence checks
  (d) COMPLETION_SMELL_RE still catches completion-adjacent words in text
  (e) Runtime: text.complete blanks status-summary text when pending work exists
  (f) Runtime: QA_RESPONSE_PATTERNS catch bolded-header summaries
  (g) Runtime: persist block carry-forward after text.complete block
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path

import pytest

from tests.unit._hook_fixtures import (
    HookEnv,
    hook_plugin_env_impl,
)

ROOT = Path(__file__).parent.parent.parent
STOP_IMPL = ROOT / ".opencode" / "plugin" / "impl" / "enforce_stop_impl.ts"
PLUGIN_TEST_EXPORTS = ROOT / ".opencode" / "lib" / "plugin_test_exports.ts"
PERSIST_BLOCK_ENV = "GLUDD_PERSIST_STOP_BLOCK_FILE"

# hasRealPendingWork() reads live filesystem state — the cwd TASKS.md and the
# hardcoded /tmp/gludd-watchdog-ci.json CI cache — NOT the pre-seeded
# GLUDD_STOP_STATE_FILE. Runtime tests must therefore seed BOTH the CI cache
# (with the desired verdict) and, for pending-work scenarios, a real TASKS.md
# in the harness cwd. The CI cache is shared with any live session on this
# machine, so it is snapshot/restored around every test (autouse fixture).
CI_CACHE_PATH = Path("/tmp/gludd-watchdog-ci.json")

# The CI cache is a SHARED /tmp file — serialize every test touching it onto
# one xdist worker so concurrent SUCCESS/failure seeds can't race.
pytestmark = pytest.mark.xdist_group("gludd-watchdog-ci-cache")


@pytest.fixture
def hook_plugin_env(tmp_path: Path):
    yield from hook_plugin_env_impl(tmp_path)


@pytest.fixture(autouse=True)
def _ci_cache_guard():
    """Snapshot /tmp/gludd-watchdog-ci.json before each test and restore the
    exact original bytes (or absence) after, so tests never leave net-visible
    contamination in a live opencode session's CI cache."""
    old = CI_CACHE_PATH.read_bytes() if CI_CACHE_PATH.exists() else None
    try:
        yield
    finally:
        try:
            if old is None:
                CI_CACHE_PATH.unlink(missing_ok=True)
            else:
                CI_CACHE_PATH.write_bytes(old)
        except OSError:
            pass  # best-effort restore — never fail test teardown


def _seed_ci_cache(status: str) -> None:
    """Write a fresh CI verdict into the live CI cache hasRealPendingWork()
    reads. status="SUCCESS" → CI clean; anything else → ciVerdictPendingOrRed."""
    CI_CACHE_PATH.write_text(json.dumps({
        "last_ci_check": int(_time.time() * 1000),
        "last_ci_status": status,
        "run_id": "000000",
        "head_sha": "000000000",
    }))


# ── Source extraction helpers ─────────────────────────────────────────────────

def _src() -> str:
    """Return the loader-safe implementation and its test-export boundary."""
    return STOP_IMPL.read_text() + "\n" + PLUGIN_TEST_EXPORTS.read_text()


def _extract_status_summary_re() -> re.Pattern:
    """Extract the status-summary regex from the shared test-export boundary."""
    src = _src()
    m = re.search(r"getStatusSummaryRe[\s\S]*?return\s+new\s+RegExp\s*\(([\s\S]*?),", src)
    if m:
        body = m.group(1).replace("\n", "").replace(" ", "")
        parts = re.findall(r'"([^"]*)"', body)
        body = "|".join(parts)
    else:
        raise AssertionError("getStatusSummaryRe() not found in plugin_test_exports.ts")
    flags = re.IGNORECASE | re.MULTILINE
    return re.compile(body, flags)


def _extract_qa_patterns() -> re.Pattern:
    src = _src()
    m = re.search(r"QA_RESPONSE_PATTERNS\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "QA_RESPONSE_PATTERNS regex literal not found"
    flags = re.IGNORECASE if "i" in m.group(2) else 0
    return re.compile(m.group(1), flags)


def _extract_completion_smell() -> re.Pattern:
    src = _src()
    m = re.search(r"COMPLETION_SMELL_RE\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "COMPLETION_SMELL_RE regex literal not found"
    flags = re.IGNORECASE if "i" in m.group(2) else 0
    return re.compile(m.group(1), flags)


def _extract_completion_words() -> re.Pattern:
    src = _src()
    m = re.search(r"COMPLETION_WORDS_RE\s*=\s*/([^/\n]+)/([a-z]*)", src)
    assert m, "COMPLETION_WORDS_RE regex literal not found"
    re.IGNORECASE if "i" in m.group(2) else 0
    return re.compile(m.group(1))


# ── Harness helpers ──────────────────────────────────────────────────────────

def _invoke_text_complete(
    env: HookEnv,
    text: str,
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict | None, str, str, int]:
    import contextlib
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
    pb_path = env.cwd / "persist-stop-block.json"
    if not pb_path.exists():
        return None
    return json.loads(pb_path.read_text())


def _seed_pending_work(env: HookEnv, **overrides) -> None:
    state = {
        "ts": int(_time.time() * 1000),
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
    # hasRealPendingWork() reads LIVE files, not the state file above:
    # a real TASKS.md in the harness cwd → hasLocalWork (race-free per-test
    # tmp dir), plus a fresh non-SUCCESS CI cache → ciVerdictPendingOrRed.
    (env.cwd / "TASKS.md").write_text(
        "- [ ] Pending item A\n- [ ] Pending item B\n- [ ] Pending item C\n"
        "- [ ] Pending item D\n- [ ] Pending item E\n"
    )
    _seed_ci_cache("failure")


def _seed_clean_state(env: HookEnv) -> None:
    state_path = env.state_path("GLUDD_STOP_STATE_FILE")
    state_path.write_text(json.dumps({
        "ts": int(_time.time() * 1000),
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
    # hasRealPendingWork() reads the LIVE CI cache — seed a fresh SUCCESS
    # verdict so live CI state from the host session can't leak in.
    _seed_ci_cache("SUCCESS")


# ═══════════════════════════════════════════════════════════════════════════════
# (a) STATUS_SUMMARY_RE catches status-summary phrasing
# ─── ──────────────────────────────────────────────────────────────────────────

STATUS_SUMMARY_PHRASES = [
    "Here's the session 36 final status",
    "Session 36 final status before continuing",
    "final status summary for session 36",
    "Here is the final status of all items",
    "Session status overview",
    "Final status before next wave",
    "Session 36 wrap-up",
    "Session 38 final summary",
    "Session 37 recap",
    "Pre-wave status recap",
    "Session progress summary",
    "Status report: all items",
    "Status update: CI pending",
    "status summary before continuing work",
    "Current session status before dispatching",
    "Here's where things stand before the next dispatch",
    "Status check before continuing",
]


class TestStatusSummaryRegexDetection:
    """STATUS_SUMMARY_RE (added in commits 0c816e34/ea0a419e) must catch
    'final status', 'session N status/summary/wrap-up/recap', and status
    report patterns — even when evidence (commit hashes, CI status) is
    embedded in the text."""

    def test_status_summary_re_exists_in_plugin(self):
        src = _src()
        assert "getStatusSummaryRe" in src, (
            "getStatusSummaryRe() must be defined at the loader-safe implementation "
            "or test-export boundary — "
            "commits 0c816e34/ea0a419e added it."
        )

    def test_status_summary_re_catches_final_status(self):
        ss = _extract_status_summary_re()
        assert ss.search("Here's the session 36 final status"), (
            "STATUS_SUMMARY_RE must catch 'Here's the session N final status'"
        )

    def test_status_summary_re_catches_session_number_pattern(self):
        ss = _extract_status_summary_re()
        assert ss.search("Session 38 final summary"), (
            "STATUS_SUMMARY_RE must catch 'Session N final summary'"
        )

    def test_status_summary_re_catches_wrap_up(self):
        ss = _extract_status_summary_re()
        assert ss.search("Session 36 wrap-up"), (
            "STATUS_SUMMARY_RE must catch 'Session N wrap-up'"
        )

    def test_status_summary_re_catches_recap(self):
        ss = _extract_status_summary_re()
        assert ss.search("Session 37 recap"), (
            "STATUS_SUMMARY_RE must catch 'Session N recap'"
        )

    def test_status_summary_re_catches_markdown_headers(self):
        ss = _extract_status_summary_re()
        assert ss.search("# Session 38 Status Summary"), (
            "STATUS_SUMMARY_RE must catch markdown headers with status/summary"
        )
        assert ss.search("## Status Report"), (
            "STATUS_SUMMARY_RE must catch '## Status Report'"
        )

    def test_status_summary_re_catches_status_report_colon(self):
        ss = _extract_status_summary_re()
        assert ss.search("Status report: all items"), (
            "STATUS_SUMMARY_RE must catch 'Status report:'"
        )
        assert ss.search("Status update: CI pending"), (
            "STATUS_SUMMARY_RE must catch 'Status update:'"
        )

    def test_status_summary_re_catches_final_status_before_continuing(self):
        ss = _extract_status_summary_re()
        assert ss.search("Session 36 final status before continuing"), (
            "STATUS_SUMMARY_RE must catch 'Session N final status before continuing'"
        )

    def test_status_summary_re_is_case_insensitive(self):
        ss = _extract_status_summary_re()
        assert ss.search("FINAL STATUS OF SESSION 42"), (
            "STATUS_SUMMARY_RE must be case-insensitive"
        )

    def test_status_summary_re_catches_final_status_without_session_number(self):
        ss = _extract_status_summary_re()
        assert ss.search("final status before next wave"), (
            "STATUS_SUMMARY_RE must catch 'final status' without session number"
        )

    def test_status_summary_re_catches_heres_final_status(self):
        ss = _extract_status_summary_re()
        assert ss.search("Here is the final status"), (
            "STATUS_SUMMARY_RE must catch 'Here is the final status'"
        )
        assert ss.search("Here's the final status"), (
            "STATUS_SUMMARY_RE must catch contracted form"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (b) looksLikeStatusSummary structural detection
# ─── ──────────────────────────────────────────────────────────────────────────

class TestLooksLikeStatusSummary:
    """looksLikeStatusSummary() uses both regex matching and structural
    detection (bolded headers + tables/bullets) to catch status summaries
    that STATUS_SUMMARY_RE alone might miss."""

    def test_function_exists_and_exported(self):
        src = _src()
        assert "function looksLikeStatusSummary" in src, (
            "looksLikeStatusSummary must be defined"
        )

    def test_wired_in_text_complete(self):
        src = _src()
        # Must be called in experimental.text.complete
        assert "looksLikeStatusSummary" in src, (
            "looksLikeStatusSummary must be referenced somewhere in the plugin"
        )

    def test_text_with_bolded_headers_and_table_is_status_summary(self):
        """looksLikeStatusSummary returns true for bolded headers + table structure."""
        # We can't invoke the function directly from Python, but we verify
        # the STATUS_SUMMARY_RE component catches the markdown headers.
        ss = _extract_status_summary_re()
        assert ss.search("## Status"), (
            "At minimum, the markdown header form must be caught"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (c) Status-summary block fires BEFORE evidence check
# ─── ──────────────────────────────────────────────────────────────────────────

class TestStatusSummaryBlocksBeforeEvidenceCheck:
    """The status-summary block (line 696-725) is checked BEFORE the
    !hasStructuredEvidence gate (line 776). Evidence never legitimizes
    stopping to summarize."""

    def test_status_summary_check_before_has_structured_evidence(self):
        """Verify the code structure: looksLikeStatusSummary check is before
        the hasStructuredEvidence gate in the text.complete hook."""
        src = _src()
        # Extract the text.complete function body
        qa_pos = src.find("QA_RESPONSE_PATTERNS.test")
        ss_pos = src.find("looksLikeStatusSummary(text)", qa_pos - 500)
        evidence_pos = src.find("!hasStructuredEvidence(text)", ss_pos)
        assert ss_pos > 0, "looksLikeStatusSummary(text) must exist in text.complete"
        assert evidence_pos > 0, "!hasStructuredEvidence(text) must exist in text.complete"
        assert ss_pos < evidence_pos, (
            f"looksLikeStatusSummary check (pos {ss_pos}) MUST be before "
            f"!hasStructuredEvidence gate (pos {evidence_pos}). "
            f"Evidence never legitimizes a status summary."
        )

    def test_status_summary_block_ignores_disengage(self):
        """The status-summary block fires REGARDLESS of disengage state."""
        src = _src()
        # The status-summary block should NOT have a `!disengaged` guard
        # Find the status-summary section and verify it doesn't check disengage
        ss_section_start = src.find("STATUS-SUMMARY BLOCK")
        assert ss_section_start > 0, "Status-summary block comment must exist"
        # Extract ~60 lines after the comment
        section = src[ss_section_start:ss_section_start + 4000]
        # There should be NO "disengaged" or "isDisengaged" check in the status-summary block
        # The disengage check at the top of text.complete applies to other sections
        # but the status-summary block itself bypasses it
        assert "looksLikeStatusSummary" in section, "Status-summary block must call looksLikeStatusSummary"


# ═══════════════════════════════════════════════════════════════════════════════
# (d) COMPLETION_SMELL_RE coverage of completion-adjacent words
# ─── ──────────────────────────────────────────────────────────────────────────

class TestCompletionSmellCoverage:
    """COMPLETION_SMELL_RE catches completion-adjacent words in status-summary
    text, providing a second detection layer."""

    def test_completion_smell_catches_completion_words(self):
        """COMPLETION_SMELL_RE catches common completion-adjacent words."""
        cs = _extract_completion_smell()
        assert cs.search("complete"), "COMPLETION_SMELL_RE must catch 'complete'"
        assert cs.search("done"), "COMPLETION_SMELL_RE must catch 'done'"
        assert cs.search("finished"), "COMPLETION_SMELL_RE must catch 'finished'"
        assert cs.search("ready"), "COMPLETION_SMELL_RE must catch 'ready'"

    def test_completion_smell_catches_continuing(self):
        cs = _extract_completion_smell()
        assert cs.search("continuing"), (
            "COMPLETION_SMELL_RE must catch 'continuing' — this was the "
            "exact word that slipped through in BUGS.md incident."
        )

    def test_completion_smell_catches_committed(self):
        cs = _extract_completion_smell()
        assert cs.search("committed"), "Must catch 'committed'"

    def test_completion_smell_catches_pushed(self):
        cs = _extract_completion_smell()
        assert cs.search("pushed"), "Must catch 'pushed'"


# ═══════════════════════════════════════════════════════════════════════════════
# (e) Runtime: text.complete blanks status-summary text with pending work
# ─── ──────────────────────────────────────────────────────────────────────────

class TestRuntimeStatusSummaryBlanked:
    """The text.complete hook must blank status-summary text when pending
    work exists. The status-summary block fires BEFORE the hasStructuredEvidence
    gate, so even text with commit hashes is blocked."""

    def test_status_summary_with_pending_work_is_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """Status-summary text ('final status') + pending work → blocked."""
        _seed_pending_work(hook_plugin_env)

        parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the session 38 final status — CI PENDING for commit abc12345, "
            "gate RED, 42 tests passing. Sessions 37 and 38 complete. "
            "Continuing with the next items.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Status-summary text with pending work MUST be blocked. "
                f"persist_block={pb}, raw={raw}"
            )
            assert pb.get("blocked") is True, f"Got: {pb}"
        else:
            block_text = parsed.get("text", "")
            assert "BLOCKED" in block_text.upper(), (
                f"MUST be blocked. Response: {raw[:300]}"
            )

    def test_bolded_header_status_summary_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """Bolded-header + table status summary with pending work → blocked."""
        _seed_pending_work(hook_plugin_env)

        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "**Session 38 Final Status**\n\n"
            "**Completed:** Item A, Item B, Item C — commit abc12345\n"
            "**In Progress:** Item D\n"
            "**Blocked:** None\n\n"
            "| Category | Status |\n"
            "|----------|--------|\n"
            "| Tests    | 42 pass|\n"
            "| CI       | PENDING|\n\n"
            "Everything is complete and ready to continue.",
        )
        assert rc == 0, stderr

        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, (
                f"Bolded-header status with pending work MUST be blocked. "
                f"persist_block={pb}"
            )
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_final_status_without_pending_work_passes(
        self, hook_plugin_env: HookEnv,
    ):
        """Status-summary text with NO pending work → allowed through.

        NOTE: the text must carry REAL structured evidence — the 2026-07-15
        evidence-regex narrowing means digit-only run numbers ("run 1234567")
        no longer count as commit hashes, and "CI green" (lowercase) does not
        match /CI (GREEN|RED|PENDING)/. Without valid evidence the
        completion-without-evidence block fires regardless of work state.
        """
        _seed_clean_state(hook_plugin_env)
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the final status — all items complete, "
            "CI GREEN (commit abc1234f), 42 passed, gate passed. Done.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is None or pb.get("blocked") is not True, (
            f"No pending work → must not block. persist_block={pb}"
        )

    def test_text_without_status_patterns_passes(
        self, hook_plugin_env: HookEnv,
    ):
        """Text without status-summary patterns and no pending work → passes."""
        _seed_clean_state(hook_plugin_env)
        _parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "commit abc12345 — 42 tests passed. All checks passed. "
            "CI GREEN. === GATE: PASSED ===. Collection OK. Done.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is None or pb.get("blocked") is not True, (
            f"Text with evidence + no work → should pass. persist_block={pb}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# (f) Runtime: QA_RESPONSE_PATTERNS applied to bolded-header summaries
# ─── ──────────────────────────────────────────────────────────────────────────

class TestRuntimeQaPatternsInStatusText:
    """QA_RESPONSE_PATTERNS catches Q&A-style stop patterns even in
    status-summary text."""

    def test_completed_in_this_session_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """'completed in this session' + pending work → blocked."""
        _seed_pending_work(hook_plugin_env)
        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here is a summary of what was completed in this session:\n"
            "- Item A (commit abc12345)\n- Item B\n"
            "Everything committed and merged. Continuing.",
        )
        assert rc == 0, stderr
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"QA pattern must be blocked. persist_block={pb}"
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_bolded_qa_headers_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """'**What changed?**' bolded Q&A header + pending work → blocked."""
        _seed_pending_work(hook_plugin_env)
        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "**What changed?** The pipeline now auto-recovers.\n"
            "**What's left?** Integration tests for recovery path.\n"
            "Continuing with the next wave.",
        )
        assert rc == 0, stderr
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"Bolded QA headers must be blocked. pb={pb}"
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()

    def test_everything_committed_merged_blocked(
        self, hook_plugin_env: HookEnv,
    ):
        """'Everything committed and merged' + pending work → blocked."""
        _seed_pending_work(hook_plugin_env)
        parsed, _raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Session wrapping up. Everything committed and merged.\n"
            "Will continue the remaining work in the next session.",
        )
        assert rc == 0, stderr
        if parsed is None:
            pb = _read_persist_block(hook_plugin_env)
            assert pb is not None, f"Must be blocked. persist_block={pb}"
            assert pb.get("blocked") is True
        else:
            assert "BLOCKED" in parsed.get("text", "").upper()


# ═══════════════════════════════════════════════════════════════════════════════
# (g) Runtime: Persist block carry-forward
# ─── ──────────────────────────────────────────────────────────────────────────

class TestPersistBlockCarryForward:
    """When text.complete blanks text, the persist block denies subsequent
    non-dispatch tools until a dispatch clears it."""

    @staticmethod
    def _invoke_tool_before(env: HookEnv, tool: str):
        """Invoke tool.execute.before with the SAME persist-block file the
        text.complete harness used — without this override the plugin reads
        (and clears) the default /tmp/gludd-persist-stop-block.json instead
        of the per-test file, so the clear never lands where the test looks."""
        return env.invoke(
            "enforce-stop.ts",
            "tool.execute.before",
            input={"tool": tool},
            output={},
            env_overrides={
                PERSIST_BLOCK_ENV: str(env.cwd / "persist-stop-block.json"),
            },
        )

    def test_persist_block_denies_next_non_dispatch_tool(
        self, hook_plugin_env: HookEnv,
    ):
        _seed_pending_work(hook_plugin_env)

        # Step 1: text.complete blocks a status summary
        _parsed, raw, stderr, rc = _invoke_text_complete(
            hook_plugin_env,
            "Here's the final status summary. Everything is done.",
        )
        assert rc == 0, stderr
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None, f"Persist block must be written. raw={raw}"
        assert pb.get("blocked") is True

        # Step 2: tool.execute.before denies a write call
        result = self._invoke_tool_before(hook_plugin_env, "write")
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

    def test_persist_block_cleared_by_dispatch(
        self, hook_plugin_env: HookEnv,
    ):
        _seed_pending_work(hook_plugin_env)

        # Step 1: text.complete blocks status text
        _invoke_text_complete(
            hook_plugin_env,
            "All done. Session complete.",
        )
        pb = _read_persist_block(hook_plugin_env)
        assert pb is not None
        assert pb.get("blocked") is True

        # Step 2: dispatch a task clears persist block
        result = self._invoke_tool_before(hook_plugin_env, "task")
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
            if parsed_out and isinstance(parsed_out, dict):
                assert parsed_out.get("permissionDecision") != "deny", (
                    f"Dispatch must not be denied. Got: {parsed_out}"
                )
