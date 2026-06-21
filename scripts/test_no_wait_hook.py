#!/usr/bin/env python3
"""Test harness for .claude/hooks/no_wait_stop.sh.

Covers the deferral patterns, the corrected anti-wedge (a deferral is now blocked
EVEN when stop_hook_active is true -- the old blanket free-pass was the bug that
let a "Want me to push?" turn end), and the bounded consecutive-block safety valve.
"""
import subprocess, json, os, tempfile, sys

HOOK = "/Users/shawnwilson/gludd/.claude/hooks/no_wait_stop.sh"
MAX_CONSECUTIVE_BLOCKS = 25  # must match the hook


def make_jsonl(path, msg):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "human", "message": {"content": [{"type": "text", "text": "go"}]}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": msg}]}}) + "\n")


def run_hook(payload_str):
    env = {**os.environ, "GLUDD_NO_WAIT_ENFORCE": "1"}
    r = subprocess.run(["bash", HOOK], input=payload_str.encode(), capture_output=True, env=env)
    out = r.stdout.decode()
    return "BLOCK" if '"decision"' in out and '"block"' in out else "NOBLOCK"


cases = []
with tempfile.TemporaryDirectory(prefix="hook_test_") as d:
    # Case 1: "Say so and I'll proceed." -> BLOCK (matches \bsay so\b)
    p1 = os.path.join(d, "t1.jsonl")
    make_jsonl(p1, "Say so and I'll proceed.")
    cases.append((1, "BLOCK", run_hook(json.dumps({"transcript_path": p1}))))

    # Case 2: "if you want me to proceed, say so" -> BLOCK
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

    # Case 5 (THE FIX): a deferral with stop_hook_active:true must STILL BLOCK.
    # The old hook let this through unconditionally -- that was the bug.
    p5 = os.path.join(d, "t5.jsonl")
    make_jsonl(p5, "Say so and I'll proceed.")
    cases.append((5, "BLOCK", run_hook(json.dumps({"transcript_path": p5, "stop_hook_active": True}))))

    # Case 6: empty payload -> NOBLOCK (fail-open, no transcript)
    cases.append((6, "NOBLOCK", run_hook("{}")))

    # Case 7: the exact phrasing that slipped through this session -> BLOCK.
    p7 = os.path.join(d, "t7.jsonl")
    make_jsonl(p7, "Want me to push fix/self-update-sec to get a CI verdict? I can commit or hold.")
    cases.append((7, "BLOCK", run_hook(json.dumps({"transcript_path": p7}))))

    # Case 8: "...your call." -> BLOCK (newly added pattern)
    p8 = os.path.join(d, "t8.jsonl")
    make_jsonl(p8, "I won't push unprompted — that's your call.")
    cases.append((8, "BLOCK", run_hook(json.dumps({"transcript_path": p8}))))

    # Case 10 (round-2 fix): ending by DESCRIBING the next step instead of doing
    # it is parking — the exact phrasing that slipped through this session.
    p10 = os.path.join(d, "t10.jsonl")
    make_jsonl(p10, "The next concrete step toward a green pipeline is a PR from "
                    "fix/self-update-sec to master.")
    cases.append((10, "BLOCK", run_hook(json.dumps({"transcript_path": p10}))))

    # Case 11: "requires opening a PR" hand-off -> BLOCK
    p11 = os.path.join(d, "t11.jsonl")
    make_jsonl(p11, "Getting a CI verdict requires opening a PR to master.")
    cases.append((11, "BLOCK", run_hook(json.dumps({"transcript_path": p11}))))

    # Case 12: "an outward action I have not taken" -> BLOCK
    p12 = os.path.join(d, "t12.jsonl")
    make_jsonl(p12, "That is an outward action I have not taken.")
    cases.append((12, "BLOCK", run_hook(json.dumps({"transcript_path": p12}))))

    # Case 13: a genuinely-finished report with NO next-step framing -> NOBLOCK.
    # Guards against the round-2 patterns over-matching a real completion.
    p13 = os.path.join(d, "t13.jsonl")
    make_jsonl(p13, "Done — committed 840c025, gate is clean, and all suites pass.")
    cases.append((13, "NOBLOCK", run_hook(json.dumps({"transcript_path": p13}))))

    # Case 9: bounded safety valve. A deferral on the SAME transcript is blocked
    # MAX_CONSECUTIVE_BLOCKS times, then fails open so a false positive cannot
    # wedge the session forever. First call BLOCKs, the (MAX+1)th NOBLOCKs.
    p9 = os.path.join(d, "t9.jsonl")
    make_jsonl(p9, "Should I proceed?")
    payload9 = json.dumps({"transcript_path": p9})
    results9 = [run_hook(payload9) for _ in range(MAX_CONSECUTIVE_BLOCKS + 1)]
    cases.append((91, "BLOCK", results9[0]))                      # first deferral blocked
    cases.append((92, "BLOCK", results9[MAX_CONSECUTIVE_BLOCKS - 1]))  # last within budget blocked
    cases.append((93, "NOBLOCK", results9[MAX_CONSECUTIVE_BLOCKS]))    # safety valve fails open
    # cleanup the counter file this case created
    import subprocess as _sp
    key = _sp.run(["cksum"], input=p9.encode(), capture_output=True).stdout.decode().split()[0]
    try:
        os.remove(f"/tmp/no_wait_block_{key}")
    except OSError:
        pass

    # Case 20: deferral phrase with NO enforce var set -> NOBLOCK (advisory mode).
    p20 = os.path.join(d, "t20.jsonl")
    make_jsonl(p20, "Say so and I'll proceed.")
    env_no_enforce = {**os.environ}
    env_no_enforce.pop("GLUDD_NO_WAIT_ENFORCE", None)
    r20 = subprocess.run(
        ["bash", HOOK], input=json.dumps({"transcript_path": p20}).encode(),
        capture_output=True, env=env_no_enforce,
    )
    out20 = r20.stdout.decode()
    cases.append((20, "NOBLOCK", "BLOCK" if ('"decision"' in out20 and '"block"' in out20) else "NOBLOCK"))

    # Case 21: deferral phrase with GLUDD_NO_WAIT_ENFORCE=0 -> NOBLOCK (explicit advisory).
    p21 = os.path.join(d, "t21.jsonl")
    make_jsonl(p21, "Say so and I'll proceed.")
    env21 = {**os.environ, "GLUDD_NO_WAIT_ENFORCE": "0"}
    r21 = subprocess.run(
        ["bash", HOOK], input=json.dumps({"transcript_path": p21}).encode(),
        capture_output=True, env=env21,
    )
    out21 = r21.stdout.decode()
    cases.append((21, "NOBLOCK", "BLOCK" if ('"decision"' in out21 and '"block"' in out21) else "NOBLOCK"))

    # Case 22: deferral phrase WITH GLUDD_NO_WAIT_ENFORCE=1 -> BLOCK (enforce mode restored).
    p22 = os.path.join(d, "t22.jsonl")
    make_jsonl(p22, "Say so and I'll proceed.")
    env22 = {**os.environ, "GLUDD_NO_WAIT_ENFORCE": "1"}
    r22 = subprocess.run(
        ["bash", HOOK], input=json.dumps({"transcript_path": p22}).encode(),
        capture_output=True, env=env22,
    )
    out22 = r22.stdout.decode()
    cases.append((22, "BLOCK", "BLOCK" if ('"decision"' in out22 and '"block"' in out22) else "NOBLOCK"))

fail = 0
for num, exp, got in cases:
    result = "PASS" if got == exp else "FAIL"
    if result == "FAIL":
        fail += 1
    print(f"Case {num:<3} | expected={exp:<7} | got={got:<7} | {result}")

passed = len(cases) - fail
print(f"--- Results: {passed} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
