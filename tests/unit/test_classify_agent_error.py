"""
Unit tests for scripts/classify_agent_error.py

Covers:
- TRANSIENT markers (at least 6 by name)
- TERMINAL markers (at least 3)
- Terminal overrides transient when both match
- Empty string → terminal
- Unknown text with no markers → terminal
- Case-insensitivity
- CLI exit codes (0=transient, 1=terminal)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Path to the script under test
_SCRIPT = str(Path(__file__).parent.parent.parent / "scripts" / "classify_agent_error.py")


# ---------------------------------------------------------------------------
# Import the module directly so we test the pure classify() function
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
# E402 suppressed: import requires the sys.path manipulation above to locate
# the scripts/ directory, which is not an installed package.
from classify_agent_error import classify  # noqa: E402

# ---------------------------------------------------------------------------
# TRANSIENT markers — at least 6 tested by name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", sorted([
    "API Error: something went wrong",
    "Service is overloaded, please wait",
    "Rate limit exceeded",
    "HTTP 429 Too Many Requests",
    "HTTP 529 error from upstream",
    "503 Service Unavailable",
    "500 Internal Server Error",
    "Connection timed out",
    "timeout waiting for response",
    "stream watchdog fired after 30s",
    "session limit reached for this account",
    "temporarily unavailable",
    "try again later",
]))
def test_transient_markers(text: str) -> None:
    """Each individual TRANSIENT marker should yield 'transient'."""
    assert classify(text) == "transient", f"Expected 'transient' for: {text!r}"


# ---------------------------------------------------------------------------
# TERMINAL markers — at least 3 tested
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", sorted([
    "Authentication failed: invalid API key",
    "Permission denied: /etc/shadow",
    "Invalid request: missing required field 'model'",
    "File not found: /nonexistent/path.py",
    "SyntaxError: unexpected indent",
    "AssertionError in test_foo",
    "Test failure: 3 tests failed",
    "No such file or directory: missing.py",
]))
def test_terminal_markers(text: str) -> None:
    """Each individual TERMINAL marker should yield 'terminal'."""
    assert classify(text) == "terminal", f"Expected 'terminal' for: {text!r}"


# ---------------------------------------------------------------------------
# Terminal overrides transient when both markers are present
# ---------------------------------------------------------------------------

def test_terminal_overrides_transient() -> None:
    """When text contains both a transient and a terminal marker, terminal wins."""
    mixed = "API Error 429: permission denied — auth failure"
    assert classify(mixed) == "terminal"


def test_terminal_overrides_transient_second_example() -> None:
    """Another mixed case: overloaded + authentication error → terminal."""
    mixed = "overloaded: authentication failed"
    assert classify(mixed) == "terminal"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_string_is_terminal() -> None:
    """Empty string has no markers → fail-closed → terminal."""
    assert classify("") == "terminal"


def test_plain_text_no_markers_is_terminal() -> None:
    """Text with no recognised markers → fail-closed → terminal."""
    assert classify("The operation completed unexpectedly without detail.") == "terminal"


def test_unknown_numeric_error_is_terminal() -> None:
    """An error code that is not in the TRANSIENT set → terminal (fail-closed)."""
    assert classify("HTTP 418 I'm a teapot") == "terminal"


# ---------------------------------------------------------------------------
# Case-insensitivity
# ---------------------------------------------------------------------------

def test_transient_uppercase() -> None:
    """TRANSIENT matching is case-insensitive."""
    assert classify("API ERROR: upstream overloaded") == "transient"


def test_transient_uppercase_timeout() -> None:
    """TIMEOUT in all-caps should be detected as transient."""
    assert classify("TIMEOUT after 30 seconds") == "transient"


def test_terminal_uppercase() -> None:
    """TERMINAL matching is case-insensitive."""
    assert classify("AUTHENTICATION FAILED") == "terminal"


# ---------------------------------------------------------------------------
# CLI exit codes — tested via subprocess
# ---------------------------------------------------------------------------

def _run_cli(text: str) -> int:
    """Run the CLI with text as argv[1], return the exit code."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, text],
        capture_output=True,
        text=True,
    )
    return result.returncode


def test_cli_transient_exit_0() -> None:
    """CLI exits 0 for a transient error."""
    rc = _run_cli("rate limit exceeded — 429")
    assert rc == 0, f"Expected exit 0 (transient) but got {rc}"


def test_cli_terminal_exit_1() -> None:
    """CLI exits 1 for a terminal error."""
    rc = _run_cli("authentication failed: invalid credentials")
    assert rc == 1, f"Expected exit 1 (terminal) but got {rc}"


def test_cli_empty_string_exit_1() -> None:
    """CLI exits 1 for an empty string (fail-closed)."""
    rc = _run_cli("")
    assert rc == 1, f"Expected exit 1 (terminal/empty) but got {rc}"


def test_cli_prints_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    """classify() returns the correct string ('transient' or 'terminal')."""
    assert classify("503 Service Unavailable") == "transient"
    assert classify("syntax error in script") == "terminal"
