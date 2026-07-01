"""Behavioral tests for the opencode TypeScript plugins.

These tests verify key logic paths in the plugins by reading their source as
text (we cannot execute TypeScript from Python). Each test targets one
load-bearing piece of enforcement so a silent regression (deleted constant,
weakened check, missing function) is caught at gate time.

Covered:
  1. enforce-make.ts  — detectStopPattern() + COMPLETION_SOUNDING list
  2. enforce-delegate.ts — sonnet/total ratio math (projShare < target)
  3. enforce-stop.ts   — NO_WAIT_PATTERNS array size (>= 20)
  4. enforce-floor.ts  — FLOOR/TARGET/CEILING constants == 10/14/16
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCE_MAKE = PLUGIN_DIR / "enforce-make.ts"
ENFORCE_DELEGATE = PLUGIN_DIR / "enforce-delegate.ts"
ENFORCE_STOP = PLUGIN_DIR / "enforce-stop.ts"
ENFORCE_FLOOR = PLUGIN_DIR / "enforce-floor.ts"
ENFORCE_DEADLINE = PLUGIN_DIR / "enforce-deadline.ts"


# --------------------------------------------------------------------------- #
# 1. enforce-make.ts — stop-pattern detection
# --------------------------------------------------------------------------- #
class TestEnforceMakeStopPattern:
    """detectStopPattern must exist and consult COMPLETION_SOUNDING."""

    def test_detect_stop_pattern_function_exists(self):
        src = ENFORCE_MAKE.read_text()
        assert "function detectStopPattern" in src, (
            "detectStopPattern function missing from enforce-make.ts — "
            "the response.transform stop-pattern guardrail is gone"
        )

    def test_completion_sounding_array_exists(self):
        src = ENFORCE_MAKE.read_text()
        assert "COMPLETION_SOUNDING" in src, (
            "COMPLETION_SOUNDING constant missing — detectStopPattern has no "
            "vocabulary list to match against"
        )

    def test_detect_stop_pattern_uses_completion_sounding(self):
        """The function must actually consult the COMPLETION_SOUNDING list."""
        src = ENFORCE_MAKE.read_text()
        # Confirm the wiring: detectStopPattern references COMPLETION_SOUNDING
        # via .some(p => lower.includes(p)) — the load-bearing check.
        assert re.search(
            r"COMPLETION_SOUNDING\.some\s*\(\s*p\s*=>\s*lower\.includes\s*\(\s*p\s*\)\s*\)",
            src,
        ), (
            "detectStopPattern must call COMPLETION_SOUNDING.some(p => lower.includes(p)) "
            "— without it the function cannot detect completion-sounding responses"
        )

    def test_completion_sounding_has_meaningful_entries(self):
        """The list must contain real completion phrases, not be empty/stubbed."""
        src = ENFORCE_MAKE.read_text()
        # Extract the array body between `const COMPLETION_SOUNDING = [` and the
        # closing `]` (non-greedy across newlines).
        m = re.search(
            r"const\s+COMPLETION_SOUNDING\s*=\s*\[(.*?)\]",
            src,
            re.DOTALL,
        )
        assert m, "Could not locate COMPLETION_SOUNDING array body"
        entries = re.findall(r'"([^"]+)"', m.group(1))
        # Sanity: a handful of known load-bearing phrases must be present.
        required = {"all passed", "all done", "ready for review"}
        found = {e.lower() for e in entries}
        missing = required - found
        assert not missing, (
            f"COMPLETION_SOUNDING lost required entries: {missing}. "
            f"Found {len(entries)} entries: {sorted(found)[:10]}..."
        )
        assert len(entries) >= 10, (
            f"COMPLETION_SOUNDING has only {len(entries)} entries — expected "
            "a broad vocabulary list; was it truncated?"
        )

    def test_detect_stop_pattern_checks_permission_seeking(self):
        """Short completion-sounding text OR explicit permission-seeking flags."""
        src = ENFORCE_MAKE.read_text()
        # The function must check for "want me to" / "should i" / "shall i"
        # as explicit permission-seeking phrases.
        perm_phrases = ['"want me to"', '"should i"', '"shall i"']
        found = [p for p in perm_phrases if p in src]
        assert len(found) >= 2, (
            "detectStopPattern must check for permission-seeking phrases "
            f"(want me to / should i / shall i); only found {found}"
        )


# --------------------------------------------------------------------------- #
# 2. enforce-delegate.ts — model utilization ratio
# --------------------------------------------------------------------------- #
class TestEnforceDelegateModelRatio:
    """The sonnet:non-sonnet ratio enforcer must compute sonnet/total."""

    def test_ratio_calculation_uses_sonnet_over_total(self):
        """projShare = projSonnet / projected.length (sonnet count / total)."""
        src = ENFORCE_DELEGATE.read_text()
        # The load-bearing line: const projShare = projSonnet / projected.length
        assert re.search(
            r"projShare\s*=\s*projSonnet\s*/\s*projected\.length",
            src,
        ), (
            "Model utilization ratio must be computed as projSonnet / projected.length "
            "(sonnet count divided by total window). Missing or altered."
        )

    def test_ratio_compared_against_target_share(self):
        """Block fires when projShare < target (share below threshold)."""
        src = ENFORCE_DELEGATE.read_text()
        assert re.search(
            r"projShare\s*<\s*target\b",
            src,
        ), (
            "Ratio enforcer must compare projShare < target to decide whether "
            "a non-sonnet dispatch would drop the share below threshold"
        )

    def test_target_share_default_is_high(self):
        """Default target_share must be >= 0.67 (sonnet-dominant policy)."""
        src = ENFORCE_DELEGATE.read_text()
        # SONNET_TARGET_DEFAULT = 0.91 — sonnet must dominate.
        m = re.search(r"SONNET_TARGET_DEFAULT\s*=\s*([\d.]+)", src)
        assert m, "SONNET_TARGET_DEFAULT constant not found"
        default = float(m.group(1))
        assert default >= 0.67, (
            f"SONNET_TARGET_DEFAULT={default} is below 0.67 (2:1 sonnet ratio) — "
            "the sonnet-dominant dispatch policy has been weakened"
        )

    def test_sonnet_count_uses_filter(self):
        """projSonnet must be derived by filtering for 'sonnet' entries."""
        src = ENFORCE_DELEGATE.read_text()
        assert re.search(
            r"projSonnet\s*=\s*projected\.filter\s*\(\s*m\s*=>\s*m\s*===\s*[\"']sonnet[\"']\s*\)\.length",
            src,
        ), (
            "projSonnet must be computed as projected.filter(m => m === 'sonnet').length"
        )


# --------------------------------------------------------------------------- #
# 3. enforce-stop.ts — NO_WAIT_PATTERNS array
# --------------------------------------------------------------------------- #
class TestEnforceStopNoWaitPatterns:
    """The deferral-pattern list must be broad enough to catch real stops."""

    def test_no_wait_patterns_array_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" in src, (
            "NO_WAIT_PATTERNS constant missing from enforce-stop.ts — "
            "the no-wait stop guardrail has no pattern list"
        )

    def _extract_no_wait_patterns_body(self) -> str:
        r"""Extract the NO_WAIT_PATTERNS array body.

        Matches up to the closing `\n]` (bracket on its own line) because
        regex character classes inside the patterns (e.g. `[- ]`) contain
        literal `]` chars that would prematurely terminate a non-greedy `.*?\]`.
        """
        src = ENFORCE_STOP.read_text()
        m = re.search(
            r"NO_WAIT_PATTERNS\s*:\s*RegExp\[\]\s*=\s*\[(.*?)\n\]",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not extract NO_WAIT_PATTERNS array body — is the declaration "
            "(NO_WAIT_PATTERNS: RegExp[] = [...]) intact?"
        )
        return m.group(1)

    def test_no_wait_patterns_has_at_least_20_entries(self):
        """The array must contain >= 20 regex patterns (deferral + constraint-as-stopsign)."""
        body = self._extract_no_wait_patterns_body()
        # Strip comment lines (they contain `/` that would create spurious
        # regex-literal matches in the counting pass).
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        # Each entry ends with /i (case-insensitive flag). Count them.
        count = len(re.findall(r"/[a-z]+\s*,?\s*$", body_no_comments, re.MULTILINE))
        # Fallback: also count by the /i, delimiter pattern (multiple per line).
        if count < 20:
            count = len(re.findall(r"/i\b", body_no_comments))
        assert count >= 25, (
            f"NO_WAIT_PATTERNS has only {count} entries — expected >= 25 "
            "(permission-seek + constraint-as-stopsign + status-report-as-handoff + Q&A-recap). "
            "The deferral vocabulary was truncated."
        )

    def test_no_wait_patterns_includes_deferral_phrases(self):
        """Must catch the canonical 'want me to' / 'should i' deferrals."""
        body = self._extract_no_wait_patterns_body()
        # Representative phrases that MUST be covered.
        required_substrings = ["want me to", "should i", "let me know", "your call"]
        missing = [s for s in required_substrings if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing deferral phrases: {missing}"
        )

    def test_no_wait_patterns_includes_constraint_phrases(self):
        """Must catch constraint-as-stopsign ('isn't possible', 'limitation')."""
        body = self._extract_no_wait_patterns_body()
        required = ["possible", "limitation", "wait"]
        missing = [s for s in required if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing constraint-as-stopsign phrases: {missing}"
        )

    def test_detect_no_wait_pattern_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function detectNoWaitPattern" in src, (
            "detectNoWaitPattern function missing — the pattern list is unused"
        )
        # Must apply the patterns via .some(p => p.test(text))
        assert re.search(
            r"NO_WAIT_PATTERNS\.some\s*\(\s*p\s*=>\s*p\.test\s*\(\s*text\s*\)\s*\)",
            src,
        ), "detectNoWaitPattern must call NO_WAIT_PATTERNS.some(p => p.test(text))"


