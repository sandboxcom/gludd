"""Anti-stop fuzz test — verifies stop-pattern detection catches completion claims.

Systematic regression test: every BUGS.md incident message must be detected.
Also verifies no false positives on normal work messages.
"""
from __future__ import annotations

import re
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _load_stop_signal_words() -> list[str]:
    plugin_path = _project_root() / ".opencode" / "plugin" / "enforce-make.ts"
    content = plugin_path.read_text(encoding="utf-8")
    match = re.search(r"const STOP_SIGNAL_WORDS = \[([\s\S]*?)\]", content)
    if not match:
        return []
    entries = re.findall(r'"([^"]*)"', match.group(1))
    assert len(entries) > 0, "STOP_SIGNAL_WORDS not found in enforce-make.ts"
    return entries


def _detect_stop(text: str, stop_words: list[str]) -> bool:
    lower = text.lower()
    return any(pattern.lower() in lower for pattern in stop_words)


# BUGS.md incident messages — each slipped through in a REAL incident
_BUGS_INCIDENT_MESSAGES: list[str] = [
    "Phase V0 complete. Here's a summary of what was implemented:",
    "All complete. The work is done. Everything passes.",
    "Session 12 summary: 3 commits, 67 new tests, all green.",
    "Shall I start working through these?",
    "All tasks done. 2 items remaining: TUI parity, model auto-population.",
    "5488 passed, 136 failed, 30 skipped — committed",
    "What did we do so far? Next steps are Phase 3, Phase 7.",
]

_SHOULD_DETECT: list[str] = [
    "All done. Everything is complete.",
    "Ready for review. Waiting for your feedback.",
    "Shall I continue working?",
    "Would you like me to proceed?",
    "That completes the task.",
    "All objectives delivered.",
    "Here's a summary of this session.",
    "Done. Committed.",
    "All green. Tests pass, lint 0.",
    "To summarize, we fixed 42 failures.",
    "This is truly done now.",
]

_SHOULD_NOT_DETECT: list[str] = [
    "Let me fix that bug now.",
    "Running make gate to verify the fix.",
    "The error is in daemon.py line 622.",
    "I need to add a guardrail for this pattern.",
    "make test-specific TESTFILE='tests/unit/test_zai_skip_behavior.py'",
    "Creating a fuzz test for the stop detection.",
    "Status: 42 failures fixed, 94 remaining with xfail markers.",
    "The gate shows: lint PASS 0, typecheck PASS 18, collect PASS 0, test PASS 0",
    "I'll continue with V1.4 now.",
    "Run make git-commit after the fix.",
]


class TestAntiStopFuzz:
    def test_signal_words_exist(self):
        words = _load_stop_signal_words()
        assert len(words) >= 50, f"Only {len(words)} stop signal words"

    def test_bugs_messages_are_caught(self):
        words = _load_stop_signal_words()
        missed = [m for m in _BUGS_INCIDENT_MESSAGES if not _detect_stop(m, words)]
        assert not missed, f"{len(missed)} BUGS.md incident(s) NOT caught: {missed}"

    def test_completion_patterns_are_caught(self):
        words = _load_stop_signal_words()
        missed = [m for m in _SHOULD_DETECT if not _detect_stop(m, words)]
        assert not missed, f"{len(missed)} completion pattern(s) NOT caught: {missed}"

    def test_work_messages_are_not_caught(self):
        words = _load_stop_signal_words()
        fp = [m for m in _SHOULD_NOT_DETECT if _detect_stop(m, words)]
        assert not fp, f"{len(fp)} false positive(s): {fp}"

    def test_ratchet_file_exists_and_has_entries(self):
        ratchet_path = _project_root() / "config" / "ratchet.yml"
        assert ratchet_path.is_file(), "config/ratchet.yml missing"
        content = ratchet_path.read_text(encoding="utf-8")
        entries = [ln for ln in content.splitlines()
                   if ln.strip() and not ln.strip().startswith("#") and ": \"" in ln]
        assert len(entries) > 0, "config/ratchet.yml has no entries"
