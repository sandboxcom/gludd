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
        assert count >= 20, (
            f"NO_WAIT_PATTERNS has only {count} entries — expected >= 20 "
            "(permission-seek + constraint-as-stopsign + status-report-as-handoff). "
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

    def test_state_check_blocks_when_ratchet_has_entries(self):
        """The STATE-BASED STOP BLOCKED message must be present and reference
        the ratchet entry count."""
        src = ENFORCE_STOP.read_text()
        assert "STATE-BASED STOP BLOCKED" in src, (
            "The state-based block response is missing its "
            "'STATE-BASED STOP BLOCKED' header"
        )
        assert "ratchetEntries.length" in src, (
            "The state-based block must report the ratchet entry count"
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