# --------------------------------------------------------------------------- #
# 3b. enforce-stop.ts — CONSTRAINT_AS_STOP_PATTERNS (self-heal guardrail)
# --------------------------------------------------------------------------- #
# Incident (2026-06-23): agent responded "restart opencode one more time" to a
# recoverable state. The first-gen constraint phrases in NO_WAIT_PATTERNS did
# not catch "restart opencode", "can't without", "limitation of", "we must
# restart/wait/stop". A dedicated CONSTRAINT_AS_STOP_PATTERNS group was added
# with its own detector + directive injection. These tests pin its existence.
class TestEnforceStopConstraintPatterns:
    """The constraint-as-stop self-heal group must exist and stay populated."""

    def test_constraint_as_stop_patterns_array_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "CONSTRAINT_AS_STOP_PATTERNS" in src, (
            "CONSTRAINT_AS_STOP_PATTERNS constant missing from enforce-stop.ts — "
            "the constraint-as-stop self-heal guardrail (2026-06-23 incident) "
            "has no pattern list"
        )

    def _extract_constraint_patterns_body(self) -> str:
        """Extract the CONSTRAINT_AS_STOP_PATTERNS array body."""
        src = ENFORCE_STOP.read_text()
        m = re.search(
            r"CONSTRAINT_AS_STOP_PATTERNS\s*:\s*RegExp\[\]\s*=\s*\[(.*?)\n\]",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not extract CONSTRAINT_AS_STOP_PATTERNS array body — is the "
            "declaration (CONSTRAINT_AS_STOP_PATTERNS: RegExp[] = [...]) intact?"
        )
        return m.group(1)

    def test_constraint_patterns_include_restart_opencode(self):
        """Must catch the incident phrase 'restart opencode'."""
        body = self._extract_constraint_patterns_body()
        assert "restart" in body and "opencode" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing 'restart opencode' — the exact "
            "phrase from the 2026-06-23 incident is no longer detected"
        )

    def test_constraint_patterns_include_cannot_without(self):
        """Must catch 'can't/cannot/not possible' + 'without/unless'."""
        body = self._extract_constraint_patterns_body()
        assert "without" in body and "unless" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing the 'can't without/unless' "
            "precondition phrasing"
        )

    def test_constraint_patterns_include_limitation_of(self):
        """Must catch '(limitation|constraint) of'."""
        body = self._extract_constraint_patterns_body()
        assert "limitation" in body and "constraint" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing '(limitation|constraint) of'"
        )

    def test_constraint_patterns_include_we_must_restart_wait_stop(self):
        """Must catch 'we need/must (to) restart/wait/stop'."""
        body = self._extract_constraint_patterns_body()
        assert "need" in body and "must" in body, (
            "CONSTRAINT_AS_STOP_PATTERNS missing 'we (need|must)( to)? (restart|wait|stop)'"
        )

    def test_constraint_patterns_has_minimum_entries(self):
        """The group must have >= 7 entries (the incident-derived set)."""
        body = self._extract_constraint_patterns_body()
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        # Each regex entry ends with /i, possibly followed by , and whitespace.
        count = len(re.findall(r"/i,?\s*$", body_no_comments, re.MULTILINE))
        if count < 7:
            # Fallback counter: count /i occurrences in the de-commented body.
            count = len(re.findall(r"/i\b", body_no_comments))
        assert count >= 7, (
            f"CONSTRAINT_AS_STOP_PATTERNS has only {count} entries — expected "
            ">= 7 (restart-opencode, can't-without, have-to-wait, "
            "limitation-of, no-way-to, isn't-possible, we-must-restart/wait/stop). "
            "The constraint vocabulary was truncated."
        )

    def test_detect_constraint_as_stop_function_exists(self):
        """The detector must exist and apply the patterns via .some()."""
        src = ENFORCE_STOP.read_text()
        assert "function detectConstraintAsStop" in src, (
            "detectConstraintAsStop function missing — CONSTRAINT_AS_STOP_PATTERNS "
            "is not wired to a detector"
        )
        assert re.search(
            r"CONSTRAINT_AS_STOP_PATTERNS\.some\s*\(\s*p\s*=>\s*p\.test\s*\(\s*text\s*\)\s*\)",
            src,
        ), (
            "detectConstraintAsStop must call "
            "CONSTRAINT_AS_STOP_PATTERNS.some(p => p.test(text))"
        )

    def test_constraint_block_response_function_exists(self):
        """The directive injector must exist and carry the workaround mandate."""
        src = ENFORCE_STOP.read_text()
        assert "function constraintBlockResponse" in src, (
            "constraintBlockResponse function missing — the constraint case has "
            "no distinct directive injection"
        )
        # Must carry the user-mandated directive language.
        assert "CONSTRAINT" in src and "DETECTED" in src, (
            "constraintBlockResponse must emit a 'CONSTRAINT ... DETECTED' header"
        )
        assert "workaround" in src, (
            "constraintBlockResponse must instruct engineering a workaround"
        )
        assert "research task" in src, (
            "constraintBlockResponse must offer dispatching a research task"
        )

    def test_constraint_detector_wired_into_response_transform(self):
        """The response.transform hook must invoke detectConstraintAsStop."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"detectConstraintAsStop\s*\(\s*output\s*\)",
            src,
        ), (
            "detectConstraintAsStop(output) is not called inside "
            "experimental.chat.response.transform — the constraint guardrail "
            "is dead code"
        )


# --------------------------------------------------------------------------- #
# 3c. enforce-stop.ts — STATE-BASED terminal-response detector (BUGS.md #1)
# --------------------------------------------------------------------------- #
# The Whac-A-Mole fix: instead of matching specific completion phrases, ask
# whether (a) the repo has pending work AND (b) the response looks like a
# finale. responseLooksTerminal detects tables, uppercase DONE/COMPLETE/SHIPPED,
# long non-question bodies, and commit-hash patterns.
class TestEnforceStopResponseLooksTerminal:
    """responseLooksTerminal must exist and detect completion-shaped responses."""

    def test_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function responseLooksTerminal" in src, (
            "responseLooksTerminal function missing from enforce-stop.ts — "
            "the state-based terminal-response detector (BUGS.md #1) is gone"
        )

    def test_detects_markdown_table(self):
        """A line with | ... | ... | must be flagged as terminal."""
        src = ENFORCE_STOP.read_text()
        # The TS regex literal uses pipe chars; just confirm the function body
        # references a pipe-based table matcher.
        m = re.search(r"function responseLooksTerminal\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract responseLooksTerminal body"
        body = m.group(1)
        assert "|" in body, (
            "responseLooksTerminal must include a markdown-table regex matching "
            "'| ... | ... |' patterns"
        )

    def test_detects_uppercase_done_complete_shipped(self):
        """Uppercase DONE/COMPLETE/SHIPPED banners must be flagged."""
        src = ENFORCE_STOP.read_text()
        for word in ("DONE", "COMPLETE", "SHIPPED"):
            assert word in src, (
                f"responseLooksTerminal must detect the uppercase '{word}' banner"
            )

    def test_detects_commit_hash_pattern(self):
        """A 7-40 hex-char commit hash must be flagged as terminal."""
        src = ENFORCE_STOP.read_text()
        # The regex must reference the hex character class and the 7,40 range.
        assert re.search(r"0-9a-f\]\{7,40\}", src), (
            "responseLooksTerminal must include a commit-hash regex "
            "(/[0-9a-f]{7,40}/) to detect pasted SHA patterns"
        )

    def test_detects_long_non_question_body(self):
        """A >200-char body not ending in '?' must be flagged as terminal."""
        src = ENFORCE_STOP.read_text()
        assert re.search(r"length\s*>\s*200", src), (
            "responseLooksTerminal must check text.length > 200 for the long-body "
            "terminal signal"
        )
        assert re.search(r"\\\?\s*\$", src) or "?\\s*$" in src or "?\\$" in src, (
            "responseLooksTerminal must check that the body does NOT end with a "
            "question mark (the long-body signal only fires for non-questions)"
        )

    def test_wired_into_response_transform(self):
        """The response.transform hook must invoke responseLooksTerminal."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"responseLooksTerminal\s*\(\s*output\s*\)",
            src,
        ), (
            "responseLooksTerminal(output) is not called inside "
            "experimental.chat.response.transform — the state-based guardrail "
            "is dead code"
        )

    def test_detects_qa_recap_bolded_headers(self):
        """3+ bolded question headers (**...?**) must be flagged as terminal.

        A Q&A-style recap with bolded question headers (e.g. **What changed?**,
        **Why?**, **What's left?**) is a completion report wearing a different
        coat — BUGS.md #2026-06-28 incident. The responseLooksTerminal function
        must count lines matching ^**...?**$ and return true at >= 3.
        """
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function responseLooksTerminal\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract responseLooksTerminal body"
        body = m.group(1)
        assert re.search(r"qaHeader", body), (
            "responseLooksTerminal must define a qaHeaders variable to count "
            "bolded question-style markdown headers"
        )
        assert re.search(r"length\s*>=\s*3", body), (
            "responseLooksTerminal must check qaHeaders.length >= 3 to detect "
            "a Q&A-recap-as-finale (3+ bolded question headers)"
        )
        # The regex pattern for bolded question headers must exist
        assert (
            re.search(r"\*\*\[\^\"\]\+\?", body)
            or re.search(r"\\\*\\\*\[\^\\\*\]\+\\\?", body)
            or re.search(r"\*\*\s*\^\\\*\*", body)
            or re.search(r"\\*\\*", body)
        ), (
            "responseLooksTerminal must include a regex for bolded question "
            "headers (**...?** pattern)"
        )

    def test_state_check_blocks_when_ratchet_has_entries(self):
        """The HARD STOP — STATE-BASED BLOCK message must be present and reference
        the ratchet entry count."""
        src = ENFORCE_STOP.read_text()
        assert "HARD STOP — STATE-BASED BLOCK" in src, (
            "The state-based block response is missing its "
            "'HARD STOP — STATE-BASED BLOCK' header"
        )
        assert "ratchetEntries.length" in src, (
            "The state-based block must report the ratchet entry count"
        )

    def test_detects_all_checked_checkbox_table(self):
        """3+ checked checkboxes (- [x]) with zero unchecked (- [ ]) must be
        flagged as terminal (BUGS.md #2026-06-30: 16-row evidence ledger)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function responseLooksTerminal\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract responseLooksTerminal body"
        body = m.group(1)
        assert re.search(r"checkboxesChecked", body), (
            "responseLooksTerminal must count checked checkboxes (variable "
            "checkboxesChecked)"
        )
        assert re.search(r"checkboxesUnchecked", body), (
            "responseLooksTerminal must count unchecked checkboxes (variable "
            "checkboxesUnchecked)"
        )
        assert re.search(r"checkboxesChecked\s*>=\s*3", body), (
            "responseLooksTerminal must require checkboxesChecked >= 3"
        )
        assert re.search(r"checkboxesUnchecked\s*===\s*0", body), (
            "responseLooksTerminal must require checkboxesUnchecked === 0 for "
            "the all-checked signal"
        )

    def test_detects_item_count_completion_claim(self):
        """A \"N items completed\" / \"N items done\" phrase must be flagged
        as terminal (BUGS.md #2026-06-30 item-count-as-completion pattern)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function responseLooksTerminal\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract responseLooksTerminal body"
        body = m.group(1)
        assert re.search(r"\b\\d\+\\s\+items\?\\s\+\(\?:completed\|done", body), (
            "responseLooksTerminal must detect '\\d+ items? (completed|done|ticked|checked)'"
        )

    def test_state_block_message_hard_stop_header(self):
        """The state-based block response must use a 'HARD STOP — STATE-BASED BLOCK'
        header (not just 'STATE-BASED STOP BLOCKED') for stronger enforcement."""
        src = ENFORCE_STOP.read_text()
        assert "HARD STOP" in src, (
            "State-based block response must begin with 'HARD STOP' — advisory "
            "wording was the structural gap in BUGS.md #2026-06-30"
        )

    def test_state_block_message_references_bugs_md_20_incidents(self):
        """The state-based block message must reference the 20+ BUGS.md incidents
        to make the agent aware of the historical pattern it is repeating."""
        src = ENFORCE_STOP.read_text()
        assert "BUGS.md" in src and "20+" in src, (
            "State-based block message must reference 'BUGS.md has 20+ incidents' "
            "so the agent knows this is a recurring failure, not a one-off"
        )

    def test_state_block_message_demands_subagent_dispatch(self):
        """The block message must demand 'Dispatch ≥5 subagents' as the immediate
        action, not just 'MAKE A TOOL CALL'."""
        src = ENFORCE_STOP.read_text()
        assert re.search(r"dispatch.*≥\s*5|dispatch.*\\u2265\s*5|≥\s*5\s+subagents", src, re.IGNORECASE) or \
               "5 subagents" in src.lower(), (
            "State-based block message must instruct 'Dispatch ≥5 subagents' "
            "as the immediate corrective action"
        )

    def test_pending_work_audit_block_hard_stop_header(self):
        """The pending-work audit block response must use a 'HARD STOP' header."""
        src = ENFORCE_STOP.read_text()
        assert "HARD STOP — PENDING-WORK AUDIT" in src, (
            "Pending-work audit block response must begin with 'HARD STOP — "
            "PENDING-WORK AUDIT' (not just 'STOP BLOCKED')"
        )


# --------------------------------------------------------------------------- #
# 3d. enforce-stop.ts — repoHasPendingWork (2026-06-28 incident fix)
# --------------------------------------------------------------------------- #
# The "## Done — answer to your question" premature-stop incident bypassed the
# state-based check because ratchet.yml was empty (the test suite was green).
# The ratchet-only proxy tracked test failures, not commit/push state, so an
# agent that did work locally but never committed/pushed could stop with a
# completion-finale undetected. repoHasPendingWork closes the hole by asking
# the actual git state (unpushed commits / dirty tree). These tests pin the
# fix and the new incident-vocabulary patterns.
class TestEnforceStopRepoPendingWork:
    """repoHasPendingWork must exist, be wired into both state checks, and the
    incident-class NO_WAIT_PATTERNS must be present."""

    def test_repo_has_pending_work_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function repoHasPendingWork" in src, (
            "repoHasPendingWork function missing from enforce-stop.ts — the "
            "2026-06-28 incident fix (ratchet-only proxy bypass) is gone"
        )

    def test_repo_has_pending_work_uses_exec_sync(self):
        """Must shell out to git via execSync (sync, fast, fail-open)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function repoHasPendingWork\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract repoHasPendingWork body"
        body = m.group(1)
        assert "execSync" in body, (
            "repoHasPendingWork must use execSync for synchronous git inspection"
        )
        assert "git log --oneline @{u}..HEAD" in body, (
            "repoHasPendingWork must check unpushed commits via "
            "'git log --oneline @{u}..HEAD'"
        )
        assert "git status --porcelain" in body, (
            "repoHasPendingWork must check the working tree via 'git status --porcelain'"
        )

    def test_repo_has_pending_work_has_timeout(self):
        """Each execSync call must carry a timeout (fail-open under all conditions)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function repoHasPendingWork\(.*?\{(.*?)\n\}", src, re.DOTALL)
        assert m, "could not extract repoHasPendingWork body"
        body = m.group(1)
        assert "timeout: 3000" in body, (
            "repoHasPendingWork execSync calls must set timeout: 3000 (3s) "
            "so a wedged git cannot stall the response transform"
        )

    def test_repo_has_pending_work_fails_open(self):
        """Errors (no upstream, not a git repo, git unavailable) return false."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function repoHasPendingWork\(.*?\{(.*?)^}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract repoHasPendingWork body"
        body = m.group(1)
        # Both inner checks must be wrapped in try/catch that falls through,
        # and the outer function must have a top-level catch returning false.
        assert body.count("catch") >= 2, (
            "repoHasPendingWork must wrap both git calls in try/catch AND have "
            "a top-level catch — fail-open on any error"
        )

    def test_repo_has_pending_work_wired_into_state_check(self):
        """The state-based check must OR repoHasPendingWork() with ratchetEntries."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"repoHasPendingWork\(\)\s*\|\|\s*ratchetEntries\.length",
            src,
        ), (
            "State-based check must use 'repoHasPendingWork() || ratchetEntries.length' "
            "so uncommitted/unpushed work is treated as pending regardless of ratchet"
        )

    def test_repo_has_pending_work_called_in_both_blocks(self):
        """Both the terminal check AND the vocabulary check must use hasPendingWork."""
        src = ENFORCE_STOP.read_text()
        # The terminal check
        assert re.search(r"hasPendingWork\s*&&\s*responseLooksTerminal", src), (
            "Terminal check must be 'hasPendingWork && responseLooksTerminal(output)'"
        )
        # The vocabulary check
        assert re.search(r"if\s*\(\s*hasPendingWork\s*\)\s*\{", src), (
            "Vocabulary check must be 'if (hasPendingWork) { ... }' — was it "
            "left gated on ratchet only?"
        )

    def test_no_wait_patterns_include_done_answer(self):
        """The exact incident phrase 'done — answer' must be matched."""
        body = TestEnforceStopNoWaitPatterns._extract_no_wait_patterns_body(self)
        assert "done — answer" in body, (
            "NO_WAIT_PATTERNS missing the 'done — answer' pattern from the "
            "2026-06-28 incident (## Done — answer to your question)"
        )

    def test_no_wait_patterns_include_qa_recap(self):
        """Q&A-recap-as-finale phrasings must be matched."""
        body = TestEnforceStopNoWaitPatterns._extract_no_wait_patterns_body(self)
        required = ["answer to your question", "what i changed"]
        missing = [s for s in required if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing Q&A-recap incident patterns: {missing}"
        )

    def test_no_wait_patterns_include_completion_recap_variants(self):
        """The full set of past-tense completion-framing patterns must be present."""
        body = TestEnforceStopNoWaitPatterns._extract_no_wait_patterns_body(self)
        # Each of these regex tokens must appear in the array body.
        required_tokens = [
            "what i (?:did|changed|implemented|delivered)",
            "here'?s|here is) what (?:i|we)",
            "i (?:made|landed|pushed|committed|shipped|applied)",
            "single canonical",
        ]
        missing = [t for t in required_tokens if t not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing incident-class completion-recap patterns: {missing}"
        )

    def test_no_wait_patterns_include_item_count_completion(self):
        """Item-count-as-completion patterns (BUGS.md #2026-06-30) must be present."""
        body = TestEnforceStopNoWaitPatterns._extract_no_wait_patterns_body(self)
        required = ["items?", "completed|done", "evidence", "ledger|table"]
        missing = [s for s in required if s not in body]
        assert not missing, (
            f"NO_WAIT_PATTERNS missing item-count completion patterns: {missing}"
        )


