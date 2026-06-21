#!/usr/bin/env python3
"""Test harness for .claude/hooks/no_wait_stop.sh — 6 cases."""
import subprocess, json, os, tempfile, sys

HOOK = "/Users/shawnwilson/gludd/.claude/hooks/no_wait_stop.sh"


def make_jsonl(path, msg):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "human", "message": {"content": [{"type": "text", "text": "go"}]}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": msg}]}}) + "\n")


def run_hook(payload_str):
    r = subprocess.run(["bash", HOOK], input=payload_str.encode(), capture_output=True)
    out = r.stdout.decode()
    return "BLOCK" if '"decision"' in out and '"block"' in out else "NOBLOCK"


cases = []
with tempfile.TemporaryDirectory(prefix="hook_test_") as d:
    # Case 1: "Say so and I'll proceed." -> BLOCK (matches \bsay so\b)
    p1 = os.path.join(d, "t1.jsonl")
    make_jsonl(p1, "Say so and I'll proceed.")
    cases.append((1, "BLOCK", run_hook(json.dumps({"transcript_path": p1}))))

    # Case 2: "if you want me to proceed, say so" -> BLOCK (\bwant me to\b or \bsay so\b)
    p2 = os.path.join(d, "t2.jsonl")
    make_jsonl(p2, "if you want me to proceed, say so")
    cases.append((2, "BLOCK", run_hook(json.dumps({"transcript_path": p2}))))

    # Case 3: "I'll hold here." -> BLOCK (matches \bi'?ll hold\b)
    p3 = os.path.join(d, "t3.jsonl")
    make_jsonl(p3, "I'll hold here.")
    cases.append((3, "BLOCK", run_hook(json.dumps({"transcript_path": p3}))))

    # Case 4: clean done message -> NOBLOCK
    p4 = os.path.join(d, "t4.jsonl")
    make_jsonl(p4, "Done — pushed d461919 and CI is green.")
    cases.append((4, "NOBLOCK", run_hook(json.dumps({"transcript_path": p4}))))

    # Case 5: stop_hook_active:true (anti-wedge) -> NOBLOCK even with deferral text
    p5 = os.path.join(d, "t5.jsonl")
    make_jsonl(p5, "Say so and I'll proceed.")
    cases.append((5, "NOBLOCK", run_hook(json.dumps({"transcript_path": p5, "stop_hook_active": True}))))

    # Case 6: empty payload -> NOBLOCK
    cases.append((6, "NOBLOCK", run_hook("{}")))

fail = 0
for num, exp, got in cases:
    result = "PASS" if got == exp else "FAIL"
    if result == "FAIL":
        fail += 1
    print(f"Case {num:<2} | expected={exp:<7} | got={got:<7} | {result}")

passed = len(cases) - fail
print(f"--- Results: {passed} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
