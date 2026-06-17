"""Tests for the main-thread delegation-budget hook (.claude/hooks/mainthread_budget.sh).

The hook is a FORCING FUNCTION: it counts consecutive main-thread tool calls
(Read/Edit/Write/Bash/...) since the last delegation (Agent/Workflow/Task) and,
once that streak is long AND the live-subagent floor is below target, emits an
advisory "MAIN-THREAD BUDGET" message telling the orchestrator to delegate. Every
delegation resets the streak. It is ADVISORY (additionalContext), never a block,
and FAIL-OPEN on any error (exit 0, no output).

These tests drive the real shell hook via subprocess, piping hook JSON on stdin,
exactly as the Claude Code harness invokes it. Determinism comes from two seams:
  * GLUDD_MAINTHREAD_STREAK_FILE -> a per-test tmp file (isolates the counter).
  * FLOOR_LIVE_OVERRIDE          -> forces scripts/agent_liveness.py --count to
                                    print a fixed integer (the hook cd's to the
                                    main repo to run that script; the env var is
                                    inherited by that child process).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# repo root = .../tests/unit/<this file> -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "mainthread_budget.sh"

# The PreToolUse branch cd's to the MAIN repo to run the liveness counter. That
# script must exist for the live-check (and its FLOOR_LIVE_OVERRIDE seam) to work.
_MAIN_REPO_LIVENESS = Path("/Users/shawnwilson/gludd/scripts/agent_liveness.py")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None
    or not HOOK_PATH.exists()
    or not _MAIN_REPO_LIVENESS.exists(),
    reason="bash, the hook, or the main-repo liveness script is unavailable",
)


def run_hook(
    event: str | None,
    tool: str | None,
    *,
    streak_file: Path,
    env_extra: dict[str, str] | None = None,
    raw_input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook via bash, piping hook JSON on stdin.

    Pass raw_input to send a malformed (non-JSON) payload; otherwise a JSON
    object is built from event/tool (a None field is simply omitted).
    """
    if raw_input is not None:
        payload = raw_input
    else:
        d: dict[str, str] = {}
        if event is not None:
            d["hook_event_name"] = event
        if tool is not None:
            d["tool_name"] = tool
        payload = json.dumps(d)

    env = dict(os.environ)
    env["GLUDD_MAINTHREAD_STREAK_FILE"] = str(streak_file)
    # Keep the live-probe cheap regardless of defaults.
    env.setdefault("FLOOR_PROBE_SECS", "0")
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


# --------------------------------------------------------------------------- #
# PostToolUse: streak accounting                                              #
# --------------------------------------------------------------------------- #
def test_posttool_read_increments(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    streak.write_text("3")
    proc = run_hook("PostToolUse", "Read", streak_file=streak)
    assert proc.returncode == 0
    assert streak.read_text().strip() == "4"


def test_posttool_read_from_empty_starts_at_one(tmp_path: Path) -> None:
    streak = tmp_path / "streak"  # intentionally absent
    proc = run_hook("PostToolUse", "Read", streak_file=streak)
    assert proc.returncode == 0
    assert streak.read_text().strip() == "1"


@pytest.mark.parametrize("tool", ["Edit", "Write", "Bash", "Grep", "Glob"])
def test_posttool_all_mainthread_tools_increment(tmp_path: Path, tool: str) -> None:
    streak = tmp_path / "streak"
    streak.write_text("5")
    proc = run_hook("PostToolUse", tool, streak_file=streak)
    assert proc.returncode == 0
    assert streak.read_text().strip() == "6"


@pytest.mark.parametrize("tool", ["Agent", "Workflow", "Task", "SendMessage"])
def test_posttool_delegation_resets_to_zero(tmp_path: Path, tool: str) -> None:
    streak = tmp_path / "streak"
    streak.write_text("7")
    proc = run_hook("PostToolUse", tool, streak_file=streak)
    assert proc.returncode == 0
    assert streak.read_text().strip() == "0"


# --------------------------------------------------------------------------- #
# PreToolUse: escalation when over budget AND floor below target              #
# --------------------------------------------------------------------------- #
def test_pretool_over_budget_emits_context(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    streak.write_text("8")  # == THRESHOLD
    proc = run_hook(
        "PreToolUse",
        "Read",
        streak_file=streak,
        env_extra={
            "GLUDD_MAINTHREAD_THRESHOLD": "8",
            "CLAUDE_AGENT_TARGET": "10",
            "FLOOR_LIVE_OVERRIDE": "0",  # floor far below target -> nag
        },
    )
    assert proc.returncode == 0
    assert "MAIN-THREAD BUDGET" in proc.stdout
    payload = json.loads(proc.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "MAIN-THREAD BUDGET" in ctx
    # Re-armed to THRESHOLD - 3 so it fires again only after a few more calls.
    assert streak.read_text().strip() == "5"


def test_pretool_floor_healthy_is_silent(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    streak.write_text("8")
    proc = run_hook(
        "PreToolUse",
        "Read",
        streak_file=streak,
        env_extra={
            "GLUDD_MAINTHREAD_THRESHOLD": "8",
            "CLAUDE_AGENT_TARGET": "10",
            "FLOOR_LIVE_OVERRIDE": "20",  # live >= target -> inline work is fine
        },
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    # Streak untouched when we don't nag.
    assert streak.read_text().strip() == "8"


def test_pretool_under_threshold_silent(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    streak.write_text("2")  # well below THRESHOLD
    proc = run_hook(
        "PreToolUse",
        "Read",
        streak_file=streak,
        env_extra={
            "GLUDD_MAINTHREAD_THRESHOLD": "8",
            "CLAUDE_AGENT_TARGET": "10",
            "FLOOR_LIVE_OVERRIDE": "0",
        },
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert streak.read_text().strip() == "2"


def test_pretool_delegate_tool_is_silent(tmp_path: Path) -> None:
    # PreToolUse for a delegation tool is not a main-thread call -> no nag even
    # over budget; the hook only escalates before INLINE work.
    streak = tmp_path / "streak"
    streak.write_text("8")
    proc = run_hook(
        "PreToolUse",
        "Agent",
        streak_file=streak,
        env_extra={
            "GLUDD_MAINTHREAD_THRESHOLD": "8",
            "CLAUDE_AGENT_TARGET": "10",
            "FLOOR_LIVE_OVERRIDE": "0",
        },
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# Fail-open: malformed / unknown / empty input                               #
# --------------------------------------------------------------------------- #
def test_malformed_json_silent(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    proc = run_hook(None, None, streak_file=streak, raw_input="{ this is not json")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    # No tool parsed -> nothing written.
    assert not streak.exists()


def test_unknown_tool_silent_and_no_write(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    proc = run_hook("PostToolUse", "SomethingWeird", streak_file=streak)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    # "other"-class tool neither increments nor resets the counter.
    assert not streak.exists()


def test_empty_tool_silent(tmp_path: Path) -> None:
    streak = tmp_path / "streak"
    proc = run_hook("PostToolUse", None, streak_file=streak)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert not streak.exists()
