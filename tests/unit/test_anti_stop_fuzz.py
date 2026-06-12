"""Anti-stop fuzz test — verifies state-based stop detection.

Tests that the plugin uses STATE checks (ratchet entries, gate status)
rather than a vocabulary word list. Auto-parses BUGS.md for incident
messages and verifies they would be caught by state-based checks.
"""
from __future__ import annotations

import re
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


PLUGIN_PATH = _project_root() / ".opencode" / "plugin" / "enforce-make.ts"
RATCHET_PATH = _project_root() / "config" / "ratchet.yml"


def _plugin_content() -> str:
    return PLUGIN_PATH.read_text(encoding="utf-8")


def _extract_stop_messages() -> list[str]:
    bugs_path = _project_root() / "BUGS.md"
    if not bugs_path.is_file():
        return []
    content = bugs_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    messages: list[str] = []
    in_section = False
    for _i, line in enumerate(lines):
        stripped = line.strip()
        if any(kw in stripped.lower() for kw in [
            "what stopped before finishing",
            "what was claimed:",
            "agent sent",
            "agent wrote",
            "agent presented",
        ]):
            in_section = True
            continue
        if stripped.startswith("###") or stripped.startswith("- **Why"):
            in_section = False
            continue
        if in_section:
            quoted = re.findall(r'"([^"]{12,})"', stripped)
            for q in quoted:
                ql = q.lower()
                if q in messages:
                    continue
                if ql.startswith(("make ", "`", "read ", "sed ", "git ")):
                    continue
                if any(bad in ql for bad in [
                    "do not stop", "do not send", "must not",
                    "should not", "never ", "blocked",
                ]):
                    continue
                messages.append(q)
    return messages


def _completion_sounding_words() -> list[str]:
    content = _plugin_content()
    match = re.search(r"const COMPLETION_SOUNDING = \[([\s\S]*?)\]", content)
    if not match:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def _is_caught_by_state_check(text: str, completion_words: list[str]) -> bool:
    lower = text.lower()
    return any(w in lower for w in completion_words)


_BASE_MESSAGES = _extract_stop_messages()
_COMPLETION_WORDS = _completion_sounding_words()

_SHOULD_DETECT: list[str] = [
    "All done. Everything is complete.",
    "Ready for review. Waiting for your feedback.",
    "That completes the task.",
    "All objectives delivered.",
    "This is truly done now.",
    "Phase complete. All work done.",
    "Gate ALL PASSED with 11 commits.",
    "23 items done. Remaining: S12, S14.",
    "Committed. Everything is done.",
    "Summary: all work completed.",
]

_SHOULD_NOT_DETECT: list[str] = [
    "Let me fix that bug now.",
    "Running make gate to verify the fix.",
    "The error is in daemon.py line 622.",
    "make test-specific TESTFILE='tests/unit/test_zai_skip_behavior.py'",
    "Creating a fuzz test for the stop detection.",
    "The gate shows: lint PASS 0, typecheck PASS 18, collect PASS 0, test PASS 0",
    "Run make git-commit after the fix.",
    "Adding a new test for the worker pipeline.",
]


class TestAntiStopFuzz:
    def test_no_stop_signal_words_list(self):
        content = _plugin_content()
        assert "const STOP_SIGNAL_WORDS" not in content, (
            "STOP_SIGNAL_WORDS vocabulary list must be deleted — "
            "state-based checks only per W1.3"
        )

    def test_state_based_checks_exist(self):
        content = _plugin_content()
        assert "ratchet" in content.lower(), "Ratchet-based check must exist"
        assert "COMPLETION_SOUNDING" in content, "COMPLETION_SOUNDING must exist"
        assert ".gate-status" in content, "Gate-status check must exist"

    def test_gate_status_check_blocks_red_gate(self):
        content = _plugin_content()
        assert "lint PASS" in content, "Gate check must verify lint PASS"
        assert "typecheck PASS" in content, "Gate check must verify typecheck PASS"
        assert "collect PASS" in content, "Gate check must verify collect PASS"
        assert "test PASS" in content, "Gate check must verify test PASS"

    def test_ratchet_check_with_completion_sounding(self):
        content = _plugin_content()
        assert 'ratchetLines.length > 0' in content, (
            "Must check ratchet has entries"
        )
        assert "soundsComplete" in content, "Must check completion-sounding words"

    def test_base_messages_caught_by_state_check(self):
        assert len(_BASE_MESSAGES) > 0, "No messages extracted from BUGS.md"
        assert len(_COMPLETION_WORDS) > 0, "No COMPLETION_SOUNDING words found"
        missed = [
            m for m in _BASE_MESSAGES
            if not _is_caught_by_state_check(m, _COMPLETION_WORDS)
        ]
        assert not missed, (
            f"{len(missed)}/{len(_BASE_MESSAGES)} BUGS.md base messages NOT caught "
            f"by state-based completion check:\n"
            + "\n".join(f"  - {m[:120]}" for m in missed)
        )

    def test_completion_patterns_caught(self):
        assert len(_COMPLETION_WORDS) > 0
        missed = [
            m for m in _SHOULD_DETECT
            if not _is_caught_by_state_check(m, _COMPLETION_WORDS)
        ]
        assert not missed, (
            f"{len(missed)} static completion patterns NOT caught: {missed}"
        )

    def test_work_messages_not_blocked(self):
        false_positives = [
            m for m in _SHOULD_NOT_DETECT
            if _is_caught_by_state_check(m, _COMPLETION_WORDS)
        ]
        assert not false_positives, (
            f"{len(false_positives)} false positive(s): {false_positives}"
        )

    def test_ratchet_file_exists_and_has_entries(self):
        assert RATCHET_PATH.is_file(), "config/ratchet.yml missing"
        content = RATCHET_PATH.read_text(encoding="utf-8")
        entries = [ln for ln in content.splitlines()
                   if ln.strip() and not ln.strip().startswith("#") and ": \"" in ln]
        assert len(entries) > 0, "config/ratchet.yml has no entries"
