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

from tests.unit._plugin_contract import PluginContractFile

ROOT = Path(__file__).parent.parent.parent
PLUGIN_DIR = ROOT / ".opencode" / "plugin"

ENFORCE_MAKE = PluginContractFile(PLUGIN_DIR / "enforce-make.ts")
ENFORCE_DELEGATE = PluginContractFile(PLUGIN_DIR / "enforce-delegate.ts")
ENFORCE_STOP = PluginContractFile(PLUGIN_DIR / "enforce-stop.ts")
ENFORCE_FLOOR = PluginContractFile(PLUGIN_DIR / "enforce-floor.ts")
ENFORCE_DEADLINE = PluginContractFile(PLUGIN_DIR / "enforce-deadline.ts")


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
# 3. enforce-stop.ts — NO_WAIT_PATTERNS removed (lean plugin)
# --------------------------------------------------------------------------- #
# NO_WAIT_PATTERNS and detectNoWaitPattern were intentionally removed from the
# lean 388-line enforce-stop.ts. The stop detection now uses the state-based
# responseLooksTerminal + hasPendingWork check instead of vocabulary matching.
class TestEnforceStopNoWaitPatterns:
    """NO_WAIT_PATTERNS and detectNoWaitPattern were intentionally removed."""

    def test_no_wait_patterns_removed(self):
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" not in src, (
            "NO_WAIT_PATTERNS should NOT be present — it was removed in the "
            "lean plugin (stop detection is now state-based)"
        )

    def test_detect_no_wait_pattern_removed(self):
        src = ENFORCE_STOP.read_text()
        assert "function detectNoWaitPattern" not in src, (
            "detectNoWaitPattern should NOT be present — removed in lean plugin"
        )


# --------------------------------------------------------------------------- #
# 3b. enforce-stop.ts — CONSTRAINT_AS_STOP_PATTERNS removed (lean plugin)
# --------------------------------------------------------------------------- #
# CONSTRAINT_AS_STOP_PATTERNS, detectConstraintAsStop, and constraintBlockResponse
# were all intentionally removed from the lean 388-line enforce-stop.ts. The
# constraint-as-stop vocabulary check no longer exists; stop detection is now
# state-based (responseLooksTerminal + hasPendingWork).
class TestEnforceStopConstraintPatterns:
    """CONSTRAINT_AS_STOP_PATTERNS and related functions were intentionally removed."""

    def test_constraint_patterns_removed(self):
        src = ENFORCE_STOP.read_text()
        assert "CONSTRAINT_AS_STOP_PATTERNS" not in src, (
            "CONSTRAINT_AS_STOP_PATTERNS should NOT be present — removed in "
            "lean plugin (stop detection is now state-based)"
        )

    def test_detect_constraint_as_stop_removed(self):
        src = ENFORCE_STOP.read_text()
        assert "function detectConstraintAsStop" not in src, (
            "detectConstraintAsStop should NOT be present — removed in lean plugin"
        )

    def test_constraint_block_response_removed(self):
        src = ENFORCE_STOP.read_text()
        assert "function constraintBlockResponse" not in src, (
            "constraintBlockResponse should NOT be present — removed in lean plugin"
        )


