#!/usr/bin/env bash
# PreToolUse(Bash) MAKE-ONLY enforcer (requested 2026-06-17).
#
# POLICY (CLAUDE.md): every Bash command in this repo MUST be `make <target>`.
# This hook is the Claude-Code-layer enforcement of that (defense-in-depth with
# opencode.json + .opencode/plugin/enforce-make.ts). It DENIES, at the moment of
# the tool call:
#   * any command whose first token is not `make`  (ls/git/find/python/cd/… -> blocked)
#   * any command that chains a second command via && || ;  (closes `make x && rm`)
# Redirects (2>/dev/null) and a pipe INSIDE a quoted make var (Q='a|b') are allowed
# because only the first token + the chaining operators are checked.
#
# The point: the model can NEVER run raw shell and must instead ADD A MAKEFILE
# TARGET — and it must never tell the user to run a non-make command.
#
# FAIL-OPEN on any parse error (exit 0 = allow normal permission flow) so a hook
# bug can never wedge the session; opencode.json is the backstop.

input="$(cat 2>/dev/null)"

printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail open: could not parse -> allow normal flow
cmd = ((d.get("tool_input") or {}).get("command") or "")
t = cmd.strip()
if not t:
    sys.exit(0)

def deny(msg):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": msg,
    }}))
    sys.exit(0)

toks = t.split()
first = toks[0] if toks else ""
if first != "make":
    deny("Bash is MAKE-ONLY in this repo (CLAUDE.md). " + repr(t) + " is not a make command. "
         "Do NOT run raw shell and do NOT ask the user to run it — instead ADD a Makefile target "
         "for what you need and run it as: make <target>. (Listing/status: make git-status / "
         "make git-log / make ps-pytest; tests: make test-unit; etc.)")

for op in ("&&", "||", ";"):
    if op in t:
        deny("Bash is MAKE-ONLY: command chaining (" + op + ") is not allowed — run a single "
             "make <target>. If you need multiple steps, add ONE Makefile target that performs "
             "them, then run that single target.")

sys.exit(0)  # first token is make and no chaining -> allow
' 2>/dev/null

exit 0
