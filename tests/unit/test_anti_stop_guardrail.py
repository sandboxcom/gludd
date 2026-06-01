"""Tests for the anti-stop guardrail in enforce-make.ts plugin."""
from __future__ import annotations

from typing import ClassVar

import pytest

STOP_SIGNAL_WORDS = [
    "want me to proceed",
    "want me to continue",
    "want me to start",
    "want me to fix",
    "should i continue",
    "should i proceed",
    "should i start",
    "should i fix",
    "shall i continue",
    "shall i proceed",
    "next steps are",
    "remaining tasks",
    "here are the remaining",
    "what priority",
    "here is what needs to be done",
    "i'll stop here",
    "let me know if you'd like",
    "let me know if you want",
    "i can continue",
    "i could continue",
    "would you like me to",
    "ready to proceed",
    "here's my plan",
    "here is my plan",
]


class TestStopPatternDetection:
    STOP_PHRASES: ClassVar[list[str]] = [
        "want me to proceed",
        "Want me to continue?",
        "should I continue",
        "Should I proceed?",
        "next steps are",
        "remaining tasks",
        "Here are the remaining",
        "What priority",
        "want me to start",
        "want me to fix",
        "would you like me to",
        "I can continue",
        "I could continue",
        "here's my plan",
        "here is my plan",
    ]

    SAFE_PHRASES: ClassVar[list[str]] = [
        "All tasks complete. Committed as abc123.",
        "Phase 2 wired. Running tests now.",
        "The implementation is in src/foo.py:42.",
        "1935 passed, 12 skipped, 92.44% coverage.",
        "Continuing with Phase 3.",
    ]

    @pytest.mark.parametrize("phrase", STOP_PHRASES)
    def test_detects_stop_pattern(self, phrase):
        lower = phrase.lower()
        detected = any(p in lower for p in STOP_SIGNAL_WORDS)
        assert detected, f"Failed to detect stop pattern: {phrase}"

    @pytest.mark.parametrize("phrase", SAFE_PHRASES)
    def test_does_not_flag_safe_phrases(self, phrase):
        lower = phrase.lower()
        detected = any(p in lower for p in STOP_SIGNAL_WORDS)
        assert not detected, f"False positive on safe phrase: {phrase}"