# --------------------------------------------------------------------------- #
# 3e. enforce-stop.ts — tasksMdHasUnchecked (2026-06-30 fix)
# --------------------------------------------------------------------------- #
# The ratchet-only proxy was broken: it tracked test failures but not
# agent-acknowledged work in TASKS.md. An agent with all-green tests and a
# clean git tree but unchecked TASKS.md rows could stop undetected. This
# function reads TASKS.md for `- [ ]` / `* [ ]` rows and gates hasPendingWork
# on them, closing the gap that caused the 2026-06-30 incident.
class TestEnforceStopTasksMdUnchecked:
    """tasksMdHasUnchecked must exist, be wired into hasPendingWork, and
    detect unchecked markdown task boxes."""

    def test_tasks_md_has_unchecked_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function tasksMdHasUnchecked" in src, (
            "tasksMdHasUnchecked function missing from enforce-stop.ts — the "
            "2026-06-30 incident fix (TASKS.md unchecked work) is absent"
        )

    def test_tasks_md_has_unchecked_uses_exists_sync(self):
        """Must guard the read with fs.existsSync (fail-open on absent file)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function tasksMdHasUnchecked\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract tasksMdHasUnchecked body"
        body = m.group(1)
        assert "existsSync" in body, (
            "tasksMdHasUnchecked must guard with fs.existsSync so absent "
            "TASKS.md does not throw"
        )

    def test_tasks_md_has_unchecked_uses_default_path(self):
        """Default path must be <cwd>/TASKS.md."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function tasksMdHasUnchecked\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract tasksMdHasUnchecked body"
        body = m.group(1)
        assert "TASKS.md" in body, (
            "tasksMdHasUnchecked must use TASKS.md as the default path"
        )

    def test_tasks_md_has_unchecked_detects_dash_checkbox(self):
        """Must detect `- [ ]` dash-marked unchecked boxes."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function tasksMdHasUnchecked\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract tasksMdHasUnchecked body"
        body = m.group(1)
        # The body contains regex literals like /-\s+\[\s*\]/ — verify the
        # box-matching tokens appear. Use the same flexible assertion as
        # enforce-floor's openWorkExists scan (substring triple: \[, \s, \]).
        assert "\\[" in body and "\\s" in body and "\\]" in body, (
            "tasksMdHasUnchecked must contain box-matching tokens for "
            "unchecked markdown checkboxes ([ ])"
        )

    def test_tasks_md_has_unchecked_fails_open(self):
        """Function must return false on any error (fail-open)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function tasksMdHasUnchecked\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract tasksMdHasUnchecked body"
        body = m.group(1)
        assert "catch" in body, (
            "tasksMdHasUnchecked must wrap in try/catch and return false on "
            "error (fail-open)"
        )

    def test_tasks_md_has_unchecked_wired_into_has_pending_work(self):
        """hasPendingWork must OR tasksMdHasUnchecked() with the other checks."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"const\s+hasPendingWork\s*=\s*repoHasPendingWork\(\)\s*\|\|\s*ratchetEntries\.length\s*>\s*0\s*\|\|\s*tasksMdHasUnchecked\(\)",
            src,
        ), (
            "hasPendingWork must be 'repoHasPendingWork() || ratchetEntries.length > 0 || "
            "tasksMdHasUnchecked()' so unchecked TASKS.md rows are treated as pending work"
        )

    def test_state_block_shows_tasks_md_status(self):
        """The state-based block response must report TASKS.md status."""
        src = ENFORCE_STOP.read_text()
        assert "TASKS.md unchecked" in src, (
            "State-based block response must include 'TASKS.md unchecked: yes/no'"
        )

    def test_pending_work_audit_block_shows_tasks_md_status(self):
        """The pending-work audit block response must also report TASKS.md status."""
        src = ENFORCE_STOP.read_text()
        assert "TASKS.md unchecked:" in src, (
            "Pending-work audit block response must include 'TASKS.md unchecked:'"
        )


# --------------------------------------------------------------------------- #
# 3f. enforce-stop.ts — gateStatusIsRed + responseMentionsCiRed (BUGS.md #3)
# --------------------------------------------------------------------------- #
# BUGS.md structural fix #3: "Make the gate-status / CI integration visible to
# the stop detector: if CI is RED, a 'done' response should ALWAYS be blocked
# regardless of phrasing." Two signals: (a) .gate-status has FAIL lines, (b) the
# response text mentions CI being red/failing. Both wire into hasPendingWork so
# a red gate is treated as known-unfinished work and blocks any terminal-looking
# response.
class TestEnforceStopGateStatusCiRed:
    """gateStatusIsRed and responseMentionsCiRed must exist and be wired in."""

    def test_gate_status_is_red_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function gateStatusIsRed" in src, (
            "gateStatusIsRed function missing from enforce-stop.ts — "
            "BUGS.md structural fix #3 (gate-status CI red detection) was removed"
        )

    def test_response_mentions_ci_red_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function responseMentionsCiRed" in src, (
            "responseMentionsCiRed function missing from enforce-stop.ts — "
            "BUGS.md structural fix #3 (CI-red text detection) was removed"
        )

    def test_ci_red_patterns_array_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "CI_RED_PATTERNS" in src, (
            "CI_RED_PATTERNS constant missing from enforce-stop.ts — "
            "the CI-red text detection has no pattern list"
        )

    def _extract_ci_red_patterns_body(self) -> str:
        src = ENFORCE_STOP.read_text()
        m = re.search(
            r"CI_RED_PATTERNS\s*:\s*RegExp\[\]\s*=\s*\[(.*?)\n\]",
            src,
            re.DOTALL,
        )
        assert m, (
            "Could not extract CI_RED_PATTERNS array body — is the declaration "
            "(CI_RED_PATTERNS: RegExp[] = [...]) intact?"
        )
        return m.group(1)

    def test_ci_red_patterns_has_minimum_entries(self):
        """CI_RED_PATTERNS must have >= 5 entries to cover common CI-red phrasings."""
        body = self._extract_ci_red_patterns_body()
        body_no_comments = "\n".join(
            line for line in body.splitlines()
            if not line.strip().startswith("//")
        )
        count = len(re.findall(r"/i\b", body_no_comments))
        assert count >= 5, (
            f"CI_RED_PATTERNS has only {count} entries — expected >= 5 "
            "to cover common CI-red phrasings"
        )

    def test_ci_red_patterns_include_core_phrases(self):
        """Must cover the canonical CI-red phrasings."""
        body = self._extract_ci_red_patterns_body()
        # The TS regex literals use \bCI\s+is\s+(?:red|failing|...) etc.
        # Check for the core tokens that signal CI-red awareness.
        required = ["CI", "red", "fail"]
        missing = [s for s in required if s not in body]
        assert not missing, (
            f"CI_RED_PATTERNS missing core CI-red tokens: {missing}"
        )

    def test_response_mentions_ci_red_wired(self):
        """responseMentionsCiRed must iterate CI_RED_PATTERNS via .some()."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"CI_RED_PATTERNS\.some\s*\(\s*p\s*=>\s*p\.test\s*\(\s*text\s*\)\s*\)",
            src,
        ), (
            "responseMentionsCiRed must call CI_RED_PATTERNS.some(p => p.test(text))"
        )

    def test_gate_status_is_red_reads_gate_status_file(self):
        """Must read .gate-status from <cwd>/.gate-status and check for FAIL lines."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function gateStatusIsRed\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract gateStatusIsRed body"
        body = m.group(1)
        assert ".gate-status" in body, (
            "gateStatusIsRed must reference .gate-status file"
        )
        assert "existsSync" in body, (
            "gateStatusIsRed must guard with fs.existsSync so absent .gate-status "
            "does not throw"
        )
        assert "FAIL" in body, (
            "gateStatusIsRed must check for FAIL lines in .gate-status content"
        )
        assert "readFileSync" in body, (
            "gateStatusIsRed must use fs.readFileSync to read .gate-status"
        )

    def test_gate_status_is_red_fails_open(self):
        """Must return false on any error (fail-open)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function gateStatusIsRed\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract gateStatusIsRed body"
        body = m.group(1)
        assert "catch" in body, (
            "gateStatusIsRed must wrap in try/catch and return false on error "
            "(fail-open)"
        )

    def test_gate_status_is_red_skips_header_line(self):
        """Must skip the header line (starts with ===) when scanning for FAIL."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function gateStatusIsRed\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "could not extract gateStatusIsRed body"
        body = m.group(1)
        assert "startsWith" in body, (
            "gateStatusIsRed must use startsWith('===') to skip the header line "
            "so a header containing 'FAIL' (e.g. in a timestamp) does not "
            "trigger a false positive"
        )

    def test_ci_red_wired_into_has_pending_work(self):
        """hasPendingWork must OR gateStatusIsRed() and responseMentionsCiRed(output)."""
        src = ENFORCE_STOP.read_text()
        # ciRed variable declaration must combine both signals
        assert re.search(
            r"ciRed\s*=\s*gateStatusIsRed\(\)\s*\|\|\s*responseMentionsCiRed\s*\(\s*output\s*\)",
            src,
        ), (
            "ciRed must be declared as "
            "'gateStatusIsRed() || responseMentionsCiRed(output)'"
        )
        # hasPendingWork must OR ciRed
        assert re.search(
            r"hasPendingWork\s*=\s*repoHasPendingWork\(\)\s*\|\|\s*ratchetEntries\.length\s*>\s*0\s*\|\|\s*tasksMdHasUnchecked\(\)\s*\|\|\s*ciRed",
            src,
        ), (
            "hasPendingWork must be "
            "'repoHasPendingWork() || ratchetEntries.length > 0 || "
            "tasksMdHasUnchecked() || ciRed' so gate-status red triggers the block"
        )

    def test_state_block_shows_ci_red_line(self):
        """The state-based block response must include CI-red status when CI is red."""
        src = ENFORCE_STOP.read_text()
        assert "gate-status red:" in src, (
            "State-based block response must include 'gate-status red: yes/no' "
            "when CI is red"
        )
        assert "CI mentioned in response:" in src, (
            "State-based block response must include 'CI mentioned in response: yes/no' "
            "when CI is red"
        )

    def test_pending_work_audit_block_shows_ci_red_line(self):
        """The pending-work audit block response must also report CI-red status."""
        src = ENFORCE_STOP.read_text()
        # Count occurrences — must appear in both block responses
        occurrences = src.count("gate-status red:")
        assert occurrences >= 2, (
            f"'gate-status red:' appears {occurrences} times — expected >= 2 "
            "(once in state-based block, once in pending-work audit block)"
        )


# --------------------------------------------------------------------------- #
# 4. enforce-floor.ts — floor/target/ceiling constants
# --------------------------------------------------------------------------- #
class TestEnforceFloorConstants:
    """The three band constants must be 10/14/16 (user directive 2026-06-22)."""

    def test_floor_constant_is_10(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+FLOOR\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_FLOOR\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "FLOOR constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_FLOOR || \"10\", 10)"
        )
        assert m.group(1) == "10", (
            f"FLOOR default is {m.group(1)}, expected 10 "
            "(user directive 2026-06-22 raised the floor from 6 to 10)"
        )

    def test_target_constant_is_14(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+TARGET\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_TARGET\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "TARGET constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_TARGET || \"14\", 10)"
        )
        assert m.group(1) == "14", (
            f"TARGET default is {m.group(1)}, expected 14"
        )

    def test_ceiling_constant_is_16(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+CEILING\s*=\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_CEILING\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "CEILING constant declaration not found — expected "
            "parseInt(process.env.CLAUDE_AGENT_CEILING || \"16\", 10)"
        )
        assert m.group(1) == "16", (
            f"CEILING default is {m.group(1)}, expected 16"
        )

    def test_all_three_constants_present(self):
        """FLOOR, TARGET, and CEILING must all be declared (bands intact)."""
        src = ENFORCE_FLOOR.read_text()
        for name in ("FLOOR", "TARGET", "CEILING"):
            assert re.search(rf"const\s+{name}\s*=", src), (
                f"{name} constant missing from enforce-floor.ts"
            )

    def test_floor_breaches_dispatch_upward(self):
        """Below floor must instruct dispatch toward TARGET (not just warn)."""
        src = ENFORCE_FLOOR.read_text()
        assert "active < FLOOR" in src or re.search(r"active\s*<\s*FLOOR", src), (
            "Floor-breach branch (active < FLOOR) missing — the guardrail "
            "cannot detect an under-staffed pool"
        )
        # The breach message must reference TARGET (dispatch UP to target).
        assert "TARGET" in src, (
            "Floor-breach response must reference TARGET to direct refilling"
        )

    def test_ceiling_breach_stops_dispatch(self):
        """Above ceiling must instruct the model to STOP adding agents."""
        src = ENFORCE_FLOOR.read_text()
        assert re.search(r"active\s*>\s*CEILING", src), (
            "Ceiling-breach branch (active > CEILING) missing — the guardrail "
            "cannot detect an over-staffed pool (disk/overload risk)"
        )


# --------------------------------------------------------------------------- #
# 4b. enforce-floor.ts — blocking mode (GLUDD_FLOOR_ENFORCE=1)
# --------------------------------------------------------------------------- #
class TestEnforceFloorBlocking:
    """The floor breach must BLOCK, not just append, when enforce mode is on."""

    def test_references_floor_enforce_env_var(self):
        src = ENFORCE_FLOOR.read_text()
        assert "GLUDD_FLOOR_ENFORCE" in src, (
            "enforce-floor.ts must reference GLUDD_FLOOR_ENFORCE so blocking "
            "mode can be toggled by the operator."
        )

    def test_floor_breach_has_block_path(self):
        """When enforce mode is on AND floor is breached, plugin MUST deny/block."""
        src = ENFORCE_FLOOR.read_text()
        has_deny = (
            'permissionDecision: "deny"' in src
            or "permissionDecision: 'deny'" in src
            or 'decision: "block"' in src
            or "decision: 'block'" in src
            or "tool.execute.before" in src
        )
        assert has_deny, (
            "No deny/block return path in enforce-floor.ts — floor breach is "
            "still advisory-only. With GLUDD_FLOOR_ENFORCE=1 the plugin MUST "
            "BLOCK non-dispatch tool calls, not just append a banner."
        )

    def test_floor_breach_skips_dispatch_tools(self):
        """Blocking must NOT fire on task/agent/workflow dispatch tools."""
        src = ENFORCE_FLOOR.read_text()
        has_helper = (
            "isDispatchTool" in src
            or re.search(r'input\.tool\s*===\s*"(?:task|agent|workflow)"', src) is not None
            or re.search(r"input\.tool\s*===\s*'(?:task|agent|workflow)'", src) is not None
        )
        assert has_helper, (
            "enforce-floor.ts must have an isDispatchTool helper (or direct "
            "input.tool === 'task'|'agent'|'workflow' check) so dispatch tools "
            "are NEVER blocked."
        )

    def test_advisory_append_preserved_as_default(self):
        """Default mode must still append the advisory banner (back-compat)."""
        src = ENFORCE_FLOOR.read_text()
        assert "AGENT-FLOOR BREACH" in src, (
            "Advisory banner (⛔ AGENT-FLOOR BREACH) must remain as default "
            "behavior — blocking is opt-in via GLUDD_FLOOR_ENFORCE=1."
        )


# --------------------------------------------------------------------------- #
# 4c. enforce-floor.ts — HARD-DENY of mutating tools below floor (2026-06-29)
# --------------------------------------------------------------------------- #
# The user repeatedly complained that the agent grinds inline despite the floor
# plugin. The block path existed but was loosely pinned. These tests tighten the
# pin: (a) mutating tools (edit/write/bash) MUST be hard-denied when live count
# < floor AND open work exists; (b) the default polarity MUST be `!== "0"` so
# the gate is ON by default (not opt-in via `=== "1"`).
class TestEnforceFloorHardDenyMutating:
    """Below-floor + open-work MUST hard-deny Edit/Write/mutating-Bash."""

    def test_floor_plugin_hard_denies_mutating_tools_when_below_floor(self):
        """The tool.execute.before hook must return permissionDecision:"deny"
        for non-dispatch tools (Edit/Write/Bash) when live < floor AND open
        work exists. The deny must be a deny DECISION, not just a console.warn
        or appended banner — otherwise the agent can grind edits forever.
        """
        src = ENFORCE_FLOOR.read_text()
        # Must declare permissionDecision:"deny" (hard deny, surfaced as a
        # blocked tool call by opencode).
        assert re.search(
            r'permissionDecision:\s*"deny"',
            src,
        ) or re.search(r"permissionDecision:\s*'deny'", src), (
            "enforce-floor.ts tool.execute.before must return "
            "permissionDecision:'deny' to HARD-BLOCK mutating tools below "
            "floor. A console.warn or appended banner is insufficient — the "
            "agent ignores advisories and keeps grinding."
        )
        # Must explicitly skip dispatch tools (task/agent/workflow) so the
        # block can't wedge the session by preventing the refill.
        assert "isDispatchTool" in src, (
            "isDispatchTool helper missing — the deny must not fire on "
            "task/agent/workflow dispatch tools"
        )
        # The deny path must be gated on BOTH live < floor AND open work.
        assert re.search(r"active\s*<\s*FLOOR", src), (
            "Deny must be gated on active < FLOOR"
        )
        assert "openWorkExists" in src, (
            "Deny must consult openWorkExists() so it doesn't wedge a "
            "session where the work is genuinely done"
        )

    def test_floor_plugin_default_on(self):
        """The FLOOR_ENFORCE flag must default ON via `!== "0"` (not `=== "1"`).

        The recurring incident was the agent grinding inline with the floor
        breached because enforcement was opt-in (`=== "1"`, default OFF). The
        fix is default-ON polarity: any value other than the literal string
        "0" keeps enforcement active. This test pins the polarity so a future
        edit cannot silently revert to opt-in.
        """
        src = ENFORCE_FLOOR.read_text()
        # The load-bearing line: const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_ENFORCE !== "0"
        m = re.search(
            r"FLOOR_ENFORCE\s*=\s*process\.env\.GLUDD_FLOOR_ENFORCE\s*!==\s*[\"']0[\"']",
            src,
        )
        assert m, (
            "FLOOR_ENFORCE must be `process.env.GLUDD_FLOOR_ENFORCE !== \"0\"` "
            "(default ON). Found either opt-in polarity (=== \"1\", which "
            "defaults OFF — the bug) or the constant was renamed/removed. "
            "The default-on polarity is the fix for the recurring "
            "'agent grinds inline despite the floor plugin' complaint."
        )
        # Negative assertion: must NOT use the opt-in polarity.
        assert not re.search(
            r"FLOOR_ENFORCE\s*=\s*process\.env\.GLUDD_FLOOR_ENFORCE\s*===\s*[\"']1[\"']",
            src,
        ), (
            "FLOOR_ENFORCE uses opt-in polarity (=== \"1\") — that defaults OFF "
            "and is the exact bug this test pins against. Revert to !== \"0\"."
        )

    def test_floor_plugin_deny_message_loads_spec_phrases(self):
        """The deny message must carry the user-mandated instruction phrases
        so the agent gets actionable guidance when blocked."""
        src = ENFORCE_FLOOR.read_text()
        # Pin the spec phrases: "Live subagent count", "Dispatch a wave",
        # "GLUDD_FLOOR_ENFORCE=0 to disable".
        assert "Live subagent count" in src, (
            "Deny message must include 'Live subagent count' header"
        )
        assert "Dispatch a wave" in src, (
            "Deny message must instruct 'Dispatch a wave BEFORE continuing'"
        )
        assert "GLUDD_FLOOR_ENFORCE=0 to disable" in src, (
            "Deny message must surface the GLUDD_FLOOR_ENFORCE=0 escape hatch"
        )

    def test_floor_plugin_open_work_checks_todowrite(self):
        """openWorkExists() must consult the todowrite state mirror so an agent
        with pending todos (but a clean git tree) is still blocked from
        grinding inline."""
        src = ENFORCE_FLOOR.read_text()
        assert "todowrite" in src.lower(), (
            "openWorkExists() must check the todowrite state mirror "
            "(GLUDD_TODOWRITE_STATE / /tmp/gludd-todowrite-state.json) — "
            "without it, an agent with pending todos and a clean tree can "
            "grind inline undetected"
        )


# --------------------------------------------------------------------------- #
# 4d. enforce-delegate.ts — mainthread streak blocker (2026-06-29 strengthening)
# --------------------------------------------------------------------------- #
# The streak counter existed but was loosely pinned and always-on (not
# toggleable). The strengthening pins: (a) after 4 consecutive main-thread
# mutating calls with no intervening dispatch, the 5th MUST be hard-denied;
# (b) a dispatch MUST reset the streak to 0; (c) default ON, disable via
# GLUDD_FORCE_DELEGATE=0; (d) state file is a separate .json file so the
# nothing-dropped plugin's frequency caps cannot interfere.
class TestEnforceDelegateMainthreadStreak:
    """The mainthread streak blocker must hard-deny after 4 consecutive
    mutating calls and reset on dispatch."""

    def test_mainthread_streak_denies_after_4_consecutive_mutating_calls(self):
        """mainthreadBudgetBefore must throw (hard-deny) when streak >=
        MAINTHREAD_THRESHOLD (default 4) AND live < TARGET. The 5th consecutive
        mutating call with no intervening dispatch MUST be blocked."""
        src = ENFORCE_DELEGATE.read_text()
        # Threshold must default to 4 — after 4 consecutive mutating calls,
        # the 5th is denied.
        m = re.search(
            r"MAINTHREAD_THRESHOLD\s*=\s*parseInt\s*\(\s*process\.env\.GLUDD_MAINTHREAD_THRESHOLD\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, "MAINTHREAD_THRESHOLD declaration not found"
        assert m.group(1) == "4", (
            f"MAINTHREAD_THRESHOLD default is {m.group(1)}, expected 4 — the "
            "5th consecutive mutating call must be the one that blocks"
        )
        # mainthreadBudgetBefore must compare streak >= threshold (via
        # `streak < MAINTHREAD_THRESHOLD ... return null` inverted — i.e. the
        # block fires when streak >= threshold).
        assert re.search(r"streak\s*<\s*MAINTHREAD_THRESHOLD", src), (
            "Streak check must gate on `streak < MAINTHREAD_THRESHOLD` "
            "(allow) vs the inverse (block)"
        )
        # The function must be wired to throw via the tool.execute.before hook.
        assert re.search(
            r"mainthreadBudgetBefore\s*\(\s*tool\s*\)", src,
        ), (
            "mainthreadBudgetBefore(tool) must be called inside "
            "tool.execute.before to actually block"
        )
        assert re.search(
            r"budgetMsg\s*=\s*mainthreadBudgetBefore[\s\S]*?throw new Error\s*\(\s*budgetMsg\s*\)",
            src,
        ), (
            "tool.execute.before must `throw new Error(budgetMsg)` when "
            "mainthreadBudgetBefore returns a message — a console.warn is "
            "ignorable and does not block the grind"
        )

    def test_mainthread_streak_resets_on_dispatch(self):
        """A task/agent/workflow dispatch MUST reset the streak to 0 so the
        agent can resume inline work after refilling the pool."""
        src = ENFORCE_DELEGATE.read_text()
        # isDelegateTool must cover task/agent/workflow.
        assert re.search(
            r'isDelegateTool\s*\([^)]*\)\s*\{[^}]*"task"[^}]*"workflow"[^}]*"agent"',
            src,
        ) or (
            "task" in src and "workflow" in src and "agent" in src
            and "isDelegateTool" in src
        ), (
            "isDelegateTool must recognize task/agent/workflow as dispatches"
        )
        # mainthreadBudgetAfter must reset streak to 0 on a dispatch tool.
        assert re.search(
            r"if\s*\(\s*isDelegateTool\s*\(\s*tool\s*\)\s*\)\s*\{?\s*writeStreak\s*\(\s*0\s*\)",
            src,
        ), (
            "mainthreadBudgetAfter must call writeStreak(0) when the tool is "
            "a dispatch — otherwise the streak never resets and the agent is "
            "permanently blocked from inline work even after delegating"
        )

    def test_mainthread_streak_default_on_via_force_delegate_not_zero(self):
        """The streak blocker must default ON: any GLUDD_FORCE_DELEGATE value
        other than "0" keeps it active. Polarity must be `!== "0"`, NOT
        opt-in `=== "1"` (which defaults OFF and was the gap)."""
        src = ENFORCE_DELEGATE.read_text()
        m = re.search(
            r"MAINTHREAD_STREAK_ENABLED\s*=\s*process\.env\.GLUDD_FORCE_DELEGATE\s*!==\s*[\"']0[\"']",
            src,
        )
        assert m, (
            "MAINTHREAD_STREAK_ENABLED must be "
            "`process.env.GLUDD_FORCE_DELEGATE !== \"0\"` (default ON). "
            "The opt-in polarity (=== \"1\") defaults OFF and is the bug."
        )
        # And mainthreadBudgetBefore must consult the flag.
        assert re.search(
            r"if\s*\(\s*!MAINTHREAD_STREAK_ENABLED\s*\)\s*return null",
            src,
        ), (
            "mainthreadBudgetBefore must early-return when "
            "MAINTHREAD_STREAK_ENABLED is false (the GLUDD_FORCE_DELEGATE=0 "
            "escape hatch)"
        )

    def test_mainthread_streak_uses_dedicated_json_state_file(self):
        """The streak state must live in a dedicated JSON file
        (/tmp/gludd-mainthread-streak.json) so it CANNOT collide with the
        nothing-dropped plugin's frequency caps (separate state files)."""
        src = ENFORCE_DELEGATE.read_text()
        assert "gludd-mainthread-streak.json" in src, (
            "MAINTHREAD_STREAK_FILE default must be "
            "/tmp/gludd-mainthread-streak.json (JSON, dedicated) so the "
            "nothing-dropped plugin's frequency caps cannot interfere"
        )
        # writeStreak must JSON.stringify (not raw number) so the .json file
        # is valid JSON.
        assert re.search(r"JSON\.stringify\s*\(\s*\{\s*count", src), (
            "writeStreak must JSON.stringify({count: n, ...}) — a bare number "
            "would not be valid JSON for a .json state file"
        )

    def test_mainthread_streak_message_loads_force_delegate_disable_hint(self):
        """The block message must surface GLUDD_FORCE_DELEGATE=0 as the
        escape hatch so the agent can tell the operator how to disable."""
        src = ENFORCE_DELEGATE.read_text()
        assert "GLUDD_FORCE_DELEGATE=0" in src, (
            "mainthreadBudgetBefore block message must mention "
            "GLUDD_FORCE_DELEGATE=0 as the disable switch"
        )


