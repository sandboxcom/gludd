#!/usr/bin/env python3
"""Test harness for .claude/hooks/worktree_disk_guard_pretool.sh.

Tests: non-worktree passthrough, healthy worktree allow, over-cap deny,
low-disk deny, combined deny, bad-json fail-open.
"""
import json
import os
import subprocess
import sys

HOOK = "/Users/shawnwilson/gludd/.claude/hooks/worktree_disk_guard_pretool.sh"

BASE_ENV = {
    **os.environ,
    # Defaults: cap=8, min_free=5.0
}


def run_hook(payload: dict, env_overrides: dict | None = None) -> str:
    """Run the hook with payload JSON on stdin. Returns 'DENY' or 'ALLOW'."""
    env = {**BASE_ENV, **(env_overrides or {})}
    r = subprocess.run(
        ["bash", HOOK],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
    )
    out = r.stdout.decode()
    if '"permissionDecision"' in out and '"deny"' in out:
        return "DENY"
    return "ALLOW"


def make_agent_payload(isolation: str | None = None, **kwargs) -> dict:
    tool_input: dict = dict(kwargs)
    if isolation is not None:
        tool_input["isolation"] = isolation
    return {"tool_name": "Agent", "tool_input": tool_input}


cases: list[tuple[int, str, str]] = []

# ---------- Case 1: non-worktree Agent dispatch → ALLOW (always) ----------
# isolation="remote" — not worktree, hook must exit silently
result1 = run_hook(
    make_agent_payload(isolation="remote", prompt="do stuff"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "1.0",   # would trigger deny if it were worktree
        "GLUDD_VENV_COUNT_OVERRIDE": "20",
    },
)
cases.append((1, "ALLOW", result1))

# ---------- Case 2: no isolation field at all → ALLOW (always) ----------
result2 = run_hook(
    {"tool_name": "Agent", "tool_input": {"prompt": "no isolation key"}},
    {
        "GLUDD_DISK_FREE_OVERRIDE": "0.5",
        "GLUDD_VENV_COUNT_OVERRIDE": "15",
    },
)
cases.append((2, "ALLOW", result2))

# ---------- Case 3: worktree dispatch, under cap, enough disk → ALLOW ----------
result3 = run_hook(
    make_agent_payload(isolation="worktree", prompt="safe dispatch"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "20.0",   # 20GB free, well above min 5.0
        "GLUDD_VENV_COUNT_OVERRIDE": "3",     # 3 venvs, well under cap 8
        "GLUDD_WORKTREE_CAP": "8",
        "GLUDD_MIN_FREE_GB": "5.0",
    },
)
cases.append((3, "ALLOW", result3))

# ---------- Case 4: worktree dispatch, over cap (9 > 8) → DENY ----------
result4 = run_hook(
    make_agent_payload(isolation="worktree", prompt="over cap"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "20.0",   # enough disk
        "GLUDD_VENV_COUNT_OVERRIDE": "9",     # 9 > cap 8 → deny
        "GLUDD_WORKTREE_CAP": "8",
        "GLUDD_MIN_FREE_GB": "5.0",
    },
)
cases.append((4, "DENY", result4))

# ---------- Case 5: worktree dispatch, disk too low (2.0 < 5.0) → DENY ----------
result5 = run_hook(
    make_agent_payload(isolation="worktree", prompt="low disk"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "2.0",    # 2.0 < min 5.0 → deny
        "GLUDD_VENV_COUNT_OVERRIDE": "3",     # under cap
        "GLUDD_WORKTREE_CAP": "8",
        "GLUDD_MIN_FREE_GB": "5.0",
    },
)
cases.append((5, "DENY", result5))

# ---------- Case 6: both over cap AND low disk → DENY ----------
result6 = run_hook(
    make_agent_payload(isolation="worktree", prompt="over cap + low disk"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "1.5",    # < 5.0 → deny
        "GLUDD_VENV_COUNT_OVERRIDE": "10",    # > 8 → deny
        "GLUDD_WORKTREE_CAP": "8",
        "GLUDD_MIN_FREE_GB": "5.0",
    },
)
cases.append((6, "DENY", result6))

# ---------- Case 7: bad/empty JSON input → ALLOW (fail-open) ----------
# Run the hook with malformed stdin
r7 = subprocess.run(
    ["bash", HOOK],
    input=b"not json at all {{{",
    capture_output=True,
    env={
        **BASE_ENV,
        "GLUDD_DISK_FREE_OVERRIDE": "1.0",
        "GLUDD_VENV_COUNT_OVERRIDE": "20",
    },
)
out7 = r7.stdout.decode()
result7 = "DENY" if ('"permissionDecision"' in out7 and '"deny"' in out7) else "ALLOW"
cases.append((7, "ALLOW", result7))

# ---------- Case 8: non-worktree dispatch never denied even with bad disk → ALLOW ----------
# isolation explicitly set to something other than "worktree"
result8 = run_hook(
    make_agent_payload(isolation="", prompt="empty isolation string"),
    {
        "GLUDD_DISK_FREE_OVERRIDE": "0.1",    # catastrophically low
        "GLUDD_VENV_COUNT_OVERRIDE": "100",   # way over cap
        "GLUDD_WORKTREE_CAP": "8",
        "GLUDD_MIN_FREE_GB": "5.0",
    },
)
cases.append((8, "ALLOW", result8))

# ---------- Report ----------
fail = 0
for num, exp, got in cases:
    ok = got == exp
    if not ok:
        fail += 1
    print(f"Case {str(num):<4} | expected={exp:<5} | got={got:<5} | {'PASS' if ok else 'FAIL'}")

passed = len(cases) - fail
print(f"--- Results: {passed} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
