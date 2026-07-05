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
# ANTI-WEDGE (dual-layer, rev 2026-07-05):
# 1. stop_hook_active==true: 1-retry grace period — a second consecutive stop is
#    allowed so a genuine dead-end can always end the session. (Restored 2026-07-05
#    per Bug 1 fix — this matches sibling hooks multitasking_backlog_stop.sh and
#    agent_floor_stop.sh.)
# 2. BOUNDED consecutive-block counter: a deferral is blocked EVERY time, but after
#    MAX_CONSECUTIVE_BLOCKS in a row we fail open so a false-positive match on a
#    genuinely-finished turn can never wedge permanently.

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

# ANTI-WEDGE: stop_hook_active means this is a second consecutive stop — allow it
# so a session can always end after one block (the 1-retry grace period that
# sibling hooks multitasking_backlog_stop.sh and agent_floor_stop.sh also use).
case "$input" in
  *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
esac

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
    # ── CONSTRAINT-AS-STOPSIGN (2026-06-21) ─────────────────────────────────
    # Policy (AGENTS.md "Constraints Are To Engineer Around"): a constraint is a
    # design prompt, not a terminal answer. Naked "can't / isn't possible / we have
    # to wait" phrasings without an immediately-paired workaround are parking the
    # problem on the user. Block them in enforce mode; the paired-workaround
    # distinction is left to human judgment (the hook catches the raw phrasing).
    r"\bisn'?t possible\b",
    r"\bis not possible\b",
    r"\bnot possible to\b",
    r"\bno way to\b",
    r"there'?s no way\b",
    r"\bit'?s a limitation\b",
    r"\bis a limitation\b",
    r"\bwe have to wait\b",
    r"\bhave to wait for\b",
    r"\bnothing (?:i|we) can do\b",
    r"\bcan'?t be done\b",
    r"\bthe api does(?:n'?t| not) (?:support|expose|provide)\b",
    # ── END CONSTRAINT-AS-STOPSIGN ───────────────────────────────────────────
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
    # Added 2026-06-25 (round 3): FUTURE-TENSE SELF-DEFERRAL. Ending a turn by
    # PROMISING the next action ("Next I'll X", "I'll X once/after/as the agents
    # or drafts return/finish", "as the drafts return") hands the work to a future
    # turn -- that is parking even though no question is asked and no permission is
    # sought. This is the EXACT phrasing ("Next I'll fix ... as the drafts return")
    # that slipped every pattern above this session. The next action is already in
    # context; describing it instead of executing it IS the recurring stop-bug.
    r"\bnext,?\s+i['’]?ll\b",
    r"\bi['’]?ll\b[^.?!]{0,60}\b(?:once|after|when|as soon as|as)\b[^.?!]{0,60}\b(?:returns?|returning|finish(?:es|ed)?|complete[sd]?|lands?|come back|are back|are done|is done|ready)\b",
    r"\bas (?:the |they )?(?:drafts?|agents?|results?|tasks?)\b[^.?!]{0,30}\b(?:returns?|come back|land|finish|are (?:back|done|ready))\b",
    r"\bonce (?:the |they )?(?:drafts?|agents?|results?|tasks?)\b[^.?!]{0,30}\b(?:returns?|land|come back|finish|are (?:back|done|ready))\b",
]
# Constraint-as-stop self-heal patterns (2026-06-23). Checked separately so the
# block reason can carry the workaround directive, not just the generic deferral
# message. ADDITIVE to `patterns` above (overlap harmless; first match wins).
constraint_patterns = [
    r"\brestart (?:opencode|required|needed)\b",
    r"\b(?:can'?t|cannot|not possible) (?:without|unless)\b",
    r"\bhave to wait\b",
    r"\b(?:limitation|constraint) of\b",
    r"\b(?:no way|there'?s no way) to\b",
    r"\bisn'?t possible\b",
    r"\bwe (?:need|must)(?: to)? (?:restart|wait|stop)\b",
]
if any(re.search(p, low) for p in patterns):
    print("BLOCK")
elif any(re.search(p, low) for p in constraint_patterns):
    print("BLOCK_CONSTRAINT")
PY
)"

verdict_type="$(printf '%s' "$verdict" | head -1)"
if [ "$verdict_type" != "BLOCK" ] && [ "$verdict_type" != "BLOCK_CONSTRAINT" ]; then
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

if [ "$verdict_type" = "BLOCK_CONSTRAINT" ]; then
  reason="CONSTRAINT DETECTED: your final message frames a constraint (restart, wait, can't-be-done, limitation, no-way) as a terminal answer. Policy (AGENTS.md 'Constraints Are To Engineer Around'): a constraint is a design prompt, NEVER a dead end. Engineer a workaround NOW or dispatch a research task to find one. Do NOT park this on the user. Do NOT instruct the user to restart / wait / reconfigure. If no workaround is obvious, DISPATCH a research task (worktree agent) -- that is not a stopping point."
else
  reason="STOP BLOCKED (no-wait): your final message DEFERS to the user / waits for go-ahead / DESCRIBES-the-next-action / promises future work ('Next I'll ...', 'I'll X once the drafts return') instead of EXECUTING it. Policy: NEVER wait on the user and NEVER hand the next step to a future turn. The next action is already in your context -- take it NOW with a tool call, state the assumption in one line. Work on branches/RC is reversible -- proceed. Only a genuinely IRREVERSIBLE+destructive action needs consent, via AskUserQuestion, not a prose sign-off."
  # SELF-HEAL ESCALATION (2026-06-25): a static scold let the same park recur turn
  # after turn. Escalate with the consecutive-block count so a repeated park is
  # treated as the recurring STOP-BUG it is, not a one-off -- and the directive
  # gets sharper each time instead of identical.
  if [ "$n" -ge 2 ]; then
    reason="${reason} ESCALATION: this is park #${n} in a row on this thread. Parking is the recurring stop-bug the operator has repeatedly flagged -- do NOT re-emit another status report or plan. Pick the SINGLE highest-priority pending action from your context and execute it with a tool call in THIS reply. If you genuinely have a finished, evidenced result, state it in one line with the pasted measurement and nothing forward-looking."
  fi
fi
python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
exit 0
