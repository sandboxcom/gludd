#!/usr/bin/env bash
# Stop hook: BLOCK turn-end whenever the assistant's final message CLAIMS work is
# done / fixed / working / shipped / landed / resolved / ✅ for outward or
# shippable work WITHOUT pasting a verification MEASUREMENT.
#
# WHY: the orchestrator marked a release pipeline "✅ Landed" when the files were
# only edited in the working tree -- uncommitted, unpushed, never run on CI, no
# Release or package observable anywhere. An unverified "done" is indistinguishable
# from a false claim. The standing policy (AGENTS.md "'Done' Claims Require
# Observable Verification Evidence"; memory: gludd-no-unquantified-status-claims)
# is: NEVER write done/landed/shipped/resolved/fixed/working/✅ unless the same
# message contains a cited, machine-produced measurement. This hook enforces it in
# code: a completion claim with NO evidence token and NO honest hedge is BLOCKED.
#
# CONTRACT: emits {"decision":"block","reason":...} + exit 0 (clean block, never a
# hook error). FAIL-OPEN (exit 0, no output) on ANY error -- it can never wedge the
# session on a parse/IO failure.
#
# ANTI-WEDGE: a bounded consecutive-block counter (per transcript). A claim-without-
# evidence is blocked EVERY time, but after MAX_CONSECUTIVE_BLOCKS in a row it fails
# open so a false-positive on a genuinely-finished turn can never wedge permanently.
#
# OFF-SWITCH: set GLUDD_FALSE_DONE_ENFORCE=0 to make this advisory (never block).
# Default is ENFORCE (block) -- this guardrail exists because the soft version
# (a policy doc) was ignored.
#
# LIMITS (honest): this is a HEURISTIC text matcher, not a truth oracle. It blocks
# the obvious pattern (completion words with no measurement); it can be fooled by a
# bare evidence token and can false-positive on an un-hedged casual "done". It is a
# forcing function, not a proof of honesty.

MAX_CONSECUTIVE_BLOCKS=25

if [ "${GLUDD_FALSE_DONE_ENFORCE:-1}" != "1" ]; then
  exit 0
fi

input="$(cat 2>/dev/null || echo '{}')"

# Avoid re-block loops: if this is already a stop-hook-induced continuation, allow.
sha="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("1" if d.get("stop_hook_active") else "")' 2>/dev/null)"
[ "$sha" = "1" ] && exit 0

transcript="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

verdict="$(python3 - "$transcript" <<'PY' 2>/dev/null
import sys, json, re
path = sys.argv[1]
last = ""
try:
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])
            texts = []
            if isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        texts.append(p.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)
            if texts:
                last = "\n".join(texts)
except Exception:
    sys.exit(0)

if not last:
    sys.exit(0)

low = last.lower()

# (1) Strong completion / success CLAIM about shippable / outward work.
claim_patterns = [
    r"✅", r"[✔✓☑🟢🆗👍]",
    r"\blanded\b", r"\bshipped\b", r"\bship it\b", r"\bdeployed\b", r"\breleased\b",
    r"\bmerged\b",
    # "is live" plus contraction forms (it's live / we're live / endpoint's live).
    r"(?:\bis|'s|\bit'?s|\bwe'?re|\bthey'?re|\bare)\s+(?:now\s+)?live\b",
    r"\bgoes? live\b", r"\bnow works\b", r"\bup and running\b", r"\boperational\b",
    r"\bdone\b", r"\ball set\b", r"\bcomplete(?:s|d)?\b", r"\bresolved\b", r"\bfixed\b",
    r"\bworking\b", r"\bfunctional\b", r"\bsuccessful(?:ly)?\b",
    r"\bwired (?:up|in)\b", r"\bfully wired\b", r"\bproduction[- ]ready\b",
    r"\bready to (?:go|ship|merge|land|release)\b", r"\bgood to go\b",
    r"\ball green\b", r"\bgreen\b.*\bpipeline\b",
]

