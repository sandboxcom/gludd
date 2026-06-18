#!/usr/bin/env bash
# PreToolUse(Edit) guardrail-integrity guard — 2026-06-18
#
# Protects ALL hook + plugin files from edits that silently remove enforcement.
# The existing enforce-make.ts check covers enforce-make.ts ONLY; this covers:
#   .claude/hooks/*.sh         — shell stop/pretool/posttool/subagent hooks
#   .opencode/plugin/*.ts      — TS plugins (enforce-make.ts, enforce-floor.ts, …)
#
# TRIGGER: file_path is under hooks/ or plugin/ AND old_string contains one or more
# ENFORCEMENT TOKENS AND new_string contains NONE of those tokens.
# (A legitimate refactor that keeps the same enforcement pattern in new words is
# allowed — only complete removal of ALL tokens fires.)
#
# ENFORCEMENT TOKENS: the set of patterns whose presence in a hook means "this hook
# actively blocks/denies/errors".  If you remove ALL of them from a hook that had
# them, the hook is now advisory or dead.
#
# FAIL-OPEN: any parse error -> exit 0 (silent).
# CONTEXT-EFFICIENT: emits nothing on non-hook/plugin edits (the vast majority).

input="$(cat 2>/dev/null || echo '{}')"

printf '%s' "$input" | python3 -c '
import sys, json, re

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail open

ti = d.get("tool_input") or {}
file_path  = ti.get("file_path") or ti.get("filePath") or ""
old_string = ti.get("old_string") or ti.get("oldString") or ""
new_string = ti.get("new_string") or ti.get("newString") or ""

# Only guard hook and plugin files.
guarded = (
    "/.claude/hooks/" in file_path or
    "/.opencode/plugin/" in file_path or
    (file_path.endswith(".sh") and "/hooks/" in file_path)
)
if not guarded:
    sys.exit(0)

# Enforcement tokens — any of these signals "this code ACTIVELY enforces".
# We use plain string checks (not regex) where possible to avoid false matches.
TOKENS = [
    "\"decision\":\"block\"",
    "\"decision\": \"block\"",
    "\"permissionDecision\":\"deny\"",
    "\"permissionDecision\": \"deny\"",
    "permissionDecision.*deny",
    "permissionDecision.*block",
    "throw new Error",
    "sys.exit(1)",
    "exit 1",
    "BLOCKED",
    "FORBIDDEN",
    "TDD VIOLATION",
    "GUARDRAIL INTEGRITY VIOLATION",
]

def has_token(text):
    for tok in TOKENS:
        # Try regex for the two patterns that need it; plain string otherwise
        if ".*" in tok:
            if re.search(tok, text):
                return True
        else:
            if tok in text:
                return True
    return False

old_has = has_token(old_string)
new_has = has_token(new_string)

# Only fire when: old had enforcement AND new removes ALL of it AND new is non-empty
# (empty new_string would be a deletion, handled differently by the harness)
if old_has and not new_has and new_string.strip():
    reason = (
        "GUARDRAIL INTEGRITY VIOLATION (fix-means-repair-never-disable): "
        "The edit removes ALL enforcement tokens from " + file_path + ". "
        "old_string contained an active block/deny/throw/exit-1 enforcement token; "
        "new_string contains none. "
        "Per the fix-means-repair-never-disable policy: \"fix\" means make "
        "the feature work correctly, NEVER disable or weaken it. "
        "If the enforcement is noisy, narrow its conditions — do NOT delete "
        "the enforcement. Repair the hook; do not defang it. See AGENTS.md."
    )
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}
    print(json.dumps(out))

sys.exit(0)
' 2>/dev/null

exit 0
