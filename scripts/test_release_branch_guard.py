#!/usr/bin/env python3
"""
Test harness for scripts/check_green_branch_guard.py.

Mirrors the structure of scripts/test_no_wait_hook.py:
each case sets override env vars, calls the script as a subprocess, and
checks the exit code (0=allow, 1=block, 2=inconclusive).

Cases:
  1 — GREEN remote + HEAD ahead  -> exit 1 (BLOCKED)
  2 — RED remote + HEAD ahead    -> exit 0 (ALLOWED)
  3 — No remote branch (new)     -> exit 0 (ALLOWED)
  4 — GREEN remote + HEAD same   -> exit 0 (ALLOWED — no new commits)
  5 — PENDING remote + HEAD ahead-> exit 0 (ALLOWED — not green)
  6 — Missing --branch arg       -> exit 2 (error, not a block)
"""
from __future__ import annotations

import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_green_branch_guard.py")
FAKE_REMOTE_SHA = "abc123def456"
FAKE_LOCAL_SHA = "fedcba987654"


def run_guard(branch: str | None, remote_sha: str, ci_verdict: str, ahead: int) -> int:
    """Run the guard script with override env vars; return exit code."""
    env = {
        **os.environ,
        "GLUDD_GUARD_REMOTE_SHA_OVERRIDE": remote_sha,
        "GLUDD_GUARD_CI_VERDICT_OVERRIDE": ci_verdict,
        "GLUDD_GUARD_HEAD_SHA_OVERRIDE": FAKE_LOCAL_SHA,
        "GLUDD_GUARD_AHEAD_OVERRIDE": str(ahead),
    }
    cmd = ["python3", SCRIPT]
    if branch is not None:
        cmd += ["--branch", branch]
    result = subprocess.run(cmd, capture_output=True, env=env)
    return result.returncode


cases: list[tuple[int, int, int]] = []  # (case_id, expected_exit, got_exit)


def _case(case_id: int, description: str, expected: int,
          branch: str | None, remote_sha: str, ci_verdict: str, ahead: int) -> None:
    got = run_guard(branch, remote_sha, ci_verdict, ahead)
    result = "PASS" if got == expected else "FAIL"
    label = "BLOCKED" if expected == 1 else ("ERROR" if expected == 2 else "ALLOWED")
    print(f"Case {case_id:<2} | expected=exit{expected} ({label:<7}) | got=exit{got} | {result}  # {description}")
    cases.append((case_id, expected, got))


# Case 1: GREEN remote, HEAD is ahead (+3 commits) -> BLOCKED (exit 1)
_case(1, "GREEN remote + HEAD ahead -> BLOCKED",
      expected=1, branch="release/v1.0", remote_sha=FAKE_REMOTE_SHA,
      ci_verdict="GREEN", ahead=3)

# Case 2: RED remote, HEAD is ahead (+3 commits) -> ALLOWED (exit 0)
_case(2, "RED remote + HEAD ahead -> ALLOWED",
      expected=0, branch="release/v1.0", remote_sha=FAKE_REMOTE_SHA,
      ci_verdict="RED", ahead=3)

# Case 3: No remote branch (empty SHA) -> ALLOWED (exit 0)
_case(3, "No remote branch (new branch) -> ALLOWED",
      expected=0, branch="release/v2.0", remote_sha="",
      ci_verdict="NONE", ahead=5)

# Case 4: GREEN remote, HEAD same tip (0 commits ahead) -> ALLOWED (exit 0)
_case(4, "GREEN remote + HEAD same SHA -> ALLOWED",
      expected=0, branch="release/v1.0", remote_sha=FAKE_REMOTE_SHA,
      ci_verdict="GREEN", ahead=0)

# Case 5: PENDING remote, HEAD is ahead -> ALLOWED (exit 0)
_case(5, "PENDING remote + HEAD ahead -> ALLOWED",
      expected=0, branch="release/v1.0", remote_sha=FAKE_REMOTE_SHA,
      ci_verdict="PENDING", ahead=2)

# Case 6: Missing --branch argument -> exit 2 (error, not a block)
_case(6, "Missing --branch arg -> exit 2 (inconclusive)",
      expected=2, branch=None, remote_sha=FAKE_REMOTE_SHA,
      ci_verdict="GREEN", ahead=1)

# Summary
failures = [(cid, exp, got) for cid, exp, got in cases if exp != got]
passed = len(cases) - len(failures)
print(f"--- Results: {passed} passed, {len(failures)} failed ---")
if failures:
    for cid, exp, got in failures:
        print(f"  FAIL case {cid}: expected exit{exp}, got exit{got}")
sys.exit(0 if not failures else 1)
