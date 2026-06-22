#!/usr/bin/env python3
"""Test harness for .claude/hooks/force_delegate_pretool.sh.

Tests: default-off, allowlist (Read/Agent/Workflow/Glob/git-status/memory-Write),
targeted deny after grace, Agent dispatch resets counter, bounded escape fails open.
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = "/Users/shawnwilson/gludd/.claude/hooks/force_delegate_pretool.sh"

BASE_ENV = {
    **os.environ,
    "GLUDD_FORCE_DELEGATE": "1",
    "FLOOR_LIVE_OVERRIDE": "0",        # live=0, below floor=6
    "CLAUDE_AGENT_FLOOR": "6",
    "GLUDD_FORCE_DELEGATE_GRACE": "3",
    "GLUDD_FORCE_DELEGATE_MAXBLOCK": "4",
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


def make_payload(tool_name: str, **tool_input) -> dict:
    return {"tool_name": tool_name, "tool_input": tool_input}


def state_env(n: int) -> dict:
    """Return env with a unique per-test state file."""
    return {"GLUDD_FORCE_DELEGATE_STATE": f"/tmp/gludd-fdtest-{n}.json"}


# Clean up any stale state files from previous runs
for i in range(30):
    try:
        os.remove(f"/tmp/gludd-fdtest-{i}.json")
    except OSError:
        pass


cases: list[tuple[int | str, str, str]] = []


# ---------- Case 1: DEFAULT-OFF — without GLUDD_FORCE_DELEGATE=1, always ALLOW ----------
env_off = {**BASE_ENV, **state_env(1)}
env_off.pop("GLUDD_FORCE_DELEGATE", None)
result1 = run_hook(make_payload("Edit", file_path="/some/file.py"), env_off)
cases.append((1, "ALLOW", result1))

# ---------- Case 2: Read tool — always ALLOW ----------
cases.append((2, "ALLOW", run_hook(
    make_payload("Read", file_path="/some/file.py"),
    {**state_env(2)},
)))

# ---------- Case 3: Agent tool — always ALLOW + resets counter ----------
# Sub-case 3a: Agent itself is ALLOW
se3 = state_env(3)
cases.append(("3a", "ALLOW", run_hook(make_payload("Agent", prompt="do stuff"), se3)))

# Sub-case 3b: Do GRACE targeted calls (should not deny within grace), then Agent, then 2 more — should not deny
se3b = {**state_env(13), "GLUDD_FORCE_DELEGATE_GRACE": "2"}  # grace=2 to get close faster
for _ in range(2):  # fill up to grace
    run_hook(make_payload("Edit", file_path="/f.py"), se3b)
# Now Agent — resets counter
agent_result = run_hook(make_payload("Agent", prompt="delegate"), se3b)
cases.append(("3b_agent", "ALLOW", agent_result))
# 2 more targeted calls — should allow because counter was reset
r_after1 = run_hook(make_payload("Edit", file_path="/f.py"), se3b)
r_after2 = run_hook(make_payload("Edit", file_path="/f.py"), se3b)
cases.append(("3b_after1", "ALLOW", r_after1))
cases.append(("3b_after2", "ALLOW", r_after2))

# ---------- Case 4: Workflow tool — always ALLOW ----------
cases.append((4, "ALLOW", run_hook(make_payload("Workflow", prompt="parallel work"), state_env(4))))

# ---------- Case 5: Glob tool — always ALLOW ----------
cases.append((5, "ALLOW", run_hook(make_payload("Glob", pattern="**/*.py"), state_env(5))))

# ---------- Case 6: Read-only Bash (make git-status) — always ALLOW ----------
cases.append((6, "ALLOW", run_hook(make_payload("Bash", command="make git-status"), state_env(6))))

# ---------- Case 7: Read-only Bash (make lint) — always ALLOW ----------
cases.append((7, "ALLOW", run_hook(make_payload("Bash", command="make lint"), state_env(7))))

# ---------- Case 8: Read-only Bash (make ci-verdict) — always ALLOW ----------
cases.append((8, "ALLOW", run_hook(make_payload("Bash", command="make ci-verdict BRANCH=main"), state_env(8))))

# ---------- Case 9: Memory-path Write — always ALLOW ----------
memory_path = os.path.expanduser("~/.claude/projects/-Users-shawnwilson-gludd/memory/MEMORY.md")
cases.append((9, "ALLOW", run_hook(make_payload("Write", file_path=memory_path), state_env(9))))

# ---------- Case 10: Mutating Edit — exceed grace → DENY ----------
# GRACE=3, so 4th targeted call should DENY (with live=0 < floor=6)
se10 = state_env(10)
results10 = []
for _ in range(4):  # GRACE+1 = 4 calls
    results10.append(run_hook(make_payload("Edit", file_path="/src/foo.py"), se10))
cases.append((10, "DENY", results10[-1]))  # 4th call should DENY

# ---------- Case 11: Mutating Bash (make git-commit) — exceed grace → DENY ----------
se11 = state_env(11)
results11 = []
for i in range(4):
    results11.append(run_hook(make_payload("Bash", command="make git-commit MSG=test"), se11))
cases.append((11, "DENY", results11[-1]))

# ---------- Case 12: Bounded escape — after MAXBLOCK denials → ALLOW ----------
# Use GRACE=0 so every targeted call is deny-eligible immediately
se12 = {**state_env(12), "GLUDD_FORCE_DELEGATE_GRACE": "0", "GLUDD_FORCE_DELEGATE_MAXBLOCK": "4"}
results12 = []
for _ in range(6):  # MAXBLOCK+1+1 to be safe (5th should be fail-open)
    results12.append(run_hook(make_payload("Edit", file_path="/src/bar.py"), se12))
# First 4 → DENY, 5th → ALLOW (fail-open)
cases.append(("12_deny1", "DENY", results12[0]))
cases.append(("12_deny4", "DENY", results12[3]))
cases.append(("12_failopen", "ALLOW", results12[4]))

# ---------- Case 13: live >= floor → never denied ----------
se13 = {**state_env(14), "FLOOR_LIVE_OVERRIDE": "7", "CLAUDE_AGENT_FLOOR": "6",
        "GLUDD_FORCE_DELEGATE_GRACE": "0"}  # grace=0 so floor is the only gate
results13 = []
for _ in range(6):
    results13.append(run_hook(make_payload("Edit", file_path="/src/baz.py"), se13))
cases.append((13, "ALLOW", results13[-1]))  # should always allow when live>=floor

# ---------- Case 14: Write to non-memory path → DENY after grace+1 ----------
se14 = state_env(15)
results14 = []
for _ in range(4):
    results14.append(run_hook(make_payload("Write", file_path="/src/new_file.py"), se14))
cases.append((14, "DENY", results14[-1]))

# ---------- Case 15: GLUDD_FORCE_DELEGATE=0 explicitly → never denies ----------
env15 = {**state_env(16), "GLUDD_FORCE_DELEGATE": "0"}
result15 = run_hook(make_payload("Edit", file_path="/src/foo.py"), env15)
cases.append((15, "ALLOW", result15))

# ---------- Case 16: make git-add targeted → DENY after grace ----------
se16 = state_env(17)
results16 = []
for _ in range(4):
    results16.append(run_hook(make_payload("Bash", command="make git-add FILES=foo.py"), se16))
cases.append((16, "DENY", results16[-1]))

# ---------- Case 17: git-push-branch ALLOWED within grace=5 (first call) ----------
# GLUDD_FORCE_DELEGATE_GRACE=5 means we must exceed 5 targeted calls before deny.
# With a fresh state, the 1st call (consecutive_targeted=1) is well within grace.
se17 = {**state_env(18), "GLUDD_FORCE_DELEGATE_GRACE": "5"}
result17 = run_hook(make_payload("Bash", command="make git-push-branch"), se17)
cases.append((17, "ALLOW", result17))

# ---------- Case 18: git-commit as 5th consecutive call still within grace=5 ----------
# grace=5 means deny only when consecutive_targeted > 5 (i.e., 6th call and beyond).
# Calls 1-5 must all be ALLOW.
se18 = {**state_env(19), "GLUDD_FORCE_DELEGATE_GRACE": "5"}
results18 = []
for _ in range(5):  # exactly at grace boundary — must all ALLOW
    results18.append(run_hook(make_payload("Bash", command="make git-commit MSG=test"), se18))
cases.append((18, "ALLOW", results18[-1]))  # 5th call at grace=5 is still within grace

# ---------- Report ----------
fail = 0
for num, exp, got in cases:
    ok = got == exp
    if not ok:
        fail += 1
    print(f"Case {str(num):<12} | expected={exp:<5} | got={got:<5} | {'PASS' if ok else 'FAIL'}")

passed = len(cases) - fail
print(f"--- Results: {passed} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
