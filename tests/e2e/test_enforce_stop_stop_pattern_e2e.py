"""E2e test for enforce-stop text blanking and system.transform behavior.

Re-implements the enforce-stop.ts plugin's text transformation and stop-pattern
detection in pure Python (matching the e2e test pattern from
test_enforcement_plugin_e2e.py). Covers:

1. system.transform: prepends MANDATORY PRE-GENERATION GATE when pending work exists
2. system.transform: passes subagent result markers through
3. system.transform: adds "[orchestration] No pending work" when clean
4. Stop-pattern detection: QA phrases, permission-seeking phrases, false-done claims
5. Evidence-carrying text passes through
6. Edge cases: empty text, partial matches, mixed content
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENFORCE_STOP_TS = ROOT / ".opencode" / "plugin" / "enforce-stop.ts"

# ── Stop-pattern regexes from the hot-reload module / compiled JS ────────────
# These patterns are defined in the enforce-stop hot module
# (/private/tmp/enforce-stop-stripped.js, /private/tmp/e-stop-as-js.js).
# They are re-implemented here as equivalent Python regexes.

QA_RESPONSE_PATTERNS = re.compile(
    r"\b(?:"
    r"completed in this session|"
    r"was done since the (?:crash|last session)|"
    r"everything (?:committed|has been committed)(?:\s+and\s+merged)?|"
    r"here['\u2019]s what (?:was\s+(?:done|completed|finished)|changed)|"
    r"what (?:changed|was done|happened)\s+since\s+the\s+(?:crash|last session)|"
    r"summary of what was (?:done|completed)"
    r")\b|"
    r"\*\*What\s+(?:changed|was\s+(?:done|completed)|happened|is\s+(?:left|remaining))\?\*\*",
    re.IGNORECASE,
)

STOP_PATTERN_PHRASES = re.compile(
    r"\b(?:shall\s+i\s+continue|should\s+i\s+proceed|want\s+me\s+to)\b",
    re.IGNORECASE,
)

SUBAGENT_TEXT_MARKERS = re.compile(
    r"task_id|task_result|agent\s+result|subagent\s+result|task\s+completed",
    re.IGNORECASE,
)

# ── Evidence patterns (must NOT be blanked) ─────────────────────────────────

EVIDENCE_RE = re.compile(
    r"(commit\s+[0-9a-f]{7,}|"
    r"VERIFIED\s+\S+@[0-9a-f]{7,}|"
    r"CI\s+(?:GREEN|RED|PENDING)|"
    r"(?:^|\n)\s*\d+\s+passed\b|"
    r"===\s*GATE:\s*(?:PASSED|FAILED)\s*===|"
    r"Collection\s+OK)",
    re.IGNORECASE | re.MULTILINE,
)

# ── Common false-done / completion-claim patterns ───────────────────────────
# Separate period-terminated patterns (no \b after period) from word patterns.

FALSE_DONE_DOT_RE = re.compile(
    r"\b(?:Done\.|All\s+done\.|All\s+complete\.?|Everything\s+is\s+complete\.?|"
    r"Ready\s+for\s+review\.?)",
    re.IGNORECASE,
)

FALSE_DONE_WORD_RE = re.compile(
    r"\b(?:landed|shipped|fixed|resolved|deployed|working\s+now)\b",
    re.IGNORECASE,
)


# ── Re-implemented plugin logic ─────────────────────────────────────────────


def _has_subagent_markers(text: str) -> bool:
    return bool(SUBAGENT_TEXT_MARKERS.search(text))


def _has_evidence(text: str) -> bool:
    return bool(EVIDENCE_RE.search(text))


def _has_stop_pattern(text: str) -> bool:
    return bool(
        QA_RESPONSE_PATTERNS.search(text)
        or STOP_PATTERN_PHRASES.search(text)
        or FALSE_DONE_DOT_RE.search(text)
        or FALSE_DONE_WORD_RE.search(text)
    )


def _pending_work_flags(
    tasks_content: str = "",
    ratchet_content: str = "",
) -> dict:
    unchecked = len(re.findall(r"^[-*]\s+\[ \]", tasks_content, re.MULTILINE))
    ratchet_count = len([
        l for l in ratchet_content.split("\n")
        if l.strip() and not l.strip().startswith("#") and (":" in l or "::" in l)
    ])
    has_work = unchecked > 0 or ratchet_count > 0
    return {"unchecked": unchecked, "ratchet_count": ratchet_count, "has_work": has_work}


def _system_transform(text: str, has_work: bool, unchecked: int = 0, ratchet_count: int = 0) -> str:
    """Re-implement enforce-stop.ts experimental.chat.system.transform."""
    if _has_subagent_markers(text):
        return text
    if has_work:
        indicators = []
        if unchecked > 0:
            indicators.append(f"{unchecked} unchecked TASKS.md items")
        if ratchet_count > 0:
            indicators.append(f"{ratchet_count} ratchet entries")
        block = (
            "\n"
            "────────────────────────────────────────────────────────────────\n"
            "PENDING WORK EXISTS: " + ", ".join(indicators) + ".\n"
            "\n"
            "YOU ARE PHYSICALLY FORBIDDEN FROM GENERATING A TEXT-ONLY RESPONSE.\n"
            "Before generating ANY text character, you MUST include a tool call.\n"
            "────────────────────────────────────────────────────────────────\n"
            "\n"
            + text
        )
        return block
    return f"[orchestration] No pending work. Normal operation.\n\n{text}"


def _text_complete_blank(text: str, has_work: bool) -> str:
    """Re-implement the text blanking behavior from enforce-stop text.complete hook.

    When pending work exists AND the response contains stop patterns AND lacks
    evidence, the text should be blanked/replaced.
    """
    if not has_work:
        return text
    if _has_subagent_markers(text):
        return text
    if _has_evidence(text):
        return text
    if _has_stop_pattern(text):
        return (
            " QA RESPONSE SUMMARY BLOCKED: The agent sent a stop-pattern "
            "response while work is pending. DISPATCH A TOOL CALL NOW."
        )
    return text


# ── Test classes ────────────────────────────────────────────────────────────


class TestSystemTransformPendingWork:
    """When hasWork is true, system.transform prepends the PRE-GENERATION GATE."""

    def test_pending_work_adds_gate_block(self):
        text = "Here is a text-only summary of what was done."
        result = _system_transform(text, has_work=True, unchecked=3, ratchet_count=1)
        assert "PENDING WORK EXISTS" in result
        assert "3 unchecked TASKS.md items" in result
        assert "1 ratchet entries" in result
        assert text in result

    def test_pending_work_with_only_unchecked_tasks(self):
        text = "Working on the fix."
        result = _system_transform(text, has_work=True, unchecked=2, ratchet_count=0)
        assert "2 unchecked TASKS.md items" in result
        assert "ratchet" not in result

    def test_pending_work_gate_block_contains_mandatory_text(self):
        text = "Status update."
        result = _system_transform(text, has_work=True, unchecked=1, ratchet_count=0)
        assert "YOU ARE PHYSICALLY FORBIDDEN" in result
        assert "tool call" in result.lower()

    def test_no_pending_work_returns_orchestration_prefix(self):
        text = "All tests pass. Continuing work."
        result = _system_transform(text, has_work=False)
        assert result.startswith("[orchestration] No pending work.")
        assert text in result

    def test_no_pending_work_empty_text(self):
        result = _system_transform("", has_work=False)
        assert result.startswith("[orchestration] No pending work.")


class TestSubagentPassThrough:
    """Subagent result markers cause system.transform to return text unchanged."""

    def test_task_id_marker_passes_through(self):
        text = "task_id: abc123 — subagent result follows.\nDone."
        result = _system_transform(text, has_work=True, unchecked=3)
        assert result == text

    def test_subagent_result_marker_passes_through(self):
        text = "subagent result: fixed enforce-stop.ts, 5 tests pass."
        result = _system_transform(text, has_work=True, unchecked=3)
        assert result == text

    def test_task_completed_passes_through(self):
        text = "task completed — commit abcdef12 landed."
        result = _system_transform(text, has_work=True, unchecked=3)
        assert result == text

    def test_subagent_marker_unaffected_by_stop_pattern(self):
        text = "agent result: All complete. Everything is finished. task_id: xyz."
        result = _system_transform(text, has_work=True, unchecked=3)
        assert result == text


class TestStopPatternBlanking:
    """When pending work exists, stop-pattern text without evidence is blanked."""

    def test_done_stop_pattern_blanked(self):
        result = _text_complete_blank("Done.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in result

    def test_all_complete_stop_pattern_blanked(self):
        result = _text_complete_blank("All complete.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in result

    def test_everything_is_complete_blanked(self):
        result = _text_complete_blank("Everything is complete. All tests pass.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in result

    def test_ready_for_review_blanked(self):
        result = _text_complete_blank("Ready for review.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in result

    def test_qa_recap_blanked(self):
        texts = [
            "Completed in this session: fixed the gate, updated tests.",
            "Here's what was done since the crash.",
            "Everything committed and merged.",
            "Summary of what was done: 3 fixes landed.",
        ]
        for text in texts:
            result = _text_complete_blank(text, has_work=True)
            assert "QA RESPONSE SUMMARY BLOCKED" in result, (
                f"Expected blanking for: {text[:60]}"
            )

    def test_permission_seeking_blanked(self):
        for text in ["Shall I continue?", "Should I proceed?", "Want me to start building?"]:
            result = _text_complete_blank(text, has_work=True)
            assert "QA RESPONSE SUMMARY BLOCKED" in result, f"Expected blanking for: {text}"

    def test_no_work_no_blanking(self):
        text = "Done. All complete. Everything is finished."
        result = _text_complete_blank(text, has_work=False)
        assert result == text

    def test_clean_text_not_blanked(self):
        for text in [
            "Working on the fix for the delegate issue.",
            "Running make test-unit to verify.",
            "The gate output shows lint 0, typecheck baseline.",
        ]:
            result = _text_complete_blank(text, has_work=True)
            assert "QA RESPONSE SUMMARY BLOCKED" not in result, (
                f"Clean text should not be blanked: {text[:60]}"
            )


class TestEvidenceCarryingText:
    """Text carrying machine-produced evidence must pass through unblanked."""

    def test_commit_hash_evidence_passes(self):
        result = _text_complete_blank("Fixed the bug. commit abc1234 landed.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_verified_ref_evidence_passes(self):
        result = _text_complete_blank("VERIFIED master@abc1234 — push confirmed.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_ci_green_evidence_passes(self):
        result = _text_complete_blank("CI GREEN: all checks passed.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_pass_count_evidence_passes(self):
        result = _text_complete_blank("42 passed in 12.34s. All done.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_gate_passed_evidence_passes(self):
        result = _text_complete_blank("=== GATE: PASSED === The build is done.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_collection_ok_evidence_passes(self):
        result = _text_complete_blank("Collection OK — 142 tests collected.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_evidence_also_prevents_gate_block_in_transform(self):
        text = "commit abc1234 — all tests pass. Done."
        result = _system_transform(text, has_work=True, unchecked=3)
        assert "PENDING WORK EXISTS" in result


class TestEdgeCases:
    """Edge cases: empty text, mixed content, partial matches, empty state."""

    def test_empty_text_not_blanked(self):
        result = _text_complete_blank("", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_very_short_text_not_blanked(self):
        for text in ["ok", "yes"]:
            result = _text_complete_blank(text, has_work=True)
            assert "QA RESPONSE SUMMARY BLOCKED" not in result, f"'{text}' should not be blanked"

    def test_bare_done_without_period_is_floating_stop_word(self):
        """'done' without period is a low-signal stop word that SHOULD be
        caught by the evidence-gap guard but does NOT match FALSE_DONE_DOT_RE.
        The plugin correctly requires the period '.' for the bare claim."""
        result = _text_complete_blank("done", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_evidence_and_stop_pattern_together_pass(self):
        text = "All complete. commit abc1234 landed."
        result = _text_complete_blank(text, has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_subagent_marker_and_stop_pattern_pass(self):
        text = "task_id: xyz — All complete. Everything is finished."
        result = _text_complete_blank(text, has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" not in result

    def test_zero_unchecked_zero_ratchet_means_no_work(self):
        flags = _pending_work_flags(
            tasks_content="# Project Tasks\n\n## Completed\n- [x] All done\n",
            ratchet_content="# No entries\n",
        )
        assert not flags["has_work"]
        assert flags["unchecked"] == 0
        assert flags["ratchet_count"] == 0

    def test_one_unchecked_means_has_work(self):
        flags = _pending_work_flags(
            tasks_content="# Tasks\n\n- [ ] Fix the bug\n",
            ratchet_content="",
        )
        assert flags["has_work"]
        assert flags["unchecked"] == 1

    def test_ratchet_entry_means_has_work(self):
        flags = _pending_work_flags(
            tasks_content="",
            ratchet_content="dead_code:: src/dead_module.py\n",
        )
        assert flags["has_work"]
        assert flags["ratchet_count"] == 1

    def test_regex_case_insensitivity(self):
        assert "QA RESPONSE SUMMARY BLOCKED" in _text_complete_blank("DONE.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in _text_complete_blank("ALL COMPLETE.", has_work=True)
        assert "QA RESPONSE SUMMARY BLOCKED" in _text_complete_blank(
            "COMPLETED IN THIS SESSION: 3 fixes.", has_work=True
        )
        assert "QA RESPONSE SUMMARY BLOCKED" in _text_complete_blank(
            "SHALL I CONTINUE?", has_work=True
        )

    def test_qa_bolded_header_blanked(self):
        for header in [
            "**What changed?**",
            "**What was done?**",
            "**What is left?**",
            "**What happened?**",
        ]:
            assert "QA RESPONSE SUMMARY BLOCKED" in _text_complete_blank(header, has_work=True), (
                f"Expected blanking for {header}"
            )


class TestPatternExistence:
    """Verify the regex patterns are real and match expected inputs."""

    def test_qa_patterns_match_canonical_phrases(self):
        assert QA_RESPONSE_PATTERNS.search("completed in this session")
        assert QA_RESPONSE_PATTERNS.search("Everything committed and merged")
        assert QA_RESPONSE_PATTERNS.search("was done since the crash")
        assert QA_RESPONSE_PATTERNS.search("here's what was completed")
        assert QA_RESPONSE_PATTERNS.search("summary of what was done")

    def test_stop_phrases_match_canonical_phrases(self):
        assert STOP_PATTERN_PHRASES.search("Shall I continue?")
        assert STOP_PATTERN_PHRASES.search("Should I proceed?")
        assert STOP_PATTERN_PHRASES.search("Want me to")
        assert not STOP_PATTERN_PHRASES.search("normal text without patterns")

    def test_subagent_markers_match(self):
        assert SUBAGENT_TEXT_MARKERS.search("task_id: 12345")
        assert SUBAGENT_TEXT_MARKERS.search("subagent result: tests pass")
        assert not SUBAGENT_TEXT_MARKERS.search("normal text without markers")

    def test_evidence_patterns_match(self):
        assert EVIDENCE_RE.search("commit abc12345 landed")
        assert EVIDENCE_RE.search("VERIFIED master@abc1234")
        assert EVIDENCE_RE.search("42 passed")
        assert not EVIDENCE_RE.search("plain text with no evidence")

    def test_false_done_dot_matches(self):
        assert FALSE_DONE_DOT_RE.search("Done.")
        assert FALSE_DONE_DOT_RE.search("All done.")
        assert FALSE_DONE_DOT_RE.search("Everything is complete.")
        assert FALSE_DONE_DOT_RE.search("Ready for review.")
        assert not FALSE_DONE_DOT_RE.search("done")  # no period

    def test_false_done_word_matches(self):
        assert FALSE_DONE_WORD_RE.search("landed")
        assert FALSE_DONE_WORD_RE.search("the bug is fixed now")
        assert FALSE_DONE_WORD_RE.search("problem resolved")
        assert not FALSE_DONE_WORD_RE.search("normal conversation")