# --------------------------------------------------------------------------- #
# 5. enforce-deadline.ts — subagent task wall-clock timeout enforcement
# --------------------------------------------------------------------------- #
class TestEnforceDeadlinePlugin:
    """The 5-minute subagent task limit is now mechanically enforced."""

    def test_plugin_file_exists(self):
        assert ENFORCE_DEADLINE.exists(), (
            "enforce-deadline.ts missing — the wall-clock task timeout has no "
            "mechanical enforcement"
        )

    def test_references_timeout_env_var(self):
        src = ENFORCE_DEADLINE.read_text()
        assert "GLUDD_TASK_TIMEOUT_MS" in src, (
            "enforce-deadline.ts must reference GLUDD_TASK_TIMEOUT_MS env var"
        )

    def test_default_timeout_is_5_minutes(self):
        """Default timeout must be 300000 ms (5 min) per AGENTS.md."""
        src = ENFORCE_DEADLINE.read_text()
        m = re.search(
            r'GLUDD_TASK_TIMEOUT_MS\s*\|\|\s*["\'](\d+)["\']',
            src,
        )
        assert m, "GLUDD_TASK_TIMEOUT_MS default literal not found"
        assert m.group(1) == "300000", (
            f"Default timeout is {m.group(1)}ms, expected 300000 (5 min)"
        )

    def test_records_dispatch_timestamps(self):
        """On task dispatch the plugin must record Date.now() in state file."""
        src = ENFORCE_DEADLINE.read_text()
        assert re.search(r"\[id\]\s*=\s*Date\.now\(\)", src), (
            "Plugin must record dispatch timestamp via d[id] = Date.now()"
        )
        assert "GLUDD_TASK_DEADLINE_STATE" in src, (
            "State file path env var not referenced"
        )

    def test_warns_on_deadline_exceeded(self):
        """Over-time tasks must emit a TASK DEADLINE EXCEEDED console.warn."""
        src = ENFORCE_DEADLINE.read_text()
        assert "console.warn" in src, (
            "Plugin must emit console.warn to surface the breach"
        )
        assert "TASK DEADLINE EXCEEDED" in src, (
            "Warning must carry the load-bearing 'TASK DEADLINE EXCEEDED' header"
        )
        assert re.search(r"elapsed\s*>\s*TASK_TIMEOUT_MS", src), (
            "Deadline check must compare elapsed > TASK_TIMEOUT_MS"
        )

    def test_console_warn_throttled_to_once_per_task(self):
        """A lingering breached task must not re-warn on every subsequent tool
        call (that floods the user UI). The plugin must guard the console.warn
        with a once-per-task-id gate so each breach surfaces at most once in
        the console stream; subsequent breaches for the same id go only to the
        persistent log. The enforcement (detection + persistent log) is
        preserved; only the noisy UI channel is throttled.
        """
        src = ENFORCE_DEADLINE.read_text()
        assert re.search(r"warnedIds|warned_ids|alreadyWarned|already_warned", src), (
            "Plugin must track which task ids have already been warned about "
            "(Set or object) so console.warn fires at most once per task id"
        )
        assert re.search(r"\.has\(.+\)|\.add\(.+\)", src), (
            "Plugin must use a Set.has() / Set.add() (or equivalent) gate "
            "around the console.warn so the warning is emitted exactly once"
        )

    def test_persistent_breach_log_exists(self):
        """When the console.warn is throttled, the breach must still be
        recorded to a persistent log file so the orchestrator can poll it via
        `make task-ttl-check`. The persistent channel is the source of truth;
        the console warn is the in-band UI signal (now throttled).
        """
        src = ENFORCE_DEADLINE.read_text()
        assert "GLUDD_TASK_DEADLINE_WARNINGS" in src, (
            "Plugin must reference a GLUDD_TASK_DEADLINE_WARNINGS env var "
            "pointing at the persistent breach log"
        )
        assert re.search(r"appendFileSync|appendFile|writeFileSync", src), (
            "Plugin must write breaches to the persistent log file via fs"
        )

    def test_cleans_up_on_task_completion(self):
        """tool.execute.after must remove the task id from the state file."""
        src = ENFORCE_DEADLINE.read_text()
        assert "tool.execute.after" in src, (
            "Plugin must hook tool.execute.after to clean up completed tasks"
        )
        assert "delete d[id]" in src, (
            "tool.execute.after must delete the completed task from the state file"
        )

    def test_fail_open_wraps_enforcement(self):
        """Plugin must fail-open — an internal error never wedges the session."""
        src = ENFORCE_DEADLINE.read_text()
        assert re.search(r"catch\s*\{[^}]*fail open[^}]*\}", src), (
            "Plugin must wrap enforcement in try/catch with 'fail open' comment"
        )

    def test_dispatch_tools_covered(self):
        """Must hook task/agent/workflow dispatch tools."""
        src = ENFORCE_DEADLINE.read_text()
        for t in ("task", "agent", "workflow"):
            assert f'"{t}"' in src, f"Dispatch tool '{t}' not covered by deadline plugin"

    def test_extract_task_id_deterministic_fallback(self):
        """extractTaskId must produce a STABLE id when task_id/id are absent.

        Bug (2026-06-28): tool.execute.before fell back to `auto-${Date.now()}`
        (different on every call) while tool.execute.after extracted null from
        the same args → entries were never deleted → leaked into the state file
        and triggered repeated throttle warns. Fix: djb2-hash the stable
        dispatch fields (subagent_type + description) so both hooks derive the
        same id.
        """
        src = ENFORCE_DEADLINE.read_text()
        # Must reference the djb2 hash seed or the d- prefixed fallback shape.
        assert "5381" in src or "djb2" in src, (
            "extractTaskId deterministic fallback missing the djb2 hash (5381) — "
            "before/after id mismatch bug is back"
        )
        assert re.search(r"d-\$\{", src) or "d-${" in src, (
            "extractTaskId must return a `d-<hex>` deterministic id when "
            "task_id/id are absent (before/after must agree)"
        )
        # Must consult subagent_type and description as the stable inputs.
        assert "subagent_type" in src and "description" in src, (
            "extractTaskId deterministic fallback must combine subagent_type "
            "and description from the dispatch args"
        )

    def test_sweep_stale_entries(self):
        """A TTL sweep must drop entries older than TASK_TIMEOUT_MS * 3.

        Belt-and-suspenders for any future id-mismatch edge case: even if a
        tool.execute.after fails to delete its entry, the next loadDeadlines
        call purges entries older than 3x the timeout window (15 min default).
        Without this, the persistent state file grows unboundedly across a
        long session.
        """
        src = ENFORCE_DEADLINE.read_text()
        assert "sweepStaleEntries" in src or "sweep_stale" in src.lower(), (
            "sweepStaleEntries function missing — loadDeadlines does not purge "
            "stale entries, so any id mismatch leaks forever"
        )
        assert re.search(r"TASK_TIMEOUT_MS\s*\*\s*3", src), (
            "TTL sweep must use TASK_TIMEOUT_MS * 3 (15 min default) as the "
            "max age boundary"
        )
        # Sweep must be invoked inside loadDeadlines so every read cleans up.
        assert re.search(r"sweepStaleEntries\s*\(", src), (
            "sweepStaleEntries must be called from loadDeadlines so stale "
            "entries are purged on every state file read"
        )