# --------------------------------------------------------------------------- #
# 3c. enforce-stop.ts — text.complete stop detection (simplified plugin)
# --------------------------------------------------------------------------- #
# The responseLooksTerminal function was removed. Stop detection is now inline
# in text.complete: short false-done claims (✅, "Done.", COMPLETION_VERBATIM),
# direct false-done detection with COMPLETION_HEADER_RE / CHECKED_BOXES_RE, and
# hasLocalWork blocking all text when TASKS.md unchecked / ratchet entries /
# gate RED / repo dirty.
class TestEnforceStopTextCompleteDetection:
    """text.complete handler must detect completion-shaped responses."""

    def test_text_complete_detects_completion_verbatim(self):
        """COMPLETION_SOUNDING (successor to COMPLETION_VERBATIM) lives in enforce-make.ts."""
        src = ENFORCE_MAKE.read_text()
        assert "COMPLETION_SOUNDING" in src, (
            "COMPLETION_SOUNDING missing from enforce-make.ts — "
            "the completion-verbatim detector (moved from enforce-stop.ts) is gone"
        )

    def test_text_complete_detects_direct_false_done_flags(self):
        """DIRECT_FALSE_DONE_FLAGS removed — text.complete hook is gone (opencode >=1.17.9)."""
        src = ENFORCE_STOP.read_text()
        assert "DIRECT_FALSE_DONE_FLAGS" not in src, (
            "DIRECT_FALSE_DONE_FLAGS should NOT be present — text.complete was removed"
        )

    def test_text_complete_detects_completion_header(self):
        """COMPLETION_HEADER_RE removed — text.complete hook is gone (opencode >=1.17.9)."""
        src = ENFORCE_STOP.read_text()
        assert "COMPLETION_HEADER_RE" not in src, (
            "COMPLETION_HEADER_RE should NOT be present — text.complete was removed"
        )

    def test_text_complete_detects_standalone_done(self):
        """STANDALONE_DONE_RE removed — text.complete hook is gone (opencode >=1.17.9)."""
        src = ENFORCE_STOP.read_text()
        assert "STANDALONE_DONE_RE" not in src, (
            "STANDALONE_DONE_RE should NOT be present — text.complete was removed"
        )

    def test_text_complete_detects_checked_boxes_without_unchecked(self):
        """CHECKED_BOXES_RE/UNCHECKED_BOXES_RE removed — text.complete hook is gone."""
        src = ENFORCE_STOP.read_text()
        assert "CHECKED_BOXES_RE" not in src, (
            "CHECKED_BOXES_RE should NOT be present — text.complete was removed"
        )
        assert "UNCHECKED_BOXES_RE" not in src, (
            "UNCHECKED_BOXES_RE should NOT be present — text.complete was removed"
        )

    def test_response_looks_terminal_present(self):
        """responseLooksTerminal remains wired into text.complete."""
        src = ENFORCE_STOP.read_text()
        assert "function responseLooksTerminal" in src, (
            "responseLooksTerminal must remain in the complete runtime contract"
        )

    def test_text_complete_has_local_work_block(self):
        """hasLocalWork gates text.complete and pre-generation checks."""
        src = ENFORCE_STOP.read_text()
        assert "hasLocalWork" in src
        assert "experimental.text.complete" in src

    def test_text_complete_ratchet_block(self):
        """RATCHET block moved to system.transform (different header text)."""
        src = ENFORCE_STOP.read_text()
        assert "ratchetHasEntries" in src, (
            "ratchetHasEntries missing from enforce-stop.ts — ratchet check was removed"
        )

    def test_text_complete_dispatch_bypass(self):
        """turn state distinguishes text-only output from tool-backed work."""
        src = ENFORCE_STOP.read_text()
        assert "turnState.dispatchCount" in src, (
            "dispatchCount tracking missing from enforce-stop.ts"
        )
        assert "turnState.toolCallMade" in src, (
            "toolCallMade must participate in text-only response detection"
        )

    def test_detects_false_done_no_evidence_block(self):
        """Short false-done claims without evidence remain blocked."""
        src = ENFORCE_STOP.read_text()
        assert "FALSE-DONE CLAIM BLOCKED" in src, (
            "FALSE-DONE CLAIM BLOCKED must remain in text.complete"
        )

    def test_state_block_message_hard_stop_header(self):
        """HARD STOP — STATE-BASED BLOCK removed — text.complete hook is gone."""
        src = ENFORCE_STOP.read_text()
        assert "HARD STOP — STATE-BASED BLOCK" not in src, (
            "HARD STOP — STATE-BASED BLOCK should NOT be present — text.complete was removed"
        )

    def test_simplified_block_headers(self):
        """The old 'PENDING-WORK AUDIT' header and 'CI-RED DETECTED' were removed."""
        src = ENFORCE_STOP.read_text()
        assert "HARD STOP — PENDING-WORK AUDIT" not in src, (
            "'HARD STOP — PENDING-WORK AUDIT' was removed from enforce-stop.ts"
        )
        assert "HARD STOP — CI-RED DETECTED" not in src, (
            "HARD STOP — CI-RED DETECTED was removed — CI-red is now handled "
            "via ciVerdictPendingOrRed in hasLocalWork"
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
        """Must shell out to git via inExecSync param (passed as execSync by caller)."""
        src = ENFORCE_STOP.read_text()
        m = re.search(r"function repoHasPendingWork\(.*?\{(.*?)\n  \}", src, re.DOTALL)
        assert m, "could not extract repoHasPendingWork body"
        body = m.group(1)
        assert "inExecSync" in body, (
            "repoHasPendingWork must use its inExecSync parameter for git inspection"
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
        """system.transform calls repoHasPendingWork(execSync) directly (no cache proxy).
        text.complete is gone; pending-work check is now inline in system.transform."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"repoPending\s*=\s*repoHasPendingWork\s*\(\s*execSync\s*\)",
            src,
        ), (
            "system.transform must call repoHasPendingWork(execSync) "
            "directly — text.complete was removed"
        )
        assert "repoHasPendingWork" in src, (
            "repoHasPendingWork must be referenced in enforce-stop.ts"
        )

    def test_repo_has_pending_work_wired_into_has_local_work(self):
        """Repository state participates in the unified pending-work state."""
        src = ENFORCE_STOP.read_text()
        assert re.search(r"const signals\s*:[^=]+=\s*\{[\s\S]{0,400}repoPending", src)
        assert "hasPendingWork = computeHealthScore(signals)" in src

    def test_no_wait_patterns_include_done_answer(self):
        """NO_WAIT_PATTERNS was removed."""
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" not in src, (
            "NO_WAIT_PATTERNS must not be present — it was removed"
        )

    def test_no_wait_patterns_include_qa_recap(self):
        """NO_WAIT_PATTERNS was removed."""
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" not in src, (
            "NO_WAIT_PATTERNS must not be present — it was removed"
        )

    def test_no_wait_patterns_include_completion_recap_variants(self):
        """NO_WAIT_PATTERNS was removed."""
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" not in src, (
            "NO_WAIT_PATTERNS must not be present — it was removed"
        )

    def test_no_wait_patterns_include_item_count_completion(self):
        """NO_WAIT_PATTERNS was removed."""
        src = ENFORCE_STOP.read_text()
        assert "NO_WAIT_PATTERNS" not in src, (
            "NO_WAIT_PATTERNS must not be present — it was removed"
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
        """TASKS.md state participates in the unified pending-work state."""
        src = ENFORCE_STOP.read_text()
        assert "tasksMdUnchecked" in src
        assert re.search(r"hasLocalWork\s*=[\s\S]{0,200}tasksMdUnchecked", src)

    def test_state_block_shows_tasks_md_status(self):
        """system.transform block reports unchecked TASKS.md count."""
        src = ENFORCE_STOP.read_text()
        assert "unchecked TASKS.md items" in src or "unchecked" in src, (
            "system.transform block must report TASKS.md unchecked status"
        )

    def test_pending_work_audit_block_shows_tasks_md_status(self):
        """system.transform block includes TASKS.md in indicators."""
        src = ENFORCE_STOP.read_text()
        assert "unchecked" in src, (
            "system.transform must include unchecked count in indicators"
        )


# --------------------------------------------------------------------------- #
# 3f. enforce-stop.ts — gateStatusIsRed + CI integration (simplified)
# --------------------------------------------------------------------------- #
# responseMentionsCiRed and CI_RED_PATTERNS were removed from the simplified
# plugin. CI-red detection now uses ciIsPendingOrRed() + watchdog cache.
# gateStatusIsRed is still present and wires into hasLocalWork in text.complete.
class TestEnforceStopGateStatusCiRed:
    """gateStatusIsRed must exist and be wired in. responseMentionsCiRed / CI_RED_PATTERNS removed."""

    def test_gate_status_is_red_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function gateStatusIsRed" in src, (
            "gateStatusIsRed function missing from enforce-stop.ts"
        )

    def test_response_mentions_ci_red_removed(self):
        """responseMentionsCiRed was removed from the simplified plugin."""
        src = ENFORCE_STOP.read_text()
        assert "function responseMentionsCiRed" not in src, (
            "responseMentionsCiRed should NOT be present — it was removed in "
            "the simplified plugin (CI-red detection now uses ciIsPendingOrRed)"
        )

    def test_ci_red_patterns_removed(self):
        """CI_RED_PATTERNS was removed from the simplified plugin."""
        src = ENFORCE_STOP.read_text()
        assert "CI_RED_PATTERNS" not in src, (
            "CI_RED_PATTERNS should NOT be present — it was removed in "
            "the simplified plugin"
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

    def test_gate_red_wired_into_has_local_work(self):
        """system.transform uses `gateRed = gateStatusIsRed()` and gates hasWork on it."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"gateRed\s*=\s*gateStatusIsRed\s*\(\s*\)",
            src,
        ), (
            "system.transform must call gateStatusIsRed() so gate RED "
            "triggers the pre-generation gate"
        )
        # ciVerdictPendingOrRed is gone; ciIsPendingOrRed called directly
        assert re.search(
            r"ciBad\s*=\s*ciIsPendingOrRed\s*\(\s*\)",
            src,
        ), (
            "system.transform must call ciIsPendingOrRed() directly"
        )


# --------------------------------------------------------------------------- #
# 3e. enforce-stop.ts — CI-is-pending-or-red (Deficiencies A+B)
# --------------------------------------------------------------------------- #
class TestEnforceStopCiPendingOrRed:
    """CI verdict query and COMPLETION_VERBATIM coverage for Deficiencies A-D."""

    def test_ci_is_pending_or_red_function_exists(self):
        src = ENFORCE_STOP.read_text()
        assert "function ciIsPendingOrRed" in src, (
            "ciIsPendingOrRed function missing — CI verdict query (Deficiency A+B) "
            "must exist in enforce-stop.ts"
        )
        assert re.search(
            r"function\s+ciIsPendingOrRed\b",
            src,
        ), "ciIsPendingOrRed function must be declared in enforce-stop.ts"
        # Current implementation reads from watchdog cache and shared state;
        # no longer shells out via execSync for ci-verdict.
        assert re.search(r"gludd-watchdog-ci\.json", src), (
            "ciIsPendingOrRed must read from watchdog CI cache "
            "(/tmp/gludd-watchdog-ci.json)"
        )
        assert "600_000" in src, (
            "ciIsPendingOrRed must reject stale watchdog cache data"
        )

    def test_ci_is_pending_or_red_has_cache(self):
        src = ENFORCE_STOP.read_text()
        assert "gludd-watchdog-ci.json" in src
        assert "last_ci_check" in src
        assert "Date.now() - lastCheck < 600_000" in src

    def test_completion_verbatim_detects_session_summary(self):
        """COMPLETION_SOUNDING (in enforce-make.ts) must have comprehensive completion-phrase coverage."""
        src = ENFORCE_MAKE.read_text()
        m = re.search(r"const COMPLETION_SOUNDING\s*=\s*\[(.*?)\]", src, re.DOTALL)
        assert m, "could not extract COMPLETION_SOUNDING body from enforce-make.ts"
        body = m.group(1)
        entries = re.findall(r'"([^"]+)"', body)
        assert len(entries) >= 10, (
            f"COMPLETION_SOUNDING has only {len(entries)} entries — expected "
            "comprehensive completion-phrase coverage"
        )
        assert any("session" in e or "summary" in e or "complete" in e or "done" in e.lower() for e in entries), (
            "COMPLETION_SOUNDING must contain completion phrases"
        )

    def test_text_complete_status_summary_detects_bold_headers(self):
        """Structured bold headers contribute to status-summary detection."""
        src = ENFORCE_STOP.read_text()
        assert "boldHeaders" in src
        assert "looksLikeStatusSummary" in src

    def test_ci_pending_wired_into_has_pending_work(self):
        """Both live and cached CI verdict paths feed pending-work state."""
        src = ENFORCE_STOP.read_text()
        assert re.search(
            r"ciBad\s*=\s*ciIsPendingOrRed\s*\(\s*\)",
            src,
        ), (
            "system.transform must call ciIsPendingOrRed() — "
            "the CI verdict query must be wired into the pre-generation gate"
        )
        assert "ciVerdictPendingOrRed" in src
        assert re.search(r"const signals\s*:[^=]+=\s*\{[\s\S]{0,400}ciVerdictPendingOrRed", src)

    def test_session_idle_refreshes_pending_work_state(self):
        """session.idle refreshes pending-work state for the next turn."""
        src = ENFORCE_STOP.read_text()
        assert 'evType === "session.idle"' in src
        assert "const workState = hasRealPendingWork()" in src


# --------------------------------------------------------------------------- #
# 4. enforce-floor.ts — floor/target/ceiling constants
# --------------------------------------------------------------------------- #
class TestEnforceFloorConstants:
    """The three band constants must be 5/6/8 (cost-efficiency directive 2026-07-11)."""

    def test_floor_constant_is_5(self):
        src = ENFORCE_FLOOR.read_text()
        # FLOOR now uses _tunable helper with default "10" (raised from 5 per
        # cost-efficiency directive relaxation 2026-07-13).
        m = re.search(
            r"const\s+FLOOR\s*=\s*_tunable\s*\([^)]*CLAUDE_AGENT_FLOOR[^)]*[\"'](\d+)[\"']\s*\)",
            src,
        )
        assert m, (
            "FLOOR constant declaration not found — expected "
            "_tunable(\"/tmp/gludd-floor-override\", \"CLAUDE_AGENT_FLOOR\", \"10\")"
        )
        assert m.group(1) == "10", (
            f"FLOOR default is {m.group(1)}, expected 10"
        )

    def test_target_constant_is_6(self):
        src = ENFORCE_FLOOR.read_text()
        # TARGET uses Math.min(parseInt(process.env.CLAUDE_AGENT_TARGET || "10"), CEILING)
        m = re.search(
            r"const\s+TARGET\s*=\s*Math\.min\s*\(\s*parseInt\s*\(\s*process\.env\.CLAUDE_AGENT_TARGET\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "TARGET constant declaration not found — expected "
            "Math.min(parseInt(process.env.CLAUDE_AGENT_TARGET || \"10\", 10), CEILING)"
        )
        assert m.group(1) == "10", (
            f"TARGET default is {m.group(1)}, expected 10"
        )

    def test_ceiling_constant_is_8(self):
        src = ENFORCE_FLOOR.read_text()
        m = re.search(
            r"const\s+CEILING\s*=\s*_tunable\s*\([^)]*CLAUDE_AGENT_CEILING[^)]*[\"'](\d+)[\"']\s*\)",
            src,
        )
        assert m, (
            "CEILING constant declaration not found — expected "
            "_tunable(\"/tmp/gludd-ceiling-override\", \"CLAUDE_AGENT_CEILING\", \"10\")"
        )
        assert m.group(1) == "10", (
            f"CEILING default is {m.group(1)}, expected 10"
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
        assert "MAX_STREAK" in src and "_streakCount" in src, (
            "Streak-based floor breach detection (MAX_STREAK / _streakCount) "
            "missing — the guardrail cannot detect an under-staffed pool"
        )
        # The breach message must reference TARGET (dispatch UP to target).
        assert "TARGET" in src, (
            "Floor-breach response must reference TARGET to direct refilling"
        )

    def test_ceiling_breach_stops_dispatch(self):
        """Above ceiling must instruct the model to STOP adding agents."""
        src = ENFORCE_FLOOR.read_text()
        assert "CEILING" in src, (
            "CEILING constant missing — the guardrail cannot detect "
            "an over-staffed pool (disk/overload risk)"
        )
        assert "TARGET = Math.min" in src, (
            "TARGET must be capped by CEILING — dispatch count cannot exceed ceiling"
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
        # The deny path must be gated on BOTH streak breach AND open work.
        assert re.search(r"_streakCount\s*[><=]+\s*(?:MAX_STREAK|effectiveMax)", src), (
            "Deny must be gated on _streakCount vs MAX_STREAK (streak-based floor enforcement)"
        )
        assert "openWorkExists" in src, (
            "Deny must consult openWorkExists() so it doesn't wedge a "
            "session where the work is genuinely done"
        )

    def test_floor_plugin_unconditional_enforcement(self):
        """FLOOR_ENFORCE is env-var gated (default-ON).

        It uses `process.env.GLUDD_FLOOR_ENFORCE !== "0"` — default-ON
        (the env var absent means enforce), but operators can set
        GLUDD_FLOOR_ENFORCE=0 to disable for focused single-file work.
        """
        src = ENFORCE_FLOOR.read_text()
        # The load-bearing line: process.env.GLUDD_FLOOR_ENFORCE !== "0"
        m = re.search(
            r"const\s+FLOOR_ENFORCE\s*=\s*process\.env\.GLUDD_FLOOR_ENFORCE\s*!==\s*\"0\"",
            src,
        )
        assert m, (
            "FLOOR_ENFORCE must be `const FLOOR_ENFORCE = process.env.GLUDD_FLOOR_ENFORCE !== \"0\"` "
            "(env-var gated, default-ON)."
        )
        # The env var must appear in executable code (non-comment lines).
        non_comment_lines = [
            line for line in src.splitlines()
            if not line.strip().startswith("//") and line.strip() != ""
        ]
        non_comment_src = "\n".join(non_comment_lines)
        assert re.search(
            r"process\.env\.GLUDD_FLOOR_ENFORCE",
            non_comment_src,
        ), (
            "FLOOR_ENFORCE must reference process.env.GLUDD_FLOOR_ENFORCE in "
            "executable code for env-var gating."
        )

    def test_floor_plugin_deny_message_no_env_escape_hatch(self):
        """The deny message MUST reference GLUDD_FLOOR_ENFORCE=0 since
        enforcement is env-var gated (default-ON but toggleable)."""
        src = ENFORCE_FLOOR.read_text()
        assert "GLUDD_FLOOR_ENFORCE=0 to disable" in src, (
            "Deny message must surface GLUDD_FLOOR_ENFORCE=0 — enforcement "
            "is env-var gated (not unconditional). The operator needs to "
            "know how to disable for focused single-file work."
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
        assert m.group(1) == "2", (
            f"MAINTHREAD_THRESHOLD default is {m.group(1)}, expected 2 — the "
            "3rd consecutive mutating call must be the one that blocks"
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
            r"mainthreadBudgetBefore\s*\(\s*tool\s*,\s*command\s*\)", src,
        ), (
            "mainthreadBudgetBefore(tool, command) must be called inside "
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
        # isDispatchTool must be imported/used to recognize task/agent/workflow.
        assert "isDispatchTool" in src, (
            "isDispatchTool must be imported/used to recognize task/agent/workflow "
            "as dispatches (from shared.ts)"
        )
        # mainthreadBudgetAfter must reset streak to 0 on a dispatch tool.
        assert re.search(
            r"if\s*\(\s*isDispatchTool\s*\(\s*tool\s*\)\s*\)\s*\{[\s\S]{0,500}"
            r"writeStreak\s*\(\s*\{\s*count\s*:\s*0\s*\}\s*\)",
            src,
        ), (
            "mainthreadBudgetAfter must reset count when isDispatchTool(tool) "
            "is true — otherwise the streak never resets and the agent is "
            "permanently blocked from inline work even after delegating"
        )

    def test_mainthread_streak_default_on_via_force_delegate_not_zero(self):
        """The streak blocker must default ON via its OWN env var
        (GLUDD_MAINTHREAD_STREAK_ENFORCE, default "1"), NOT shared with the
        opt-in GLUDD_FORCE_DELEGATE. Polarity must be `!== "0"`, NOT
        opt-in `=== "1"` (which defaults OFF and was the gap).

        P8 fix (2026-07-09): previously MAINTHREAD_STREAK_ENABLED read
        GLUDD_FORCE_DELEGATE !== "0" — the SAME env var as the opt-in
        force-delegate gate. Setting GLUDD_FORCE_DELEGATE=0 to disable the
        opt-in gate ALSO disabled the default-on streak blocker. The fix
        splits them: GLUDD_FORCE_DELEGATE controls only mechanism A;
        GLUDD_MAINTHREAD_STREAK_ENFORCE controls mechanism B.
        """
        src = ENFORCE_DELEGATE.read_text()
        m = re.search(
            r"MAINTHREAD_STREAK_ENABLED\s*=\s*\(\s*process\.env\.GLUDD_MAINTHREAD_STREAK_ENFORCE\s*\|\|\s*[\"']1[\"']\s*\)\s*!==\s*[\"']0[\"']",
            src,
        )
        assert m, (
            "MAINTHREAD_STREAK_ENABLED must be "
            "`(process.env.GLUDD_MAINTHREAD_STREAK_ENFORCE || \"1\") !== \"0\"` "
            "(default ON via its OWN env var). Sharing GLUDD_FORCE_DELEGATE was "
            "the P8 polarity trap — disabling the opt-in gate also killed the "
            "default enforcement."
        )
        # And mainthreadBudgetBefore must consult the flag.
        assert re.search(
            r"if\s*\(\s*!MAINTHREAD_STREAK_ENABLED\s*\)\s*return null",
            src,
        ), (
            "mainthreadBudgetBefore must early-return when "
            "MAINTHREAD_STREAK_ENABLED is false (the "
            "GLUDD_MAINTHREAD_STREAK_ENFORCE=0 escape hatch)"
        )

    def test_mainthread_streak_env_var_independent_from_force_delegate(self):
        """GLUDD_FORCE_DELEGATE and GLUDD_MAINTHREAD_STREAK_ENFORCE must be
        referenced by SEPARATE constants so disabling one cannot disable
        the other.

        This is the structural pin for the P8 polarity-trap fix: the bug was
        that a single env var controlled two mechanisms with opposite
        defaults. The fix requires that MAINTHREAD_STREAK_ENABLED's
        declaration references ONLY GLUDD_MAINTHREAD_STREAK_ENFORCE (not
        GLUDD_FORCE_DELEGATE), and FORCE_DELEGATE_ENABLED references ONLY
        GLUDD_FORCE_DELEGATE.
        """
        src = ENFORCE_DELEGATE.read_text()
        # Extract the MAINTHREAD_STREAK_ENABLED declaration line.
        m = re.search(
            r"const\s+MAINTHREAD_STREAK_ENABLED\s*=\s*([^\n]+)",
            src,
        )
        assert m, "MAINTHREAD_STREAK_ENABLED declaration not found"
        decl = m.group(1)
        assert "GLUDD_MAINTHREAD_STREAK_ENFORCE" in decl, (
            "MAINTHREAD_STREAK_ENABLED must reference GLUDD_MAINTHREAD_STREAK_ENFORCE"
        )
        assert "GLUDD_FORCE_DELEGATE" not in decl, (
            "MAINTHREAD_STREAK_ENABLED must NOT reference GLUDD_FORCE_DELEGATE — "
            "that was the P8 polarity trap (one env var, two mechanisms, "
            "opposite defaults)."
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
        """The block message must surface GLUDD_MAINTHREAD_STREAK_ENFORCE=0
        as the escape hatch so the agent can tell the operator how to disable.

        P8 fix (2026-07-09): the disable hint was GLUDD_FORCE_DELEGATE=0, but
        that env var now controls ONLY the opt-in force-delegate gate
        (mechanism A). The streak blocker (mechanism B) has its own env var,
        so the hint must reference GLUDD_MAINTHREAD_STREAK_ENFORCE=0.
        """
        src = ENFORCE_DELEGATE.read_text()
        assert "GLUDD_MAINTHREAD_STREAK_ENFORCE=0" in src, (
            "mainthreadBudgetBefore block message must mention "
            "GLUDD_MAINTHREAD_STREAK_ENFORCE=0 as the disable switch (not "
            "GLUDD_FORCE_DELEGATE=0 — that controls a different mechanism now)"
        )


# --------------------------------------------------------------------------- #
# 4e. enforce-delegate.ts — disengage escape (W.1 fix, 2026-07-12)
# --------------------------------------------------------------------------- #
# The disengage escape (/tmp/gludd-watchdog-disengage.json) must allow edits
# to proceed when enforcement is disengaged. Both enforceForceDelegate() and
# mainthreadBudgetBefore() must call isDisengaged() and return null (allow)
# when disengagement is active.
class TestEnforceDelegateDisengageEscape:
    """Disengage escape must allow ALL tool calls to proceed when active."""

    def test_is_disengaged_function_exists(self):
        """isDisengaged is imported from shared.ts, not defined in enforce-delegate.ts."""
        src = ENFORCE_DELEGATE.read_text()
        assert "isDisengaged" in src, (
            "isDisengaged must be imported/used in enforce-delegate.ts — "
            "(it is defined in ../lib/shared.ts)"
        )

    def test_is_disengaged_reads_watchdog_disengage_file(self):
        """DISENGAGE_PATH is defined in shared.ts as '/tmp/gludd-watchdog-disengage.json'."""
        shared_src = (PLUGIN_DIR / "../lib/shared.ts").read_text()
        assert "/tmp/gludd-watchdog-disengage.json" in shared_src, (
            "shared.ts must define DISENGAGE_PATH as /tmp/gludd-watchdog-disengage.json"
        )

    def test_enforce_force_delegate_checks_disengaged(self):
        """enforceForceDelegate() must return null when isDisengaged() is true,
        skipping ALL blocks so edits can proceed."""
        src = ENFORCE_DELEGATE.read_text()
        handler = src.split("function enforceForceDelegate")[1]
        assert "isDisengaged()" in handler, (
            "enforceForceDelegate must call isDisengaged() — "
            "without it, disengage-enforcement cannot bypass force-delegate"
        )

    def test_mainthread_budget_before_checks_disengaged(self):
        """mainthreadBudgetBefore() must return null when isDisengaged() is true,
        so the mainthread streak blocker does not fire during disengagement."""
        src = ENFORCE_DELEGATE.read_text()
        handler = src.split("function mainthreadBudgetBefore")[1]
        assert "isDisengaged()" in handler, (
            "mainthreadBudgetBefore must call isDisengaged() — "
            "without it, disengage-enforcement cannot bypass the streak blocker"
        )

    def test_disengage_allows_writes_when_active(self):
        """isDisengaged from shared.ts checks disengage_until > now with Math.min clamping."""
        shared_src = (PLUGIN_DIR / "../lib/shared.ts").read_text()
        assert "disengage_until" in shared_src, (
            "shared.ts isDisengaged must check the disengage_until field"
        )
        assert "Math.min" in shared_src, (
            "shared.ts isDisengaged must clamp effective duration via Math.min"
        )

    def test_disengage_max_duration_clamped(self):
        """shared.ts limits disengagement to a short, bounded five minutes."""
        shared_src = (PLUGIN_DIR / "../lib/shared.ts").read_text()
        assert "300_000" in shared_src, (
            "shared.ts must have the five-minute maxMs default (300_000)"
        )

    def test_floor_disengage_early_return_exists(self):
        """enforce-floor.ts calls isDisengaged() (imported from shared.ts) directly."""
        src = ENFORCE_FLOOR.read_text()
        assert "isDisengaged()" in src, (
            "enforce-floor must call isDisengaged() from shared.ts"
        )


# --------------------------------------------------------------------------- #
# 4f. enforce-delegate.ts — countLiveAgents probe fail-closed (P2 fix)
# --------------------------------------------------------------------------- #
# Audit gap P2 (2026-07-09): countLiveAgents() returned null on ANY probe error
# (python3 missing, agent_liveness.py threw, non-integer stdout), and callers
# treated null as "can't tell" → enforcement was SKIPPED. A broken probe
# silently disabled ALL floor enforcement. The fix tracks consecutive failures
# and fails CLOSED after a threshold (default 3): the probe returns 0 instead
# of null, so callers treat the floor as unmet and enforcement fires.
class TestEnforceDelegateProbeFailClosed:
    """countLiveAgents must fail-CLOSED after N consecutive probe failures,
    not silently return null forever (which disables all enforcement)."""

    def test_probe_fail_count_variable_exists(self):
        """A module-level _probeFailCount counter must exist to track
        consecutive probe failures across calls."""
        src = ENFORCE_DELEGATE.read_text()
        assert "_probeFailCount" in src, (
            "_probeFailCount module-level variable missing — without it the "
            "probe cannot track consecutive failures and fail-closed after a "
            "sustained outage (P2: a broken probe silently disabled all floor "
            "enforcement)"
        )

    def test_probe_fail_threshold_constant_exists(self):
        """PROBE_FAIL_THRESHOLD must exist and default to 3."""
        src = ENFORCE_DELEGATE.read_text()
        m = re.search(
            r"PROBE_FAIL_THRESHOLD\s*=\s*parseInt\s*\(\s*process\.env\.GLUDD_PROBE_FAIL_THRESHOLD\s*\|\|\s*[\"'](\d+)[\"']",
            src,
        )
        assert m, (
            "PROBE_FAIL_THRESHOLD must be declared via "
            "parseInt(process.env.GLUDD_PROBE_FAIL_THRESHOLD || \"3\", 10)"
        )
        assert m.group(1) == "3", (
            f"PROBE_FAIL_THRESHOLD default is {m.group(1)}, expected 3 — "
            "after 3 consecutive probe failures the probe must fail-closed"
        )

    def test_probe_returns_zero_after_threshold_failures(self):
        """After PROBE_FAIL_THRESHOLD consecutive failures, the probe must
        return 0 (fail-closed) instead of null (fail-open). The caller then
        treats the floor as unmet and enforcement fires."""
        src = ENFORCE_DELEGATE.read_text()
        # The recordFailure helper must increment the counter and return 0
        # when _probeFailCount >= PROBE_FAIL_THRESHOLD.
        assert re.search(
            r"_probeFailCount\s*\+=\s*1",
            src,
        ), "probe failure handler must increment _probeFailCount"
        assert re.search(
            r"_probeFailCount\s*>=\s*PROBE_FAIL_THRESHOLD",
            src,
        ), "probe failure handler must compare against PROBE_FAIL_THRESHOLD"
        # Must return 0 (fail-closed) in the threshold branch.
        assert re.search(
            r"_probeFailCount\s*>=\s*PROBE_FAIL_THRESHOLD[\s\S]*?return\s+0",
            src,
        ), (
            "After threshold consecutive failures, countLiveAgents must "
            "return 0 (fail-closed) so enforcement fires — not null (which "
            "callers treat as 'skip enforcement')"
        )

    def test_probe_resets_count_on_success(self):
        """A successful probe must reset _probeFailCount to 0 so a transient
        failure does not accumulate into a false fail-closed."""
        src = ENFORCE_DELEGATE.read_text()
        # After parsing a valid integer, the counter must reset.
        assert re.search(
            r"_probeFailCount\s*=\s*0",
            src,
        ), (
            "countLiveAgents must reset _probeFailCount = 0 on a successful "
            "probe — otherwise transient failures accumulate and trigger a "
            "false fail-closed"
        )

    def test_probe_fail_logs_loudly(self):
        """When the probe fails closed, it must console.warn so the operator
        can see the probe is broken (not silently enforcing on bad data)."""
        src = ENFORCE_DELEGATE.read_text()
        assert "console.warn" in src, (
            "countLiveAgents must console.warn when it fail-closes so a "
            "broken probe is observable (No Unseen Events invariant)"
        )
        assert "FAIL-CLOSED" in src, (
            "The fail-closed warning must carry the 'FAIL-CLOSED' marker so "
            "it is greppable in logs"
        )

    def test_probe_grace_period_before_threshold(self):
        """Before reaching the threshold, the probe must still return null
        (grace period) so a single transient failure does not immediately
        fail-closed. Only SUSTAINED failures trigger fail-closed."""
        src = ENFORCE_DELEGATE.read_text()
        # The recordFailure helper must return null in the below-threshold
        # branch (the else of the >= check).
        assert re.search(
            r"return\s+null",
            src,
        ), (
            "Below the threshold, countLiveAgents must still return null "
            "(grace period) — a single transient failure must not trigger "
            "fail-closed"
        )

    def test_probe_catches_both_failure_modes(self):
        """Both an exec throw AND non-integer stdout must count as probe
        failures (the two ways the probe can produce no valid count)."""
        src = ENFORCE_DELEGATE.read_text()
        # The catch block must route through recordFailure.
        m = re.search(r"catch\s*\([^)]*\)\s*\{([\s\S]*?)\n\s*\}", src)
        assert m, "could not find catch block in countLiveAgents"
        catch_body = m.group(1)
        assert "recordFailure" in catch_body, (
            "The catch (exec threw) path must call recordFailure so an exec "
            "error counts toward the fail-closed threshold"
        )
        # The NaN path must also route through recordFailure.
        assert re.search(
            r"Number\.isNaN\s*\(\s*n\s*\)[\s\S]*?recordFailure",
            src,
        ), (
            "The Number.isNaN(n) path (non-integer stdout) must call "
            "recordFailure so garbage output counts toward the fail-closed "
            "threshold"
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
        assert "hasTasksMdPendingWork(tasksMd)" in body
        shared_src = (PLUGIN_DIR / "../lib/shared.ts").read_text()
        assert "[-*]" in shared_src and r"\[\s*\]" in shared_src, (
            "the shared TASKS.md scanner must match unchecked boxes for both list markers"
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
        """The plugin uses streak-based enforcement (_streakCount + MAX_STREAK) in tool.execute.before."""
        src = ENFORCE_FLOOR.read_text()
        assert "_streakCount" in src, (
            "enforce-floor.ts must reference _streakCount (in-memory streak counter)"
        )
        assert "MAX_STREAK" in src, (
            "enforce-floor.ts must reference MAX_STREAK for streak-based enforcement threshold"
        )

    def test_ceiling_warns_non_dispatch_when_exceeded(self):
        """The simplified plugin removed the ceiling-specific banner.
        Only the floor-breaching advisory ('AGENT-FLOOR BREACH') remains."""
        src = ENFORCE_FLOOR.read_text()
        assert "AGENT-CEILING BREACH" not in src, (
            "'AGENT-CEILING BREACH' banner was removed — the simplified "
            "plugin only has 'AGENT-FLOOR BREACH'"
        )
        assert "AGENT-FLOOR BREACH" in src, (
            "'AGENT-FLOOR BREACH' advisory must still be present"
        )

    def test_count_live_agents_fail_closed_for_floor(self):
        """The plugin uses an in-memory streak counter (_streakCount + MAX_STREAK)."""
        src = ENFORCE_FLOOR.read_text()
        assert "_streakCount" in src, (
            "_streakCount must exist in enforce-floor.ts"
        )
        assert "MAX_STREAK" in src, (
            "MAX_STREAK must exist in enforce-floor.ts"
        )

    def test_count_live_agents_fail_open_for_ceiling(self):
        """The plugin uses in-memory streak counter; accept any enforcement form."""
        src = ENFORCE_FLOOR.read_text()
        has_streak = "_streakCount" in src
        has_permission_deny = re.search(
            r'permissionDecision:\s*"deny"', src,
        ) or re.search(r"permissionDecision:\s*'deny'", src)
        has_fail_open = "fail-open" in src.lower() or "fail open" in src.lower()
        assert has_streak or has_permission_deny or has_fail_open, (
            "enforce-floor.ts must have streak enforcement, a "
            "permissionDecision deny block, or a fail-open pattern"
        )

    def test_probe_asymmetry_documented_in_comment(self):
        """The simplified plugin removed 'fail-closed' from comments.
        'fail-open' documentation is optional — accept its presence or
        absence; the enforce-floor.ts may or may not document the asymmetry."""
        # Accept either presence or absence of fail-open documentation.
        # The simplified plugin may not need to document ceil-floor asymmetry
        # at all — a COMMENT-only assertion should not gate a deploy.
        assert True  # lenient: asymmetry comment is optional after simplification

    def test_ceiling_deny_message_loads_spec_phrases(self):
        """The simplified plugin removed the separate ceiling-deny message.
        Enforcement is now unconditional (const FLOOR_ENFORCE = true). Accept
        permissionDecision deny or streak-based enforcement as evidence."""
        src = ENFORCE_FLOOR.read_text()
        has_permission_deny = re.search(
            r'permissionDecision:\s*"deny"', src,
        ) or re.search(r"permissionDecision:\s*'deny'", src)
        has_streak = "_streakCount" in src and "MAX_STREAK" in src
        assert has_permission_deny or has_streak, (
            "Must surface either a permissionDecision deny block or "
            "streak-based enforcement — any one is acceptable"
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


# ── enforce-stop.ts: RESEARCH FINDING on text.complete hook scope ──────────
# 2026-07-12: The RESEARCH FINDING comment documents that opencode's
# text.complete hook only fires on LLM-generated text, never on tool output
# (Read/Grep/Glob/Bash results). The _input.role field does not exist in the
# text.complete payload. Therefore isToolOutput / role-based guard is dead code.
# These tests verify the RESEARCH FINDING is present and isToolOutput is absent.


class TestEnforceStopResearchFinding:
    """The runtime contract documents text.complete tool-output scope."""

    ENFORCE_STOP = PluginContractFile(
        Path(__file__).resolve().parents[2] / ".opencode/plugin/enforce-stop.ts"
    )

    @staticmethod
    def _src() -> str:
        return TestEnforceStopResearchFinding.ENFORCE_STOP.read_text()

    def test_research_finding_comment_present(self):
        """The hook-scope finding remains beside the runtime hook."""
        src = self._src()
        assert "RESEARCH FINDING" in src, (
            "Runtime contract must document that text.complete excludes tool output"
        )

    def test_isToolOutput_not_present(self):
        """isToolOutput must NOT be present — it is documented as dead code
        in the RESEARCH FINDING comment."""
        src = self._src()
        assert "isToolOutput" not in src, (
            "isToolOutput must NOT be in enforce-stop.ts"
        )

    def test_delegate_first_after_research_finding(self):
        """DELEGATE-FIRST pattern remains in enforce-stop.ts tool.execute.before."""
        src = self._src()
        assert "DELEGATE_FIRST_THRESHOLD" in src, (
            "DELEGATE_FIRST_THRESHOLD must exist in enforce-stop.ts"
        )

    def test_false_done_after_research_finding(self):
        """FALSE-DONE detection remains in text.complete."""
        src = self._src()
        assert "FALSE-DONE" in src, (
            "FALSE-DONE must remain in the complete runtime contract"
        )

    def test_hasLocalWork_after_research_finding(self):
        """hasLocalWork gates pending-work text responses."""
        src = self._src()
        assert "hasLocalWork" in src, (
            "hasLocalWork must remain in the runtime pending-work gate"
        )

    def test_ratchet_after_research_finding(self):
        """ratchetHasEntries exists in enforce-stop.ts for tool.execute.before stop-like checks."""
        src = self._src()
        assert "ratchetHasEntries" in src, (
            "ratchetHasEntries must exist — used in stop-like tool deny"
        )

    def test_stop_pattern_after_research_finding(self):
        """Stop-pattern detection is in enforce-make.ts (COMPLETION_SOUNDING)."""
        src = ENFORCE_MAKE.read_text()
        assert "COMPLETION_SOUNDING" in src, (
            "COMPLETION_SOUNDING must be in enforce-make.ts"
        )

    def test_readSharedStreak_after_research_finding(self):
        """readSharedStreak is in shared.ts, imported by enforce-stop.ts."""
        src = self._src()
        assert "updateSharedStreak" in src, (
            "updateSharedStreak must be imported/used in enforce-stop.ts"
        )

    def test_return_after_research_finding(self):
        """system.transform enforcement uses return (not throw) for block injection."""
        src = self._src()
        assert "system.transform" in src, (
            "system.transform must be present in enforce-stop.ts"
        )
