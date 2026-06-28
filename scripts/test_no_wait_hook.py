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

    # ── CONSTRAINT-AS-STOPSIGN cases (AGENTS.md "Constraints Are To Engineer Around") ──
    # All run with GLUDD_NO_WAIT_ENFORCE=1 (enforce mode).
    # Note: the hook cannot distinguish a paired-workaround from a naked stop-sign
    # at the regex level — that distinction is left to human judgment. These cases
    # assert the RAW phrasing blocks in enforce mode.

    # Case 30: "isn't possible" -> BLOCK
    p30 = os.path.join(d, "t30.jsonl")
    make_jsonl(p30, "That isn't possible with the current API.")
    cases.append((30, "BLOCK", run_hook(json.dumps({"transcript_path": p30}))))

    # Case 31: "is not possible" -> BLOCK
    p31 = os.path.join(d, "t31.jsonl")
    make_jsonl(p31, "Live per-step status is not possible via GitHub Actions.")
    cases.append((31, "BLOCK", run_hook(json.dumps({"transcript_path": p31}))))

    # Case 32: "not possible to" -> BLOCK
    p32 = os.path.join(d, "t32.jsonl")
    make_jsonl(p32, "It's not possible to get that data without a paid plan.")
    cases.append((32, "BLOCK", run_hook(json.dumps({"transcript_path": p32}))))

    # Case 33: "no way to" -> BLOCK
    p33 = os.path.join(d, "t33.jsonl")
    make_jsonl(p33, "There's no way to run the full gate locally without OOM.")
    cases.append((33, "BLOCK", run_hook(json.dumps({"transcript_path": p33}))))

    # Case 34: "there's no way" -> BLOCK
    p34 = os.path.join(d, "t34.jsonl")
    make_jsonl(p34, "There's no way around the rate limit.")
    cases.append((34, "BLOCK", run_hook(json.dumps({"transcript_path": p34}))))

    # Case 35: "it's a limitation" -> BLOCK
    p35 = os.path.join(d, "t35.jsonl")
    make_jsonl(p35, "It's a limitation of the GitHub API.")
    cases.append((35, "BLOCK", run_hook(json.dumps({"transcript_path": p35}))))

    # Case 36: "is a limitation" -> BLOCK
    p36 = os.path.join(d, "t36.jsonl")
    make_jsonl(p36, "The 60-second granularity is a limitation we can't avoid.")
    cases.append((36, "BLOCK", run_hook(json.dumps({"transcript_path": p36}))))

    # Case 37: "we have to wait" -> BLOCK
    p37 = os.path.join(d, "t37.jsonl")
    make_jsonl(p37, "The GitHub API doesn't expose that, so we have to wait for the run.")
    cases.append((37, "BLOCK", run_hook(json.dumps({"transcript_path": p37}))))

    # Case 38: "have to wait for" -> BLOCK (subset phrase)
    p38 = os.path.join(d, "t38.jsonl")
    make_jsonl(p38, "We'll have to wait for CI to finish before we know.")
    cases.append((38, "BLOCK", run_hook(json.dumps({"transcript_path": p38}))))

    # Case 39: "nothing we can do" -> BLOCK
    p39 = os.path.join(d, "t39.jsonl")
    make_jsonl(p39, "There's nothing we can do until the API quota resets.")
    cases.append((39, "BLOCK", run_hook(json.dumps({"transcript_path": p39}))))

    # Case 40: "can't be done" -> BLOCK
    p40 = os.path.join(d, "t40.jsonl")
    make_jsonl(p40, "That can't be done with make-only Bash.")
    cases.append((40, "BLOCK", run_hook(json.dumps({"transcript_path": p40}))))

    # Case 41: "the api doesn't support" -> BLOCK
    p41 = os.path.join(d, "t41.jsonl")
    make_jsonl(p41, "The API doesn't support per-step result streaming.")
    cases.append((41, "BLOCK", run_hook(json.dumps({"transcript_path": p41}))))

    # Case 42: "the api doesn't expose" -> BLOCK
    p42 = os.path.join(d, "t42.jsonl")
    make_jsonl(p42, "The API doesn't expose live step output until completion.")
    cases.append((42, "BLOCK", run_hook(json.dumps({"transcript_path": p42}))))

    # Case 43: worked-example phrasing from AGENTS.md -> BLOCK (raw phrasing; paired
    # workaround distinction is left to judgment as noted above)
    p43 = os.path.join(d, "t43.jsonl")
    make_jsonl(p43, "GitHub only exposes step results at completion — there's no way to get live per-step status.")
    cases.append((43, "BLOCK", run_hook(json.dumps({"transcript_path": p43}))))

    # ── CONSTRAINT-AS-STOP self-heal cases (2026-06-23 incident) ──────────────
    # The agent responded "restart opencode one more time" to a recoverable state.
    # These cases verify the new constraint_patterns group catches the restart/
    # wait/precondition phrasings the first-gen constraint regex missed.

    # Case 50: "restart opencode" (the incident phrase) -> BLOCK
    p50 = os.path.join(d, "t50.jsonl")
    make_jsonl(p50, "The plugin didn't reload — you'll need to restart opencode one more time.")
    cases.append((50, "BLOCK", run_hook(json.dumps({"transcript_path": p50}))))

    # Case 51: "cannot without" precondition (adjacent) -> BLOCK
    p51 = os.path.join(d, "t51.jsonl")
    make_jsonl(p51, "Cannot without the artifact URL — we'd need to confirm the release.")
    cases.append((51, "BLOCK", run_hook(json.dumps({"transcript_path": p51}))))

    # Case 52: "not possible unless" precondition (adjacent) -> BLOCK
    p52 = os.path.join(d, "t52.jsonl")
    make_jsonl(p52, "Not possible unless the CI quota resets first.")
    cases.append((52, "BLOCK", run_hook(json.dumps({"transcript_path": p52}))))

    # Case 53: "limitation of" attribution -> BLOCK
    p53 = os.path.join(d, "t53.jsonl")
    make_jsonl(p53, "That's a limitation of the GitHub API granularity.")
    cases.append((53, "BLOCK", run_hook(json.dumps({"transcript_path": p53}))))

    # Case 54: "constraint of" attribution -> BLOCK
    p54 = os.path.join(d, "t54.jsonl")
    make_jsonl(p54, "It's a constraint of the make-only bash policy.")
    cases.append((54, "BLOCK", run_hook(json.dumps({"transcript_path": p54}))))

    # Case 55: "we need to restart" -> BLOCK
    p55 = os.path.join(d, "t55.jsonl")
    make_jsonl(p55, "The config didn't take, so we need to restart the daemon.")
    cases.append((55, "BLOCK", run_hook(json.dumps({"transcript_path": p55}))))

    # Case 56: "we must wait" -> BLOCK
    p56 = os.path.join(d, "t56.jsonl")
    make_jsonl(p56, "The gate is still running, so we must wait for it to finish.")
    cases.append((56, "BLOCK", run_hook(json.dumps({"transcript_path": p56}))))

    # Case 57: clean workaround message -> NOBLOCK (no constraint phrasing at all;
    # proves the new constraint_patterns do not over-match on normal status text).
    p57 = os.path.join(d, "t57.jsonl")
    make_jsonl(p57, "CI granularity is coarse, so I sharded the test job and I'm polling annotations — running now.")
    cases.append((57, "NOBLOCK", run_hook(json.dumps({"transcript_path": p57}))))
    # ── END CONSTRAINT-AS-STOP self-heal cases ─────────────────────────────────

    # ── END CONSTRAINT-AS-STOPSIGN cases ──────────────────────────────────────

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

    # ── ROUND-3: FUTURE-TENSE SELF-DEFERRAL (2026-06-25 incident) ──────────────
    # The exact phrasing that slipped EVERY pattern this session: ending a turn by
    # promising future action instead of executing it. No question, no permission
    # seek -- still parking. These prove the round-3 patterns catch it.

    # Case 60: the verbatim phrasing that slipped this session -> BLOCK
    p60 = os.path.join(d, "t60.jsonl")
    make_jsonl(p60, "Next I'll fix those 4 release issues and land Layer 2 + the branch guard as the drafts return.")
    cases.append((60, "BLOCK", run_hook(json.dumps({"transcript_path": p60}))))

    # Case 61: "I'll apply it once the agents finish." -> BLOCK
    p61 = os.path.join(d, "t61.jsonl")
    make_jsonl(p61, "I'll apply the patch once the audit agents finish.")
    cases.append((61, "BLOCK", run_hook(json.dumps({"transcript_path": p61}))))

    # Case 62: "I'll wire X as the drafts return." -> BLOCK
    p62 = os.path.join(d, "t62.jsonl")
    make_jsonl(p62, "I'll wire the completion gate as the drafts return.")
    cases.append((62, "BLOCK", run_hook(json.dumps({"transcript_path": p62}))))

    # Case 63: "Next, I'll ..." comma variant -> BLOCK
    p63 = os.path.join(d, "t63.jsonl")
    make_jsonl(p63, "Next, I'll harvest the completed agent and integrate it.")
    cases.append((63, "BLOCK", run_hook(json.dumps({"transcript_path": p63}))))

    # Case 64 (over-match guard): an evidenced completion that MENTIONS agents/drafts
    # but is genuinely finished -> NOBLOCK. Proves round-3 patterns don't trip a
    # real done-with-measurement report.
    p64 = os.path.join(d, "t64.jsonl")
    make_jsonl(p64, "Applied the guard and ran make test-release-guard: 13 passed, 0 failed.")
    cases.append((64, "NOBLOCK", run_hook(json.dumps({"transcript_path": p64}))))

fail = 0
for num, exp, got in cases:
    result = "PASS" if got == exp else "FAIL"
    if result == "FAIL":
        fail += 1
    print(f"Case {num:<3} | expected={exp:<7} | got={got:<7} | {result}")

passed = len(cases) - fail
print(f"--- Results: {passed} passed, {fail} failed ---")
sys.exit(0 if fail == 0 else 1)