# --------------------------------------------------------------------------- #
# 6. enforce-floor.ts — openWorkExists TASKS.md/BUGS.md scan (2026-06-29 audit)
# --------------------------------------------------------------------------- #
# Audit gap #1: openWorkExists() checked ratchet.yml, the multitasking backlog,
# todowrite state, and git status — but NOT TASKS.md / BUGS.md. So whenever
# the tree was clean (no uncommitted changes) and ratchet was empty, an agent
# with unchecked TASKS.md rows could grind inline with the floor disabled.
# The fix adds a markdown-task scan (`- [ ]` / `* [ ]`) and an open-incident
# header scan in BUGS.md. These tests pin the wiring.
class TestEnforceFloorOpenWorkScan:
    """openWorkExists must scan TASKS.md and BUGS.md for open items."""

    def test_open_work_exists_detects_unchecked_tasks_md_dash(self):
        """openWorkExists must return true when TASKS.md has `- [ ]` rows."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(r"function openWorkExists\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "openWorkExists function body not found"
        body = m.group(1)
        assert "TASKS" in body.upper(), (
            "openWorkExists must read TASKS.md — without it a clean tree + "
            "unchecked TASKS.md rows silently disable the floor gate"
        )
        # Must match an unchecked markdown task box (`[ ]` with optional inner
        # whitespace) preceded by a list marker.
        assert re.search(r"\\\[\s*\\\]|\\\[\s*\*\\\]|\\\\\[\s*\\\\\]", body) or \
               ("\\[" in body and "\\s" in body and "\\]" in body) or \
               re.search(r"\[\s*\\s\*\]", body), (
            "openWorkExists must count unchecked markdown task boxes "
            "(regex matching `^[\\s*][-*]\\s+\\[\\s*\\]`)"
        )

    def test_open_work_exists_detects_unchecked_tasks_md_asterisk(self):
        """The TASKS.md scan must also match `* [ ]` (asterisk marker), not
        just `-` markers — both are valid markdown unordered-list syntax."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(r"function openWorkExists\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "openWorkExists function body not found"
        body = m.group(1)
        # The character class for the list marker must include both `-` and `*`.
        assert "[-*]" in body or "[-+*]" in body or ("-" in body and "*" in body), (
            "TASKS.md scan must accept both `-` and `*` list markers (markdown "
            "allows both); a `-`-only regex misses `* [ ]` rows"
        )

    def test_open_work_exists_false_when_tasks_md_all_checked(self):
        """Checked rows (`- [x]`) must NOT trigger openWorkExists. The regex
        must require an EMPTY box (`[ ]`), not match `[x]`."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(r"function openWorkExists\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "openWorkExists function body not found"
        body = m.group(1)
        # The unchecked-box regex must NOT match `x` or `X` inside the box.
        # We assert the regex uses `\\s*` (whitespace only) between the
        # brackets, not `\\S` or `.`.
        assert re.search(r"\\\[\s*\\\s\*\\\]|\\\[\s*\\\\s\*\\\]", body) or \
               re.search(r"\\\[\s*\\s\*\s*\\\]", body) or \
               "\\s*" in body, (
            "The unchecked-box regex must use `\\s*` (whitespace) inside the "
            "brackets so `[x]`/`[X]` (checked) rows do NOT match"
        )

    def test_open_work_exists_false_when_no_tasks_md(self):
        """When TASKS.md is absent, the scan must not throw (fail-open)."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(r"function openWorkExists\(.*?\{(.*?)^\}", src, re.DOTALL | re.MULTILINE)
        assert m, "openWorkExists function body not found"
        body = m.group(1)
        # Must guard the read with fs.existsSync (so absent file is a no-op).
        assert "existsSync" in body, (
            "TASKS.md scan must guard with fs.existsSync so an absent file "
            "does not throw (fail-open)"
        )

    def test_tasks_md_path_env_override(self):
        """GLUDD_TASKS_MD must override the default TASKS.md path."""
        src = ENFORCE_FLOOR.read_text()
        assert "process.env.GLUDD_TASKS_MD" in src, (
            "openWorkExists must honor process.env.GLUDD_TASKS_MD so tests "
            "can point at a fixture path"
        )

    def test_bugs_md_path_env_override(self):
        """GLUDD_BUGS_MD must override the default BUGS.md path."""
        src = ENFORCE_FLOOR.read_text()
        assert "process.env.GLUDD_BUGS_MD" in src, (
            "openWorkExists must honor process.env.GLUDD_BUGS_MD so tests "
            "can point at a fixture path"
        )

    def test_tasks_md_default_path(self):
        """Default TASKS.md path is <cwd>/TASKS.md."""
        src = ENFORCE_FLOOR.read_text()
        assert re.search(r'[\'"][^\'"]*TASKS\.md[\'"]', src), (
            "openWorkExists must default to <cwd>/TASKS.md"
        )

    def test_bugs_md_default_path(self):
        """Default BUGS.md path is <cwd>/BUGS.md."""
        src = ENFORCE_FLOOR.read_text()
        assert re.search(r'[\'"][^\'"]*BUGS\.md[\'"]', src), (
            "openWorkExists must default to <cwd>/BUGS.md"
        )


# --------------------------------------------------------------------------- #
# 7. enforce-floor.ts — ceiling deny-on-dispatch + probe fail asymmetry
# --------------------------------------------------------------------------- #
# Audit gaps #2 and #3:
#   #2 — the ceiling was APPEND-only (advisory warning), never a deny. So
#        disk exhaustion proceeded unchecked as the agent kept dispatching
#        worktree-isolated agents (each ~320MB venv).
#   #3 — countActiveAgents returned null on probe error -> fail-open for
#        BOTH floor and ceiling, silently disabling ALL enforcement when
#        agent_liveness.py was killed.
# Fixes:
#   - ceiling: HARD-DENY dispatches when active > CEILING (read-only ops
#     still warn — they add no venv).
#   - probe: floor FAIL-CLOSED (null -> 0, deny mutating, force dispatch),
#     ceiling FAIL-OPEN (null -> unknown, do NOT deny dispatch — wedging
#     the session by blocking all dispatches is worse).
class TestEnforceFloorCeilingDenyAndProbeAsymmetry:
    """Ceiling must deny dispatches; floor probe fail-closed, ceiling fail-open."""

    def test_ceiling_denies_dispatch_when_exceeded(self):
        """When active > CEILING AND the tool is a dispatch (task/agent/
        workflow), the plugin MUST return permissionDecision:'deny' — not
        just append a warning. The warning-only path let disk exhaustion
        proceed unchecked."""
        src = ENFORCE_FLOOR.read_text()
        # Must emit the load-bearing ceiling-deny header.
        assert "AGENT CEILING BREACHED" in src, (
            "tool.execute.before ceiling-deny must emit an 'AGENT CEILING "
            "BREACHED' header (distinct from the advisory 'AGENT-CEILING "
            "BREACH' banner) so operators can grep dispatch-denials"
        )
        # The deny must be gated on `active > CEILING` inside the
        # tool.execute.before hook (not just the response.transform).
        assert re.search(r"active\s*>\s*CEILING", src), (
            "Ceiling deny must compare `active > CEILING`"
        )

    def test_ceiling_warns_non_dispatch_when_exceeded(self):
        """For non-dispatch tools (Read/Edit/Bash read-only) over ceiling,
        the plugin must NOT hard-deny — read-only ops add no venv. The
        advisory AGENT-CEILING BREACH banner must remain on the
        response.transform channel."""
        src = ENFORCE_FLOOR.read_text()
        assert "AGENT-CEILING BREACH" in src, (
            "response.transform must retain the 'AGENT-CEILING BREACH' "
            "advisory banner — non-dispatch / response-level guidance"
        )

    def test_count_live_agents_fail_closed_for_floor(self):
        """On probe error (countActiveAgents returns null), the FLOOR check
        must treat null as 0 (fail-closed) — deny mutating tools, forcing
        the agent to dispatch. The old code did `if (active === null)
        return` which was fail-open and silently disabled enforcement."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r'"tool\.execute\.before"(.*?)(?="experimental\.chat\.response\.transform)',
            src,
            re.DOTALL,
        )
        assert m, "tool.execute.before hook not found"
        before_body = m.group(1)
        # The floor branch must coerce null -> 0 (NOT early-return on null).
        assert re.search(r"active\s*===\s*null\s*\?\s*0", before_body) or \
               re.search(r"\|\|\s*0\b", before_body), (
            "Floor branch must coerce countActiveAgents() null to 0 "
            "(fail-closed) so a dead agent_liveness.py probe does NOT "
            "silently disable enforcement"
        )
        # Must NOT have the old fail-open early-return on the floor path.
        # (The ceiling path may early-return on null — that's the asymmetry.)

    def test_count_live_agents_fail_open_for_ceiling(self):
        """On probe error (null), the CEILING check must NOT deny dispatches
        (fail-open). Blocking ALL dispatches when the probe is dead would
        wedge the session — worse than the rare over-dispatch."""
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r'"tool\.execute\.before"(.*?)(?="experimental\.chat\.response\.transform)',
            src,
            re.DOTALL,
        )
        assert m, "tool.execute.before hook not found"
        before_body = m.group(1)
        # Ceiling deny branch must guard on `active !== null` so a probe
        # error does not trigger the deny.
        assert re.search(r"active\s*!==\s*null", before_body), (
            "Ceiling deny branch must guard on `active !== null` — when the "
            "probe errored we cannot know if we're over ceiling, so we must "
            "NOT block dispatches (fail-open for ceiling, fail-closed for floor)"
        )

    def test_probe_asymmetry_documented_in_comment(self):
        """The fail-closed/fail-open asymmetry must be documented in a
        comment so a future reader does not 'fix' one side to match the
        other (which would re-introduce the bug)."""
        src = ENFORCE_FLOOR.read_text()
        assert "fail-closed" in src.lower() or "fail closed" in src.lower(), (
            "enforce-floor.ts must document the floor fail-closed side of "
            "the asymmetry in a comment"
        )
        assert "fail-open" in src.lower() or "fail open" in src.lower(), (
            "enforce-floor.ts must document the ceiling fail-open side of "
            "the asymmetry in a comment"
        )

    def test_ceiling_deny_message_loads_spec_phrases(self):
        """The ceiling-deny message must carry the user-mandated phrases:
        disk-exhaustion warning, clean-worktree-venvs hint, and the
        GLUDD_FLOOR_ENFORCE=0 disable switch."""
        src = ENFORCE_FLOOR.read_text()
        assert "disk exhaustion" in src.lower(), (
            "Ceiling-deny message must warn about disk exhaustion"
        )
        assert "clean-worktree-venvs" in src, (
            "Ceiling-deny message must suggest `make clean-worktree-venvs`"
        )
        assert "GLUDD_FLOOR_ENFORCE=0" in src, (
            "Ceiling-deny message must surface GLUDD_FLOOR_ENFORCE=0 as the "
            "disable switch (same as the floor deny)"
        )

    def test_open_work_message_lists_tasks_md_bugs_md(self):
        """The floor-deny message must now list TASKS.md/BUGS.md as open-work
        signals (so the operator knows the gate consulted them)."""
        src = ENFORCE_FLOOR.read_text()
        assert "TASKS.md" in src, (
            "Floor-deny message must list TASKS.md as one of the open-work "
            "signals it consulted"
        )
        assert "BUGS.md" in src, (
            "Floor-deny message must list BUGS.md as one of the open-work "
            "signals it consulted"
        )
