"""E2E tests verifying that opencode's enforce-make.ts plugin blocks
non-make bash commands at runtime.

These tests launch the actual opencode binary and verify:
- make <target> commands are allowed (no enforce-make denial in output)
- Non-make commands (python3, gh, cat) produce enforce-make's exact denial

Uses --print-logs to surface plugin permission decisions in the output.
Timeout-bounded at 30s per test to prevent unbounded runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.test_opencode_tui_permissions import DeterministicProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_BIN = "opencode"

BLOCKED_PHRASES = [
    "BLOCKED: Direct bash commands are not allowed in this project.",
    "Command does not start with 'make'",
]

_OPENCODE_MISSING = shutil.which(OPENCODE_BIN) is None
pytestmark = [
    pytest.mark.skipif(
        _OPENCODE_MISSING,
        reason=f"{OPENCODE_BIN} binary not found on PATH",
    ),
    pytest.mark.xdist_group("opencode-live"),
]

# Crash signatures that indicate opencode failed before exercising enforcement
CRASH_RE = re.compile(r"Unexpected server error")
CRASH_SIGNATURES = ["TypeError:", "ReferenceError:", "undefined is not an object"]


def _tool_response(command: str) -> list[dict[str, Any]]:
    return [
        DeterministicProvider.tool_call(
            "bash",
            {"command": command, "description": f"E2E run {command}"},
            "call_enforce_make",
        ),
        {"text": "The deterministic command attempt completed."},
    ]


def _run_opencode(
    prompt: str,
    timeout: int = 30,
    forced_command: str | None = None,
) -> tuple[int, str, str, str]:
    """Run opencode run --print-logs <prompt> and return
    (exit_code, stdout, stderr, tool-result transcript)."""
    responses = (
        _tool_response(forced_command)
        if forced_command is not None
        else [{"text": "The deterministic smoke prompt completed."}]
    )
    provider = DeterministicProvider(responses=responses)
    env = os.environ.copy()
    # This suite exercises the root-session guardrail itself. Never inherit a
    # parent harness's delegated-agent bypass or an enforcement-disable toggle.
    env["OPENCODE_SUBAGENT"] = ""
    env["GLUDD_MAKE_ENFORCE"] = "1"
    env["GLUDD_SESSION_START_ENFORCE"] = "0"
    env["OPENCODE_CONFIG_CONTENT"] = provider.config_content
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    try:
        result = subprocess.run(
            [OPENCODE_BIN, "run", "--print-logs", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        assert provider.main_calls == len(responses), (
            "OpenCode did not complete the deterministic tool loop"
        )
        tool_results = [
            message.get("content", "")
            for request in provider.requests
            for message in request.get("messages", [])
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        return (
            result.returncode,
            result.stdout,
            result.stderr,
            json.dumps(tool_results),
        )
    finally:
        provider.close()


def _has_block_phrase(text: str) -> bool:
    """True only when enforce-make's stable denial text appears in *text*."""
    lowered = text.lower()
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
        _exit_code, _stdout, _stderr, _tool_results = _run_opencode("exit")
        # No assertion on exit_code -- opencode may exit non-zero even on success.
        # The fact that we got here without TimeoutExpired is the pass condition.

    def test_no_crash_on_simple_prompt(self):
        """opencode must process a simple prompt without crashing."""
        _exit_code, stdout, stderr, _tool_results = _run_opencode("exit")
        combined = stdout + stderr
        crashed = _is_opencode_crash(combined)
        # If opencode is crashing, all enforcement tests are meaningless.
        # Report the crash explicitly rather than silently failing.
        if crashed:
            # Find the crash line for the error message
            crash_lines = [
                line.strip() for line in combined.split(chr(10))
                if CRASH_RE.search(line) or any(s in line for s in CRASH_SIGNATURES)
            ]
            pytest.fail(
                f"opencode crashed on a simple prompt -- enforcement tests cannot run.\n"
                f"Crash indicators found: {len(crash_lines)}\n"
                + "\n".join(crash_lines[-5:])
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
        _exit_code, stdout, stderr, tool_results = _run_opencode(
            "run make lint",
            forced_command="make lint",
        )
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(
                f"opencode crashed before exercising enforce-make.\n"
                f"Output tail: {combined[-500:]}"
            )
        assert tool_results != "[]", "make lint never produced a tool result"
        assert not _has_block_phrase(tool_results), (
            f"make lint was blocked but should have been allowed.\n"
            f"BLOCKED phrases: {BLOCKED_PHRASES}\n"
            f"Tool results: {tool_results[-1000:]}"
        )

    def test_python3_blocked(self):
        """Prompting "run python3 -c ..." must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr, tool_results = _run_opencode(
            "run python3 -c 'print(1)'",
            forced_command="python3 -c 'print(1)'",
        )
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(
                f"opencode crashed before exercising enforce-make.\n"
                f"Output tail: {combined[-500:]}"
            )
        assert _has_block_phrase(tool_results), (
            f"python3 should have been blocked.\n"
            f"BLOCKED phrases: {BLOCKED_PHRASES}\n"
            f"Tool results: {tool_results[-1000:]}\n"
            f"Output tail: {combined[-500:]}"
        )

    def test_gh_blocked(self):
        """Prompting "run gh --version" must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr, tool_results = _run_opencode(
            "run gh --version",
            forced_command="gh --version",
        )
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(
                f"opencode crashed before exercising enforce-make.\n"
                f"Output tail: {combined[-500:]}"
            )
        assert _has_block_phrase(tool_results), (
            f"gh should have been blocked.\n"
            f"BLOCKED phrases: {BLOCKED_PHRASES}\n"
            f"Tool results: {tool_results[-1000:]}\n"
            f"Output tail: {combined[-500:]}"
        )

    def test_cat_blocked(self):
        """Prompting "run cat /etc/hosts" must produce a BLOCKED phrase."""
        _exit_code, stdout, stderr, tool_results = _run_opencode(
            "run cat /etc/hosts",
            forced_command="cat /etc/hosts",
        )
        combined = stdout + stderr
        if _is_opencode_crash(combined):
            pytest.fail(
                f"opencode crashed before exercising enforce-make.\n"
                f"Output tail: {combined[-500:]}"
            )
        assert _has_block_phrase(tool_results), (
            f"cat should have been blocked.\n"
            f"BLOCKED phrases: {BLOCKED_PHRASES}\n"
            f"Tool results: {tool_results[-1000:]}\n"
            f"Output tail: {combined[-500:]}"
        )
