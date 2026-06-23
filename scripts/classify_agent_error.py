#!/usr/bin/env python3
"""
classify_agent_error.py — Classify a subagent result/error string as TRANSIENT or TERMINAL.

Usage:
    python scripts/classify_agent_error.py "some error string"
    echo "api overloaded" | python scripts/classify_agent_error.py

Exit codes:
    0  — TRANSIENT: the error is a retry signal (overload, rate-limit, network blip, etc.)
    1  — TERMINAL: the error is a structural problem; do not retry, fix the root cause.

Classification logic (applied in order):
    1. If any TERMINAL marker is found → "terminal" (exit 1)
    2. Else if any TRANSIENT marker is found → "transient" (exit 0)
    3. Else → "terminal" (fail-closed: unknown errors don't get retried)

TRANSIENT markers (case-insensitive):
    "api error", "overloaded", "rate limit", "429", "529", "503", "500",
    "timeout", "connection", "stream watchdog", "session limit",
    "temporarily", "try again"

TERMINAL markers (case-insensitive — override TRANSIENT if matched):
    "auth", "permission denied", "invalid request", "file not found",
    "syntax error", "assertion", "test failure", "no such file"

See also:
    make classify-error MSG='...'
    AGENTS.md § "CRITICAL: Transient API Errors Are Retry Signals"
"""

from __future__ import annotations

import sys


# TRANSIENT: these errors are ephemeral — retry with exponential backoff.
_TRANSIENT_MARKERS: tuple[str, ...] = (
    "api error",
    "overloaded",
    "rate limit",
    "429",
    "529",
    "503",
    "500",
    "timeout",
    "connection",
    "stream watchdog",
    "session limit",
    "temporarily",
    "try again",
)

# TERMINAL: these errors indicate a structural problem — fix the root cause,
# do NOT retry.  These override TRANSIENT if both are present.
_TERMINAL_MARKERS: tuple[str, ...] = (
    "auth",
    "permission denied",
    "invalid request",
    "file not found",
    "syntax error",
    "assertion",
    "test failure",
    "no such file",
)


def classify(text: str) -> str:
    """
    Return 'transient' or 'terminal' for the given error/result string.

    TERMINAL markers override TRANSIENT if both appear in the same text.
    Unknown errors return 'terminal' (fail-closed: unknowns don't get retried).
    """
    lower = text.lower()

    # TERMINAL check first — overrides transient
    for marker in _TERMINAL_MARKERS:
        if marker in lower:
            return "terminal"

    # TRANSIENT check
    for marker in _TRANSIENT_MARKERS:
        if marker in lower:
            return "transient"

    # Fail-closed default
    return "terminal"


def main(argv: list[str]) -> int:
    """CLI entry point. Returns exit code (0=transient, 1=terminal)."""
    if len(argv) > 1:
        text = argv[1]
    else:
        text = sys.stdin.read()

    verdict = classify(text)
    print(verdict)
    return 0 if verdict == "transient" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
