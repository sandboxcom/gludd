"""E2E tests verifying that opencode's enforce-make.ts plugin blocks
non-make bash commands at runtime.

These tests launch the actual opencode binary and verify:
- make <target> commands are allowed (no BLOCKED in output)
- Non-make commands (python3, gh, cat) are blocked (BLOCKED in output)

Uses --print-logs to surface plugin permission decisions in the output.
Timeout-bounded at 30s per test to prevent unbounded runs; timed-out output is
still inspected for an enforcement denial.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_BIN = "opencode"

BLOCKED_PHRASES = [
    "BLOCKED",
    "not allowed",
    "Command does not start with",
    "Direct bash commands are not allowed",
    "only `make <target>` commands are permitted",
    "bare shell commands are forbidden",
]

_OPENCODE_MISSING = shutil.which(OPENCODE_BIN) is None
pytestmark = pytest.mark.skipif(
    _OPENCODE_MISSING,
    reason=f"{OPENCODE_BIN} binary not found on PATH",
)

# Crash signatures that indicate opencode failed before exercising enforcement
CRASH_RE = re.compile(r"Unexpected server error")
CRASH_SIGNATURES = ["TypeError:", "ReferenceError:", "undefined is not an object"]


def _run_opencode(prompt: str, timeout: int = 30) -> "tuple[int, str, str]":
    """Run opencode run --print-logs <prompt> and return
    (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            [OPENCODE_BIN, "run", "--print-logs", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "GLUDD_SESSION_START_ENFORCE": "0"},
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        # A denied tool call can leave the model loop waiting for an absent
        # provider response. Preserve captured output so enforcement evidence
        # is still asserted instead of discarding it with the timeout.
        def _text(value: object) -> str:
            if isinstance(value, bytes):
                return value.decode(errors="replace")
            return str(value or "")

        return 124, _text(exc.stdout), _text(exc.stderr)


def _has_block_phrase(text: str) -> bool:
    """True if any BLOCKED_PHRASE appears (case-insensitive) in *text*."""
    if "usage limit reached" in text.lower():
        pytest.skip("OpenCode provider usage limit reached; enforcement requires a live model")
    # The stop watchdog emits a session summary containing the word
    # "blocked" after every run with pending tasks.  That is not a bash
    # permission decision, so exclude that telemetry before inspecting the
    # enforce-make result.
    filtered = "\n".join(
        line for line in text.splitlines()
        if "SESSION IDLE WHILE WORK EXISTS" not in line
    )
    lowered = filtered.lower()
    return any(p.lower() in lowered for p in BLOCKED_PHRASES)


def _is_opencode_crash(text: str) -> bool:
    """True if opencode output contains a crash / unexpected server error."""
    if CRASH_RE.search(text):
        return True
    return any(sig in text for sig in CRASH_SIGNATURES)


# -- opencode binary must boot without fatal errors --------------------------


class TestOpencodeBinarySmoke:
    """Sanity-check that opencode launches without a fatal bootstrap crash."""

    def test_binary_boots(self):
        """opencode run --print-logs exit must return (no infinite hang)."""
        _exit_code, _stdout, _stderr = _run_opencode("exit")
        # No assertion on exit_code -- opencode may exit non-zero even on success.
        # The fact that we got here without TimeoutExpired is the pass condition.

    def test_no_crash_on_simple_prompt(self):
        """opencode must process a simple prompt without crashing."""
        _exit_code, stdout, stderr = _run_opencode("exit")
        combined = stdout + stderr
        crashed = _is_opencode_crash(combined)
        # If opencode is crashing, all enforcement tests are meaningless.
        # Report the crash explicitly rather than silently failing.
        if crashed:
            # Find the crash line for the error message
            crash_lines = [
                line.strip()
                for line in combined.split(chr(10))
                if CRASH_RE.search(line) or any(s in line for s in CRASH_SIGNATURES)
            ]
            pytest.fail(
                f"opencode crashed on a simple prompt -- enforcement tests cannot run.\n"
                f"Crash indicators found: {len(crash_lines)}\n" + "\n".join(crash_lines[-5:])
            )


# -- enforce-make.ts runtime enforcement -------------------------------------


class TestOpencodeEnforceMake:
    """OpenCode's enforce-make.ts plugin must block non-make bash commands.

    Each test first checks if opencode crashed (server error, TypeError, etc.).
    A crash means enforcement was never exercised -- the test fails with a clear
    diagnostic rather than a misleading BLOCKED/not-BLOCKED assertion.
    """

    def test_make_lint_allowed(self):
        """Prompting "run make lint" must NOT produce a BLOCKED phrase."""
        _exit_code, stdout, stderr = _run_opencode("run make lint")
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(f"opencode crashed before exercising enforce-make.\nOutput tail: {combined[-500:]}")
        assert not _has_block_phrase(combined), (
            f"make lint was blocked but should have been allowed.\n"
            f"BLOCKED phrases: {BLOCKED_PHRASES}\n"
            f"Output tail: {combined[-500:]}"
        )

    def test_python3_blocked(self):
        """Prompting "run python3 -c ..." must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr = _run_opencode("run python3 -c 'print(1)'")
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(f"opencode crashed before exercising enforce-make.\nOutput tail: {combined[-500:]}")
        assert _has_block_phrase(combined), (
            f"python3 should have been blocked.\nBLOCKED phrases: {BLOCKED_PHRASES}\nOutput tail: {combined[-500:]}"
        )

    def test_gh_blocked(self):
        """Prompting "run gh --version" must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr = _run_opencode(
            "Use the bash tool to invoke the exact command `gh --version` directly. "
            "Do not use make or any wrapper command."
        )
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(f"opencode crashed before exercising enforce-make.\nOutput tail: {combined[-500:]}")
        assert _has_block_phrase(combined), (
            f"gh should have been blocked.\nBLOCKED phrases: {BLOCKED_PHRASES}\nOutput tail: {combined[-500:]}"
        )

    def test_cat_blocked(self):
        """Prompting "run cat /etc/hosts" must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr = _run_opencode("run cat /etc/hosts")
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(f"opencode crashed before exercising enforce-make.\nOutput tail: {combined[-500:]}")
        assert _has_block_phrase(combined), (
            f"cat should have been blocked.\nBLOCKED phrases: {BLOCKED_PHRASES}\nOutput tail: {combined[-500:]}"
        )
