#!/usr/bin/env bash
# Stop hook: BLOCK turn-end when the assistant's FINAL message asks the user for
# go-ahead / permission / preference instead of taking the default action.
#
# WHY THIS EXISTS: no_blocking_questions_pretool.sh only intercepts the
# AskUserQuestion TOOL. A blocking question posed in plain PROSE at the end of a
# turn ("Want me to start now, or commit first?") bypassed that guard entirely --
# the agent stopped and waited for the user. That violates the standing policy
# (memory: gludd-never-block-on-questions): never pause work to ask; default to
# action, state the assumption, keep moving. This hook closes the prose gap.
#
# CONTRACT: emits {"decision":"block","reason":...} + exit 0 (a CLEAN block, never
# a non-zero hook error). FAIL-OPEN on any error (exit 0 = allow stop) so a hook
# bug can never wedge the session. Anti-wedge: stop_hook_active lets a genuine
# second consecutive stop through, so a truly stuck turn can still end.

input="$(cat 2>/dev/null || echo '{}')"

# Anti-wedge: if we already blocked once this stop-cycle, allow the stop.
case "$input" in
  *'"stop_hook_active": true'*|*'"stop_hook_active":true'*) exit 0 ;;
esac

# Locate the transcript from the hook payload; fail-open if absent.
transcript="$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

# Inspect the LAST assistant text message for a permission-seeking sign-off.
verdict="$(python3 - "$transcript" <<'PY' 2>/dev/null
import sys, json, re

path = sys.argv[1]
last_assistant = ""
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
                last_assistant = "\n".join(texts)
except Exception:
    sys.exit(0)

if not last_assistant:
    sys.exit(0)

# Only the tail matters -- a permission-seeking sign-off lives at the very end.
tail = last_assistant[-700:].lower()

# Must read as a question to the user...
if "?" not in tail:
    sys.exit(0)

# ...AND match a permission/preference-to-continue pattern.
patterns = [
    r"\bwant me to\b",
    r"\bwould you like me to\b",
    r"\bdo you want me to\b",
    r"\bshould i\b",
    r"\bshall i\b",
    r"\blet me know\b",
    r"\bon your go\b",
    r"\byour go[- ]?ahead\b",
    r"\bgive me the go\b",
    r"\bwaiting for (?:your|the)\b",
    r"\bawait(?:ing)? your\b",
    r"\bready to .*(?:now|on your go)\b",
    r"\bconfirm (?:before|and i)\b",
    r"\bproceed\?\s*$",
    r"\bor (?:first|should i|do you)\b",
]
if any(re.search(p, tail) for p in patterns):
    print("BLOCK")
PY
)"

if [ "$verdict" = "BLOCK" ]; then
  reason="STOP BLOCKED (no-blocking-questions, prose): your final message asks the user for go-ahead / permission / which-option instead of acting. Per the never-block-on-questions policy, DO NOT wait. Pick the default/recommended action, state the assumption in one line, and DO IT now. Reserve user questions for TRULY irreversible choices and raise those via the AskUserQuestion tool, not a prose sign-off. (If you were genuinely blocked by a rate-limit/quota or a destructive-irreversible action needing consent, this stop will pass on the next attempt via stop_hook_active.)"
  python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$reason" 2>/dev/null && exit 0
  # python3 failed -> fail OPEN (never wedge, never error).
  exit 0
fi
exit 0
