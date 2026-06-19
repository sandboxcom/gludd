#!/usr/bin/env bash
# PreToolUse(Write|Edit) gate-flag-file write guard — 2026-06-18
#
# POLICY: agents MUST NOT write .gate-status, .gate-failed, or any
# *.gate-status file directly.  run_gate.sh writes these via shell
# (outside the harness tool-use path) and is NOT affected by this hook.
# Allowing agent writes would let an agent forge a PASS gate status and
# bypass the commit freshness gate — a direct guardrail integrity breach.
#
# DETECTION: match file_path against:
#   .gate-status          (exact basename)
#   .gate-failed          (exact basename)
#   *.gate-status         (any file whose basename ends with .gate-status)
#
# OUTPUT: permissionDecision:deny JSON on match (exit 0 always).
# FAIL-CLOSED: on a parse error, deny IF the raw payload references a
#   protected flag-file name; unrelated writes still pass (no wedging).
# SILENT on non-matching paths (no output).
#
# Matchers: Write + Edit (registered separately in settings.json).

input="$(cat 2>/dev/null || echo '{}')"

matched="$(printf '%s' "$input" | python3 -c '
import sys, json, os, re
raw = sys.stdin.read()
def is_flag(base):
    return base in (".gate-status", ".gate-failed") or base.endswith(".gate-status")
try:
    d = json.loads(raw)
    ti = d.get("tool_input") or {}
    # Write uses "file_path"; Edit uses "file_path" too.
    path = (ti.get("file_path") or "").strip()
    print("yes" if is_flag(os.path.basename(path)) else "no")
except Exception:
    # FAIL-CLOSED: parse failed, so we cannot trust the structured path.
    # Deny ONLY if the raw payload references a protected flag-file name;
    # unrelated writes still pass (no wedging of normal writes).
    print("yes" if re.search(r"(\.gate-status|\.gate-failed)\b", raw) else "no")
' 2>/dev/null)"

[ "$matched" = "yes" ] || exit 0

reason="GUARDRAIL: agents must not write gate flag files (.gate-status / .gate-failed / *.gate-status) directly. run_gate.sh is the sanctioned writer (shell, not a harness tool call). Allowing agent writes would let an agent forge a PASS gate status and bypass the commit freshness guard. This write is DENIED."

python3 -c '
import json, sys
reason = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason
    }
}))
' "$reason" 2>/dev/null || true

exit 0
