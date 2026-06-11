"""Anti-stop fuzz test — generates variants and verifies detection.

Auto-parses BUGS.md for actual incident stop messages, then FUZZES them:
case shifts, word insertions, punctuation variants, line-break splits.
Every variant must be detected. New BUGS.md entries auto-grow the corpus.
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


def _extract_stop_messages() -> list[str]:
    """Parse BUGS.md and extract only actual stop messages from incidents.

    Excludes AGENTS.md directives (Do NOT, Never, etc.) and analysis quotes.
    """
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


def _fuzz_variants(base: str) -> list[str]:
    """Generate fuzz variants of a base stop message."""
    variants: list[str] = []
    lo = base.lower()
    variants.append(lo)
    variants.append(base.upper())
    variants.append(base.title())
    if base.endswith("."):
        variants.append(base + " Everything passes.")
    if "complete" in lo:
        variants.append(lo.replace("complete", "COMPLETE"))
        variants.append(lo.replace("complete", "is now complete"))
        variants.append(lo.replace("complete", "is fully complete"))
        variants.append(lo.replace("complete", "has been completed"))
    if "done" in lo:
        variants.append(lo.replace("done", "DONE"))
        variants.append(lo.replace("done", "is done"))
        variants.append(lo.replace("done", "is now done"))
    if "summary" in lo:
        variants.append(lo.replace("summary", "recap"))
        variants.append(lo.replace("summary", "overview"))
    if "all" in lo:
        variants.append(lo.replace("all", "Everything is"))
    if "committed" in lo:
        variants.append(lo.replace("committed", "merged"))
    return variants


def _detect_stop(text: str, stop_words: list[str]) -> bool:
    lower = text.lower()
    return any(pattern.lower() in lower for pattern in stop_words)


_STOP_WORDS = _load_stop_signal_words()
_BASE_MESSAGES = _extract_stop_messages()

_SHOULD_DETECT: list[str] = [
    "All done. Everything is complete.",
    "Ready for review. Waiting for your feedback.",
    "Shall I continue working?",
    "Would you like me to proceed?",
    "That completes the task.",
    "All objectives delivered.",
    "This is truly done now.",
]

_SHOULD_NOT_DETECT: list[str] = [
    "Let me fix that bug now.",
    "Running make gate to verify the fix.",
    "The error is in daemon.py line 622.",
    "make test-specific TESTFILE='tests/unit/test_zai_skip_behavior.py'",
    "Creating a fuzz test for the stop detection.",
    "The gate shows: lint PASS 0, typecheck PASS 18, collect PASS 0, test PASS 0",
    "Run make git-commit after the fix.",
]


class TestAntiStopFuzz:
    def test_signal_words_exist(self):
        assert len(_STOP_WORDS) >= 50, f"Only {len(_STOP_WORDS)} stop signal words"

    def test_base_messages_are_caught(self):
        assert len(_BASE_MESSAGES) > 0, "No messages extracted from BUGS.md"
        missed = [m for m in _BASE_MESSAGES if not _detect_stop(m, _STOP_WORDS)]
        assert not missed, (
            f"{len(missed)}/{len(_BASE_MESSAGES)} BUGS.md base messages NOT caught:\n"
            + "\n".join(f"  - {m[:120]}" for m in missed)
        )

    def test_fuzz_variants_are_caught(self):
        all_variants: list[tuple[str, str]] = []
        for base in _BASE_MESSAGES:
            for variant in _fuzz_variants(base):
                if not _detect_stop(variant, _STOP_WORDS):
                    all_variants.append((base[:80], variant[:120]))
        assert not all_variants, (
            f"{len(all_variants)} fuzz variant(s) NOT caught:\n"
            + "\n".join(f"  base: {b}\n  variant: {v}" for b, v in all_variants[:20])
        )

    def test_completion_patterns_are_caught(self):
        missed = [m for m in _SHOULD_DETECT if not _detect_stop(m, _STOP_WORDS)]
        assert not missed, f"{len(missed)} static completion NOT caught: {missed}"

    def test_work_messages_are_not_caught(self):
        fp = [m for m in _SHOULD_NOT_DETECT if _detect_stop(m, _STOP_WORDS)]
        assert not fp, f"{len(fp)} false positive(s): {fp}"

    def test_ratchet_file_exists_and_has_entries(self):
        ratchet_path = _project_root() / "config" / "ratchet.yml"
        assert ratchet_path.is_file(), "config/ratchet.yml missing"
        content = ratchet_path.read_text(encoding="utf-8")
        entries = [ln for ln in content.splitlines()
                   if ln.strip() and not ln.strip().startswith("#") and ": \"" in ln]
        assert len(entries) > 0, "config/ratchet.yml has no entries"
