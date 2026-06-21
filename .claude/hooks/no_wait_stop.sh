#!/usr/bin/env bash
# Stop hook: BLOCK turn-end whenever the assistant's final message WAITS ON / DEFERS
# TO the user instead of taking the next action autonomously.
#
# WHY: the orchestrator repeatedly ended turns with "say so and I'll proceed",
# "tell me to proceed", "if you want me to", "either way", "when you're ready",
# "I'll hold", "Want me to ... ?", "your call", etc. -- parking the work and
# waiting for the user. The standing policy (memory: gludd-never-block-on-questions)
# is: NEVER pause work to ask; default to action, state the assumption, keep going.
# This hook enforces it in code: if the final message reads as a deferral /
# permission-seek / hold, the stop is BLOCKED and the agent is told to act NOW.
#
# CONTRACT: emits {"decision":"block","reason":...} + exit 0 (clean block, never a
# hook error). FAIL-OPEN on any error.
#
# ANTI-WEDGE (the fix): the old version unconditionally let ANY second consecutive
# stop through via stop_hook_active==true. That was a blanket free-pass: after a
# single block, the very next stop -- even a pure deferral -- went straight through
# (this is exactly how a "Want me to push?" turn ended despite matching). We replace
# that with a BOUNDED consecutive-block counter: a deferral is blocked EVERY time,
# but after MAX_CONSECUTIVE_BLOCKS in a row we fail open so a genuine false-positive
# (a finished turn that happens to trip a pattern) can never wedge permanently.

MAX_CONSECUTIVE_BLOCKS=25

# ADVISORY MODE (2026-06-21, by user instruction): this hook previously emitted
# {"decision":"block"} and forcibly prevented turn-end, which trapped the
# orchestrator in a coercive loop (it could not answer the user or stop even when
# that was the right thing to do). It is now ADVISORY: it never blocks. Set
# GLUDD_NO_WAIT_ENFORCE=1 to restore the old blocking behaviour.
if [ "${GLUDD_NO_WAIT_ENFORCE:-0}" != "1" ]; then
  exit 0
fi

input="$(cat 2>/dev/null || echo '{}')"

transcript="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

verdict="$(python3 - "$transcript" <<'PY' 2>/dev/null
import sys, json, re
path = sys.argv[1]
last = ""
try:
    with open(path) as f:
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

# Deferral / wait-on-user / permission-seeking patterns. If the FINAL message
# contains any of these, the agent is parking the work instead of proceeding.
patterns = [
    r"\bsay so\b", r"\bsay the word\b",
    r"\btell me to\b", r"\bif you want me to\b", r"\bif you'?d (?:like|prefer|rather)\b",
    r"\bwant me to\b", r"\bwould you like me to\b", r"\bdo you want me to\b",
    r"\bshould i\b", r"\bshall i\b",
    r"\blet me know\b", r"\bjust let me know\b",
    r"\bon your go\b", r"\byour go[- ]?ahead\b", r"\bgive me the go\b",
    r"\bwhen you'?re ready\b", r"\bwhenever you'?re ready\b", r"\bready when you are\b",
    r"\bi'?ll hold\b", r"\bholding (?:here|for|off)\b", r"\bi'?ll wait\b", r"\bi'?ll pause\b",
    r"\bstanding by\b", r"\bawait(?:ing)? your\b", r"\bwaiting (?:for|on) (?:your|you|the)\b",
    r"\beither way\b.*\?", r"\bpoint a fresh session\b", r"\bfresh (?:context|session)\b",
    r"\bif you'?d rather i proceed\b", r"\bwant me to proceed\b", r"\bproceed\?\s*$",
    r"\blet me know (?:if|when|whether|which|what)\b",
    # Added: phrasings that previously slipped through.
    r"\byour call\b", r"\bup to you\b", r"\bleave (?:it|that|this|the\b.*) to you\b",
    r"\bor hold\b", r"\bcommit or hold\b", r"\bi'?ll leave (?:it|that|this)?\s*to (?:your|you)\b",
    r"\bi can (?:commit|push|apply|proceed|hold)\b.*\bor\b",
    r"\bwhich (?:would you|do you want|one)\b", r"\bprefer (?:that )?i\b",
    # Added 2026-06-21 (round 2): ending a turn by DESCRIBING the next / remaining
    # / pending action instead of EXECUTING it is still parking. A status report
    # that hands the next step back to the user (no question asked) slipped through
    # the permission-seek patterns above; catch the hand-off framing directly.
    r"\bnext (?:step|steps|action|concrete step)\b",
    r"\bthe next (?:step|thing|action|move|concrete)\b",
    r"\bremaining (?:work|step|steps|item|items|action|task)\b",
    r"\b(?:still need to|yet to|left to|remains? to|the remaining)\b",
    r"\brequires?\b.{0,24}?\b(?:pr\b|pull request|push|merge|manual)\b",
    r"\bwould (?:need|require) (?:a |an |to )?\b",
    r"\bi have not (?:pushed|opened|taken|merged|run|applied|done|created)\b",
    r"\bi haven'?t (?:pushed|opened|taken|merged|run|applied|done|created)\b",
    r"\bnot yet (?:pushed|opened|taken|merged|run|applied|done|created)\b",
    r"\bhave not taken\b", r"\boutward action i have not\b",
    r"\bcaptured (?:for|as)\b.*\bfollow-?up\b", r"\bfor a future pr\b",
    r"\bto get (?:a )?(?:ci|green|verdict|coverage)\b.*\b(?:requires?|need|would|open|push)\b",
]
if any(re.search(p, low) for p in patterns):
    print("BLOCK")
PY
)"

if [ "$verdict" != "BLOCK" ]; then
  # Not a deferral: this is a legitimate turn-end. Reset the consecutive-block
  # counter for this transcript and allow the stop.
  key="$(printf '%s' "$transcript" | cksum 2>/dev/null | awk '{print $1}')"
  [ -n "$key" ] && rm -f "/tmp/no_wait_block_${key}" 2>/dev/null
  exit 0
fi

# Deferral detected. Apply the bounded anti-wedge counter.
key="$(printf '%s' "$transcript" | cksum 2>/dev/null | awk '{print $1}')"
statefile="/tmp/no_wait_block_${key}"
n=0
[ -n "$key" ] && [ -f "$statefile" ] && n="$(cat "$statefile" 2>/dev/null || echo 0)"
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))

if [ "$n" -gt "$MAX_CONSECUTIVE_BLOCKS" ]; then
  # Safety valve: too many consecutive blocks. Fail open so a false-positive
  # match on a genuinely-finished turn can never wedge the session forever.
  [ -n "$key" ] && rm -f "$statefile" 2>/dev/null
  exit 0
fi

[ -n "$key" ] && printf '%s' "$n" > "$statefile" 2>/dev/null

reason="STOP BLOCKED (no-wait): your final message DEFERS to the user / waits for go-ahead / offers to proceed 'if they want' instead of just doing it. Policy: NEVER wait on the user. Take the default/recommended action NOW, state the assumption in one line, and execute it. The work is reversible (branches/RC, not master) -- proceed. Only a genuinely IRREVERSIBLE+destructive action needs consent, raised via AskUserQuestion, not a prose sign-off."
python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
exit 0