# (2) EVIDENCE tokens -- a cited, machine-produced measurement. Presence anywhere
#     means the claim is backed; allow the stop. HARDENED 2026-06-25 (adversarial
#     audit): a bare ``` fence, the lone word "verified", a placeholder/fake SHA,
#     and "0 passed" were all free passes. Each now requires a real measurement.
evidence_patterns = [
    r"ci-verdict", r"conclusion:\s*success", r"\brun[ _]?id\b", r"\brun \d{6,}",
    r"gh release view", r"verify-release", r"verify-remote",
    r"\.gate-status", r"gate(?:-status)?:?\s*pass", r"\bgate green\b",
    # Nonzero test counts only ("0 passed" is failure/none, not evidence).
    r"\b[1-9]\d*\s+passed\b", r"\b[1-9]\d*\s+passing\b",
    # "verified" only counts when adjacent to an actual measurement.
    r"\bverified\b[^.\n]{0,40}(?:[1-9]\d*\s+passed|conclusion:\s*success|run \d{6,})",
    # Commit SHA, excluding low-entropy placeholders (deadbeef / c0ffee / all-zero).
    r"\bcommit\s+(?!0{7}|deadbeef|c0ffee)[0-9a-f]{7,40}\b",
    r"\bsha[:= ]\s*[0-9a-f]{7,40}\b",
    # A code fence counts ONLY if its body contains a measurement token. Uses a
    # lookahead + single greedy sweep (NOT two lazy unbounded [^`]*? spans, which
    # are O(N^2) on an adversarial unclosed fence and could hang this Stop hook).
    r"```(?=[^`]*?(?:[1-9]\d*\s+passed|passed in|conclusion|success))[^`]*```",
]

# (3) HONEST HEDGE / negation / in-progress markers -- the claim is qualified, not
#     asserted. Presence anywhere means allow the stop. HARDENED 2026-06-25: bare
#     "will"/"would", greedy "not .* done", and negated hedges ("zero uncommitted",
#     "no packages broke") were over-broad free passes. Each is now scoped.
hedge_patterns = [
    r"\bnot (?:yet |fully )?(?:done|live|complete|completed|committed|pushed|built|working|applied|landed|shipped|wired|verified)\b",
    r"\bnot yet\b", r"\bin progress\b", r"\bin-flight\b",
    # uncommitted/unpushed/pending only count when NOT preceded by a negator.
    r"(?<!no )(?<!not )(?<!zero )\buncommitted\b",
    r"(?<!no )(?<!not )(?<!zero )\bunpushed\b",
    r"(?<!no )(?<!not )(?<!zero )\bpending\b",
    r"\bunverified\b",
    r"\b(?:this is|it'?s|still) a draft\b",
    # future-WORK senses of will/would only (not "users will love it").
    r"\bwill (?:not|need|require|fail|follow|be (?:done|built|pushed|added))\b",
    r"\bwould (?:need|have to|require)\b",
    r"\bnot applied\b", r"\bnot built\b",
    r"\bisn'?t\b", r"\bhaven'?t\b", r"\bhasn'?t\b", r"\bnothing is (?:live|on)\b",
    r"\bnot real\b", r"\bcan'?t claim\b", r"\bnot committed\b", r"\bnot pushed\b",
    r"\bover-?claim\b", r"\bi was wrong\b", r"\bfalse claim\b",
    r"\b(?:need|have|going) to prove\b",
    r"\bonce .{0,30}?(?:lands?|returns?|finish)",
    r"\b(?:is|are|was|were|'s|'re)\s+not\s+(?:yet\s+)?\w*\s?(?:done|live|complete)\b",
]

has_claim = any(re.search(p, low) for p in claim_patterns)
has_evidence = any(re.search(p, low) for p in evidence_patterns)
has_hedge = any(re.search(p, low) for p in hedge_patterns)

if has_claim and not has_evidence and not has_hedge:
    print("BLOCK")
PY
)"

verdict_type="$(printf '%s' "$verdict" | head -1)"
if [ "$verdict_type" != "BLOCK" ]; then
  key="$(printf '%s' "$transcript" | cksum 2>/dev/null | awk '{print $1}')"
  [ -n "$key" ] && rm -f "/tmp/no_false_done_block_${key}" 2>/dev/null
  exit 0
fi

# Unverified completion claim detected. Apply the bounded anti-wedge counter.
key="$(printf '%s' "$transcript" | cksum 2>/dev/null | awk '{print $1}')"
statefile="/tmp/no_false_done_block_${key}"
n=0
[ -n "$key" ] && [ -f "$statefile" ] && n="$(cat "$statefile" 2>/dev/null || echo 0)"
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))

if [ "$n" -gt "$MAX_CONSECUTIVE_BLOCKS" ]; then
  [ -n "$key" ] && rm -f "$statefile" 2>/dev/null
  exit 0
fi

[ -n "$key" ] && printf '%s' "$n" > "$statefile" 2>/dev/null

reason="STOP BLOCKED (no-false-completion): your final message claims work is done / fixed / working / shipped / landed / resolved / ✅ but pastes NO verification MEASUREMENT. Policy (AGENTS.md \"'Done' Claims Require Observable Verification Evidence\"): an unverified 'done' is indistinguishable from a false claim. Either (a) paste the cited measurement that makes it observable -- make ci-verdict / gh release view showing the release+assets / .gate-status PASS / an 'N passed' test count / a commit SHA -- in the SAME message, OR (b) restate the claim honestly as in-progress / uncommitted / unverified / a draft. Do NOT end the turn on an unverified completion claim."
python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
exit 0
